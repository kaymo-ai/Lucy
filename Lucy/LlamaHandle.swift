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
    ///
    /// The markers are NOT the same across Gemma generations, and getting this
    /// wrong is silent. Gemma 3 uses <start_of_turn>/<end_of_turn>. Gemma 4
    /// E2B uses <|turn>role and <turn|>, and does not have the Gemma 3 tokens
    /// in its vocabulary at all -- they tokenise as ordinary text, so every
    /// prompt arrives bracketed by characters the model was never trained to
    /// read as control tokens. The app shipped that way from the day the LLM
    /// was integrated, and every prompt tuned before 2026-08-10 was tuned
    /// against malformed input.
    ///
    /// Asking the vocabulary is the only reliable test. A filename says
    /// nothing, and both models answer plausibly enough with the wrong markers
    /// that nothing looks broken.
    static func chatWrap(_ prompt: String, style: TurnStyle) -> String {
        switch style {
        case .gemma3:
            return "<start_of_turn>user\n\(prompt)<end_of_turn>\n<start_of_turn>model\n"
        case .gemma4:
            return "<|turn>user\n\(prompt)<turn|>\n<|turn>model\n"
        }
    }

    enum TurnStyle { case gemma3, gemma4 }

    /// Which markers this model actually understands, decided by tokenising a
    /// marker and seeing whether the vocabulary has it as one token. A model
    /// that knows <start_of_turn> encodes it as exactly one; a model that does
    /// not splits it into several ordinary pieces.
    var turnStyle: TurnStyle {
        var tokens = [llama_token](repeating: 0, count: 16)
        let marker = "<start_of_turn>"
        let n = llama_tokenize(vocab, marker, Int32(marker.utf8.count),
                               &tokens, Int32(tokens.count), false, true)
        return n == 1 ? .gemma3 : .gemma4
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
        // The whole prompt goes through llama_decode in one batch, and
        // llama-context asserts n_tokens_all <= n_batch — so a prompt longer
        // than n_batch aborts the process rather than erroring. At 512 that was
        // every real question: persona plus a retrieved document passage is
        // well past it. Batch matches context, so anything that fits in the
        // window can be submitted.
        ctxParams.n_batch = UInt32(contextTokens)

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
    func clearContext() {
        llama_memory_clear(llama_get_memory(context), true)
    }

    /// Streaming variant. A 2B model on a phone produces maybe 15 tokens a
    /// second; waiting for the whole answer before showing anything makes it
    /// feel broken. `onToken` is called on whatever thread generation runs on.
    ///
    /// `register` sets temperature and top_k (DIAL 3 of the register
    /// experiment). No default value, deliberately: a default that silently
    /// falls back to `.default_`'s numbers is exactly the shape of bug this
    /// file has already shipped twice (hardcoded turn markers, a fixed
    /// sampler seed) -- a future edit that drops this argument should fail to
    /// compile, not quietly stop varying. It is a plain value here, not read
    /// from `Experiments.shared`: this runs on `queue`, a bare DispatchQueue
    /// with no actor, and `Experiments` is `@MainActor` -- the caller reads
    /// it where that is actually safe and passes the result in.
    func generate(prompt: String, maxTokens: Int32, register: Register,
                  onToken: (String) -> Void) throws {
        var tokens = [llama_token](repeating: 0, count: prompt.utf8.count + 16)
        let tokenCount = llama_tokenize(vocab, prompt, Int32(prompt.utf8.count),
                                        &tokens, Int32(tokens.count), true, true)
        guard tokenCount > 0 else { throw LlamaError.tokenizationFailed }
        tokens = Array(tokens.prefix(Int(tokenCount)))

        let promptRC = tokens.withUnsafeMutableBufferPointer { buf -> Int32 in
            let batch = llama_batch_get_one(buf.baseAddress, Int32(buf.count))
            return llama_decode(context, batch)
        }
        guard promptRC == 0 else { throw LlamaError.decodeFailed(promptRC) }

        let sampler = llama_sampler_chain_init(llama_sampler_chain_default_params())
        // A little sampling, not greedy: greedy makes her repeat herself across
        // turns, which reads as a stuck machine rather than a character.
        //
        // The seed must actually vary. dist(0) is a FIXED seed — identical
        // prompt, identical answer, every time — which silently undid the
        // whole point of this chain: the journal (2026-08-11) shows one
        // sentence surviving five generations character-for-character.
        llama_sampler_chain_add(sampler, llama_sampler_init_top_k(register.topK))
        llama_sampler_chain_add(sampler, llama_sampler_init_temp(register.temperature))
        llama_sampler_chain_add(sampler, llama_sampler_init_dist(UInt32.random(in: .min ... .max)))
        defer { llama_sampler_free(sampler) }

        // Bytes are emitted per token, but a multi-byte character can straddle
        // two tokens — decoding each token alone would print replacement
        // characters mid-word.
        var pending: [UInt8] = []
        for _ in 0..<maxTokens {
            let next = llama_sampler_sample(sampler, context, -1)
            if llama_vocab_is_eog(vocab, next) { break }
            pending.append(contentsOf: piece(next))
            if let text = String(bytes: pending, encoding: .utf8) {
                onToken(text)
                pending.removeAll(keepingCapacity: true)
            }
            try decodeOne(next)
        }
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
        let prompt = Self.chatWrap("\(marker)\nDescribe this photo in one sentence.",
                                   style: turnStyle)

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
