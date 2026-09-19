#!/usr/bin/env python3
"""
Build a LoRA training set that teaches VOICE, not facts.

The project's one invariant is that retrieval supplies the facts and the
model only phrases them — a fine-tune that memorises camp specifics into its
weights breaks that invariant permanently, because there is no way to
un-train a specific fact once it is baked into an adapter. So this script is
built around two things it must never do: it must never teach the model to
write like a raw chat message, and it must never let a specific piece of
personal data reach a training file. Everything below exists because of one
of those two.

--- Answer-shape, not chat-shape -----------------------------------------

`person_content` is 24,192 raw WhatsApp messages: short, fragmentary,
thread-dependent, "Oh yes. I meant to clarify that. Halp." Training directly
on that teaches the model to WRITE LIKE A CHAT MESSAGE, which is the opposite
of an assistant that answers a question. The corpus is a source of IDIOM —
this camp's rhythm, its dryness, its Burning Man register — not a source of
training targets. So messages are grouped into contiguous threads (this
supplies context a lone message doesn't have), the threads are fed to
Vertex, and Vertex is asked to produce answer-shaped prose: a standalone
explanation a camp member might give someone who wasn't in the
conversation, not a reply within it. See prompts/voice_pairs.md for exactly
what is and isn't asked for, and why no example sentence appears in it (the
same reason LucyBrain.prompt carries none — a model that parses a prompt
correctly copies its examples faithfully, and a copied example is a specific
sentence with no thread behind it).

--- Weights cannot be redacted --------------------------------------------

A database gets a `redact()` pass and a `check_shipped_db.py` gate before it
ships. A trained adapter gets neither: there's no scrubbing a fact out of a
LoRA after the fact, and no gate that opens the weights to check. So personal
data has to never enter the pipeline, checked twice, not once:

  1. Every message is sanitised BEFORE it is ever sent to Vertex — the
     redaction has to hold even against the possibility that a paraphrase
     preserves something it shouldn't, and it costs nothing to also keep
     personal data out of a third party's request logs.
  2. Every finished pair is scanned again, independently, after generation —
     because sanitising the input proves the input was clean, not that the
     output is; a model can still reconstruct a shape it was shown.

`build_preview_db.redact()` already does the hard part — card, order and
bank numbers unconditionally; addresses and contact details only where a
receipt label, roster name, or consumer email domain marks them as a
person's, because a blanket address pattern once ate the camp's own storage
unit. It is imported, not reimplemented. Two things it deliberately does
NOT do, because the device build has a reason to keep them and this dataset
has no matching reason: it leaves a vendor's phone number and a business
email standing (a campmate in the desert needs the hardware store's number;
nobody needs Vertex's training input to carry it), and it says nothing about
`@⁨handle⁩` mentions, which aren't its job. This script closes both gaps with
its own unconditional strip — see `sanitize()`.

--- Real names are a fact, not a voice sample -----------------------------

`redact()` intentionally leaves ordinary names alone; a phone's device
database is allowed to know the camp's own membership. This dataset is not
that database. An answer that says "Piotr organised the recycling run" is not
teaching voice, it is teaching an association between a name and an act, and
that is exactly the "facts baked into weights" failure this whole exercise
exists to avoid — voice needs the camp's *rhythm*, not its *roster*. The
generation prompt asks for role references instead of names, and
`_names_leaked()` drops any pair that names a real thread participant anyway,
on the assumption that an instruction is not trustworthy just because it was
given nicely.

--- Two validation passes, not one ----------------------------------------

`score_answers.invented()` already exists and already states the rule
exactly: a capitalised word, a number, or a year in an answer that isn't in
its source material is one the model added, not one it was given. It's
imported unchanged and run against each pair's own source thread. Anything
it flags is dropped — not edited, dropped, because there's no way to tell
whether the invention is a hallucinated fact or a harmless paraphrase without
asking the source, and the source already gave its answer.

A pair that survives that check still gets the PII scan; a hallucination
scanner has no reason to catch a phone number the model happened to quote
back correctly from its (already-sanitised, so this should never fire)
input.
"""
import argparse
import json
import random
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from vertex_client import run_all
from build_preview_db import redact, set_roster, _EMAIL, _PHONE, _ADDRESS
import score_answers

VOICE_PROMPT = (HERE / "prompts" / "voice_pairs.md").read_text()
CULTURE_PROMPT = (HERE / "prompts" / "culture_pairs.md").read_text()

