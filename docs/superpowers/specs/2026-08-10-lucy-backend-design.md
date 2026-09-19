# Lucy backend: sync

Design settled 2026-08-10. Nothing here is built yet.

## What this is

A manual sync button. Press it and the phone hands the backend whatever it has
been holding — captured notes, and the log of what you asked Lucy — and takes
down whatever other people have added since last time.

That is the whole of it. Synthesis, summarising, secret sharing, person
feedback and Help Lucy are all wanted eventually and none of them are in scope
here. They are, however, the reason two decisions below are made the way they
are: identity and transport get designed once, and those features become small
additions rather than new systems.

## What already works, and is not re-litigated

The download half shipped on 2026-08-10 and is in testers' hands. A public GCS
bucket serves `manifest.json` and the model files; `ModelGate` blocks the app
until a verified model is present. That path stays exactly as it is. The
backend described here does not serve models, and should not: a bucket needs no
uptime and the VM does.

## The constraints that actually shape this

**There is no signal on playa, except sometimes.** Starlink means some people
will be connected some of the time. So sync is a thing you press, not a thing
that happens — and every path through the app has to work with the backend
unreachable, indefinitely, without looking broken.

**The camp is the trust boundary.** Notes carry names. Photos and voice memos
are visible to everyone in the camp. This was a deliberate decision: within the
camp, attribution is more useful than anonymity, and "who noted this" is
exactly the kind of question Lucy exists to answer. It does mean the sync feed
is not a private space, and members should understand that a note is a thing
you post.

**Chat logs carry no author, and that is the whole of the privacy.** They
upload behind auth, the row has no member column, and two tests in
`test_chatlog.py` hold it there. What stays private is *who asked* —
permanently, by shape rather than by policy. What does not stay private is the
question: refusal-shaped turns come back to the camp verbatim as the gap list,
because a question nobody can see is a question nobody can answer. Decided
2026-08-19; the Sync screen's copy changes with it.

**A local playa server is wanted later.** A wifi router in camp, a small box, no
internet, the same functions. This is not built now, but it is why the backend
is a plain HTTP service in a container talking to Postgres, rather than
Firebase or a pile of managed services. The same compose file should run on a
box behind a camp router with only the base URL changed.

## Architecture

One always-on Compute Engine VM in `lucy-snails`. On it, three containers:

- **Caddy** — TLS termination. iOS requires HTTPS and Caddy gets a Let's
  Encrypt certificate with a three-line config and renews it without help.
- **api** — a single HTTP service. All endpoints below.
- **postgres** — metadata: members, notes, chat turns, answers. Not
  gaps: a gap is a query over chat turns, not a row (see **Help Lucy:
  the answer half**).

Media (photos, voice memos) is stored on the VM's own disk under a
content-addressed path, not in GCS. This is deliberate: a playa server has no
GCS, and a design that only works with a cloud dependency cannot move. Disk is
cheap and the volume is small — a hundred notes a week at ~2.5 MB is a quarter
of a gigabyte.

Backups of the VM's Postgres and media directory go to
`gs://lucy-snails-backups`, which already exists, is private with public access
prevention enforced, and has object versioning on.

**The VM is a pet.** It needs patching, it can fill its disk, and if it is down
on the Tuesday of the burn then nobody syncs. That is an accepted cost — the
eval pipeline wants a machine that holds a database and runs batch jobs, and
gluing four managed services together to avoid owning one box is a worse trade.
It does mean the app must treat the backend as optional at every point.

## Identity

No accounts, no email, no password.

On first launch the app generates a UUID and keeps it in the keychain. The
member picks a display name. To join, they enter a **camp join code** — one
shared secret, rotated per year — and the server returns a long-lived device
token, also kept in the keychain. Every request carries the token.

This is weak authentication and that is the correct amount. It keeps strangers
out of a camp feed; it is not protecting anything that would survive a
determined attacker who is also a Burning Man camp member.

Why a stable ID rather than just a name: the later features need it. Secret
sharing is anonymous but must still rate-limit and de-duplicate. Person
feedback is "anonymous, visible only to the recipient" — the server must know
who wrote it and must never tell. Building on names alone would mean
retrofitting identity later.

**The server always knows who said what**, including for the features that
present as anonymous. That is a promise being made to the camp, and it should
be stated plainly rather than implied.

## The API

All under `/v1`. JSON except where noted. Every endpoint except `health` and
`join` requires `Authorization: Bearer <device token>`.

