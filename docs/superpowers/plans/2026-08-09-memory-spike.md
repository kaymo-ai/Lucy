# Memory Spike Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Determine whether Gemma 4 E2B (Q4_K_M, 3.11 GB) plus the multimodal projector (986 MB) can be held resident on an 8 GB iPhone under iOS's per-app memory cap, and produce a go/no-go number that unblocks the iOS subsystems.

**Architecture:** A throwaway single-view iOS app that links a locally built llama.cpp xcframework, loads the models from the app's Documents directory, and samples `phys_footprint` and `os_proc_available_memory()` at defined checkpoints. No product code, no persistence, no UI beyond a results list.

**Tech Stack:** Swift 5.9+, Xcode 26.2, llama.cpp xcframework (pinned `b10333`), Metal, `mach/task_info.h`, `os/proc.h`.

## Global Constraints

- llama.cpp pinned at tag **`b10333`**. Record the exact commit SHA in the repo; never track `master`.
- xcframework built by llama.cpp's own `build-xcframework.sh` with `LLAMA_BUILD_MTMD=ON` (its default). Do not hand-roll the CMake invocation.
- Deployment target **iOS 16.4** minimum (the xcframework floor); the spike runs on **iOS 26.x**.
- Must run on a **physical iPhone 15 Pro or newer**. The Simulator does not reproduce iOS memory limits or Metal behaviour and its numbers are meaningless for this question.
- Entitlement `com.apple.developer.kernel.increased-memory-limit` must be present and confirmed active.
- Swift/C++ interop required (Xcode 15+/Swift 5.9+), including for nested dependencies.
- **This is a spike.** Nothing here is production code. It is deleted or archived once the number is recorded.

## Success Criteria

The spike answers one question with a number, at each checkpoint below:

| checkpoint | measure | pass condition |
|---|---|---|
| C1 baseline | `phys_footprint` before any load | recorded |
| C2 llama model loaded | `phys_footprint`, `os_proc_available_memory()` | app alive, >500 MB available |
| C3 after 256-token generation | peak `phys_footprint` | app alive, >300 MB available |
| C4 mmproj loaded | `phys_footprint`, available | app alive, >300 MB available |
| C5 after one image eval | peak `phys_footprint` | app alive, >200 MB available |

**Go** = C5 reached without jetsam. **Partial** = C3 passes, C5 fails → projector is deferred, on-device photo understanding drops from the design. **No-go** = C3 fails → drop to a Q3 quant and re-run the whole plan.

---

### Task 1: Build and vendor the llama.cpp xcframework

**Files:**
- Create: `SnailsNative/vendor/llama.cpp/` (git submodule)
- Create: `SnailsNative/vendor/build-llama-xcframework.sh`
- Create: `SnailsNative/vendor/README.md`
- Modify: `.gitignore` — add `SnailsNative/vendor/llama.xcframework/`

**Interfaces:**
- Consumes: nothing.
- Produces: `SnailsNative/vendor/llama.xcframework` containing `libllama.a`, `libggml*.a`, `libmtmd.a` and headers `llama.h`, `ggml.h`, `ggml-metal.h`, `gguf.h`, `mtmd.h`, `mtmd-helper.h`. Later tasks import it as `import llama`.

- [ ] **Step 1: Add llama.cpp as a pinned submodule**

```bash
cd ~/Snails
git submodule add https://github.com/ggml-org/llama.cpp.git SnailsNative/vendor/llama.cpp
cd SnailsNative/vendor/llama.cpp
git fetch --tags --depth 1 origin refs/tags/b10333:refs/tags/b10333
git checkout b10333
cd ~/Snails
git add .gitmodules SnailsNative/vendor/llama.cpp
```

- [ ] **Step 2: Record the exact commit so the build is reproducible**

```bash
cd ~/Snails/SnailsNative/vendor/llama.cpp
git rev-parse HEAD > ../llama.cpp.pinned-sha
cat ../llama.cpp.pinned-sha
```

Expected: a 40-character SHA printed. This file is committed; the xcframework itself is not.

- [ ] **Step 3: Write the build wrapper**

Create `SnailsNative/vendor/build-llama-xcframework.sh`:

```bash
#!/usr/bin/env bash
# Builds llama.xcframework (device + simulator, Metal + mtmd) from the pinned submodule.
# The xcframework is a build artifact and is NOT committed; run this after a fresh clone.
set -euo pipefail

VENDOR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="${VENDOR_DIR}/llama.cpp"
EXPECTED_SHA="$(cat "${VENDOR_DIR}/llama.cpp.pinned-sha")"
ACTUAL_SHA="$(git -C "${SRC_DIR}" rev-parse HEAD)"

if [ "${EXPECTED_SHA}" != "${ACTUAL_SHA}" ]; then
  echo "ERROR: llama.cpp submodule is at ${ACTUAL_SHA}, expected ${EXPECTED_SHA}" >&2
  echo "Run: git submodule update --init --recursive" >&2
  exit 1
fi

cd "${SRC_DIR}"
./build-xcframework.sh

rm -rf "${VENDOR_DIR}/llama.xcframework"
cp -R "${SRC_DIR}/build-apple/llama.xcframework" "${VENDOR_DIR}/llama.xcframework"

echo "Built ${VENDOR_DIR}/llama.xcframework from ${ACTUAL_SHA}"
```

```bash
chmod +x ~/Snails/SnailsNative/vendor/build-llama-xcframework.sh
```

- [ ] **Step 4: Run the build**

```bash
cd ~/Snails
./SnailsNative/vendor/build-llama-xcframework.sh
```

Expected: completes in 5–20 minutes. If `build-apple/` is not the output directory in this llama.cpp version, read the tail of `build-xcframework.sh` for the actual path and correct the wrapper's `cp` line.

- [ ] **Step 5: Verify the artifact contains the multimodal library and Metal**

```bash
cd ~/Snails/SnailsNative/vendor/llama.xcframework
ls
find . -name 'mtmd.h' -o -name 'ggml-metal.h' | head
find . -name '*.a' | head -20
```

Expected: an `ios-arm64` slice; `mtmd.h` and `ggml-metal.h` present in a `Headers/` directory. If `mtmd.h` is missing, the build ran with `LLAMA_BUILD_MTMD=OFF` — re-run with it forced on.

- [ ] **Step 6: Ignore the artifact, commit the inputs**

Append to `.gitignore`:

```
SnailsNative/vendor/llama.xcframework/
SnailsNative/vendor/llama.cpp/build-apple/
```

```bash
cd ~/Snails
git add .gitignore .gitmodules SnailsNative/vendor/llama.cpp SnailsNative/vendor/llama.cpp.pinned-sha SnailsNative/vendor/build-llama-xcframework.sh
git commit -m "build: vendor llama.cpp b10333 as pinned submodule with xcframework build script"
```

---

### Task 2: Create the spike app with the memory entitlement

**Files:**
- Create: `SnailsNative/spike/MemorySpike/MemorySpike.xcodeproj`
- Create: `SnailsNative/spike/MemorySpike/MemorySpike/MemorySpikeApp.swift`
- Create: `SnailsNative/spike/MemorySpike/MemorySpike/ContentView.swift`
- Create: `SnailsNative/spike/MemorySpike/MemorySpike/MemorySpike.entitlements`

**Interfaces:**
- Consumes: `llama.xcframework` from Task 1.
- Produces: a runnable app target named `MemorySpike` that links llama and displays a `[String]` results log.

- [ ] **Step 1: Create the Xcode project**

In Xcode: File → New → Project → iOS → App. Product Name `MemorySpike`, Interface SwiftUI, Language Swift. Save to `~/Snails/SnailsNative/spike/MemorySpike`.

Set the deployment target to iOS 16.4 and the team to your signing identity.

- [ ] **Step 2: Link the xcframework**

In the `MemorySpike` target → General → "Frameworks, Libraries, and Embedded Content" → `+` → "Add Other…" → "Add Files…" → select `~/Snails/SnailsNative/vendor/llama.xcframework`. Set it to **Do Not Embed** (it is a static library).

In Build Settings, set `C++ and Objective-C Interoperability` to `C++ / Objective-C++`.

- [ ] **Step 3: Add the increased memory limit entitlement**

Create `MemorySpike/MemorySpike.entitlements`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>com.apple.developer.kernel.increased-memory-limit</key>
    <true/>
</dict>
</plist>
```

In Build Settings, set `Code Signing Entitlements` to `MemorySpike/MemorySpike.entitlements`.

- [ ] **Step 4: Write the results UI**

Replace `ContentView.swift`:

```swift
import SwiftUI

