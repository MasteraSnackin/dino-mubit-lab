"""Offline compact-review custody checks; no provider, credentials or live data."""
from copy import deepcopy
import json
from pathlib import Path
import stat
import tempfile
import threading
import unittest
from unittest.mock import patch

import review_task_store as store


def approved_case():
    baseline = dict(store.mubit_bridge.EXPERIMENT_BASELINE_CONFIG)
    candidate = {**baseline, "duckExtraMs": 80}
    source = {
        "baselineConfig": baseline, "candidateConfig": candidate,
        "originalAnswer": {"parameter": "duckExtraMs", "value": 80,
                           "rationale": "Test a longer duck hold.", "caveat": "This is only a hypothesis."},
        "comparison": {"status": "complete", "comparable": True,
                       "aggregate": {"conclusion": "unchanged"}},
        "input": {"largeOriginalRequest": "MUST-NOT-BE-SENT"},
        "model": "source-model", "requestId": "source_request_id", "experimentHash": "e" * 64,
        "observedRuns": [{"slot": prefix + "-" + name, "runId": "run_" + prefix + name,
                          "seed": seed, "config": baseline if prefix == "baseline" else candidate,
                          "score": 60, "ticks": 600, "outcome": "collision",
                          "actions": [{"action": "HIDDEN-ACTION"}],
                          "frames": [{"image": "HIDDEN-IMAGE"}], "obstacle": "HIDDEN-COORDINATES"}
                         for prefix in ("baseline", "candidate")
                         for name, seed in (("a", 104729), ("b", 130363))]}
    return {"id": "case_fixture", "revision": 2, "sourceHash": "a" * 64,
            "sourceExperimentId": "experiment_fixture", "source": source,
            "latestDraft": {"revision": 1, "recommendation": {
                "kind": "no_change", "rationale": "PRIVATE APPROVED TARGET",
                "caveat": "PRIVATE TARGET CAVEAT", "evidenceRefs": ["comparison:a", "comparison:b"]},
                "reason": "PRIVATE CORRECTION REASON"},
            "approval": {"status": "approved", "draftRevision": 1,
                         "approvedAt": "2026-10-24T00:00:00+00:00", "identityVerified": False}}


def success(assessment="unchanged"):
    return {"ok": True, "assessment": assessment, "decision": "retain_baseline",
            "rationale": "The saved survival ticks are unchanged on both courses.",
            "caveat": "Two courses do not establish general performance.",
            "model": "fixture-model", "requestId": "mreq_review_fixture", "latencyMs": 123,
            "usage": {"promptTokens": 250, "completionTokens": 40, "totalTokens": 290}, "callsRemaining": 19}


class ReviewTaskStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(patch.stopall)
        self.directory = Path(self.temp.name) / "review_tasks"
        patch.object(store, "REVIEW_TASKS_DIR", self.directory).start()
        self.case = approved_case()
        self.source_patch = patch.object(store.learning_store, "get_case",
                                         side_effect=lambda _: {"ok": True, "case": deepcopy(self.case)})
        self.source = self.source_patch.start()
        self.connection = patch.object(store.mubit_bridge, "connection_status", return_value={
            "configured": True, "verified": True, "ready": True, "model": "fixture-model", "callsRemaining": 20}).start()
        self.provider = patch.object(store.mubit_bridge, "review_tested_change", return_value=success(), create=True).start()
        self.network = patch.object(store.mubit_bridge, "_open_request", side_effect=AssertionError("No network")).start()
        self.keychain = patch.object(store.mubit_bridge, "_keychain_key", side_effect=AssertionError("No credentials")).start()

    def request(self):
        return store.request_review(self.case["id"], {"expectedRevision": self.case["revision"]})

    def receipt_path(self):
        return next(path for path in self.directory.glob("review_*.json"))

    def change_input(self):
        self.case["source"]["originalAnswer"]["rationale"] = "Test the same hold on another observed scenario."

    def assert_no_external(self):
        self.network.assert_not_called()
        self.keychain.assert_not_called()

    def test_preview_is_exact_text_only_and_does_not_start_or_bind_task(self):
        result = store.get_case_review(self.case["id"])
        self.assertTrue(result["ok"], result)
        task = result["task"]
        self.assertTrue(task["requestAllowed"])
        self.assertTrue(task["connectionVerified"])
        self.assertIsNone(task["attempt"])
        self.assertEqual(task["expectedAssessment"], "unchanged")
        self.assertEqual(task["model"], "fixture-model")
        self.assertEqual(set(task["input"]), {"source", "runId", "taskVersion", "protocol", "frameRate", "maxTicks",
                                            "baselineConfig", "candidateConfig", "originalProposal", "courses"})
        raw = json.dumps(task["input"])
        for forbidden in ("PRIVATE", "HIDDEN", "MUST-NOT-BE-SENT", "expectedAssessment", "aggregate", "case_fixture", "experiment_fixture", "run_baseline"):
            self.assertNotIn(forbidden, raw)
        self.assertFalse((self.directory / "task.json").exists())
        self.assertFalse(list(self.directory.glob("review_*.json")))
        self.provider.assert_not_called()
        self.assert_no_external()

    def test_success_retains_custody_and_safe_receipt_but_sends_no_target(self):
        before = deepcopy(self.case)
        result = self.request()
        self.assertTrue(result["ok"], result)
        task, attempt = result["task"], result["task"]["attempt"]
        self.assertEqual(attempt["status"], "succeeded")
        self.assertEqual(attempt["result"]["assessment"], "unchanged")
        self.assertTrue(attempt["assessmentMatchesMeasurements"])
        self.assertEqual(attempt["sourceCaseId"], self.case["id"])
        self.assertEqual(attempt["sourceCaseRevision"], 2)
        self.assertEqual(attempt["sourceHash"], self.case["sourceHash"])
        self.assertEqual(attempt["approvedTarget"]["recommendation"], self.case["latestDraft"]["recommendation"])
        self.assertEqual(attempt["approvedTarget"]["reason"], self.case["latestDraft"]["reason"])
        self.assertFalse(attempt["approvedCorrectionSent"])
        self.assertFalse(attempt["modelUpdateVerified"])
        self.assertFalse(task["requestAllowed"])
        self.assertEqual(attempt["requestId"], "mreq_review_fixture")
        self.assertEqual(attempt["latencyMs"], 123)
        self.assertEqual(attempt["usage"], success()["usage"])
        self.provider.assert_called_once_with(task["input"], expected_model="fixture-model")
        self.assertNotIn("PRIVATE", json.dumps(self.provider.call_args))
        self.assertEqual(self.case, before)
        self.assertEqual(json.loads(self.receipt_path().read_text()), attempt)
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        for path in self.directory.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assert_no_external()

    def test_pending_receipt_precedes_provider_and_blocks_simultaneous_request(self):
        entered, release = threading.Event(), threading.Event()
        output = []

        def provider(payload, *, expected_model):
            saved = json.loads(self.receipt_path().read_text())
            self.assertEqual(saved["status"], "pending")
            self.assertEqual(saved["model"], expected_model)
            entered.set()
            if not release.wait(5):
                raise RuntimeError("Test release missing")
            return success()

        self.provider.side_effect = provider
        worker = threading.Thread(target=lambda: output.append(self.request()))
        worker.start()
        try:
            self.assertTrue(entered.wait(5))
            second = self.request()
            self.assertTrue(second["alreadyRequested"])
            self.assertEqual(second["task"]["attempt"]["status"], "pending")
            preview = store.get_case_review(self.case["id"])
            self.assertEqual(preview["task"]["attempt"]["status"], "pending")
            self.assertFalse(preview["task"]["requestAllowed"])
        finally:
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(output[0]["task"]["attempt"]["status"], "succeeded")
        self.provider.assert_called_once()

    def test_identical_input_deduplicates_across_case_ids_and_correction_revisions(self):
        first = self.request()["task"]["attempt"]
        self.case["id"] = "case_other"
        self.case["sourceExperimentId"] = "experiment_other"
        self.case["sourceHash"] = "b" * 64
        self.case["revision"] = 4
        self.case["approval"]["draftRevision"] = 3
        self.case["latestDraft"]["reason"] = "A different private reason."
        for run in self.case["source"]["observedRuns"]:
            run["runId"] += "_other"
        second = self.request()
        self.assertTrue(second["ok"], second)
        self.assertTrue(second["alreadyRequested"])
        self.assertEqual(second["task"]["attempt"], first)
        self.assertEqual(second["task"]["caseId"], "case_other")
        self.assertEqual(second["task"]["caseRevision"], 4)
        self.assertEqual(second["task"]["attempt"]["sourceCaseRevision"], 2)
        self.provider.assert_called_once()
        altered = deepcopy(first["input"])
        altered["runId"] = "review_" + "1" * 32
        self.assertEqual(store._input_hash(altered), first["inputHash"])

    def test_failed_and_interrupted_requests_are_never_retried(self):
        self.provider.side_effect = TimeoutError("Untrusted provider failure")
        first = self.request()
        self.assertTrue(first["ok"], first)
        self.assertEqual(first["task"]["attempt"]["status"], "failed")
        self.assertNotIn("Untrusted provider failure", json.dumps(first))
        self.assertTrue(self.request()["alreadyRequested"])
        self.provider.assert_called_once()

    def test_completion_write_failure_retains_pending_and_prevents_resend(self):
        original = store._write_record

        def fail_completion(record, *, new=False):
            if not new:
                raise OSError("Simulated disk failure")
            return original(record, new=new)

        with patch.object(store, "_write_record", side_effect=fail_completion):
            result = self.request()
        self.assertFalse(result["ok"])
        self.assertEqual(json.loads(self.receipt_path().read_text())["status"], "pending")
        self.assertTrue(self.request()["alreadyRequested"])
        self.provider.assert_called_once()

    def test_a_wrong_assessment_remains_a_successful_measurable_answer(self):
        self.provider.return_value = success("improved_on_both")
        attempt = self.request()["task"]["attempt"]
        self.assertEqual(attempt["status"], "succeeded")
        self.assertFalse(attempt["assessmentMatchesMeasurements"])
        self.assertEqual(attempt["result"]["assessment"], "improved_on_both")
        self.assertEqual(attempt["expectedAssessment"], "unchanged")

    def test_unknown_or_extra_provider_fields_do_not_enter_receipt(self):
        self.provider.return_value = {**success(), "rawResponse": "PRIVATE-REMOTE-TEXT", "apiKey": "PRIVATE-REMOTE-KEY"}
        result = self.request()
        self.assertNotIn("PRIVATE-REMOTE", json.dumps(result))
        self.assertEqual(result["task"]["attempt"]["status"], "succeeded")

    def test_invalid_answer_is_durable_failure_and_partial_usage_is_retained(self):
        self.provider.return_value = {**success(), "assessment": "unrecognised",
                                     "usage": {"promptTokens": 250, "completionTokens": None, "totalTokens": None}}
        first = self.request()
        attempt = first["task"]["attempt"]
        self.assertEqual(attempt["status"], "failed")
        self.assertIsNone(attempt["result"])
        self.assertEqual(attempt["usage"]["promptTokens"], 250)
        self.assertTrue(self.request()["alreadyRequested"])
        self.provider.assert_called_once()

    def test_stale_revision_and_unapproved_case_do_not_infer(self):
        stale = store.request_review(self.case["id"], {"expectedRevision": 1})
        self.assertEqual(stale["code"], "revision_conflict")
        self.case["approval"] = {"status": "not_approved"}
        self.case["latestDraft"] = None
        preview = store.get_case_review(self.case["id"])
        self.assertTrue(preview["ok"], preview)
        self.assertFalse(preview["task"]["requestAllowed"])
        self.assertEqual(self.request()["code"], "approval_required")
        self.provider.assert_not_called()

    def test_read_failure_preserves_unknown_and_does_not_replace_source(self):
        self.source.return_value = {"ok": False}
        self.source.side_effect = None
        self.assertEqual(store.get_case_review(self.case["id"])["code"], "source_unavailable")
        self.assertEqual(self.request()["code"], "source_unavailable")
        self.assertFalse(self.directory.exists())
        self.provider.assert_not_called()

    def test_reserved_and_mixed_course_pairs_are_blocked(self):
        for mixed in (True, False):
            self.case = approved_case()
            for run in self.case["source"]["observedRuns"]:
                if not mixed or run["slot"].endswith("a"):
                    run["seed"] = store.learning_store.PLAN["evaluationSeeds"][0 if run["slot"].endswith("a") else 1]
            self.assertEqual(self.request()["code"], "reserved_evaluation")
            self.assertEqual(store.get_case_review(self.case["id"])["code"], "reserved_evaluation")
        self.provider.assert_not_called()

    def test_corrupt_comparison_is_not_replaced_with_a_derived_expected_answer(self):
        self.case["source"]["comparison"]["aggregate"]["conclusion"] = "improved_on_both"
        self.assertEqual(self.request()["code"], "review_unavailable")
        self.provider.assert_not_called()

    def test_model_is_bound_and_changed_model_cannot_silently_start_another_task(self):
        first = self.request()
        self.assertTrue(first["ok"], first)
        self.connection.return_value = {"model": "different-model", "verified": True, "ready": True}
        self.assertTrue(self.request()["alreadyRequested"])
        self.change_input()
        preview = store.get_case_review(self.case["id"])["task"]
        self.assertEqual(preview["model"], "fixture-model")
        self.assertEqual(preview["configuredModel"], "different-model")
        self.assertFalse(preview["requestAllowed"])
        self.assertEqual(self.request()["code"], "task_model_changed")
        self.provider.assert_called_once()

    def test_model_change_during_request_cannot_be_recorded_as_success(self):
        self.provider.return_value = {**success(), "model": "different-model"}
        attempt = self.request()["task"]["attempt"]
        self.assertEqual(attempt["status"], "failed")
        self.assertEqual(attempt["model"], "fixture-model")
        self.provider.assert_called_once_with(attempt["input"], expected_model="fixture-model")

    def test_connection_is_required_for_new_request_but_not_reading_old_attempt(self):
        self.connection.return_value = {"model": "fixture-model", "verified": False, "ready": False}
        self.assertEqual(self.request()["code"], "connection_required")
        self.provider.assert_not_called()
        self.connection.return_value.update(verified=True, ready=True)
        self.assertTrue(self.request()["ok"])
        self.connection.return_value.update(verified=False, ready=False)
        self.assertEqual(store.get_case_review(self.case["id"])["task"]["attempt"]["status"], "succeeded")
        self.assertTrue(self.request()["alreadyRequested"])
        self.provider.assert_called_once()

    def test_corrupt_receipt_or_missing_manifest_fails_closed(self):
        self.assertTrue(self.request()["ok"])
        path = self.receipt_path()
        saved = path.read_bytes()
        path.write_text('{"status":"unknown"}')
        self.assertEqual(store.get_case_review(self.case["id"])["code"], "review_unavailable")
        self.assertEqual(self.request()["code"], "review_unavailable")
        path.write_bytes(saved)
        (self.directory / "task.json").unlink()
        self.assertEqual(self.request()["code"], "review_unavailable")
        self.provider.assert_called_once()

    def test_unrelated_unknown_files_and_symlinks_are_not_treated_as_empty_storage(self):
        self.directory.mkdir()
        unknown = self.directory / "unknown.json"
        unknown.write_text("{}")
        self.assertEqual(self.request()["code"], "review_unavailable")
        unknown.unlink()
        outside = Path(self.temp.name) / "outside.json"
        outside.write_text("{}")
        (self.directory / "task.json").symlink_to(outside)
        self.assertEqual(self.request()["code"], "review_unavailable")
        self.assertEqual(outside.read_text(), "{}")
        self.provider.assert_not_called()

    def test_attempt_capacity_is_bounded_without_replacing_existing_evidence(self):
        self.assertTrue(self.request()["ok"])
        first = self.receipt_path().read_bytes()
        self.change_input()
        with patch.object(store, "MAX_ATTEMPTS", 1):
            self.assertEqual(self.request()["code"], "storage_full")
        self.assertEqual(self.receipt_path().read_bytes(), first)
        self.provider.assert_called_once()

    def test_duplicate_json_keys_and_receipt_hash_tampering_fail_closed(self):
        self.assertTrue(self.request()["ok"])
        path = self.receipt_path()
        original = path.read_text()
        path.write_text(original[:-1] + ',"status":"failed"}')
        self.assertEqual(self.request()["code"], "review_unavailable")
        changed = json.loads(original)
        changed["sourceCaseRevision"] = 4
        path.write_text(json.dumps(changed))
        self.assertEqual(self.request()["code"], "review_unavailable")
        self.provider.assert_called_once()

    def test_request_requires_only_an_exact_integer_revision(self):
        for request in ({}, {"expectedRevision": True}, {"expectedRevision": 0},
                        {"expectedRevision": 2, "retry": True}, {"expectedRevision": "2"}):
            self.assertEqual(store.request_review(self.case["id"], request)["code"], "invalid_request")
        self.provider.assert_not_called()

    def test_real_saved_case_integration_preserves_source_bytes(self):
        # Exercise the public learning-store view as well as mocked snapshots.
        # All experiment/learning/receipt files remain inside this test's temp dir.
        import experiment_store as experiments
        from test_learning_store import LearningStoreTests

        self.source_patch.stop()
        learning_dir = Path(self.temp.name) / "learning"
        patch.object(store.learning_store, "LEARNING_DIR", learning_dir).start()
        patch.object(experiments, "EXPERIMENTS_DIR", Path(self.temp.name) / "experiments").start()
        proposal = {**self.case["source"]["originalAnswer"], "ok": True,
                    "model": "fixture-model", "requestId": "mreq_proposal_fixture",
                    "latencyMs": 1, "usage": None, "callsRemaining": 19}
        with patch.object(store.mubit_bridge, "propose", return_value=proposal):
            experiment = experiments.create_experiment({})["experiment"]
            identifier = experiment["id"]
            for slot in ("baseline-a", "repeat-a", "baseline-b"):
                saved = experiments.save_run(identifier, LearningStoreTests.run_record(self, slot))
                self.assertTrue(saved["ok"], saved)
            proposed = experiments.propose_change(identifier, {})
            self.assertTrue(proposed["ok"], proposed)
            for slot in ("candidate-a", "candidate-b"):
                saved = experiments.save_run(identifier, LearningStoreTests.run_record(self, slot))
                self.assertTrue(saved["ok"], saved)
        imported = store.learning_store.import_experiment(identifier, {})
        self.assertTrue(imported["ok"], imported)
        case_id = imported["case"]["id"]
        drafted = store.learning_store.create_result_draft(case_id, {"expectedRevision": 0})
        self.assertTrue(drafted["ok"], drafted)
        approved = store.learning_store.approve_correction(case_id, {"expectedRevision": 1})
        self.assertTrue(approved["ok"], approved)
        path = learning_dir / (case_id + ".json")
        before = path.read_bytes()
        reviewed = store.request_review(case_id, {"expectedRevision": 2})
        self.assertTrue(reviewed["ok"], reviewed)
        self.assertEqual(reviewed["task"]["attempt"]["status"], "succeeded")
        self.assertEqual(reviewed["task"]["attempt"]["sourceHash"], approved["case"]["sourceHash"])
        self.assertEqual(path.read_bytes(), before)
        self.assert_no_external()


if __name__ == "__main__":
    unittest.main()