```
GET  /v1/health                     → {ok, time}. Used as the reachability probe.
POST /v1/join    {code, deviceId, displayName}   → {token, memberId}

POST /v1/notes   multipart: meta.json + photo.jpg? + memo.m4a?
                                    → {noteId}
GET  /v1/notes?since=<cursor>       → {notes: [...], cursor}
GET  /v1/notes/<id>/photo           → image/jpeg
GET  /v1/notes/<id>/memo            → audio/m4a

POST /v1/chatlog {turns: [{askedAt, question, facts, answer}]}
                                    → {accepted}
GET  /v1/gaps?since=<cursor>        → {gaps: [{gapKey, question, asked, answered}], cursor}
POST /v1/answers {answers: [{id, question, body}]}
                                    → {stored}   question, not gapKey: see below
GET  /v1/answers?since=<cursor>     → {answers: [{id, seq, gapKey, question, body}], cursor}
```

Approving and rejecting an answer is not in this list on purpose: it belongs to
`admin.py`, which already exists and is already the thing behind a different
door.

Notes are immutable once uploaded. A member can delete their own note; deletion
tombstones so other phones can drop it on next sync.

`since` is a server-assigned monotonic cursor, not a timestamp — phone clocks in
the desert are not to be trusted for ordering.

## What the app does

**A Sync item in the menu.** The hamburger added on 2026-08-10 exists partly for
this.

Pressing it:

1. Probes `GET /v1/health` with a short timeout. If unreachable, it says so
   plainly — "couldn't reach the camp server, try when you have signal" — and
   changes nothing. It must never look like a failure of the app.
2. Uploads any notes not yet uploaded, one at a time, resumable, skipping ones
   already sent.
3. Uploads chat turns not yet sent.
4. Uploads any answers typed offline, after the chat turns, for the same
   reason chat turns go after notes: the half that helps everyone else goes
   first, and a failure in a later step never un-succeeds an earlier one.
5. Fetches the note list since its cursor and reports **"N new notes"**.
6. Fetches approved answers since its own separate cursor. These are not a
   list to browse — they land in the store `Retrieval` reads, and the only
   visible sign is that Lucy stops saying she does not know.
7. Downloading those notes is a separate, explicit action. Photos and audio are
   fetched lazily — the list is cheap, the media is not.

**Notes from others are visible before they are downloaded.** The point of
sharing them is so people do not note the same thing twice, which only works if
you can see what is already there. The list carries author, time, transcript
and a thumbnail; the full media comes on request.

## Chat logs and the gap list

Every turn Lucy answers is already written to `Documents/lucy-journal.txt` as a
`QUESTION` / `FACTS GIVEN` / `ANSWERED` triple. That is the upload payload,
essentially unchanged.

The valuable part is nearly free. A turn where the answer matches a refusal —
"I don't have that written down", "I don't know" — is a question the camp's
knowledge does not cover. Grouped and counted across everyone's phones, that is
a ranked list of what Lucy is missing, which is three things at once:

- the work queue for the next enrichment pass
- the seed for the Help Lucy feature, where members answer the questions
  themselves
- the beginning of an eval set

This needs no model and no tokens. It is string matching over rows, and it
turns something found by hand on 2026-08-10 — reading a journal and noticing
"medkit" never matches "first aid" — into something continuous and automatic.

## Help Lucy: the answer half

Designed 2026-08-19. The upload half shipped in `4772085`; this is what it was
uploading for.

**A gap is not a stored thing.** It is a chatlog row whose answer is
refusal-shaped. `GET /v1/gaps` groups by *normalised* question text and returns
the **verbatim latest phrasing** with an ask count and a stable `gapKey`. The
verbatim text is what a member reads, because a normalised question reads like a
database and nobody wants to answer a database.

**`gapKey` is assigned by the server, and the phone never computes one.** The
tempting design is a hash of the normalised question, derived independently on
both sides. It does not survive contact: normalising a *question* is
`contentWords()` in `ChatView.swift`, which lowercases through Swift's Unicode
folding, splits on `CharacterSet.alphanumerics` and returns an **unordered
Set** — none of which Postgres `lower()` and a `[^a-z0-9]` regex reproduce
byte-for-byte, and all of which the camp's own names and apostrophes will find.
A hash that disagrees on one codepoint does not throw; it silently files an
answer against a gap that does not exist. So an answer typed offline travels
with the **verbatim question text** it was answering, and the server normalises
once, on arrival, and joins. Each side keeps the normalisation it already has
and neither has to match the other's bytes. The grouping is what stops the
camp answering the same thing twelve times because twelve people phrased it
twelve ways.

**The refusal test must exist once.** It lives in `deadEnd()` in
`ChatView.swift` and again as the documented query in `chatlog.py`, and that
docstring already warns what happens when the two drift. The gaps route does not
add a third copy: the normalisation moves into a SQL function in
`003_help_lucy.sql`, and `deadEnd()` stays the one place a new refusal phrase
gets added.

