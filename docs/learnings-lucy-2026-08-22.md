# Fine-tuning Lucy's voice — 2026-08-22

Four tuning jobs, one of which trained for fifty-eight seconds and looked like
a success. What follows is how to tell, and what the knobs actually do.

## A job can succeed and have trained almost nothing

`Lucy-2` (`5097364639700746240`, us-central1) reported
`JOB_STATE_SUCCEEDED` after sixty-seven minutes and produced a real merged
checkpoint. Its own TensorBoard says:

| step | epoch | learning rate | loss | perplexity |
|---|---|---|---|---|
| 1 | 1 | 2.0e-4 | 6.100 | 445.9 |
| 2 | 2 | 1.5e-4 | 6.029 | 415.4 |
| 3 | 3 | 0.5e-4 | 5.857 | 349.7 |

`train_runtime` is **58.2 seconds**. The other sixty-six minutes were GPU
provisioning, base-model load, and the LoRA merge. **Three optimizer steps.**

The cause is in the exported `debug.log`, which carries the axolotl config the
managed service built for us: `sample_packing: true`, `sequence_len: 8192`.
Our 903 examples average 73 input and 67 output tokens, so the whole dataset is
126,500 tokens — sixteen packed sequences of 8192. `train/tokens/total` reports
**131,072 tokens per step, which is exactly 16 × 8192**: one optimizer step
consumed the entire corpus. One step per epoch.

**So `epochCount` is a step count, and the run is full-batch gradient descent.**
That reads alarming and is not: a hundred epochs here is a hundred gradient
updates, which is the low end of normal for a LoRA, not a hundred SGD passes.
The overfitting intuition that says "never train 100 epochs on 903 examples"
is calibrated for minibatch training and does not transfer.

The general lesson is the one this repo keeps relearning: **a green terminal
state is not a result.** `check_shipped_db.py` exists because the Flash
regression passed every gate. Same shape here. Before converting or shipping a
tuned model, read the loss.

### Reading the loss without TensorBoard installed

The tuning job carries an `experiment` field pointing at a metadata context
whose `metadata` is empty; the numbers are in the `backing_tensorboard_resource`
it names. Fetch scalars with `…/timeSeries` then `:read`, or parse the
`events.out.tfevents.*` file included in the model export — it is a TFRecord
stream of `Event` protos and `scripts/`-adjacent throwaway code can read it
without a tensorboard dependency (`Event` field 2 = step, field 5 = `Summary`;
`Value` field 1 = tag, field 2 = `simple_value` as a little-endian float).

**`eval/loss` was never logged**, despite `eval_strategy: epoch` and a
validation file. Eval *ran* — `eval/runtime` × `eval/samples_per_second` works
out to ~99 samples, exactly our validation split — but no loss scalar came out.
The validation half currently buys nothing measurable. Worth fixing before
trusting a longer run not to memorise.

## The tuning API, field by field

Wrong turns cost four jobs. What is actually true:

- **`learningRateMultiplier` is rejected** for
  `google/gemma4@gemma-4-e2b-it`: *"Learning rate multiplier is not supported
  for base model …"*. Only `epochCount` and `adapterSize` are available. With
  the LR lever gone, `epochCount` is the only way to buy more training.
- **`outputUri` exists only in `v1beta1`.** It is a top-level field on
  `TuningJob`, a sibling of `baseModel`. The `v1` surface has no such field, so
  a `v1` request returns `Unknown name "outputUri"`, while omitting it returns
  *"The output_uri field is required for this model."* Two errors, one cause,
  and neither names the API version. The console uses `v1beta1`.
- **Roles are `user` and `model`.** The open-model-tuning docs show
  `assistant` in their GenerateContent example; the service rejects it:
  *"Supported roles are: user, model, function."* This one cost a failed job on
  903 examples after a code review flagged it correctly and was overruled on
  the strength of the doc snippet. When the documentation and the runtime
  disagree, believe the one that runs.
- **`systemInstruction` is supported and renders as `role: "system"`.** Gemma 4
  has a genuine `<|turn>system` turn — line 157 of its `chat_template.jinja` —
  so unlike Gemma 3 there is no folding of system text into the first user
  turn. The template also maps `assistant` → `model` itself, which is why the
  docs' example looks plausible.
- **`global` is a router, not a place.** A job created at
  `locations/global` executes in a region and its `tunedModel` resolves there.
  The same job appears in both listings. One `global` job fanned out to
  `europe-west4` and died on *"high demand in this region"* — a capacity
  failure, not a configuration one, and explicitly not charged.

Listing jobs needs the matching hostname: `https://aiplatform.googleapis.com`
for `global`, `https://<region>-aiplatform.googleapis.com` for a region.
Using the global host with a regional path returns 400 and looks like an
auth or permission problem.

## Getting the weights onto the phone

`scripts/tuned_model_to_gguf.sh` does the whole chain. What it encodes:

**Export destination must match the model's region.** Ours are in us-west1;
the tuned model lands in us-central1, and the export refuses with a
`FAILED_PRECONDITION` naming both regions.

**The export contains both a merged checkpoint and the bare adapter** —
`model.safetensors` at 9.6 GB alongside `adapter/adapter_model.safetensors` at
24 MB. Take the merged one. `debug.log` ends with *"Applied LoRA to 205/2011
tensors"*, so there is no gated base-model download and nothing to merge
locally. This also avoids teaching `LlamaHandle` about runtime LoRA loading,
which would be new Swift on a path the Simulator cannot prove.

**`convert_hf_to_gguf.py` is a 307-line shim.** The model classes moved into
`conversion/`, and `Gemma4ForConditionalGeneration` lives in
`conversion/gemma.py`. Grepping the entrypoint for "Gemma4" finds nothing and
suggests, wrongly, that Gemma 4 is unsupported.

Measured on this Mac: convert plus quantize is a few minutes, and the result
loads on Metal and generates at ~100 tokens/sec.

**The tuned Q4_K_M is 3.43 GB against the stock 3.11 GB**, a 10% increase from
a byte-identical architecture. The likely cause is that the two were quantized
by different llama.cpp versions making different per-tensor type choices, not
anything the tune did. On a phone that difference is worth confirming rather
than assuming — requantise the stock checkpoint with the same build before
concluding the tune is free.

**Turn markers survived**: the tuned checkpoint's template still uses
`<|turn>` / `<turn|>`, so `LlamaHandle.turnStyle` — which decides by tokenising
`<start_of_turn>` and counting tokens, never by filename — still takes the
Gemma 4 branch. Check this on any tuned model. A tokeniser that drifted would
send every prompt down the Gemma 3 path and nothing would look broken, which is
precisely the failure this repo already had once.

## What has not been established

- How Lucy-2 actually sounds. Everything above is training telemetry. The
  model converts and generates; it has not been put in front of the camp's
  real questions, and with a 4% loss reduction the expectation is that it is
  indistinguishable from stock.
- The untuned baseline **on today's database**. CLAUDE.md's 2% invention rate
  was measured 2026-08-20, before documents went 627 → 144, before the
  291-person roster entered a build at all, and before mention markup stopped
  eating 6% of messages. Comparing a tuned model on the new corpus against 2%
  on the old one would credit the corpus work to the LoRA. Measure the current
  shipping GGUF with `replay_prompts.py` and `score_answers.invented()` before
  `device.sh` replaces it — afterwards, re-measuring costs a reinstall.
