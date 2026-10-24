"""Offline custody checks: temporary files, mocked provider, no credentials."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import unittest
from unittest.mock import patch

import experiment_store as experiments
import learning_store as store
from test_experiment_store import png


class LearningStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(patch.stopall)
        self.directory = Path(self.temp.name) / "learning"
        patch.object(store, "LEARNING_DIR", self.directory).start()
        patch.object(experiments, "EXPERIMENTS_DIR", Path(self.temp.name) / "experiments").start()
        self.provider = patch.object(experiments.mubit_bridge, "propose", return_value={
            "ok": True, "parameter": "duckExtraMs", "value": 80,
            "rationale": "Test an extended duck hold.", "caveat": "The screenshots may not establish the cause.",
            "requestId": "mreq_learning_fixture", "model": "fixture-model", "latencyMs": 10,
            "usage": {"promptTokens": 1, "completionTokens": 2, "totalTokens": 3}, "callsRemaining": 19}).start()
        self.connection = patch.object(experiments.mubit_bridge, "connection_status", return_value={"model": "fixture-model"}).start()
        self.network = patch.object(experiments.mubit_bridge, "_open_request", side_effect=AssertionError("No network")).start()
        self.keychain = patch.object(experiments.mubit_bridge, "_keychain_key", side_effect=AssertionError("No credentials")).start()
        self.experiment = self.completed_experiment()
        self.provider.reset_mock()
        self.connection.reset_mock()

    def completed_experiment(self):
        experiment = experiments.create_experiment({})["experiment"]
        identifier = experiment["id"]
        for slot in ("baseline-a", "repeat-a", "baseline-b"):
            result = experiments.save_run(identifier, self.run_record(slot))
            self.assertTrue(result["ok"], result)
        self.assertTrue(experiments.propose_change(identifier, {})["ok"])
        for slot in ("candidate-a", "candidate-b"):
            result = experiments.save_run(identifier, self.run_record(slot))
            self.assertTrue(result["ok"], result)
        self.assertTrue(result["experiment"]["comparison"]["comparable"])
        return result["experiment"]

    def run_record(self, slot):
        start = datetime(2026, 10, 24, tzinfo=timezone.utc)
        config = {**experiments.BASELINE_CONFIG}
        if slot.startswith("candidate"):
            config["duckExtraMs"] = 80
        return {"slot": slot, "runId": "run_" + slot, "protocol": "native60-v1",
            "seed": experiments.SLOTS[slot]["seed"], "maxTicks": 3600, "config": config,
            "startedAt": start.isoformat(), "endedAt": (start + timedelta(seconds=10)).isoformat(),
            "wallDurationMs": 10000, "ticks": 600, "score": 60, "outcome": "collision",
            "canvasWidth": 600, "maxFrameGapMs": 20,
            "actions": [{"tick": 100, "tMs": 1667, "action": "jump", "score": 10, "reason": "Visible obstacle."}],
            "frames": [{"tick": 600, "tMs": 10000, "score": 60, "image": png()}],
            "course": [{"index": 0, "tick": 100, "type": "CACTUS_SMALL", "size": 1,
                        "y": 105, "width": 17, "gap": 150, "speedOffset": 0}]}

    def imported(self):
        result = store.import_experiment(self.experiment["id"], {})
        self.assertTrue(result["ok"], result)
        return result["case"]

    def draft(self, case=None):
        case = case or self.imported()
        result = store.create_result_draft(case["id"], {"expectedRevision": case["revision"]})
        self.assertTrue(result["ok"], result)
        return result["case"]

    def submitted(self):
        return {"expectedRevision": 0, "recommendation": {
            "kind": "no_change", "rationale": "No gain was measured on these two courses.",
            "caveat": "The cause remains uncertain.", "evidenceRefs": ["comparison:a", "comparison:b"]},
            "reason": "The observed result does not support the proposed timing change."}

    def assert_no_model_activity(self):
        self.provider.assert_not_called()
        self.connection.assert_not_called()
        self.network.assert_not_called()
        self.keychain.assert_not_called()

    def test_locked_plan_has_disjoint_pairs_and_no_collected_or_trained_claim(self):
        result = store.status()
        self.assertTrue(result["ok"])
        status = result["status"]
        plan = status["plan"]
        self.assertEqual(len(plan["trainingCases"]), 40)
        self.assertEqual(len(plan["evaluationCases"]), 10)
        self.assertEqual(len(set(plan["trainingSeeds"])), 80)
        self.assertEqual(len(set(plan["evaluationSeeds"])), 20)
        self.assertFalse(set(plan["trainingSeeds"]) & set(plan["evaluationSeeds"]))
        self.assertEqual(plan["trainingCases"][0]["seeds"], {"a": 104729, "b": 130363})
        self.assertEqual(plan["trainingCases"][0]["status"], "not_started")
        self.assertEqual(status["caseCount"], 0)
        self.assertFalse(status["modelUpdated"])
        self.assertEqual(status["provider"]["status"], "unverified_contract")
        self.assertIsNone(status["provider"]["adapterId"])
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        self.assert_no_model_activity()

    def test_import_preserves_original_and_model_input_excludes_audit_and_repeat(self):
        source_path = experiments.EXPERIMENTS_DIR / (self.experiment["id"] + ".json")
        original_bytes = source_path.read_bytes()
        case = self.imported()
        self.assertEqual(case["source"]["originalAnswer"], self.experiment["proposal"]["result"])
        self.assertEqual(case["source"]["requestId"], "mreq_learning_fixture")
        self.assertEqual(case["source"]["candidateConfig"]["duckExtraMs"], 80)
        payload = case["source"]["input"]
        self.assertEqual([c["slot"] for c in payload["courses"]], ["baseline-a", "baseline-b"])
        self.assertNotIn("CACTUS", json.dumps(payload))
        self.assertNotIn("speedOffset", json.dumps(case))
        self.assertNotIn("repeat-a", json.dumps(case))
        self.assertEqual(source_path.read_bytes(), original_bytes)
        self.assertEqual(case["revision"], 0)
        self.assertIsNone(case["latestDraft"])
        self.assertEqual(case["approval"]["status"], "not_approved")
        self.assertEqual(stat.S_IMODE((self.directory / (case["id"] + ".json")).stat().st_mode), 0o600)
        self.assert_no_model_activity()

    def test_import_requires_authoritative_complete_comparable_succeeded_source(self):
        for path, value in ((["comparison", "status"], "awaiting_candidates"),
                            (["comparison", "comparable"], False), (["proposal", "status"], "failed")):
            altered = deepcopy(self.experiment)
            altered[path[0]][path[1]] = value
            with patch.object(experiments, "get_experiment", return_value={"ok": True, "experiment": altered}):
                self.assertEqual(store.import_experiment(altered["id"], {})["code"], "complete_comparison_required")
        self.assertEqual(store.import_experiment(self.experiment["id"], {"result": {}})["code"], "invalid_request")
        self.assertEqual(store.import_experiment("missing", {})["code"], "source_unavailable")
        self.assert_no_model_activity()

    def test_identical_full_review_task_is_one_case_despite_new_experiment_and_run_ids(self):
        original = self.imported()
        copied = deepcopy(self.experiment)
        copied["id"] = "exp_copy"
        for run in copied["runs"].values():
            run["runId"] += "_copy"
        with patch.object(experiments, "get_experiment", return_value={"ok": True, "experiment": copied}):
            duplicate = store.import_experiment("exp_copy", {})
        self.assertTrue(duplicate["alreadyImported"])
        self.assertEqual(duplicate["case"]["id"], original["id"])
        self.assertEqual(duplicate["case"]["sourceExperimentId"], self.experiment["id"])
        self.assertEqual(len(store.list_cases()["cases"]), 1)

    def test_same_baseline_with_different_proposal_or_outcome_is_a_distinct_case(self):
        original = self.imported()
        for difference in ("proposal", "outcome"):
            copied = deepcopy(self.experiment)
            copied["id"] = "exp_different_" + difference
            if difference == "proposal":
                copied["proposal"]["result"]["value"] = 100
                for slot in ("candidate-a", "candidate-b"):
                    copied["runs"][slot]["config"]["duckExtraMs"] = 100
            else:
                run = copied["runs"]["candidate-a"]
                run.update(score=61, ticks=610)
                run["frames"][-1].update(tick=610, tMs=10167, score=61)
                copied["comparison"] = experiments._comparison(copied)
            with patch.object(experiments, "get_experiment", return_value={"ok": True, "experiment": copied}):
                result = store.import_experiment(copied["id"], {})
                self.assertTrue(result["ok"], result)
                self.assertNotEqual(result["case"]["id"], original["id"])
                self.assertEqual(result["case"]["inputFingerprint"], original["inputFingerprint"])
                self.assertNotEqual(result["case"]["taskInputFingerprint"], original["taskInputFingerprint"])
                repeated = store.import_experiment(copied["id"], {})
                self.assertTrue(repeated["alreadyImported"])
                self.assertEqual(repeated["case"]["id"], result["case"]["id"])
        self.assertEqual(len(store.list_cases()["cases"]), 3)
        self.assert_no_model_activity()

    def test_legacy_baseline_case_stays_readable_immutable_and_idempotent(self):
        case = self.draft()
        original_path = self.directory / (case["id"] + ".json")
        record = json.loads(original_path.read_bytes())
        record["schemaVersion"] = 1
        record["id"] = "case_" + record["inputFingerprint"][:32]
        del record["taskInputFingerprint"]
        legacy_path = self.directory / (record["id"] + ".json")
        original_path.unlink()
        legacy_path.write_text(json.dumps(record))
        legacy_bytes = legacy_path.read_bytes()
        loaded = store.get_case(record["id"])["case"]
        self.assertEqual(loaded["revision"], 1)
        self.assertEqual(loaded["taskInputFingerprint"], case["taskInputFingerprint"])
        repeated = store.import_experiment(self.experiment["id"], {})
        self.assertTrue(repeated["alreadyImported"])
        self.assertEqual(repeated["case"]["id"], record["id"])
        self.assertEqual(repeated["case"]["revision"], 1)
        copied = deepcopy(self.experiment)
        copied["id"] = "exp_new_proposal"
        copied["proposal"]["result"]["rationale"] = "A different proposal rationale."
        with patch.object(experiments, "get_experiment", return_value={"ok": True, "experiment": copied}):
            result = store.import_experiment(copied["id"], {})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["case"]["schemaVersion"], 2)
        self.assertNotEqual(result["case"]["id"], record["id"])
        self.assertEqual(len(store.list_cases()["cases"]), 2)
        self.assertEqual(legacy_path.read_bytes(), legacy_bytes)
        self.assert_no_model_activity()

    def test_reserved_and_mixed_evaluation_inputs_cannot_be_imported(self):
        heldout = store.PLAN["evaluationCases"][0]["seeds"]
        for seeds in (heldout, {"a": heldout["a"], "b": 130363}):
            altered = deepcopy(self.experiment)
            altered["plan"]["seeds"] = seeds
            with patch.object(experiments, "get_experiment", return_value={"ok": True, "experiment": altered}):
                result = store.import_experiment(altered["id"], {})
            self.assertEqual(result["code"], "reserved_evaluation")
            self.assertTrue(store.is_evaluation_seeds(seeds))
        self.assertFalse(store.is_evaluation_seeds({"a": 104729, "b": 130363}))
        self.assertFalse(store.is_evaluation_seeds(None))
        self.assert_no_model_activity()

    def test_machine_draft_is_source_linked_not_approved_or_exported(self):
        case = self.draft()
        self.assertEqual(case["revision"], 1)
        self.assertEqual(case["latestRevisionStatus"], "draft")
        self.assertEqual(case["latestDraft"]["origin"], "machine-drafted")
        self.assertEqual(case["latestDraft"]["recommendation"]["kind"], "insufficient_evidence")
        self.assertIn("did not improve", case["latestDraft"]["recommendation"]["rationale"])
        self.assertEqual(store.export_training({})["export"]["exampleCount"], 0)
        self.assertEqual(store.status()["status"]["draftCount"], 1)
        self.assertEqual(store.status()["status"]["approvedCount"], 0)
        self.assert_no_model_activity()

    def test_draft_revisions_append_and_stale_updates_do_not_overwrite(self):
        case = self.imported()
        first = store.save_draft(case["id"], self.submitted())["case"]
        self.assertEqual(first["latestDraft"]["origin"], "submitted")
        self.assertEqual(store.save_draft(case["id"], self.submitted())["code"], "revision_conflict")
        second = store.save_draft(case["id"], {**self.submitted(), "expectedRevision": 1})["case"]
        self.assertEqual(second["revisions"][0], first["revisions"][0])
        self.assertEqual(second["revision"], 2)
        self.assertEqual(second["source"], case["source"])
        self.assert_no_model_activity()

    def test_explicit_approval_exports_only_latest_approved_source_with_hashes(self):
        case = self.draft()
        approved = store.approve_correction(case["id"], {"expectedRevision": 1})["case"]
        self.assertEqual(approved["revision"], 2)
        self.assertEqual(approved["approval"]["draftRevision"], 1)
        self.assertFalse(approved["approval"]["identityVerified"])
        self.assertEqual(approved["revisions"][-1]["origin"], "explicit-local-action")
        export = store.export_training({})["export"]
        self.assertEqual(export["exampleCount"], 1)
        self.assertFalse(export["providerCompatible"])
        self.assertFalse(export["evaluationIncluded"])
        item = export["examples"][0]
        self.assertEqual(item["task"], "review-tested-controller-change-v1")
        self.assertEqual(export["task"], item["task"])
        self.assertEqual(item["input"]["originalModelInput"], case["source"]["input"])
        self.assertEqual(item["correctedAnswer"], case["latestDraft"]["recommendation"])
        self.assertEqual(item["originalAnswer"], case["source"]["originalAnswer"])
        self.assertEqual(item["exampleHash"], store._hash({k: v for k, v in item.items() if k != "exampleHash"}))
        self.assertEqual(export["datasetHash"], store._hash(export["examples"]))
        self.assertEqual(store.status()["status"]["approvedCount"], 1)
        self.assertFalse(store.status()["status"]["provider"]["trained"])
        self.assert_no_model_activity()

    def test_export_task_includes_candidate_outcomes_and_every_supported_evidence_reference(self):
        case = self.imported()
        references = [entry["id"] for entry in case["evidence"]]
        self.assertLessEqual(len(references), 12)
        request = self.submitted()
        request["recommendation"]["evidenceRefs"] = references
        store.save_draft(case["id"], request)
        store.approve_correction(case["id"], {"expectedRevision": 1})
        item = store.export_training()["export"]["examples"][0]
        self.assertEqual(item["input"]["comparison"], case["source"]["comparison"])
        self.assertEqual(item["input"]["candidateConfig"], case["source"]["candidateConfig"])
        self.assertEqual([run["slot"] for run in item["input"]["observedRuns"]],
                         ["baseline-a", "baseline-b", "candidate-a", "candidate-b"])
        self.assertEqual({entry["id"] for entry in item["input"]["evidence"]}, set(references))
        self.assertEqual(item["input"]["evidence"], case["evidence"])
        self.assertEqual(item["input"]["originalAnswer"], case["source"]["originalAnswer"])
        self.assertEqual(item["provenance"]["originalGatewayInput"], case["source"]["input"])
        self.assertIn("before candidate runs", item["provenance"]["originalGatewayTask"])
        self.assertEqual(item["provenance"]["originalGatewayInputHash"], store._hash(case["source"]["input"]))
        self.assertNotIn("speedOffset", json.dumps(item))
        self.assertNotIn("repeat-a", json.dumps(item))
        self.assert_no_model_activity()

    def test_new_draft_withdraws_export_eligibility_without_erasing_approval_history(self):
        case = self.draft()
        store.approve_correction(case["id"], {"expectedRevision": 1})
        updated = store.save_draft(case["id"], {**self.submitted(), "expectedRevision": 2})["case"]
        self.assertEqual(updated["latestRevisionStatus"], "draft")
        self.assertEqual(updated["revisions"][1]["action"], "approve")
        self.assertEqual(store.export_training()["export"]["exampleCount"], 0)

    def test_approval_needs_current_draft_and_cannot_be_repeated(self):
        case = self.imported()
        self.assertEqual(store.approve_correction(case["id"], {"expectedRevision": 0})["code"], "draft_required")
        self.draft(case)
        self.assertEqual(store.approve_correction(case["id"], {"expectedRevision": 0})["code"], "revision_conflict")
        store.approve_correction(case["id"], {"expectedRevision": 1})
        self.assertEqual(store.approve_correction(case["id"], {"expectedRevision": 2})["code"], "draft_required")

    def test_invalid_corrections_and_secret_shaped_text_do_not_persist(self):
        case = self.imported()
        good = self.submitted()
        variations = [
            {"kind": "execute"}, {"parameter": "jumpLeadMs"}, {"rationale": "mbt_secret"},
            {"rationale": "x" * 601}, {"caveat": ""}, {"evidenceRefs": []},
            {"evidenceRefs": ["invented:reference"]}, {"evidenceRefs": ["comparison:a", "comparison:a"]},
            {"kind": "timing_change", "parameter": "duckExtraMs", "value": 999},
            {"kind": "timing_change", "parameter": "duckExtraMs", "value": 0},
            {"kind": "timing_change", "parameter": "duckExtraMs", "value": 85},
            {"kind": "timing_change", "parameter": "duckExtraMs", "value": True},
        ]
        for changes in variations:
            with self.subTest(changes=changes):
                result = store.save_draft(case["id"], {**good, "recommendation": {**good["recommendation"], **changes}})
                self.assertEqual(result["code"], "invalid_correction")
        self.assertEqual(store.save_draft(case["id"], {**good, "reason": "api_key=secret"})["code"], "invalid_correction")
        self.assertEqual(store.save_draft(case["id"], {**good, "expectedRevision": False})["code"], "invalid_request")
        self.assertEqual(store.get_case(case["id"])["case"]["revision"], 0)

    def test_valid_bounded_timing_correction_is_draft(self):
        case = self.imported()
        request = self.submitted()
        request["recommendation"].update(kind="timing_change", parameter="jumpLeadMs", value=190)
        result = store.save_draft(case["id"], request)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["case"]["latestRevisionStatus"], "draft")

    def test_corrupt_record_is_preserved_and_export_does_not_silently_omit_it(self):
        case = self.imported()
        path = self.directory / (case["id"] + ".json")
        path.write_text('{"broken":true}')
        raw = path.read_bytes()
        self.assertEqual(store.get_case(case["id"])["code"], "unavailable")
        self.assertEqual(store.import_experiment(self.experiment["id"], {})["code"], "unavailable")
        self.assertEqual(store.export_training()["code"], "unavailable_records")
        self.assertEqual(store.list_cases()["unavailable"][0]["id"], case["id"])
        self.assertEqual(path.read_bytes(), raw)

    def test_changed_source_or_split_invalidates_case(self):
        case = self.imported()
        path = self.directory / (case["id"] + ".json")
        original = path.read_bytes()
        for changes in ({"split": "evaluation"}, {"sourceHash": "0" * 64}, {"inputFingerprint": "0" * 64}, {"taskInputFingerprint": "0" * 64}):
            data = json.loads(original)
            data.update(changes)
            path.write_text(json.dumps(data))
            self.assertEqual(store.get_case(case["id"])["code"], "unavailable")
        path.write_bytes(original)

    def test_changed_or_missing_locked_plan_is_unknown_not_replaced(self):
        case = self.imported()
        path = self.directory / "plan.json"
        original = path.read_bytes()
        altered = json.loads(original)
        altered["evaluationSeeds"][0] = 104729
        path.write_text(json.dumps(altered))
        raw = path.read_bytes()
        self.assertEqual(store.status()["code"], "storage_unavailable")
        self.assertEqual(path.read_bytes(), raw)
        path.unlink()
        self.assertEqual(store.get_case(case["id"])["code"], "storage_unavailable")
        self.assertFalse(path.exists())

    def test_symlinks_and_nonregular_records_are_not_followed(self):
        case = self.imported()
        path = self.directory / (case["id"] + ".json")
        outside = Path(self.temp.name) / "outside.json"
        outside.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(outside)
        self.assertEqual(store.get_case(case["id"])["code"], "unavailable")
        path.unlink()
        os.mkfifo(path)
        self.assertEqual(store.get_case(case["id"])["code"], "unavailable")
        alternate = Path(self.temp.name) / "linked"
        alternate.symlink_to(self.directory, target_is_directory=True)
        with patch.object(store, "LEARNING_DIR", alternate):
            self.assertEqual(store.status()["code"], "storage_unavailable")

    def test_revision_limit_preserves_history(self):
        case = self.draft()
        with patch.object(store, "MAX_REVISIONS", 1):
            self.assertEqual(store.approve_correction(case["id"], {"expectedRevision": 1})["code"], "revision_limit")
        self.assertEqual(store.get_case(case["id"])["case"]["revision"], 1)

    def test_planned_creation_is_idempotent_and_never_calls_model(self):
        first = store.create_planned_experiment("train-02", {})
        self.assertTrue(first["ok"], first)
        self.assertEqual(first["experiment"]["plan"]["seeds"], store.PLAN["trainingCases"][1]["seeds"])
        second = store.create_planned_experiment("train-02", {})
        self.assertTrue(second["alreadyCreated"])
        self.assertEqual(first["experiment"]["id"], second["experiment"]["id"])
        scenario = store.status()["status"]["plan"]["trainingCases"][1]
        self.assertEqual(scenario["status"], "ready")
        self.assertEqual(scenario["experimentId"], first["experiment"]["id"])
        self.assert_no_model_activity()

    def test_missing_scenario_associations_do_not_allow_duplicate_creation(self):
        prepared = store.create_planned_experiment("train-02", {})
        self.assertTrue(prepared["ok"])
        path = self.directory / "plan-links.json"
        path.unlink()
        with patch.object(experiments, "create_experiment") as create:
            self.assertEqual(store.status()["code"], "storage_unavailable")
            self.assertEqual(store.create_planned_experiment("train-02", {})["code"], "storage_unavailable")
            create.assert_not_called()
        self.assertFalse(path.exists())

    def test_planned_test_creation_marks_reserved_split_without_inference(self):
        result = store.create_planned_experiment("test-01", {})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["split"], "evaluation")
        self.assertTrue(store.is_evaluation_seeds(result["experiment"]["plan"]["seeds"]))
        self.assert_no_model_activity()

    def test_unknown_creation_cannot_repeat_local_experiment_creation(self):
        with patch.object(experiments, "create_experiment", return_value={"ok": False}) as create:
            self.assertEqual(store.create_planned_experiment("train-02", {})["code"], "creation_outcome_unknown")
            self.assertEqual(store.create_planned_experiment("train-02", {})["code"], "creation_outcome_unknown")
            create.assert_called_once()
        self.assertEqual(store.status()["status"]["plan"]["trainingCases"][1]["status"], "outcome_unknown")

    def test_concurrent_planned_creation_is_one_attempt(self):
        started, release = threading.Event(), threading.Event()
        def hold(_):
            started.set()
            self.assertTrue(release.wait(3))
            return {"ok": False}
        results = []
        with patch.object(experiments, "create_experiment", side_effect=hold) as create:
            thread = threading.Thread(target=lambda: results.append(store.create_planned_experiment("train-02", {})))
            thread.start()
            try:
                self.assertTrue(started.wait(3))
                self.assertEqual(store.create_planned_experiment("train-02", {})["code"], "creation_outcome_unknown")
                create.assert_called_once()
            finally:
                release.set()
                thread.join(3)
        self.assertFalse(thread.is_alive())

    def test_bad_identifiers_and_payloads_cannot_escape_or_supply_records(self):
        for identifier in ("../outside", "", "mbt_secret", None):
            self.assertEqual(store.get_case(identifier)["code"], "invalid_id")
        for identifier in ("unknown", [], None, {}):
            self.assertEqual(store.create_planned_experiment(identifier, {})["code"], "invalid_request")
        self.assertEqual(store.create_planned_experiment("train-02", {"seeds": {}})["code"], "invalid_request")
        self.assertEqual(store.export_training({"examples": []})["code"], "invalid_request")


if __name__ == "__main__":
    unittest.main()
