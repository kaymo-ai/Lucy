# Lucy on Android

Design settled 2026-08-11. Nothing here is built yet.

## What this is

A second Lucy, for the campmates whose phones are not iPhones. Same knowledge,
same invariant, same offline bar: the model runs on the phone and she answers
standing in the deep playa with no signal.

Full parity with the iOS app is the target — Ask, Memory, People, Sync, the
model gate, all of it. The Android app is a second client, not a lesser one.

## What already works, and is not re-litigated

**The knowledge is done.** `enriched_preview.db` is built by the existing
pipeline, redacted by `scripts/build_preview_db.py`, and gated by
`scripts/check_shipped_db.py`. Android ships the same file. None of that work is
touched, repeated, or forked.

**The model distribution is done.** `manifest.json` in the
`lucy-snails-releases` bucket lists both models with a SHA-256 each. Android
reads the same manifest from the same bucket. The bucket needs no uptime and
does not care what is asking.

Note that *which* model to fetch is not in the manifest — `isSmallForThisDevice`
is computed on the client from `Device`. So Android inherits the file list
unchanged and supplies its own choice, which is what makes the measured gate
below possible without touching the bucket or the iOS app.

**The backend is done.** `/v1/chatlog`, phone-assigned note ids, `?since=N`,
`seq` under an advisory lock, content-addressed blobs that are never deleted.
Android is another client against an unchanged contract. No endpoint is added
for it.

**The retrieval design is done.** Whole-word matching, the `overlap²` fact
score, the conditional blend of the previous question. Android translates this;
it does not redesign it.

## The constraints that actually shape this

**The invariant is the whole reason this is hard.** Retrieval supplies the
facts; the model only phrases them. Two clients means two implementations of
retrieval, and two implementations means they can disagree — the same question
answered from different rows on different phones. Every structural decision
below exists to make that divergence visible rather than silent.

**The iOS app must not be put at risk.** It is in testers' hands and the burn is
three weeks out. Nothing in this design edits a Swift file. The correct
architecture — one shared core consumed by both platforms — is deliberately
deferred for this reason and this reason only.

**The phone is mid-range, 6–8 GB.** That means the 806 MB
`gemma-3-1b-it-Q4_K_M`, not the 3.11 GB E2B. This is survivable precisely
because of the invariant: a 1B model is a worse writer, not a less accurate
one. It is not survivable for the prompt, which was tuned against E2B — see
below.

**There is no Metal.** `LlamaHandle` sets `n_gpu_layers = 999` and gets full
Metal offload for free. Android has no equivalent that can be relied on across
devices. Throughput on real Android silicon is the one number in this design
that is not known, and everything downstream of it is a guess until it is
measured.

**Sideloaded, not Play.** A new Play developer account requires a 14-day closed
test before production, which does not fit. Testers install an APK. `minSdk 29`,
`arm64-v8a` only.

## Architecture

Three Gradle modules.

```
android/
  app/     com.android.application  — Compose UI, ViewModels, CameraX, WorkManager
  core/    kotlin("jvm")            — Retrieval, stores, LucyVoice, LucyBrain, Sync
  llama/   com.android.library      — JNI bridge, NDK build of llama.cpp
```

`core/` is a **plain JVM module, not an Android library**. This is the load-
bearing decision in the whole design and it does two things:

1. An `import android.*` that creeps into retrieval logic **fails the build**.
   The rule is enforced by the toolchain rather than by anyone remembering it —
   the same reasoning that makes `turnStyle` ask the vocabulary instead of
   trusting a filename.
2. `core/` runs on the desktop JVM. Retrieval and voice get plain JUnit tests
   with no emulator and no device attached. Given that this is the layer the
   invariant lives in, it is the layer that needs the fastest test loop.

The module is also, not incidentally, already the shape Kotlin Multiplatform
common code takes. When the iOS app is safe to touch, converging the two
implementations becomes a build-file change rather than a rewrite. That is the
whole reason to accept duplicated logic now: the duplication is temporary by
construction.

### What moves for free

The knowledge DB, the GGUF files, the manifest, and the backend contract. All
four are formats or wire protocols, not platform artifacts.

### What is translated

| Swift | Kotlin |
|---|---|
| `Retrieval.swift` | Near line-for-line. Pure algorithm. |
| `EntityStore`, `PeopleStore` | Raw `SQLite3` C API → `SQLiteDatabase`. Queries verbatim. |
| `ChatLog` | Same, writable, app-internal storage. |
| `LucyVoice`, `LucyBrain` | Pure string composition. Translate carefully, test hardest. |
| `Sync` | `URLSession` → OkHttp. `CryptoKit` SHA-256 → `MessageDigest`. |

### What is rewritten

The 21 SwiftUI views become Compose. This is a dialect change, not a paradigm
change: `@StateObject` → `ViewModel` + `StateFlow`, `@Published` →
`MutableStateFlow`, `.task {}` → `LaunchedEffect`, `NavigationStack` →
`NavHost`. Platform services swap wholesale — `Speech` → `SpeechRecognizer`,
`AVFoundation` → CameraX, `Keychain` → Keystore-backed storage, background
`URLSession` → `WorkManager`.

## The JNI bridge

Swift imports C, so `LlamaHandle.swift` calls `llama_decode` directly. Kotlin
cannot. `llama/` holds a small C++ shim exposing roughly eight entry points —
load, free, tokenise, decode, sample, `clearContext`, `turnStyle`, generate —
with `external fun` declarations on the Kotlin side. llama.cpp ships an Android
example with this bridge already written; it is a starting point, not research.

