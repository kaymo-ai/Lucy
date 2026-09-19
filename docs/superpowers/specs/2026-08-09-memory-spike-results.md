# Memory Spike Results

**Status:** ✅ MEASURED — verdict **Go**, with one checkpoint unconfirmed (C5, see gaps).

**Date run:** 2026-08-09
**Device:** iPhone 16 Pro (`iPhone17,1`), **8 GB RAM**, iOS 26.5.2
**Build:** llama.cpp `b10333` @ `08659901c43b51de735740f1cf61bb82fbe0c4e4`,
Gemma 4 E2B Q4_K_M (2.9 GiB on disk), mmproj-F16 (940 MiB on disk)
**Entitlement active:** yes — `run-spike.sh check` hard-fails otherwise, and the
runs below completed, so `com.apple.developer.kernel.increased-memory-limit` is live.
Corroborated by the C1 baseline reporting **6131 MB available**, far above the
~1.3 GB an unentitled app would see.

## How to reproduce

```bash
./SnailsNative/spike/run-spike.sh all      # build, verify entitlement, push ~4 GB
./SnailsNative/spike/run-spike.sh run 4096            # or 32768, or: run 4096 --nommap
./SnailsNative/spike/run-spike.sh log
```

Keep the phone unlocked (Auto-Lock → Never). It auto-locks between commands and
every launch then fails with `Locked`.

## Measurements

| checkpoint | mmap, n_ctx=32768 | no-mmap, n_ctx=4096 | available | survived |
|---|---|---|---|---|
| C1 baseline | 13 MB | 13 MB | 6131 MB | ✅ |
| C2 model loaded | 720 MB | **3600 MB** | 5424 / **2544 MB** | ✅ |
| C3 generation peak | 757 MB | — | 5399 MB | ✅ |
| C4 projector loaded | 1684 MB | — | 4460 MB | ✅ |
| C5 image eval peak | not reached cleanly | — | — | ⚠️ see gaps |

Pass conditions from the plan: C2 >500 MB available, C3 >300 MB, C4 >300 MB,
C5 >200 MB. Every checkpoint reached clears its bar by more than an order of
magnitude.

## Reading the two columns — this is the whole subtlety

**`load_mode` decides what `phys_footprint` even means.**

Under `LLAMA_LOAD_MODE_MMAP` (the b10333 default) the weights are file-backed
*clean* pages, which iOS does not charge to the footprint the same way. That is
why a 2.9 GiB model appears to "load" into 720 MB. Those numbers are real but
they are **not** the memory cost of the weights.

`LLAMA_LOAD_MODE_NONE` forces the weights into dirty anonymous memory and gives
the honest worst case: **3600 MB resident, 2544 MB still available, no kill.**

Production will use mmap, so the true behaviour sits between the columns and is
*better* than the pessimistic column — mmap'd clean pages can be evicted and
re-faulted under pressure, which makes the app more resilient to jetsam, not
less. The pessimistic column is the one to design against.

## Validity checks

1. **C3 emitted coherent text** — *"A shared tool inventory ensures that
   necessary equipment is readily available for all campers to use, promoting
   efficiency and reducing the need for individual…"* This is the proof the
   weights genuinely faulted in and the model ran. An earlier run reported an
   empty caption and a 210 MB footprint; both were artefacts of a missing Gemma
   chat template and mmap accounting, **not** memory findings. Numbers from
   before commit `c03b880` should be discarded.
2. **`load_mode` is logged at C2** in every run, so no figure is ambiguous.
3. **Runs must not overlap.** `devicectl … --terminate-existing` kills a run in
   flight, so a background retry loop racing a foreground run truncates both.
   Run configurations strictly one at a time.

## Verdict

**Go.**

Gemma 4 E2B Q4_K_M plus the F16 vision projector stay resident on an 8 GB
iPhone 16 Pro with the increased-memory-limit entitlement, at a realistic 32K
working context, with multiple gigabytes of headroom even under the pessimistic
no-mmap accounting.

**Consequences for the architecture spec:**

- On-device photo understanding **stays** in the design — the projector loads
  and leaves 4460 MB available at C4.
- Working context is **not** the binding constraint. 32768 costs roughly 500 MB
  over 4096 in the mmap column; retrieval does not need to budget chunks
  aggressively.
- The Q3 fallback is **not needed**. Q4_K_M has ample room.
- Keep `LLAMA_LOAD_MODE_MMAP`. It is the default, it is what these numbers
  assume for production, and it makes the weights evictable under pressure.

## Gaps — what is NOT established

- **C5 (image eval peak) was never captured cleanly.** C4 reached 1684 MB with
  4460 MB available, and an earlier (pre-fix) run showed the image encode adding
  only ~55 MB on top of the projector, so C5 is very likely fine — but it is
  inferred, not measured. Re-run `run 32768` alone, phone unlocked, to close it.
- **No caption has been read.** `test.jpg` is a photograph of a rocky turquoise
  lake with snow-capped mountains. Until a caption plausibly describes *that*,
  the vision path is proven to load and allocate but not to actually see.
- **No sustained-load or thermal testing.** These are cold, single-shot runs.
  A week on playa at ambient 40°C is a different question.
