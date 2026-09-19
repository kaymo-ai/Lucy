# What this project taught us on 2026-08-10

21 commits, `53451e7` through `b614d41`. The day was about getting Lucy off
this laptop and onto other people's phones — a signed build, a bucket, a
download, a TestFlight release — and about discovering, at 16:49, that the
model had never once been given the prompt format it was trained on.

Numbers here were checked against the repo, the manifest, the databases or
the source at `b614d41`, except where they are marked as recorded before the
history rewrite, which destroyed the evidence for them.

One size reconciliation before anything else, because two numbers for the same
file will otherwise read as two files. The model is 3,106,738,272 bytes. That
is 3.11 GB decimal and 2.89 GiB, and yesterday's document says 2.89 while
today's commits say 3.11. Same download. (`ModelGate.swift` still carries a
comment claiming 2.89 next to a formatter dividing by 1,000,000,000; it is
wrong and was left alone.)

---

## 1. The turn markers, and everything downstream of them

`LlamaHandle.chatWrap` hardcoded `<start_of_turn>` and `<end_of_turn>`. Those
are Gemma 3's control tokens. The shipping model is Gemma 4 E2B, and its
vocabulary does not contain them — grepping the GGUF finds zero occurrences of
either string, while the newly downloaded gemma-3-1b contains both. Gemma 4's
own `tokenizer.chat_template`, read out of the file's metadata, builds a turn
as `<|turn>` plus the role and closes it with `<turn|>`.

So every prompt sent to the model since the LLM was integrated arrived
bracketed by five or six pieces of punctuation and word fragments, tokenised as
ordinary text, that the model had never been trained to read as structure.

**Nothing looked broken.** That is the whole lesson. A model handed the wrong
markers still answers plausibly; it simply treats the entire prompt as one
undifferentiated blob of prose rather than as an instruction turn and a request
for a reply. There is no error, no empty generation, no warning in the log —
just answers that are a bit worse than they should be, indefinitely.

Two consequences worth holding onto:

**Every prompt instruction tuned before today was tuned against malformed
input.** Yesterday's persona work, the first-person rule, the length rule, the
rules about citing facts — all of it was iterated against a model that was not
parsing the prompt as a prompt. Conclusions drawn from that work are not
reliable, and at least one of them turned out to be actively harmful once the
markers were right (section 2).

**The fix is to ask the vocabulary, not the filename.** `turnStyle` tokenises
`<start_of_turn>` and checks whether it comes back as exactly one token. A
model that knows the marker encodes it as one; a model that does not splits it
into several. That is four lines, needs no lookup table, and stays correct now
that two different models are in circulation on testers' phones — which a
filename check would not have, since both files are named `gemma-*-it-Q4_K_M`.

The measurable effect, same retrieval and same facts on both sides: "how do I
get access to the Empire storage" answered "I don't have that written down" in
the morning and, after the fix, gave the lot number, where the key is kept and
what to bring.

**It was found by accident.** A subagent sent to fetch the small model read
both vocabularies to check that its chat template would work, and noticed the
mismatch. Nobody was looking for it. It had been shipping for as long as the
LLM had existed in this app. When a whole class of quality problem resists
explanation, the format the model is fed is worth checking directly rather than
assumed from the code that has "always worked".

---

## 2. A prompt's examples are answers waiting to be pasted

"The barrels live in Doris" opened four consecutive answers in the journal — to
"Arsehole", to "Is in Oz hole", to "What is an Oz hole" and to "How do I get
access to the Empire storage". It is not a retrieved fact and it is not
responsive to any of those. It is an example sentence inside rule 8 of the
prompt, written the previous evening to demonstrate how to drop the camp's
"we", and she was emitting it verbatim as the opening line regardless of what
was asked.

**This got worse the moment the turn markers were fixed, and that is the useful
part of the diagnosis.** A model that finally parses the prompt as structure
attends to it properly — including to its examples. The illustration was being
copied precisely because the instruction was, at last, landing. A fix that
improves comprehension will amplify anything wrong with what is being
comprehended, so the first regression after a comprehension fix should be read
as evidence the fix worked, not as evidence it should be reverted.

The same failure had already happened once that morning, from the persona line:
an empty question made her recite "we meet the driver" back as though it were a
fact. That was fixed by deleting the phrase without working out why it
appeared, which is why it cost a second diagnosis eight hours later.

The rule now: **rules describe the transformation, they do not demonstrate
it.** There is no quoted sentence anywhere in the prompt that she could paste,
and a note at the top of the file says why, because worked examples read as
obviously helpful and their cost is invisible until you diff two journals.

---

## 3. Which model, and why the answer is not settled