@MainActor
final class SpikeLog: ObservableObject {
    @Published var lines: [String] = []

    func log(_ line: String) {
        print("SPIKE: \(line)")
        lines.append(line)
    }
}

struct ContentView: View {
    @StateObject private var log = SpikeLog()
    @State private var running = false

    var body: some View {
        NavigationStack {
            List(Array(log.lines.enumerated()), id: \.offset) { _, line in
                Text(line).font(.system(.caption, design: .monospaced))
            }
            .navigationTitle("Memory Spike")
            .toolbar {
                Button(running ? "Running…" : "Run") {
                    running = true
                    Task {
                        await SpikeRunner(log: log).run()
                        running = false
                    }
                }
                .disabled(running)
            }
        }
    }
}
```

- [ ] **Step 5: Add a stub runner so the project compiles**

Create `MemorySpike/SpikeRunner.swift`:

```swift
import Foundation

struct SpikeRunner {
    let log: SpikeLog

    func run() async {
        await log.log("runner not implemented yet")
    }
}
```

- [ ] **Step 6: Build and run on a physical device**

Run: Xcode → select your iPhone 15 Pro or newer → Cmd+R
Expected: app launches, "Run" button present, tapping it logs "runner not implemented yet".

- [ ] **Step 7: Confirm the entitlement is actually active**

Run on the device, then in Terminal:

```bash
codesign -d --entitlements - --xml \
  ~/Library/Developer/Xcode/DerivedData/MemorySpike-*/Build/Products/Debug-iphoneos/MemorySpike.app \
  2>/dev/null | plutil -p -
```

Expected: output contains `"com.apple.developer.kernel.increased-memory-limit" => 1`. If absent, the entitlement is not applied and every number this spike produces will be wrong — fix before continuing.

- [ ] **Step 8: Commit**

```bash
cd ~/Snails
git add SnailsNative/spike
git commit -m "spike: add MemorySpike app target with increased-memory-limit entitlement"
```

---

### Task 3: Memory instrumentation

**Files:**
- Create: `SnailsNative/spike/MemorySpike/MemorySpike/MemoryProbe.swift`

**Interfaces:**
- Consumes: nothing.
- Produces: `MemoryProbe.footprintBytes() -> UInt64`, `MemoryProbe.availableBytes() -> UInt64`, `MemoryProbe.describe(_ label: String) -> String`.

- [ ] **Step 1: Write the probe**

Create `MemoryProbe.swift`:

```swift
import Foundation
import os

enum MemoryProbe {

    /// Resident physical footprint of this process, as counted by jetsam.
    static func footprintBytes() -> UInt64 {
        var info = task_vm_info_data_t()
        var count = mach_msg_type_number_t(MemoryLayout<task_vm_info_data_t>.size / MemoryLayout<natural_t>.size)

        let result = withUnsafeMutablePointer(to: &info) { ptr in
            ptr.withMemoryRebound(to: integer_t.self, capacity: Int(count)) { intPtr in
                task_info(mach_task_self_, task_flavor_t(TASK_VM_INFO), intPtr, &count)
            }
        }

        guard result == KERN_SUCCESS else { return 0 }
        return UInt64(info.phys_footprint)
    }

    /// Bytes this process may still allocate before iOS terminates it.
    static func availableBytes() -> UInt64 {
        UInt64(os_proc_available_memory())
    }

