"""Local correction custody. Nothing in this module sends data or starts training."""
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

import experiment_store
import mubit_bridge

LEARNING_DIR = Path(__file__).resolve().parent / "learning"
MAX_CASES = 100
MAX_REVISIONS = 200
MAX_CASE_BYTES = 16 * 1024 * 1024
MAX_EXPORT_BYTES = 64 * 1024 * 1024
EXPORT_TASK = "review-tested-controller-change-v1"
_LOCK = threading.RLock()
_ID = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_PROVIDER = {"status": "unverified_contract", "adapterId": None, "trainingJobId": None,
             "trained": False, "correctionsUploaded": False,
             "message": "The route from approved corrections to Mubit training, screenshot eligibility and model-version access has not been verified. Local approval and export do not update a model."}
_TRAINING = [{"id": f"train-{i + 1:02}", "split": "training", "seeds":
              ({"a": 104729, "b": 130363} if i == 0 else
               {"a": 1000003 + i * 2018, "b": 1001012 + i * 2018})} for i in range(40)]
_EVALUATION = [{"id": f"test-{i + 1:02}", "split": "evaluation",
                "seeds": {"a": 30000001 + i * 2018, "b": 30001010 + i * 2018}} for i in range(10)]
PLAN = {"version": "course-split-v1", "locked": True, "trainingTarget": 40, "evaluationTarget": 10,
        "unit": "two-course-scenario", "trainingCases": _TRAINING, "evaluationCases": _EVALUATION,
        "trainingSeeds": [s for item in _TRAINING for s in item["seeds"].values()],
        "evaluationSeeds": [s for item in _EVALUATION for s in item["seeds"].values()],
        "message": "A reserved collection plan, not collected examples. Evaluation courses never enter training exports."}
_PLANNED = {item["id"]: item for item in _TRAINING + _EVALUATION}


class _Invalid(ValueError):
    pass


def _error(code, message):
    return {"ok": False, "code": code, "message": message}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _text(value, maximum, allow_empty=False):
    return mubit_bridge._proposal_text(value, maximum, allow_empty)


def _id(value):
    return isinstance(value, str) and bool(_ID.fullmatch(value)) and not mubit_bridge._EXPERIMENT_SECRET.search(value)


def _encode(value, limit=MAX_CASE_BYTES):
    try:
        raw = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (ValueError, TypeError, RecursionError, OverflowError):
        raise _Invalid() from None
    if len(raw) > limit:
        raise _Invalid()
    return raw


def _clone(value):
    return json.loads(_encode(value))


def _hash(value):
    return hashlib.sha256(_encode(value, MAX_EXPORT_BYTES)).hexdigest()


def is_evaluation_seeds(seeds):
    """Pure routing guard; held-out courses must not reach recorded inference."""
    return isinstance(seeds, dict) and any(type(value) is int and value in PLAN["evaluationSeeds"] for value in seeds.values())


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _Invalid()
        result[key] = value
    return result


def _directory():
    LEARNING_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    if LEARNING_DIR.is_symlink() or not stat.S_ISDIR(LEARNING_DIR.lstat().st_mode):
        raise OSError()
    os.chmod(LEARNING_DIR, 0o700)


@contextmanager
def _storage_lock():
    with _LOCK:
        _directory()
        descriptor = os.open(LEARNING_DIR / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise OSError()
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)


def _read_json(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_CASE_BYTES:
            raise _Invalid()
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(MAX_CASE_BYTES + 1)
        if len(raw) > MAX_CASE_BYTES:
            raise _Invalid()
        return json.loads(raw, object_pairs_hook=_unique)
    finally:
        os.close(descriptor)


def _write_json(path, value, new=False):
    raw = _encode(value)
    descriptor, temp = tempfile.mkstemp(prefix=".pending-", dir=LEARNING_DIR)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if new:
            os.link(temp, path)
        else:
            if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
                raise OSError()
            os.replace(temp, path)
        directory = os.open(LEARNING_DIR, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass


def _plan():
    path = LEARNING_DIR / "plan.json"
    try:
        value = _read_json(path)
    except FileNotFoundError:
        if any(p.name.startswith("case_") or p.name == "plan-links.json" for p in LEARNING_DIR.iterdir()):
            raise _Invalid()
        _write_json(path, PLAN, new=True)
        _write_json(LEARNING_DIR / "plan-links.json", {}, new=True)
        value = PLAN
    if value != PLAN:
        raise _Invalid()
    return _clone(value)


def _links():
    path = LEARNING_DIR / "plan-links.json"
    try:
        value = _read_json(path)
    except FileNotFoundError:
        raise _Invalid() from None
    if not isinstance(value, dict) or set(value) - set(_PLANNED):
        raise _Invalid()
    for identifier, link in value.items():
        if (not isinstance(link, dict) or set(link) != {"status", "experimentId", "createdAt"}
                or link["status"] not in ("creating", "ready", "outcome_unknown")
                or not _text(link["createdAt"], 40)
                or (link["status"] == "ready" and not _id(link["experimentId"]))
                or (link["status"] != "ready" and link["experimentId"] is not None)):
            raise _Invalid()
    return value


def _save_links(links):
    path = LEARNING_DIR / "plan-links.json"
    _write_json(path, links, new=not path.exists())


def _plan_view():
    plan, links = _plan(), _links()
    for category in ("trainingCases", "evaluationCases"):
        for item in plan[category]:
            item.update(links.get(item["id"], {"status": "not_started", "experimentId": None}))
    return plan


def _input_fingerprint(payload):
    # Retain the original gateway-input identity separately from review identity.
    return _hash({key: value for key, value in payload.items() if key != "runId"})


def _review_input(source):
    return {"originalModelInput": source["input"], "originalAnswer": source["originalAnswer"],
            "comparison": source["comparison"], "baselineConfig": source["baselineConfig"],
            "candidateConfig": source["candidateConfig"], "observedRuns": source["observedRuns"],
            "evidence": _evidence(source)}


def _task_fingerprint(source):
    # Reused evidence is one task even when copied to another experiment/run ID.
    # Different proposals, tested settings or outcomes are different review tasks.
    payload = _clone(_review_input(source))
    payload["originalModelInput"].pop("runId")
    for run in payload["observedRuns"]:
        run.pop("runId")
    return _hash({"task": EXPORT_TASK, "input": payload})


def _run_excerpt(run):
    return {key: _clone(run[key]) for key in ("slot", "runId", "seed", "config", "ticks", "score", "outcome")} | {
        "actions": _clone(run["actions"][-60:]), "frames": _clone(run["frames"][-6:])}


def _evidence(source):
    result = []
    for pair in source["comparison"]["pairs"]:
        result.append({"id": "comparison:" + pair["course"], "type": "comparison",
            "label": f"Course {pair['course'].upper()}: {pair['baselineScore']} to {pair['candidateScore']} points; {pair['tickDelta']} game-step change.",
            "slot": "candidate-" + pair["course"], "baselineScore": pair["baselineScore"],
            "candidateScore": pair["candidateScore"], "scoreDelta": pair["scoreDelta"], "tickDelta": pair["tickDelta"]})
    for run in source["observedRuns"]:
        slot = run["slot"]
        for index, frame in enumerate(run["frames"]):
            result.append({"id": f"{slot}:frame:{index}", "type": "frame", "slot": slot,
                           "label": f"{slot} at {frame['tMs'] / 1000:.2f}s", **frame})
        for index, action in enumerate(run["actions"]):
            result.append({"id": f"{slot}:action:{index}", "type": "action", "slot": slot,
                           "label": f"{slot}: {action['action']} at {action['tMs'] / 1000:.2f}s", **action})
    return result


def _recommendation(value, references):
    if not isinstance(value, dict):
        raise _Invalid()
    required = {"kind", "rationale", "caveat", "evidenceRefs"}
    if value.get("kind") == "timing_change":
        required |= {"parameter", "value"}
        mubit_bridge._validate_proposal_result({key: value.get(key) for key in ("parameter", "value", "rationale", "caveat")})
    elif value.get("kind") not in ("no_change", "insufficient_evidence"):
        raise _Invalid()
    if (set(value) != required or not _text(value["rationale"], 600)
            or not _text(value["caveat"], 600) or not isinstance(value["evidenceRefs"], list)
            or not 1 <= len(value["evidenceRefs"]) <= 12
            or any(not isinstance(ref, str) or ref not in references for ref in value["evidenceRefs"])
            or len(set(value["evidenceRefs"])) != len(value["evidenceRefs"])):
        raise _Invalid()
    return _clone(value)


def _validate_case(record):
    fields = {"schemaVersion", "id", "createdAt", "split", "sourceExperimentId", "inputFingerprint", "sourceHash", "source", "revisions"}
    if isinstance(record, dict) and record.get("schemaVersion") == 2:
        fields.add("taskInputFingerprint")
    if (not isinstance(record, dict) or set(record) != fields or type(record["schemaVersion"]) is not int or record["schemaVersion"] not in (1, 2)
            or not _id(record["id"]) or not _text(record["createdAt"], 40)
            or record["split"] != "training" or not _id(record["sourceExperimentId"])
            or not isinstance(record["inputFingerprint"], str) or not _HASH.fullmatch(record["inputFingerprint"])
            or not isinstance(record["sourceHash"], str) or not _HASH.fullmatch(record["sourceHash"])):
        raise _Invalid()
    source = record["source"]
    if (not isinstance(source, dict) or set(source) != {"input", "originalAnswer", "model", "requestId", "comparison", "baselineConfig", "candidateConfig", "experimentHash", "observedRuns"}
            or _hash(source) != record["sourceHash"] or not _text(source["model"], 160)
            or not mubit_bridge._safe_identifier(source["requestId"], mubit_bridge._REQUEST_ID, "")
            or not isinstance(source["experimentHash"], str) or not _HASH.fullmatch(source["experimentHash"])
            or _input_fingerprint(source["input"]) != record["inputFingerprint"]
            or source["input"]["runId"] != record["sourceExperimentId"]
            or source["baselineConfig"] != experiment_store.BASELINE_CONFIG):
        raise _Invalid()
    mubit_bridge._validate_proposal(source["input"])
    mubit_bridge._validate_proposal_result(source["originalAnswer"])
    expected = {**source["baselineConfig"], source["originalAnswer"]["parameter"]: source["originalAnswer"]["value"]}
    if source["candidateConfig"] != expected:
        raise _Invalid()
    seeds = {course["seed"] for course in source["input"]["courses"]}
    if len(seeds) != 2 or seeds & set(PLAN["evaluationSeeds"]):
        raise _Invalid()
    comparison = source["comparison"]
    if (not isinstance(comparison, dict) or comparison.get("status") != "complete"
            or comparison.get("comparable") is not True or len(comparison.get("pairs", [])) != 2
            or not isinstance(comparison.get("aggregate"), dict)
            or comparison["aggregate"].get("conclusion") not in ("improved_on_both", "mixed", "unchanged", "worse_on_both")):
        raise _Invalid()
    runs = source["observedRuns"]
    if not isinstance(runs, list) or [r.get("slot") for r in runs] != ["baseline-a", "baseline-b", "candidate-a", "candidate-b"]:
        raise _Invalid()
    for run in runs:
        if (set(run) != {"slot", "runId", "seed", "config", "ticks", "score", "outcome", "actions", "frames"}
                or not _id(run["runId"]) or run["seed"] not in seeds
                or not experiment_store._integer(run["ticks"], 1, 3600)
                or not experiment_store._integer(run["score"], 0, 1000000)
                or run["outcome"] not in ("collision", "time_limit")
                or run["config"] != (source["baselineConfig"] if run["slot"].startswith("baseline") else expected)
                or not isinstance(run["actions"], list) or len(run["actions"]) > 60
                or not isinstance(run["frames"], list) or not 1 <= len(run["frames"]) <= 6):
            raise _Invalid()
        for entries, fields in ((run["frames"], {"tick", "tMs", "score", "image"}),
                                (run["actions"], {"tick", "tMs", "score", "action", "reason"})):
            previous = -1
            for item in entries:
                if (not isinstance(item, dict) or set(item) != fields
                        or not experiment_store._integer(item["tick"], 0, run["ticks"]) or item["tick"] < previous
                        or type(item["tMs"]) is not int or item["tMs"] != round(item["tick"] * 1000 / 60)
                        or not experiment_store._integer(item["score"], 0, run["score"])):
                    raise _Invalid()
                previous = item["tick"]
                if "image" in item and not experiment_store._png_image(item["image"]):
                    raise _Invalid()
                if "action" in item and (item["action"] not in ("run", "jump", "duck") or not _text(item["reason"], 200, True)):
                    raise _Invalid()
    task_fingerprint = _task_fingerprint(source)
    if record["schemaVersion"] == 2:
        if record["taskInputFingerprint"] != task_fingerprint or record["id"] != "case_" + task_fingerprint[:32]:
            raise _Invalid()
    elif record["id"] != "case_" + record["inputFingerprint"][:32]:
        raise _Invalid()
    revisions = record["revisions"]
    if not isinstance(revisions, list) or len(revisions) > MAX_REVISIONS:
        raise _Invalid()
    refs = {entry["id"] for entry in _evidence(source)}
    last_draft = None
    for number, item in enumerate(revisions, 1):
        if not isinstance(item, dict) or type(item.get("revision")) is not int or item.get("revision") != number or not _text(item.get("createdAt"), 40):
            raise _Invalid()
        if item.get("action") == "draft":
            if (set(item) != {"revision", "createdAt", "action", "origin", "recommendation", "reason"}
                    or item["origin"] not in ("machine-drafted", "submitted") or not _text(item["reason"], 1000)):
                raise _Invalid()
            _recommendation(item["recommendation"], refs)
            last_draft = number
        elif item.get("action") == "approve":
            if (set(item) != {"revision", "createdAt", "action", "draftRevision", "origin", "identityVerified"}
                    or last_draft is None or item["draftRevision"] != last_draft
                    or item["origin"] != "explicit-local-action" or item["identityVerified"] is not False
                    or revisions[number - 2]["action"] != "draft"):
                raise _Invalid()
        else:
            raise _Invalid()
    return record


def _view(record):
    result = _clone(record)
    revisions = result["revisions"]
    draft = next((item for item in reversed(revisions) if item["action"] == "draft"), None)
    approved = bool(revisions and revisions[-1]["action"] == "approve")
    result.update(revision=len(revisions), latestDraft=draft,
                  taskInputFingerprint=_task_fingerprint(result["source"]),
                  latestRevisionStatus="approved" if approved else "draft" if draft else "none",
                  evidence=_evidence(result["source"]),
                  approval=({"status": "approved", "draftRevision": revisions[-1]["draftRevision"],
                             "approvedAt": revisions[-1]["createdAt"], "identityVerified": False}
                            if approved else {"status": "not_approved"}))
    return result


def _load(identifier):
    if not _id(identifier):
        return None, _error("invalid_id", "The correction case identifier is invalid.")
    try:
        value = _validate_case(_read_json(LEARNING_DIR / (identifier + ".json")))
        if value["id"] != identifier:
            raise _Invalid()
        return value, None
    except FileNotFoundError:
        return None, _error("not_found", "This correction case was not found.")
    except (OSError, ValueError, TypeError, KeyError, OverflowError, mubit_bridge._InvalidResponse, mubit_bridge._InvalidInput):
        return None, _error("unavailable", "This correction case is unreadable or invalid. Its contents are unknown and were preserved.")


def _case_rows():
    cases, unavailable = [], []
    for path in LEARNING_DIR.iterdir():
        if not path.name.startswith("case_") or not path.name.endswith(".json"):
            continue
        record, error = _load(path.stem)
        if error:
            unavailable.append({"id": path.stem if _id(path.stem) else None, "message": "An unreadable correction record was preserved."})
        else:
            view = _view(record)
            cases.append({key: view[key] for key in ("id", "sourceExperimentId", "createdAt", "revision", "split", "inputFingerprint", "taskInputFingerprint", "latestRevisionStatus")} |
                         {"comparisonConclusion": record["source"]["comparison"]["aggregate"]["conclusion"]})
    cases.sort(key=lambda item: (item["createdAt"], item["id"]), reverse=True)
    return cases, unavailable


def status():
    try:
        with _storage_lock():
            plan = _plan_view()
            cases, unavailable = _case_rows()
            return {"ok": True, "status": {"caseCount": len(cases),
                "draftCount": sum(case["latestRevisionStatus"] == "draft" for case in cases),
                "approvedCount": sum(case["latestRevisionStatus"] == "approved" for case in cases),
                "unavailableCount": len(unavailable), "provider": dict(_PROVIDER), "plan": plan,
                "trainingStarted": False, "modelUpdated": False}}
    except (OSError, ValueError, TypeError, KeyError):
        return _error("storage_unavailable", "The learning plan or records could not be read. Their contents are unknown and were not replaced.")


def list_cases():
    try:
        with _storage_lock():
            _plan()
            cases, unavailable = _case_rows()
            return {"ok": True, "cases": cases, "unavailable": unavailable, "limit": MAX_CASES}
    except (OSError, ValueError, TypeError, KeyError):
        return _error("storage_unavailable", "Correction records could not be read. Their contents are unknown.")


def get_case(identifier):
    try:
        with _storage_lock():
            _plan()
            record, error = _load(identifier)
            return error or {"ok": True, "case": _view(record)}
    except (OSError, ValueError, TypeError, KeyError):
        return _error("storage_unavailable", "This correction record could not be read. Its contents are unknown.")


def import_experiment(identifier, payload):
    if not isinstance(payload, dict) or payload or not _id(identifier):
        return _error("invalid_request", "Import accepts an experiment ID and an empty request; evidence is read from its saved record.")
    found = experiment_store.get_experiment(identifier)
    if not found.get("ok"):
        return _error("source_unavailable", "The saved source comparison is unavailable. No correction case was created.")
    experiment = found["experiment"]
    if (experiment["comparison"]["status"] != "complete" or experiment["comparison"]["comparable"] is not True
            or experiment["proposal"]["status"] != "succeeded"):
        return _error("complete_comparison_required", "Complete both baseline and candidate courses with a valid Mubit proposal before importing a review.")
    seeds = set(experiment["plan"]["seeds"].values())
    if seeds & set(PLAN["evaluationSeeds"]):
        return _error("reserved_evaluation", "This comparison uses a reserved test course. It cannot become a training correction or enter a training export.")
    source = {"input": experiment_store._proposal_payload(experiment),
              "originalAnswer": _clone(experiment["proposal"]["result"]),
              "model": experiment["proposal"]["model"], "requestId": experiment["proposal"]["requestId"],
              "comparison": _clone(experiment["comparison"]),
              "baselineConfig": _clone(experiment["plan"]["baselineConfig"]),
              "candidateConfig": _clone(experiment["runs"]["candidate-a"]["config"]),
              "experimentHash": _hash(experiment),
              "observedRuns": [_run_excerpt(experiment["runs"][slot]) for slot in
                               ("baseline-a", "baseline-b", "candidate-a", "candidate-b")]}
    fingerprint = _input_fingerprint(source["input"])
    task_fingerprint = _task_fingerprint(source)
    record = {"schemaVersion": 2, "id": "case_" + task_fingerprint[:32], "createdAt": _now(),
              "split": "training", "sourceExperimentId": identifier,
              "inputFingerprint": fingerprint, "taskInputFingerprint": task_fingerprint,
              "sourceHash": _hash(source), "source": source, "revisions": []}
    try:
        _validate_case(record)
        with _storage_lock():
            _plan()
            # Legacy IDs used only baseline input. Reuse their case only if the
            # full review task matches, without rewriting the immutable record.
            for existing_id in dict.fromkeys((record["id"], "case_" + fingerprint[:32])):
                existing_path = LEARNING_DIR / (existing_id + ".json")
                if not existing_path.exists() and not existing_path.is_symlink():
                    continue
                saved, error = _load(existing_id)
                if error:
                    return error
                if _task_fingerprint(saved["source"]) == task_fingerprint:
                    return {"ok": True, "alreadyImported": True, "case": _view(saved)}
                if existing_id == record["id"]:
                    return _error("conflict", "This case identifier already exists with different evidence.")
            if sum(p.name.startswith("case_") and p.name.endswith(".json") for p in LEARNING_DIR.iterdir()) >= MAX_CASES:
                return _error("storage_full", "The correction case limit has been reached. Existing cases were preserved.")
            _write_json(LEARNING_DIR / (record["id"] + ".json"), record, new=True)
            return {"ok": True, "case": _view(record)}
    except (ValueError, TypeError, KeyError, OverflowError, mubit_bridge._InvalidInput, mubit_bridge._InvalidResponse):
        return _error("invalid_source", "The source comparison could not form a valid correction case. Nothing was substituted.")
    except OSError:
        return _error("storage_unavailable", "The import could not be confirmed. Refresh the case list before retrying; the source comparison is unchanged.")


def _mutate(identifier, payload, kind):
    fields = {"expectedRevision", "recommendation", "reason"} if kind == "draft" else {"expectedRevision"}
    if (not isinstance(payload, dict) or set(payload) != fields
            or type(payload.get("expectedRevision")) is not int or payload["expectedRevision"] < 0):
        return _error("invalid_request", "This action requires the current saved revision and its exact fields.")
    try:
        with _storage_lock():
            _plan()
            record, error = _load(identifier)
            if error:
                return error
            revisions = record["revisions"]
            if len(revisions) != payload["expectedRevision"]:
                return _error("revision_conflict", "This case changed in another view. Reload it before saving or approving.")
            if len(revisions) >= MAX_REVISIONS:
                return _error("revision_limit", "This case has reached its revision limit. Its full history was preserved.")
            if kind == "approve":
                if not revisions or revisions[-1]["action"] != "draft":
                    return _error("draft_required", "Save a new correction draft before approving it.")
                event = {"revision": len(revisions) + 1, "createdAt": _now(), "action": "approve",
                         "draftRevision": revisions[-1]["revision"], "origin": "explicit-local-action", "identityVerified": False}
            else:
                if kind == "result-draft":
                    conclusion = record["source"]["comparison"]["aggregate"]["conclusion"]
                    rationale = ("The tested timing change did not improve score or survival on either saved course. The recorded result does not justify adopting it."
                                 if conclusion == "unchanged" else
                                 "The saved two-course comparison records a limited outcome. It does not establish the cause of failure or a generally better setting.")
                    recommendation = {"kind": "insufficient_evidence", "rationale": rationale,
                        "caveat": "This is a machine-drafted correction from the measured result. Review the original evidence before approval; two courses do not establish general improvement.",
                        "evidenceRefs": ["comparison:a", "comparison:b"]}
                    reason = "Drafted locally from the saved comparison outcome. No model or human review was invoked."
                else:
                    recommendation, reason = payload["recommendation"], payload["reason"]
                references = {entry["id"] for entry in _evidence(record["source"])}
                recommendation = _recommendation(recommendation, references)
                if not _text(reason, 1000):
                    raise _Invalid()
                event = {"revision": len(revisions) + 1, "createdAt": _now(), "action": "draft",
                         "origin": "machine-drafted" if kind == "result-draft" else "submitted",
                         "recommendation": recommendation, "reason": reason}
            record["revisions"].append(event)
            _validate_case(record)
            _write_json(LEARNING_DIR / (identifier + ".json"), record)
            return {"ok": True, "case": _view(record)}
    except (ValueError, TypeError, KeyError, OverflowError, mubit_bridge._InvalidInput, mubit_bridge._InvalidResponse):
        return _error("invalid_correction", "The correction needs a supported recommendation, bounded text and references to this case's saved evidence.")
    except OSError:
        return _error("storage_unavailable", "The correction save could not be confirmed. Reload the case before retrying; history was not intentionally discarded.")


def save_draft(identifier, payload):
    return _mutate(identifier, payload, "draft")


def create_result_draft(identifier, payload):
    return _mutate(identifier, payload, "result-draft")


def approve_correction(identifier, payload):
    return _mutate(identifier, payload, "approve")


def export_training(payload=None):
    if payload is not None and (not isinstance(payload, dict) or payload):
        return _error("invalid_request", "Training export accepts no supplied examples.")
    try:
        with _storage_lock():
            plan = _plan()
            rows, unavailable = _case_rows()
            if unavailable:
                return _error("unavailable_records", "Some correction records are unreadable. Export stopped rather than silently omitting unknown records.")
            examples = []
            for row in rows:
                record, error = _load(row["id"])
                if error:
                    return error
                view = _view(record)
                if view["latestRevisionStatus"] != "approved":
                    continue
                draft = view["latestDraft"]
                source = view["source"]
                example = {"caseId": view["id"], "split": "training", "sourceExperimentId": view["sourceExperimentId"],
                    "inputFingerprint": view["inputFingerprint"], "sourceHash": view["sourceHash"],
                    "taskInputFingerprint": view["taskInputFingerprint"],
                    "task": EXPORT_TASK,
                    "input": _review_input(source),
                    "originalAnswer": source["originalAnswer"],
                    "correctedAnswer": draft["recommendation"], "correctionReason": draft["reason"],
                    "correctionOrigin": draft["origin"], "approvedRevision": view["revision"],
                    "approval": view["approval"], "requestId": source["requestId"],
                    "provenance": {"originalGatewayTask": "Suggest one bounded timing change from baseline observations before candidate runs.",
                                   "originalGatewayInput": source["input"], "originalGatewayInputHash": _hash(source["input"]),
                                   "model": source["model"], "requestId": source["requestId"],
                                   "experimentHash": source["experimentHash"]}}
                example["exampleHash"] = _hash(example)
                examples.append(example)
            examples.sort(key=lambda item: item["caseId"])
            export = {"format": "dino-correction-dataset-v1", "providerCompatible": False,
                "task": EXPORT_TASK,
                "taskDescription": "Review a previously proposed controller change against its measured two-course outcomes and saved evidence. This task receives candidate outcomes; it is not the original pre-run timing-proposal task.",
                "createdAt": _now(), "exampleCount": len(examples), "examples": examples,
                "datasetHash": _hash(examples), "planHash": _hash(plan), "evaluationIncluded": False,
                "provider": dict(_PROVIDER),
                "message": "Generic local export for reviewing tested controller changes. It is not a verified Mubit upload format, has not been uploaded and does not prove a model update."}
            _encode(export, MAX_EXPORT_BYTES)
            return {"ok": True, "export": export}
    except (OSError, ValueError, TypeError, KeyError):
        return _error("export_unavailable", "The approved training export could not be prepared within its limits. Saved evidence was preserved.")


def create_planned_experiment(identifier, payload):
    if not isinstance(identifier, str) or identifier not in _PLANNED or not isinstance(payload, dict) or payload:
        return _error("invalid_request", "Choose one reserved scenario and send an empty request.")
    try:
        with _storage_lock():
            _plan()
            links = _links()
            if identifier in links:
                link = links[identifier]
                if link["status"] != "ready":
                    return _error("creation_outcome_unknown", "This scenario already has a creation attempt whose outcome is unknown. It will not create another comparison automatically.")
                result = experiment_store.get_experiment(link["experimentId"])
                if not result.get("ok"):
                    return _error("source_unavailable", "The scenario's linked comparison is unavailable. Its link was preserved.")
                if result["experiment"]["plan"]["seeds"] != _PLANNED[identifier]["seeds"]:
                    return _error("source_unavailable", "The linked comparison does not match the reserved scenario. Its link was preserved.")
                return {"ok": True, "experiment": result["experiment"], "plannedCaseId": identifier,
                        "split": _PLANNED[identifier]["split"], "alreadyCreated": True}
            links[identifier] = {"status": "creating", "experimentId": None, "createdAt": _now()}
            _save_links(links)
        # The durable intent prevents concurrent clicks or an interrupted write
        # from creating duplicate experiments. Creation itself makes no model call.
        try:
            result = experiment_store.create_experiment({"seeds": _PLANNED[identifier]["seeds"]})
        except Exception:
            result = {"ok": False}
        with _storage_lock():
            links = _links()
            link = links[identifier]
            if result.get("ok"):
                experiment = result["experiment"]
                link.update(status="ready", experimentId=experiment["id"])
                _save_links(links)
                return {"ok": True, "experiment": experiment, "plannedCaseId": identifier, "split": _PLANNED[identifier]["split"]}
            link["status"] = "outcome_unknown"
            _save_links(links)
            return _error("creation_outcome_unknown", "The comparison creation could not be confirmed. Check the saved comparisons; this scenario will not create another automatically.")
    except (OSError, ValueError, TypeError, KeyError):
        return _error("storage_unavailable", "The scenario preparation could not be confirmed. Its records were preserved and no model request was made.")