**`answer` mirrors `note`, deliberately.** A phone-chosen `id`, so a retry on a
bad connection cannot double-store. A `seq` assigned under an advisory lock, so
a client polling `?since=` cannot step over a row that committed late. A
`status` of pending / approved / rejected. **No member column**, matching both
`chatlog` and the decision that notes are anonymous.

**Where an answer is allowed to become a fact.** An approved answer reaches
`Retrieval` as a row, never as prompt text. That is not a style preference. It
is the invariant in CLAUDE.md, and an answer spliced into the prompt would be
the model phrasing a claim no row supports — which is the exact bug that
invariant exists to name. At the next enrichment pass, approved answers export
into the pipeline and become ordinary `camp_fact` rows, so the sync-side
`answer` table stays a queue and never becomes a second permanent knowledge
store.

**Two speeds, and the slow one is honest.** Your own answer serves your own
phone the moment you type it, offline, unreviewed — it is yours, and no trust
boundary is crossed by Lucy saying it back to you. It reaches anyone else only
after you find Starlink, someone reviews it, and they sync. During the burn that
is realistically not same-day. The in-camp box named in **The constraints that
actually shape this** is what would make it same-day, and it is not built. So
review defaults **on** before the burn, when the loop is slow regardless and the
answers are feeding a database that ships. Turning it off during the burn is
worth revisiting only once that box exists.

**On the site.** The gap list and an answer box go behind the codeword cookie
`site.py` already issues. No second auth mechanism, and no individual's profile
card, same as the People screenshot rule.

**The Sync copy, specifically.** `SyncView.swift` currently says conversations
go "privately to the camp's server". The replacement has to carry all three
true things and no more: conversations upload, the questions Lucy could not
answer can come back for the camp to answer, and no row anywhere says who
asked. This is part of the change, not follow-up work — the previous sentence
"stays on your phone" was already retired on the day it stopped being true.

Out of scope, and named here so it stays out: semantic clustering of gaps
(string matching needs no model and no tokens, which is the whole appeal),
editing or threading answers, attribution of any kind, and the eval-set wiring,
which is phase two above.

## Evaluation (phase two)

Vertex AI's Gen AI Evaluation Service, because its metrics map onto this app
unusually well and `scripts/vertex_client.py` already exists.

- **Pointwise groundedness** — "the response contains information included only
  in the user prompt". That is Lucy's architectural invariant restated as a
  metric: retrieval supplies facts, the model phrases them. Currently enforced
  by hope.
- **Pairwise question-answering quality** — A / SAME / B against a stored
  baseline. This is the thing that was missing on 2026-08-10, when the prompt
  changed six times with no way to tell better from merely different.

The stored `(question, facts, answer)` triples are the evaluation instances with
no transformation. Build the counting first; grading comes behind it.

## Failure modes, and what happens

| what | behaviour |
|---|---|
| Backend unreachable | Sync says so. Nothing else in the app changes. Notes keep accumulating locally. |
| Upload interrupted mid-note | Note is not marked sent; retried next sync. Server de-duplicates on note UUID. |
| Two people note the same thing | Allowed. The feed is the mitigation, not the schema. |
| Member deletes a note | Tombstone; other phones drop it on next sync. |
| VM down for the whole burn | Everyone syncs when they get home. Nothing is lost, nothing is corrupted. |
| Disk fills | Uploads 507; sync reports it; nothing on the phone is discarded. |

## Not in scope, not precluded

- **Local playa server** — the reason for containers and a configurable base
  URL. Should need only a different host.
- **Secret sharing, person feedback, Help Lucy** — all sit on the identity and
  transport defined here.
- **Synthesis and summarising** — explicitly deprioritised. The gap list is the
  useful 10% of it and is in scope.
- **Serving models** — stays in the GCS bucket.

## Open

- **The join code mechanism** is the weakest part. One shared secret in a camp
  WhatsApp is how it will actually be distributed, and it will leak. Acceptable
  for a camp feed; worth revisiting before anything more sensitive rides on it.
- **Media retention.** A hundred notes a week is fine. Five years of them is a
  question nobody has asked yet.
- **Whether chat logs should be opt-out.** Sharper now that the gap list is
  open: the questions are readable by the camp, and the record of them sits on a
  machine Marcus administers, even though no row says who asked. The camp should
  be told rather than merely not lied to.
- ~~**Whether the gap list is leads-only.**~~ **Resolved 2026-08-19: open, not
  leads-only, and verbatim.** Members see the questions in the words they were
  asked in. The asker stays unknowable — `chatlog` has no member column and two
  tests hold it there — so what is spent here is the confidentiality of the
  question, not of the person. The cost is real and lands in one place: the Sync
  screen says conversations go "privately to the camp's server", and that
  sentence stops being true the same way "stays on your phone" did. It changes
  with the feature. See **Help Lucy: the answer half**.
