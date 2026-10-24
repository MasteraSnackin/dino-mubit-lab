"""Offline experiment checks: temporary private records and a mocked Mubit bridge."""
import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import stat
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch
import zlib

import experiment_store as store


def png():
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    data = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\0\0\0\0\xff")) + chunk(b"IEND", b""))
    return "data:image/png;base64," + base64.b64encode(data).decode()


class ExperimentStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name) / "experiments"
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(patch.stopall)
        patch.object(store, "EXPERIMENTS_DIR", self.directory).start()
        self.propose = patch.object(store.mubit_bridge, "propose", return_value=self.answer()).start()
        self.status = patch.object(store.mubit_bridge, "connection_status", return_value={"model": "test-model"}).start()
        self.network = patch.object(store.mubit_bridge, "_open_request", side_effect=AssertionError("No network in tests")).start()
        self.keychain = patch.object(store.mubit_bridge, "_keychain_key", side_effect=AssertionError("No credentials in tests")).start()
        self.experiment = store.create_experiment({})["experiment"]
        self.identifier = self.experiment["id"]

    @staticmethod
    def answer():
        return {"ok": True, "parameter": "jumpLeadMs", "value": 190,
                "rationale": "Test a slightly earlier jump.", "caveat": "Only two courses are represented.",
                "requestId": "mreq_offline_proposal", "model": "test-model", "latencyMs": 123,
                "usage": {"promptTokens": 30, "completionTokens": 20, "totalTokens": 50}, "callsRemaining": 19}

    def run_record(self, slot="baseline-a", ticks=600, **changes):
        start = datetime(2026, 10, 24, tzinfo=timezone.utc)
        duration = round(ticks * 1000 / 60)
        config = dict(store.BASELINE_CONFIG)
        if slot.startswith("candidate"):
            config["jumpLeadMs"] = 190
        record = {"slot": slot, "runId": "run_" + slot, "protocol": "native60-v1",
                  "seed": store.SLOTS[slot]["seed"], "maxTicks": 3600, "config": config,
                  "startedAt": start.isoformat(), "endedAt": (start + timedelta(milliseconds=duration)).isoformat(),
                  "wallDurationMs": duration, "ticks": ticks, "score": ticks // 10, "outcome": "collision",
                  "canvasWidth": 600, "maxFrameGapMs": 20,
                  "actions": [{"tick": 100, "tMs": 1667, "action": "jump", "score": 10, "reason": "Visible obstacle."}],
                  "frames": [{"tick": ticks, "tMs": duration, "score": ticks // 10, "image": png()}],
                  "course": [{"index": 0, "tick": 100, "type": "CACTUS_SMALL", "size": 1,
                              "y": 105, "width": 17, "gap": 150, "speedOffset": 0}]}
        record.update(changes)
        return record

    def save(self, slot="baseline-a", **changes):
        result = store.save_run(self.identifier, self.run_record(slot, **changes))
        self.assertTrue(result["ok"], result)
        return result["experiment"]

    def baselines(self):
        for slot in ("baseline-a", "repeat-a", "baseline-b"):
            self.save(slot)

    def coached(self):
        self.baselines()
        result = store.propose_change(self.identifier, {})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["experiment"]["proposal"]["status"], "succeeded")
        return result["experiment"]

    def disk_record(self):
        path = self.directory / (self.identifier + ".json")
        return path, json.loads(path.read_text())

    def test_creation_is_fixed_private_bounded_and_has_no_network(self):
        self.assertEqual(self.experiment["plan"], store.PLAN)
        self.assertEqual(self.experiment["comparison"]["status"], "awaiting_baselines")
        self.assertEqual(self.experiment["runs"], {})
        self.assertEqual(self.experiment["proposal"], {"status": "idle"})
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.directory / (self.identifier + ".json")).stat().st_mode), 0o600)
        self.assertFalse(store.create_experiment({"seed": 99})["ok"])
        with patch.object(store, "MAX_EXPERIMENTS", 1):
            self.assertEqual(store.create_experiment({})["code"], "storage_full")
        self.network.assert_not_called()
        self.keychain.assert_not_called()
        self.propose.assert_not_called()
        self.status.assert_not_called()

    def test_saved_slot_is_immutable_and_retry_is_idempotent(self):
        record = self.run_record()
        self.assertTrue(store.save_run(self.identifier, record)["ok"])
        before = (self.directory / (self.identifier + ".json")).read_bytes()
        again = store.save_run(self.identifier, deepcopy(record))
        self.assertTrue(again["alreadySaved"])
        self.assertEqual(store.save_run(self.identifier, {**record, "score": 99})["code"], "conflict")
        self.assertEqual(before, (self.directory / (self.identifier + ".json")).read_bytes())
        record["actions"][0]["reason"] = "caller mutation"
        saved = store.get_experiment(self.identifier)["experiment"]["runs"]["baseline-a"]
        self.assertEqual(saved["actions"][0]["reason"], "Visible obstacle.")

    def test_custom_courses_survive_save_coaching_comparison_and_recovery(self):
        seeds = {"a": 1002021, "b": 1003030}
        self.experiment = store.create_experiment({"seeds": seeds})["experiment"]
        self.identifier = self.experiment["id"]
        for slot in ("baseline-a", "repeat-a", "baseline-b"):
            self.save(slot, seed=seeds[slot[-1]])
        self.assertEqual(store.save_run(self.identifier, self.run_record())["code"], "invalid_run")
        self.propose.return_value = {"ok": False, "message": "Offline test failure"}
        self.assertEqual(store.propose_change(self.identifier, {})["experiment"]["proposal"]["status"], "failed")
        copied = store.reuse_baselines(self.identifier, {})["experiment"]
        self.identifier = copied["id"]
        self.assertEqual(copied["plan"]["seeds"], seeds)
        self.propose.return_value = self.answer()
        self.assertTrue(store.propose_change(self.identifier, {})["ok"])
        sent = self.propose.call_args.args[0]
        self.assertEqual([course["seed"] for course in sent["courses"]], list(seeds.values()))
        for slot in ("candidate-a", "candidate-b"):
            self.save(slot, seed=seeds[slot[-1]])
        saved = store.get_experiment(self.identifier)["experiment"]
        self.assertEqual(saved["plan"]["seeds"], seeds)
        self.assertEqual(saved["comparison"]["status"], "complete")
        self.assertEqual([pair["seed"] for pair in saved["comparison"]["pairs"]], list(seeds.values()))

    def test_invalid_or_duplicate_course_seeds_are_rejected(self):
        for seeds in ({"a": 1, "b": 1}, {"a": True, "b": 2}, {"a": -1, "b": 2},
                      {"a": 2**32, "b": 2}, {"a": 1}, {"a": 1, "b": 2, "c": 3}, []):
            with self.subTest(seeds=seeds):
                self.assertFalse(store.create_experiment({"seeds": seeds})["ok"])

    def test_reserved_test_and_mixed_courses_cannot_contact_provider(self):
        import learning_store
        reserved = learning_store.PLAN["evaluationCases"][0]["seeds"]
        for seeds in (reserved, {"a": reserved["a"], "b": 1002021}):
            self.identifier = store.create_experiment({"seeds": seeds})["experiment"]["id"]
            for slot in ("baseline-a", "repeat-a", "baseline-b"):
                self.save(slot, seed=seeds[slot[-1]])
            result = store.propose_change(self.identifier, {})
            self.assertEqual(result["code"], "evaluation_reserved")
            saved = store.get_experiment(self.identifier)["experiment"]
            self.assertFalse(saved["reviewAllowed"])
            self.assertTrue(saved["evaluationReserved"])
            self.assertEqual(saved["proposal"]["status"], "idle")
        self.propose.assert_not_called()
        self.status.assert_not_called()
        self.network.assert_not_called()
        self.keychain.assert_not_called()

    def test_wrong_parameters_types_and_extra_claims_rejected(self):
        good = self.run_record()
        variants = [
            {"seed": 1}, {"seed": True}, {"maxTicks": 1}, {"protocol": "realtime"},
            {"config": {**store.BASELINE_CONFIG, "jumpLeadMs": 190}},
            {"config": {**store.BASELINE_CONFIG, "duckExtraMs": False}},
            {"config": {**store.BASELINE_CONFIG, "execute": "code"}},
            {"ticks": 3601}, {"score": True}, {"canvasWidth": 599}, {"maxFrameGapMs": float("nan")},
            {"outcome": "winner"}, {"outcome": "time_limit"}, {"wallDurationMs": -1},
            {"startedAt": "2026-10-24T00:00:00"}, {"endedAt": "2025-10-24T00:00:00+00:00"},
            {"runId": "../unsafe"}, {"runId": "mbt_secret"}, {"training": True},
        ]
        for changes in variants:
            with self.subTest(changes=changes):
                self.assertEqual(store.save_run(self.identifier, {**good, **changes})["code"], "invalid_run")
        self.assertEqual(store.get_experiment(self.identifier)["experiment"]["runs"], {})

    def test_recording_evidence_bounds_and_chronology(self):
        good = self.run_record()
        action, frame, obstacle = good["actions"][0], good["frames"][0], good["course"][0]
        variants = [
            {"actions": [action] * 1201}, {"frames": [frame] * 61}, {"course": [obstacle] * 301},
            {"actions": [{**action, "tick": 601}]}, {"actions": [{**action, "tMs": 1666}]},
            {"actions": [{**action, "action": "execute"}]}, {"actions": [{**action, "reason": "mbt_private"}]},
            {"actions": [{**action, "score": 61}]}, {"actions": [{**action, "reason": "x" * 201}]},
            {"frames": [{**frame, "image": "data:image/png;base64,bad"}]},
            {"frames": [{**frame, "tick": 100, "tMs": 1667}, {**frame, "tick": 0, "tMs": 0}]},
            {"course": [{**obstacle, "index": 1}]}, {"course": [{**obstacle, "speedOffset": float("inf")}]},
            {"course": [{**obstacle, "type": "INVENTED"}]}, {"course": [{**obstacle, "extra": True}]},
        ]
        for changes in variants:
            with self.subTest(changes=list(changes)):
                self.assertEqual(store.save_run(self.identifier, {**good, **changes})["code"], "invalid_run")

    def test_duplicate_run_ids_across_slots_rejected(self):
        self.save()
        self.assertEqual(store.save_run(self.identifier, self.run_record("repeat-a", runId="run_baseline-a"))["code"], "conflict")

    def test_candidate_requires_saved_valid_proposal(self):
        self.assertEqual(store.save_run(self.identifier, self.run_record("candidate-a"))["code"], "invalid_run")
        self.baselines()
        self.assertEqual(store.save_run(self.identifier, self.run_record("candidate-a"))["code"], "invalid_run")
        self.propose.assert_not_called()

    def test_repeated_baseline_requires_exact_ticks_score_actions_and_course(self):
        self.save("baseline-a")
        self.save("baseline-b")
        repeat = self.run_record("repeat-a")
        repeat["actions"][0]["reason"] = "Different recorded decision."
        result = store.save_run(self.identifier, repeat)
        self.assertTrue(result["ok"])
        self.assertEqual(result["experiment"]["comparison"]["status"], "baseline_mismatch")
        self.assertEqual(store.propose_change(self.identifier, {})["code"], "baselines_required")
        self.propose.assert_not_called()

    def test_interrupted_and_lagged_evidence_remains_saved_but_inconclusive(self):
        self.save("baseline-a", outcome="hidden")
        self.save("repeat-a", outcome="hidden")
        result = self.save("baseline-b")
        self.assertEqual(result["comparison"]["status"], "inconclusive")
        self.assertEqual(store.propose_change(self.identifier, {})["code"], "baselines_required")
        for changes in ({"maxFrameGapMs": 251}, {"frames": []}, {"course": []}):
            self.assertFalse(store._complete(self.run_record(**changes)))

    def test_save_get_list_never_call_provider(self):
        self.baselines()
        self.assertEqual(store.get_experiment(self.identifier)["experiment"]["comparison"]["status"], "awaiting_proposal")
        listing = store.list_experiments()
        self.assertTrue(listing["ok"])
        self.assertEqual(listing["experiments"][0]["runCount"], 3)
        self.assertEqual(listing["unavailable"], [])
        self.propose.assert_not_called()
        self.status.assert_not_called()

    def test_proposal_reads_authoritative_bounded_data_without_engine_audit(self):
        actions = [{"tick": tick, "tMs": round(tick * 1000 / 60), "action": "run", "score": 0, "reason": "Observed."}
                   for tick in range(70)]
        frames = [{"tick": tick, "tMs": round(tick * 1000 / 60), "score": 0, "image": png()} for tick in range(10)]
        for slot in ("baseline-a", "repeat-a", "baseline-b"):
            self.save(slot, actions=actions, frames=frames)
        self.assertFalse(store.propose_change(self.identifier, {"courses": []})["ok"])
        result = store.propose_change(self.identifier, {})
        self.assertEqual(result["experiment"]["proposal"]["status"], "succeeded")
        evidence = self.propose.call_args.args[0]
        self.assertEqual(evidence["runId"], self.identifier)
        self.assertEqual([course["slot"] for course in evidence["courses"]], ["baseline-a", "baseline-b"])
        for course in evidence["courses"]:
            self.assertEqual(course["actions"], actions[-60:])
            self.assertEqual(course["frames"], frames[-6:])
            self.assertTrue(course["actionsTruncated"])
            self.assertTrue(course["framesTruncated"])
            self.assertNotIn("course", course)
        self.assertNotIn("CACTUS", json.dumps(evidence))
        self.assertNotIn("speedOffset", json.dumps(evidence))
        store.mubit_bridge._validate_proposal(evidence)
        self.assertEqual(store.get_experiment(self.identifier)["experiment"]["proposal"], result["experiment"]["proposal"])

    def test_proposal_is_one_attempt_and_failed_result_is_persisted(self):
        self.baselines()
        self.propose.return_value = {"ok": False, "message": "The provider rejected the request.",
            "requestId": "mreq_failed", "httpStatus": 400, "errorCode": "unsupported_parameter",
            "latencyMs": 100, "usage": None, "callsRemaining": 19}
        first = store.propose_change(self.identifier, {})["experiment"]
        self.assertEqual(first["proposal"]["status"], "failed")
        self.assertEqual(first["proposal"]["httpStatus"], 400)
        self.assertTrue(store.propose_change(self.identifier, {})["alreadyRequested"])
        self.assertEqual(store.get_experiment(self.identifier)["experiment"]["proposal"], first["proposal"])
        self.propose.assert_called_once()
        self.assertEqual(store.save_run(self.identifier, self.run_record("candidate-a"))["code"], "invalid_run")

    def test_invalid_provider_change_and_secret_diagnostics_never_persist(self):
        self.baselines()
        self.propose.return_value = {**self.answer(), "value": 999, "rationale": "mbt_secret"}
        result = store.propose_change(self.identifier, {})["experiment"]
        self.assertEqual(result["proposal"]["status"], "failed")
        self.assertNotIn("result", result["proposal"])
        self.assertNotIn("mbt_", json.dumps(result))

    def test_concurrent_proposals_do_not_duplicate_and_pending_survives_read(self):
        self.baselines()
        started, release = threading.Event(), threading.Event()
        def hold(payload):
            started.set()
            self.assertTrue(release.wait(3))
            return self.answer()
        self.propose.side_effect = hold
        results = []
        worker = threading.Thread(target=lambda: results.append(store.propose_change(self.identifier, {})))
        worker.start()
        try:
            self.assertTrue(started.wait(3))
            pending = store.get_experiment(self.identifier)["experiment"]["proposal"]
            self.assertEqual(pending["status"], "pending")
            retry = store.propose_change(self.identifier, {})
            self.assertTrue(retry["alreadyRequested"])
            self.assertEqual(retry["experiment"]["proposal"], pending)
            self.propose.assert_called_once()
        finally:
            release.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results[0]["experiment"]["proposal"]["status"], "succeeded")

    def test_failed_final_write_leaves_pending_unknown_without_resend(self):
        self.baselines()
        original_write = store._write
        def fail_final(record, **options):
            if record["proposal"]["status"] == "succeeded":
                raise OSError("secret diagnostic should not be returned")
            return original_write(record, **options)
        with patch.object(store, "_write", side_effect=fail_final):
            self.assertEqual(store.propose_change(self.identifier, {})["code"], "storage_unavailable")
        self.assertEqual(store.get_experiment(self.identifier)["experiment"]["proposal"]["status"], "pending")
        self.assertEqual(store.reuse_baselines(self.identifier, {})["code"], "reuse_not_allowed")
        self.assertTrue(store.propose_change(self.identifier, {})["alreadyRequested"])
        self.propose.assert_called_once()

    def failed_review(self):
        self.baselines()
        self.propose.return_value = {"ok": False, "message": "Mubit reached the response limit.",
            "requestId": "mreq_failed", "latencyMs": 100, "callsRemaining": 19,
            "validationReason": "truncated_response", "finishReason": "length", "contentLength": 0,
            "usage": {"promptTokens": 6413, "completionTokens": 700, "totalTokens": 7113}}
        result = store.propose_change(self.identifier, {})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["experiment"]["proposal"]["status"], "failed")
        return result["experiment"]

    def test_failure_preserves_safe_diagnostics_and_usage(self):
        failed = self.failed_review()
        saved = store.get_experiment(self.identifier)["experiment"]["proposal"]
        self.assertEqual(saved, failed["proposal"])
        self.assertEqual(saved["validationReason"], "truncated_response")
        self.assertEqual(saved["finishReason"], "length")
        self.assertEqual(saved["contentLength"], 0)
        self.assertEqual(saved["usage"]["totalTokens"], 7113)

    def test_failed_review_can_copy_baselines_to_new_experiment_without_provider_call(self):
        failed = self.failed_review()
        original_path, _ = self.disk_record()
        original_bytes = original_path.read_bytes()
        self.propose.reset_mock()
        self.status.reset_mock()
        result = store.reuse_baselines(self.identifier, {})
        self.assertTrue(result["ok"], result)
        copied = result["experiment"]
        self.assertNotEqual(copied["id"], self.identifier)
        self.assertEqual(copied["baselineSourceExperimentId"], self.identifier)
        self.assertEqual(copied["runs"], failed["runs"])
        self.assertEqual(copied["proposal"], {"status": "idle"})
        self.assertEqual(copied["comparison"]["status"], "awaiting_proposal")
        self.assertEqual(original_path.read_bytes(), original_bytes)
        self.assertEqual(store.get_experiment(copied["id"])["experiment"], copied)
        self.propose.assert_not_called()
        self.status.assert_not_called()
        listing = store.list_experiments()
        row = next(row for row in listing["experiments"] if row["id"] == copied["id"])
        self.assertEqual(row["baselineSourceExperimentId"], self.identifier)
        self.propose.return_value = self.answer()
        self.assertEqual(store.propose_change(copied["id"], {})["experiment"]["proposal"]["status"], "succeeded")
        self.assertTrue(store.propose_change(copied["id"], {})["alreadyRequested"])
        self.propose.assert_called_once()
        self.assertEqual(original_path.read_bytes(), original_bytes)

    def test_baseline_reuse_refuses_client_records_and_nonfailed_proposals(self):
        self.assertEqual(store.reuse_baselines(self.identifier, {})["code"], "reuse_not_allowed")
        self.assertEqual(store.reuse_baselines(self.identifier, {"runs": {}})["code"], "invalid_request")
        self.coached()
        self.propose.reset_mock()
        self.assertEqual(store.reuse_baselines(self.identifier, {})["code"], "reuse_not_allowed")
        self.propose.assert_not_called()
        self.assertEqual(len(store.list_experiments()["experiments"]), 1)

    def test_baseline_reuse_preserves_corrupt_source_and_storage_limit(self):
        self.failed_review()
        self.propose.reset_mock()
        with patch.object(store, "MAX_EXPERIMENTS", 1):
            self.assertEqual(store.reuse_baselines(self.identifier, {})["code"], "storage_full")
        path, record = self.disk_record()
        record["runs"]["repeat-a"]["score"] += 1
        path.write_text(json.dumps(record))
        raw = path.read_bytes()
        self.assertEqual(store.reuse_baselines(self.identifier, {})["code"], "unavailable")
        self.assertEqual(path.read_bytes(), raw)
        self.propose.assert_not_called()

    def test_copied_baseline_provenance_must_be_valid_and_complete(self):
        self.failed_review()
        for source_id in ("../outside", "mbt_secret", self.identifier, None):
            with self.subTest(source_id=source_id):
                path, record = self.disk_record()
                record["baselineSourceExperimentId"] = source_id
                path.write_text(json.dumps(record))
                self.assertEqual(store.get_experiment(self.identifier)["code"], "unavailable")
                record.pop("baselineSourceExperimentId")
                path.write_text(json.dumps(record))
        path, record = self.disk_record()
        record["baselineSourceExperimentId"] = "exp_other"
        record["proposal"] = {"status": "idle"}
        record["runs"].pop("baseline-b")
        path.write_text(json.dumps(record))
        self.assertEqual(store.get_experiment(self.identifier)["code"], "unavailable")

    def test_failure_diagnostic_allowlist_rejects_reflected_or_oversized_values(self):
        self.baselines()
        self.propose.return_value = {"ok": False, "message": "Review failed.", "requestId": "mreq_failed",
            "latencyMs": 1, "callsRemaining": 19, "validationReason": "mbt_secret",
            "finishReason": "mbt_secret", "contentLength": 10**30}
        result = store.propose_change(self.identifier, {})
        self.assertTrue(result["ok"])
        proposal = result["experiment"]["proposal"]
        self.assertNotIn("validationReason", proposal)
        self.assertNotIn("finishReason", proposal)
        self.assertNotIn("contentLength", proposal)
        self.assertNotIn("mbt_", json.dumps(proposal))

    def test_failed_intent_write_never_sends_request(self):
        self.baselines()
        with patch.object(store, "_write", side_effect=OSError()):
            self.assertEqual(store.propose_change(self.identifier, {})["code"], "storage_unavailable")
        self.propose.assert_not_called()

    def test_comparison_is_derived_on_two_matching_courses_and_never_changes_default(self):
        self.coached()
        self.save("candidate-a", ticks=720)
        experiment = self.save("candidate-b", ticks=660)
        comparison = experiment["comparison"]
        self.assertTrue(comparison["comparable"])
        self.assertEqual(comparison["status"], "complete")
        self.assertEqual(comparison["aggregate"]["conclusion"], "improved_on_both")
        self.assertEqual(comparison["aggregate"]["scoreDelta"], 18)
        self.assertEqual(comparison["aggregate"]["tickDelta"], 180)
        self.assertEqual(experiment["plan"]["baselineConfig"], store.BASELINE_CONFIG)
        self.assertFalse(comparison["defaultPolicyChanged"])
        self.assertFalse(comparison["modelTraining"])
        self.assertNotIn("comparison", self.disk_record()[1])

    def test_candidate_may_have_extra_course_evidence_after_baseline_ends(self):
        self.coached()
        candidate = self.run_record("candidate-a", ticks=720)
        candidate["course"].append({**candidate["course"][0], "index": 1, "tick": 650, "gap": 200})
        self.assertTrue(store.save_run(self.identifier, candidate)["ok"])
        experiment = self.save("candidate-b", ticks=720)
        self.assertTrue(experiment["comparison"]["comparable"])

    def test_changed_common_course_evidence_is_inconclusive(self):
        self.coached()
        candidate = self.run_record("candidate-a", ticks=720)
        candidate["course"][0]["gap"] += 1
        self.assertTrue(store.save_run(self.identifier, candidate)["ok"])
        experiment = self.save("candidate-b", ticks=720)
        self.assertEqual(experiment["comparison"]["status"], "inconclusive")
        self.assertIsNone(experiment["comparison"]["aggregate"])
        self.assertFalse(experiment["comparison"]["pairs"][0]["comparable"])

    def test_shorter_candidate_cannot_drop_shared_course_events(self):
        self.coached()
        candidate = self.run_record("candidate-a", ticks=500, course=[])
        self.assertTrue(store.save_run(self.identifier, candidate)["ok"])
        experiment = self.save("candidate-b", ticks=500)
        self.assertFalse(experiment["comparison"]["comparable"])

    def test_corrupt_unknown_records_are_preserved_not_replaced(self):
        path, _ = self.disk_record()
        path.write_text('{"broken": true}')
        raw = path.read_bytes()
        self.assertEqual(store.get_experiment(self.identifier)["code"], "unavailable")
        self.assertEqual(store.save_run(self.identifier, self.run_record())["code"], "unavailable")
        self.assertEqual(store.propose_change(self.identifier, {})["code"], "unavailable")
        listing = store.list_experiments()
        self.assertEqual(listing["experiments"], [])
        self.assertEqual(listing["unavailable"][0]["id"], self.identifier)
        self.assertEqual(path.read_bytes(), raw)
        self.propose.assert_not_called()

    def test_duplicate_json_keys_and_tampered_saved_proposal_are_unavailable(self):
        path, record = self.disk_record()
        path.write_text(json.dumps(record)[:-1] + ', "schemaVersion": 1}')
        self.assertEqual(store.get_experiment(self.identifier)["code"], "unavailable")
        path.write_text(json.dumps(record))
        record["proposal"] = {"status": "succeeded", "result": {"value": 190}}
        path.write_text(json.dumps(record))
        self.assertEqual(store.get_experiment(self.identifier)["code"], "unavailable")

    def test_symbolic_links_and_nonregular_records_refused(self):
        path, _ = self.disk_record()
        path.unlink()
        outside = Path(self.temp.name) / "outside.json"
        outside.write_text("private outside file")
        path.symlink_to(outside)
        self.assertEqual(store.get_experiment(self.identifier)["code"], "unavailable")
        self.assertEqual(outside.read_text(), "private outside file")
        path.unlink()
        os.mkfifo(path)
        self.assertEqual(store.get_experiment(self.identifier)["code"], "unavailable")

    def test_symlink_directory_is_not_followed(self):
        alternate = Path(self.temp.name) / "linked"
        alternate.symlink_to(self.directory, target_is_directory=True)
        with patch.object(store, "EXPERIMENTS_DIR", alternate):
            self.assertEqual(store.create_experiment({})["code"], "storage_unavailable")
            self.assertEqual(store.list_experiments()["code"], "storage_unavailable")

    def test_invalid_identifiers_do_not_escape_directory(self):
        for identifier in ("../outside", "", "mbt_secret", None):
            with self.subTest(identifier=identifier):
                self.assertEqual(store.get_experiment(identifier)["code"], "invalid_id")


if __name__ == "__main__":
    unittest.main()
