"""Private, explicit local LoRA pilot. Import/status/export never run a model.

Only approved matched-task exports enter training. One recorded reference can
prove an adapter update, but cannot demonstrate correction learning or quality.
"""
from datetime import datetime, timezone
import argparse
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import uuid

import learning_store
import review_target_store
import review_task_store

ROOT = Path(__file__).resolve().parent
ADAPTERS_DIR = ROOT / "local_adapters"
MODEL_ID = "Qwen/Qwen2.5-1.5B-Instruct"
MLX_VERSION = "0.32.2"
MAX_JSON = 8 * 1024 * 1024
ID_PATTERN = re.compile(r"adapter_[0-9a-f]{32}\Z")
SHA_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
OPTIONS = {"iters": 20, "batch_size": 1, "num_layers": 4, "max_seq_length": 2048,
           "learning_rate": 1e-5, "seed": 17, "steps_per_report": 1, "save_every": 20}
EVALUATION = {"status": "not_available", "examples": 0,
              "message": "No independent approved evaluation examples are available. Training replay is in-sample and cannot establish improved quality."}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _digest(value):
    return hashlib.sha256(_encode(value)).hexdigest()


def _read(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_JSON:
            raise ValueError("Invalid local record")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            return json.loads(stream.read(MAX_JSON + 1), object_pairs_hook=review_task_store._unique)
    finally:
        os.close(descriptor)


def _write_new(path, value, raw=False):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(value if raw else _encode(value))
        stream.flush()
        os.fsync(stream.fileno())


def _file_hash(path, model_file=False):
    # Model snapshots may contain HF-cache symlinks; private evidence may not.
    if model_file:
        path = Path(path).resolve(strict=True)
    hasher = hashlib.sha256()
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Expected a regular evidence file")
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def _directory(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or not stat.S_ISDIR(path.lstat().st_mode):
        raise ValueError("Local adapter directory must be a real directory")
    os.chmod(path, 0o700)


def _attempt(identifier):
    if not isinstance(identifier, str) or not ID_PATTERN.fullmatch(identifier):
        raise ValueError("Invalid adapter identifier")
    path = ADAPTERS_DIR / identifier
    if ADAPTERS_DIR.is_symlink() or path.is_symlink() or not path.is_dir():
        raise ValueError("Unknown adapter attempt")
    return path


def _model_record(model_path, revision):
    if not SHA_PATTERN.fullmatch(revision or ""):
        raise ValueError("Supply the full resolved Hugging Face model commit")
    path = Path(model_path).expanduser().resolve(strict=True)
    if not path.is_dir():
        raise ValueError("Download the pinned official model first")
    config = _read((path / "config.json").resolve(strict=True))
    if config.get("model_type") != "qwen2" or config.get("auto_map"):
        raise ValueError("Only the locally loaded standard Qwen2 model is supported")
    files = sorted(p for p in path.iterdir() if p.is_file() and
                   (p.suffix in (".json", ".safetensors", ".txt", ".jinja") or p.name == "LICENSE"))
    if not any(p.suffix == ".safetensors" for p in files):
        raise ValueError("Model weights are missing")
    return {"id": MODEL_ID, "source": "https://huggingface.co/" + MODEL_ID,
            "revision": revision, "path": str(path), "licence": "Apache-2.0",
            "trustRemoteCode": False, "files": {p.name: _file_hash(p, model_file=True) for p in files}}


def _approved_export(case_id):
    case_result = learning_store.get_case(case_id)
    if not case_result.get("ok") or case_result["case"].get("split") != "training":
        raise ValueError("Only saved training cases can enter this pilot")
    result = review_target_store.export_target(case_id, read_connection=False)
    if not result.get("ok"):
        raise ValueError("The exact compact-task answer must have a saved approval")
    item = result["export"]
    source = item["source"]
    review_target_store._validate_source(source)
    review_task_store._validate_result(item["correctedAnswer"])
    if (item["format"] != "dino-compact-review-target-v1"
            or item["taskVersion"] != review_task_store.TASK_VERSION
            or source["sourceCaseId"] != case_id
            or item["sourceHash"] != review_target_store._hash(source)
            or item["approval"]["status"] != "approved"
            or item["messages"] != source["messages"]
            or item["input"] != source["input"]
            or item["inputHash"] != source["inputHash"]):
        raise ValueError("Export custody could not be validated")
    seeds = {c["course"]: c["seed"] for c in item["input"]["courses"]}
    if learning_store.is_evaluation_seeds(seeds):
        raise ValueError("Reserved evaluation courses cannot enter training")
    changed = item["correctedAnswer"] != source["originalAnswer"]
    expected_type = "corrected_answer" if changed else "confirmed_reference"
    if item["changedFromModel"] is not changed or item["reviewType"] != expected_type:
        raise ValueError("Reference/correction classification is inconsistent")
    return item


def prepare(model_path, revision, case_ids=None):
    """Freeze approved evidence and model hashes; no MLX import or model call."""
    if case_ids is None:
        listing = learning_store.list_cases()
        if not listing.get("ok") or listing.get("unavailable"):
            raise ValueError("Some saved cases are unavailable; choose a readable case explicitly")
        case_ids = [row["id"] for row in listing["cases"]
                    if review_target_store.get_target(row["id"], read_connection=False).get("target", {}).get("approval", {}).get("status") == "approved"]
    if not case_ids or len(case_ids) > 100 or len(set(case_ids)) != len(case_ids):
        raise ValueError("Choose one to 100 distinct approved matched-task cases")
    exports = [_approved_export(case_id) for case_id in case_ids]
    if len({item["inputHash"] for item in exports}) != len(exports):
        raise ValueError("Duplicate task inputs cannot become separate examples")
    model = _model_record(model_path, revision)
    rows = [{"messages": item["messages"] + [{"role": "assistant", "content":
             json.dumps(item["correctedAnswer"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))}]} for item in exports]
    dataset = b"".join(_encode(row) + b"\n" for row in rows)
    identifier = "adapter_" + uuid.uuid4().hex
    _directory(ADAPTERS_DIR)
    attempt = ADAPTERS_DIR / identifier
    attempt.mkdir(mode=0o700)
    (attempt / "data").mkdir(mode=0o700)
    manifest = {"format": "dino-local-adapter-pilot-v1", "adapterId": identifier,
        "createdAt": _now(), "taskVersion": review_task_store.TASK_VERSION, "model": model,
        "runtime": {"mlxLm": MLX_VERSION, "python": "3.12", "training": OPTIONS},
        "datasetHash": hashlib.sha256(dataset).hexdigest(), "trainingExamples": len(rows),
        "correctedExamples": sum(item["changedFromModel"] for item in exports),
        "confirmedReferences": sum(not item["changedFromModel"] for item in exports),
        "sources": [{"caseId": case_id, "targetId": item["targetId"], "targetRevision": item["targetRevision"],
                     "inputHash": item["inputHash"], "sourceHash": item["sourceHash"], "exportHash": _digest(item),
                     "reviewType": item["reviewType"], "courseSeeds": [c["seed"] for c in item["input"]["courses"]],
                     "split": "training"} for case_id, item in zip(case_ids, exports)],
        "splitPolicy": "All saved training-course families remain training; no record is copied into validation or test.",
        "independentEvaluation": EVALUATION}
    manifest["manifestHash"] = _digest(manifest)
    _write_new(attempt / "data" / "train.jsonl", dataset, raw=True)
    _write_new(attempt / "approved-exports.json", exports)
    _write_new(attempt / "manifest.json", manifest)
    return status(identifier)


def _manifest(attempt):
    manifest = _read(attempt / "manifest.json")
    if (manifest.get("manifestHash") != _digest({k: v for k, v in manifest.items() if k != "manifestHash"})
            or manifest.get("adapterId") != attempt.name
            or manifest.get("datasetHash") != _file_hash(attempt / "data" / "train.jsonl")):
        raise ValueError("Frozen manifest or dataset changed")
    if any((attempt / "data" / name).exists() for name in ("valid.jsonl", "test.jsonl")):
        raise ValueError("This pilot cannot claim a validation or test split")
    exports = _read(attempt / "approved-exports.json")
    if len(exports) != manifest["trainingExamples"] or len(exports) != len(manifest["sources"]):
        raise ValueError("Frozen source count changed")
    for item, source in zip(exports, manifest["sources"]):
        if _digest(item) != source["exportHash"]:
            raise ValueError("Frozen approved source changed")
    return manifest


def _base_view():
    return {"status": "not_started", "message": "Local adapter training has not started.",
            "model": {"id": MODEL_ID, "source": "https://huggingface.co/" + MODEL_ID, "revision": None},
            "trainingExamples": 0, "correctedExamples": 0, "confirmedReferences": 0,
            "adapterId": None, "weightsChanged": None, "baseline": None, "adapted": None,
            "independentEvaluation": EVALUATION, "exportAvailable": False}


def status(identifier=None):
    """Read-only public summary; unreadable records remain unknown, never empty."""
    view = _base_view()
    try:
        if identifier is None:
            if not ADAPTERS_DIR.exists():
                return {"ok": True, "localAdapter": view}
            if ADAPTERS_DIR.is_symlink():
                raise ValueError("Invalid storage")
            paths = sorted(ADAPTERS_DIR.glob("adapter_*"), key=lambda p: p.lstat().st_mtime_ns)
            if not paths:
                return {"ok": True, "localAdapter": view}
            identifier = paths[-1].name
        attempt = _attempt(identifier)
        manifest = _manifest(attempt)
        view.update({key: manifest[key] for key in ("adapterId", "trainingExamples", "correctedExamples", "confirmedReferences")})
        view["model"] = {key: manifest["model"][key] for key in ("id", "source", "revision", "licence")}
        view.update(status="prepared", message="Approved data is frozen. This local pilot has not run.")
        if (attempt / "started.json").exists():
            view.update(status="running", message="A local attempt was started. Completion is not yet recorded; interrupted attempts are not retried automatically.")
        if (attempt / "receipt.json").exists():
            receipt = _read(attempt / "receipt.json")
            if (receipt.get("receiptHash") != _digest({k: v for k, v in receipt.items() if k != "receiptHash"})
                    or receipt.get("manifestHash") != manifest["manifestHash"]):
                raise ValueError("Receipt changed")
            for name, digest in receipt.get("artifactHashes", {}).items():
                if Path(name).is_absolute() or ".." in Path(name).parts or _file_hash(attempt / name) != digest:
                    raise ValueError("Adapter evidence changed")
            view.update({key: receipt[key] for key in ("status", "message", "weightsChanged", "baseline", "adapted")})
            view.update(exportAvailable=True, trainingLossHistory=receipt.get("trainingLossHistory", []),
                        elapsedSeconds=receipt.get("elapsedSeconds"), completedAt=receipt["completedAt"])
        return {"ok": True, "localAdapter": view}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        view.update(status="unknown", message="Saved local adapter evidence is unreadable or changed. It has been preserved.", exportAvailable=False)
        return {"ok": False, "localAdapter": view}


def export(identifier=None):
    result = status(identifier)
    if not result["ok"] or not result["localAdapter"]["exportAvailable"]:
        return result | {"ok": False, "message": "A readable completed local attempt is required for export."}
    attempt = _attempt(result["localAdapter"]["adapterId"])
    manifest = _manifest(attempt)
    manifest["model"].pop("path", None)
    manifest["unredactedManifestHash"] = manifest.pop("manifestHash")
    return result | {"export": {"format": "dino-local-adapter-evidence-v1", "manifest": manifest,
                               "receipt": _read(attempt / "receipt.json"), "containsWeights": False,
                               "manifestPathRedacted": True}}


def _child(stage, attempt, env):
    command = [sys.executable, str(Path(__file__).resolve()), "_worker", stage, attempt.name]
    with open(attempt / (stage + ".log"), "xb") as log:
        os.chmod(log.name, 0o600)
        completed = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=1200)
    if completed.returncode:
        raise RuntimeError("Local " + stage + " step failed; inspect its private log")


def _adapter_parameters(path):
    config = _read(path)
    parameters = config.get("lora_parameters", {})
    if not isinstance(parameters, dict):
        raise ValueError("Saved adapter parameters are missing")
    scale, rank = parameters.get("scale"), parameters.get("rank")
    if (config.get("fine_tune_type") != "lora" or type(scale) not in (int, float)
            or not math.isfinite(scale) or scale <= 0 or type(rank) is not int or rank < 1):
        raise ValueError("Saved adapter configuration does not establish a finite LoRA scale and rank")
    return {"scale": scale, "rank": rank}


def _weight_evidence(path):
    parameters = _adapter_parameters(path.parent / "adapter_config.json")
    import numpy as np
    from safetensors.numpy import load_file
    tensors = load_file(str(path))
    stats = []
    for name, array in tensors.items():
        if name.endswith(".lora_b"):
            other = tensors.get(name[:-1] + "a")
            if (other is None or array.ndim != 2 or other.ndim != 2
                    or array.shape[0] != parameters["rank"] or other.shape[1] != parameters["rank"]
                    or not np.isfinite(array).all() or not np.isfinite(other).all()):
                raise ValueError("Missing or non-finite adapter tensors")
            delta = parameters["scale"] * (other.astype(np.float32) @ array.astype(np.float32))
            norm = float(np.linalg.norm(delta))
            if not math.isfinite(norm):
                raise ValueError("Non-finite adapter delta")
            stats.append({"name": name, "nonzeroB": int(np.count_nonzero(array)), "effectiveDeltaL2": norm})
    changed = bool(stats) and any(item["nonzeroB"] > 0 and item["effectiveDeltaL2"] > 0 for item in stats)
    return {"weightsChanged": changed, "method": "LoRA B starts at zero; inspect finite saved B and effective scale*A*B delta",
            "adapterParameters": parameters, "tensors": stats}


def _check_approvals(manifest):
    for source in manifest["sources"]:
        if _digest(_approved_export(source["caseId"])) != source["exportHash"]:
            raise ValueError("A source approval changed after preparation; training cannot start")


def run(identifier):
    """Explicit one-shot attempt. Never retries or replaces an earlier attempt."""
    attempt = _attempt(identifier)
    manifest = _manifest(attempt)
    _check_approvals(manifest)
    if sys.version_info[:2] != (3, 12) or importlib.metadata.version("mlx-lm") != MLX_VERSION:
        raise ValueError("Run with the dedicated Python 3.12 environment and mlx-lm 0.32.2")
    if _model_record(manifest["model"]["path"], manifest["model"]["revision"]) != manifest["model"]:
        raise ValueError("Pinned model files changed")
    runtime = {"python": sys.version.split()[0], "packages": {name: importlib.metadata.version(name)
               for name in ("mlx-lm", "mlx", "transformers", "numpy", "safetensors")}}
    _write_new(attempt / "started.json", {"startedAt": _now(), "manifestHash": manifest["manifestHash"], "pid": os.getpid()})
    started = time.perf_counter()
    previous_umask = os.umask(0o077)
    receipt = {"format": "dino-local-adapter-receipt-v1", "adapterId": identifier,
               "manifestHash": manifest["manifestHash"], "status": "failed", "weightsChanged": None,
               "baseline": None, "adapted": None, "independentEvaluation": EVALUATION,
               "runtime": runtime}
    # No keychain, no inherited service credentials and no model/network download.
    env = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL") if key in os.environ}
    env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1", DO_NOT_TRACK="1", NO_COLOR="1")
    try:
        _child("baseline", attempt, env)
        receipt["baseline"] = _read(attempt / "baseline.json")
        _check_approvals(manifest)
        _child("train", attempt, env)
        weight_info = _weight_evidence(attempt / "weights" / "adapters.safetensors")
        receipt.update(weight_info)
        receipt["trainingLossHistory"] = _read(attempt / "training-metrics.json")["trainingLossHistory"]
        _child("adapted", attempt, env)
        receipt["adapted"] = _read(attempt / "adapted.json")
        receipt.update(status="succeeded", message=("A local LoRA parameter update was verified. The replay uses training data; independent quality is unmeasured."
                       if weight_info["weightsChanged"] else "The local run completed, but no non-zero adapter update was verified."))
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        receipt["message"] = str(error)[:400]
    finally:
        receipt["artifactHashes"] = {str(path.relative_to(attempt)): _file_hash(path)
            for path in (attempt / "weights" / "adapters.safetensors", attempt / "weights" / "adapter_config.json",
                         attempt / "baseline.json", attempt / "adapted.json", attempt / "training-metrics.json") if path.exists()}
        receipt.update(completedAt=_now(), elapsedSeconds=round(time.perf_counter() - started, 3))
        receipt["receiptHash"] = _digest(receipt)
        _write_new(attempt / "receipt.json", receipt)
        os.umask(previous_umask)
    return status(identifier)


def _worker(stage, identifier):
    """Child process used only by explicit run; imports the optional MLX runtime."""
    attempt = _attempt(identifier)
    manifest = _manifest(attempt)
    rows = [json.loads(line) for line in (attempt / "data" / "train.jsonl").read_text().splitlines()]
    if stage == "train":
        _check_approvals(manifest)
        from mlx_lm import lora
        from mlx_lm.tuner.callbacks import SUPPORT_CALLBACK, TrainingCallback
        reports = []

        class LocalReceipt(TrainingCallback):
            def __init__(self, **unused):
                pass

            def on_train_loss_report(self, report):
                reports.append(report)

        # Register only a local callback, then use the package's official CLI.
        SUPPORT_CALLBACK["dino_local_receipt"] = LocalReceipt
        sys.argv = ["mlx_lm.lora", "--model", manifest["model"]["path"], "--train", "--data", str(attempt / "data"),
                    "--fine-tune-type", "lora", "--mask-prompt", "--adapter-path", str(attempt / "weights"),
                    "--report-to", "dino_local_receipt"]
        for key, value in OPTIONS.items():
            sys.argv.extend(["--" + key.replace("_", "-"), str(value)])
        lora.main()
        _write_new(attempt / "training-metrics.json", {"label": "in_sample_training_loss", "trainingLossHistory": reports})
        return
    if stage not in ("baseline", "adapted"):
        raise ValueError("Unknown local stage")
    import mlx.core as mx
    from mlx_lm import load, generate
    from mlx_lm.sample_utils import make_sampler
    started = time.perf_counter()
    model, tokenizer = load(manifest["model"]["path"], trust_remote_code=False,
        tokenizer_config={"trust_remote_code": False},
        adapter_path=str(attempt / "weights") if stage == "adapted" else None)
    lengths = [len(tokenizer.apply_chat_template(row["messages"], return_dict=False)) for row in rows]
    if max(lengths) > OPTIONS["max_seq_length"]:
        raise ValueError("The approved exchange exceeds the pilot token limit; training was not started or silently truncated")
    messages = rows[0]["messages"][:-1]
    prompt = tokenizer.apply_chat_template(messages, add_generation_prompt=True, return_dict=False)
    mx.random.seed(OPTIONS["seed"])
    text = generate(model, tokenizer, prompt=prompt, max_tokens=512, sampler=make_sampler(temp=0), verbose=False)
    answer = None
    try:
        candidate = json.loads(text, object_pairs_hook=review_task_store._unique)
        review_task_store._validate_result(candidate)
        answer = candidate
    except (ValueError, TypeError, KeyError):
        pass
    _write_new(attempt / (stage + ".json"), {"label": "in_sample_training_replay", "text": text,
        "answer": answer, "jsonValid": answer is not None, "elapsedSeconds": round(time.perf_counter() - started, 3),
        "timingIncludesModelLoad": True, "trainingSequenceTokens": lengths, "maxOutputTokens": 512,
        "inputHash": manifest["sources"][0]["inputHash"], "temperature": 0,
        "exactApprovedAnswerMatch": answer == json.loads(rows[0]["messages"][-1]["content"])})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepared = commands.add_parser("prepare")
    prepared.add_argument("--model-path", required=True)
    prepared.add_argument("--revision", required=True)
    prepared.add_argument("--case", action="append", dest="case_ids")
    for name in ("status", "export", "run"):
        command = commands.add_parser(name)
        command.add_argument("adapter_id", nargs=None if name == "run" else "?")
    worker = commands.add_parser("_worker", help=argparse.SUPPRESS)
    worker.add_argument("stage", choices=("baseline", "train", "adapted"))
    worker.add_argument("adapter_id")
    args = parser.parse_args()
    try:
        if args.command == "_worker":
            _worker(args.stage, args.adapter_id)
            return
        result = (prepare(args.model_path, args.revision, args.case_ids) if args.command == "prepare"
                  else globals()[args.command](args.adapter_id))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not result.get("ok") or (args.command == "run" and result["localAdapter"]["status"] != "succeeded"):
            raise SystemExit(1)
    except (OSError, ValueError, RuntimeError, importlib.metadata.PackageNotFoundError) as error:
        print(json.dumps({"ok": False, "message": str(error)[:400]}))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