An iPhone 15 tester reported the phone overheating and panicking within a day
of the alpha going out. That is exactly what the warn-and-allow gate was built
to find out, and it answered the question faster than any amount of reasoning
would have.

**A smaller quantisation would not have fixed it.** Gemma 4 E2B is already an
efficiency design at roughly 2B effective parameters, and a lighter quant of the
same model does the same arithmetic per token. The heat is sustained compute,
not memory pressure. It takes a genuinely smaller model, so the manifest now
carries an optional second entry, `small`: gemma-3-1b-it-Q4_K_M at 806,058,240
bytes, chosen by device memory.

The device thresholds, in `ModelDownload.swift`:

- Below 5.5 GiB there is **no download button at all**. The deployment target
  is iOS 17, which installs on a 4 GB iPhone XS, and until today there was no
  memory check anywhere: a tester on an older phone would have spent twenty
  minutes fetching 3.11 GB over wi-fi and then been killed on load with no
  explanation. An offered button is an invitation, and that one led only to a
  wasted download.
- Below 7.5 GiB gets the small model. That is the 6 GB tier — iPhone 14, 14
  Pro, 15, 15 Plus.
- Above that, the full model.

The full model holds about 3.6 GB resident, and an 8 GB phone measured
6,131 MB available with the increased-memory-limit entitlement, so a 6 GB phone
gets roughly 4.3 GB. **Nobody has run it on 6 GB hardware.** Blocking that tier
would have excluded camp members on an untested assumption; the alpha is how
the assumption gets tested.

### The manifest key that must never be renamed

`model` keeps its name and meaning permanently. Build 131 is on testers' phones
and cannot parse a manifest without that key; the failure presents as "can't
reach the model list" and leaves them with nothing to do and no way to update.
`small` is optional in both directions — an old app ignores it, a new app
survives its absence. Likewise the installed filename is now recorded rather
than hardcoded, but the fallback to the original name has to stay, because
phones that installed before today have that file in Documents and no recorded
name, and without the fallback they would silently re-download 3.11 GB.

### Which brain is loaded is not a question the app could answer

"The dev build now downloads the smaller model" could not be confirmed or
denied from inside the app. Settling it took reading the phone's container over
a cable, which showed both containers holding the full model and no partial
file — nothing had switched. Later, "the UI says I'm running on the smaller
model but it's not clear to me" took the same trip, and that time found
gemma-3-1b sitting next to gemma-4-E2B with a timestamp showing which had
arrived first. The greeting had been telling the truth; there was simply no way
to check it.

`LucyBrain` now writes the loaded file, its size and which turn markers it
understands into the journal immediately above the answers that model produced,
and the hamburger menu names the model read *from the file on disk* rather than
from what the manifest believes — those can disagree and the file wins. A
strange answer can now be attributed instead of argued about, which matters
because journals are coming back from testers running two different models.

### The open question, stated honestly

The answers Marcus judged "much, much better" this evening came from Gemma 3
1B, not from the full model. It is tempting to conclude that correct turn
markers and a clean prompt matter more than four times the parameters.

**That conclusion is not supported, because three variables moved together.**
The 1B was only ever heard after the turn-marker fix and after the example
sentences were removed. Nobody has run Gemma 4 E2B with correct markers and the
cleaned prompt, which is the single comparison that would settle it. Until that
run happens, what is known is that the 1B is good enough to be worth taking
seriously, not that it is better.

---

## 4. Speech was being clipped at both ends

Six of the eighteen questions in yesterday's journal were cut off mid-sentence
before retrieval ever saw them. "Do Marcus and Sammy like each" and "How can
get access to the Empire storage l" are the entire question as far as the rest
of the app was concerned. No amount of ranking or prompt work reaches a
fragment, and every retrieval failure debugged against those journals was
partly a debug of the wrong input.

Two unrelated causes, one at each end.

**The tail.** `stop()` snapshotted the last partial transcript, then called
`endAudio()` and `task.cancel()` in the same breath. `endAudio()` is the
*request* for a final transcription; `cancel()` killed the task before it could
arrive. Everything spoken since the last partial callback was discarded.
`finish()` now waits for the final result with a 1.2s deadline, falling back to
the partial, so a recogniser that never finalises cannot leave the button dead.
Confirmed on the phone: "like each other" and "storage lot" both survive, and
the journal shows the final arriving well inside the deadline.

**The head.** `requestAccess()` ran on every button press, round-tripping to a
system daemon even when the answer was already yes, before the audio session
was activated and the tap installed. Measured at 208–353 ms of speech into a
microphone that was not yet recording. Permission is now answered once per
launch.