    static func describe(_ label: String) -> String {
        let mb = { (b: UInt64) in String(format: "%.0f MB", Double(b) / 1_048_576.0) }
        return "\(label): footprint=\(mb(footprintBytes())) available=\(mb(availableBytes()))"
    }
}
```

- [ ] **Step 2: Wire it into the stub runner**

Replace the body of `SpikeRunner.run()`:

```swift
func run() async {
    await log.log(MemoryProbe.describe("C1 baseline"))
}
```

- [ ] **Step 3: Run on device and verify plausible numbers**

Run: Cmd+R, tap "Run"
Expected: a line like `C1 baseline: footprint=32 MB available=3800 MB`.

Two checks that the entitlement is live: `available` should be well above 2 GB on an 8 GB device. If it reads closer to 1.3 GB, the entitlement is not active — return to Task 2 Step 7.

- [ ] **Step 4: Commit**

```bash
cd ~/Snails
git add SnailsNative/spike/MemorySpike/MemorySpike/MemoryProbe.swift SnailsNative/spike/MemorySpike/MemorySpike/SpikeRunner.swift
git commit -m "spike: add memory footprint and available-memory probe"
```

---

### Task 4: Load Gemma 4 E2B and measure (C2)

**Files:**
- Create: `SnailsNative/spike/MemorySpike/MemorySpike/LlamaHandle.swift`
- Modify: `SnailsNative/spike/MemorySpike/MemorySpike/SpikeRunner.swift`

**Interfaces:**
- Consumes: `MemoryProbe` from Task 3.
- Produces: `LlamaHandle` — `init(modelPath: String, contextTokens: Int32) throws`, `func generate(prompt: String, maxTokens: Int32) throws -> String`, `deinit` frees. Task 6 extends it with projector loading.

- [ ] **Step 1: Download the model to the device**

```bash
cd /private/tmp/claude-501/-Users-marcus-Development-Snails/f1b66435-7666-4f2b-a21d-9d177cf31f4e/scratchpad
curl -L -o gemma-4-E2B-it-Q4_K_M.gguf \
  https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/resolve/main/gemma-4-E2B-it-Q4_K_M.gguf
ls -lh gemma-4-E2B-it-Q4_K_M.gguf
```

Expected: 3.11 GB.

Copy it onto the device: Xcode → Window → Devices and Simulators → select device → `MemorySpike` → gear icon → "Download Container…" is for reading; instead use the `+` under "Installed Apps" file sharing, or add `UIFileSharingEnabled` and `LSSupportsOpeningDocumentsInPlace` to Info.plist and drag the file into the app's Documents via Finder.

Simplest reliable route: add both keys to `Info.plist` as `YES`, rebuild, then use Finder → iPhone → Files → MemorySpike → drag the `.gguf` in.

- [ ] **Step 2: Write the llama wrapper**

Create `LlamaHandle.swift`:

```swift
import Foundation
import llama

enum LlamaError: Error {
    case modelLoadFailed
    case contextCreationFailed
    case tokenizationFailed
    case decodeFailed(Int32)
}

final class LlamaHandle {
    // fileprivate, not private: Task 6 adds an mtmd extension in this file
    // that needs `model`, `context` and `vocab`.
    fileprivate let model: OpaquePointer
    fileprivate let context: OpaquePointer
    fileprivate let vocab: OpaquePointer
    fileprivate static var projector: OpaquePointer?

    init(modelPath: String, contextTokens: Int32) throws {
        llama_backend_init()

        var modelParams = llama_model_default_params()
        modelParams.n_gpu_layers = 999   // offload everything to Metal

        guard let model = llama_model_load_from_file(modelPath, modelParams) else {
            throw LlamaError.modelLoadFailed
        }
        self.model = model
        self.vocab = llama_model_get_vocab(model)

        var ctxParams = llama_context_default_params()
        ctxParams.n_ctx = UInt32(contextTokens)
        ctxParams.n_batch = 512

        guard let context = llama_init_from_model(model, ctxParams) else {
            llama_model_free(model)
            throw LlamaError.contextCreationFailed
        }
        self.context = context
    }

    deinit {
        llama_free(context)
        llama_model_free(model)
        llama_backend_free()
    }

    func generate(prompt: String, maxTokens: Int32) throws -> String {
        var tokens = [llama_token](repeating: 0, count: prompt.utf8.count + 16)
        let tokenCount = llama_tokenize(vocab, prompt, Int32(prompt.utf8.count),
                                        &tokens, Int32(tokens.count), true, true)
        guard tokenCount > 0 else { throw LlamaError.tokenizationFailed }
        tokens = Array(tokens.prefix(Int(tokenCount)))

        var batch = llama_batch_get_one(&tokens, Int32(tokens.count))
        var output = ""
        var sampler = llama_sampler_chain_init(llama_sampler_chain_default_params())
        llama_sampler_chain_add(sampler, llama_sampler_init_greedy())
        defer { llama_sampler_free(sampler) }

        for _ in 0..<maxTokens {
            let rc = llama_decode(context, batch)
            guard rc == 0 else { throw LlamaError.decodeFailed(rc) }

            let next = llama_sampler_sample(sampler, context, -1)
            if llama_vocab_is_eog(vocab, next) { break }

            var buf = [CChar](repeating: 0, count: 256)
            let n = llama_token_to_piece(vocab, next, &buf, Int32(buf.count), 0, true)
            if n > 0 { output += String(cString: Array(buf.prefix(Int(n))) + [0]) }

            var nextToken = next
            batch = llama_batch_get_one(&nextToken, 1)
        }

        return output
    }
}
```

If any symbol above does not resolve, read the exact signature from `SnailsNative/vendor/llama.cpp/include/llama.h` at the pinned SHA and correct the call — the header at that SHA is the authority, not this plan.

- [ ] **Step 3: Measure C2 in the runner**

Replace `SpikeRunner.swift`:

```swift
import Foundation

