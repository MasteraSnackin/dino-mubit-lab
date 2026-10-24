"""Explicit Mubit reviews with approved local references, never weight training."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import threading

import learning_store
import mubit_bridge
import review_task_store
import review_target_store

MEMORY_REVIEWS_DIR = Path(__file__).resolve().parent / "memory_reviews"
TASK_VERSION = "memory-assisted-review-v1"
MAX_RECORDS = 100
MAX_BYTES = 256 * 1024
_LOCK = threading.RLock()
_ID = re.compile(r"memory_[a-f0-9]{32}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_ERRORS = (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError,
           mubit_bridge._InvalidInput, mubit_bridge._InvalidResponse)


class _Invalid(ValueError):
    pass


_clone = review_task_store._clone
_hash = review_task_store._hash
_text = review_task_store._text
_integer = review_task_store._integer
_now = review_task_store._now


def _error(code, message):
    return {"ok": False, "code": code, "message": message}


@contextmanager
def _storage_lock():
    with _LOCK:
        MEMORY_REVIEWS_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        if MEMORY_REVIEWS_DIR.is_symlink() or not stat.S_ISDIR(MEMORY_REVIEWS_DIR.lstat().st_mode):
            raise OSError()
        os.chmod(MEMORY_REVIEWS_DIR, 0o700)
        fd = os.open(MEMORY_REVIEWS_DIR / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise OSError()
            os.fchmod(fd, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)


def _read(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
            raise _Invalid()
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise _Invalid()
        return json.loads(raw, object_pairs_hook=review_task_store._unique)
    finally:
        os.close(fd)


def _write(record, *, new=False):
    record["recordHash"] = _hash({k: v for k, v in record.items() if k != "recordHash"})
    _validate_record(record)
    raw = review_task_store._encode(record, MAX_BYTES)
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=MEMORY_REVIEWS_DIR)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        path = MEMORY_REVIEWS_DIR / (record["id"] + ".json")
        if new:
            os.link(temporary, path)
        else:
            if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
                raise OSError()
            os.replace(temporary, path)
        descriptor = os.open(MEMORY_REVIEWS_DIR, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _snapshot(record):
    draft, approval = review_target_store._history_state(record["source"], record["history"])
    if approval["status"] != "approved":
        return None
    return {"targetId": record["id"], "targetRevision": len(record["history"]),
            "source": _clone(record["source"]), "sourceHash": record["sourceHash"],
            "targetRecordHash": record["recordHash"], "approvedAnswer": _clone(draft["answer"]),
            "reviewType": "corrected_answer" if draft["changedFromModel"] else "confirmed_reference",
            "reason": draft["reason"], "approval": _clone(approval)}


def _validate_memory(memory):
    if (not isinstance(memory, dict) or set(memory) != {"targetId", "targetRevision", "source", "sourceHash",
            "targetRecordHash", "approvedAnswer", "reviewType", "reason", "approval"}
            or not _integer(memory["targetRevision"], 2, review_target_store.MAX_REVISIONS)
            or not _text(memory["reason"], 1000)
            or not isinstance(memory["targetRecordHash"], str) or not _HASH.fullmatch(memory["targetRecordHash"])):
        raise _Invalid()
    review_target_store._validate_source(memory["source"])
    if (memory["sourceHash"] != _hash(memory["source"])
            or memory["targetId"] != review_target_store._identity(memory["source"])):
        raise _Invalid()
    mubit_bridge._validate_tested_review_result(memory["approvedAnswer"])
    changed = memory["approvedAnswer"] != memory["source"]["originalAnswer"]
    if memory["reviewType"] != ("corrected_answer" if changed else "confirmed_reference"):
        raise _Invalid()
    approval = memory["approval"]
    if (not isinstance(approval, dict) or set(approval) != {"status", "draftRevision", "approvedAt", "identityVerified"}
            or approval["status"] != "approved" or approval["identityVerified"] is not False
            or not _integer(approval["draftRevision"], 1, memory["targetRevision"] - 1)):
        raise _Invalid()
    review_task_store._timestamp(approval["approvedAt"])


def _wire(current, memories, model):
    identity = {"taskVersion": TASK_VERSION, "model": model,
                "currentInputHash": review_task_store._input_hash(current),
                "memories": [{"targetId": m["targetId"], "targetRevision": m["targetRevision"],
                              "recordHash": m["targetRecordHash"]} for m in memories]}
    return {"source": "saved-memory-review", "runId": "memory_" + _hash(identity)[:32],
            "taskVersion": TASK_VERSION, "current": _clone(current),
            "memories": [{"input": _clone(m["source"]["input"]), "approvedAnswer": _clone(m["approvedAnswer"]),
                          "reviewType": m["reviewType"]} for m in memories]}


def _select(current, model):
    with review_target_store._storage_lock():
        return _select_records(current, model, review_target_store._inventory())


def _select_records(current, model, records):
    memories = [memory for record in records.values() if (memory := _snapshot(record)) is not None
                and memory["source"]["inputHash"] != review_task_store._input_hash(current)]
    # Corrected answers are preferred, then matching controller parameter and
    # newer approvals. Exact duplicate inputs never count as extra memories.
    memories.sort(key=lambda m: (m["reviewType"] == "corrected_answer",
        m["source"]["input"]["originalProposal"]["parameter"] == current["originalProposal"]["parameter"],
        m["approval"]["approvedAt"], m["targetId"]), reverse=True)
    selected, seen = [], set()
    for memory in memories:
        _validate_memory(memory)
        hashed = memory["source"]["inputHash"]
        if hashed in seen:
            continue
        candidate = selected + [memory]
        try:
            mubit_bridge._validate_memory_review(_wire(current, candidate, model))
        except mubit_bridge._InvalidInput:
            continue  # Each source is valid; only the combined wire budget can fail here.
        selected = candidate
        seen.add(hashed)
        if len(selected) == 3:
            break
    return selected


def _source(case_id):
    if not learning_store._id(case_id):
        return None, None, _error("invalid_id", "Choose a saved comparison review.")
    found = review_task_store.get_case_review(case_id)
    case_result = learning_store.get_case(case_id)
    if not found.get("ok") or not case_result.get("ok"):
        return None, None, _error("source_unavailable", "The saved comparison could not be read. Existing memory receipts were preserved.")
    task, case = found["task"], case_result["case"]
    if task["caseId"] != case_id or case["id"] != case_id or task["caseRevision"] != case["revision"]:
        raise _Invalid()
    review_task_store._validate_input(task["input"])
    if task.get("attempt"):
        review_task_store._validate_record(task["attempt"])
        if task["attempt"]["input"] != task["input"]:
            raise _Invalid()
    return task, case, None


def _validate_record(record):
    fields = {"schemaVersion", "id", "taskVersion", "model", "caseId", "caseRevision", "sourceHash", "input",
              "memories", "status", "startedAt", "completedAt", "requestId", "latencyMs", "usage", "callsRemaining",
              "result", "assessmentMatchesMeasurements", "expectedAssessment", "message", "weightUpdate", "modelUpdateVerified", "recordHash"}
    if (not isinstance(record, dict) or set(record) != fields or type(record["schemaVersion"]) is not int
            or record["schemaVersion"] != 1 or record["taskVersion"] != TASK_VERSION
            or record["weightUpdate"] is not False or record["modelUpdateVerified"] is not False
            or not learning_store._id(record["caseId"]) or not _integer(record["caseRevision"], 1, learning_store.MAX_REVISIONS)
            or not _text(record["model"], 160) or not mubit_bridge._MODEL_ID.fullmatch(record["model"])
            or not isinstance(record["sourceHash"], str) or not _HASH.fullmatch(record["sourceHash"])
            or record["status"] not in ("pending", "succeeded", "failed") or not _text(record["message"], 1500)
            or record["recordHash"] != _hash({k: v for k, v in record.items() if k != "recordHash"})):
        raise _Invalid()
    if not isinstance(record["memories"], list) or not 1 <= len(record["memories"]) <= 3:
        raise _Invalid()
    for memory in record["memories"]:
        _validate_memory(memory)
    review_task_store._validate_input(record["input"]["current"])
    mubit_bridge._validate_memory_review(record["input"])
    if (record["input"] != _wire(record["input"]["current"], record["memories"], record["model"])
            or record["id"] != record["input"]["runId"] or record["expectedAssessment"] != review_task_store._assessment(record["input"]["current"])):
        raise _Invalid()
    started = review_task_store._timestamp(record["startedAt"])
    if record["requestId"] is not None and not mubit_bridge._safe_identifier(record["requestId"], mubit_bridge._REQUEST_ID, ""):
        raise _Invalid()
    if not _integer(record["latencyMs"], 0, 3600000):
        raise _Invalid()
    review_task_store._usage(record["usage"])
    if record["callsRemaining"] is not None and not _integer(record["callsRemaining"], 0, mubit_bridge.MAX_INFERENCE_ATTEMPTS):
        raise _Invalid()
    if record["status"] == "pending":
        if any(record[k] is not None for k in ("completedAt", "requestId", "usage", "callsRemaining", "result", "assessmentMatchesMeasurements")):
            raise _Invalid()
    else:
        if review_task_store._timestamp(record["completedAt"]) < started:
            raise _Invalid()
        if record["status"] == "succeeded":
            mubit_bridge._validate_tested_review_result(record["result"])
            if (record["requestId"] is None or type(record["assessmentMatchesMeasurements"]) is not bool
                    or record["assessmentMatchesMeasurements"] != (record["result"]["assessment"] == record["expectedAssessment"])):
                raise _Invalid()
        elif record["result"] is not None or record["assessmentMatchesMeasurements"] is not None:
            raise _Invalid()
    return record


def _inventory():
    records = {}
    for path in MEMORY_REVIEWS_DIR.iterdir():
        if path.name == ".lock" or path.name.startswith(".pending-"):
            continue
        if path.suffix != ".json" or not _ID.fullmatch(path.stem) or len(records) >= MAX_RECORDS:
            raise _Invalid()
        record = _validate_record(_read(path))
        if record["id"] != path.stem:
            raise _Invalid()
        records[record["id"]] = record
    return records


def _baseline(task, model):
    attempt = task.get("attempt")
    if not attempt or attempt["status"] != "succeeded":
        return None
    return {**{k: _clone(attempt[k]) for k in ("model", "requestId", "latencyMs", "usage", "result", "assessmentMatchesMeasurements")},
            "sameModel": attempt["model"] == model, "sameInput": True}


def _existing(records, current, model):
    hashed = review_task_store._input_hash(current)
    matching = [record for record in records.values() if record["model"] == model
                and review_task_store._input_hash(record["input"]["current"]) == hashed]
    return min(matching, key=lambda r: (r["startedAt"], r["id"])) if matching else None


def _view(task, case, model, connection, memories, wire, attempt):
    approved = case.get("approval", {}).get("status") == "approved"
    ready = connection.get("ready") is True and connection.get("verified") is True
    allowed = approved and ready and bool(memories) and attempt is None
    message = ("This memory-assisted request is already recorded and will not be sent again." if attempt else
               "Approve the current saved comparison review before using memory." if not approved else
               "No other approved text-review input is available as memory. This exact input is excluded." if not memories else
               "Check the Mubit connection before requesting a memory-assisted review." if not ready else
               "One explicit review can use the approved references shown below. This changes prompt context, not model weights.")
    seeds = {c["seed"] for c in task["input"]["courses"]}
    summaries = [{"targetId": m["targetId"], "targetRevision": m["targetRevision"], "sourceHash": m["sourceHash"],
                  "inputHash": m["source"]["inputHash"], "reviewType": m["reviewType"],
                  "sourceCaseId": m["source"]["sourceCaseId"], "model": m["source"]["model"],
                  "requestId": m["source"]["requestId"], "approvedAt": m["approval"]["approvedAt"],
                  "sameCourseFamily": seeds == {c["seed"] for c in m["source"]["input"]["courses"]}}
                 for m in memories]
    return {"ok": True, "memoryReview": {"caseId": case["id"], "caseRevision": case["revision"], "taskVersion": TASK_VERSION,
            "model": model, "input": _clone(wire), "inputHash": _hash(wire) if wire else None,
            "memories": summaries, "attempt": _clone(attempt),
            "baseline": _baseline(task, model), "requestAllowed": allowed, "connectionVerified": ready,
            "weightUpdate": False, "modelUpdateVerified": False, "message": message}}


def _prepare(case_id):
    task, case, error = _source(case_id)
    if error:
        return None, error
    connection = mubit_bridge.connection_status()
    model = connection.get("model")
    # A valid identifier is needed to preview a deterministic request ID. A
    # missing model does not result in an inference call or saved attempt.
    if not _text(model, 160) or not mubit_bridge._MODEL_ID.fullmatch(model):
        return None, _error("model_required", "Select a Mubit model before previewing the memory review.")
    memories = _select(task["input"], model)
    wire = _wire(task["input"], memories, model) if memories else None
    return (task, case, model, connection, memories, wire), None


def get_case_review(case_id):
    try:
        prepared, error = _prepare(case_id)
        if error:
            return error
        task, case, model, connection, memories, wire = prepared
        with _storage_lock():
            records = _inventory()
            attempt = _existing(records, task["input"], model)
            if attempt:
                memories, wire = attempt["memories"], attempt["input"]
        return _view(task, case, model, connection, memories, wire, attempt)
    except _ERRORS:
        return _error("memory_unavailable", "The memory sources or saved receipt could not be read. Their state is unknown and was preserved.")


def request_review(case_id, request):
    if (not isinstance(request, dict) or set(request) != {"expectedRevision", "expectedInputHash"}
            or not _integer(request["expectedRevision"], 1, learning_store.MAX_REVISIONS)
            or not isinstance(request["expectedInputHash"], str) or not _HASH.fullmatch(request["expectedInputHash"])):
        return _error("invalid_request", "A memory review requires the exact saved revision and displayed memory input.")
    try:
        prepared, error = _prepare(case_id)
        if error:
            return error
        task, case, model, connection, memories, wire = prepared
        if request["expectedRevision"] != case["revision"]:
            return _error("revision_conflict", "The saved comparison review changed. Refresh it before requesting memory.")
        if case.get("approval", {}).get("status") != "approved":
            return _error("approval_required", "Approve the current comparison review before requesting memory.")
        # Commit in the fixed order learning -> targets -> memory. Use the
        # unlocked readers while their stores are locked; calling public getters
        # here would reacquire a separate flock descriptor and can deadlock.
        # All approvals remain stable through the durable pending write, then
        # every storage lock is released before the single provider call.
        with learning_store._storage_lock(), review_target_store._storage_lock(), _storage_lock():
            records = _inventory()
            existing = _existing(records, task["input"], model)
            if existing:
                return {**_view(task, case, model, connection, existing["memories"], existing["input"], existing), "alreadyRequested": True}
            current_record, current_error = learning_store._load(case_id)
            if current_error:
                return _error("revision_conflict", "The current comparison review could not be confirmed. Refresh it; nothing was sent.")
            current_case = learning_store._view(current_record)
            if (current_case["revision"] != case["revision"] or current_case["sourceHash"] != case["sourceHash"]
                    or current_case["source"] != case["source"] or current_case["approval"] != case["approval"]):
                return _error("revision_conflict", "The current comparison review or its approval changed. Refresh it; nothing was sent.")
            if current_case["approval"].get("status") != "approved":
                return _error("approval_required", "Approve the current comparison review before requesting memory. Nothing was sent.")
            current_memories = _select_records(task["input"], model, review_target_store._inventory())
            if current_memories != memories:
                return _error("context_changed", "The approved memory set changed. Refresh and inspect the new input; nothing was sent.")
            if not memories:
                return _error("no_eligible_memory", "No other approved input is available as memory. This exact input is excluded.")
            if request["expectedInputHash"] != _hash(wire):
                return _error("context_changed", "The approved memory set changed. Refresh and inspect the new input before requesting a review.")
            if connection.get("ready") is not True or connection.get("verified") is not True:
                return _error("connection_required", "Check the Mubit connection and remaining call allowance first. Nothing was sent.")
            if len(records) >= MAX_RECORDS:
                return _error("storage_full", "The saved memory-review limit was reached. Existing receipts were preserved.")
            record = {"schemaVersion": 1, "id": wire["runId"], "taskVersion": TASK_VERSION, "model": model,
                "caseId": case["id"], "caseRevision": case["revision"], "sourceHash": case["sourceHash"],
                "input": wire, "memories": _clone(memories), "status": "pending", "startedAt": _now(),
                "completedAt": None, "requestId": None, "latencyMs": 0, "usage": None, "callsRemaining": None,
                "result": None, "assessmentMatchesMeasurements": None,
                "expectedAssessment": review_task_store._assessment(task["input"]),
                "message": "Memory review started. If interrupted, the outcome is unknown and will not be retried.",
                "weightUpdate": False, "modelUpdateVerified": False}
            _write(record, new=True)
            pending_hash = record["recordHash"]
        try:
            response = mubit_bridge.memory_review(_clone(wire), expected_model=model)
        except Exception:
            response = {"ok": False, "message": "The memory request outcome is unknown and was not retried."}
        record = _clone(record)
        record.update(status="failed", completedAt=_now(), message="The memory review did not return a usable answer. It will not be retried.")
        if isinstance(response, dict):
            if _text(response.get("message"), 1500):
                record["message"] = response["message"]
            if mubit_bridge._safe_identifier(response.get("requestId"), mubit_bridge._REQUEST_ID, ""):
                record["requestId"] = response["requestId"]
            if _integer(response.get("latencyMs"), 0, 3600000):
                record["latencyMs"] = response["latencyMs"]
            if _integer(response.get("callsRemaining"), 0, mubit_bridge.MAX_INFERENCE_ATTEMPTS):
                record["callsRemaining"] = response["callsRemaining"]
            try:
                record["usage"] = review_task_store._usage(response.get("usage"))
            except ValueError:
                pass
            if response.get("ok") is True and response.get("model") == model and record["requestId"] is not None:
                try:
                    result = mubit_bridge._validate_tested_review_result({k: response.get(k) for k in ("assessment", "decision", "rationale", "caveat")})
                    record.update(status="succeeded", result=result, assessmentMatchesMeasurements=result["assessment"] == record["expectedAssessment"],
                                  message="Mubit reviewed this input with approved local references. This is prompt-memory use, not evidence of a model-weight update.")
                except mubit_bridge._InvalidResponse:
                    pass
        with _storage_lock():
            saved = _inventory().get(record["id"])
            if saved is None or saved["status"] != "pending" or saved["recordHash"] != pending_hash:
                return _error("receipt_conflict", "The pending memory receipt changed. Inspect its saved state before continuing.")
            _write(record)
        return _view(task, case, model, connection, memories, wire, record)
    except _ERRORS:
        return _error("memory_unavailable", "The memory request could not be confirmed. Reload its receipt; no automatic retry is allowed.")