Two smaller things found while reading that code, both of which would have bitten
someone eventually: the session category was `.record`, which is input-only and
would have swallowed the start tone regardless of where it was played; and
`engine.prepare()` cannot run before the session is active, where it raises an
uncatchable Objective-C exception and kills the app on launch.

The design response is the part that generalises. The screen now answers your
voice — the button's aura, ring and edge glow all move to the microphone's own
RMS level rather than to a timer, with fast-up slow-down smoothing so it does
not flicker out in the gaps between syllables and read as a fault. **A screen
that responds to sound is proof the tap is open and hearing you.** Speaking
into a microphone that had not opened yet was this app's failure this morning,
and nothing on screen disagreed with it.

---

## 5. UIFeedbackGenerator is advisory, and three good theories were wrong

The chat's record button gave no haptic. NOTE's did. Three explanations were
produced across the afternoon, each of which fit the evidence available at the
time, each of which was implemented, and **each of which was wrong**:

1. iOS suppresses the Taptic Engine while a session is recording, so the cue
   was firing too late. Moved it to fire on the press, before the session came
   up. Still nothing.
2. The chat called the cue from inside `SpeechListener`'s async `start()`,
   after `await requestAccess()`; by the time that suspension resumed the audio
   session was coming up. Moved both cues into `HoldToRecord`, synchronously,
   in the view, before a single async hop. Still nothing.
3. `prewarm()` set a recording audio category the moment the chat screen
   appeared, while NOTE only set one when recording began — and a category left
   standing, with the session never activated, appears to be enough for iOS to
   stop honouring the engine. Moved the category back into `start()`. Still
   nothing.

What actually made the phone move was **also playing system sounds 1519 and
1520**, which reach the same motor by an older and blunter path that is not
subject to the same suppression. `UIFeedbackGenerator` is advisory: iOS drops
it under conditions it does not report, and there is no return value, no error
and no log line to tell you it was dropped. The comment in `Haptics.recordStart`
now says not to simplify the system-sound call away, because it looks redundant
next to the `UIImpactFeedbackGenerator` line and it is the half that works.

**The method lesson is larger than the haptic.** Three plausible mechanisms in
a row, each consistent with everything known, each costing a commit. What ended
it was instrumenting rather than theorising: `HoldToRecord` writes a journal
line on touch-down, so the next round can be settled by evidence — if the line
is there and nothing was felt, the call is being made and dropped, and no
fourth theory about where it is called from will help. That line should have
been the first commit of the three, not the fourth.

Two other things surfaced in the same pass. NOTE refused to record without a
photo — "Take the photo first" — because the capture folder was only ever
created by the shutter, so voice memos were rejected outright and nothing was
saved or transcribed; half the useful notes are said rather than photographed,
because your hands are full of the thing you are describing. And
`startRecording` waited for the begin tone to finish before opening the
recorder, which is right for a button you tap and speak into and wrong for one
you hold: the haptic says "go" on touch-down, people start talking immediately,
and half a second was being eaten off the front of every memo.

---

## 6. The download had never been run

The model download was written, reviewed, committed, and the model uploaded to
a bucket. Its first-ever end-to-end execution failed immediately with
`NSURLErrorDomain -1`.

Every alpha tester would have met that on first launch: install, tap once,
"unknown error", dead app, no model, no path forward.

The cause is that **background `URLSession` transfers do not work in the
Simulator** — and the Simulator is the only place the fresh-install path can be
exercised at all, because a real phone already has the model in Documents and
never sees the gate. The two facts together are why nobody had run it: the only
environment that can test it is the one environment where it cannot work. The
session configuration is now chosen at compile time; devices keep the background
session, which is what survives the app being backgrounded during a twenty-minute
transfer, and the Simulator uses a default one so the path can be driven at all.
`--auto-download` starts the fetch without a tap, since the button needs a
finger and the Simulator has none.

Failures now report their domain and code. "Unknown error" is worth nothing to
somebody in a car park trying to get this working before they drive into a
desert.

Verified afterwards as a whole path rather than as parts: the gate reads the
live manifest, 3.11 GB arrives with progress, the sha256 matches, no `.part`
file is left behind, and the gate opens into the app. The hash is computed in
4 MB chunks — reading a 3 GB file into memory to hash it is a way to be killed
by the memory limiter on the very last step of a twenty-minute download, and an
unverified 3.11 GB file is indistinguishable from a truncated one until Gemma
fails to load.

**Generalisation: a path that has never been executed is not code, it is a
plan.** Ask which paths in a release have actually had bytes through them, and
treat "reviewed and committed" as evidence of nothing.

---

## 7. The repository, and what made it unpushable

