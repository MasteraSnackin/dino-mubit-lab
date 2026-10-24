LOCAL PARAMETER-UPDATE PILOT

This optional route trains real LoRA parameters locally on Apple silicon. It
does not update the Mubit-hosted model. Mubit memory/context is a separate route.
Importing local_adapter, reading status, or exporting evidence does not train,
download a model, access a key, or make a model/provider request.

Use a dedicated Python 3.12 environment with mlx-lm[train]==0.32.2. The official
Qwen/Qwen2.5-1.5B-Instruct model is Apache-2.0 (about 3.1 GB). Download its pinned
full Hugging Face commit first, retaining config/tokenizer files and safetensors.
The helper does not install dependencies or download models. Remote code is off.

From this directory, after installation and the authorised model download:

  .venv-local-training/bin/python local_adapter.py prepare \
    --model-path /absolute/path/to/pinned/model --revision FULL_40_CHARACTER_SHA

Optionally add --case CASE_ID (repeat for additional approved cases). Only
approved compact tested-review-text-v1 targets are eligible. Older proposal
correction schemas are excluded. Reserved test courses cannot enter the data.
The helper freezes the actual approved exports, source IDs, model and dataset
hashes. All training-course families stay in training. The single-row pilot has
train.jsonl only: do not create empty valid.jsonl or test.jsonl files.
Exact approvals are checked at run start, after baseline generation and again
inside the training child. A changed or revoked target stops training.

Then explicitly run the returned adapterId once:

  .venv-local-training/bin/python local_adapter.py run adapter_RETURNED_ID

This runs baseline generation, the installed MLX LoRA CLI, and adapted generation
in separate local processes. It uses 20 iterations, batch size 1, four layers,
rank 8, scale 20, learning rate 1e-5, seed 17 and prompt masking. Full approved
training exchanges are checked against 2048 tokens before training; longer
exchanges fail rather than being silently truncated. Generation uses identical
training input, greedy decoding and at most 512 output tokens. Time includes
loading each model and is not a production latency benchmark.

The MLX CLI is called with a local-only callback to preserve its actual reported
losses. No WandB or other remote reporting is enabled. Child processes run with
Hugging Face/Transformers offline and without inherited service credentials.

Every explicit attempt gets a new private directory (700) with private files
(600). A started attempt cannot be retried or overwritten. Failure, interruption
and missing evidence are preserved. Creating another attempt requires an
explicit prepare command; the helper never does so automatically.

After completion:

  python3 local_adapter.py status
  python3 local_adapter.py export

Status/export can run without MLX. They validate receipt and artifact hashes.
The export contains evidence, not model weights. The private model directory
path is redacted from the exported manifest. The original unredacted manifest
hash remains a custody reference and is identified as such in the export.

EVIDENCE LIMITS

Confirmed references and changed answers have separate counts. A confirmed
reference does not become a correction just because it was used for training.
The receipt checks actual saved finite LoRA B tensors (initially zero) and
effective adapter deltas using the scale and rank in the saved configuration;
a saved file alone is not called a verified update.
Training losses and both generations are explicitly in-sample. No independent
evaluation or improvement claim is made. A one-example pilot establishes a
local parameter-update path, not generalisation, improved game play, or full
completion of a correction-to-better-model challenge.

Official implementation references:
https://github.com/ml-explore/mlx-lm/tree/v0.32.2
https://github.com/ml-explore/mlx-lm/blob/v0.32.2/mlx_lm/LORA.md
https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct
