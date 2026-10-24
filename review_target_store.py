"""Local review targets bound to a real compact-task reply; no training calls.

An unchanged answer is a confirmed reference, not a manufactured correction.
The earlier proposal-review approval is never reused for this different task.
"""
from contextlib import contextmanager
from pathlib import Path
import fcntl
import json
import os
import re
import stat
import tempfile
import threading

import learning_store
import mubit_bridge
import review_task_store

REVIEW_TARGETS_DIR = Path(__file__).resolve().parent / "review_targets"
MAX_RECORDS = 100
MAX_REVISIONS = 100
MAX_BYTES = 512 * 1024
_LOCK = threading.RLock()
_ID = re.compile(r"target_[a-f0-9]{32}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_ERRORS = (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError,
           mubit_bridge._InvalidInput, mubit_bridge._InvalidResponse)


class _Invalid(ValueError):
    pass


def _error(code, message):
    return {"ok": False, "code": code, "message": message}


def _encode(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(raw) > MAX_BYTES:
        raise _Invalid()
    return raw


def _clone(value):
    return json.loads(_encode(value))


def _hash(value):
    import hashlib
    return hashlib.sha256(_encode(value)).hexdigest()


def _integer(value, minimum=0, maximum=MAX_REVISIONS):
    return type(value) is int and minimum <= value <= maximum


def _text(value, maximum):
    return mubit_bridge._proposal_text(value, maximum)


def _unapproved():
    return {"status": "unapproved", "identityVerified": False}


@contextmanager
def _storage_lock():
    with _LOCK:
        REVIEW_TARGETS_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        if REVIEW_TARGETS_DIR.is_symlink() or not stat.S_ISDIR(REVIEW_TARGETS_DIR.lstat().st_mode):
            raise OSError()
        os.chmod(REVIEW_TARGETS_DIR, 0o700)
        descriptor = os.open(REVIEW_TARGETS_DIR / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise OSError()
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)


def _read(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
            raise _Invalid()
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise _Invalid()
        return json.loads(raw, object_pairs_hook=review_task_store._unique)
    finally:
        os.close(descriptor)


def _write(record, *, new):
    record["recordHash"] = _hash({k: v for k, v in record.items() if k != "recordHash"})
    _validate_record(record)
    path = REVIEW_TARGETS_DIR / (record["id"] + ".json")
    descriptor, temporary = tempfile.mkstemp(prefix=".pending-", dir=REVIEW_TARGETS_DIR)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(_encode(record))
            stream.flush()
            os.fsync(stream.fileno())
        if new:
            os.link(temporary, path)
        else:
            if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
                raise OSError()
            os.replace(temporary, path)
        directory = os.open(REVIEW_TARGETS_DIR, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _identity(source):
    return "target_" + _hash({key: source[key] for key in ("taskVersion", "inputHash", "model", "requestId")})[:32]


def _validate_source(source):
    fields = {"taskVersion", "inputHash", "input", "messages", "model", "requestId", "originalAnswer",
              "attemptId", "sourceCaseId", "sourceCaseRevision", "sourceRecordHash", "expectedAssessment"}
    if (not isinstance(source, dict) or set(source) != fields
            or source["taskVersion"] != review_task_store.TASK_VERSION
            or not learning_store._id(source["sourceCaseId"])
            or not _integer(source["sourceCaseRevision"], 1, learning_store.MAX_REVISIONS)
            or not isinstance(source["sourceRecordHash"], str) or not _HASH.fullmatch(source["sourceRecordHash"])
            or not _text(source["model"], 160) or not mubit_bridge._MODEL_ID.fullmatch(source["model"])
            or not mubit_bridge._safe_identifier(source["requestId"], mubit_bridge._REQUEST_ID, "")):
        raise _Invalid()
    review_task_store._validate_input(source["input"])
    mubit_bridge._validate_tested_review_result(source["originalAnswer"])
    if (source["inputHash"] != review_task_store._input_hash(source["input"])
            or source["attemptId"] != source["input"]["runId"]
            or source["attemptId"] != "review_" + source["inputHash"][:32]
            or source["expectedAssessment"] != review_task_store._assessment(source["input"])):
        raise _Invalid()
    messages = mubit_bridge._validate_tested_review(source["input"])
    expected = [messages[0], {"role": "user", "content": json.dumps({"runId": source["attemptId"]})}, *messages[1:]]
    if source["messages"] != expected:
        raise _Invalid()


def _source(case_id, *, read_connection=True):
    if not learning_store._id(case_id):
        return None, _error("invalid_id", "Choose a saved compact review.")
    found = (review_task_store.get_case_review(case_id) if read_connection else
             review_task_store.get_case_review(case_id, read_connection=False))
    if not found.get("ok"):
        return None, _error("source_unavailable", "The compact review could not be read. Existing review targets were preserved.")
    task = found["task"]
    if task.get("caseId") != case_id:
        raise _Invalid()
    attempt = task.get("attempt")
    if not attempt or attempt.get("status") != "succeeded":
        return None, _error("reply_required", "A saved successful compact review is required before reviewing its answer.")
    # Validate the authoritative receipt, rather than trusting a client answer.
    review_task_store._validate_record(attempt)
    if (task["taskVersion"] != attempt["taskVersion"] or task["input"] != attempt["input"]
            or task["model"] != attempt["model"]):
        raise _Invalid()
    messages = mubit_bridge._validate_tested_review(attempt["input"])
    source = {"taskVersion": attempt["taskVersion"], "inputHash": attempt["inputHash"], "input": _clone(attempt["input"]),
              "messages": [messages[0], {"role": "user", "content": json.dumps({"runId": attempt["id"]})}, *messages[1:]],
              "model": attempt["model"], "requestId": attempt["requestId"], "originalAnswer": _clone(attempt["result"]),
              "attemptId": attempt["id"], "sourceCaseId": attempt["sourceCaseId"], "sourceCaseRevision": attempt["sourceCaseRevision"],
              "sourceRecordHash": attempt["recordHash"], "expectedAssessment": attempt["expectedAssessment"]}
    _validate_source(source)
    return source, None


def _history_state(source, history):
    if not isinstance(history, list) or not 1 <= len(history) <= MAX_REVISIONS:
        raise _Invalid()
    draft = None
    approval = _unapproved()
    last_time = None
    for revision, entry in enumerate(history, 1):
        if not isinstance(entry, dict) or entry.get("revision") != revision or type(entry["revision"]) is not int:
            raise _Invalid()
        created = review_task_store._timestamp(entry.get("createdAt"))
        if last_time and created < last_time:
            raise _Invalid()
        last_time = created
        if entry.get("type") == "draft":
            if (set(entry) != {"type", "revision", "answer", "reason", "changedFromModel", "createdAt"}
                    or not _text(entry["reason"], 1000) or type(entry["changedFromModel"]) is not bool):
                raise _Invalid()
            mubit_bridge._validate_tested_review_result(entry["answer"])
            if entry["changedFromModel"] != (entry["answer"] != source["originalAnswer"]):
                raise _Invalid()
            draft = {key: _clone(value) for key, value in entry.items() if key != "type"}
            approval = _unapproved()
        elif entry.get("type") == "approval":
            if (set(entry) != {"type", "revision", "draftRevision", "createdAt", "identityVerified"}
                    or draft is None or approval["status"] == "approved"
                    or type(entry["draftRevision"]) is not int or entry["draftRevision"] != draft["revision"]
                    or entry["identityVerified"] is not False):
                raise _Invalid()
            approval = {"status": "approved", "draftRevision": draft["revision"], "approvedAt": entry["createdAt"], "identityVerified": False}
        else:
            raise _Invalid()
    return draft, approval


def _validate_record(record):
    if (not isinstance(record, dict) or set(record) != {"schemaVersion", "id", "source", "sourceHash", "history", "recordHash"}
            or type(record["schemaVersion"]) is not int or record["schemaVersion"] != 1
            or record["recordHash"] != _hash({k: v for k, v in record.items() if k != "recordHash"})):
        raise _Invalid()
    _validate_source(record["source"])
    if record["id"] != _identity(record["source"]) or record["sourceHash"] != _hash(record["source"]):
        raise _Invalid()
    _history_state(record["source"], record["history"])
    return record


def _inventory():
    records = {}
    for path in REVIEW_TARGETS_DIR.iterdir():
        if path.name == ".lock" or path.name.startswith(".pending-"):
            continue
        if path.suffix != ".json" or not _ID.fullmatch(path.stem) or len(records) >= MAX_RECORDS:
            raise _Invalid()
        record = _validate_record(_read(path))
        if path.stem != record["id"]:
            raise _Invalid()
        records[record["id"]] = record
    return records


def _find(records, source):
    record = records.get(_identity(source))
    if record is not None and record["source"] != source:
        raise _Invalid()
    return record


def _view(case_id, source, record):
    draft, approval = _history_state(source, record["history"]) if record else (None, _unapproved())
    return {"ok": True, "target": {"id": _identity(source), "caseId": case_id, "taskVersion": source["taskVersion"],
            "revision": len(record["history"]) if record else 0, "source": _clone(source), "latestDraft": draft,
            "approval": approval, "history": _clone(record["history"]) if record else [],
            "providerCompatible": False, "submitted": False, "modelUpdateVerified": False,
            "message": ("This exact saved answer is approved for local export. No training submission was made." if approval["status"] == "approved" else
                        "Review the saved draft and approve it separately before export." if draft else
                        "No review target has been saved. An unchanged answer may be confirmed as a reference; it is not a correction.")}}


def get_target(case_id, *, read_connection=True):
    try:
        source, error = _source(case_id, read_connection=read_connection)
        if error:
            return error
        with _storage_lock():
            return _view(case_id, source, _find(_inventory(), source))
    except _ERRORS:
        return _error("target_unavailable", "The source or saved review target could not be read. Its state is unknown and was preserved.")


def save_target(case_id, request):
    if (not isinstance(request, dict) or set(request) != {"expectedRevision", "answer", "reason"}
            or not _integer(request["expectedRevision"]) or not _text(request["reason"], 1000)):
        return _error("invalid_request", "Save an exact review answer, a reason and the current revision.")
    try:
        answer = mubit_bridge._validate_tested_review_result(request["answer"])
        source, error = _source(case_id)
        if error:
            return error
        with _storage_lock():
            records = _inventory()
            record = _find(records, source)
            revision = len(record["history"]) if record else 0
            if request["expectedRevision"] != revision:
                return _error("revision_conflict", "This review target changed. Refresh its saved revision before saving.")
            if revision >= MAX_REVISIONS or (record is None and len(records) >= MAX_RECORDS):
                return _error("storage_full", "The local review target limit was reached. Existing evidence was preserved.")
            new = record is None
            if new:
                record = {"schemaVersion": 1, "id": _identity(source), "source": source, "sourceHash": _hash(source), "history": []}
            record["history"].append({"type": "draft", "revision": revision + 1, "answer": _clone(answer),
                                      "reason": request["reason"], "changedFromModel": answer != source["originalAnswer"],
                                      "createdAt": review_task_store._now()})
            _write(record, new=new)
            return _view(case_id, source, record)
    except _ERRORS:
        return _error("target_unavailable", "The review target could not be saved. Refresh its record before continuing; existing evidence was preserved.")


def approve_target(case_id, request):
    if (not isinstance(request, dict) or set(request) != {"expectedRevision", "confirm"}
            or not _integer(request["expectedRevision"], 1) or request["confirm"] is not True):
        return _error("invalid_request", "Approval requires the exact saved revision and a separate confirmation.")
    try:
        source, error = _source(case_id)
        if error:
            return error
        with _storage_lock():
            record = _find(_inventory(), source)
            if record is None or len(record["history"]) != request["expectedRevision"]:
                return _error("revision_conflict", "This review target changed or has no saved draft. Refresh it before approval.")
            draft, approval = _history_state(source, record["history"])
            if approval["status"] == "approved":
                return {**_view(case_id, source, record), "alreadyApproved": True}
            if len(record["history"]) >= MAX_REVISIONS:
                return _error("storage_full", "The local review revision limit was reached. Existing evidence was preserved.")
            record["history"].append({"type": "approval", "revision": len(record["history"]) + 1,
                                      "draftRevision": draft["revision"], "createdAt": review_task_store._now(), "identityVerified": False})
            _write(record, new=False)
            return _view(case_id, source, record)
    except _ERRORS:
        return _error("target_unavailable", "The review approval could not be confirmed. Refresh its saved record before continuing.")


def export_target(case_id, *, read_connection=True):
    result = get_target(case_id) if read_connection else get_target(case_id, read_connection=False)
    if not result.get("ok"):
        return result
    target = result["target"]
    if target["approval"]["status"] != "approved":
        return _error("approval_required", "Approve the exact saved compact-task answer before exporting it.")
    source, draft = target["source"], target["latestDraft"]
    return {"ok": True, "export": {"format": "dino-compact-review-target-v1", "taskVersion": source["taskVersion"],
            "targetId": target["id"], "targetRevision": target["revision"], "sourceHash": _hash(source),
            "source": source,
            "inputHash": source["inputHash"], "input": source["input"], "messages": source["messages"],
            "model": source["model"], "requestId": source["requestId"], "sourceAttemptId": source["attemptId"],
            "originalAnswer": source["originalAnswer"], "correctedAnswer": draft["answer"], "reason": draft["reason"],
            "changedFromModel": draft["changedFromModel"],
            "reviewType": "corrected_answer" if draft["changedFromModel"] else "confirmed_reference",
            "approval": target["approval"], "providerCompatible": False, "submitted": False, "modelUpdateVerified": False,
            "message": "Generic local export only. No supported Mubit upload format or training acceptance is verified."}}