struct SpikeRunner {
    let log: SpikeLog

    private var modelPath: String {
        let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        return docs.appendingPathComponent("gemma-4-E2B-it-Q4_K_M.gguf").path
    }

    func run() async {
        await log.log(MemoryProbe.describe("C1 baseline"))

        guard FileManager.default.fileExists(atPath: modelPath) else {
            await log.log("MODEL MISSING at \(modelPath)")
            return
        }

        do {
            let handle = try LlamaHandle(modelPath: modelPath, contextTokens: 4096)
            await log.log(MemoryProbe.describe("C2 model loaded"))
            _ = handle
        } catch {
            await log.log("C2 FAILED: \(error)")
        }
    }
}
```

- [ ] **Step 4: Run on device and record C2**

Run: Cmd+R, tap "Run"
Expected: `C2 model loaded: footprint=~3300 MB available=…`

Record the numbers. **If the app is killed here, that is a No-go** — record it and go to the Q3 fallback in the Success Criteria table.

Note: `n_ctx` is deliberately 4096 here, not 128000. Context KV cache scales with it and would dominate the measurement. Task 7 re-measures at a realistic working context.

- [ ] **Step 5: Commit**

```bash
cd ~/Snails
git add SnailsNative/spike
git commit -m "spike: load Gemma 4 E2B via llama.cpp and measure resident footprint"
```

---

### Task 5: Measure generation peak (C3)

**Files:**
- Modify: `SnailsNative/spike/MemorySpike/MemorySpike/SpikeRunner.swift`

**Interfaces:**
- Consumes: `LlamaHandle.generate(prompt:maxTokens:)` from Task 4.
- Produces: recorded C3 numbers.

- [ ] **Step 1: Add a generation pass and peak sampling**

Add to `SpikeRunner`, inside the `do` block after the C2 log:

```swift
var peak = MemoryProbe.footprintBytes()
let sampler = Task {
    while !Task.isCancelled {
        peak = max(peak, MemoryProbe.footprintBytes())
        try? await Task.sleep(nanoseconds: 50_000_000)
    }
}

let text = try handle.generate(
    prompt: "Explain in three sentences why a camp would keep a shared tool inventory.",
    maxTokens: 256
)
sampler.cancel()

await log.log("C3 generated \(text.count) chars")
await log.log(String(format: "C3 peak footprint=%.0f MB", Double(peak) / 1_048_576.0))
await log.log(MemoryProbe.describe("C3 settled"))
```

- [ ] **Step 2: Run on device and record C3**

Run: Cmd+R, tap "Run"
Expected: generated text appears, `C3 peak footprint` is logged, app survives.

Record peak and settled footprint, and the available-memory figure.

- [ ] **Step 3: Commit**

```bash
cd ~/Snails
git add SnailsNative/spike/MemorySpike/MemorySpike/SpikeRunner.swift
git commit -m "spike: measure peak footprint during 256-token generation"
```

---

### Task 6: Load the projector and measure image eval (C4, C5)

**Files:**
- Modify: `SnailsNative/spike/MemorySpike/MemorySpike/LlamaHandle.swift`
- Modify: `SnailsNative/spike/MemorySpike/MemorySpike/SpikeRunner.swift`

**Interfaces:**
- Consumes: `LlamaHandle` from Task 4.
- Produces: `LlamaHandle.loadProjector(path: String) throws` and `LlamaHandle.describeImage(jpegPath: String, maxTokens: Int32) throws -> String`.

- [ ] **Step 1: Download the projector and a test image**

```bash
cd /private/tmp/claude-501/-Users-marcus-Development-Snails/f1b66435-7666-4f2b-a21d-9d177cf31f4e/scratchpad
curl -L -o mmproj-F16.gguf \
  https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/resolve/main/mmproj-F16.gguf
