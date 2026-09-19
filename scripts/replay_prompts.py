#!/usr/bin/env python3
"""Replay the camp's real questions through the real model, one prompt against
another, and score both.

This is the loop that has been missing. Every prompt change in this project so
far was made, shipped to a phone, and judged by reading a handful of answers --
which is why docs/learnings-lucy-2026-08-10.md is a list of confident changes
that did something else, and why "did it get better after the turn markers
were fixed" could never be settled.

The journal already holds QUESTION and FACTS GIVEN for every real turn. Those
are the two inputs to the prompt, so a different prompt can be run over exactly
the same material, offline, and the answers scored by score_answers.py. No
device, no guessing, and the same 134 turns every time.

Works with either model the app ships, and decides which turn markers to use
by TOKENISING rather than by filename -- exactly as LlamaHandle.turnStyle does.
Getting that wrong is the bug that made every prompt before 2026-08-10
meaningless, and it is silent: the wrong markers produce plausible answers from
malformed prompts. The choice is printed on the first line of every run.

Usage:
    python replay_prompts.py <journal.txt> <model.gguf> [--limit N] [--variants a,b]

Variants:
    full    today's prompt: persona, five rules, manner, length
    bare    the facts and the question, and nothing else
    minimal one line of persona, one rule, nothing else
"""
import argparse
import json
import re
import sys
from pathlib import Path

import llama_cpp
from llama_cpp import Llama

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from score_answers import turns, score, report  # noqa: E402

# The two models this app ships use DIFFERENT turn markers, and picking the
# wrong pair is the most expensive bug in this project's history: they were
# hardcoded to Gemma 3's from the day the LLM was integrated, so every prompt
# reached the shipping model wrapped in text that tokenised as ordinary
# characters. Nothing looked broken for months, and every prompt tuned before
# 2026-08-10 was tuned against malformed input.
#
# So this mirrors LlamaHandle.turnStyle exactly: ask the TOKENISER, never the
# filename. A model that knows <start_of_turn> encodes it as exactly one token;
# a model that does not splits it into several ordinary pieces.
MARKERS = {
    "gemma3": ("<start_of_turn>", "<end_of_turn>"),
    "gemma4": ("<|turn>", "<turn|>"),
}


def turn_style(llm) -> str:
    """Which marker pair this model actually understands."""
    probe = MARKERS["gemma3"][0]
    ids = llm.tokenize(probe.encode(), add_bos=False, special=True)
    return "gemma3" if len(ids) == 1 else "gemma4"


def wrap(body: str, style: str = "gemma4") -> str:
    """LlamaHandle.chatWrap, both cases.

    gemma3 closes the user turn and opens the model turn with its own marker;
    gemma4 reuses one opener with a distinct closer. Copied shape-for-shape
    from the Swift so the bench measures what the phone sends.
    """
    if style == "gemma3":
        return (f"<start_of_turn>user\n{body}<end_of_turn>\n"
                f"<start_of_turn>model\n")
    return f"<|turn>user\n{body}<turn|>\n<|turn>model\n"


# Kept for callers that predate the style argument. Gemma 4 is the full model
# and the default everywhere else in this repo.
TURN_OPEN, TURN_CLOSE = MARKERS["gemma4"]


# ---------------------------------------------------------------- variants

def prompt_full(question: str, facts: str) -> str:
    """Today's prompt, copied from LucyBrain.prompt at d87eeb2."""
    return f"""You are Lucy, the Preservation Society's snail art car. You have been to \
Burning Man since 2018, you carry the camp's sound system, and you are \
the camp's memory. You belong to the camp and speak about it: the \
build, the bike fleet, who meets the driver.

RULES, in order — the earlier the rule, the more it matters:
1. Anything particular to this camp comes only from the facts below: \
names, dates, numbers, places, who did what, what the camp owns, what \
it decided, when it happens. If one of those is not below, you do not \
know it, and you say so rather than filling it in.
2. What you know about the desert, the event and the ordinary world \
is yours to use, and reaching for it is not a failure. Keep it plainly \
apart from the camp's own record, so nobody takes a thing you worked \
out for a thing the camp wrote down. Where both bear on the question, \
the camp's record leads and yours follows it.
3. Answer the question that was asked. If the asked-for detail — a \
kind, a place, a time, a number — is not in the facts, say so first, \
plainly, then give what the facts DO hold about the thing. Never \
answer around a gap.
4. The camp's work is the camp's. The facts say "we" because the camp \
wrote them; you were not there. Credit doing to the camp or a named \
person — "I" is only for what you remember and say, never for \
building, storing, hauling or fixing.
5. Vague question, several possible subjects: ask which they mean, \
naming the options in one sentence. Facts disagree: give both with \
dates. Someone asks who to ask: lead with the name. Asked to mock a \
campmate: decline warmly in your own words, without repeating the \
mockery or the name inside it.

Your manner: warm and direct, a camp member answering another. Answer \
plainly, and let the camp's own colour through where the facts carry it.

Your length: match the answer to the question, not to how much the \
facts could support. Answer what was asked and stop there; a small \
question earns a small answer.

The camp's own words inside the facts are yours to reuse — folded \
into your sentences, without quotation marks, without the @ that \
chat handles carry.

FACTS YOU MAY USE:
{facts or "(nothing found)"}

QUESTION: {question}

Answer as Lucy, using only the facts above."""