# --------------------------------------------------------------------------
# Threading
# --------------------------------------------------------------------------
#
# person_content has no thread id — one row is one message from one sender,
# and the only thing tying a conversation together is that its messages
# share a `source` (the WhatsApp group) and sit close in time. So a thread
# here is: same group, ordered by timestamp, split wherever the gap between
# two consecutive messages is wide enough that they're plausibly a different
# conversation, not a continuation of the last one.
#
# 90 minutes was chosen by looking at the corpus, not by convention: PS's
# main channel runs bursts of replies seconds apart, then goes quiet for
# hours between conversations. A tighter window fragments an active
# discussion into a dozen threads too short to hold any subject; a much
# looser one merges "should we bring extra propane" with an unrelated
# afternoon's discussion of a costume, at which point the "topic" a Q&A pair
# is asked to explain no longer exists.
GAP_MINUTES = 90

# A thread longer than this is more than one Vertex prompt should have to
# hold coherently, and the cap also bounds the cost of one job's response.
MAX_THREAD_MESSAGES = 30

# Below this a "thread" is a couple of replies with no real topic to explain
# — the model would either invent one (caught downstream, but wasted money)
# or correctly return nothing (also wasted money). Filtering here is free;
# filtering after a paid Vertex call is not.
MIN_THREAD_MESSAGES = 4
MIN_THREAD_WORDS = 40

# The system instruction every training row carries.
#
# DELIBERATELY NOT LucyBrain.prompt. That prompt's Rule 1 is "anything
# particular to this camp comes only from the facts below" -- and these rows
# carry no fact block, because they teach REGISTER, not grounding. Pairing
# "answer only from the facts below" with an answer derived from no facts at
# all trains the model to break Rule 1 under the very prompt that states it.
# A database gets redact() and a gate before it ships; a LoRA gets neither,
# and there is no un-training it.
#
# So this is the identity -- which is what voice hangs off -- and nothing
# about grounding. The rules stay at inference, where they can still be
# edited, measured and reverted.
VOICE_SYSTEM = (
    "You are Lucy, the Preservation Society's snail art car. You have been to "
    "Burning Man since 2018, you carry the camp's sound system, and you are "
    "the camp's memory."
)

# Every supported base model caps a tuning example at 8192 tokens -- Gemma 4
# E2B IT and Gemma 3 1B IT alike (open-model-tuning, "Limitations").
#
# Estimated rather than tokenised: adding a tokeniser here would mean loading
# a GGUF into a script with no model dependency at all. 3.0 characters per
# token is conservative for English prose against Gemma's roughly 4, so the
# estimate overcounts and errs toward dropping. A row over the cap is dropped
# here rather than left for the tuning job to truncate silently mid-answer.
MAX_SEQUENCE_TOKENS = 8192
CHARS_PER_TOKEN = 3.0


def estimated_tokens(*parts: str) -> int:
    return int(sum(len(p) for p in parts) / CHARS_PER_TOKEN)


def as_generate_content(row: dict) -> dict:
    """The GenerateContent schema -- the only one of the three that carries a
    system instruction, so the only one that can match inference conditions."""
    return {
        "systemInstruction": {"parts": [{"text": VOICE_SYSTEM}]},
        "contents": [
            {"role": "user", "parts": [{"text": row["instruction"]}]},
            # "model", NOT "assistant".
            #
            # The open-model-tuning docs show "assistant" in their
            # GenerateContent example. The service rejects it outright:
            # "Supported roles are: user, model, function." A code review
            # flagged this against the SDK's own Content type -- role is
            # Literal['user'] | Literal['model'] -- and I overrode the review
            # on the strength of the doc snippet, then wrote a test pinning
            # the wrong value. It cost a failed tuning job on 903 examples.
            # When the documentation and the runtime disagree, believe the
            # one that runs.
            {"role": "model", "parts": [{"text": row["response"]}]},
        ],
    }


def as_prompt_completion(row: dict) -> dict:
    """The bare schema. No system instruction, so training conditions differ
    from inference by the whole prompt -- kept for a register-only trial."""
    return {"prompt": row["instruction"], "completion": row["response"]}


FORMATTERS = {"generate_content": as_generate_content,
              "prompt_completion": as_prompt_completion}