`master` had been unpushable for seven months. The cause was three copies of
`ps_knowledge.db`, each about 192 MB, in three separate paths in the history —
GitHub's hard limit is 100 MB per blob, so no push could ever succeed.

It had been made substantially worse the same day, before it was fixed.
`.gitignore` covered `Lucy/.build/` but not `Lucy/.build-check/` or
`Lucy/.build-release/`, and repeated `git add -A` swept them in: **2,968 build
files across nine commits, 460 MB of the 465 MB tracked at HEAD**, including
three 67 MB dSYMs and a signed IPA. (Those figures were recorded before the
rewrite and cannot be re-derived now; the blobs are gone.)

`git filter-repo` took the pack from 203.93 MiB to 6.16 MiB with all 133
commits intact. Verified at `b614d41`: `size-pack` is 6.16 MiB, the history
holds 142 commits, and `origin/master` equals `HEAD` with nothing ahead. The
oldest entry in the `origin/master` reflog is `e7ce1db` — the ignore-rules
commit made immediately after the rewrite — which is the proof that the rewrite
is what unblocked seven months of pushing.

`.gitignore` now names all four build directories — `Lucy/.build/`,
`.build-device/`, `.build-check/`, `.build-release/` — with a comment saying
that anything matching `Lucy/.build*` is generated and disposable, so the next
variant gets added rather than committed. It also carries `*.xcarchive/`,
`*.ipa`, `*.dSYM/` and a bare `ps_knowledge.db` that matches the database in
any path.

**Where the database lives now.** It is 106 MiB and every other artifact in the
project derives from it, and until today it existed in exactly one place, two
weeks before that place goes to a desert. `scripts/backup_to_gcs.sh` sends the
raw WhatsApp exports, `PS Processed` and the database to
`gs://lucy-snails-backups`, with `--list` and `--restore`.

That is a **different bucket** from `lucy-snails-releases`, and the separation
is load-bearing. Releases is public so phones can fetch the model without
credentials. Backups is the camp's entire chat history: putting it there would
publish every message anyone in the camp has ever sent. Backups has public
access prevention *enforced* rather than merely unset, and an anonymous fetch
returns 403 — verified, not assumed. Object versioning is on, so a bad
enrichment run is recoverable rather than final, which matters for a file
rebuilt by spending frontier-model API calls. The manifest recording sha256,
byte count and commit is written last, so under `set -e` a failed upload leaves
no stamp and the manifest can never claim a backup that did not happen.

---

## 8. Distribution: the traps, in the order they were hit

All of this is now verified end to end — bucket, manifest, first-run gate,
signed IPA, TestFlight. What follows is the list of things that cost time, kept
because each one presents as a different problem than it is.

**Public bucket reads return HTTP 412.** Granting `allUsers` read on
`lucy-snails-releases` failed with "one or more users named in the policy do
not belong to a permitted customer", which reads like a permissions problem and
is not. kaymo.ai sets `constraints/iam.allowedPolicyMemberDomains` to its own
customer at the organisation level, so `allUsers` belongs to no permitted
domain and no bucket anywhere in the org can be read anonymously.
`deploy/allow-public-objects.yaml` overrides the constraint for the lucy-snails
project alone; the rest of the organisation keeps it. **It also needs about a
minute to propagate** — the grant failed immediately after the policy was set
and succeeded on retry, which looks exactly like the override not working.

**`exportArchive` needs an App Store Connect API key.** Without
`-authenticationKeyPath` it fails with "No Accounts", then "No signing
certificate iOS Distribution found", then "No profiles for ai.kaymo.Lucy" —
three messages that each suggest a different missing thing. `xcodebuild` cannot
see an Apple ID whatever the Xcode GUI shows: one Xcode installed, running,
account list empty. With the key it is unattended.

**Automatic signing does not work here either way.** With the API key it gives
"Cloud signing permission error" — the key can read apps, bundle IDs and
certificates, and can create profiles, but is not permitted to mint
certificates. The distribution certificate had to be created by hand; the team
had only a development one, which is what "No signing certificate iOS
Distribution found" actually meant. So: manual signing against a profile
created through the API, because the developer portal's navigation had moved.
`deploy/make_profile.py` creates and installs it and `deploy/asc_query.py`
answers what App Store Connect actually believes exists — which is how the app
record was confirmed present while every local error implied it was not. The
profile expires 2027-08-10 and will be a stranger's problem by then.

`release.sh` now checks for an installed profile before archiving and says what
to do, rather than dying inside an export with a cryptic message. It produces
an 8.7 MB IPA, build number from the commit count so it rises on its own and
never collides, with `ITSAppUsesNonExemptEncryption` set so TestFlight stops
asking the same question on every upload.