ls -lh mmproj-F16.gguf
```

Expected: 986 MB. Copy it and any JPEG (name it `test.jpg`) into the app's Documents the same way as Task 4 Step 1.

- [ ] **Step 2: Add projector support**

The `LlamaHandle` fields `model` and `context` must be visible here, so change their declarations in Task 4's file from `private let` to `fileprivate let` and put this code in the same file.

Add to `LlamaHandle.swift`:

```swift
// mtmd.h and mtmd-helper.h ship inside llama.xcframework's Headers/ and are
// exposed through the same `llama` module.

extension LlamaHandle {

    func loadProjector(path: String) throws {
        var params = mtmd_context_params_default()
        params.use_gpu = true
        params.n_threads = 4
        params.print_timings = false

        guard let ctx = mtmd_init_from_file(path, model, params) else {
            throw LlamaError.modelLoadFailed
        }
        LlamaHandle.projector = ctx

        guard mtmd_support_vision(ctx) else {
            throw LlamaError.modelLoadFailed
        }
    }

    func freeProjector() {
        if let ctx = LlamaHandle.projector {
            mtmd_free(ctx)
            LlamaHandle.projector = nil
        }
    }

    func describeImage(jpegPath: String, maxTokens: Int32) throws -> String {
        guard let mctx = LlamaHandle.projector else {
            throw LlamaError.modelLoadFailed
        }

        // The media marker is where the image is spliced into the prompt.
        let marker = String(cString: mtmd_default_marker())
        let prompt = "\(marker)\nDescribe this photo in one sentence."

        let wrapper = mtmd_helper_bitmap_init_from_file(mctx, jpegPath, false)
        guard let bitmap = wrapper.bitmap else {
            throw LlamaError.tokenizationFailed
        }
        defer { mtmd_bitmap_free(bitmap) }

        guard let chunks = mtmd_input_chunks_init() else {
            throw LlamaError.tokenizationFailed
        }
        defer { mtmd_input_chunks_free(chunks) }

        var rc: Int32 = 0
        prompt.withCString { cText in
            var input = mtmd_input_text(text: cText,
                                        text_len: strlen(cText),
                                        add_special: true,
                                        parse_special: true)
            var bitmaps: [UnsafePointer<mtmd_bitmap>?] = [UnsafePointer(bitmap)]
            rc = mtmd_tokenize(mctx, chunks, &input, &bitmaps, 1)
        }
        guard rc == 0 else { throw LlamaError.decodeFailed(rc) }

        var nPast: llama_pos = 0
        let evalRC = mtmd_helper_eval_chunks(mctx, context, chunks,
                                             0,        // n_past
                                             0,        // seq_id
                                             512,      // n_batch
                                             true,     // logits_last
                                             &nPast)
        guard evalRC == 0 else { throw LlamaError.decodeFailed(evalRC) }

        // Sample the caption from the position the image left us at.
        var output = ""
        let sampler = llama_sampler_chain_init(llama_sampler_chain_default_params())
        llama_sampler_chain_add(sampler, llama_sampler_init_greedy())
        defer { llama_sampler_free(sampler) }

        for _ in 0..<maxTokens {
            let next = llama_sampler_sample(sampler, context, -1)
            if llama_vocab_is_eog(vocab, next) { break }

            var buf = [CChar](repeating: 0, count: 256)
            let n = llama_token_to_piece(vocab, next, &buf, Int32(buf.count), 0, true)
            if n > 0 { output += String(cString: Array(buf.prefix(Int(n))) + [0]) }

            var token = next
            var batch = llama_batch_get_one(&token, 1)
            let rc = llama_decode(context, batch)
            guard rc == 0 else { throw LlamaError.decodeFailed(rc) }
            nPast += 1
        }

        return output
    }
}
```

`LlamaHandle.projector` is already declared in the class body from Task 4.

These signatures are taken from `mtmd.h` and `mtmd-helper.h` at pinned SHA `b10333`. If the submodule is ever moved off that tag, re-check them — `mtmd_encode` is already deprecated in favour of `mtmd_encode_chunk`, so this surface does move.

- [ ] **Step 3: Measure C4 and C5**

Add to `SpikeRunner` after the C3 block:

```swift
let projPath = FileManager.default
    .urls(for: .documentDirectory, in: .userDomainMask)[0]
    .appendingPathComponent("mmproj-F16.gguf").path

