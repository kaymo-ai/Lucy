#!/bin/bash
#
# A tuned model on Agent Platform -> a Q4_K_M GGUF the phone can load.
#
#   ./scripts/tuned_model_to_gguf.sh <export-gcs-prefix> <name>
#
# e.g. ./scripts/tuned_model_to_gguf.sh \
#        gs://lucy-model-exports/model-8904725320788082688/custom-trained/2026-08-22T19:15:36.329300Z \
#        lucy-2
#
# Get that prefix by exporting the tuned model first. The export is a REST
# call, not a gcloud subcommand:
#
#   POST https://<region>-aiplatform.googleapis.com/v1/<model-resource>:export
#   {"outputConfig": {"exportFormatId": "custom-trained",
#                     "artifactDestination": {"outputUriPrefix": "gs://..."}}}
#
# Three things that cost time the first time round:
#
# - The destination bucket must be in the MODEL's region, not the project's
#   usual one. Ours live in us-west1; the tuned model lands in us-central1 and
#   the export refuses with FAILED_PRECONDITION naming both.
# - The export contains BOTH a merged `model.safetensors` and the bare LoRA in
#   `adapter/`. Take the merged one -- axolotl has already applied the adapter
#   ("Applied LoRA to 205/2011 tensors" in debug.log), so no gated download of
#   the base model is needed and there is nothing to merge locally.
# - `convert_hf_to_gguf.py` is a 307-line shim now; the Gemma classes live in
#   `conversion/gemma.py`. A checkout too old to have that directory will not
#   know `Gemma4ForConditionalGeneration`.
#
set -euo pipefail

SRC="${1:?usage: $0 <export-gcs-prefix> <name>}"
NAME="${2:?usage: $0 <export-gcs-prefix> <name>}"

WORK="${LUCY_GGUF_WORK:-$TMPDIR/lucy-gguf}"
HF="$WORK/$NAME-hf"
LLAMA="$WORK/llama.cpp"
VENV="$WORK/venv"
OUT_DIR="$(cd "$(dirname "$0")" && pwd)/models"
F16="$WORK/$NAME-f16.gguf"
Q4="$OUT_DIR/$NAME-Q4_K_M.gguf"

mkdir -p "$HF" "$OUT_DIR"

echo "=== 1/5  fetching the merged checkpoint"
# Not adapter/, not runs/, not debug.log -- just what the converter reads.
for f in config.json generation_config.json processor_config.json \
         tokenizer_config.json chat_template.jinja tokenizer.json \
         model.safetensors; do
  [ -f "$HF/$f" ] || gcloud storage cp "$SRC/$f" "$HF/$f"
done

echo "=== 2/5  llama.cpp"
if [ ! -d "$LLAMA" ]; then
  git clone --depth 1 https://github.com/ggml-org/llama.cpp.git "$LLAMA"
fi
if [ ! -x "$LLAMA/build/bin/llama-quantize" ]; then
  cmake -B "$LLAMA/build" -S "$LLAMA" -DCMAKE_BUILD_TYPE=Release \
        -DLLAMA_CURL=OFF -DGGML_METAL=ON -DLLAMA_BUILD_TESTS=OFF \
        -DLLAMA_BUILD_EXAMPLES=OFF
  cmake --build "$LLAMA/build" --config Release -j 8 \
        --target llama-quantize llama-cli
fi

echo "=== 3/5  python deps"
[ -d "$VENV" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -q torch numpy safetensors sentencepiece \
                            transformers protobuf

echo "=== 4/5  safetensors -> f16 gguf"
"$VENV/bin/python" "$LLAMA/convert_hf_to_gguf.py" "$HF" \
    --outtype f16 --outfile "$F16"

echo "=== 5/5  f16 -> Q4_K_M"
"$LLAMA/build/bin/llama-quantize" "$F16" "$Q4" Q4_K_M
rm -f "$F16"        # ~9 GB, and reproducible from the checkpoint

echo
echo "wrote $Q4"
ls -la "$OUT_DIR"/*.gguf

cat <<'EOF'

Smoke-test it before pushing to the phone -- the Simulator does not prove the
phone, but a model that cannot generate on the Mac will not generate anywhere:

  "$LLAMA/build/bin/llama-cli" -m "$Q4" --single-turn -ngl 99 \
      -p "What should I bring to the playa for a week?" -n 150

Check the turn markers survived. The FULL model must tokenise <|turn>/<turn|>;
LlamaHandle.turnStyle decides by tokenising, so a tuned model whose tokeniser
drifted would silently take the Gemma 3 branch:

  grep -oE '<[a-z_|][^>]*>' "$HF/chat_template.jinja" | sort -u
EOF
