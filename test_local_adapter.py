"""Offline custody tests. Fixtures never run/download a model or contact Mubit."""
from copy import deepcopy
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

import local_adapter as store
from test_review_task_store import approved_case, success


class LocalAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(patch.stopall)
        self.root = Path(self.temp.name)
        self.directory = self.root / "adapters"
        patch.object(store, "ADAPTERS_DIR", self.directory).start()
        patch.object(store.review_target_store, "REVIEW_TARGETS_DIR", self.root / "targets").start()
        patch.object(store.review_task_store, "REVIEW_TASKS_DIR", self.root / "tasks").start()
        self.case = approved_case() | {"split": "training"}
        patch.object(store.learning_store, "get_case", side_effect=lambda _: {"ok": True, "case": deepcopy(self.case)}).start()
        patch.object(store.learning_store, "list_cases", return_value={"ok": True, "cases": [{"id": self.case["id"]}], "unavailable": []}).start()
        self.network = patch.object(store.review_task_store.mubit_bridge, "_open_request", side_effect=AssertionError("No live network")).start()
        self.keychain = patch.object(store.review_task_store.mubit_bridge, "_keychain_key", side_effect=AssertionError("No key access")).start()
        patch.object(store.review_task_store.mubit_bridge, "connection_status", return_value={
            "configured": True, "verified": True, "ready": True, "model": "fixture-model", "callsRemaining": 20}).start()
        provider = patch.object(store.review_task_store.mubit_bridge, "review_tested_change", return_value=success()).start()
        self.task = store.review_task_store.request_review(self.case["id"], {"expectedRevision": self.case["revision"]})["task"]
        provider.reset_mock()
        self.provider = provider
        self.real_case_review = store.review_task_store.get_case_review
        self.case_reader = patch.object(store.review_task_store, "get_case_review", side_effect=lambda _, **unused: {"ok": True, "task": deepcopy(self.task)})
        self.case_reader.start()
        self.model = self.root / "model-fixture"
        self.model.mkdir()
        (self.model / "config.json").write_text('{"model_type":"qwen2"}')
        (self.model / "model.safetensors").write_bytes(b"OFFLINE TEST FIXTURE, NOT MODEL WEIGHTS")
        self.revision = "a" * 40

    def tearDown(self):
        self.network.assert_not_called()
        self.keychain.assert_not_called()
        self.provider.assert_not_called()

    def approve(self, corrected=False):
        answer = deepcopy(self.task["attempt"]["result"])
        if corrected:
            answer["rationale"] = "Both saved course outcomes stayed unchanged."
        result = store.review_target_store.save_target(self.case["id"], {
            "expectedRevision": 0, "answer": answer, "reason": "Offline fixture review."})
        self.assertTrue(result["ok"], result)
        self.assertTrue(store.review_target_store.approve_target(self.case["id"], {"expectedRevision": 1, "confirm": True})["ok"])

    def prepare(self):
        result = store.prepare(self.model, self.revision, [self.case["id"]])
        self.assertTrue(result["ok"], result)
        self.identifier = result["localAdapter"]["adapterId"]
        self.attempt = self.directory / self.identifier
        return result

    def fake_child(self, stage, attempt, env):
        self.assertEqual(env["HF_HUB_OFFLINE"], "1")
        self.assertNotIn("MUBIT_API_KEY", env)
        if stage == "train":
            (attempt / "weights").mkdir(mode=0o700)
            store._write_new(attempt / "weights" / "adapters.safetensors", b"OFFLINE TEST ADAPTER FIXTURE", raw=True)
            store._write_new(attempt / "weights" / "adapter_config.json", {"fixture": True})
            store._write_new(attempt / "training-metrics.json", {"trainingLossHistory": [{"iteration": 1, "train_loss": 1.2}]})
        else:
            store._write_new(attempt / (stage + ".json"), {"label": "in_sample_training_replay",
                "text": "offline fixture", "answer": None, "jsonValid": False, "elapsedSeconds": 0.1})

    def fake_run(self, changed=True, child=None):
        with patch.object(store.sys, "version_info", (3, 12)), patch.object(store.importlib.metadata, "version", return_value=store.MLX_VERSION), \
             patch.object(store, "_child", side_effect=child or self.fake_child), \
             patch.object(store, "_weight_evidence", return_value={"weightsChanged": changed, "tensors": [], "method": "OFFLINE MOCK"}):
            return store.run(self.identifier)

    def test_status_and_export_without_attempt_are_read_only(self):
        self.assertEqual(store.status()["localAdapter"]["status"], "not_started")
        self.assertFalse(store.export()["ok"])
        self.assertFalse(self.directory.exists())

    def test_reference_dataset_retains_exact_approved_answer_and_no_test_split(self):
        self.approve()
        result = self.prepare()["localAdapter"]
        self.assertEqual(result["trainingExamples"], 1)
        self.assertEqual(result["correctedExamples"], 0)
        self.assertEqual(result["confirmedReferences"], 1)
        self.assertEqual(result["independentEvaluation"]["status"], "not_available")
        self.assertFalse(result["exportAvailable"])
        self.assertEqual([p.name for p in (self.attempt / "data").iterdir()], ["train.jsonl"])
        row = json.loads((self.attempt / "data" / "train.jsonl").read_text())
        source = store.review_target_store.export_target(self.case["id"])["export"]
        self.assertEqual(row["messages"][:-1], source["messages"])
        self.assertEqual(json.loads(row["messages"][-1]["content"]), source["correctedAnswer"])
        for path in self.attempt.rglob("*"):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700 if path.is_dir() else 0o600)

    def test_correction_count_is_from_actual_answer_difference(self):
        self.approve(corrected=True)
        result = self.prepare()["localAdapter"]
        self.assertEqual(result["correctedExamples"], 1)
        self.assertEqual(result["confirmedReferences"], 0)

    def test_unapproved_and_legacy_case_are_rejected(self):
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse(self.directory.exists())

    def test_reserved_course_and_nontraining_case_are_rejected(self):
        self.approve()
        self.case["split"] = "evaluation"
        with self.assertRaises(ValueError):
            self.prepare()
        self.case["split"] = "training"
        with patch.object(store.learning_store, "is_evaluation_seeds", return_value=True), self.assertRaises(ValueError):
            self.prepare()

    def test_duplicate_case_is_rejected(self):
        self.approve()
        with self.assertRaises(ValueError):
            store.prepare(self.model, self.revision, [self.case["id"], self.case["id"]])

    def test_model_revision_and_remote_code_fail_closed(self):
        self.approve()
        with self.assertRaises(ValueError):
            store.prepare(self.model, "main", [self.case["id"]])
        (self.model / "config.json").write_text('{"model_type":"qwen2","auto_map":{"AutoModel":"custom.py"}}')
        with self.assertRaises(ValueError):
            self.prepare()

    def test_frozen_data_tamper_and_extra_split_are_unknown(self):
        self.approve(); self.prepare()
        original = (self.attempt / "data" / "train.jsonl").read_bytes()
        (self.attempt / "data" / "train.jsonl").write_bytes(b"changed")
        self.assertEqual(store.status()["localAdapter"]["status"], "unknown")
        (self.attempt / "data" / "train.jsonl").write_bytes(original)
        (self.attempt / "data" / "valid.jsonl").touch()
        self.assertFalse(store.status()["ok"])

    def test_partial_or_symlink_attempt_is_not_empty_state(self):
        self.directory.mkdir()
        (self.directory / ("adapter_" + "c" * 32)).mkdir()
        self.assertEqual(store.status()["localAdapter"]["status"], "unknown")

    def test_completed_run_is_one_shot_and_exports_evidence(self):
        self.approve(); self.prepare()
        result = self.fake_run()["localAdapter"]
        self.assertEqual(result["status"], "succeeded")
        self.assertTrue(result["weightsChanged"])
        self.assertTrue(result["exportAvailable"])
        self.assertEqual(result["independentEvaluation"]["examples"], 0)
        receipt_bytes = (self.attempt / "receipt.json").read_bytes()
        with self.assertRaises(FileExistsError):
            self.fake_run()
        self.assertEqual((self.attempt / "receipt.json").read_bytes(), receipt_bytes)
        exported = store.export()["export"]
        self.assertNotIn("path", exported["manifest"]["model"])
        self.assertFalse(exported["containsWeights"])
        (self.attempt / "weights" / "adapters.safetensors").write_bytes(b"tampered")
        self.assertFalse(store.status()["ok"])
        self.assertFalse(store.export()["ok"])

    def test_zero_adapter_update_is_never_claimed_changed(self):
        self.approve(); self.prepare()
        result = self.fake_run(changed=False)["localAdapter"]
        self.assertFalse(result["weightsChanged"])
        self.assertIn("no non-zero", result["message"])

    def test_failed_attempt_is_preserved_and_never_retried(self):
        self.approve(); self.prepare()
        result = self.fake_run(child=lambda *args: (_ for _ in ()).throw(RuntimeError("Fixture failure")))["localAdapter"]
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["weightsChanged"])
        self.assertTrue(result["exportAvailable"])
        with self.assertRaises(FileExistsError):
            self.fake_run()

    def test_model_change_prevents_start(self):
        self.approve(); self.prepare()
        (self.model / "model.safetensors").write_bytes(b"changed fixture")
        with self.assertRaises(ValueError):
            self.fake_run()
        self.assertFalse((self.attempt / "started.json").exists())

    def test_revoked_approval_prevents_start_of_prepared_attempt(self):
        self.approve(); self.prepare()
        self.assertTrue(store.review_target_store.save_target(self.case["id"], {
            "expectedRevision": 2, "answer": self.task["attempt"]["result"],
            "reason": "Changed review; approval must be given again."})["ok"])
        with self.assertRaises(ValueError):
            self.fake_run()
        self.assertFalse((self.attempt / "started.json").exists())

    def test_approval_revoked_during_baseline_prevents_training(self):
        self.approve(); self.prepare()
        stages = []

        def revoke_after_baseline(stage, attempt, env):
            stages.append(stage)
            self.fake_child(stage, attempt, env)
            self.assertTrue(store.review_target_store.save_target(self.case["id"], {
                "expectedRevision": 2, "answer": self.task["attempt"]["result"],
                "reason": "Approval revoked while baseline was running."})["ok"])

        result = self.fake_run(child=revoke_after_baseline)["localAdapter"]
        self.assertEqual(stages, ["baseline"])
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["weightsChanged"])
        self.assertFalse((self.attempt / "weights").exists())
        self.assertIsNotNone(result["baseline"])

    def test_adapter_parameters_use_actual_saved_scale_and_reject_invalid(self):
        path = self.root / "adapter-config-fixture.json"
        path.write_text(json.dumps({"fine_tune_type": "lora", "lora_parameters": {"scale": 7.5, "rank": 4}}))
        self.assertEqual(store._adapter_parameters(path), {"scale": 7.5, "rank": 4})
        for parameters in ({"scale": True, "rank": 4}, {"scale": 0, "rank": 4},
                           {"scale": 20, "rank": False}, {"scale": 20}, {}):
            path.write_text(json.dumps({"fine_tune_type": "lora", "lora_parameters": parameters}))
            with self.assertRaises(ValueError):
                store._adapter_parameters(path)

    def test_real_offline_store_chain_never_reads_connection_or_keychain(self):
        self.approve()
        self.case_reader.stop()
        bridge = store.review_task_store.mubit_bridge
        with patch.object(bridge, "connection_status", side_effect=AssertionError("No connection read")) as connection, \
             patch.object(bridge, "_read_config", side_effect=AssertionError("No config/credentials")) as config:
            self.assertTrue(self.real_case_review(self.case["id"], read_connection=False)["ok"])
            self.assertTrue(store.review_target_store.export_target(self.case["id"], read_connection=False)["ok"])
            self.prepare()
            self.assertTrue(self.fake_run()["ok"])
            self.assertTrue(store.status()["ok"])
            self.assertTrue(store.export()["ok"])
            connection.assert_not_called()
            config.assert_not_called()
        self.keychain.assert_not_called()

    def test_training_child_rechecks_approval_before_importing_runtime(self):
        self.approve(); self.prepare()
        self.assertTrue(store.review_target_store.save_target(self.case["id"], {
            "expectedRevision": 2, "answer": self.task["attempt"]["result"],
            "reason": "Revoked before child starts."})["ok"])
        with self.assertRaises(ValueError):
            store._worker("train", self.identifier)
        self.assertFalse((self.attempt / "weights").exists())


if __name__ == "__main__":
    unittest.main()