The build goes through CMake via Gradle's `externalNativeBuild`, against the
same pinned SHA as the xcframework. **The pin is shared.** Two clients running
different llama.cpp revisions is a class of bug nobody will enjoy diagnosing in
a tent.

`turnStyle` carries over unchanged and is correct for free — it decides by
tokenising `<start_of_turn>` and counting, so the 1B model is detected as Gemma
3 without a filename ever being consulted.

## The prompt is not portable

`LucyBrain.prompt` was tuned against Gemma 4 E2B, after the marker bug was
fixed on 2026-08-10. The Android app runs Gemma 3 1B. A 1B model parrots its
prompt far more readily than a 2B one, which is precisely the failure CLAUDE.md
warns about: rules that illustrate themselves get emitted verbatim as answers.

So the Android prompt is its own piece of work with its own journal-reading
loop, not a copied constant. The rule holds harder here than on iOS: **describe
the transformation, never write a sentence she could paste.**

## The device gate

`ModelGate` on iOS chooses from a handful of known Apple SoCs, and
`Device.tooSmall` is `physicalMemory < 5.5 GiB`.

Neither the threshold nor the method survives the port.

**The threshold is a trap.** Android's `ActivityManager.MemoryInfo.totalMem`
reports *below* physical RAM — the kernel's reservation is not counted. An 8 GB
phone reads as roughly 7.3. Porting `marginal = < 7.5` unchanged would
misclassify exactly the mid-range devices this app is aimed at, and it would do
it silently.

**The method is worse.** Android's hardware spread is far too wide for RAM to
predict throughput, and there is no allowlist of SoCs worth maintaining.

So the Android gate **measures**. On first run, after the model is present, it
generates a short fixed completion and times it. That number decides ready,
marginal, or too-small, and it is written to the journal. Same screen as iOS,
same honesty — "THIS PHONE IS TOO SMALL" is better than ninety seconds of
spinner — but the verdict comes from the phone rather than from a table.

## Data flow

Unchanged from iOS in every respect that matters.

A question enters, `Retrieval.answer` pulls rows from the read-only knowledge
DB, `LucyVoice` composes them into facts-given, `LucyBrain` wraps that in the
prompt, the JNI bridge generates, and the answer streams to the UI a token at a
time. `ChatLog` records QUESTION / FACTS GIVEN / ANSWERED per turn, to the same
journal format, because that file is how prompt changes get judged.

Sync stays a button. Nothing reaches the network unless someone presses it.

## First run

Android cannot open a SQLite file from `assets/` — inside the APK it is a
compressed entry, not a file. So first launch copies `assets/knowledge.db` into
`filesDir` and opens it read-only, with `androidResources { noCompress += "db" }`
so the copy is not fighting decompression.

This is a visible cost on a cold install, and it belongs on the model-gate
screen with the download rather than hidden behind a spinner — for the same
reason the gate exists at all: an app that looks like it is working while it is
not is the failure this project keeps re-learning.

## Error handling

The rules are inherited, not invented.

- **The backend being unreachable costs nothing.** Every sync path fails
  quietly and the app is fully usable with the server down for the whole week.
- **A missing model is loud.** The gate blocks; there is no silent fallback to
  a worse brain.
- **A failed generation is not an empty answer.** An empty string from the
  bridge is an error, not a reply.
- **A note's id comes from the phone**, so a retry over a bad Starlink link is
  idempotent. The server must not assign it.

New to Android: the OS kills large-footprint processes far more readily than
iOS does. A generation interrupted by process death has to be recoverable on
relaunch rather than leaving a half-written turn in `ChatLog`.

## Testing

- **`core/` on the JVM, with JUnit.** Retrieval and voice, no device. The
  corpus questions that already exercise the iOS retriever become the fixture
  set, and both clients must agree on them.
- **Parity fixtures are the divergence alarm.** A shared list of question →
  expected-facts cases, run against both implementations. This is what makes
  the duplicated logic safe until it is deduplicated: drift fails a test
  instead of surprising someone in the desert.
- **The phone, not the emulator.** The project's own rule, and it binds harder
  here: an emulator says nothing about tokens/sec, which is the number this
  design is most exposed to.
- **`lucy-journal.txt` after every prompt change**, same as iOS.

## Sequencing

One thing has to happen before anything else is built: **measure generation
throughput for the 1B model on the actual phone, through the JNI bridge.**

This is not caution for its own sake. It is the only unknown in the design that
can invalidate the rest of it, and it is cheap to settle — the llama.cpp Android
example plus the real GGUF is a day at most. If the number is good, everything
above proceeds. If it is bad, the design changes shape while there is still time
for it to matter.

## Open questions

1. **APK size.** The knowledge DB is not built in this checkout, so it is not
   known whether it ships inside the APK or downloads alongside the model. The
   manifest already supports serving files, so the fallback exists.
2. **Speech.** Android's on-device `SpeechRecognizer` is OEM-variable and its
   offline behaviour is not guaranteed. whisper.cpp is the alternative and
   reuses the ggml build already being stood up for llama.cpp — at the cost of
   another model to download. Deferred until the throughput number is known,
   since it competes for the same CPU.
3. **Google's developer verification.** Announced to reach sideloaded apps in
   four countries around September 2026 and globally in 2027. A US phone at this
   year's burn should be unaffected, but this is close enough to the dates to
   be worth confirming, and it likely constrains next year.
4. **Camp secret.** `Backend.campSecret` is compiled into the iOS binary and is
   extractable from an IPA; that was an accepted level. An APK is meaningfully
   easier to open than an IPA. Whether that changes the calculus is the camp's
   call, not a technical one.