**The shipped database is checked as an artifact, not as a function.**
`check_shipped_db.py` runs against the database inside the signed IPA before
upload. A passing unit test on `redact()` proves nothing about a database built
before `redact()` existed, and the database is what goes onto other people's
phones. It mirrors redaction's own scope deliberately — card, order and bank
numbers refused anywhere, addresses refused only inside the window a shipping
label opens in a document — because run wider it objects to the camp's own
places, the Monument warehouse, the Fernley dump, the Tahoe decom house, which
are operational information and the answer to real questions. Verified in both
directions: it passes the shipped database and it catches a planted receipt.

### The bundle ID split, which exists to protect 3.11 GB

Debug is `ai.kaymo.Lucy.dev`, labelled "Lucy Dev". Release stays
`ai.kaymo.Lucy` and goes to TestFlight. `device.sh` builds Debug and
`release.sh` builds Release, so the split falls out of the commands that
already exist.

Without it, every cable install fights the TestFlight one: iOS will not put a
cable-signed build over a TestFlight build, so you delete the app — and
deleting an app takes its Documents directory, which is where the model lives.
**That is the trap that cost an evening on 2026-08-09 and came within a tap of
costing another today.** Now the tester-facing build sits untouched on the
phone while the dev build is replaced ten times an afternoon, which is the only
way to keep noticing what testers actually experience.

The cost is two models and 6.2 GB, because containers are sandboxed and each
app keeps its own. The risk worth checking beforehand was whether automatic
provisioning would grant `com.apple.developer.kernel.increased-memory-limit` to
a brand-new identifier — without it the dev build would be killed loading the
model and it would present as a memory regression rather than a provisioning
one. It does: the installed binary carries the entitlement against
`78J6KTETAK.ai.kaymo.Lucy.dev`.

The same reasoning produced `reload()` and the hamburger menu. `LucyBrain.load()`
refuses to run twice, so switching models without a reload meant killing the
app — and on this project the moment somebody deletes the app to force the issue
is the moment 3.11 GB goes with it. The menu names the loaded model and switches
in place; `--model full|small|auto` does the same over the cable.

---

## 9. What is verified, and what is not

Verified today, by running it rather than by reading it: the GGUF vocabulary
grep and the tokenise-one-token test; manifest 200 and model 206 on a range
request, which is what makes resume real rather than claimed; the full download
path in the Simulator including sha256 match and no leftover `.part`; the
signed 8.7 MB IPA and its clean database; the backups bucket refusing anonymous
reads with 403; 93 of 118 `person_profile` rows containing gendered pronouns;
the repository pushed, 6.16 MiB, nothing ahead of origin; and the speech head
and tail fixes, from the journal on the phone.

Not verified: nobody has run either model on a 6 GB phone, so the warn-and-proceed
tier is an untested assumption that the alpha exists to test; nobody knows
whether the 1B actually resolves the iPhone 15 overheating, only that it should
by construction; and the "much better" verdict on the 1B is confounded three
ways, as section 3 sets out.

Two things today were asserted confidently and were false — that the dev build
had switched to the small model when both containers held the full one, and
three consecutive mechanisms for the dead haptic. In both cases the thing that
settled it was a journal line or a container listing, not an argument. **When a
question is about what the machine is actually doing, the cheapest correct move
is almost always to make it say so.**

---

## 10. She had no memory, and the fix is not a bigger window

Every question was standalone. `answer()` called `clearMemory()` each turn, on
the reasoning — written into the code — that carrying the previous turn's
tokens invites reusing a fact that no longer applies. That is a real risk and
the instinct was right. It also threw out the conversation.

The cost is in this morning's journal. Asked about the manual, told "You do,
it's in the camp manual", she answered **"I don't know."** That sentence went
through retrieval as a query of its own and reduced to `camp` and `manual`; the
subject lived in the previous turn and nowhere in this one, so retrieval
searched for the wrong thing while the answer sat in the database.

The obvious fix is a larger context window and it is wrong. A week of
conversation does not fit in 4k, or 32k, or any size — and Lucy is meant to be
a companion for the week, not for the last few minutes. A rolling summary is
worse: it discards the specific detail long before anyone asks for it, and the
detail is the whole point.

**Memory is a retrieval layer, which is the architecture this app already
runs.** Every turn goes to a writable SQLite in `Documents`; the ones bearing
on the question come back and are rendered into the fact sheet. That reaches a
turn from Tuesday three hundred exchanges later, which no context length does.

Four decisions inside it are load-bearing:

- **A separate database from `enriched_preview.db`.** That one ships inside the
  app, is read-only, and is replaced wholesale by every update. Conversation
  has to survive updates, so they cannot share a file. It is also the right
  conceptual split: what the camp knows, against what you said.
- **Two layers.** `recent()` carries the last three exchanges so a follow-up
  resolves and she does not repeat herself; `recall()` searches everything and
  excludes what `recent()` already holds, because the same turn twice in one
  prompt reads as emphasis.
- **History is labelled as conversation and never as fact.** A remembered turn
  is evidence of what was *said*, not of what is *true*. She can be told
  something wrong, and repeating it back as a camp record would be the worst
  failure this app has.
- **The previous question's terms blend in only when the current one has fewer
  than two of its own.** Unconditional blending is how assistants end up
  answering the question you asked a minute ago.

It inherits the vocabulary gap: "where's my bike" will not reach "I left the
cruiser by the trash fence". Personal history is smaller and in the asker's own
words, so it misses less often than the camp corpus — but this is now the
*third* subsystem waiting on E7.

**Unverified, and of a familiar shape.** This puts new text into the prompt,
which is exactly what produced "the barrels live in Doris" earlier the same
day. Nobody has read a journal after a long conversation yet. If she begins
narrating the asker's own past questions back at them, the section header is
the thing to change.

---

## 11. A correction made under time pressure, which was wrong

Build 142 uploaded and processed to `VALID`. TestFlight on the phone still
showed 131. The API said the beta group could see only 131, so the earlier
belief — that internal groups receive every build automatically — looked wrong,
and it was corrected out loud.

The correction was the error. Assigning the build manually returned **422,
"Builds cannot be assigned to this internal group"** — and the same query, run
immediately afterwards, showed `['142', '131']`. Internal groups do receive
every build automatically. The first check simply ran before propagation
finished, and a delay was mistaken for a missing association.

Nothing was fixed by intervening; the build arrived on its own. What the
episode cost was a true statement replaced with a false one, in the middle of a
session where three haptic theories had already gone the same way. **The
failure mode is not being wrong. It is treating a single observation as
sufficient to overturn a working belief, when the cheaper move was to wait
sixty seconds and look again.**

A smaller instance, the same evening: choosing the light model made the choice
unreachable. The picker lived on the download gate, and the gate only appears
when no model is present — so the moment one was, there was no way back to the
other from inside the app at all. Both files were sitting in `Documents`; only
the pointer was stuck. UI that exists solely in a first-run flow is UI that
cannot be used to undo a first-run decision, and that is a category of bug
worth looking for rather than hitting.

---

## 12. Open

- **Whether the full model earns its 3.11 GB.** Gemma 3 1B is 806 MB, runs on
  every phone in the camp, and produced the best answers anyone has seen from
  this app. But it was only ever heard downstream of the turn-marker fix and
  the example-sentence removal, so the comparison is three-way confounded. The
  run that settles it is Gemma 4 E2B, correct markers, cleaned prompt, same
  questions. If the 1B holds up, the download shrinks by 74%, the 5.5 GB floor
  and the 6 GB warning both disappear, and the overheating problem goes with
  them. This is the highest-value open question in the project.

- **The upload half of the backend: server built, phone not.** The spec is at
  `docs/superpowers/specs/2026-08-10-lucy-backend-design.md` and its four open
  questions still stand — two of them for the camp rather than for the code.
  What changed late on 2026-08-10 is that the server half exists and is
  serving: see §13 below. Nothing on the phone talks to it yet, so notes,
  journals and questions still do not move.
  The design is an always-on VM with Postgres behind Caddy. Notes sync up and
  are shared with the camp, attributed to whoever wrote them, because a note
  nobody else can see is a diary. Chat logs go up private — nobody reads
  anybody's questions — but are mined in aggregate for the questions Lucy could
  not answer, which is the only honest source of what the corpus is missing.
  Evaluation is Vertex groundedness on answers against the facts they cite,
  plus pairwise comparisons between model and prompt versions, which is what
  turns "much, much better" into a number. None of this precludes a server on
  the playa; it is a different deployment of the same thing, and the design
  should not assume connectivity that will not exist for a week.

- **Expertise ranking still weights confidence over relevance.** Unchanged from
  yesterday and worth restating because it is now more visible: `searchPeople`
  ranks by the strength the enrichment recorded — strong 3, moderate 2,
  mentioned 1 — so for "who knows how to fix a bike", Walter Lindell's "bike
  repair" (mentioned) loses to "Bike Inventory Management" (moderate). The most
  relevant person is beaten by a confidence score attached to a less relevant
  tag. Relevance to the question should dominate.

