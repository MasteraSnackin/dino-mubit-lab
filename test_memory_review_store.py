"""Offline prompt-memory custody checks; all provider and key access mocked."""
from copy import deepcopy
import json
from pathlib import Path
import stat
import tempfile
import threading
import unittest
from unittest.mock import patch

import memory_review_store as store
from test_review_task_store import approved_case, success


class MemoryReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(patch.stopall)
        root = Path(self.temp.name)
        self.directory = root / "memory"
        patch.object(store, "MEMORY_REVIEWS_DIR", self.directory).start()
        patch.object(store.learning_store, "LEARNING_DIR", root / "learning").start()
        patch.object(store.review_task_store, "REVIEW_TASKS_DIR", root / "tasks").start()
        patch.object(store.review_target_store, "REVIEW_TARGETS_DIR", root / "targets").start()
        self.reference = approved_case()
        self.current = approved_case()
        self.current["id"] = "case_current"
        self.current["source"]["originalAnswer"]["value"] = 60
        self.current["source"]["candidateConfig"]["duckExtraMs"] = 60
        for run in self.current["source"]["observedRuns"]:
            if run["slot"].startswith("candidate"):
                run["config"]["duckExtraMs"] = 60
        self.cases = {self.reference["id"]: self.reference, self.current["id"]: self.current}
        patch.object(store.learning_store, "get_case", side_effect=lambda case: {"ok": True, "case": deepcopy(self.cases[case])}).start()
        # The compact fixtures intentionally omit screenshot-heavy case custody.
        # Keep the real learning lock, but project the same fixture through its
        # unlocked reader for the atomic approval recheck at pending commitment.
        patch.object(store.learning_store, "_load", side_effect=lambda case: (deepcopy(self.cases[case]), None)).start()
        patch.object(store.learning_store, "_view", side_effect=deepcopy).start()
        self.connection = patch.object(store.mubit_bridge, "connection_status", return_value={
            "model": "fixture-model", "verified": True, "ready": True, "callsRemaining": 20}).start()
        self.plain = patch.object(store.mubit_bridge, "review_tested_change", return_value=success()).start()
        self.provider = patch.object(store.mubit_bridge, "memory_review", return_value=success()).start()
        self.network = patch.object(store.mubit_bridge, "_open_request", side_effect=AssertionError("No network")).start()
        self.keychain = patch.object(store.mubit_bridge, "_keychain_key", side_effect=AssertionError("No credentials")).start()
        ref_task = store.review_task_store.request_review(self.reference["id"], {"expectedRevision": 2})["task"]
        self.answer = deepcopy(ref_task["attempt"]["result"])
        draft = store.review_target_store.save_target(self.reference["id"], {
            "expectedRevision": 0, "answer": self.answer, "reason": "Reviewed by Codex AI against saved outcomes."})
        self.assertTrue(draft["ok"], draft)
        approved = store.review_target_store.approve_target(self.reference["id"], {"expectedRevision": 1, "confirm": True})
        self.assertTrue(approved["ok"], approved)
        self.target_id = approved["target"]["id"]
        self.plain.reset_mock()

    def tearDown(self):
        self.network.assert_not_called()
        self.keychain.assert_not_called()

    def preview(self, case=None):
        return store.get_case_review(case or self.current["id"])

    def request(self, payload=None):
        if payload is None:
            view = self.preview()["memoryReview"]
            payload = {"expectedRevision": self.current["revision"], "expectedInputHash": view["inputHash"] or "0" * 64}
        return store.request_review(self.current["id"], payload)

    def path(self):
        return next(self.directory.glob("memory_*.json"))

    def test_preview_is_readonly_and_only_sends_bounded_approved_examples(self):
        view = self.preview()["memoryReview"]
        self.assertTrue(view["requestAllowed"])
        self.assertIsNone(view["attempt"])
        self.assertIsNone(view["baseline"])
        self.assertEqual(len(view["memories"]), 1)
        self.assertEqual(view["memories"][0]["reviewType"], "confirmed_reference")
        self.assertTrue(view["memories"][0]["sameCourseFamily"])
        self.assertNotEqual(view["input"]["current"]["originalProposal"]["value"], view["input"]["memories"][0]["input"]["originalProposal"]["value"])
        raw = json.dumps(view["input"])
        for forbidden in ("PRIVATE", "HIDDEN", "reason", "expectedAssessment", "targetRecordHash", "sourceCaseId"):
            self.assertNotIn(forbidden, raw)
        self.assertEqual(view["inputHash"], store._hash(view["input"]))
        self.assertFalse(list(self.directory.glob("memory_*.json")))
        self.provider.assert_not_called(); self.plain.assert_not_called()

    def test_exact_current_input_is_not_eligible_memory(self):
        view = self.preview(self.reference["id"])["memoryReview"]
        self.assertEqual(view["memories"], [])
        self.assertFalse(view["requestAllowed"])
        self.assertIsNone(view["input"])
        self.provider.assert_not_called()

    def test_selection_is_bounded_and_prefers_approved_corrections(self):
        for value in (10, 20, 30):
            case = approved_case()
            case["id"] = "case_reference_" + str(value)
            case["source"]["originalAnswer"]["value"] = value
            case["source"]["candidateConfig"]["duckExtraMs"] = value
            for run in case["source"]["observedRuns"]:
                if run["slot"].startswith("candidate"):
                    run["config"]["duckExtraMs"] = value
            self.cases[case["id"]] = case
            self.assertTrue(store.review_task_store.request_review(case["id"], {"expectedRevision": 2})["ok"])
            self.assertTrue(store.review_target_store.save_target(case["id"], {
                "expectedRevision": 0, "answer": {**self.answer, "rationale": "Checked outcome with clearer wording."},
                "reason": "AI reviewed the supplied measurements."})["ok"])
            self.assertTrue(store.review_target_store.approve_target(case["id"], {"expectedRevision": 1, "confirm": True})["ok"])
        view = self.preview()["memoryReview"]
        self.assertEqual(len(view["memories"]), 3)
        self.assertEqual({m["reviewType"] for m in view["memories"]}, {"corrected_answer"})
        self.assertEqual(len({m["inputHash"] for m in view["memories"]}), 3)
        self.assertLessEqual(len(store.mubit_bridge._validate_memory_review(view["input"])[1]["content"].encode()), 24 * 1024)
        self.provider.assert_not_called()

    def test_pending_is_durable_before_one_call_and_success_is_not_training(self):
        old = {str(p): p.read_bytes() for d in (store.review_task_store.REVIEW_TASKS_DIR, store.review_target_store.REVIEW_TARGETS_DIR) for p in d.glob("*.json")}
        def call(payload, expected_model):
            pending = json.loads(self.path().read_text())
            self.assertEqual(pending["status"], "pending")
            self.assertEqual(pending["input"], payload)
            self.assertEqual(expected_model, "fixture-model")
            return success()
        self.provider.side_effect = call
        view = self.request()["memoryReview"]
        attempt = view["attempt"]
        self.assertEqual(attempt["status"], "succeeded")
        self.assertTrue(attempt["assessmentMatchesMeasurements"])
        self.assertFalse(attempt["weightUpdate"])
        self.assertFalse(attempt["modelUpdateVerified"])
        self.assertEqual(attempt["memories"][0]["targetId"], self.target_id)
        self.assertFalse(view["requestAllowed"])
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.path().stat().st_mode), 0o600)
        self.assertEqual(old, {p: Path(p).read_bytes() for p in old})
        self.provider.assert_called_once()

    def test_success_failed_and_pending_attempts_cannot_repeat(self):
        self.provider.return_value = {"ok": False, "message": "Timed out without a confirmed result."}
        first = self.request()["memoryReview"]["attempt"]
        self.assertEqual(first["status"], "failed")
        self.assertTrue(self.request()["alreadyRequested"])
        self.provider.assert_called_once()
        record = json.loads(self.path().read_text())
        record.update(status="pending", completedAt=None, requestId=None, usage=None, callsRemaining=None, result=None, assessmentMatchesMeasurements=None)
        with store._storage_lock():
            store._write(record)
        self.assertEqual(self.request()["memoryReview"]["attempt"]["status"], "pending")
        self.provider.assert_called_once()

    def test_valid_wrong_assessment_stays_visible(self):
        self.provider.return_value = success("improved_on_both")
        attempt = self.request()["memoryReview"]["attempt"]
        self.assertEqual(attempt["status"], "succeeded")
        self.assertEqual(attempt["result"]["assessment"], "improved_on_both")
        self.assertFalse(attempt["assessmentMatchesMeasurements"])

    def test_preview_drift_rejected_before_call_or_pending_write(self):
        view = self.preview()["memoryReview"]
        store.review_target_store.save_target(self.reference["id"], {"expectedRevision": 2, "answer": {**self.answer, "caveat": "Keep a narrow conclusion."}, "reason": "AI reviewed new wording."})
        store.review_target_store.approve_target(self.reference["id"], {"expectedRevision": 3, "confirm": True})
        result = self.request({"expectedRevision": 2, "expectedInputHash": view["inputHash"]})
        self.assertEqual(result["code"], "context_changed")
        self.assertFalse(list(self.directory.glob("memory_*.json")))
        self.provider.assert_not_called()

    def test_reference_approval_revoked_after_prepare_prevents_pending_and_call(self):
        view = self.preview()["memoryReview"]
        original = store._prepare
        def prepare(case_id):
            result = original(case_id)
            changed = store.review_target_store.save_target(self.reference["id"], {
                "expectedRevision": 2, "answer": self.answer, "reason": "New review awaiting approval."})
            self.assertTrue(changed["ok"], changed)
            self.assertEqual(changed["target"]["approval"]["status"], "unapproved")
            return result
        with patch.object(store, "_prepare", side_effect=prepare):
            result = self.request({"expectedRevision": 2, "expectedInputHash": view["inputHash"]})
        self.assertEqual(result["code"], "context_changed")
        self.assertFalse(list(self.directory.glob("memory_*.json")))
        self.provider.assert_not_called()

    def test_current_case_changed_after_prepare_prevents_pending_and_call(self):
        view = self.preview()["memoryReview"]
        original = store._prepare
        original_case = deepcopy(self.current)
        for field, changed in (("revision", 3), ("sourceHash", "b" * 64),
                               ("approval", {"status": "unapproved"})):
            with self.subTest(field=field):
                self.current.clear(); self.current.update(deepcopy(original_case))
                def prepare(case_id):
                    result = original(case_id)
                    self.current[field] = changed
                    return result
                with patch.object(store, "_prepare", side_effect=prepare):
                    result = self.request({"expectedRevision": 2, "expectedInputHash": view["inputHash"]})
                self.assertEqual(result["code"], "revision_conflict")
                self.assertFalse(list(self.directory.glob("memory_*.json")))
                self.provider.assert_not_called()

    def test_approval_locks_held_through_pending_write_and_released_before_call(self):
        original = store._write
        started = [threading.Event(), threading.Event()]
        acquired = [threading.Event(), threading.Event()]
        threads = []
        def take_lock(index, lock):
            started[index].set()
            with lock():
                acquired[index].set()
        def write(record, **kwargs):
            if kwargs.get("new"):
                for index, lock in enumerate((store.learning_store._storage_lock, store.review_target_store._storage_lock)):
                    thread = threading.Thread(target=take_lock, args=(index, lock), daemon=True)
                    threads.append(thread)
                    thread.start()
                    self.assertTrue(started[index].wait(2))
                result = original(record, **kwargs)
                for event in acquired:
                    self.assertFalse(event.wait(0.05))
                return result
            return original(record, **kwargs)
        def call(*args, **kwargs):
            for event in acquired:
                self.assertTrue(event.wait(2))
            return success()
        self.provider.side_effect = call
        try:
            with patch.object(store, "_write", side_effect=write):
                self.assertTrue(self.request()["ok"])
        finally:
            for thread in threads:
                thread.join(3)
        self.provider.assert_called_once()

    def test_saved_attempt_keeps_historical_memory_after_target_changes(self):
        first = self.request()["memoryReview"]
        store.review_target_store.save_target(self.reference["id"], {"expectedRevision": 2, "answer": self.answer, "reason": "New unapproved review."})
        after = self.preview()["memoryReview"]
        self.assertEqual(after["attempt"], first["attempt"])
        self.assertEqual(after["inputHash"], first["inputHash"])
        self.assertEqual(after["memories"], first["memories"])
        self.assertTrue(self.request()["alreadyRequested"])
        self.provider.assert_called_once()

    def test_unapproved_reference_is_excluded_and_current_approval_required(self):
        store.review_target_store.save_target(self.reference["id"], {"expectedRevision": 2, "answer": self.answer, "reason": "New draft without approval."})
        self.assertEqual(self.request()["code"], "no_eligible_memory")
        self.current["approval"]["status"] = "unapproved"
        self.assertEqual(self.request()["code"], "approval_required")
        self.provider.assert_not_called()

    def test_revision_and_schema_checks_prevent_arbitrary_input(self):
        for payload in ({"expectedRevision": 2}, {"expectedRevision": True, "expectedInputHash": "0" * 64},
                        {"expectedRevision": 2, "expectedInputHash": "not-a-hash"},
                        {"expectedRevision": 2, "expectedInputHash": "0" * 64, "memories": []}):
            self.assertEqual(self.request(payload)["code"], "invalid_request")
        self.assertEqual(self.request({"expectedRevision": 1, "expectedInputHash": self.preview()["memoryReview"]["inputHash"]})["code"], "revision_conflict")
        self.provider.assert_not_called()

    def test_current_reserved_course_fails_before_provider(self):
        self.current["source"]["observedRuns"][0]["seed"] = 30000001
        self.current["source"]["observedRuns"][2]["seed"] = 30000001
        self.assertFalse(self.preview()["ok"])
        self.provider.assert_not_called()

    def test_no_ready_connection_and_changed_model_response_fail_closed(self):
        self.connection.return_value["ready"] = False
        self.assertFalse(self.preview()["memoryReview"]["requestAllowed"])
        self.assertEqual(self.request()["code"], "connection_required")
        self.provider.assert_not_called()
        self.connection.return_value["ready"] = True
        self.provider.return_value = {**success(), "model": "unexpected-model"}
        self.assertEqual(self.request()["memoryReview"]["attempt"]["status"], "failed")

    def test_baseline_is_only_the_same_compact_input(self):
        self.assertIsNone(self.preview()["memoryReview"]["baseline"])
        result = store.review_task_store.request_review(self.current["id"], {"expectedRevision": 2})
        self.assertTrue(result["ok"], result)
        baseline = self.preview()["memoryReview"]["baseline"]
        self.assertTrue(baseline["sameInput"])
        self.assertTrue(baseline["sameModel"])
        self.assertEqual(baseline["result"], self.answer)

    def test_corrupt_memory_or_receipt_is_unknown_never_empty(self):
        self.request()
        raw = self.path().read_bytes()
        self.path().write_text('{"bad":true}')
        self.assertFalse(self.preview()["ok"])
        self.assertFalse(self.request({"expectedRevision": 2, "expectedInputHash": "0" * 64})["ok"])
        self.provider.assert_called_once()
        self.path().write_bytes(raw)
        target = next(store.review_target_store.REVIEW_TARGETS_DIR.glob("target_*.json"))
        target.write_text('{}')
        self.assertFalse(self.preview()["ok"])

    def test_pending_write_failure_makes_no_call_and_final_failure_preserves_pending(self):
        original = store._write
        with patch.object(store, "_write", side_effect=OSError()):
            self.assertFalse(self.request()["ok"])
        self.provider.assert_not_called()
        def write(record, **kwargs):
            if not kwargs.get("new"):
                raise OSError()
            return original(record, **kwargs)
        with patch.object(store, "_write", side_effect=write):
            self.assertFalse(self.request()["ok"])
        self.assertEqual(json.loads(self.path().read_text())["status"], "pending")
        self.assertTrue(self.request()["alreadyRequested"])
        self.provider.assert_called_once()

    def test_concurrent_requests_share_pending_attempt(self):
        entered, release = threading.Event(), threading.Event()
        def call(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(2))
            return success()
        self.provider.side_effect = call
        payload = {"expectedRevision": 2, "expectedInputHash": self.preview()["memoryReview"]["inputHash"]}
        results = []
        thread = threading.Thread(target=lambda: results.append(self.request(payload)))
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            duplicate = self.request(payload)
            self.assertTrue(duplicate["alreadyRequested"])
            self.assertEqual(duplicate["memoryReview"]["attempt"]["status"], "pending")
        finally:
            release.set(); thread.join(3)
        self.assertTrue(results[0]["ok"])
        self.provider.assert_called_once()


if __name__ == "__main__":
    unittest.main()
