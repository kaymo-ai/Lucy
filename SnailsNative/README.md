# SnailsNative

What remains of the native layer after the app moved to `Lucy/`. Two things
live here, and only the first is on the build path.

## `vendor/` — llama.cpp, pinned

`vendor/llama.cpp` is a git submodule pinned to a specific tag, recorded in
`vendor/llama.cpp.pinned-sha`. `vendor/build-llama-xcframework.sh` builds
`llama.xcframework` from it, which `Lucy/project.yml` links. The framework is
a build artifact and is not committed.

```bash
git submodule update --init --recursive
SnailsNative/vendor/build-llama-xcframework.sh
```

`vendor/README.md` explains why the pin matters: the multimodal C API moves
between llama.cpp releases, and an unpinned bump silently changes the surface
the Swift compiles against. `vendor/build-android` holds the Android build
of the same library for the Kotlin port, which lives on a separate branch.

## `spike/` — the memory spike

`spike/MemorySpike` is a throwaway app that answered one question before
Lucy was built: how large a model, at what context size, an iPhone can hold
in memory alongside everything else. `run-spike.sh` drives it end to end on
a physical device — build, push the models into Documents, confirm the
increased-memory-limit entitlement is live, run at a context size, pull the
log back. The results are written up in
`docs/superpowers/specs/2026-08-09-memory-spike-results.md`.

## `scripts/`

Two Python scripts from an earlier ingestion design, kept for reference.
Nothing current reads them; the pipeline is in the repository's top-level
`scripts/`.