- **Embeddings for `camp_fact` (E7).** Also unchanged, and now the oldest thing
  on this list. Retrieval is whole-word keyword matching, so "medkit" cannot
  reach "first aid", "Empire storage" cannot reach "Emigrant Storage", and
  "water delivered" cannot reach "service vouchers". Four independent cases,
  none reachable by any ranking change, because the right facts are never
  candidates. The corpus already holds EmbeddingGemma vectors for messages and
  documents.

- **93 of 118 `person_profile` rows use he/she.** Verified again today. They
  render in the "WHAT THEY DO" block directly beneath the they/them portraits,
  so the contradiction is visible on a single card to every person in the camp.
  A targeted rewrite of those 93 summaries under the same pronoun rule — with
  the same code-enforced re-run and drop that made the portraits comply — is
  the obvious next job and has been deferred twice.

- **Memory has never been read back.** It ships with build 142 and nobody has
  looked at a journal after a long conversation. Two product questions are
  unanswered and belong to the camp, not the code: whether memory should
  survive between burns — decided yes for now, which means a year of somebody's
  conversations sitting on their phone — and that "Lucy remembers what you told
  her" and "chat logs upload to a machine Marcus administers" are the same fact
  wearing two hats. The camp should hear that as one sentence.

- **The 6 GB tier is shipping untested.** iPhone 14, 14 Pro, 15 and 15 Plus get
  the small model and a warning. The threshold, the resident-memory estimate
  and the assumption that the 1B runs cool on that hardware are all reasoning,
  not measurement. The first tester report on one of those phones is worth more
  than any further analysis.

---

## 13. The camp server exists, and the plan was cut differently than asked

The backend spec describes chat-log upload, a gap list, tombstoned deletes,
lazy media fetch and a Vertex evaluation pipeline. The plan written against it
implements none of those. It is one vertical slice — health, join, note upload,
note list — carried all the way through to a phone.

The reason is this document. Sections 1, 5 and 6 are three separate accounts of
something that passed every local check and was wrong on the device: prompts
malformed for weeks with nothing looking broken, three correct-sounding haptic
theories that were each wrong, a download that had never once been run because
background `URLSession` does not exist in the Simulator. A complete backend
with a green test suite would say nothing about whether an iPhone can POST
multipart through Caddy carrying a keychain token. That is the risk, and
building the server whole would have put it in plan three of three.

What is live at `https://lucy.marcusfoster.com`:

- `GET /v1/health` — the reachability probe. Touches no database on purpose: a
  probe that failed because Postgres was slow would tell someone "no signal"
  while they are standing under Starlink.
- `POST /v1/join` — camp code in, bearer token out. Tokens are stored as
  SHA-256 digests, so a copy of the database is not a set of credentials.
- `POST /v1/notes` — multipart, idempotent.
- `GET /v1/notes?since=` — everyone's notes, on a server-assigned cursor.
- `GET /v1/notes/<id>/photo|memo`.
- Everything else — the camp instructions page, behind a codeword.

64 tests, against a real Postgres rather than a fake, because everything
load-bearing in the service is a Postgres behaviour: `ON CONFLICT`, an advisory
lock, arrays, and a `BIGSERIAL` whose ordering *is* the sync cursor. A stub
would have tested the stub.

Three decisions are load-bearing and easy to undo by accident. Each is
documented at the place someone would break it:

**The note's id comes from the phone.** That is the entire idempotency
mechanism. An upload that dies halfway leaves nothing behind that blocks the
retry, and the retry does not produce a second note. A server-assigned id
cannot do this.

**Blobs are content-addressed and never deleted.** Two members photograph the
same sign, produce byte-identical JPEGs, and get one file. Unlinking it when
one of them deletes their note would silently blind the other. Deletion
tombstones rows and leaves the disk alone.

**`seq` is assigned under an advisory lock.** A sequence hands out numbers in
request order while transactions commit in whatever order they finish. Without
the lock a client polling `?since=N` can step over a lower `seq` that committed
later and never see that note again — a bug that would appear as "sometimes a
note just doesn't sync" and would be close to impossible to reproduce.

### The VM, re-argued and kept

Serverless was raised as a better fit, and the answer is not "the spec says
so". Cloud Run scales to zero; the managed Postgres behind it does not, so the
cost is a wash. The burn is roughly three weeks out and the VM was already
running with DNS pointing at it.

What was worth conceding is that the VM *is* a liability, and the honest
response is to make leaving cheap rather than to argue it away. `media.py` was
the only module that assumed a filesystem, so its interface became `put` /
`get` / `exists` with no path in sight. Swapping in GCS — which is what Cloud
Run would need, its local disk being ephemeral — is now a new module rather
than a rewrite of the note routes. The disk implementation stays, because it is
the one a playa box would run either way.

