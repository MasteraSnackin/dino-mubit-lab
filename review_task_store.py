"""Durable text-only inference receipts, separate from correction training.

The approved correction is retained locally as provenance. It is never included
in the inference payload. A gateway response is not an accepted training target
or a verified adapter update.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import threading

import learning_store
import mubit_bridge

REVIEW_TASKS_DIR = Path(__file__).resolve().parent / "review_tasks"
TASK_VERSION = "tested-review-text-v1"
MAX_ATTEMPTS = 100
MAX_BYTES = 128 * 1024
MAX_INPUT_BYTES = 8192
_LOCK = threading.RLock()
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_ATTEMPT_ID = re.compile(r"review_[a-f0-9]{32}\Z")
_ASSESSMENTS = {"improved_on_both", "unchanged", "worse_on_both", "mixed", "inconclusive"}
_DECISIONS = {"retain_baseline", "test_more", "consider_candidate"}


class _Invalid(ValueError):
    pass


def _error(code, message):
    return {"ok": False, "code": code, "message": message}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _text(value, maximum):
    return mubit_bridge._proposal_text(value, maximum)


def _integer(value, minimum, maximum):
    return type(value) is int and minimum <= value <= maximum


def _encode(value, maximum=MAX_BYTES):
    try:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    except (TypeError, ValueError, RecursionError, OverflowError):
        raise _Invalid() from None
    if len(raw) > maximum:
        raise _Invalid()
    return raw


def _clone(value):
    return json.loads(_encode(value))


def _hash(value):
    return hashlib.sha256(_encode(value)).hexdigest()


def _input_hash(value):
    return _hash({key: item for key, item in value.items() if key != "runId"})


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _Invalid()
        result[key] = value
    return result


def _timestamp(value):
    if not _text(value, 40):
        raise _Invalid()
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _Invalid()
    return parsed


@contextmanager
def _storage_lock():
    with _LOCK:
        REVIEW_TASKS_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        if REVIEW_TASKS_DIR.is_symlink() or not stat.S_ISDIR(REVIEW_TASKS_DIR.lstat().st_mode):
            raise OSError()
        os.chmod(REVIEW_TASKS_DIR, 0o700)
        descriptor = os.open(REVIEW_TASKS_DIR / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise OSError()
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)


def _read_json(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
            raise _Invalid()
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise _Invalid()
        return json.loads(raw, object_pairs_hook=_unique)
    finally:
        os.close(descriptor)


def _write_json(path, value, *, new=False):
    raw = _encode(value)
    descriptor, temporary = tempfile.mkstemp(prefix=".pending-", dir=REVIEW_TASKS_DIR)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if new:
            os.link(temporary, path)
        else:
            if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
                raise OSError()
            os.replace(temporary, path)
        directory = os.open(REVIEW_TASKS_DIR, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _validate_input(payload):
    fields = {"source", "runId", "taskVersion", "protocol", "frameRate", "maxTicks", "baselineConfig", "candidateConfig", "originalProposal", "courses"}
    if (not isinstance(payload, dict) or set(payload) != fields
            or payload["source"] != "saved-tested-review" or payload["taskVersion"] != TASK_VERSION
            or payload["protocol"] != "native60-v1" or type(payload["frameRate"]) is not int or payload["frameRate"] != 60
            or type(payload["maxTicks"]) is not int or payload["maxTicks"] != 3600
            or not isinstance(payload["runId"], str) or not _ATTEMPT_ID.fullmatch(payload["runId"])
            or payload["baselineConfig"] != mubit_bridge.EXPERIMENT_BASELINE_CONFIG):
        raise _Invalid()
    proposal = payload["originalProposal"]
    mubit_bridge._validate_proposal_result(proposal)
    candidate = {**payload["baselineConfig"], proposal["parameter"]: proposal["value"]}
    if payload["candidateConfig"] != candidate:
        raise _Invalid()
    courses = payload["courses"]
    if not isinstance(courses, list) or len(courses) != 2:
        raise _Invalid()
    seeds = []
    for name, course in zip(("a", "b"), courses):
        if (not isinstance(course, dict) or set(course) != {"course", "seed", "baseline", "candidate"}
                or course["course"] != name or not _integer(course["seed"], 0, 0xffffffff)):
            raise _Invalid()
        seeds.append(course["seed"])
        for key in ("baseline", "candidate"):
            run = course[key]
            if (not isinstance(run, dict) or set(run) != {"score", "ticks", "outcome"}
                    or not _integer(run["score"], 0, 1000000) or not _integer(run["ticks"], 1, 3600)
                    or run["outcome"] not in ("collision", "time_limit")
                    or run["outcome"] == "time_limit" and run["ticks"] != 3600):
                raise _Invalid()
    if len(set(seeds)) != 2 or learning_store.is_evaluation_seeds(dict(zip(("a", "b"), seeds))):
        raise _Invalid()
    _encode(payload, MAX_INPUT_BYTES)
    # Use the gateway's wire-size and secret checks as well as the storage
    # contract, so a ready preview cannot exceed the actual request envelope.
    mubit_bridge._validate_tested_review(payload)


def _assessment(payload):
    deltas = [c["candidate"]["ticks"] - c["baseline"]["ticks"] for c in payload["courses"]]
    return ("improved_on_both" if all(d > 0 for d in deltas) else "worse_on_both" if all(d < 0 for d in deltas)
            else "unchanged" if all(d == 0 for d in deltas) else "mixed")


def _source(case_id):
    if not learning_store._id(case_id):
        return None, None, _error("invalid_id", "Choose a saved correction case.")
    found = learning_store.get_case(case_id)
    if not found.get("ok"):
        return None, None, _error("source_unavailable", "The saved correction case could not be read. Its evidence has not been replaced.")
    case = found["case"]
    source = case["source"]
    runs = {run["slot"]: run for run in source["observedRuns"]}
    payload = {"source": "saved-tested-review", "runId": "review_" + "0" * 32,
               "taskVersion": TASK_VERSION, "protocol": "native60-v1", "frameRate": 60, "maxTicks": 3600,
               "baselineConfig": _clone(source["baselineConfig"]), "candidateConfig": _clone(source["candidateConfig"]),
               "originalProposal": _clone(source["originalAnswer"]), "courses": []}
    for name in ("a", "b"):
        baseline, candidate = runs["baseline-" + name], runs["candidate-" + name]
        if baseline["seed"] != candidate["seed"]:
            raise _Invalid()
        payload["courses"].append({"course": name, "seed": baseline["seed"],
            "baseline": {key: baseline[key] for key in ("score", "ticks", "outcome")},
            "candidate": {key: candidate[key] for key in ("score", "ticks", "outcome")}})
    if learning_store.is_evaluation_seeds({c["course"]: c["seed"] for c in payload["courses"]}):
        return None, None, _error("reserved_evaluation", "Reserved test courses cannot enter this recorded Mubit review task.")
    payload["runId"] = "review_" + _input_hash(payload)[:32]
    _validate_input(payload)
    if (source["comparison"].get("status") != "complete" or source["comparison"].get("comparable") is not True
            or source["comparison"]["aggregate"]["conclusion"] != _assessment(payload)):
        raise _Invalid()
    return case, payload, None


def _validate_result(result):
    if (not isinstance(result, dict) or set(result) != {"assessment", "decision", "rationale", "caveat"}
            or result["assessment"] not in _ASSESSMENTS or result["decision"] not in _DECISIONS
            or not _text(result["rationale"], 600) or not _text(result["caveat"], 600)):
        raise _Invalid()


def _usage(value):
    keys = {"promptTokens", "completionTokens", "totalTokens"}
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) != keys
            or any(v is not None and not _integer(v, 0, 1000000000) for v in value.values())):
        raise _Invalid()
    return _clone(value)


def _validate_record(record):
    fields = {"schemaVersion", "id", "taskVersion", "inputHash", "input", "model", "sourceCaseId", "sourceCaseRevision",
              "sourceHash", "approvedTarget", "expectedAssessment", "status", "startedAt", "completedAt", "requestId",
              "latencyMs", "usage", "callsRemaining", "result", "assessmentMatchesMeasurements", "message",
              "approvedCorrectionSent", "modelUpdateVerified", "recordHash"}
    if (not isinstance(record, dict) or set(record) != fields or type(record["schemaVersion"]) is not int or record["schemaVersion"] != 1
            or record["taskVersion"] != TASK_VERSION or record["status"] not in ("pending", "succeeded", "failed")
            or record["approvedCorrectionSent"] is not False or record["modelUpdateVerified"] is not False
            or not learning_store._id(record["sourceCaseId"]) or not _integer(record["sourceCaseRevision"], 1, learning_store.MAX_REVISIONS)
            or not isinstance(record["sourceHash"], str) or not _HASH.fullmatch(record["sourceHash"])
            or not isinstance(record["model"], str) or not mubit_bridge._MODEL_ID.fullmatch(record["model"])
            or not _text(record["model"], 160) or not _text(record["message"], 1500)
            or record["recordHash"] != _hash({k: v for k, v in record.items() if k != "recordHash"})):
        raise _Invalid()
    _validate_input(record["input"])
    if (record["inputHash"] != _input_hash(record["input"]) or record["id"] != record["input"]["runId"]
            or record["id"] != "review_" + record["inputHash"][:32] or record["expectedAssessment"] != _assessment(record["input"])):
        raise _Invalid()
    target = record["approvedTarget"]
    if (not isinstance(target, dict) or set(target) != {"recommendation", "reason", "approval"}
            or not _text(target["reason"], 1000)):
        raise _Invalid()
    approval = target["approval"]
    if (not isinstance(approval, dict) or set(approval) != {"status", "draftRevision", "approvedAt", "identityVerified"}
            or approval["status"] != "approved" or approval["identityVerified"] is not False
            or not _integer(approval["draftRevision"], 1, record["sourceCaseRevision"] - 1)):
        raise _Invalid()
    _timestamp(approval["approvedAt"])
    recommendation = target["recommendation"]
    learning_store._recommendation(recommendation, set(recommendation.get("evidenceRefs", [])))
    started = _timestamp(record["startedAt"])
    if record["requestId"] is not None and not mubit_bridge._safe_identifier(record["requestId"], mubit_bridge._REQUEST_ID, ""):
        raise _Invalid()
    if not _integer(record["latencyMs"], 0, 3600000):
        raise _Invalid()
    _usage(record["usage"])
    if record["callsRemaining"] is not None and not _integer(record["callsRemaining"], 0, mubit_bridge.MAX_INFERENCE_ATTEMPTS):
        raise _Invalid()
    if record["status"] == "pending":
        if any(record[key] is not None for key in ("completedAt", "requestId", "usage", "callsRemaining", "result", "assessmentMatchesMeasurements")):
            raise _Invalid()
    else:
        if _timestamp(record["completedAt"]) < started:
            raise _Invalid()
        if record["status"] == "succeeded":
            _validate_result(record["result"])
            if (record["requestId"] is None or type(record["assessmentMatchesMeasurements"]) is not bool
                    or record["assessmentMatchesMeasurements"] != (record["result"]["assessment"] == record["expectedAssessment"])):
                raise _Invalid()
        elif record["result"] is not None or record["assessmentMatchesMeasurements"] is not None:
            raise _Invalid()
    return record


def _write_record(record, *, new=False):
    record["recordHash"] = _hash({k: v for k, v in record.items() if k != "recordHash"})
    _validate_record(record)
    _write_json(REVIEW_TASKS_DIR / (record["id"] + ".json"), record, new=new)


def _inventory():
    records = {}
    for path in REVIEW_TASKS_DIR.iterdir():
        if path.name == "task.json" or path.name.startswith(".pending-") or path.name == ".lock":
            continue
        if path.suffix != ".json" or not _ATTEMPT_ID.fullmatch(path.stem) or len(records) >= MAX_ATTEMPTS:
            raise _Invalid()
        record = _validate_record(_read_json(path))
        if record["id"] != path.stem:
            raise _Invalid()
        records[record["id"]] = record
    manifest = None
    try:
        manifest = _read_json(REVIEW_TASKS_DIR / "task.json")
    except FileNotFoundError:
        if records:
            raise _Invalid() from None
    if manifest is not None:
        if (not isinstance(manifest, dict) or set(manifest) != {"taskVersion", "model", "boundAt"}
                or manifest["taskVersion"] != TASK_VERSION or not _text(manifest["model"], 160)
                or not mubit_bridge._MODEL_ID.fullmatch(manifest["model"])):
            raise _Invalid()
        _timestamp(manifest["boundAt"])
        if any(record["model"] != manifest["model"] for record in records.values()):
            raise _Invalid()
    return manifest, records


def _view(case, payload, connection, manifest, attempt):
    approved = case.get("approval", {}).get("status") == "approved"
    configured_model = connection.get("model")
    model = manifest["model"] if manifest else configured_model
    verified = connection.get("verified") is True and connection.get("ready") is True
    changed = manifest is not None and configured_model != model
    allowed = approved and verified and not changed and attempt is None
    message = ("A request is already recorded for this exact input. It will not be sent again." if attempt else
               "Approve the current correction revision before requesting this text review." if not approved else
               "This task is bound to a different model. Restore its model setting before requesting a new review." if changed else
               "Check the Mubit connection before requesting this text review." if not verified else
               "One explicit text review is ready. The approved correction stays local and will not be sent as a training target.")
    return {"ok": True, "task": {"taskVersion": TASK_VERSION, "caseId": case["id"], "caseRevision": case["revision"],
            "model": model, "configuredModel": configured_model, "input": _clone(payload), "attempt": _clone(attempt),
            "expectedAssessment": case["source"]["comparison"]["aggregate"]["conclusion"],
            "connectionVerified": verified, "requestAllowed": allowed,
            "approvedCorrectionSent": False, "modelUpdateVerified": False, "message": message}}


def get_case_review(case_id, *, read_connection=True):
    try:
        case, payload, error = _source(case_id)
        if error:
            return error
        # Local training/export only needs the saved receipt, not credentials.
        connection = mubit_bridge.connection_status() if read_connection else {}
        with _storage_lock():
            manifest, records = _inventory()
            return _view(case, payload, connection, manifest, records.get(payload["runId"]))
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError, mubit_bridge._InvalidInput, mubit_bridge._InvalidResponse):
        return _error("review_unavailable", "The saved review evidence or receipt could not be read. Its contents are unknown and were preserved.")


def request_review(case_id, request):
    if (not isinstance(request, dict) or set(request) != {"expectedRevision"}
            or not _integer(request["expectedRevision"], 1, learning_store.MAX_REVISIONS)):
        return _error("invalid_request", "A text review requires the exact saved correction revision.")
    try:
        case, payload, error = _source(case_id)
        if error:
            return error
        if case["revision"] != request["expectedRevision"]:
            return _error("revision_conflict", "This correction changed. Reload its saved revision before requesting a review.")
        if case.get("approval", {}).get("status") != "approved":
            return _error("approval_required", "Approve the current saved correction before requesting this text review.")
        connection = mubit_bridge.connection_status()
        with _storage_lock():
            manifest, records = _inventory()
            if payload["runId"] in records:
                return {**_view(case, payload, connection, manifest, records[payload["runId"]]), "alreadyRequested": True}
            model = connection.get("model")
            if manifest is not None and model != manifest["model"]:
                return _error("task_model_changed", "This task is bound to a different model. Restore its model setting; no request was sent.")
            if not _text(model, 160) or not mubit_bridge._MODEL_ID.fullmatch(model) or connection.get("ready") is not True or connection.get("verified") is not True:
                return _error("connection_required", "Check the Mubit connection and its remaining call allowance first. No request was sent.")
            if len(records) >= MAX_ATTEMPTS:
                return _error("storage_full", "The text review record limit has been reached. Existing evidence was preserved.")
            if manifest is None:
                manifest = {"taskVersion": TASK_VERSION, "model": model, "boundAt": _now()}
                _write_json(REVIEW_TASKS_DIR / "task.json", manifest, new=True)
            record = {"schemaVersion": 1, "id": payload["runId"], "taskVersion": TASK_VERSION,
                "inputHash": _input_hash(payload), "input": payload, "model": model,
                "sourceCaseId": case["id"], "sourceCaseRevision": case["revision"], "sourceHash": case["sourceHash"],
                "approvedTarget": {"recommendation": _clone(case["latestDraft"]["recommendation"]),
                    "reason": case["latestDraft"]["reason"], "approval": _clone(case["approval"])},
                "expectedAssessment": case["source"]["comparison"]["aggregate"]["conclusion"],
                "status": "pending", "startedAt": _now(),
                "completedAt": None, "requestId": None, "latencyMs": 0, "usage": None, "callsRemaining": None,
                "result": None, "assessmentMatchesMeasurements": None,
                "message": "One text review was started. If interrupted, its outcome is unknown and it will not be resent.",
                "approvedCorrectionSent": False, "modelUpdateVerified": False}
            _write_record(record, new=True)
        try:
            response = mubit_bridge.review_tested_change(_clone(payload), expected_model=model)
        except Exception:
            response = {"ok": False, "message": "The text review failed. Its request outcome is unknown and it was not retried."}
        record = _clone(record)
        record.update(status="failed", completedAt=_now(), message="The text review did not return a usable result. It will not be retried.")
        if isinstance(response, dict):
            if _text(response.get("message"), 1500):
                record["message"] = response["message"]
            request_id = response.get("requestId")
            if mubit_bridge._safe_identifier(request_id, mubit_bridge._REQUEST_ID, ""):
                record["requestId"] = request_id
            if _integer(response.get("latencyMs"), 0, 3600000):
                record["latencyMs"] = response["latencyMs"]
            if _integer(response.get("callsRemaining"), 0, mubit_bridge.MAX_INFERENCE_ATTEMPTS):
                record["callsRemaining"] = response["callsRemaining"]
            try:
                record["usage"] = _usage(response.get("usage"))
            except _Invalid:
                pass
            if response.get("ok") is True and response.get("model") == model and record["requestId"] is not None:
                result = {key: response.get(key) for key in ("assessment", "decision", "rationale", "caveat")}
                try:
                    _validate_result(result)
                    record.update(status="succeeded", result=result,
                                  assessmentMatchesMeasurements=result["assessment"] == record["expectedAssessment"],
                                  message="Mubit returned a text review. This is inference evidence, not confirmation of a corrected training target or model update.")
                except _Invalid:
                    pass
        with _storage_lock():
            saved_manifest, records = _inventory()
            saved = records.get(record["id"])
            if saved is None or saved["status"] != "pending" or saved["inputHash"] != record["inputHash"]:
                return _error("receipt_conflict", "The pending review receipt changed. The outcome was not substituted; inspect the saved receipt.")
            _write_record(record)
            return _view(case, payload, connection, saved_manifest, record)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError, mubit_bridge._InvalidInput, mubit_bridge._InvalidResponse):
        return _error("review_unavailable", "The text review could not be confirmed. Reload its receipt before continuing; no automatic retry is allowed.")
