"""Private, bounded experiment records and one explicit, durable coaching attempt.

The browser supplies observed run evidence. Comparisons below check consistency;
they are not a claim of independently replaying or authenticating that evidence.
No model request happens when this module is imported or records are read/saved.
"""
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import uuid

import mubit_bridge

EXPERIMENTS_DIR = Path(__file__).resolve().parent / "experiments"
MAX_EXPERIMENTS = 100
MAX_RUN_BYTES = 12 * 1024 * 1024
MAX_BYTES = 5 * MAX_RUN_BYTES + 65536
MAX_IMAGE_BYTES = 250 * 1024
BASELINE_CONFIG = dict(mubit_bridge.EXPERIMENT_BASELINE_CONFIG)
PARAMETER_BOUNDS = mubit_bridge.EXPERIMENT_PARAMETER_BOUNDS
SLOTS = {
    "baseline-a": {"seed": 104729, "kind": "baseline"},
    "repeat-a": {"seed": 104729, "kind": "repeat"},
    "baseline-b": {"seed": 130363, "kind": "baseline"},
    "candidate-a": {"seed": 104729, "kind": "candidate"},
    "candidate-b": {"seed": 130363, "kind": "candidate"},
}
PLAN = {"protocol": "native60-v1", "mode": "realtime", "frameRate": 60, "maxTicks": 3600,
        "seeds": {"a": 104729, "b": 130363}, "baselineConfig": BASELINE_CONFIG,
        "parameterBounds": PARAMETER_BOUNDS, "slots": SLOTS}
LEGACY_PROVENANCE = {"source": "browser-recorded-local-experiment", "controller": "local-vision-rules",
              "mubitControlsGameplay": False, "modelTraining": False, "defaultPolicyChanged": False}
TRAINING_EVIDENCE = {"modelTrainingScope": "local-workflow", "providerTrainingVerification": "unverified"}
PROVENANCE = {**LEGACY_PROVENANCE, **TRAINING_EVIDENCE}
GATEWAY_PROVENANCE = {"source": "mubit-gateway", "modelTraining": False, **TRAINING_EVIDENCE}
_ID = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
_SECRET = mubit_bridge._EXPERIMENT_SECRET
_RUN_FIELDS = {"slot", "runId", "protocol", "seed", "maxTicks", "config", "startedAt", "endedAt",
               "wallDurationMs", "ticks", "score", "outcome", "canvasWidth", "maxFrameGapMs",
               "actions", "frames", "course"}
_RECORD_FIELDS = {"schemaVersion", "id", "createdAt", "plan", "runs", "proposal", "provenance"}
_LOCK = threading.RLock()


class _Invalid(ValueError):
    pass


def _error(code, message):
    return {"ok": False, "code": code, "message": message}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _number(value, low, high):
    return type(value) in (int, float) and math.isfinite(value) and low <= value <= high


def _text(value, maximum, allow_empty=False):
    return mubit_bridge._proposal_text(value, maximum, allow_empty)


def _valid_id(value):
    return isinstance(value, str) and bool(_ID.fullmatch(value)) and not _SECRET.search(value)


def _timestamp(value):
    if not _text(value, 40):
        raise _Invalid()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        raise _Invalid() from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _Invalid()
    return parsed