---

## 14. Three deploy failures, each of which named the wrong cause

Every one of these produced an error message that pointed somewhere other than
the problem. Together they are the same lesson as §5, in a different domain.

**A `UnicodeDecodeError` about byte `0xa3`.** The api container died at startup
reading its own migration. The file is pure ASCII. What it was actually reading
was `._001_init.sql` — the AppleDouble sidecar macOS `tar` writes beside every
file it archives, which matches a `*.sql` glob and is binary. Nothing in the
error mentions tar, macOS, or a second file. Fixed twice over:
`COPYFILE_DISABLE=1` and `--exclude '._*'` when packing, and the migration
runner now skips dotfiles.

**Caddy: "illegal base64 data at input byte 2".** A bcrypt hash is full of `$`,
and it was written to `.env` through an unquoted remote heredoc, so `$2a`,
`$14` and `$FQdDyKJg` expanded to nothing on the far side. The stored value was
`a4.l.NRqv...`. Caddy's complaint about base64 is accurate and useless. `.env`
is `scp`'d now, and its values are single-quoted so docker compose's dotenv
parser does not have its own turn at them.

**A deploy that succeeded and changed nothing.** The Caddyfile is a bind mount.
Editing it and running `docker compose up -d` restarts nothing, because compose
sees no change to the caddy service — so the old configuration stays loaded
while every log line says the deploy worked. `push.sh` now reloads Caddy
explicitly. This one is the most dangerous of the three: the other two failed
loudly.

There is also a fourth, which was self-inflicted and worth naming: regenerating
`POSTGRES_PASSWORD` when rewriting `.env` broke authentication, because
Postgres only applies that variable when it initialises an empty data
directory. The volume kept the first password. Nothing was stored yet so the
directory was wiped, but on a burn-week deploy that would be data loss.

---

## 15. Knowing when to stop debugging and move the problem

The codeword gate on the instructions page began in Caddy, as `forward_auth`
with a `handle_response` block. It did not work: browsers got a bare 401 with
an empty body and no page. The config adapted and reloaded without complaint.
The file on the server was correct. The reload was confirmed. Reading it
explained nothing.

The thing that ended it was not more reading. It was replacing the block's
contents with `respond "GATE FIRED" 200` and observing that it still did not
fire — which converted an unbounded config question into a single fact, and
that fact was enough to stop. The check moved into the API: fifteen lines of
Python, seventeen tests, and Caddy left doing TLS, which it is unambiguously
good at.

Worth stating as a rule, because §5 is the same story with haptics: when a
component's behaviour cannot be explained after two or three honest attempts,
the cheapest next move is often to test whether it is running at all, and then
to move the responsibility somewhere observable — not to find the fourth
theory.

### And what the gate is not

It is not basic auth, because that means a browser dialog with a username field
and the camp does not have usernames — forty people typing "camp" into a
chrome-less popup that reads like a phishing attempt.

It is not a JavaScript gate either. The page is never sent to a browser that
has not passed, so it is not one `curl` away. This was verified against the
live host rather than asserted: with no cookie the response is the gate page
and `grep` for the instructions returns nothing, and `screens/people.png`
returns HTML rather than an image.

- **Letting campers correct her: designed, parked 2026-08-10.** Not built and
  not specced. The part worth keeping is the framing: "she's wrong" is five
  different failures wearing one face — retrieval picked the wrong record, the
  record is stale, the record is wrong, the phrasing garbled correct facts, or
  she had nothing and said so. One button collapses all five into a pile
  somebody then has to read by hand, so what a correction must capture is *what
  the right answer is*, in the camper's words.

  The design that follows from the invariant: a correction becomes **a fact**,
  not feedback — a row, attributed and dated, with authority over what it
  supersedes, flowing through the machinery that already exists. Two things
  fall out for free. It is the same mechanism as Help Lucy, since an unanswered
  question plus an answer and a wrong answer plus the right one are the same
  shape. And a (question, wrong answer, right answer) triple *is* an eval case,
  which is the pairwise-quality data the backend spec wanted and had no source
  for.

  The open question, which is the camp's and not the code's: who arbitrates
  when two members disagree. Recommendation on the table is that a correction
  applies immediately on the correcting person's phone — cheap, a `correction`
  table in the writable DB read by `Retrieval` ahead of the bundled rows, the
  same pattern `ChatLog` uses — and goes up for review before it reaches anyone
  else. A pure review queue means the corrector sees nothing happen and stops
  bothering; camp-wide-instant means one confident wrong person rewrites the
  manual for forty.
