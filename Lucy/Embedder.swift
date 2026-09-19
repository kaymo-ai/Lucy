import Foundation
import llama

// E7 — turning a question into a vector, on the phone.
//
// Retrieval is whole-word matching, which cannot get from "how do we get water
// delivered" to a row about picking up service vouchers: the two share no
// words. Five separate scoring changes were made in one day trying to close
// gaps of that shape, each fixing one case and exposing another, and
// docs/learnings-lucy-2026-08-09.md concluded that the pattern itself was the
// argument for embeddings rather than a sixth change.
//
// THE TASK PREFIX IS LOAD-BEARING AND ITS ABSENCE IS SILENT.
//
// EmbeddingGemma is trained asymmetrically: documents and queries carry
// different instruction prefixes. `queryPrefix` below must stay identical to
// QUERY_PREFIX in scripts/embed_claims.py, which encoded every vector in the
// database. Encode one side bare and cosine still returns numbers, ranking
// still happens and nothing errors -- it is merely worse, with nothing saying
// so. Measured on 400 real rows before this was written: bare encoding put the
// target row at rank 9 with a median cosine of 0.2362, prefixed put it at rank
// 6 with a median of 0.1206. The wider spread is the point; a high floor means
// the similarity carries less information.
//
// Optional by design. The encoder is a separate 333 MB file and a phone
// without it must still work: `shared` stays nil, retrieval runs exactly as it
// did before, and the journal says which happened. Semantic recall going
// missing is a degradation, never a failure to answer.

@MainActor
final class Embedder {
    /// nil when the encoder is not on this phone. Every caller treats that as
    /// "no semantic candidates", not as an error.
    static let shared: Embedder? = load()

    /// The file name the pipeline's model has. Sits beside the chat model in
    /// Documents rather than in the bundle, for the same reason that one does:
    /// App Store limits, and a file this size must be replaceable without a
    /// new build.
    static let fileName = "embeddinggemma-300M-Q8_0.gguf"

    /// Written by `scripts/embed_claims.py` into every row's `model_name`. The
    /// database is checked against it at load, because a vector produced by a
    /// different encoder is not detectably wrong -- cosine between mismatched
    /// vectors is just a number, which is the failure `scripts/package_db.py`
    /// was written about.
    static let modelName = "embeddinggemma-300M-Q8_0"

    /// 768 floats, little-endian Float32, as `pack_vector` writes them.
    static let dimensions = 768

    /// Identical to QUERY_PREFIX in scripts/embed_claims.py. Do not tune this
    /// without re-embedding the corpus; the two sides are one convention.
    private static let queryPrefix = "task: search result | query: "

    private let model: OpaquePointer
    private let vocab: OpaquePointer
    private let context: OpaquePointer

    /// nonisolated: the download delegate runs off the main actor and needs
    /// the destination before it can stage a file. It reads nothing but
    /// FileManager and a constant, so there is no state to be isolated.
    nonisolated static var path: String {
        let docs = FileManager.default.urls(for: .documentDirectory,
                                            in: .userDomainMask)[0]
        return docs.appendingPathComponent(fileName).path
    }

    private static func load() -> Embedder? {
        guard FileManager.default.fileExists(atPath: path) else {
            Journal.write("EMBEDDER absent — semantic recall off, lexical only")
            return nil
        }
        do {
            let e = try Embedder(modelPath: path)
            Journal.write("EMBEDDER loaded \(fileName)")
            return e
        } catch {
            // A broken encoder must not take the app down with it. Lexical
            // retrieval is the floor and it is a working floor.
            Journal.write("EMBEDDER failed to load — \(error). Lexical only")
            return nil
        }
    }