def _encode(data, limit=MAX_BYTES):
    try:
        raw = json.dumps(data, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode()
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise _Invalid() from None
    if len(raw) > limit:
        raise _Invalid()
    return raw


def _clone(data):
    return json.loads(_encode(data))


def _plan_for_seeds(seeds):
    if (not isinstance(seeds, dict) or set(seeds) != {"a", "b"}
            or any(not _integer(value, 0, 0xffffffff) for value in seeds.values())
            or seeds["a"] == seeds["b"]):
        raise _Invalid()
    plan = _clone(PLAN)
    plan["seeds"] = dict(seeds)
    for slot in plan["slots"]:
        plan["slots"][slot]["seed"] = seeds[slot[-1]]
    return plan


def _reserved_for_evaluation(record):
    # The fixed split is a pure local rule. No provider or storage lookup occurs.
    import learning_store
    return learning_store.is_evaluation_seeds(record["plan"]["seeds"])


def _png_image(value):
    if (not isinstance(value, str) or len(value) > MAX_IMAGE_BYTES
            or not value.startswith("data:image/png;base64,")):
        return False
    try:
        raw = base64.b64decode(value[22:], validate=True)
    except (ValueError, UnicodeError):
        return False
    return len(raw) <= MAX_IMAGE_BYTES and mubit_bridge._valid_png(raw)


def _config(record, slot):
    config = dict(BASELINE_CONFIG)
    if SLOTS[slot]["kind"] == "candidate":
        proposal = record["proposal"]
        if proposal["status"] != "succeeded":
            raise _Invalid()
        change = mubit_bridge._validate_proposal_result(proposal["result"])
        config[change["parameter"]] = change["value"]
    return config


def _validate_run(payload, record):
    if not isinstance(payload, dict) or set(payload) != _RUN_FIELDS:
        raise _Invalid()
    slot = payload["slot"]
    if not isinstance(slot, str) or slot not in SLOTS:
        raise _Invalid()
    expected = _config(record, slot)
    if (not _valid_id(payload["runId"]) or payload["protocol"] != PLAN["protocol"]
            or type(payload["seed"]) is not int or payload["seed"] != record["plan"]["slots"][slot]["seed"]
            or type(payload["maxTicks"]) is not int or payload["maxTicks"] != PLAN["maxTicks"]
            or not isinstance(payload["config"], dict) or payload["config"] != expected
            or any(type(value) is not int for value in payload["config"].values())
            or not _integer(payload["ticks"], 0, PLAN["maxTicks"])
            or not _integer(payload["score"], 0, 1000000)
            or not _integer(payload["wallDurationMs"], 0, 3600000)
            or not _number(payload["maxFrameGapMs"], 0, 3600000)
            or type(payload["canvasWidth"]) is not int or payload["canvasWidth"] != 600
            or payload["outcome"] not in ("collision", "time_limit", "stopped", "hidden", "lagged", "error", "resized")
            or (payload["outcome"] == "time_limit" and payload["ticks"] != PLAN["maxTicks"])):
        raise _Invalid()
    elapsed = (_timestamp(payload["endedAt"]) - _timestamp(payload["startedAt"])).total_seconds() * 1000
    if elapsed < 0 or abs(elapsed - payload["wallDurationMs"]) > 500:
        raise _Invalid()
    actions, frames, course = payload["actions"], payload["frames"], payload["course"]
    if (not isinstance(actions, list) or len(actions) > 1200
            or not isinstance(frames, list) or len(frames) > 60
            or not isinstance(course, list) or len(course) > 300):
        raise _Invalid()
    for entries, fields in ((actions, {"tick", "tMs", "action", "score", "reason"}),
                            (frames, {"tick", "tMs", "score", "image"})):
        previous = -1
        for item in entries:
            if (not isinstance(item, dict) or set(item) != fields
                    or not _integer(item["tick"], 0, payload["ticks"]) or item["tick"] < previous
                    or type(item["tMs"]) is not int or item["tMs"] != round(item["tick"] * 1000 / 60)
                    or not _integer(item["score"], 0, payload["score"])):
                raise _Invalid()
            previous = item["tick"]
            if entries is actions:
                if item["action"] not in ("jump", "duck", "run") or not _text(item["reason"], 200, True):
                    raise _Invalid()
            elif not _png_image(item["image"]):
                raise _Invalid()
    previous_tick = -1
    for index, item in enumerate(course):
        if (not isinstance(item, dict)
                or set(item) != {"index", "tick", "type", "size", "y", "width", "gap", "speedOffset"}
                or type(item["index"]) is not int or item["index"] != index
                or not _integer(item["tick"], 0, payload["ticks"]) or item["tick"] < previous_tick
                or item["type"] not in ("CACTUS_SMALL", "CACTUS_LARGE", "PTERODACTYL")
                or not _integer(item["size"], 1, 3) or not _integer(item["y"], 0, 200)
                or not _integer(item["width"], 1, 200) or not _integer(item["gap"], 0, 3000)
                or not _number(item["speedOffset"], -10, 10)):
            raise _Invalid()
        previous_tick = item["tick"]
    clean = json.loads(_encode(payload, MAX_RUN_BYTES))
    # Persist canonical server-derived parameters after validating the browser's claim.
    clean.update(seed=record["plan"]["slots"][slot]["seed"], config=expected, maxTicks=PLAN["maxTicks"])
    return clean


def _complete(run):
    return (run["outcome"] in ("collision", "time_limit") and run["ticks"] > 0
            and bool(run["course"]) and bool(run["frames"])
            and run["maxFrameGapMs"] <= 250
            and abs(run["wallDurationMs"] - run["ticks"] * 1000 / 60) <= 500)


def _baseline_check(record):
    runs = record["runs"]
    if any(slot not in runs for slot in ("baseline-a", "repeat-a", "baseline-b")):
        return "awaiting_baselines", "Record both baseline courses and an identical repeat of course A."
    if any(not _complete(runs[slot]) for slot in ("baseline-a", "repeat-a", "baseline-b")):
        return "inconclusive", "A baseline was interrupted, lagged, or lacks complete course and screenshot evidence."
    a, repeat = runs["baseline-a"], runs["repeat-a"]
    if any(a[key] != repeat[key] for key in ("ticks", "score", "outcome", "actions", "course")):
        return "baseline_mismatch", "Course A did not repeat exactly in ticks, score, outcome, actions and course evidence."
    return None, None


def _comparison(record):
    status, reason = _baseline_check(record)
    result = {"status": status or "awaiting_proposal", "comparable": False,
              "reason": reason or "Ask Mubit for one change before recording candidate runs.",
              "pairs": [], "aggregate": None,
              "evidenceSource": "saved-browser-observations",
              "defaultPolicyChanged": False, "modelTraining": False, **TRAINING_EVIDENCE}
    if status:
        return result
    if record["proposal"]["status"] != "succeeded":
        return result
    runs = record["runs"]
    if any(slot not in runs for slot in ("candidate-a", "candidate-b")):
        result.update(status="awaiting_candidates", reason="Record the proposed setting on both matching courses.")
        return result
    for course in ("a", "b"):
        baseline_slot, candidate_slot = "baseline-" + course, "candidate-" + course
        baseline, candidate = runs[baseline_slot], runs[candidate_slot]
        shared_tick = min(baseline["ticks"], candidate["ticks"])
        baseline_prefix = [item for item in baseline["course"] if item["tick"] <= shared_tick]
        candidate_prefix = [item for item in candidate["course"] if item["tick"] <= shared_tick]
        complete = _complete(candidate)
        matching = bool(baseline_prefix) and baseline_prefix == candidate_prefix
        comparable = complete and matching
        result["pairs"].append({
            "course": course, "seed": record["plan"]["seeds"][course],
            "baselineSlot": baseline_slot, "candidateSlot": candidate_slot,
            "baselineScore": baseline["score"], "candidateScore": candidate["score"],
            "baselineTicks": baseline["ticks"], "candidateTicks": candidate["ticks"],
            "scoreDelta": candidate["score"] - baseline["score"],
            "tickDelta": candidate["ticks"] - baseline["ticks"], "comparable": comparable,
            "reason": ("Matching course evidence through the shared run duration."
                       if comparable else "Candidate was interrupted, lagged or lacks complete evidence."
                       if not complete else "Course evidence differs within the shared run duration."),
        })
    comparable = all(pair["comparable"] for pair in result["pairs"])
    result.update(status="complete" if comparable else "inconclusive", comparable=comparable,
                  reason="Both recorded course pairs can be compared." if comparable
                  else "At least one course pair cannot support a verified comparison.")
    if not comparable:
        return result
    pairs = result["pairs"]
    deltas = [pair["tickDelta"] for pair in pairs]
    conclusion = ("improved_on_both" if all(delta > 0 for delta in deltas)
                  else "worse_on_both" if all(delta < 0 for delta in deltas)
                  else "unchanged" if all(delta == 0 for delta in deltas) else "mixed")
    result["aggregate"] = {field: sum(pair[field] for pair in pairs) for field in (
        "baselineScore", "candidateScore", "scoreDelta", "baselineTicks", "candidateTicks", "tickDelta")}
    result["aggregate"].update(conclusion=conclusion,
        caveat="Two selected courses, capped at 60 seconds each; this does not establish general improvement. The default policy is unchanged.")
    return result


def _view(record):
    reserved = _reserved_for_evaluation(record)
    return {**_clone(record), "comparison": _comparison(record),
            "reviewAllowed": not reserved, "evaluationReserved": reserved,
            "reviewRestriction": ("Reserved test courses stay out of recorded Mubit calls and training. A verified evaluation route is required." if reserved else None)}


def _validate_proposal_record(proposal):
    if not isinstance(proposal, dict):
        raise _Invalid()
    status = proposal.get("status")
    if status == "idle":
        if set(proposal) != {"status"}:
            raise _Invalid()
        return
    base = {"status", "startedAt", "model", "message", "provenance"}
    if status == "pending":
        allowed = base
    elif status in ("succeeded", "failed"):
        allowed = base | {"completedAt", "requestId", "latencyMs", "usage", "callsRemaining"}
        if status == "succeeded":
            allowed |= {"result"}
        else:
            allowed |= {"httpStatus", "errorCode", "errorType", "errorParam", "providerMessage",
                        "validationReason", "finishReason", "contentLength"}
    else:
        raise _Invalid()
    required = base if status == "pending" else base | {"completedAt", "requestId", "latencyMs", "usage", "callsRemaining"}
    if (set(proposal) - allowed or required - set(proposal)
            or not _text(proposal["message"], 1500)
            or (proposal["model"] is not None and not _text(proposal["model"], 160))
            or proposal["provenance"] not in (GATEWAY_PROVENANCE, {"source": "mubit-gateway", "modelTraining": False})):
        raise _Invalid()
    _timestamp(proposal["startedAt"])
    if status == "pending":
        return
    if _timestamp(proposal["completedAt"]) < _timestamp(proposal["startedAt"]):
        raise _Invalid()
    if (proposal["requestId"] is not None and not mubit_bridge._safe_identifier(proposal["requestId"], mubit_bridge._REQUEST_ID, "")):
        raise _Invalid()
    if (not _integer(proposal["latencyMs"], 0, 3600000)
            or not _integer(proposal["callsRemaining"], 0, mubit_bridge.MAX_INFERENCE_ATTEMPTS)):
        raise _Invalid()
    usage = proposal["usage"]
    if usage is not None and (not isinstance(usage, dict)
            or set(usage) != {"promptTokens", "completionTokens", "totalTokens"}
            or any(value is not None and not _integer(value, 0, 1000000000) for value in usage.values())):
        raise _Invalid()
    if status == "succeeded":
        if proposal["requestId"] is None or proposal["model"] is None:
            raise _Invalid()
        mubit_bridge._validate_proposal_result(proposal.get("result"))
    else:
        for field in ("errorCode", "errorType", "errorParam", "providerMessage"):
            if proposal.get(field) is not None and not _text(proposal[field], 1000):
                raise _Invalid()
        if proposal.get("httpStatus") is not None and not _integer(proposal["httpStatus"], 100, 599):
            raise _Invalid()
        if (proposal.get("validationReason") is not None
                and (not isinstance(proposal["validationReason"], str)
                     or proposal["validationReason"] not in mubit_bridge.VALIDATION_REASONS)):
            raise _Invalid()
        if (proposal.get("finishReason") is not None
                and (not isinstance(proposal["finishReason"], str)
                     or proposal["finishReason"] not in mubit_bridge.FINISH_REASONS)):
            raise _Invalid()
        if proposal.get("contentLength") is not None and not _integer(proposal["contentLength"], 0, mubit_bridge.MAX_RESPONSE_BYTES):
            raise _Invalid()


def _directory():
    if EXPERIMENTS_DIR.is_symlink():
        raise OSError()
    EXPERIMENTS_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not EXPERIMENTS_DIR.is_dir():
        raise OSError()


@contextmanager
def _storage_lock():
    with _LOCK:
        _directory()
        descriptor = os.open(EXPERIMENTS_DIR / ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise OSError()
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _Invalid()
        result[key] = value
    return result


def _read(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as file:
        if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
            raise _Invalid()
        raw = file.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise _Invalid()
    try:
        record = json.loads(raw, object_pairs_hook=_unique)
    except RecursionError:
        raise _Invalid() from None
    if (not isinstance(record, dict) or _RECORD_FIELDS - set(record)
            or set(record) - _RECORD_FIELDS - {"baselineSourceExperimentId"}
            or type(record["schemaVersion"]) is not int or record["schemaVersion"] != 1
            or not _valid_id(record["id"]) or path.stem != record["id"]
            or not isinstance(record["plan"], dict)
            or record["plan"] != _plan_for_seeds(record["plan"].get("seeds"))
            or record["provenance"] not in (PROVENANCE, LEGACY_PROVENANCE)
            or not isinstance(record["runs"], dict) or set(record["runs"]) - set(SLOTS)):
        raise _Invalid()
    _timestamp(record["createdAt"])
    _validate_proposal_record(record["proposal"])
    run_ids = set()
    for slot, run in record["runs"].items():
        clean = _validate_run(run, record)
        if clean["slot"] != slot or clean["runId"] in run_ids:
            raise _Invalid()
        run_ids.add(clean["runId"])
    if record["proposal"]["status"] != "idle" and _baseline_check(record)[0] is not None:
        raise _Invalid()
    if "baselineSourceExperimentId" in record:
        source = record["baselineSourceExperimentId"]
        if not _valid_id(source) or source == record["id"] or _baseline_check(record)[0] is not None:
            raise _Invalid()
    return record


def _write(record, *, new=False):
    raw, temporary = _encode(record), None
    try:
        descriptor, name = tempfile.mkstemp(prefix=".pending-", dir=EXPERIMENTS_DIR)
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as file:
            file.write(raw)
            file.flush()
            os.fsync(file.fileno())
        destination = EXPERIMENTS_DIR / (record["id"] + ".json")
        if new:
            os.link(temporary, destination)
            temporary.unlink()
        else:
            os.replace(temporary, destination)
        temporary = None
        directory_fd = os.open(EXPERIMENTS_DIR, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def _load(identifier):
    if not _valid_id(identifier):
        return None, _error("invalid_id", "The experiment identifier is invalid.")
    try:
        return _read(EXPERIMENTS_DIR / (identifier + ".json")), None
    except FileNotFoundError:
        return None, _error("not_found", "This experiment was not found.")
    except (OSError, ValueError, TypeError, KeyError, OverflowError, mubit_bridge._InvalidResponse):
        return None, _error("unavailable", "This experiment is unreadable or invalid. Its contents are unknown and were not changed.")


def create_experiment(payload):
    if not isinstance(payload, dict) or set(payload) - {"seeds"}:
        return _error("invalid_request", "Create a comparison with optional distinct course seeds; its timing and settings stay fixed.")
    try:
        plan = _plan_for_seeds(payload.get("seeds", PLAN["seeds"]))
    except _Invalid:
        return _error("invalid_request", "Choose two distinct whole-number course seeds between 0 and 4294967295.")
    record = {"schemaVersion": 1, "id": "exp_" + uuid.uuid4().hex, "createdAt": _now(),
              "plan": plan, "runs": {}, "proposal": {"status": "idle"}, "provenance": dict(PROVENANCE)}
    try:
        with _storage_lock():
            if sum(1 for path in EXPERIMENTS_DIR.iterdir() if path.name.endswith(".json")) >= MAX_EXPERIMENTS:
                return _error("storage_full", "The experiment storage limit has been reached. Existing records were preserved.")
            _write(record, new=True)
        return {"ok": True, "experiment": _view(record)}
    except OSError:
        return _error("storage_unavailable", "The experiment save could not be confirmed. Check the saved list before creating another.")


def reuse_baselines(identifier, payload):
    """Explicitly create a new comparison from one failed review's saved baselines.

    The failed experiment is never rewritten. Unknown/pending and successful
    attempts are ineligible, and copying records never calls the provider.
    """
    if not isinstance(payload, dict) or payload:
        return _error("invalid_request", "Baseline reuse accepts an empty request and reads the saved records only.")
    try:
        with _storage_lock():
            source, error = _load(identifier)
            if error:
                return error
            if source["proposal"]["status"] != "failed":
                return _error("reuse_not_allowed", "Only a comparison with a recorded failed review can supply these baselines. Pending or unknown reviews cannot be reused.")
            status, reason = _baseline_check(source)
            if status:
                return _error("baselines_required", reason)
            if sum(1 for path in EXPERIMENTS_DIR.iterdir() if path.name.endswith(".json")) >= MAX_EXPERIMENTS:
                return _error("storage_full", "The experiment storage limit has been reached. Existing records were preserved.")
            record = {"schemaVersion": 1, "id": "exp_" + uuid.uuid4().hex, "createdAt": _now(),
                      "plan": _clone(source["plan"]), "runs": _clone({slot: source["runs"][slot]
                          for slot in ("baseline-a", "repeat-a", "baseline-b")}),
                      "proposal": {"status": "idle"}, "provenance": dict(PROVENANCE),
                      "baselineSourceExperimentId": source["id"]}
            _write(record, new=True)
            return {"ok": True, "experiment": _view(record)}
    except OSError:
        return _error("storage_unavailable", "The copied comparison's save could not be confirmed. Check the saved list before creating another; no review was sent.")


def get_experiment(identifier):
    try:
        with _storage_lock():
            record, error = _load(identifier)
            return error or {"ok": True, "experiment": _view(record)}
    except OSError:
        return _error("storage_unavailable", "Saved experiments could not be read. Their contents are unknown.")


def list_experiments():
    records, unavailable = [], []
    try:
        with _storage_lock():
            for path in EXPERIMENTS_DIR.iterdir():
                if not path.name.endswith(".json"):
                    continue
                record, error = _load(path.stem)
                if error:
                    unavailable.append({"id": path.stem if _valid_id(path.stem) else None,
                                        "code": "unavailable", "message": "A saved experiment is unreadable or invalid and was preserved."})
                else:
                    records.append({"id": record["id"], "createdAt": record["createdAt"],
                                    "runCount": len(record["runs"]), "proposalStatus": record["proposal"]["status"],
                                    "comparison": _comparison(record),
                                    **({"baselineSourceExperimentId": record["baselineSourceExperimentId"]}
                                       if "baselineSourceExperimentId" in record else {})})
        records.sort(key=lambda record: (record["createdAt"], record["id"]), reverse=True)
        return {"ok": True, "experiments": records, "unavailable": unavailable, "limit": MAX_EXPERIMENTS}
    except OSError:
        return _error("storage_unavailable", "Saved experiments could not be read. Their contents are unknown.")


def save_run(identifier, payload):
    try:
        with _storage_lock():
            record, error = _load(identifier)
            if error:
                return error
            try:
                run = _validate_run(payload, record)
            except (ValueError, TypeError, KeyError, OverflowError, mubit_bridge._InvalidResponse):
                return _error("invalid_run", "The run does not match this experiment's fixed protocol, course, settings or evidence limits.")
            slot = run["slot"]
            if slot in record["runs"]:
                if record["runs"][slot] == run:
                    return {"ok": True, "alreadySaved": True, "experiment": _view(record)}
                return _error("conflict", "This experiment slot already contains a different run. It was not changed.")
            if any(saved["runId"] == run["runId"] for saved in record["runs"].values()):
                return _error("conflict", "This run identifier is already used by another slot.")
            record["runs"][slot] = run
            _write(record)
            return {"ok": True, "experiment": _view(record)}
    except OSError:
        return _error("storage_unavailable", "The run save could not be confirmed. Read the experiment before retrying the same record.")


def _proposal_payload(record):
    courses = []
    for slot in ("baseline-a", "baseline-b"):
        run = record["runs"][slot]
        courses.append({**{key: run[key] for key in ("slot", "seed", "score", "ticks", "outcome")},
                        "actionCount": len(run["actions"]), "actionsTruncated": len(run["actions"]) > 60,
                        "actions": run["actions"][-60:], "frameCount": len(run["frames"]),
                        "framesTruncated": len(run["frames"]) > 6, "frames": run["frames"][-6:]})
    return {"source": "saved-local-experiment", "runId": record["id"], "protocol": PLAN["protocol"],
            "mode": "realtime", "maxTicks": PLAN["maxTicks"], "baselineConfig": dict(BASELINE_CONFIG),
            "courses": _clone(courses)}


def _completed_proposal(pending, response):
    response = response if isinstance(response, dict) else {}
    proposal = {**pending, "status": "failed", "completedAt": _now(),
                "message": "Mubit did not return a usable proposal. The attempt was not retried.",
                "requestId": None, "latencyMs": 0, "usage": None, "callsRemaining": 0}
    if mubit_bridge._safe_identifier(response.get("requestId"), mubit_bridge._REQUEST_ID, ""):
        proposal["requestId"] = response["requestId"]
    for field, maximum in (("latencyMs", 3600000), ("callsRemaining", mubit_bridge.MAX_INFERENCE_ATTEMPTS)):
        if _integer(response.get(field), 0, maximum):
            proposal[field] = response[field]
    usage = response.get("usage")
    if (isinstance(usage, dict) and set(usage) == {"promptTokens", "completionTokens", "totalTokens"}
            and all(value is None or _integer(value, 0, 1000000000) for value in usage.values())):
        proposal["usage"] = dict(usage)
    if _text(response.get("model"), 160):
        proposal["model"] = response["model"]
    if response.get("ok") is True:
        try:
            result = mubit_bridge._validate_proposal_result({key: response[key] for key in ("parameter", "value", "rationale", "caveat")})
            if proposal["requestId"] is not None and proposal["model"] is not None:
                proposal.update(status="succeeded", result=result,
                    message="Mubit proposed one timing change. Run both candidate courses to compare it; the default policy is unchanged.")
        except (KeyError, TypeError, mubit_bridge._InvalidResponse):
            pass
    else:
        for field, maximum in (("message", 1500), ("errorCode", 160), ("errorType", 160), ("errorParam", 160), ("providerMessage", 1000)):
            if _text(response.get(field), maximum):
                proposal[field] = response[field]
        if _integer(response.get("httpStatus"), 100, 599):
            proposal["httpStatus"] = response["httpStatus"]
        if isinstance(response.get("validationReason"), str) and response["validationReason"] in mubit_bridge.VALIDATION_REASONS:
            proposal["validationReason"] = response["validationReason"]
        if isinstance(response.get("finishReason"), str) and response["finishReason"] in mubit_bridge.FINISH_REASONS:
            proposal["finishReason"] = response["finishReason"]
        if _integer(response.get("contentLength"), 0, mubit_bridge.MAX_RESPONSE_BYTES):
            proposal["contentLength"] = response["contentLength"]
    return proposal


def propose_change(identifier, payload):
    if not isinstance(payload, dict) or payload:
        return _error("invalid_request", "Coaching uses the saved baseline records; no client-supplied evidence or change is accepted.")
    try:
        with _storage_lock():
            record, error = _load(identifier)
            if error:
                return error
            if record["proposal"]["status"] != "idle":
                return {"ok": True, "alreadyRequested": True, "experiment": _view(record)}
            if _reserved_for_evaluation(record):
                return _error("evaluation_reserved", "These courses are reserved for testing. Recorded Mubit reviews are blocked so their evidence cannot become training examples.")
            status, reason = _baseline_check(record)
            if status:
                return _error("baselines_required", reason)
            model = mubit_bridge.connection_status().get("model")
            pending = {"status": "pending", "startedAt": _now(),
                       "model": model if _text(model, 160) else None,
                       "message": "One coaching request was started. If it was interrupted, its outcome is unknown and it will not be resent.",
                       "provenance": dict(GATEWAY_PROVENANCE)}
            record["proposal"] = pending
            evidence = _proposal_payload(record)
            # Persist intent before the call. An interrupted/uncertain attempt cannot be billed twice.
            _write(record)
        try:
            response = mubit_bridge.propose(evidence)
        except Exception:
            response = {"ok": False}
        completed = _completed_proposal(pending, response)
        with _storage_lock():
            record, error = _load(identifier)
            if error:
                return error
            if record["proposal"] != pending:
                return _error("outcome_unknown", "The saved coaching state changed during the request. Its result was not substituted or retried.")
            record["proposal"] = completed
            _write(record)
            return {"ok": True, "experiment": _view(record)}
    except OSError:
        return _error("storage_unavailable", "The coaching outcome could not be confirmed. Read the saved experiment; no automatic retry was made.")
