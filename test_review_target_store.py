"""Offline matched-task target custody checks; no live data or credentials."""
from copy import deepcopy
import json
from pathlib import Path
import stat
import tempfile
import threading
import unittest
from unittest.mock import patch

import review_target_store as store
from test_review_task_store import approved_case, success


class ReviewTargetStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(patch.stopall)
        root = Path(self.temp.name)
        self.directory = root / "targets"
        self.tasks = root / "tasks"
        patch.object(store, "REVIEW_TARGETS_DIR", self.directory).start()
        patch.object(store.review_task_store, "REVIEW_TASKS_DIR", self.tasks).start()
        self.case = approved_case()
        patch.object(store.learning_store, "get_case", side_effect=lambda _: {"ok": True, "case": deepcopy(self.case)}).start()
        self.connection = patch.object(store.mubit_bridge, "connection_status", return_value={
            "configured": True, "verified": True, "ready": True, "model": "fixture-model", "callsRemaining": 20}).start()
        self.provider = patch.object(store.mubit_bridge, "review_tested_change", return_value=success()).start()
        self.network = patch.object(store.mubit_bridge, "_open_request", side_effect=AssertionError("No network")).start()
        self.keychain = patch.object(store.mubit_bridge, "_keychain_key", side_effect=AssertionError("No credentials")).start()
        self.task = store.review_task_store.request_review(self.case["id"], {"expectedRevision": self.case["revision"]})["task"]
        self.provider.reset_mock()
        self.connection.reset_mock()
        self.reader = patch.object(store.review_task_store, "get_case_review", side_effect=lambda _: {"ok": True, "task": deepcopy(self.task)}).start()
        self.reason = "Reviewed by Codex (AI assistant) under the delegated user request against both saved course results."
        self.answer = deepcopy(self.task["attempt"]["result"])
        self.original_files = {p.name: p.read_bytes() for p in self.tasks.glob("*.json")}

    def tearDown(self):
        self.provider.assert_not_called()
        self.connection.assert_not_called()
        self.network.assert_not_called()
        self.keychain.assert_not_called()
        self.assertEqual(self.original_files, {p.name: p.read_bytes() for p in self.tasks.glob("*.json")})

    def get(self):
        return store.get_target(self.case["id"])

    def save(self, revision=0, answer=None, reason=None):
        return store.save_target(self.case["id"], {"expectedRevision": revision,
            "answer": self.answer if answer is None else answer, "reason": self.reason if reason is None else reason})

    def approve(self, revision=1):
        return store.approve_target(self.case["id"], {"expectedRevision": revision, "confirm": True})

    def path(self):
        return next(self.directory.glob("target_*.json"))

    def test_preview_never_creates_draft_or_reuses_old_approval(self):
        target = self.get()["target"]
        self.assertEqual(target["revision"], 0)
        self.assertIsNone(target["latestDraft"])
        self.assertEqual(target["history"], [])
        self.assertEqual(target["approval"], {"status": "unapproved", "identityVerified": False})
        self.assertFalse(list(self.directory.glob("target_*.json")))
        self.assertNotIn("PRIVATE", json.dumps(target))
        self.assertEqual(target["source"]["originalAnswer"], self.answer)
        self.assertEqual(target["source"]["inputHash"], self.task["attempt"]["inputHash"])
        self.assertEqual(target["source"]["requestId"], self.task["attempt"]["requestId"])
        self.assertEqual(target["source"]["messages"][0]["role"], "system")
        self.assertEqual(json.loads(target["source"]["messages"][2]["content"]), self.task["input"])
        self.assertFalse(store.export_target(self.case["id"])["ok"])

    def test_confirmed_reference_requires_separate_approval_and_exports_matched_schema(self):
        saved = self.save()
        self.assertTrue(saved["ok"], saved)
        self.assertFalse(saved["target"]["latestDraft"]["changedFromModel"])
        self.assertFalse(store.export_target(self.case["id"])["ok"])
        approved = self.approve()
        self.assertTrue(approved["ok"], approved)
        self.assertEqual(approved["target"]["revision"], 2)
        self.assertEqual(approved["target"]["approval"]["draftRevision"], 1)
        exported = store.export_target(self.case["id"])["export"]
        self.assertEqual(exported["reviewType"], "confirmed_reference")
        self.assertEqual(exported["correctedAnswer"], self.answer)
        self.assertEqual(exported["originalAnswer"], self.answer)
        self.assertEqual(set(exported["correctedAnswer"]), {"assessment", "decision", "rationale", "caveat"})
        self.assertEqual(exported["reason"], self.reason)
        for key in ("changedFromModel", "providerCompatible", "submitted", "modelUpdateVerified"):
            self.assertIs(exported[key], False)
        self.assertIs(exported["approval"]["identityVerified"], False)
        self.assertEqual(store._hash(exported["source"]), exported["sourceHash"])
        self.assertEqual(exported["source"]["sourceRecordHash"], self.task["attempt"]["recordHash"])
        self.assertNotIn("PRIVATE", json.dumps(exported))

    def test_corrected_answer_preserves_original_and_is_distinct_from_reference(self):
        answer = {**self.answer, "rationale": "No measured improvement occurred on either course."}
        self.assertTrue(self.save(answer=answer)["ok"])
        self.assertTrue(self.approve()["ok"])
        exported = store.export_target(self.case["id"])["export"]
        self.assertEqual(exported["reviewType"], "corrected_answer")
        self.assertTrue(exported["changedFromModel"])
        self.assertEqual(exported["correctedAnswer"], answer)
        self.assertEqual(exported["originalAnswer"], self.answer)

    def test_new_draft_invalidates_approval_and_preserves_history(self):
        self.save(); self.approve()
        old = self.get()["target"]
        changed = self.save(2, answer={**self.answer, "decision": "test_more"})
        self.assertTrue(changed["ok"], changed)
        target = changed["target"]
        self.assertEqual(target["revision"], 3)
        self.assertEqual(target["history"][:2], old["history"])
        self.assertEqual(target["approval"]["status"], "unapproved")
        self.assertFalse(store.export_target(self.case["id"])["ok"])
        self.assertTrue(self.approve(3)["ok"])

    def test_stale_saves_and_approvals_never_overwrite(self):
        self.save()
        before = self.path().read_bytes()
        for result in (self.save(), self.approve(2)):
            self.assertFalse(result["ok"])
            self.assertEqual(result["code"], "revision_conflict")
        self.assertEqual(self.path().read_bytes(), before)
        self.approve()
        before = self.path().read_bytes()
        self.assertFalse(self.approve(1)["ok"])
        self.assertTrue(self.approve(2)["alreadyApproved"])
        self.assertEqual(self.path().read_bytes(), before)

    def test_invalid_input_cannot_save_or_approve(self):
        invalid_answers = [None, {"kind": "no_change", "rationale": "Old schema"}, {**self.answer, "extra": True},
                           {**self.answer, "assessment": "no_change"}, {**self.answer, "rationale": "x" * 601},
                           {**self.answer, "rationale": "mbt_secret_fixture"}]
        for answer in invalid_answers:
            with self.subTest(answer=answer):
                result = store.save_target(self.case["id"], {"expectedRevision": 0, "answer": answer, "reason": self.reason})
                self.assertFalse(result["ok"])
        for request in ({"expectedRevision": True, "answer": self.answer, "reason": self.reason},
                        {"expectedRevision": 0, "answer": self.answer, "reason": ""},
                        {"expectedRevision": 0, "answer": self.answer, "reason": "mbt_secret_fixture"},
                        {"expectedRevision": 0, "answer": self.answer, "reason": self.reason, "approve": True}):
            self.assertFalse(store.save_target(self.case["id"], request)["ok"])
        for request in ({"expectedRevision": 0, "confirm": True}, {"expectedRevision": 1, "confirm": 1},
                        {"expectedRevision": 1}, {"expectedRevision": 1, "confirm": True, "identityVerified": True}):
            self.assertFalse(store.approve_target(self.case["id"], request)["ok"])
        self.assertFalse(list(self.directory.glob("target_*.json")))

    def test_no_succeeded_reply_fails_closed_and_cannot_export(self):
        for attempt in (None, {"status": "pending"}, {"status": "failed"}):
            self.task["attempt"] = attempt
            self.assertEqual(self.get()["code"], "reply_required")
            self.assertFalse(self.save()["ok"])
            self.assertFalse(store.export_target(self.case["id"])["ok"])
        self.assertFalse(self.directory.exists())

    def test_source_read_failure_does_not_treat_saved_target_as_absent(self):
        self.save()
        before = self.path().read_bytes()
        self.reader.side_effect = lambda _: {"ok": False, "message": "Unreadable source"}
        self.assertEqual(self.get()["code"], "source_unavailable")
        self.assertFalse(self.save(1)["ok"])
        self.assertEqual(self.path().read_bytes(), before)

    def test_alias_case_resolves_to_same_record_without_changing_custody(self):
        first = self.save()["target"]
        self.task["caseId"] = "case_alias"
        second = store.get_target("case_alias")["target"]
        self.assertEqual(second["id"], first["id"])
        self.assertEqual(second["caseId"], "case_alias")
        self.assertEqual(second["source"], first["source"])
        self.assertEqual(len(list(self.directory.glob("target_*.json"))), 1)

    def test_changed_provider_reply_under_same_request_is_not_substituted(self):
        self.save()
        before = self.path().read_bytes()
        attempt = self.task["attempt"]
        attempt["result"]["rationale"] = "A different answer under the same request."
        attempt["recordHash"] = store.review_task_store._hash({k: v for k, v in attempt.items() if k != "recordHash"})
        self.assertFalse(self.get()["ok"])
        self.assertFalse(self.save(1)["ok"])
        self.assertEqual(self.path().read_bytes(), before)

    def test_mismatched_case_response_cannot_create_a_target(self):
        self.task["caseId"] = "case_other"
        self.assertFalse(self.get()["ok"])
        self.assertFalse(self.save()["ok"])
        self.assertFalse(self.directory.exists())

    def test_reserved_course_cannot_be_used_even_if_receipt_hash_is_rebuilt(self):
        attempt = self.task["attempt"]
        attempt["input"]["courses"][0]["seed"] = 30000001
        attempt["inputHash"] = store.review_task_store._input_hash(attempt["input"])
        attempt["id"] = attempt["input"]["runId"] = "review_" + attempt["inputHash"][:32]
        attempt["recordHash"] = store.review_task_store._hash({k: v for k, v in attempt.items() if k != "recordHash"})
        self.task["input"] = deepcopy(attempt["input"])
        self.assertFalse(self.get()["ok"])
        self.assertFalse(self.save()["ok"])
        self.assertFalse(self.directory.exists())

    def test_corrupt_record_is_unknown_not_empty_and_preserved(self):
        self.save()
        path = self.path()
        for raw in (b'{"broken":', b'{"a":1,"a":2}'):
            path.write_bytes(raw)
            self.assertFalse(self.get()["ok"])
            self.assertFalse(self.save(1)["ok"])
            self.assertEqual(path.read_bytes(), raw)

    def test_tampered_approval_or_source_fails_validation(self):
        self.save(); self.approve()
        original = json.loads(self.path().read_text())
        for field, value in (("identityVerified", True), ("draftRevision", 99)):
            record = deepcopy(original)
            record["history"][-1][field] = value
            record["recordHash"] = store._hash({k: v for k, v in record.items() if k != "recordHash"})
            self.path().write_text(json.dumps(record))
            self.assertFalse(self.get()["ok"])
            self.assertFalse(store.export_target(self.case["id"])["ok"])

    def test_private_permissions_and_symlink_refusal(self):
        self.save()
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        for path in self.directory.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        record_path = self.path()
        outside = Path(self.temp.name) / "outside.json"
        outside.write_bytes(record_path.read_bytes())
        record_path.unlink(); record_path.symlink_to(outside)
        before = outside.read_bytes()
        self.assertFalse(self.get()["ok"])
        self.assertFalse(self.save(1)["ok"])
        self.assertEqual(outside.read_bytes(), before)

    def test_concurrent_initial_saves_have_one_winner(self):
        outcomes = []
        gate = threading.Barrier(2)
        def save():
            gate.wait(3)
            outcomes.append(self.save())
        threads = [threading.Thread(target=save) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(5)
        self.assertTrue(all(not t.is_alive() for t in threads))
        self.assertEqual(sum(r["ok"] for r in outcomes), 1)
        self.assertEqual(self.get()["target"]["revision"], 1)
        self.assertEqual(len(list(self.directory.glob("target_*.json"))), 1)

    def test_record_and_revision_limits_preserve_existing_state(self):
        with patch.object(store, "MAX_RECORDS", 0):
            self.assertFalse(self.save()["ok"])
        self.save()
        before = self.path().read_bytes()
        with patch.object(store, "MAX_REVISIONS", 1):
            self.assertEqual(self.save(1)["code"], "storage_full")
            self.assertEqual(self.approve()["code"], "storage_full")
        self.assertEqual(self.path().read_bytes(), before)

    def test_write_failure_never_returns_success_or_substitutes_approval(self):
        self.save()
        before = self.path().read_bytes()
        with patch.object(store.os, "replace", side_effect=OSError("Not writable")):
            self.assertFalse(self.approve()["ok"])
        self.assertEqual(self.path().read_bytes(), before)
        self.assertEqual(self.get()["target"]["approval"]["status"], "unapproved")
        self.assertFalse(list(self.directory.glob(".pending-*")))


if __name__ == "__main__":
    unittest.main()