    private init(modelPath: String) throws {
        llama_backend_init()

        var modelParams = llama_model_default_params()
        // CPU. Not an oversight and not a placeholder.
        //
        // With n_gpu_layers = 999 this encoder returns a vector of the right
        // length, full of NaN. Nothing errors: embed() returns non-nil, cosine
        // against every stored vector is NaN, NaN clears no floor, and
        // semantic recall silently contributes nothing at all. E7 would have
        // looked finished and changed no answer. It was caught by
        // EmbedderLiveTests and by nothing else -- the same shape as the turn
        // marker bug, where the wrong thing was fed to the model for months
        // and nothing looked broken.
        //
        // Reproduced in the iOS 26.2 simulator; not tested on the device,
        // which may well be fine. That asymmetry is the argument FOR the CPU
        // rather than against it: the failure is silent, so the correct choice
        // is the path that cannot produce it, not the faster path that is
        // unverified on one of the two targets.
        //
        // The cost is small. This is a 300M model encoding one short sentence
        // per question, and it leaves Metal entirely to the 3.1 GB chat model
        // it runs alongside instead of competing with it for the GPU.
        modelParams.n_gpu_layers = 0
        modelParams.load_mode = LLAMA_LOAD_MODE_MMAP

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
        // A question is a sentence. 512 covers one many times over, and the
        // context is what the encoder actually costs in memory while the 3.1 GB
        // chat model is also resident.
        ctxParams.n_ctx = 512
        ctxParams.n_batch = 512
        ctxParams.embeddings = true
        // Mean pooling, named rather than left to GGUF metadata -- the same
        // choice embed_claims.py makes, and for the same reason: a model that
        // ships without a pooling type otherwise produces no sequence
        // embedding at all.
        ctxParams.pooling_type = LLAMA_POOLING_TYPE_MEAN

        guard let context = llama_init_from_model(model, ctxParams) else {
            llama_model_free(model)
            throw LlamaError.contextCreationFailed
        }
        self.context = context
    }

    deinit {
        llama_free(context)
        llama_model_free(model)
    }

    /// The question as a vector, or nil if it could not be encoded.
    ///
    /// Returned normalised, so callers compare with a plain dot product. The
    /// stored vectors are not normalised -- `pack_vector` writes what the model
    /// produced -- so `EntityStore` normalises those as it reads them, and the
    /// two meet as unit vectors.
    func embed(_ question: String) -> [Float]? {
        let text = Self.queryPrefix + question
        var tokens = [llama_token](repeating: 0, count: text.utf8.count + 16)
        let n = llama_tokenize(vocab, text, Int32(text.utf8.count),
                               &tokens, Int32(tokens.count), true, true)
        guard n > 0 else { return nil }

        llama_memory_clear(llama_get_memory(context), true)
        var batch = llama_batch_init(n, 0, 1)
        defer { llama_batch_free(batch) }
        batch.n_tokens = n
        for i in 0..<Int(n) {
            batch.token[i] = tokens[i]
            batch.pos[i] = llama_pos(i)
            batch.n_seq_id[i] = 1
            batch.seq_id[i]![0] = 0
            // Every position must be flagged for output, or mean pooling has
            // nothing to average and llama_get_embeddings_seq returns null.
            batch.logits[i] = 1
        }
        guard llama_decode(context, batch) >= 0 else { return nil }
        guard let raw = llama_get_embeddings_seq(context, 0) else { return nil }

        let dim = Int(llama_model_n_embd(model))
        guard dim == Self.dimensions else { return nil }
        var out = [Float](repeating: 0, count: dim)
        for i in 0..<dim { out[i] = raw[i] }
        // A NaN or infinite component poisons every comparison downstream and
        // reports as "nothing was close", which is indistinguishable from a
        // question the corpus genuinely does not cover. Refuse the vector
        // instead, and say so once: no semantic candidates is a state the
        // callers already handle correctly.
        guard out.allSatisfy({ $0.isFinite }) else {
            Journal.write("EMBEDDER produced a non-finite vector — "
                          + "semantic recall skipped for this question")
            return nil
        }
        return normalise(out)
    }
}

/// Unit length, guarding the zero vector rather than dividing by it.
func normalise(_ v: [Float]) -> [Float] {
    var sum: Float = 0
    for x in v { sum += x * x }
    let mag = sum.squareRoot()
    guard mag > 0 else { return v }
    return v.map { $0 / mag }
}

/// Both sides normalised, so this is cosine.
func dot(_ a: [Float], _ b: [Float]) -> Float {
    guard a.count == b.count else { return 0 }
    var sum: Float = 0
    for i in 0..<a.count { sum += a[i] * b[i] }
    return sum
}
