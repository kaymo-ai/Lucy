import Foundation

struct SpikeRunner {
    let log: SpikeLog
    let contextTokens: Int32
    var useMmap: Bool = true

    private var documents: URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
    }

    private func path(_ name: String) -> String {
        documents.appendingPathComponent(name).path
    }

    func run() async {
        await log.log("=== RUN n_ctx=\(contextTokens) mmap=\(useMmap) ===")
        await log.log(MemoryProbe.describe("C1 baseline"))

        let modelPath = path("gemma-4-E2B-it-Q4_K_M.gguf")
        guard FileManager.default.fileExists(atPath: modelPath) else {
            await log.log("MODEL MISSING at \(modelPath)")
            return
        }

        do {
            // --- C2: weights resident -------------------------------------
            let loadPeak = PeakSampler()
            let handle = try LlamaHandle(modelPath: modelPath,
                                        contextTokens: contextTokens,
                                        useMmap: useMmap)
            await log.log("C2 load peak footprint=\(MemoryProbe.mb(loadPeak.finish()))")
            await log.log("C2 load_mode=\(handle.loadModeName)")
            await log.log(MemoryProbe.describe("C2 model loaded"))

            // --- C3: generation peak --------------------------------------
            let genPeak = PeakSampler()
            let text = try handle.generate(
                prompt: LlamaHandle.chatWrap(
                    "Explain in three sentences why a camp would keep a shared tool inventory."),
                maxTokens: 256
            )
            await log.log("C3 peak footprint=\(MemoryProbe.mb(genPeak.finish()))")
            // The text itself, not just its length: coherent output is the only
            // proof the weights were really faulted in and the model really ran.
            await log.log("C3 text: \(text.prefix(160).replacingOccurrences(of: "\n", with: " "))")
            await log.log(MemoryProbe.describe("C3 settled"))

            // --- C4: projector resident -----------------------------------
            let projPath = path("mmproj-F16.gguf")
            guard FileManager.default.fileExists(atPath: projPath) else {
                await log.log("PROJECTOR MISSING — C4/C5 skipped")
                return
            }

            // C3 left its prompt and 256 generated tokens in the KV cache, and
            // the image pass below evaluates from n_past = 0.
            handle.clearMemory()

            try handle.loadProjector(path: projPath)
            await log.log(MemoryProbe.describe("C4 projector loaded"))

            // --- C5: image eval peak --------------------------------------
            let imgPath = path("test.jpg")
            guard FileManager.default.fileExists(atPath: imgPath) else {
                await log.log("TEST IMAGE MISSING — C5 skipped")
                return
            }

            let evalPeak = PeakSampler()
            let caption = try handle.describeImage(jpegPath: imgPath, maxTokens: 128)
            await log.log("C5 peak footprint=\(MemoryProbe.mb(evalPeak.finish()))")
            await log.log("C5 caption: \(caption.prefix(120))")
            await log.log(MemoryProbe.describe("C5 settled"))

            await log.log("=== RUN COMPLETE n_ctx=\(contextTokens) ===")
        } catch {
            await log.log("FAILED: \(error)")
        }
    }
}
