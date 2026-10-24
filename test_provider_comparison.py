"""Offline comparison custody tests: fake keys/replies, never live requests."""
from contextlib import contextmanager
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import provider_comparison as store


DIRECT_KEY = "sk-" + "FAKE_OFFLINE_DIRECT_KEY_" * 3
MUBIT_KEY = "mbt_" + "FAKE_OFFLINE_MUBIT_KEY_" * 3


class Response:
    def __init__(self, payload, url, *, headers=None, raw=None, code=200):
        self.raw = json.dumps(payload).encode() if raw is None else raw
        self.url, self.code = url, code
        self.headers = headers or {"x-middleware-request-id": "mreq_fixture", "x-request-id": "req_fixture"}

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        pass

    def geturl(self):
        return self.url

    def getcode(self):
        return self.code

    def read(self, length):
        return self.raw[:length]


class ProviderComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(patch.stopall)
        self.root = Path(self.temp.name) / "comparisons"
        patch.object(store, "COMPARISONS_DIR", self.root).start()
        self.learning_dir = Path(self.temp.name) / "learning"
        self.learning_dir.mkdir()
        patch.object(store.learning_store, "LEARNING_DIR", self.learning_dir).start()
        patch.dict(os.environ, {"DINO_COMPARISON_CASE_ID": ""}).start()
        self.case_id = "case_" + "1" * 32
        self.inventory = patch.object(store.learning_store, "list_cases", return_value={
            "ok": True, "cases": [{"id": self.case_id}], "unavailable": []}).start()
        patch.object(store.bridge, "_probe_lock", threading.Lock()).start()
        patch.object(store.bridge, "_state_lock", threading.Lock()).start()
        patch.object(store.bridge, "_attempts", 0).start()
        self.config = (MUBIT_KEY, store.MODEL, b"fixture-fingerprint")
        self.read_config = patch.object(store.bridge, "_read_config", side_effect=lambda: self.config).start()
        patch.object(store.bridge, "_sync_config_locked", return_value=None).start()
        self.ready = True
        self.connection = lambda: {"model": store.MODEL, "verified": self.ready, "ready": self.ready,
                                   "callsRemaining": 20 - store.bridge._attempts}
        patch.object(store.bridge, "_snapshot_locked", side_effect=self.connection).start()
        patch.object(store.bridge, "connection_status", side_effect=self.connection).start()
        baseline = dict(store.bridge.EXPERIMENT_BASELINE_CONFIG)
        self.current = {"source": "saved-tested-review", "runId": "review_" + "0" * 32,
            "taskVersion": "tested-review-text-v1", "protocol": "native60-v1", "frameRate": 60,
            "maxTicks": 3600, "baselineConfig": baseline, "candidateConfig": {**baseline, "duckExtraMs": 60},
            "originalProposal": {"parameter": "duckExtraMs", "value": 60,
                "rationale": "OFFLINE FIXTURE: test extra hold.", "caveat": "Only a hypothesis."},
            "courses": [{"course": label, "seed": seed,
                "baseline": {"score": score, "ticks": ticks, "outcome": "collision"},
                "candidate": {"score": score, "ticks": ticks, "outcome": "collision"}}
                for label, seed, score, ticks in (("a", 104729, 600, 3000), ("b", 130363, 200, 1200))]}
        self.current["runId"] = "review_" + store.review_task_store._input_hash(self.current)[:32]
        self.task = {"model": store.MODEL, "caseRevision": 3, "input": self.current,
                     "attempt": {"status": "succeeded", "completedAt": "2026-01-02T12:00:00+00:00"}}
        self.source = patch.object(store.review_task_store, "get_case_review", side_effect=lambda *a, **kw:
                                   {"ok": True, "task": deepcopy(self.task)}).start()
        self.keychain = patch.object(store.bridge, "_keychain_key", side_effect=AssertionError("No real key access")).start()
        self.posts = []
        self.requests = []
        self.real_open_request = store._open_request
        self.network = patch.object(store, "_open_request", side_effect=self.reply).start()

    def tearDown(self):
        self.keychain.assert_not_called()

    def answer(self):
        return {"assessment": "unchanged", "decision": "retain_baseline",
                "rationale": "Survival stayed at 3,000 and 1,200 steps; scores stayed at 600 and 200.",
                "caveat": "Two courses do not establish general improvement."}

    def completion(self, arm):
        completion = 30 if arm == "mubit" else 40
        return {"id": "chatcmpl_fixture", "model": store.MODEL, "system_fingerprint": "fp_fixture",
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(self.answer())}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": completion, "total_tokens": 100 + completion,
                      "prompt_tokens_details": {"cached_tokens": 10},
                      "completion_tokens_details": {"reasoning_tokens": 5}}}

    def reply(self, request):
        self.requests.append(request)
        if request.get_method() == "GET":
            self.assertEqual(request.full_url, store.DIRECT_MODEL_URL)
            self.assertFalse(self.root.exists())
            self.assertEqual(request.get_header("Authorization"), "Bearer " + DIRECT_KEY)
            return Response({"id": store.MODEL}, store.DIRECT_MODEL_URL)
        arm = "mubit" if request.full_url == store.MUBIT_URL else "direct"
        self.assertIn(request.full_url, (store.MUBIT_URL, store.DIRECT_URL))
        self.posts.append((arm, request))
        receipt = json.loads(self.receipt_path().read_text())
        self.assertEqual(receipt["status"], "pending")
        self.assertTrue(any(pair[arm] and pair[arm]["status"] == "pending" for pair in receipt["pairs"]))
        self.assertEqual(store.bridge._attempts, sum(name == "mubit" for name, _ in self.posts))
        return Response(self.completion(arm), request.full_url)

    def payload(self):
        return {"openaiKey": DIRECT_KEY, "expectedInputHash": store._preview()["inputHash"]}

    def receipt_path(self):
        return next(self.root.glob("comparison_*/receipt.json"))

    def test_readonly_preview_has_exact_grouped_prompt_and_no_network_or_storage(self):
        result = store.status()
        self.assertTrue(result["comparison"]["ready"])
        body = result["comparison"]["preview"]["body"]
        self.assertEqual(body["model"], store.MODEL)
        self.assertFalse(body["store"])
        self.assertEqual(body["max_completion_tokens"], 2000)
        self.assertEqual(json.loads(body["messages"][1]["content"]), {"runId": self.current["runId"]})
        self.assertEqual(json.loads(body["messages"][2]["content"]), self.current)
        self.assertNotIn("temperature", body)
        self.assertNotIn("seed", body)
        self.source.assert_called_with(self.case_id, read_connection=False)
        self.assertFalse(self.root.exists())
        self.network.assert_not_called()

    def test_empty_clone_is_not_started_without_creating_store_or_sending_requests(self):
        self.learning_dir.rmdir()
        result = store.status()
        self.assertTrue(result["ok"])
        self.assertEqual(result["comparison"]["status"], "not_started")
        self.assertFalse(result["comparison"]["ready"])
        self.assertIsNone(result["comparison"]["preview"])
        self.assertIn("complete a compact text review", result["comparison"]["message"])
        result = store.run({"openaiKey": DIRECT_KEY, "expectedInputHash": "0" * 64})
        self.assertEqual(result["code"], "source_not_ready")
        self.assertTrue(result["noInferenceSent"])
        self.inventory.assert_not_called()
        self.source.assert_not_called()
        self.network.assert_not_called()
        self.assertFalse(self.learning_dir.exists())
        self.assertFalse(self.root.exists())

    def test_latest_completed_eligible_review_is_selected_regardless_of_case_order(self):
        ids = ["case_" + str(index) * 32 for index in (1, 2, 3, 4)]
        tasks = {identifier: deepcopy(self.task) for identifier in ids}
        tasks[ids[0]]["attempt"]["completedAt"] = "2026-01-04T12:00:00+00:00"
        tasks[ids[1]]["attempt"]["completedAt"] = "2026-01-05T12:00:00+00:00"
        tasks[ids[2]]["attempt"]["status"] = "pending"
        tasks[ids[3]]["model"] = "another-model"
        self.inventory.return_value["cases"] = [{"id": identifier} for identifier in reversed(ids)]
        self.source.side_effect = lambda identifier, **unused: {"ok": True, "task": tasks[identifier]}
        self.assertEqual(store._preview()["caseId"], ids[1])
        self.assertEqual(self.source.call_count, 4)
        self.network.assert_not_called()

    def test_explicit_case_selection_never_falls_back_to_another_case(self):
        selected = "case_" + "9" * 32
        with patch.dict(os.environ, {"DINO_COMPARISON_CASE_ID": selected}):
            self.assertEqual(store._preview()["caseId"], selected)
            self.source.assert_called_with(selected, read_connection=False)
            self.task["attempt"]["status"] = "failed"
            result = store.status()
        self.assertEqual(result["comparison"]["status"], "not_started")
        self.assertFalse(result["comparison"]["ready"])
        self.assertIn("selected case is not eligible", result["comparison"]["message"])
        self.inventory.assert_not_called()
        self.network.assert_not_called()

    def test_no_compatible_review_is_setup_state_but_unreadable_inventory_is_unknown(self):
        self.task["input"]["courses"][0]["candidate"]["ticks"] -= 10
        view = store.status()["comparison"]
        self.assertEqual(view["status"], "not_started")
        self.assertFalse(view["ready"])
        self.inventory.return_value = {"ok": True, "cases": [], "unavailable": [{"id": None}]}
        result = store.status()
        self.assertFalse(result["ok"])
        self.assertEqual(result["comparison"]["status"], "unknown")
        self.network.assert_not_called()

    def test_measurement_scoring_uses_selected_input_and_rejects_partial_numeric_matches(self):
        current = deepcopy(self.current)
        for course, score, ticks in zip(current["courses"], (41, 93), (790, 1300)):
            for arm in ("baseline", "candidate"):
                course[arm].update(score=score, ticks=ticks)
        answer = {**self.answer(), "rationale": "Scores were 41 and 93; survival was 790 and 1,300 steps."}
        self.assertTrue(store._score(answer, current)["mentionsAllMeasurements"])
        self.assertFalse(store._score(self.answer(), current)["mentionsAllMeasurements"])
        for text in ("Scores 410 and 93; survival 790 and 1300.", "Scores 41.5 and 93; survival 790 and 1300."):
            self.assertFalse(store._score({**answer, "rationale": text}, current)["mentionsAllMeasurements"])
        # An unchanged survival label does not guarantee identical game scores.
        current["courses"][0]["candidate"]["score"] = 42
        self.assertFalse(store._score(answer, current)["mentionsAllMeasurements"])
        answer["rationale"] += " The candidate score was 42."
        self.assertTrue(store._score(answer, current)["mentionsAllMeasurements"])

    def test_run_scores_all_answers_against_the_frozen_selected_case(self):
        for course, score, ticks in zip(self.current["courses"], (41, 93), (790, 1300)):
            for arm in ("baseline", "candidate"):
                course[arm].update(score=score, ticks=ticks)
        answer = {**self.answer(), "rationale": "Scores 41 and 93; survival 790 and 1,300 steps."}
        with patch.object(self, "answer", return_value=answer):
            result = store.run(self.payload())
        self.assertTrue(result["ok"], result)
        for arm in ("mubit", "direct"):
            self.assertEqual(result["comparison"]["latest"]["summary"][arm]["measurementMentionCount"], 3)

    def test_success_has_identical_bodies_alternating_order_private_evidence_and_three_budgeted_calls(self):
        result = store.run(self.payload())
        self.assertTrue(result["ok"], result)
        view = result["comparison"]
        self.assertEqual(view["status"], "succeeded")
        self.assertFalse(view["ready"])
        self.assertEqual([arm for arm, _ in self.posts], ["mubit", "direct", "direct", "mubit", "mubit", "direct"])
        self.assertEqual(len({request.data for _, request in self.posts}), 1)
        for arm, request in self.posts:
            headers = {k.lower(): v for k, v in request.header_items()}
            self.assertEqual(headers["authorization"], "Bearer " + (MUBIT_KEY if arm == "mubit" else DIRECT_KEY))
            self.assertEqual(headers.get("x-middleware-record"), "off" if arm == "mubit" else None)
        self.assertEqual(len(self.requests), 7)
        self.assertEqual(store.bridge._attempts, 3)
        summary = view["latest"]["summary"]
        self.assertTrue(summary["allPairsValid"])
        self.assertTrue(summary["modelConsistencyVerified"])
        self.assertEqual(summary["mubit"]["meanTotalTokens"], 130)
        self.assertEqual(summary["direct"]["meanTotalTokens"], 140)
        self.assertEqual(summary["difference"]["totalTokens"], -10)
        self.assertEqual(summary["percentDifference"]["totalTokens"], -7.143)
        self.assertEqual(summary["mubit"]["measurementMentionCount"], 3)
        exported = store.export()
        self.assertTrue(exported["ok"])
        manifest = exported["comparison"]["export"]["manifest"]
        self.assertTrue(manifest["directAvailability"]["available"])
        self.assertEqual(manifest["bodyHash"], store._hash(manifest["body"]))
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
        for path in self.root.rglob("*"):
            if path.is_file():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                self.assertNotIn(DIRECT_KEY.encode(), path.read_bytes())
                self.assertNotIn(MUBIT_KEY.encode(), path.read_bytes())
        before = self.receipt_path().read_bytes()
        repeated = store.run(self.payload())
        self.assertTrue(repeated["alreadyRequested"])
        self.assertEqual(before, self.receipt_path().read_bytes())
        self.assertEqual(len(self.requests), 7)

    def test_invalid_key_and_changed_preview_send_nothing(self):
        payload = self.payload()
        for value in (MUBIT_KEY, "sk-short", DIRECT_KEY + "\n", "secret", "sk-" + "é" * 40):
            result = store.run({**payload, "openaiKey": value})
            self.assertFalse(result["ok"])
            self.assertTrue(result["noInferenceSent"])
        result = store.run({**payload, "expectedInputHash": "0" * 64})
        self.assertEqual(result["code"], "context_changed")
        self.assertFalse(self.root.exists())
        self.network.assert_not_called()
        self.assertEqual(store.bridge._attempts, 0)

    def test_model_preflight_rejection_never_creates_attempt_or_sends_inference_or_echoes_key(self):
        self.network.side_effect = HTTPError(store.DIRECT_MODEL_URL, 401, DIRECT_KEY, {}, io.BytesIO(DIRECT_KEY.encode()))
        result = store.run(self.payload())
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "direct_preflight_failed")
        self.assertTrue(result["noInferenceSent"])
        self.assertIsNone(result["comparison"]["latest"])
        self.assertEqual(result["directAvailability"]["httpStatus"], 401)
        self.assertNotIn(DIRECT_KEY, json.dumps(result))
        self.assertFalse(self.root.exists())
        self.assertEqual(store.bridge._attempts, 0)
        self.assertEqual(self.network.call_count, 1)

    def test_changed_input_during_preflight_rejects_before_freezing(self):
        original = self.reply
        def changed(request):
            response = original(request)
            self.task["input"]["originalProposal"]["rationale"] = "Changed after preview."
            return response
        self.network.side_effect = changed
        result = store.run(self.payload())
        self.assertEqual(result["code"], "context_changed")
        self.assertTrue(result["noInferenceSent"])
        self.assertEqual(len(self.requests), 1)
        self.assertFalse(self.root.exists())

    def test_insufficient_budget_and_busy_bridge_are_definite_no_send_without_lock_deadlock(self):
        store.bridge._attempts = 18
        original = store._reject
        def unlocked(*args):
            self.assertFalse(store.bridge._state_lock.locked())
            return original(*args)
        with patch.object(store, "_reject", side_effect=unlocked):
            result = store.run(self.payload())
        self.assertEqual(result["code"], "not_ready")
        store.bridge._attempts = 0
        store.bridge._probe_lock.acquire()
        try:
            result = store.run(self.payload())
            self.assertEqual(result["code"], "busy")
        finally:
            store.bridge._probe_lock.release()
        self.network.assert_not_called()

    def test_receipt_arriving_before_storage_commit_is_returned_outside_storage_lock(self):
        depth = 0
        original = store._storage_lock
        @contextmanager
        def tracked(**kwargs):
            nonlocal depth
            with original(**kwargs):
                depth += 1
                try:
                    yield
                finally:
                    depth -= 1
        def view():
            self.assertEqual(depth, 0)
            return {"ok": True, "comparison": {"status": "pending"}}
        with patch.object(store, "_storage_lock", side_effect=tracked), \
             patch.object(store, "_latest", side_effect=[(None, None), ({}, {"status": "pending"})]), \
             patch.object(store, "status", side_effect=view):
            result = store.run(self.payload())
        self.assertTrue(result["alreadyRequested"])
        self.assertEqual(len(self.requests), 1)
        self.assertFalse(self.posts)

    def test_timeout_preserves_uncertain_first_arm_and_never_retries(self):
        original = self.reply
        def timeout(request):
            if request.get_method() == "GET":
                return original(request)
            raise URLError("DO NOT ECHO " + DIRECT_KEY)
        self.network.side_effect = timeout
        result = store.run(self.payload())
        self.assertFalse(result["ok"])
        self.assertEqual(result["comparison"]["status"], "failed")
        receipt = result["comparison"]["latest"]
        self.assertEqual(receipt["pairs"][0]["mubit"]["status"], "uncertain")
        self.assertIsNone(receipt["pairs"][0]["direct"])
        self.assertIsNone(receipt["summary"]["difference"])
        self.assertNotIn(DIRECT_KEY, json.dumps(result))
        self.assertTrue(store.run(self.payload())["alreadyRequested"])
        self.assertEqual(self.network.call_count, 2)
        self.assertEqual(store.bridge._attempts, 1)

    def test_interrupt_keeps_pending_receipt_and_cannot_resume(self):
        original = self.reply
        def interrupt(request):
            if request.get_method() == "GET":
                return original(request)
            raise KeyboardInterrupt()
        self.network.side_effect = interrupt
        with self.assertRaises(KeyboardInterrupt):
            store.run(self.payload())
        view = store.status()["comparison"]
        self.assertEqual(view["status"], "pending")
        self.assertFalse(view["ready"])
        self.assertFalse(view["exportAvailable"])
        self.assertTrue(store.run(self.payload())["alreadyRequested"])
        self.assertEqual(self.network.call_count, 2)
        self.assertFalse(store.bridge._probe_lock.locked())

    def test_invalid_or_secret_reflecting_content_is_omitted_but_usage_preserved(self):
        original = self.reply
        def invalid(request):
            if request.get_method() == "GET":
                return original(request)
            payload = self.completion("mubit")
            payload["choices"][0]["message"]["content"] = json.dumps({**self.answer(), "rationale": DIRECT_KEY})
            payload["id"] = DIRECT_KEY
            payload["system_fingerprint"] = MUBIT_KEY
            return Response(payload, request.full_url)
        self.network.side_effect = invalid
        result = store.run(self.payload())
        arm = result["comparison"]["latest"]["pairs"][0]["mubit"]
        self.assertEqual(arm["status"], "failed")
        self.assertFalse(arm["score"]["jsonValid"])
        self.assertIsNone(arm["answer"])
        self.assertIsNone(arm["responseId"])
        self.assertIsNone(arm["systemFingerprint"])
        self.assertEqual(arm["usage"]["totalTokens"], 130)
        self.assertNotIn(DIRECT_KEY, self.receipt_path().read_text())
        self.assertNotIn(MUBIT_KEY, self.receipt_path().read_text())
        self.assertEqual(self.network.call_count, 2)

    def test_model_mismatch_and_unknown_usage_do_not_become_savings_claims(self):
        original = self.reply
        def mismatch(request):
            if request.get_method() == "GET":
                return original(request)
            result = original(request)
            payload = json.loads(result.raw)
            if request.full_url == store.DIRECT_URL:
                payload["model"] = "another-model"
                payload["usage"]["total_tokens"] = 1  # inconsistent, not zero usage
            result.raw = json.dumps(payload).encode()
            return result
        self.network.side_effect = mismatch
        result = store.run(self.payload())
        self.assertTrue(result["ok"])
        summary = result["comparison"]["latest"]["summary"]
        self.assertFalse(summary["modelConsistencyVerified"])
        self.assertIsNone(summary["difference"])
        self.assertIsNone(summary["percentDifference"])
        self.assertIsNone(summary["direct"]["meanTotalTokens"])
        self.assertEqual(summary["direct"]["meanPromptTokens"], 100)

    def test_wrong_but_valid_answer_is_saved_and_scored_without_fabricating_accuracy(self):
        wrong = {**self.answer(), "assessment": "mixed", "decision": "test_more", "rationale": "Unhelpful explanation."}
        with patch.object(self, "answer", return_value=wrong):
            result = store.run(self.payload())
        self.assertTrue(result["ok"])
        summary = result["comparison"]["latest"]["summary"]
        self.assertEqual(summary["mubit"]["jsonValidCount"], 3)
        self.assertEqual(summary["mubit"]["assessmentCorrectCount"], 0)
        self.assertEqual(summary["direct"]["decisionCorrectCount"], 0)
        self.assertEqual(summary["direct"]["measurementMentionCount"], 0)

    def test_completed_export_survives_live_source_unavailability_and_detects_tampering(self):
        result = store.run(self.payload())
        self.assertTrue(result["ok"])
        self.source.side_effect = AssertionError("Live source should not replace frozen evidence")
        self.assertTrue(store.status()["ok"])
        self.assertTrue(store.export()["ok"])
        path = self.receipt_path()
        data = json.loads(path.read_text())
        data["pairs"][0]["mubit"]["usage"]["totalTokens"] = 1
        path.write_text(json.dumps(data))
        result = store.status()
        self.assertFalse(result["ok"])
        self.assertEqual(result["comparison"]["status"], "unknown")
        self.assertFalse(result["comparison"]["exportAvailable"])

    def test_configuration_change_between_arms_stops_before_second_post(self):
        original = self.reply
        def change(request):
            result = original(request)
            if request.get_method() == "POST":
                self.config = (MUBIT_KEY, store.MODEL, b"changed-fingerprint")
            return result
        self.network.side_effect = change
        result = store.run(self.payload())
        self.assertFalse(result["ok"])
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(result["comparison"]["status"], "failed")

    def test_redirect_and_oversized_response_stop_without_retry(self):
        original = self.reply
        def oversized(request):
            if request.get_method() == "GET":
                return original(request)
            return Response({}, request.full_url, raw=b"x" * (store.MAX_RESPONSE_BYTES + 1))
        self.network.side_effect = oversized
        result = store.run(self.payload())
        self.assertEqual(result["comparison"]["latest"]["pairs"][0]["mubit"]["failure"]["code"], "invalid_response")
        self.assertEqual(self.network.call_count, 2)
        # The real opener disables redirect following for every endpoint.
        with patch.object(store, "build_opener") as opener:
            request = self.requests[0]
            self.real_open_request(request)
            self.assertIsInstance(opener.call_args.args[0], store.bridge._NoRedirect)
            opener.return_value.open.assert_called_once_with(request, timeout=30)


if __name__ == "__main__":
    unittest.main()