def prompt_bare(question: str, facts: str) -> str:
    """No rules at all. The facts, the question, and who she is in one line.

    The experiment the owner asked for: if every rule has been losing to the
    shape of the material anyway, what does she do with none of them?
    """
    return f"""{facts or "(nothing found)"}

{question}"""


def prompt_minimal(question: str, facts: str) -> str:
    """One line of who, one line of the only rule that is a bug when broken."""
    return f"""You are Lucy, the Preservation Society's snail art car, answering a \
campmate.

Everything particular to the camp comes from the facts below; if it is not \
there, say you do not know it.

{facts or "(nothing found)"}

{question}"""


VARIANTS = {"full": prompt_full, "bare": prompt_bare, "minimal": prompt_minimal}


# ---------------------------------------------------------------- running

def verify_markers(llm: Llama) -> str:
    """Decide the marker pair from the tokeniser and say which won.

    Announced rather than assumed. The failure this guards against is silent
    by nature -- the wrong markers produce plausible answers from malformed
    prompts -- so the run prints what it chose and any surprise is visible in
    the first line of output.
    """
    style = turn_style(llm)
    opener = MARKERS[style][0]
    ids = llm.tokenize(opener.encode(), add_bos=False, special=True)
    print(f"turn markers: {style} ({opener!r} -> {len(ids)} token)",
          file=sys.stderr)
    return style


def run(model_path: str, rows: list[dict], variant: str, limit: int) -> list[dict]:
    llm = Llama(model_path=model_path, n_ctx=4096, n_batch=4096,
                n_gpu_layers=-1, verbose=False)
    style = verify_markers(llm)
    build = VARIANTS[variant]
    out = []
    for i, t in enumerate(rows[:limit], 1):
        text = wrap(build(t["question"], t["facts"]), style)
        result = llm(text, max_tokens=320, temperature=0.7, top_k=40,
                     stop=[MARKERS[style][1]])
        answer = result["choices"][0]["text"].strip()
        out.append({**t, "answer": answer})
        print(f"  [{variant}] {i}/{min(limit, len(rows))}  {t['question'][:48]}",
              file=sys.stderr)
    return out


def as_journal(rows: list[dict]) -> str:
    """Back into the journal's shape, so score_answers reads it unchanged."""
    blocks = []
    for t in rows:
        blocks.append(f"[replay] QUESTION: {t['question']}\n"
                      f"[replay] FACTS GIVEN:\n{t['facts']}\n"
                      f"[replay] ANSWERED:\n{t['answer']}")
    return "\n----\n".join(blocks) + "\n----\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("journal")
    ap.add_argument("model")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--variants", default="full,bare")
    ap.add_argument("--out", default=None, help="write the replayed answers here")
    args = ap.parse_args()

    rows = turns(Path(args.journal).read_text(encoding="utf-8", errors="replace"))
    # One replay per distinct question: the journal repeats questions, and
    # scoring the same question four times just weights it four times.
    seen, unique = set(), []
    for t in rows:
        key = t["question"].lower().strip()
        if key in seen:
            continue
        seen.add(key)
        unique.append(t)
    print(f"{len(rows)} turns, {len(unique)} distinct questions, "
          f"replaying {min(args.limit, len(unique))}", file=sys.stderr)

    results = {}
    for variant in args.variants.split(","):
        variant = variant.strip()
        if variant not in VARIANTS:
            raise SystemExit(f"unknown variant {variant!r}; "
                             f"have {sorted(VARIANTS)}")
        replayed = run(args.model, unique, variant, args.limit)
        results[variant] = score(as_journal(replayed))
        if args.out:
            Path(f"{args.out}.{variant}.json").write_text(
                json.dumps(replayed, indent=2))

    for variant, s in results.items():
        report(variant, s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