def stratified_split(rows: list[dict], seed: int,
                     fraction: float) -> tuple[list[dict], list[dict]]:
    """(train, validation), holding `fraction` back per source.

    Stratified so a split cannot hand every culture pair to training and
    leave validation measuring camp threads alone -- the two halves of this
    dataset teach different things and a run that validates on only one of
    them reports a number about half the work.

    Its own Random, seeded independently of the thread sampler: sharing that
    rng would make the split depend on whether --limit-threads was passed,
    so the same corpus would split differently after a cheap trial run.
    """
    rng = random.Random(seed + 1)
    train: list[dict] = []
    val: list[dict] = []
    for source in sorted({r.get("source", "?") for r in rows}):
        group = [r for r in rows if r.get("source", "?") == source]
        rng.shuffle(group)
        cut = int(len(group) * fraction)
        val += group[:cut]
        train += group[cut:]
    rng.shuffle(train)
    rng.shuffle(val)
    return train, val


def _parse_ts(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def build_threads(conn: sqlite3.Connection) -> list[list[tuple]]:
    """[[(id, person_name, content, source, timestamp), ...], ...], in time
    order within each source, across the whole corpus."""
    rows = conn.execute("""
        SELECT pc.id, COALESCE(p.name, '?'), pc.content, pc.source, pc.timestamp
        FROM person_content pc LEFT JOIN person p ON p.id = pc.person_id
        WHERE pc.content IS NOT NULL AND length(trim(pc.content)) > 0
          AND pc.timestamp IS NOT NULL
        ORDER BY pc.source, pc.timestamp, pc.id
    """).fetchall()

    threads: list[list[tuple]] = []
    cur: list[tuple] = []
    prev_source = None
    prev_dt = None
    for row in rows:
        _id, _name, _content, source, ts = row
        dt = _parse_ts(ts)
        new_thread = (
            source != prev_source
            or prev_dt is None or dt is None
            or (dt - prev_dt).total_seconds() > GAP_MINUTES * 60
            or len(cur) >= MAX_THREAD_MESSAGES
        )
        if new_thread and cur:
            threads.append(cur)
            cur = []
        cur.append(row)
        prev_source, prev_dt = source, dt
    if cur:
        threads.append(cur)

    return [t for t in threads
            if len(t) >= MIN_THREAD_MESSAGES
            and sum(len(m[2].split()) for m in t) >= MIN_THREAD_WORDS]


# --------------------------------------------------------------------------
# Sanitisation — every message, before it is ever sent anywhere
# --------------------------------------------------------------------------
#
# WhatsApp's own mention markup: "@⁨Sarah Bright⁩" — an @ followed by
# a name wrapped in Unicode first-strong-isolate / pop-directional-isolate
# characters (U+2068 / U+2069). Counted at 1,481 occurrences across the
# corpus. redact() has no opinion on this; it isn't a contact detail, it's a
# WhatsApp rendering artefact, and it's this script's to strip.
_ISOLATE_MENTION = re.compile(r"@⁨[^⁩]*⁩")

# The isolate form covers WhatsApp's own @-mentions, but the corpus also has
# plain "@piotr", "@Alastair", "@evan" typed by hand. Deliberately loose
# (word after @) rather than checked against the roster: a bare @mention
# that ISN'T a person ("@ 8:30AM", "@SMF", "@5pm") only costs a harmless
# word in the sanitised text the model never has to reproduce faithfully,
# where a missed real handle costs a name in a training file forever. Same
# call build_preview_db.py's phone regex makes for the same reason — a wide
# net here is the safe direction to be wrong in.
_BARE_MENTION = re.compile(r"@[A-Za-z][\w'-]{1,30}")


def strip_handles(text: str) -> str:
    text = _ISOLATE_MENTION.sub(" ", text)
    text = _BARE_MENTION.sub(" ", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def sanitize(text: str) -> str:
    """Everything personal, out, before this message reaches a prompt.

    redact() first — card/order/bank numbers unconditionally, addresses and
    contact details wherever something marks them as a person's. Then the
    two gaps redact() leaves on purpose because the device database has a
    reason to keep them that this dataset does not: a vendor's phone number
    and a business email are useful to someone in the desert and useless to
    a voice sample, so here they go unconditionally rather than only when
    "personal" context is detected. Then handles, which redact() was never
    asked to touch.
    """
    text = redact(text)
    text = strip_handles(text)
    text = _EMAIL.sub("[email removed]", text)
    text = _PHONE.sub("[phone removed]", text)
    return text


# --------------------------------------------------------------------------
# Corpus pairs: thread -> (instruction, response) via Vertex
# --------------------------------------------------------------------------

CORPUS_SCHEMA = {
    "type": "object",
    "properties": {
        "pairs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "instruction": {"type": "string"},
                    "response": {"type": "string"},
                    "supporting_message_ids": {
                        "type": "array", "items": {"type": "integer"}},
                },
                "required": ["instruction", "response",
                             "supporting_message_ids"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["pairs"],
    "additionalProperties": False,
}


def build_corpus_jobs(threads: list[list[tuple]]):
    """Returns (jobs, seen_ids, thread_names, thread_text, thread_source),
    all keyed by job id.

    seen_ids bounds which message ids a citation is allowed to name — the
    same "the row existing proves nothing about whether the model saw it"
    rule enrich_lore.py uses. thread_names is every real participant name in
    the thread, checked against the response by _names_leaked(). thread_text
    is the sanitised prompt body itself, which doubles as the "facts" side
    of score_answers.invented()'s check — the model may only be as specific
    as what it was actually shown, in this exact, already-sanitised form.
    thread_source is the WhatsApp group name, carried into the output row so
    a reviewer can trace a pair back to its conversation.

    Every dict is keyed by job id rather than by list position: sanitising a
    thread down to fewer than MIN_THREAD_MESSAGES real lines (rare, but a
    thread that was mostly @mentions and reactions can do it) skips that
    thread here, and a position-based key would silently misalign every
    thread after the skip with the wrong job.
    """
    jobs, seen_ids, thread_names, thread_text, thread_source = [], {}, {}, {}, {}
    for i, t in enumerate(threads):
        lines, names, shown_ids = [], set(), set()
        for mid, name, content, _source, _ts in t:
            clean = sanitize(content)
            if not clean:
                # Sanitised down to nothing -- e.g. a message that was only
                # an @mention. It never reaches the prompt, so its id must
                # not be a citable one either: seen_ids has to track what
                # was actually SHOWN, not what the raw thread contained, or
                # a citation of this id would pass validation for a message
                # the model never saw.
                continue
            lines.append(f"[{mid}] {name}: {clean}")
            shown_ids.add(mid)
            if name and name != "?":
                names.add(name)
        if len(lines) < MIN_THREAD_MESSAGES:
            continue
        jid = f"voice-{i}"
        body = "\n".join(lines)
        jobs.append((jid, f"Messages from {t[0][3]}:\n\n{body}"))
        seen_ids[jid] = shown_ids
        thread_names[jid] = names
        thread_text[jid] = body
        thread_source[jid] = t[0][3]
    return jobs, seen_ids, thread_names, thread_text, thread_source


# --------------------------------------------------------------------------
# Culture pairs: no source thread, generated from a topic list
# --------------------------------------------------------------------------

CULTURE_SCHEMA = {
    "type": "object",
    "properties": {
        "pairs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "instruction": {"type": "string"},
                    "response": {"type": "string"},
                },
                "required": ["instruction", "response"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["pairs"],
    "additionalProperties": False,
}

# General Black Rock City knowledge, not this camp's business — the same
# split bm_basics.py draws for the enrichment database, applied here for the
# same reason: "how the event works" is safe to state outright, "what our
# camp decided" is not, because the second can change and the first mostly
# doesn't. Generated rather than scraped, deliberately — no copyright or
# robots.txt question, and this is exactly the kind of stable, general
# knowledge a frontier model already has cold.
CULTURE_TOPICS = [
    "radical self-reliance and why you pack in everything you need",
    "MOOP and Leave No Trace",
    "greywater and what you're allowed to do with it",
    "dust storms and whiteouts, and what to do when one hits",
    "hydration and heat in the desert",
    "the gifting economy and why nothing is for sale except ice and coffee",
    "bikes on the playa: lights, decoration, theft",
    "camp etiquette: workshifts, shared space, quiet hours",
    "art cars: what makes a vehicle a licensed art car",
    "consent and communication norms around the more adult parts of the event",
    "the Ten Principles, in practice rather than as a list",
    "what to do in a dust storm or a medical situation, who to call",
    "playa provisioning: what you can and can't buy on site",
    "sun protection and playa skin/eye care",
    "the Man burn and the Temple burn: what's different about each",
]


def build_culture_jobs(topics: list[str]):
    return [(f"culture-{i}", f"Topic: {topic}") for i, topic in enumerate(topics)]


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def _names_leaked(response: str, names: set[str]) -> str | None:
    """A real participant's name, whole-word, in the response.

    Checked against every space-separated token of every name in the thread
    (first name alone is how the camp actually refers to people, so "Piotr"
    has to be caught even though the roster says "Piotr Moravec"), not just
    full names -- a partial match here is a fact leaking, not a false
    positive to worry about.

    Case-insensitive on purpose, even though that costs an occasional
    over-drop -- a name that happens to also be an ordinary word ("Max",
    "Grace", "Faith") would otherwise slip through whenever the model's
    capitalisation didn't happen to match the roster's, and losing a
    training pair to a false positive is the safe direction to be wrong in;
    a leaked name shipped in an adapter is not recoverable the same way.
    """
    for name in names:
        for token in name.split():
            if len(token) < 3:
                continue
            if re.search(rf"\b{re.escape(token)}\b", response, re.IGNORECASE):
                return token
    return None


_ADDRESS_UNCONDITIONAL = _ADDRESS  # both patterns, run without redact()'s
                                    # label-window gate: a final pair gets no
                                    # benefit of the doubt an input thread did.


def pii_hits(text: str) -> list[str]:
    """Every category this dataset must ship with zero of, found in one
    string. Used both as a drop condition during generation and as the
    independent final scan over the written file -- the same check, run
    twice, so the second run is a check on the first rather than a repeat of
    whatever the first got wrong."""
    hits = []
    if _ISOLATE_MENTION.search(text) or _BARE_MENTION.search(text):
        hits.append("handle")
    if _EMAIL.search(text):
        hits.append("email")
    if _PHONE.search(text):
        hits.append("phone")
    if any(p.search(text) for p in _ADDRESS_UNCONDITIONAL):
        hits.append("address")
    return hits


def validate_corpus(results: dict, seen_ids: dict, thread_names: dict,
                     thread_text: dict, thread_source: dict) -> tuple[list[dict], dict]:
    stats = {"malformed": 0, "uncited": 0, "invented": 0,
              "named_individual": 0, "pii": 0, "kept": 0, "empty_job": 0}
    kept = []
    for jid, payload in results.items():
        if not payload:
            # run_all already prints a WARNING with the job ids to stderr;
            # this is the same fact carried into the printed report instead
            # of only stderr, so "how many pairs" always accounts for every
            # job sent, not just the ones that answered.
            stats["empty_job"] += 1
            continue
        shown = seen_ids.get(jid, set())
        for p in payload.get("pairs") or []:
            instruction = (p.get("instruction") or "").strip()
            response = (p.get("response") or "").strip()
            if not instruction or not response:
                stats["malformed"] += 1
                continue

            ids = sorted({mid for mid in (p.get("supporting_message_ids") or [])
                          if isinstance(mid, int) and mid in shown})
            if not ids:
                stats["uncited"] += 1
                continue

            # score_answers.invented() checks the answer against facts +
            # question together, which is correct in its home use: there,
            # the question is trusted user input Lucy is allowed to echo.
            # Here it is not -- the instruction is generated by the same
            # untrusted pass as the response, so a hallucinated specific
            # sitting in BOTH ("San Francisco" invented in an instruction
            # that only ever said "SF") would validate the response against
            # its own invention if the question were passed through. Passing
            # an empty question and folding the instruction into the answer
            # side instead means both halves of the pair are checked against
            # the thread alone, never against each other.
            turn = {"question": "", "facts": thread_text[jid],
                    "answer": instruction + " " + response}
            inv = score_answers.invented(turn)
            if inv:
                stats["invented"] += 1
                continue

            # BOTH halves, not just the response. The instruction is written
            # into the training file as the user turn -- as_generate_content()
            # puts it there -- so "What did Piotr do about the recycling run?"
            # teaches a name-to-act association exactly as surely as an answer
            # would. This checked `response` alone until 2026-08-20, and
            # nothing downstream covered the gap: invented() cannot flag it,
            # because thread_text is built as "[mid] name: content" and every
            # participant's real name is therefore already in its haystack;
            # pii_hits() has no name category; and redact() deliberately
            # leaves ordinary names alone.
            named = (_names_leaked(instruction, thread_names.get(jid, set()))
                     or _names_leaked(response, thread_names.get(jid, set())))
            if named:
                stats["named_individual"] += 1
                continue

            hits = pii_hits(instruction) + pii_hits(response)
            if hits:
                stats["pii"] += 1
                continue

            stats["kept"] += 1
            kept.append({
                "instruction": instruction,
                "response": response,
                "source": "corpus",
                "thread_source": thread_source.get(jid),
                "supporting_message_ids": ids,
            })
    return kept, stats


def validate_culture(results: dict, topics: list[str]) -> tuple[list[dict], dict]:
    stats = {"malformed": 0, "pii": 0, "kept": 0, "empty_job": 0}
    kept = []
    for jid, payload in results.items():
        if not payload:
            stats["empty_job"] += 1
            continue
        idx = int(jid.rsplit("-", 1)[1])
        topic = topics[idx] if idx < len(topics) else None
        for p in payload.get("pairs") or []:
            instruction = (p.get("instruction") or "").strip()
            response = (p.get("response") or "").strip()
            if not instruction or not response:
                stats["malformed"] += 1
                continue
            hits = pii_hits(instruction) + pii_hits(response)
            if hits:
                stats["pii"] += 1
                continue
            stats["kept"] += 1
            kept.append({
                "instruction": instruction,
                "response": response,
                "source": "culture",
                "topic": topic,
            })
    return kept, stats


# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(HERE / "output" / "ps_knowledge.db"))
    ap.add_argument("--out", default=str(HERE / "output" / "voice_dataset.jsonl"))
    ap.add_argument("--limit-threads", type=int, default=None,
                     help="sample only N corpus threads (cheap trial run)")
    ap.add_argument("--culture-topics", type=int, default=len(CULTURE_TOPICS),
                     help="how many culture topics to send (cheap trial run)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--format", default="generate_content",
                    choices=sorted(FORMATTERS),
                    help="schema for the uploadable train/val files")
    ap.add_argument("--val-fraction", type=float, default=0.1,
                    help="held-out share; the tuning docs strongly "
                         "recommend a validation dataset")
    ap.add_argument("--yes", action="store_true",
                     help="skip the confirmation prompt and spend money")
    args = ap.parse_args()

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)

    # Same roster redact() uses on the way to the device -- both rosters,
    # for the same reason build_preview_db.py draws from both: person and
    # camp_member don't hold the same names.
    def _names():
        for q in ("SELECT name FROM person WHERE name IS NOT NULL",
                  "SELECT name FROM camp_member WHERE name IS NOT NULL",
                  "SELECT first_name FROM camp_member WHERE first_name IS NOT NULL",
                  "SELECT last_name FROM camp_member WHERE last_name IS NOT NULL",
                  "SELECT nickname FROM camp_member WHERE nickname IS NOT NULL"):
            try:
                yield from (r[0] for r in conn.execute(q))
            except sqlite3.OperationalError:
                continue
    set_roster(_names())

    all_threads = build_threads(conn)
    print(f"{len(all_threads)} candidate threads in the corpus "
          f"(>= {MIN_THREAD_MESSAGES} messages, >= {MIN_THREAD_WORDS} words)")

    rng = random.Random(args.seed)
    threads = list(all_threads)
    if args.limit_threads is not None and args.limit_threads < len(threads):
        threads = rng.sample(threads, args.limit_threads)
    print(f"sampling {len(threads)} thread(s) for this run "
          f"(seed={args.seed})")

    topics = CULTURE_TOPICS[:args.culture_topics]

    corpus_jobs, seen_ids, thread_names, thread_text, thread_source = \
        build_corpus_jobs(threads)
    culture_jobs = build_culture_jobs(topics)

    print(f"{len(corpus_jobs)} corpus job(s), {len(culture_jobs)} culture job(s)")

    if not args.yes:
        if corpus_jobs:
            print("\n--- sample corpus prompt (voice_pairs.md), first 500 chars ---")
            print(corpus_jobs[0][1][:500])
        if culture_jobs:
            print("\n--- sample culture prompt (culture_pairs.md) ---")
            print(culture_jobs[0][1])
        print("\ndry run — pass --yes to spend money on this")
        return 0

    print("\nsending corpus jobs to Vertex...")
    corpus_results = run_all(corpus_jobs, VOICE_PROMPT, CORPUS_SCHEMA)
    print("sending culture jobs to Vertex...")
    culture_results = run_all(culture_jobs, CULTURE_PROMPT, CULTURE_SCHEMA)

    # Cache raw results before any filtering -- a paid call that gets thrown
    # away by a validation bug should still be on disk to inspect.
    cache_path = HERE / "output" / f"voice_dataset_raw_{int(time.time())}.json"
    cache_path.write_text(json.dumps(
        {"corpus": corpus_results, "culture": culture_results}, indent=2))
    print(f"raw results cached to {cache_path}")

    corpus_pairs, corpus_stats = validate_corpus(
        corpus_results, seen_ids, thread_names, thread_text, thread_source)
    culture_pairs, culture_stats = validate_culture(culture_results, topics)

    all_pairs = corpus_pairs + culture_pairs

    # Independent final scan -- see pii_hits()'s docstring for why this is
    # not redundant with the per-pair check above.
    final_clean = []
    final_pii_dropped = 0
    for row in all_pairs:
        hits = pii_hits(row["instruction"]) + pii_hits(row["response"])
        if hits:
            final_pii_dropped += 1
            continue
        final_clean.append(row)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        for row in final_clean:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # --- the uploadable pair --------------------------------------------
    #
    # Three files, not one. `--out` keeps the auditable shape -- instruction,
    # response and the `source` that says whether a row came from the camp's
    # own threads or from the culture topics -- and that shape is NOT a
    # supported tuning schema. The train/val pair is the strict schema, and
    # carries no extra keys.
    #
    # A validation set because the tuning docs strongly recommend one, and
    # because without it a run reports a loss curve and nothing about whether
    # the thing learned to sound like the camp.
    fitting: list[dict] = []
    over_cap = 0
    for r in final_clean:
        if estimated_tokens(VOICE_SYSTEM, r["instruction"],
                            r["response"]) > MAX_SEQUENCE_TOKENS:
            over_cap += 1
        else:
            fitting.append(r)

    train_rows, val_rows = stratified_split(fitting, args.seed,
                                            args.val_fraction)

    to_schema = FORMATTERS[args.format]
    train_path = out_path.with_suffix(".train.jsonl")
    val_path = out_path.with_suffix(".val.jsonl")
    for path, rows in ((train_path, train_rows), (val_path, val_rows)):
        with path.open("w") as f:
            for row in rows:
                f.write(json.dumps(to_schema(row), ensure_ascii=False) + "\n")

    print(f"\n{len(final_clean)} pairs written to {out_path}")
    print(f"  tuning schema: {args.format}")
    print(f"  train {len(train_rows):>5}  {train_path}")
    print(f"  val   {len(val_rows):>5}  {val_path}")
    if over_cap:
        print(f"  DROPPED {over_cap} pair(s) over the "
              f"{MAX_SEQUENCE_TOKENS}-token cap")
    if len(train_rows) < 100:
        print(f"  WARNING: {len(train_rows)} training pairs. The tuning docs "
              f"recommend starting at 100; below that a run measures noise.")
    print(f"  corpus:  {corpus_stats['kept']} kept "
          f"(of {sum(len(r.get('pairs') or []) for r in corpus_results.values() if r)} generated, "
          f"{corpus_stats['empty_job']} of {len(corpus_jobs)} job(s) returned nothing)")
    print(f"    dropped -- malformed: {corpus_stats['malformed']}, "
          f"uncited: {corpus_stats['uncited']}, "
          f"invented specific: {corpus_stats['invented']}, "
          f"named an individual: {corpus_stats['named_individual']}, "
          f"pii: {corpus_stats['pii']}")
    print(f"  culture: {culture_stats['kept']} kept "
          f"(of {sum(len(r.get('pairs') or []) for r in culture_results.values() if r)} generated, "
          f"{culture_stats['empty_job']} of {len(culture_jobs)} job(s) returned nothing)")
    print(f"    dropped -- malformed: {culture_stats['malformed']}, "
          f"pii: {culture_stats['pii']}")
    print(f"  final independent PII scan over the written set: "
          f"{final_pii_dropped} additional pair(s) dropped "
          f"({'ZERO' if final_pii_dropped == 0 else 'NONZERO'} left standing "
          f"after the per-pair checks -- expected zero)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