guard FileManager.default.fileExists(atPath: projPath) else {
    await log.log("PROJECTOR MISSING — C4/C5 skipped")
    return
}

try handle.loadProjector(path: projPath)
await log.log(MemoryProbe.describe("C4 projector loaded"))

var peak5 = MemoryProbe.footprintBytes()
let sampler5 = Task {
    while !Task.isCancelled {
        peak5 = max(peak5, MemoryProbe.footprintBytes())
        try? await Task.sleep(nanoseconds: 50_000_000)
    }
}

let imgPath = FileManager.default
    .urls(for: .documentDirectory, in: .userDomainMask)[0]
    .appendingPathComponent("test.jpg").path
let caption = try handle.describeImage(jpegPath: imgPath, maxTokens: 128)
sampler5.cancel()

await log.log("C5 caption: \(caption.prefix(120))")
await log.log(String(format: "C5 peak footprint=%.0f MB", Double(peak5) / 1_048_576.0))
await log.log(MemoryProbe.describe("C5 settled"))
```

The caption's *quality* is not what this task measures — a garbled caption still proves the projector loaded, encoded an image, and did so within the memory budget. Only an outright failure or a jetsam kill is a negative result.

- [ ] **Step 4: Run on device and record C4/C5**

Run: Cmd+R, tap "Run"
Expected: all five checkpoints logged, app survives, a caption is produced.

**If the app is killed at C4 or C5, that is the Partial outcome** — record it. On-device photo understanding leaves the design and photo analysis is deferred to the Mac.

- [ ] **Step 5: Commit**

```bash
cd ~/Snails
git add SnailsNative/spike
git commit -m "spike: measure projector load and image eval footprint"
```

---

### Task 7: Re-measure at a realistic context and record the verdict

**Files:**
- Modify: `SnailsNative/spike/MemorySpike/MemorySpike/SpikeRunner.swift`
- Create: `docs/superpowers/specs/2026-08-09-memory-spike-results.md`

**Interfaces:**
- Consumes: all prior checkpoints.
- Produces: a committed results document that the iOS subsystem plans read before being written.

- [ ] **Step 1: Re-run at the context the product will actually use**

Change the `LlamaHandle` init in `SpikeRunner` to `contextTokens: 32768` and re-run all checkpoints.

32K is chosen deliberately: it is well beyond what retrieval will fill and far cheaper than 128K in KV cache. If 32K passes, the design has ample headroom; if only 4K passes, retrieval must budget chunks and that constraint goes into the iOS plan.

Run: Cmd+R, tap "Run"
Expected: a second full set of numbers. Record both sets.

- [ ] **Step 2: Write the results document**

Create `docs/superpowers/specs/2026-08-09-memory-spike-results.md` with the measured table filled in:

```markdown
# Memory Spike Results

**Date:** <date run>
**Device:** <exact model>, <iOS version>
**Build:** llama.cpp <pinned SHA>, Gemma 4 E2B Q4_K_M, mmproj-F16
**Entitlement active:** yes / no (evidence: <codesign output>)

## Measurements

| checkpoint | n_ctx=4096 footprint | n_ctx=32768 footprint | available | survived |
|---|---|---|---|---|
| C1 baseline | | | | |
| C2 model loaded | | | | |
| C3 generation peak | | | | |
| C4 projector loaded | | | | |
| C5 image eval peak | | | | |

## Verdict

**Go / Partial / No-go:** <one of these>

**Consequences for the architecture spec:**
- <e.g. on-device photo understanding stays / is deferred to the Mac>
- <e.g. working context is capped at N tokens>
- <e.g. quantization drops to Q3>
```

- [ ] **Step 3: Commit the results**

```bash
cd ~/Snails
git add docs/superpowers/specs/2026-08-09-memory-spike-results.md SnailsNative/spike
git commit -m "spike: record memory spike results and verdict"
```

- [ ] **Step 4: Update the architecture spec if the verdict is not Go**

If Partial or No-go, edit `docs/superpowers/specs/2026-08-09-lucy-pt-architecture-design.md`:
- §5 — correct the model stack table
- §6 Tier 2 — remove on-device photo understanding if C5 failed
- §8 — replace the risk with the measured outcome
- §10 — close the first open question

```bash
git add docs/superpowers/specs/2026-08-09-lucy-pt-architecture-design.md
git commit -m "docs: reconcile architecture spec with measured memory results"
```
