"""One explicit, private, six-request Mubit versus OpenAI inference pilot.

Reading status never invokes a model. The OpenAI key exists only in the calling
request and local variables; neither keys nor raw provider replies are saved.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener
import uuid

import learning_store
import mubit_bridge as bridge
import review_task_store


COMPARISONS_DIR = Path(__file__).resolve().parent / "provider_comparisons"
MODEL = "gpt-6-luna"
DIRECT_URL = "https://api.openai.com/v1/chat/completions"
DIRECT_MODEL_URL = "https://api.openai.com/v1/models/gpt-6-luna"
MUBIT_URL = "https://api.mubit.ai/openai/v1/chat/completions"
MAX_RESPONSE_BYTES = 262144
MAX_RECORD_BYTES = 131072
PAIR_ORDERS = (("mubit", "direct"), ("direct", "mubit"), ("mubit", "direct"))
_LOCK = threading.RLock()
_ID = re.compile(r"comparison_[a-f0-9]{32}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}\Z")
CAVEAT = ("Three matched pairs repeat one frozen review example. This is not an "
          "independent quality test or evidence of general token or speed savings. "
          "Token counts are provider-reported; monetary cost is not measured. "
          "Matching model names do not prove identical backend weights.")


class _Invalid(ValueError):
    pass


class _SourceNotReady(ValueError):
    pass


SOURCE_GUIDANCE = ("Create a two-course comparison, import and approve its correction, "
                   "then complete a compact text review with gpt-6-luna whose measured "
                   "outcome is unchanged. The newest compatible completed review will "
                   "be selected automatically.")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _encode(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _hash(value):
    return hashlib.sha256(_encode(value)).hexdigest()


def _clone(value):
    return json.loads(_encode(value))


def _valid_direct_key(value):
    return (isinstance(value, str) and 20 <= len(value) <= 4096
            and value.startswith("sk-") and value.isascii()
            and all(33 <= ord(char) <= 126 for char in value))


@contextmanager
def _storage_lock(*, create=False):
    with _LOCK:
        if not COMPARISONS_DIR.exists() and not COMPARISONS_DIR.is_symlink():
            if not create:
                yield
                return
            COMPARISONS_DIR.mkdir(mode=0o700)
        if COMPARISONS_DIR.is_symlink() or not stat.S_ISDIR(COMPARISONS_DIR.lstat().st_mode):
            raise _Invalid()
        if create:
            os.chmod(COMPARISONS_DIR, 0o700)
        lock_path = COMPARISONS_DIR / ".lock"
        flags = os.O_RDWR | os.O_NOFOLLOW | (os.O_CREAT if create else 0)
        fd = os.open(lock_path, flags, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise _Invalid()
            if create:
                os.fchmod(fd, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)


def _read(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_RECORD_BYTES:
            raise _Invalid()
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(MAX_RECORD_BYTES + 1)
        if len(raw) > MAX_RECORD_BYTES:
            raise _Invalid()
        return json.loads(raw, object_pairs_hook=review_task_store._unique)
    finally:
        os.close(fd)


def _write(path, value, *, new=False):
    raw = _encode(value)
    if len(raw) > MAX_RECORD_BYTES:
        raise _Invalid()
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if new:
            os.link(temporary, path)
        else:
            if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
                raise _Invalid()
            os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _save_receipt(path, receipt, *, new=False):
    receipt["receiptHash"] = _hash({k: v for k, v in receipt.items() if k != "receiptHash"})
    _write(path / "receipt.json", receipt, new=new)


def _latest():
    if not COMPARISONS_DIR.exists():
        return None, None
    directories = []
    for path in COMPARISONS_DIR.iterdir():
        if path.name == ".lock":
            continue
        if not _ID.fullmatch(path.name) or path.is_symlink() or not stat.S_ISDIR(path.lstat().st_mode):
            raise _Invalid()
        directories.append(path)
    if not directories:
        return None, None
    # This pilot intentionally has one attempt. Unknown extra attempts are not
    # silently hidden by choosing whichever timestamp happens to be newest.
    if len(directories) != 1:
        raise _Invalid()
    path = directories[0]
    manifest, receipt = _read(path / "manifest.json"), _read(path / "receipt.json")
    if (manifest.get("id") != path.name or receipt.get("id") != path.name
            or manifest.get("manifestHash") != _hash({k: v for k, v in manifest.items() if k != "manifestHash"})
            or receipt.get("receiptHash") != _hash({k: v for k, v in receipt.items() if k != "receiptHash"})
            or receipt.get("manifestHash") != manifest["manifestHash"]
            or manifest.get("bodyHash") != _hash(manifest.get("body"))
            or manifest.get("inputHash") != review_task_store._input_hash(manifest["input"])
            or receipt.get("status") not in ("pending", "succeeded", "failed", "unknown")
            or len(receipt.get("pairs", [])) != 3):
        raise _Invalid()
    for index, (pair, order) in enumerate(zip(receipt["pairs"], PAIR_ORDERS), 1):
        if pair.get("index") != index or pair.get("order") != list(order):
            raise _Invalid()
        for arm in ("mubit", "direct"):
            result = pair.get(arm)
            if result is not None and (not isinstance(result, dict) or result.get("status") not in
                                      ("pending", "succeeded", "failed", "uncertain")):
                raise _Invalid()
    return manifest, receipt


def _selected_task():
    """Select existing local evidence without credentials or inference."""
    requested = os.environ.get("DINO_COMPARISON_CASE_ID", "").strip()
    if requested:
        if not learning_store._id(requested):
            raise _SourceNotReady("DINO_COMPARISON_CASE_ID must identify a saved correction case.")
        identifiers = [requested]
    else:
        # Avoid creating a learning store merely to report an empty fresh clone.
        if not learning_store.LEARNING_DIR.exists() and not learning_store.LEARNING_DIR.is_symlink():
            raise _SourceNotReady(SOURCE_GUIDANCE)
        inventory = learning_store.list_cases()
        if not inventory.get("ok") or inventory.get("unavailable"):
            raise _Invalid()
        identifiers = [case["id"] for case in inventory["cases"]]
    candidates = []
    for identifier in identifiers:
        source = review_task_store.get_case_review(identifier, read_connection=False)
        if not source.get("ok"):
            if requested and source.get("code") in ("invalid_id", "source_unavailable"):
                raise _SourceNotReady("The case selected by DINO_COMPARISON_CASE_ID is unavailable. " + SOURCE_GUIDANCE)
            raise _Invalid()
        task = source["task"]
        attempt = task.get("attempt")
        if (not isinstance(attempt, dict) or attempt.get("status") != "succeeded"
                or task.get("model") != MODEL
                or review_task_store._assessment(task["input"]) != "unchanged"):
            continue
        candidates.append((attempt["completedAt"], identifier, task))
    if not candidates:
        prefix = "The selected case is not eligible. " if requested else ""
        raise _SourceNotReady(prefix + SOURCE_GUIDANCE)
    _, identifier, task = max(candidates, key=lambda item: (item[0], item[1]))
    return identifier, task


def _preview():
    identifier, task = _selected_task()
    current = task["input"]
    messages = bridge._validate_tested_review(current)
    grouped = [messages[0], {"role": "user", "content": json.dumps({"runId": current["runId"]})}, *messages[1:]]
    body = {"model": MODEL, "messages": grouped, "response_format": {"type": "json_object"},
            "max_completion_tokens": 2000, "store": False}
    return {"caseId": identifier, "caseRevision": task["caseRevision"],
            "inputHash": review_task_store._input_hash(current), "bodyHash": _hash(body),
            "requestedModel": MODEL, "pairCount": 3, "requestCount": 6, "mubitRequestCount": 3,
            "body": body, "input": _clone(current)}


def _unknown(message="Saved comparison evidence is unavailable or changed. It has been preserved."):
    return {"ok": False, "comparison": {"status": "unknown", "message": message, "ready": False,
            "preview": None, "latest": None, "exportAvailable": False}}


def status():
    """Read source and saved results; never perform an inference or direct-key read."""
    try:
        with _storage_lock():
            manifest, receipt = _latest()
        preview = ({key: manifest[key] for key in ("caseId", "caseRevision", "inputHash", "bodyHash",
                   "requestedModel", "pairCount", "requestCount", "mubitRequestCount", "body", "input")}
                   if manifest is not None else _preview())
        connection = bridge.connection_status()
        ready = (receipt is None and connection.get("ready") is True
                 and connection.get("verified") is True and connection.get("model") == MODEL
                 and type(connection.get("callsRemaining")) is int and connection["callsRemaining"] >= 3)
        if receipt:
            message = receipt["message"]
        elif ready:
            message = "One six-request pilot is ready. Enter the direct OpenAI key only when starting it."
        else:
            message = "Verify the Mubit connection to gpt-6-luna with at least three calls remaining."
        return {"ok": True, "comparison": {"status": receipt["status"] if receipt else "not_started",
            "message": message, "ready": ready, "preview": preview, "latest": receipt,
            "mubitCallsRemaining": connection.get("callsRemaining"),
            "exportAvailable": receipt is not None and receipt["status"] != "pending"}}
    except _SourceNotReady as error:
        return {"ok": True, "comparison": {"status": "not_started", "message": str(error),
                "ready": False, "preview": None, "latest": None, "exportAvailable": False}}
    except Exception:
        return _unknown()


def export():
    result = status()
    if not result["ok"] or not result["comparison"]["exportAvailable"]:
        return {**result, "ok": False}
    try:
        with _storage_lock():
            manifest, receipt = _latest()
        result["comparison"]["export"] = {"format": "dino-provider-comparison-evidence-v1",
            "manifest": manifest, "receipt": receipt, "containsCredentials": False}
        return result
    except Exception:
        return _unknown()


def _safe_identifier(value, secrets):
    if (not isinstance(value, str) or not _SAFE_ID.fullmatch(value)
            or re.search(r"sk-|mbt_", value, re.I) or any(secret and secret in value for secret in secrets)):
        return None
    return value


def _count(value):
    return value if type(value) is int and 0 <= value <= 1000000000 else None


def _usage(payload):
    raw = payload.get("usage")
    raw = raw if isinstance(raw, dict) else {}
    result = {"promptTokens": _count(raw.get("prompt_tokens")),
              "completionTokens": _count(raw.get("completion_tokens")),
              "totalTokens": _count(raw.get("total_tokens")),
              "cachedPromptTokens": None, "reasoningTokens": None}
    if isinstance(raw.get("prompt_tokens_details"), dict):
        result["cachedPromptTokens"] = _count(raw["prompt_tokens_details"].get("cached_tokens"))
    if isinstance(raw.get("completion_tokens_details"), dict):
        result["reasoningTokens"] = _count(raw["completion_tokens_details"].get("reasoning_tokens"))
    if (all(result[name] is not None for name in ("promptTokens", "completionTokens", "totalTokens"))
            and result["totalTokens"] != result["promptTokens"] + result["completionTokens"]):
        result["totalTokens"] = None
    if (result["cachedPromptTokens"] is not None and result["promptTokens"] is not None
            and result["cachedPromptTokens"] > result["promptTokens"]):
        result["cachedPromptTokens"] = None
    if (result["reasoningTokens"] is not None and result["completionTokens"] is not None
            and result["reasoningTokens"] > result["completionTokens"]):
        result["reasoningTokens"] = None
    return result


def _score(answer, current):
    rationale = answer["rationale"].replace(",", "")
    measurements = {course[arm][metric] for course in current["courses"]
                    for arm in ("baseline", "candidate") for metric in ("score", "ticks")}
    return {"jsonValid": True,
            "assessmentMatchesMeasured": answer["assessment"] == review_task_store._assessment(current),
            # Only unchanged-outcome cases are eligible for this bounded pilot.
            "decisionMatchesExpected": answer["decision"] == "retain_baseline",
            "mentionsAllMeasurements": all(re.search(r"(?<![\d.])" + str(value) + r"(?!\d|\.\d)", rationale)
                                           for value in measurements)}


def _pending_arm():
    return {"status": "pending", "startedAt": _now(), "completedAt": None, "latencyMs": None,
            "requestedModel": MODEL, "returnedModel": None, "returnedModelMatchesRequested": None,
            "responseId": None, "requestId": None, "systemFingerprint": None,
            "usage": _usage({}), "answer": None,
            "score": {"jsonValid": False, "assessmentMatchesMeasured": None,
                      "decisionMatchesExpected": None, "mentionsAllMeasurements": None}, "failure": None}


def _failed(result, code, message, *, status="failed", http_status=None):
    result.update(status=status, completedAt=_now(), failure={"code": code, "message": message,
                                                            "httpStatus": http_status})
    return result


def _open_request(request):
    return build_opener(bridge._NoRedirect()).open(request, timeout=30)


def _direct_preflight(key):
    """Read model availability only; never infer, persist a key, or echo errors."""
    result = {"available": False, "httpStatus": None, "model": MODEL, "checkedAt": _now()}
    request = Request(DIRECT_MODEL_URL, method="GET", headers={"Authorization": "Bearer " + key,
                                                             "Accept": "application/json"})
    try:
        with _open_request(request) as response:
            result["httpStatus"] = response.getcode()
            if response.getcode() != 200 or response.geturl() != DIRECT_MODEL_URL:
                return result
            length = response.headers.get("Content-Length")
            if length is not None and (not length.isascii() or not length.isdecimal() or int(length) > MAX_RESPONSE_BYTES):
                return result
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if not isinstance(raw, bytes) or len(raw) > MAX_RESPONSE_BYTES:
                return result
            payload = json.loads(raw, object_pairs_hook=review_task_store._unique)
            result["available"] = isinstance(payload, dict) and payload.get("id") == MODEL
    except HTTPError as error:
        result["httpStatus"] = error.code
        error.close()
    except Exception:
        pass
    return result


def _perform(arm, body, key, other_key, pending, current):
    """One request only. Save allowlisted metadata and a validated answer, not raw text."""
    result = _clone(pending)
    url = MUBIT_URL if arm == "mubit" else DIRECT_URL
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json", "Accept": "application/json"}
    if arm == "mubit":
        headers["x-middleware-record"] = "off"
    request = Request(url, data=body, headers=headers, method="POST")
    start = time.monotonic()
    try:
        with _open_request(request) as response:
            if response.getcode() != 200 or response.geturl() != url:
                raise _Invalid()
            length = response.headers.get("Content-Length")
            if length is not None and (not length.isascii() or not length.isdecimal() or int(length) > MAX_RESPONSE_BYTES):
                raise _Invalid()
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if not isinstance(raw, bytes) or len(raw) > MAX_RESPONSE_BYTES:
                raise _Invalid()
            payload = json.loads(raw, object_pairs_hook=review_task_store._unique)
            if not isinstance(payload, dict):
                raise _Invalid()
            secrets = (key, other_key)
            result["requestId"] = _safe_identifier(response.headers.get(
                "x-middleware-request-id" if arm == "mubit" else "x-request-id"), secrets)
            if arm == "mubit" and result["requestId"] is None:
                raise _Invalid()
            result.update(returnedModel=_safe_identifier(payload.get("model"), secrets),
                          responseId=_safe_identifier(payload.get("id"), secrets),
                          systemFingerprint=_safe_identifier(payload.get("system_fingerprint"), secrets),
                          usage=_usage(payload))
            result["returnedModelMatchesRequested"] = (result["returnedModel"] == MODEL
                if result["returnedModel"] is not None else None)
            choices = payload.get("choices")
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                raise _Invalid()
            choice = choices[0]
            if choice.get("finish_reason") != "stop":
                raise _Invalid()
            message = choice.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if (not isinstance(content, str) or not content.strip() or len(content) > 10000
                    or bridge._EXPERIMENT_SECRET.search(content)
                    or any(secret and secret in content for secret in secrets)):
                raise _Invalid()
            answer = json.loads(content, object_pairs_hook=review_task_store._unique)
            bridge._validate_tested_review_result(answer)
            result.update(status="succeeded", completedAt=_now(), answer=answer, score=_score(answer, current))
    except HTTPError as error:
        code = error.code
        error.close()
        _failed(result, "redirect_refused" if 300 <= code < 400 else "http_error",
                "The endpoint did not accept the request. No retry was made.", http_status=code)
    except (URLError, TimeoutError, OSError):
        _failed(result, "outcome_unknown", "The request did not return a confirmed result. It was not retried.", status="uncertain")
    except Exception:
        _failed(result, "invalid_response", "The endpoint returned an unusable response. Raw reply text was not saved.")
    result["latencyMs"] = round((time.monotonic() - start) * 1000)
    return result


def _mean(values):
    return round(sum(values) / len(values), 3) if values and all(type(v) in (int, float) and math.isfinite(v) for v in values) else None


def _summary(pairs):
    valid = [pair for pair in pairs if all(pair.get(arm) and pair[arm]["status"] == "succeeded" for arm in ("mubit", "direct"))]
    result = {"pairedSamples": len(valid), "allPairsValid": len(valid) == 3,
              "modelConsistencyVerified": False, "mubit": None, "direct": None,
              "difference": None, "percentDifference": None, "caveat": CAVEAT}
    if len(valid) != 3:
        return result
    for arm in ("mubit", "direct"):
        rows = [pair[arm] for pair in valid]
        result[arm] = {"meanPromptTokens": _mean([row["usage"]["promptTokens"] for row in rows]),
            "meanCompletionTokens": _mean([row["usage"]["completionTokens"] for row in rows]),
            "meanTotalTokens": _mean([row["usage"]["totalTokens"] for row in rows]),
            "meanLatencyMs": _mean([row["latencyMs"] for row in rows]),
            "jsonValidCount": sum(row["score"]["jsonValid"] is True for row in rows),
            "assessmentCorrectCount": sum(row["score"]["assessmentMatchesMeasured"] is True for row in rows),
            "decisionCorrectCount": sum(row["score"]["decisionMatchesExpected"] is True for row in rows),
            "measurementMentionCount": sum(row["score"]["mentionsAllMeasurements"] is True for row in rows)}
    result["modelConsistencyVerified"] = all(pair[arm]["returnedModelMatchesRequested"] is True for pair in valid for arm in ("mubit", "direct"))
    if not result["modelConsistencyVerified"]:
        result["caveat"] = "The returned model identifiers did not all confirm the requested model. Direct comparison deltas are withheld. " + CAVEAT
        return result
    result["difference"], result["percentDifference"] = {}, {}
    for metric, name in (("promptTokens", "meanPromptTokens"), ("completionTokens", "meanCompletionTokens"),
                         ("totalTokens", "meanTotalTokens"), ("latencyMs", "meanLatencyMs")):
        a, b = result["mubit"][name], result["direct"][name]
        result["difference"][metric] = round(a - b, 3) if a is not None and b is not None else None
        result["percentDifference"][metric] = round((a - b) / b * 100, 3) if a is not None and b not in (None, 0) else None
    return result


def _reject(code, message):
    result = status()
    result.update(ok=False, code=code, message=message, noInferenceSent=True)
    result["comparison"]["message"] = message
    return result


def run(payload):
    """Run this one-shot pilot once. Existing or uncertain attempts cannot resume."""
    direct_key = mub_key = None
    if not isinstance(payload, dict) or set(payload) != {"openaiKey", "expectedInputHash"}:
        return _reject("invalid_input", "Supply the direct key and the inspected input hash.")
    try:
        with _storage_lock():
            _, existing = _latest()
        if existing is not None:
            return {**status(), "alreadyRequested": True}
    except Exception:
        return _unknown()
    if (not _valid_direct_key(payload["openaiKey"]) or not isinstance(payload["expectedInputHash"], str)
            or not _HASH.fullmatch(payload["expectedInputHash"])):
        return _reject("invalid_input", "Enter a valid direct OpenAI key and inspect the saved input before starting.")
    direct_key = payload["openaiKey"]
    if not bridge._probe_lock.acquire(blocking=False):
        return _reject("busy", "Another Mubit action is in progress. Nothing was sent.")
    path = receipt = None
    try:
        preview = _preview()
        if preview["inputHash"] != payload["expectedInputHash"]:
            return _reject("context_changed", "The saved input changed. Refresh and inspect it before starting. Nothing was sent.")
        with bridge._state_lock:
            config = bridge._read_config()
            bridge._sync_config_locked(config)
            mub_key, model, fingerprint = config
            connection = bridge._snapshot_locked()
            not_ready = (model != MODEL or not bridge._valid_key(mub_key) or connection.get("verified") is not True
                         or connection.get("ready") is not True or connection["callsRemaining"] < 3)
        if not_ready:
            return _reject("not_ready", "Verify Mubit with gpt-6-luna and at least three calls remaining. Nothing was sent.")
        body = _encode(preview["body"])
        if any(key.encode() in body for key in (direct_key, mub_key)):
            return _reject("invalid_input", "The frozen input is not suitable for this comparison. Nothing was sent.")
        availability = _direct_preflight(direct_key)
        if availability["available"] is not True:
            result = _reject("direct_preflight_failed", "The direct OpenAI key could not verify access to gpt-6-luna. No inference was sent. Check the key, account and model access before trying the setup again.")
            result["directAvailability"] = availability
            return result
        # Model availability can take time. Re-read the source immediately
        # before freezing it; a changed preview is a definite no-send outcome.
        refreshed = _preview()
        if any(refreshed[name] != preview[name] for name in ("caseId", "caseRevision", "inputHash", "bodyHash")):
            return _reject("context_changed", "The saved input changed during the connection check. Nothing was sent. Refresh and inspect it again.")
        preview = refreshed
        with _storage_lock(create=True):
            _, existing = _latest()
            if existing is not None:
                pass
            else:
                identifier = "comparison_" + uuid.uuid4().hex
                path = COMPARISONS_DIR / identifier
                path.mkdir(mode=0o700)
                manifest = {"format": "dino-provider-comparison-manifest-v1", "id": identifier,
                    "createdAt": _now(), **preview, "orders": [list(order) for order in PAIR_ORDERS],
                    "endpoints": {"mubit": MUBIT_URL, "direct": DIRECT_URL},
                    "directAvailability": availability,
                    "mubitRecordingRequested": False, "providerStorageRequested": False,
                    "memoryExamples": 0, "scoring": "JSON/schema, measured label, expected decision, and literal mention of every frozen baseline/candidate score and survival-step count. Mention matching is a limited heuristic.",
                    "caveat": CAVEAT}
                manifest["manifestHash"] = _hash(manifest)
                _write(path / "manifest.json", manifest, new=True)
                receipt = {"format": "dino-provider-comparison-receipt-v1", "id": identifier,
                    "manifestHash": manifest["manifestHash"], "status": "pending", "startedAt": _now(),
                    "completedAt": None, "message": "One explicit pilot has started. Interrupted attempts are preserved and never resumed.",
                    "pairs": [{"index": index, "order": list(order), "mubit": None, "direct": None}
                              for index, order in enumerate(PAIR_ORDERS, 1)], "summary": None}
                _save_receipt(path, receipt, new=True)
        if existing is not None:
            return {**status(), "alreadyRequested": True}
        for pair in receipt["pairs"]:
            for arm in pair["order"]:
                # Hold the bridge's shared probe lock throughout. Key/model
                # changes are still checked before every arm and stop the pilot.
                with bridge._state_lock:
                    current = bridge._read_config()
                    bridge._sync_config_locked(current)
                    changed = current[2] != fingerprint or current[1] != MODEL
                    if not changed and arm == "mubit":
                        if bridge._attempts >= bridge.MAX_INFERENCE_ATTEMPTS:
                            changed = True
                        else:
                            bridge._attempts += 1
                if changed:
                    receipt.update(status="failed", completedAt=_now(), message="The Mubit configuration or request allowance changed. Remaining calls were not sent.")
                    receipt["summary"] = _summary(receipt["pairs"])
                    with _storage_lock():
                        _save_receipt(path, receipt)
                    return {**status(), "ok": False}
                pair[arm] = _pending_arm()
                with _storage_lock():
                    _save_receipt(path, receipt)
                result = _perform(arm, body, mub_key if arm == "mubit" else direct_key,
                                  direct_key if arm == "mubit" else mub_key, pair[arm], preview["input"])
                pair[arm] = result
                receipt["summary"] = _summary(receipt["pairs"])
                if result["status"] != "succeeded":
                    receipt.update(status="failed", completedAt=_now(),
                        message="The pilot stopped after a failed or unconfirmed request. The evidence is saved; no request was retried.")
                with _storage_lock():
                    _save_receipt(path, receipt)
                if receipt["status"] == "failed":
                    return {**status(), "ok": False}
        receipt.update(status="succeeded", completedAt=_now(), message="All three matched pairs are saved. Interpret the token and timing comparison with the stated limits.")
        with _storage_lock():
            _save_receipt(path, receipt)
        return status()
    except _SourceNotReady as error:
        return _reject("source_not_ready", str(error))
    except Exception:
        # An unpersisted provider outcome must never be made retryable. A durable
        # pending receipt (or incomplete directory) remains an unknown attempt.
        return _unknown("The pilot could not be completed or its latest result could not be saved. Existing evidence is preserved; do not repeat it.")
    finally:
        direct_key = mub_key = None
        bridge._probe_lock.release()
