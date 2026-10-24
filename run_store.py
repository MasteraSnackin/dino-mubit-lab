"""Immutable local Dino run records. No network or credential access."""
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import threading

from mubit_bridge import _valid_png

RUNS_DIR = Path(__file__).resolve().parent / "runs"
MAX_RUNS = 100
MAX_BYTES = 12 * 1024 * 1024
MAX_IMAGE_BYTES = 250 * 1024
MAX_DURATION_MS = 60 * 60 * 1000
PROVENANCE = {"source": "local-canvas-observation", "mubitUsed": False, "modelTraining": False}
_ID = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
_SECRET = re.compile(r"mbt_|sk-[A-Za-z0-9_-]{8,}|bearer\s+|api[ _-]?key\s*[=:]", re.I)
_FIELDS = {"schemaVersion", "id", "controller", "policyVersion", "mode", "startedAt", "endedAt",
           "score", "durationMs", "outcome", "actions", "frames"}
_LOCK = threading.RLock()


class _Invalid(ValueError):
    pass


def _error(code, message):
    return {"ok": False, "code": code, "message": message}


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _safe_text(value, maximum, allow_empty=False):
    return (isinstance(value, str) and len(value) <= maximum
            and (allow_empty or bool(value.strip()))
            and not any(ord(c) < 32 for c in value) and not _SECRET.search(value))


def _timestamp(value):
    if not _safe_text(value, 40):
        raise _Invalid()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        raise _Invalid() from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _Invalid()
    return parsed


def _valid_id(value):
    return isinstance(value, str) and bool(_ID.fullmatch(value)) and not _SECRET.search(value)


def _png_image(value):
    if (not isinstance(value, str) or len(value) > MAX_IMAGE_BYTES
            or not value.startswith("data:image/png;base64,")):
        return False
    try:
        raw = base64.b64decode(value[22:], validate=True)
    except (ValueError, UnicodeError):
        return False
    return len(raw) <= MAX_IMAGE_BYTES and _valid_png(raw)


