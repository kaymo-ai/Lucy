import Foundation
import llama

// mtmd.h and mtmd-helper.h ship inside llama.xcframework's Headers/ (verified
// at pinned SHA 08659901c43b51de735740f1cf61bb82fbe0c4e4) and are exposed
// through the same `llama` umbrella module.

enum LlamaError: Error {
    case modelLoadFailed
    case contextCreationFailed
    case tokenizationFailed
    case projectorLoadFailed
    case visionUnsupported
    case bitmapLoadFailed
    case decodeFailed(Int32)
}

final class LlamaHandle {
    // fileprivate, not private: the mtmd extension below needs these.
    fileprivate let model: OpaquePointer
    fileprivate let context: OpaquePointer
    fileprivate let vocab: OpaquePointer
    fileprivate var projector: OpaquePointer?

    /// How the weights were mapped. This is not a detail: under mmap the
    /// weights are file-backed *clean* pages, which the kernel may evict and
    /// re-fault, and which account differently from dirty anonymous memory.
    /// A footprint figure is uninterpretable without it, so it is logged.
    let loadModeName: String

    /// Gemma's instruction format. Without it the model tends to emit EOS as
    /// its very first token, which reads as an empty generation rather than as
    /// a failure -- the empty C5 caption in the first run was exactly this.
    static func chatWrap(_ prompt: String) -> String {
        "<start_of_turn>user\n\(prompt)<end_of_turn>\n<start_of_turn>model\n"
    }

    init(modelPath: String, contextTokens: Int32, useMmap: Bool = true) throws {
        llama_backend_init()

        var modelParams = llama_model_default_params()
        modelParams.n_gpu_layers = 999   // offload everything to Metal

        // LLAMA_LOAD_MODE_MMAP is the default at b10333 (llama-model.cpp:2445);
        // note `use_mmap` no longer exists as a bool on llama_model_params at
        // this tag, it is this enum now.
        //
        // This is THE variable that decides what phys_footprint means. Under
        // mmap the weights are file-backed clean pages, which iOS does not
        // charge to the footprint the same way -- hence a 2.9 GB model
        // "loading" into 210 MB. NONE forces the weights into dirty anonymous
        // memory and gives the pessimistic bound.
        modelParams.load_mode = useMmap ? LLAMA_LOAD_MODE_MMAP : LLAMA_LOAD_MODE_NONE
        self.loadModeName = useMmap ? "MMAP" : "NONE (no mmap)"

        guard let model = llama_model_load_from_file(modelPath, modelParams) else {
            throw LlamaError.modelLoadFailed
        }
        self.model = model

        guard let vocab = llama_model_get_vocab(model) else {
            llama_model_free(model)
            throw LlamaError.modelLoadFailed
        }
        self.vocab = vocab

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
        freeProjector()
        llama_free(context)
        llama_model_free(model)
        llama_backend_free()
    }

    /// Decodes one token id into bytes. Token pieces are not individually valid
    /// UTF-8, so bytes are accumulated and decoded once at the end.
    fileprivate func piece(_ token: llama_token) -> [UInt8] {
        var buf = [CChar](repeating: 0, count: 256)
        let n = llama_token_to_piece(vocab, token, &buf, Int32(buf.count), 0, true)
        guard n > 0 else { return [] }
        return buf.prefix(Int(n)).map { UInt8(bitPattern: $0) }
    }

    /// Decodes a single token, keeping the token buffer alive for the whole
    /// call. (Passing `&someLocal` and using the resulting batch on a LATER
    /// iteration would dangle -- Swift only guarantees the pointer for the
    /// duration of the call it is passed to.)
    fileprivate func decodeOne(_ token: llama_token) throws {
        var tok = token
        let rc = withUnsafeMutablePointer(to: &tok) { ptr -> Int32 in
            let batch = llama_batch_get_one(ptr, 1)
            return llama_decode(context, batch)
        }
        guard rc == 0 else { throw LlamaError.decodeFailed(rc) }
    }

    /// Empties the KV cache. Required between the text pass and the image
    /// pass: `mtmd_helper_eval_chunks` is called with `n_past = 0`, and if the
    /// cache still holds C3's tokens the image may never be encoded at all --
    /// which would show up as a suspiciously LOW C5 footprint, i.e. a false Go.
    func clearMemory() {
        llama_memory_clear(llama_get_memory(context), true)
    }

    func generate(prompt: String, maxTokens: Int32) throws -> String {
        var tokens = [llama_token](repeating: 0, count: prompt.utf8.count + 16)
        let tokenCount = llama_tokenize(vocab, prompt, Int32(prompt.utf8.count),
                                        &tokens, Int32(tokens.count), true, true)
        guard tokenCount > 0 else { throw LlamaError.tokenizationFailed }
        tokens = Array(tokens.prefix(Int(tokenCount)))

        // Prompt pass.
        let promptRC = tokens.withUnsafeMutableBufferPointer { buf -> Int32 in
            let batch = llama_batch_get_one(buf.baseAddress, Int32(buf.count))
            return llama_decode(context, batch)
        }
        guard promptRC == 0 else { throw LlamaError.decodeFailed(promptRC) }

        let sampler = llama_sampler_chain_init(llama_sampler_chain_default_params())
        llama_sampler_chain_add(sampler, llama_sampler_init_greedy())
        defer { llama_sampler_free(sampler) }

        var bytes: [UInt8] = []
        for _ in 0..<maxTokens {
            let next = llama_sampler_sample(sampler, context, -1)
            if llama_vocab_is_eog(vocab, next) { break }
            bytes.append(contentsOf: piece(next))
            try decodeOne(next)
        }

        return String(decoding: bytes, as: UTF8.self)
    }
}

// MARK: - Multimodal (C4/C5)

extension LlamaHandle {

    func loadProjector(path: String) throws {
        var params = mtmd_context_params_default()
        params.use_gpu = true
        params.n_threads = 4
        params.print_timings = false

        guard let ctx = mtmd_init_from_file(path, model, params) else {
            throw LlamaError.projectorLoadFailed
        }
        projector = ctx

        guard mtmd_support_vision(ctx) else {
            throw LlamaError.visionUnsupported
        }
    }

    func freeProjector() {
        if let ctx = projector {
            mtmd_free(ctx)
            projector = nil
        }
    }

    func describeImage(jpegPath: String, maxTokens: Int32) throws -> String {
        guard let mctx = projector else { throw LlamaError.projectorLoadFailed }

        // The media marker is where the image is spliced into the prompt.
        let marker = String(cString: mtmd_default_marker())
        let prompt = Self.chatWrap("\(marker)\nDescribe this photo in one sentence.")

        let wrapper = mtmd_helper_bitmap_init_from_file(mctx, jpegPath, false)
        guard let bitmap = wrapper.bitmap else { throw LlamaError.bitmapLoadFailed }
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
            var bitmaps: [OpaquePointer?] = [bitmap]
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
        let sampler = llama_sampler_chain_init(llama_sampler_chain_default_params())
        llama_sampler_chain_add(sampler, llama_sampler_init_greedy())
        defer { llama_sampler_free(sampler) }

        var bytes: [UInt8] = []
        for _ in 0..<maxTokens {
            let next = llama_sampler_sample(sampler, context, -1)
            if llama_vocab_is_eog(vocab, next) { break }
            bytes.append(contentsOf: piece(next))
            try decodeOne(next)
            nPast += 1
        }

        return String(decoding: bytes, as: UTF8.self)
    }
}