def _validate(payload):
    if not isinstance(payload, dict) or set(payload) != _FIELDS:
        raise _Invalid()
    if (type(payload["schemaVersion"]) is not int or payload["schemaVersion"] != 1
            or not _valid_id(payload["id"]) or payload["controller"] != "local-vision-rules"
            or payload["policyVersion"] not in ("vision-v1", "vision-v2") or payload["mode"] != "realtime"
            or payload["outcome"] not in ("collision", "stopped", "time_limit", "hidden", "error")
            or not _integer(payload["score"], 0, 10_000_000)
            or not _integer(payload["durationMs"], 0, MAX_DURATION_MS)):
        raise _Invalid()
    start, end = _timestamp(payload["startedAt"]), _timestamp(payload["endedAt"])
    elapsed = (end - start).total_seconds() * 1000
    if elapsed < 0 or abs(elapsed - payload["durationMs"]) > 3000:
        raise _Invalid()
    actions, frames = payload["actions"], payload["frames"]
    if not isinstance(actions, list) or len(actions) > 1200 or not isinstance(frames, list) or len(frames) > 60:
        raise _Invalid()
    for entries, fields in ((actions, {"tMs", "action", "score", "reason"}),
                            (frames, {"tMs", "score", "image"})):
        previous = -1
        for item in entries:
            if not isinstance(item, dict) or set(item) != fields:
                raise _Invalid()
            if (not _integer(item["tMs"], 0, payload["durationMs"]) or item["tMs"] < previous
                    or not _integer(item["score"], 0, payload["score"])):
                raise _Invalid()
            previous = item["tMs"]
            if entries is actions:
                if item["action"] not in ("jump", "duck", "run") or not _safe_text(item["reason"], 200, True):
                    raise _Invalid()
            elif not _png_image(item["image"]):
                raise _Invalid()
    try:
        raw = json.dumps(payload, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode()
    except (ValueError, TypeError, OverflowError):
        raise _Invalid() from None
    if len(raw) > MAX_BYTES:
        raise _Invalid()
    # Snapshot caller-owned data so subsequent mutations cannot change the write.
    return json.loads(raw)


def _summary(run):
    fields = ("id", "startedAt", "endedAt", "score", "durationMs", "outcome", "controller",
              "policyVersion", "mode", "savedAt", "provenance")
    return {**{key: run[key] for key in fields}, "saved": True,
            "frameCount": len(run["frames"]), "actionCount": len(run["actions"])}


def _directory():
    if RUNS_DIR.is_symlink():
        raise OSError("Run directory cannot be a symbolic link")
    RUNS_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not RUNS_DIR.is_dir():
        raise OSError("Run directory unavailable")


@contextmanager
def _storage_lock():
    with _LOCK:
        _directory()
        descriptor = os.open(RUNS_DIR / ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise OSError("Run lock is not a regular file")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def _reject_duplicate_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _Invalid()
        result[key] = value
    return result


def _read(path):
    if path.is_symlink():
        raise _Invalid()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as file:
        if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
            raise _Invalid()
        raw = file.read(MAX_BYTES + 1025)
    if len(raw) > MAX_BYTES + 1024:
        raise _Invalid()
    try:
        data = json.loads(raw, object_pairs_hook=_reject_duplicate_pairs)
    except RecursionError:
        raise _Invalid() from None
    if (not isinstance(data, dict) or set(data) != _FIELDS | {"savedAt", "provenance"}
            or data["provenance"] != PROVENANCE):
        raise _Invalid()
    saved_at = data["savedAt"]
    _timestamp(saved_at)
    payload = _validate({k: data[k] for k in _FIELDS})
    if path.stem != payload["id"]:
        raise _Invalid()
    return {**payload, "savedAt": saved_at, "provenance": dict(PROVENANCE)}


def _existing_result(path, record):
    try:
        existing = _read(path)
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        return _error("unavailable", "An existing run with this identifier is unreadable or invalid. It was not changed.")
    if all(existing[key] == record[key] for key in _FIELDS):
        return {"ok": True, "run": _summary(existing), "alreadySaved": True}
    return _error("conflict", "A different saved run already has this identifier. It was not changed.")


def save_run(payload):
    """Validate and publish one new record atomically; never replace a run."""
    try:
        record = _validate(payload)
    except (ValueError, TypeError, KeyError, OverflowError):
        return _error("invalid_run", "The run record is invalid or exceeds the recording limits.")
    record.update(savedAt=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                  provenance=dict(PROVENANCE))
    raw = json.dumps(record, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode()
    temporary = None
    try:
        with _storage_lock():
            destination = RUNS_DIR / (record["id"] + ".json")
            if destination.exists() or destination.is_symlink():
                return _existing_result(destination, record)
            if sum(1 for path in RUNS_DIR.iterdir() if path.name.endswith(".json")) >= MAX_RUNS:
                return _error("storage_full", "The 100-run local recording limit has been reached. No recordings were deleted.")
            fd, name = tempfile.mkstemp(prefix=".pending-", dir=RUNS_DIR)
            temporary = Path(name)
            with os.fdopen(fd, "wb") as file:
                file.write(raw)
                file.flush()
                os.fsync(file.fileno())
            # A hard-link publish is atomic and refuses an existing destination.
            try:
                os.link(temporary, destination)
            except FileExistsError:
                return _existing_result(destination, record)
            temporary.unlink()
            temporary = None
            directory_fd = os.open(RUNS_DIR, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        return {"ok": True, "run": _summary(record)}
    except OSError:
        return _error("storage_unavailable", "The local run's save could not be confirmed. Check saved runs before retrying. Existing recordings were left unchanged.")
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def list_runs():
    """Return metadata and explicit unavailable entries for unreadable records."""
    runs, unavailable = [], []
    try:
        with _storage_lock():
            for path in RUNS_DIR.iterdir():
                if not path.name.endswith(".json"):
                    continue
                safe_id = path.stem if _valid_id(path.stem) else None
                try:
                    runs.append(_summary(_read(path)))
                except (OSError, ValueError, TypeError, KeyError, OverflowError):
                    unavailable.append({"id": safe_id, "code": "unavailable", "message": "A saved run is unreadable or invalid; it has not been deleted."})
    except OSError:
        return _error("storage_unavailable", "Saved runs could not be read. Their contents are unknown.")
    runs.sort(key=lambda run: (_timestamp(run["startedAt"]), run["id"]), reverse=True)
    return {"ok": True, "runs": runs, "unavailable": unavailable, "limit": MAX_RUNS}


def get_run(run_id):
    if not _valid_id(run_id):
        return _error("invalid_id", "The run identifier is invalid.")
    try:
        with _storage_lock():
            run = _read(RUNS_DIR / (run_id + ".json"))
        return {"ok": True, "run": run}
    except FileNotFoundError:
        return _error("not_found", "That run is not saved locally.")
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        return _error("unavailable", "This saved run is unreadable or invalid; it has not been deleted.")


def export_run(run_id):
    return get_run(run_id)
