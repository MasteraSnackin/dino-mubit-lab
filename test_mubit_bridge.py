"""Offline checks only: every HTTP request is replaced with a local double."""

import io
import json
import os
import struct
import threading
import unittest
import zlib
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

import mubit_bridge as bridge


FAKE_KEY = "mbt_unit_test_not_a_real_key"


class Response:
    def __init__(self, payload=None, *, raw=None, headers=None, url=None, code=200):
        self.raw = raw if raw is not None else json.dumps(
            payload if payload is not None else {"data": [{"id": "test-model"}]}
        ).encode()
        self.headers = {"x-middleware-request-id": "mreq_offline_test"} if headers is None else headers
        self.url = url or bridge.MODELS_URL
        self.code = code
        self.read_size = None

    def getcode(self):
        return self.code

    def geturl(self):
        return self.url

    def read(self, size):
        self.read_size = size
        return self.raw[:size]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.keychain = patch.object(bridge, "_keychain_key", return_value="")
        self.keychain.start()
        with bridge._state_lock:
            bridge._fingerprint = None
            bridge._state = None
            bridge._revision = 0
            bridge._attempts = 0
        self.transport = patch.object(bridge, "_open_request")
        self.open_request = self.transport.start()
        self.open_request.return_value = Response()

    def tearDown(self):
        self.transport.stop()
        self.keychain.stop()
        self.environment.stop()

    def configure(self, model="test-model"):
        os.environ["MUBIT_API_KEY"] = FAKE_KEY
        os.environ["DINO_MUBIT_MODEL"] = model

    def assert_no_secret(self, status):
        self.assertNotIn(FAKE_KEY, json.dumps(status))
        self.assertEqual(set(status), {
            "configured", "ready", "verified", "model", "message", "models", "requestId", "checkedAt", "callsRemaining"
        })

    def test_missing_key_and_status_never_open_network(self):
        for status in (bridge.connection_status(), bridge.check_connection()):
            self.assertFalse(status["configured"])
            self.assertFalse(status["verified"])
            self.assertIsNone(status["checkedAt"])
            self.assert_no_secret(status)
        self.open_request.assert_not_called()

    def test_configured_does_not_mean_verified(self):
        self.configure()
        status = bridge.connection_status()
        self.assertTrue(status["configured"])
        self.assertFalse(status["verified"])
        self.open_request.assert_not_called()

    def test_valid_probe_is_get_only_bounded_and_has_provenance(self):
        self.configure()
        response = Response({"data": [{"id": "z-model"}, {"id": "test-model"}, {"id": "test-model"}]})
        self.open_request.return_value = response
        status = bridge.check_connection()
        request = self.open_request.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.full_url, bridge.MODELS_URL)
        self.assertIsNone(request.data)
        self.assertEqual(request.get_header("Authorization"), "Bearer " + FAKE_KEY)
        self.assertEqual(response.read_size, bridge.MAX_RESPONSE_BYTES + 1)
        self.assertTrue(status["verified"])
        self.assertEqual(status["models"], ["test-model", "z-model"])
        self.assertEqual(status["requestId"], "mreq_offline_test")
        self.assertIsNotNone(status["checkedAt"])
        self.assert_no_secret(status)
        status["models"].append("changed-by-caller")
        self.assertNotIn("changed-by-caller", bridge.connection_status()["models"])

    def test_configuration_changes_invalidate_verified_state(self):
        self.configure()
        self.assertTrue(bridge.check_connection()["verified"])
        os.environ["DINO_MUBIT_MODEL"] = "another-model"
        status = bridge.connection_status()
        self.assertFalse(status["verified"])
        self.assertIsNone(status["checkedAt"])
        self.assertEqual(status["models"], [])
        os.environ.pop("MUBIT_API_KEY")
        self.assertFalse(bridge.connection_status()["configured"])

    def test_configuration_change_during_request_discards_result(self):
        self.configure()
        def change_config(request):
            os.environ["MUBIT_API_KEY"] = "mbt_other_dummy_key"
            return Response()
        self.open_request.side_effect = change_config
        status = bridge.check_connection()
        self.assertFalse(status["verified"])
        self.assertEqual(status["models"], [])
        self.assertIsNone(status["checkedAt"])
        self.assertIn("changed", status["message"])

    def test_invalid_key_does_not_leave_machine(self):
        for key in ("provider-key", "mbt_test\r\nHeader: value", "mbt_" + "x" * 4096):
            with self.subTest(key_kind=key[:4]):
                os.environ["MUBIT_API_KEY"] = key
                self.assertFalse(bridge.check_connection()["verified"])
        self.open_request.assert_not_called()

    def test_invalid_or_reflected_models_and_request_ids_are_rejected(self):
        self.configure()
        replies = [
            Response(headers={}),
            Response(headers={"x-middleware-request-id": FAKE_KEY}),
            Response(headers={"x-middleware-request-id": "mreq_\nunsafe"}),
            Response({"data": [{"id": FAKE_KEY}]}),
            Response({"data": [{"id": "x" * 161}]}),
            Response({"data": [{"id": "<script>"}]}),
            Response({"data": [None]}),
            Response({"data": []}, url="https://another.example/models"),
        ]
        for reply in replies:
            with self.subTest(reply_kind=reply.headers):
                self.open_request.return_value = reply
                status = bridge.check_connection()
                self.assertFalse(status["verified"])
                self.assertEqual(status["models"], [])
                self.assert_no_secret(status)

    def test_malformed_and_oversized_responses_fail_closed(self):
        self.configure()
        replies = [
            Response(raw=b"not json"),
            Response(payload=[]),
            Response(payload={"data": "not a list"}),
            Response(payload={"data": [{"id": "ok"}] * (bridge.MAX_MODELS + 1)}),
            Response(raw=b" " * (bridge.MAX_RESPONSE_BYTES + 1)),
            Response(headers={"x-middleware-request-id": "mreq_test", "Content-Length": str(bridge.MAX_RESPONSE_BYTES + 1)}),
            Response(headers={"x-middleware-request-id": "mreq_test", "Content-Length": "invalid"}),
        ]
        for reply in replies:
            with self.subTest(size=len(reply.raw)):
                self.open_request.return_value = reply
                status = bridge.check_connection()
                self.assertFalse(status["verified"])
                self.assert_no_secret(status)

    def test_http_network_and_unexpected_errors_do_not_echo_secrets(self):
        self.configure()
        for error in (
            HTTPError(bridge.MODELS_URL, 401, FAKE_KEY, {}, io.BytesIO(FAKE_KEY.encode())),
            HTTPError(bridge.MODELS_URL, 503, FAKE_KEY, {}, io.BytesIO(FAKE_KEY.encode())),
            URLError(FAKE_KEY), TimeoutError(FAKE_KEY), RuntimeError(FAKE_KEY),
        ):
            with self.subTest(error_type=type(error).__name__):
                self.open_request.side_effect = error
                status = bridge.check_connection()
                self.assertFalse(status["verified"])
                self.assert_no_secret(status)
        self.assertEqual(self.open_request.call_count, 5)

    def test_redirect_handler_refuses_without_forwarding_key(self):
        request = Request(bridge.MODELS_URL, headers={"Authorization": "Bearer " + FAKE_KEY})
        with self.assertRaises(HTTPError) as raised:
            bridge._NoRedirect().redirect_request(
                request, None, 302, "Found", {}, "https://other.example/"
            )
        self.assertEqual(raised.exception.code, 302)
        self.assertNotIn(FAKE_KEY, str(raised.exception))
        raised.exception.close()
        self.open_request.assert_not_called()

    def test_transport_uses_timeout_and_redirect_refusal(self):
        # No transport is called: inspect the real transport's opener arguments.
        self.transport.stop()
        try:
            with patch.object(bridge, "build_opener") as opener:
                request = Request(bridge.MODELS_URL)
                bridge._open_request(request)
                self.assertIsInstance(opener.call_args.args[0], bridge._NoRedirect)
                opener.return_value.open.assert_called_once_with(request, timeout=10)
                opener.reset_mock()
                request = Request(bridge.COMPLETIONS_URL, data=b"{}", method="POST")
                bridge._open_request(request)
                opener.return_value.open.assert_called_once_with(request, timeout=30)
        finally:
            self.open_request = self.transport.start()

    def test_concurrent_check_does_not_duplicate_request(self):
        self.configure()
        started = threading.Event()
        release = threading.Event()
        def hold_request(request):
            started.set()
            self.assertTrue(release.wait(2))
            return Response()
        self.open_request.side_effect = hold_request
        result = []
        worker = threading.Thread(target=lambda: result.append(bridge.check_connection()))
        worker.start()
        try:
            self.assertTrue(started.wait(2))
            status = bridge.check_connection()
            self.assertFalse(status["verified"])
            self.assertIn("already in progress", status["message"])
            self.assertEqual(self.open_request.call_count, 1)
        finally:
            release.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertTrue(result[0]["verified"])

    def test_configured_model_cannot_reflect_the_key(self):
        self.configure(FAKE_KEY)
        status = bridge.connection_status()
        self.assertIsNone(status["model"])
        self.assert_no_secret(status)

    def decision_payload(self):
        def chunk(kind, contents):
            return struct.pack(">I", len(contents)) + kind + contents + struct.pack(">I", zlib.crc32(kind + contents))
        png = (b"\x89PNG\r\n\x1a\n"
               + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
               + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\xff"))
               + chunk(b"IEND", b""))
        return {"screenshot": "data:image/png;base64," + bridge.base64.b64encode(png).decode(),
                "runId": "run_1", "step": 0, "score": 0}

    def review_payload(self):
        return {"runId": "run_1", "mode": "turn-based", "score": 10, "steps": 2,
                "actions": [{"action": "jump", "frames": 20, "score": 10}], "outcome": "collision"}

    def saved_review_payload(self, count=2):
        return {"source": "saved-local-run", "runId": "local_run_1", "mode": "realtime",
                "controller": "local-vision-rules", "policyVersion": "vision-v1",
                "score": 100, "durationMs": 10000, "outcome": "collision",
                "actionCount": count, "actionsTruncated": count > 40,
                "actions": [{"tMs": index * 10, "action": "jump" if index % 2 else "run",
                             "score": 10, "reason": "Visible obstacle reached the threshold."}
                            for index in range(max(0, count - 40), count)]}

    def completion(self, answer=None, **options):
        return Response({"choices": [{"message": {"content": json.dumps(answer or {
            "action": "jump", "frames": 20, "reason": "A cactus is approaching."
        })}}], "usage": {"prompt_tokens": 30, "completion_tokens": 20, "total_tokens": 50}},
                        url=bridge.COMPLETIONS_URL, **options)

    def connect_for_inference(self):
        self.configure()
        self.open_request.side_effect = None
        self.open_request.return_value = Response()
        self.assertTrue(bridge.check_connection()["ready"])
        self.open_request.reset_mock()

    def test_decision_sends_only_screenshot_context_and_returns_validated_action(self):
        self.connect_for_inference()
        self.open_request.return_value = self.completion()
        status = bridge.decide(self.decision_payload())
        self.assertTrue(status["ok"])
        self.assertEqual(status["action"], "jump")
        self.assertEqual(status["callsRemaining"], 19)
        self.assertEqual(status["usage"]["totalTokens"], 50)
        request = self.open_request.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(request.full_url, bridge.COMPLETIONS_URL)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(body["max_completion_tokens"], 700)
        self.assertNotIn("metadata", body)
        self.assertNotIn("store", body)
        self.assertEqual(body["messages"][1], {"role": "user", "content": json.dumps({"runId": "run_1"})})
        self.assertEqual(body["messages"][2]["content"][1]["type"], "image_url")
        self.assertEqual(json.loads(body["messages"][2]["content"][0]["text"])["mode"], "paused")
        self.assertNotIn(FAKE_KEY, request.data.decode())
        self.assertNotIn(FAKE_KEY, json.dumps(status))
        self.assertTrue(bridge.connection_status()["verified"])

    def test_review_sends_bounded_summary_and_returns_suggestions(self):
        self.connect_for_inference()
        self.open_request.return_value = self.completion({"summary": "The run ended at 10.", "suggestions": ["Jump earlier."]})
        result = bridge.review(self.review_payload())
        self.assertTrue(result["ok"])
        self.assertEqual(result["suggestions"], ["Jump earlier."])
        body = json.loads(self.open_request.call_args.args[0].data)
        self.assertEqual(body["max_completion_tokens"], 700)
        messages = body["messages"]
        self.assertEqual(json.loads(messages[2]["content"])["score"], 10)
        self.assertEqual(json.loads(messages[1]["content"]), {"runId": "run_1"})
        self.assertNotIn("metadata", body)
        self.assertNotIn("store", body)

    def test_saved_local_review_preserves_observations_without_inventing_frames(self):
        self.connect_for_inference()
        self.open_request.return_value = self.completion({
            "summary": "Only the action summary is available.", "suggestions": []})
        payload = self.saved_review_payload(45)
        result = bridge.review(payload)
        self.assertTrue(result["ok"])
        body = json.loads(self.open_request.call_args.args[0].data)
        self.assertEqual(json.loads(body["messages"][1]["content"]), {"runId": payload["runId"]})
        self.assertNotIn("metadata", body)
        self.assertNotIn("store", body)
        submitted = json.loads(body["messages"][2]["content"])
        self.assertEqual(submitted, payload)
        self.assertEqual(submitted["actionCount"], 45)
        self.assertTrue(submitted["actionsTruncated"])
        self.assertEqual(len(submitted["actions"]), 40)
        self.assertNotIn("frames", submitted)
        self.assertNotIn("screenshot", submitted)
        self.assertTrue(all("frames" not in action for action in submitted["actions"]))
        self.assertIn("no screenshots or action frame durations", body["messages"][0]["content"])
        self.assertIn("not a Mubit-controlled run", body["messages"][0]["content"])

    def test_saved_local_review_accepts_exact_excerpt_boundaries(self):
        for count in (0, 1, 40, 41, 1200):
            with self.subTest(count=count):
                payload = self.saved_review_payload(count)
                # The longest valid recording may exceed the short fixture duration.
                payload["durationMs"] = 20000
                messages = bridge._validate_review(payload)
                self.assertEqual(json.loads(messages[1]["content"]), payload)

    def test_saved_local_review_preserves_v2_policy_version(self):
        payload = {**self.saved_review_payload(), "policyVersion": "vision-v2"}
        messages = bridge._validate_review(payload)
        self.assertEqual(json.loads(messages[1]["content"])["policyVersion"], "vision-v2")

    def test_invalid_saved_local_reviews_never_send_or_consume_calls(self):
        self.connect_for_inference()
        good = self.saved_review_payload()
        action = good["actions"][0]
        invalid = [
            {**good, "source": "mubit"}, {**good, "runId": "../other"},
            {**good, "controller": "mubit"}, {**good, "policyVersion": "vision-v99"},
            {**good, "mode": "paused"}, {**good, "outcome": "unknown"},
            {**good, "score": True}, {**good, "durationMs": 3600001},
            {**good, "actionCount": True}, {**good, "actionCount": 1201},
            {**good, "actionCount": 1}, {**good, "actionsTruncated": 0},
            {**good, "actionsTruncated": True},
            {**self.saved_review_payload(41), "actionsTruncated": False},
            {**self.saved_review_payload(41), "actions": good["actions"]},
            {**good, "frames": []}, {**good, "screenshot": "invented"},
            {**good, "actions": [{**action, "frames": 20}, good["actions"][1]]},
            {**good, "actions": [{**action, "action": "wait"}, good["actions"][1]]},
            {**good, "actions": [{**action, "tMs": -1}, good["actions"][1]]},
            {**good, "actions": [{**action, "tMs": 10001}, good["actions"][1]]},
            {**good, "actions": list(reversed(good["actions"]))},
            {**good, "actions": [{**action, "score": 101}, good["actions"][1]]},
            {**good, "actions": [{**action, "reason": "x" * 201}, good["actions"][1]]},
            {**good, "actions": [{**action, "reason": "hidden\0text"}, good["actions"][1]]},
        ]
        for payload in invalid:
            with self.subTest(payload=payload):
                self.assertFalse(bridge.review(payload)["ok"])
        self.open_request.assert_not_called()
        self.assertEqual(bridge.connection_status()["callsRemaining"], 20)

    def test_decisions_and_reviews_share_stable_run_prefix_without_provider_storage(self):
        self.connect_for_inference()
        self.open_request.return_value = self.completion()
        payload = self.decision_payload()
        self.assertTrue(bridge.decide(payload)["ok"])
        self.assertTrue(bridge.decide({**payload, "step": 1})["ok"])
        self.open_request.return_value = self.completion({"summary": "Summary.", "suggestions": []})
        self.assertTrue(bridge.review(self.review_payload())["ok"])
        self.assertTrue(bridge.review({**self.saved_review_payload(), "runId": "run_1"})["ok"])
        self.assertTrue(bridge.review({**self.saved_review_payload(), "runId": "run_2"})["ok"])
        bodies = [json.loads(call.args[0].data) for call in self.open_request.call_args_list]
        groups = [body["messages"][1] for body in bodies]
        self.assertEqual(groups[:4], [{"role": "user", "content": json.dumps({"runId": "run_1"})}] * 4)
        self.assertEqual(groups[4], {"role": "user", "content": json.dumps({"runId": "run_2"})})
        for body in bodies:
            self.assertNotIn("metadata", body)
            self.assertNotIn("store", body)
            self.assertEqual(body["messages"][0]["role"], "system")
            self.assertEqual(body["messages"][2]["role"], "user")
        self.assertEqual(bodies[0]["messages"][0], bodies[1]["messages"][0])
        self.assertNotEqual(bodies[0]["messages"][2], bodies[1]["messages"][2])

    def test_inference_needs_key_and_explicit_model(self):
        self.assertFalse(bridge.decide(self.decision_payload())["ok"])
        os.environ["MUBIT_API_KEY"] = FAKE_KEY
        self.assertFalse(bridge.review(self.review_payload())["ok"])
        self.assertFalse(bridge.connection_status()["ready"])
        self.open_request.assert_not_called()
        self.assertEqual(bridge.connection_status()["callsRemaining"], 20)

    def proposal_payload(self):
        image = self.decision_payload()["screenshot"]
        return {"source": "saved-local-experiment", "runId": "exp_test", "protocol": "native60-v1",
                "mode": "realtime", "maxTicks": 3600, "baselineConfig": dict(bridge.EXPERIMENT_BASELINE_CONFIG),
                "courses": [{"slot": slot, "seed": seed, "score": 100, "ticks": 600, "outcome": "collision",
                    "actionCount": 1, "actionsTruncated": False,
                    "actions": [{"tick": 100, "tMs": 1667, "action": "jump", "score": 10, "reason": "Visible obstacle."}],
                    "frameCount": 1, "framesTruncated": False,
                    "frames": [{"tick": 600, "tMs": 10000, "score": 100, "image": image}]}
                    for slot, seed in (("baseline-a", 104729), ("baseline-b", 130363))]}

    def proposal_answer(self):
        return {"parameter": "jumpLeadMs", "value": 190, "rationale": "Try a slightly earlier jump.",
                "caveat": "Closing screenshots may not show the cause of the collision."}

    def compact_review_payload(self):
        return {
            "source": "saved-tested-review", "runId": "review_offline_test",
            "taskVersion": "tested-review-text-v1", "protocol": "native60-v1",
            "frameRate": 60, "maxTicks": 3600,
            "baselineConfig": dict(bridge.EXPERIMENT_BASELINE_CONFIG),
            "candidateConfig": {**bridge.EXPERIMENT_BASELINE_CONFIG, "jumpLeadMs": 190},
            "originalProposal": self.proposal_answer(),
            "courses": [{"course": label, "seed": seed,
                         "baseline": {"score": score, "ticks": ticks, "outcome": "collision"},
                         "candidate": {"score": score, "ticks": ticks, "outcome": "collision"}}
                        for label, seed, score, ticks in (("a", 1002021, 539, 2897),
                                                         ("b", 1003030, 211, 1272))],
        }

    def compact_review_answer(self):
        return {"assessment": "unchanged", "decision": "retain_baseline",
                "rationale": "Neither course survived for more steps after the change.",
                "caveat": "These two courses do not establish performance elsewhere."}

    def test_tested_review_sends_compact_text_with_stable_task_and_model(self):
        self.connect_for_inference()
        self.open_request.return_value = self.completion(self.compact_review_answer())
        original = self.compact_review_payload()
        first = bridge.review_tested_change(original, expected_model="test-model")
        self.assertTrue(first["ok"])
        self.assertEqual(first["model"], "test-model")
        self.assertEqual(first["requestId"], "mreq_offline_test")
        self.assertEqual(first["usage"]["totalTokens"], 50)
        self.assertEqual(first["callsRemaining"], 19)
        varied = self.compact_review_payload()
        varied["runId"] = "review_other_saved_case"
        varied["courses"][0]["seed"] = 1004041
        self.assertTrue(bridge.review_tested_change(varied, expected_model="test-model")["ok"])
        bodies = [json.loads(call.args[0].data) for call in self.open_request.call_args_list]
        self.assertEqual(bodies[0]["messages"][0], bodies[1]["messages"][0])
        for body, payload in zip(bodies, (original, varied)):
            self.assertEqual(body["model"], "test-model")
            self.assertEqual(body["max_completion_tokens"], 2000)
            self.assertEqual(body["response_format"], {"type": "json_object"})
            self.assertNotIn("metadata", body)
            self.assertNotIn("store", body)
            self.assertEqual(len(body["messages"]), 3)
            self.assertTrue(all(isinstance(message["content"], str) for message in body["messages"]))
            self.assertEqual(json.loads(body["messages"][1]["content"]), {"runId": payload["runId"]})
            sent = body["messages"][2]["content"]
            self.assertEqual(json.loads(sent), payload)
            self.assertLessEqual(len(sent.encode("utf-8")), 8192)
            for excluded in ("approvedCorrection", "expectedAssessment", "image_url", "actions", FAKE_KEY):
                self.assertNotIn(excluded, sent)

    def test_tested_review_preserves_valid_but_incorrect_assessment_for_scoring(self):
        self.connect_for_inference()
        answer = {**self.compact_review_answer(), "assessment": "improved_on_both",
                  "decision": "consider_candidate", "rationale": "The candidate improved both courses."}
        self.open_request.return_value = self.completion(answer)
        result = bridge.review_tested_change(self.compact_review_payload(), expected_model="test-model")
        self.assertTrue(result["ok"])
        self.assertEqual({key: result[key] for key in answer}, answer)
        self.open_request.assert_called_once()

    def test_tested_review_rejects_unapproved_input_fields_credentials_and_images(self):
        self.connect_for_inference()
        variants = []
        for key, value in (("approvedCorrection", {"assessment": "unchanged"}),
                           ("expectedAssessment", "unchanged"), ("screenshot", "data:image/png;base64,fixture"),
                           ("source", "browser"), ("taskVersion", "unrecognised-task"),
                           ("protocol", "different-protocol")):
            variants.append({**self.compact_review_payload(), key: value})
        for key, value in (("actions", []), ("enginePositions", []), ("frames", [])):
            payload = self.compact_review_payload()
            payload["courses"][0][key] = value
            variants.append(payload)
        for secret in ("mbt_secret_fixture", "sk-secret-credential", FAKE_KEY):
            payload = self.compact_review_payload()
            payload["originalProposal"]["rationale"] = secret
            variants.append(payload)
        for payload in variants:
            with self.subTest(fields=list(payload), rationale=payload["originalProposal"]["rationale"]):
                self.assertFalse(bridge.review_tested_change(payload)["ok"])
        self.open_request.assert_not_called()
        self.assertEqual(bridge.connection_status()["callsRemaining"], 20)

    def test_tested_review_rejects_inconsistent_configs_and_invalid_course_measurements(self):
        self.connect_for_inference()
        variants = []
        for key, value in (("frameRate", True), ("frameRate", 30), ("maxTicks", 3601)):
            variants.append({**self.compact_review_payload(), key: value})
        for config, key, value in (("baselineConfig", "duckExtraMs", False),
                                    ("candidateConfig", "duckExtraMs", False),
                                    ("candidateConfig", "jumpLeadMs", 170),
                                    ("candidateConfig", "duckLeadMs", 350)):
            payload = self.compact_review_payload()
            payload[config][key] = value
            variants.append(payload)
        for key, value in (("seed", True), ("seed", -1), ("seed", 2**32), ("course", "b")):
            payload = self.compact_review_payload()
            payload["courses"][0][key] = value
            variants.append(payload)
        payload = self.compact_review_payload()
        payload["courses"][1]["seed"] = payload["courses"][0]["seed"]
        variants.append(payload)
        for key, value in (("ticks", True), ("ticks", 0), ("ticks", 3601), ("score", True),
                           ("score", -1), ("score", 1000001), ("outcome", "time_limit"),
                           ("outcome", "cancelled"), ("durationMs", 1000)):
            payload = self.compact_review_payload()
            payload["courses"][0]["candidate"][key] = value
            variants.append(payload)
        for payload in variants:
            with self.subTest(payload=payload):
                self.assertFalse(bridge.review_tested_change(payload)["ok"])
        self.open_request.assert_not_called()
        self.assertEqual(bridge.connection_status()["callsRemaining"], 20)

    def test_tested_review_accepts_boundary_seeds_and_completed_time_limit(self):
        payload = self.compact_review_payload()
        payload["courses"][0]["seed"] = 0
        payload["courses"][1]["seed"] = 2**32 - 1
        payload["courses"][0]["candidate"] = {"score": 702, "ticks": 3600, "outcome": "time_limit"}
        messages = bridge._validate_tested_review(payload)
        self.assertEqual(json.loads(messages[1]["content"]), payload)
        self.open_request.assert_not_called()

    def test_tested_review_caps_encoded_payload_size_even_with_bounded_text_fields(self):
        self.connect_for_inference()
        payload = self.compact_review_payload()
        # Six hundred printable non-BMP characters expand to 7,200 JSON bytes per field.
        payload["originalProposal"]["rationale"] = chr(0x1f680) * 600
        payload["originalProposal"]["caveat"] = chr(0x1f680) * 600
        self.assertFalse(bridge.review_tested_change(payload)["ok"])
        self.open_request.assert_not_called()
        self.assertEqual(bridge.connection_status()["callsRemaining"], 20)

    def test_tested_review_model_mismatch_does_not_consume_attempt_or_call_provider(self):
        self.connect_for_inference()
        result = bridge.review_tested_change(self.compact_review_payload(), expected_model="other-model")
        self.assertFalse(result["ok"])
        self.assertIn("selected model changed", result["message"])
        self.assertEqual(result["callsRemaining"], 20)
        self.assertTrue(bridge.connection_status()["ready"])
        self.open_request.assert_not_called()

    def test_tested_review_rejects_invalid_reply_without_retry_or_raw_content(self):
        for changes in ({"assessment": "better"}, {"assessment": []}, {"decision": "adopt"},
                        {"rationale": ""}, {"rationale": "x" * 601}, {"caveat": "hidden\0text"},
                        {"caveat": FAKE_KEY}, {"caveat": "sk-secret-credential"},
                        {"trained": True}):
            with self.subTest(changes=changes):
                self.connect_for_inference()
                before = bridge.connection_status()["callsRemaining"]
                self.open_request.return_value = self.completion({**self.compact_review_answer(), **changes})
                result = bridge.review_tested_change(self.compact_review_payload())
                self.assertFalse(result["ok"])
                self.assertEqual(result["callsRemaining"], before - 1)
                self.assertNotIn("assessment", result)
                self.assertNotIn("rationale", result)
                self.assertNotIn(FAKE_KEY, json.dumps(result))
                self.open_request.assert_called_once()

    def test_tested_review_truncation_preserves_usage_but_never_retries(self):
        self.connect_for_inference()
        self.open_request.return_value = Response({
            "choices": [{"finish_reason": "length", "message": {"content": ""}}],
            "usage": {"prompt_tokens": 400, "completion_tokens": 2000, "total_tokens": 2400},
        }, url=bridge.COMPLETIONS_URL)
        result = bridge.review_tested_change(self.compact_review_payload())
        self.assertFalse(result["ok"])
        self.assertEqual(result["validationReason"], "truncated_response")
        self.assertEqual(result["usage"], {"promptTokens": 400, "completionTokens": 2000, "totalTokens": 2400})
        self.assertEqual(result["requestId"], "mreq_offline_test")
        self.assertEqual(result["callsRemaining"], 19)
        self.assertNotIn("content", result)
        self.open_request.assert_called_once()

    def test_tested_review_shares_inference_cap_and_single_request_lock(self):
        self.connect_for_inference()
        with bridge._state_lock:
            bridge._attempts = bridge.MAX_INFERENCE_ATTEMPTS
        self.assertFalse(bridge.review_tested_change(self.compact_review_payload())["ok"])
        self.open_request.assert_not_called()
        with bridge._state_lock:
            bridge._attempts = 0
        with bridge._probe_lock:
            result = bridge.review_tested_change(self.compact_review_payload())
        self.assertFalse(result["ok"])
        self.assertIn("already in progress", result["message"])
        self.assertEqual(result["callsRemaining"], 20)
        self.open_request.assert_not_called()

    def memory_payload(self):
        current = self.compact_review_payload()
        prior = self.compact_review_payload()
        prior["runId"] = "prior_review"
        prior["originalProposal"]["rationale"] = "A previous bounded proposal for comparison."
        return {"source": "saved-memory-review", "runId": "memory_" + "a" * 32,
                "taskVersion": "memory-assisted-review-v1", "current": current,
                "memories": [{"input": prior, "approvedAnswer": self.compact_review_answer(), "reviewType": "confirmed_reference"}]}

    def test_memory_review_is_text_context_and_does_not_change_plain_task(self):
        plain_prompt = bridge._validate_tested_review(self.compact_review_payload())[0]
        self.connect_for_inference()
        self.open_request.return_value = self.completion(self.compact_review_answer())
        payload = self.memory_payload()
        result = bridge.memory_review(payload, expected_model="test-model")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["model"], "test-model")
        self.assertEqual(result["callsRemaining"], 19)
        body = json.loads(self.open_request.call_args.args[0].data)
        self.assertEqual(body["max_completion_tokens"], 2000)
        self.assertEqual(json.loads(body["messages"][2]["content"]), payload)
        self.assertTrue(all(isinstance(m["content"], str) for m in body["messages"]))
        self.assertIn("not a model-weight update", body["messages"][0]["content"])
        self.assertNotEqual(body["messages"][0], plain_prompt)
        self.assertEqual(bridge._validate_tested_review(self.compact_review_payload())[0], plain_prompt)
        for key in ("store", "metadata"):
            self.assertNotIn(key, body)
        self.open_request.assert_called_once()

    def test_memory_rejects_same_input_despite_changed_trace_id_or_duplicate_memory(self):
        self.connect_for_inference()
        payload = self.memory_payload()
        payload["memories"][0]["input"] = self.compact_review_payload()
        payload["memories"][0]["input"]["runId"] = "different_trace_same_input"
        self.assertFalse(bridge.memory_review(payload)["ok"])
        payload = self.memory_payload()
        payload["memories"].append(json.loads(json.dumps(payload["memories"][0])))
        self.assertFalse(bridge.memory_review(payload)["ok"])
        self.open_request.assert_not_called()

    def test_memory_rejects_unbounded_unapproved_shape_and_credentials(self):
        self.connect_for_inference()
        for change in (lambda p: p.update(memories=[]),
                       lambda p: p.update(secret=FAKE_KEY),
                       lambda p: p["memories"][0].update(reviewType="draft"),
                       lambda p: p["memories"][0].update(reason="hidden extra"),
                       lambda p: p["memories"][0]["approvedAnswer"].update(rationale=FAKE_KEY),
                       lambda p: p["current"].update(expectedAssessment="unchanged")):
            payload = self.memory_payload(); change(payload)
            self.assertFalse(bridge.memory_review(payload)["ok"])
        payload = self.memory_payload()
        for seed in (200001, 200003):
            memory = json.loads(json.dumps(payload["memories"][0]))
            memory["input"]["courses"][0]["seed"] = seed
            payload["memories"].append(memory)
        for memory in payload["memories"]:
            memory["input"]["originalProposal"]["rationale"] = chr(0x1f680) * 400
            memory["approvedAnswer"]["rationale"] = chr(0x1f680) * 600
            bridge._validate_tested_review(memory["input"])
        self.assertFalse(bridge.memory_review(payload)["ok"])
        self.open_request.assert_not_called()
        self.assertEqual(bridge.connection_status()["callsRemaining"], 20)

    def test_memory_keeps_wrong_valid_answer_and_never_retries_invalid_answer(self):
        self.connect_for_inference()
        self.open_request.return_value = self.completion({**self.compact_review_answer(), "assessment": "worse_on_both"})
        self.assertEqual(bridge.memory_review(self.memory_payload())["assessment"], "worse_on_both")
        self.open_request.reset_mock()
        self.open_request.return_value = self.completion({**self.compact_review_answer(), "trained": True})
        result = bridge.memory_review(self.memory_payload())
        self.assertFalse(result["ok"])
        self.assertNotIn("trained", result)
        self.open_request.assert_called_once()

    def test_memory_uses_existing_model_guard_limit_and_single_call_lock(self):
        self.connect_for_inference()
        self.assertFalse(bridge.memory_review(self.memory_payload(), expected_model="changed-model")["ok"])
        with bridge._probe_lock:
            self.assertFalse(bridge.memory_review(self.memory_payload())["ok"])
        with bridge._state_lock:
            bridge._attempts = bridge.MAX_INFERENCE_ATTEMPTS
        self.assertFalse(bridge.memory_review(self.memory_payload())["ok"])
        self.open_request.assert_not_called()

    def test_proposal_is_bounded_multimodal_and_preserves_stable_session_prefix(self):
        self.connect_for_inference()
        self.open_request.return_value = self.completion(self.proposal_answer())
        result = bridge.propose(self.proposal_payload())
        self.assertTrue(result["ok"])
        self.assertEqual(result["value"], 190)
        self.assertEqual(result["model"], "test-model")
        self.assertEqual(result["callsRemaining"], 19)
        body = json.loads(self.open_request.call_args.args[0].data)
        self.assertEqual(body["max_completion_tokens"], 3000)
        self.assertNotIn("metadata", body)
        self.assertNotIn("store", body)
        self.assertEqual(json.loads(body["messages"][1]["content"]), {"runId": "exp_test"})
        evidence = body["messages"][2]["content"]
        self.assertEqual(sum(item["type"] == "image_url" for item in evidence), 2)
        self.assertEqual(json.loads(evidence[1]["text"])["actions"][0]["tick"], 100)
        labels = [json.loads(item["text"]) for item in evidence if item["type"] == "text"]
        self.assertIn({"slot": "baseline-a", "tick": 600, "tMs": 10000, "score": 100}, labels)
        self.assertNotIn("speedOffset", json.dumps(body))
        self.assertIn("not by Mubit", body["messages"][0]["content"])
        self.assertIn("No setting has been changed yet", body["messages"][0]["content"])
        self.assertNotIn(FAKE_KEY, json.dumps(body))

    def test_proposal_rejects_hidden_engine_data_and_invalid_excerpts_without_calls(self):
        import copy
        self.connect_for_inference()
        variants = []
        for key, value in (("course", []), ("mode", "paused"), ("maxTicks", 3601), ("source", "client")):
            variants.append({**self.proposal_payload(), key: value})
        for key, value in (("course", []), ("seed", -1), ("actionsTruncated", True),
                           ("framesTruncated", True), ("ticks", 3601), ("slot", "repeat-a")):
            payload = self.proposal_payload()
            payload["courses"][0][key] = value
            variants.append(payload)
        for key, value in (("tick", 601), ("tMs", 0), ("reason", "mbt_secret"), ("extra", 1)):
            payload = self.proposal_payload()
            payload["courses"][0]["actions"][0][key] = value
            variants.append(payload)
        payload = self.proposal_payload()
        payload["courses"][0]["frames"] *= 7
        variants.append(payload)
        payload = self.proposal_payload()
        payload["courses"][0]["frames"][0]["image"] = "data:image/png;base64,not-a-png"
        variants.append(payload)
        for payload in variants:
            with self.subTest(keys=list(payload)):
                self.assertFalse(bridge.propose(copy.deepcopy(payload))["ok"])
        self.open_request.assert_not_called()
        self.assertEqual(bridge.connection_status()["callsRemaining"], 20)

    def test_proposal_accepts_distinct_custom_seeds_and_rejects_duplicates(self):
        payload = self.proposal_payload()
        payload["courses"][0]["seed"] = 0
        payload["courses"][1]["seed"] = 2**32 - 1
        bridge._validate_proposal(payload)
        payload["courses"][1]["seed"] = 0
        with self.assertRaises(bridge._InvalidInput):
            bridge._validate_proposal(payload)
        self.open_request.assert_not_called()

    def test_proposal_requires_exactly_one_changed_bounded_parameter(self):
        invalid = [
            {"parameter": "code"}, {"value": True}, {"value": 191}, {"value": 170}, {"value": 250},
            {"rationale": ""}, {"rationale": "x" * 601}, {"caveat": "mbt_secret"},
            {"caveat": "sk-secret-credential"}, {"extra": "code"},
        ]
        for changes in invalid:
            with self.subTest(changes=changes):
                self.connect_for_inference()
                self.open_request.return_value = self.completion({**self.proposal_answer(), **changes})
                result = bridge.propose(self.proposal_payload())
                self.assertFalse(result["ok"])
                self.assertNotIn("parameter", result)
                self.open_request.assert_called_once()
        for parameter, bounds in bridge.EXPERIMENT_PARAMETER_BOUNDS.items():
            for value in (bounds["min"], bounds["max"]):
                if value == bridge.EXPERIMENT_BASELINE_CONFIG[parameter]:
                    continue
                answer = {**self.proposal_answer(), "parameter": parameter, "value": value}
                self.assertEqual(bridge._validate_proposal_result(answer), answer)

    def test_proposals_share_existing_call_cap_and_probe_lock(self):
        self.connect_for_inference()
        self.open_request.return_value = self.completion(self.proposal_answer())
        with bridge._state_lock:
            bridge._attempts = bridge.MAX_INFERENCE_ATTEMPTS
        self.assertFalse(bridge.propose(self.proposal_payload())["ok"])
        self.open_request.assert_not_called()
        with bridge._state_lock:
            bridge._attempts = 0
        with bridge._probe_lock:
            result = bridge.propose(self.proposal_payload())
        self.assertFalse(result["ok"])
        self.assertIn("already in progress", result["message"])
        self.open_request.assert_not_called()

    def test_proposal_timeout_is_never_retried(self):
        self.connect_for_inference()
        self.open_request.side_effect = TimeoutError(FAKE_KEY)
        result = bridge.propose(self.proposal_payload())
        self.assertFalse(result["ok"])
        self.assertEqual(result["callsRemaining"], 19)
        self.assertNotIn(FAKE_KEY, json.dumps(result))
        self.open_request.assert_called_once()

    def test_empty_truncated_proposal_preserves_usage_without_raw_reply(self):
        self.connect_for_inference()
        self.open_request.return_value = Response({
            "choices": [{"finish_reason": "length", "message": {"content": ""}}],
            "usage": {"prompt_tokens": 6413, "completion_tokens": 700, "total_tokens": 7113},
        }, url=bridge.COMPLETIONS_URL)
        result = bridge.propose(self.proposal_payload())
        self.assertFalse(result["ok"])
        self.assertEqual(result["validationReason"], "truncated_response")
        self.assertEqual(result["finishReason"], "length")
        self.assertEqual(result["contentLength"], 0)
        self.assertEqual(result["usage"], {"promptTokens": 6413, "completionTokens": 700, "totalTokens": 7113})
        self.assertEqual(result["callsRemaining"], 19)
        self.assertIn("response limit", result["message"])
        self.assertNotIn("content", result)
        self.assertNotIn("choices", result)
        self.open_request.assert_called_once()

    def test_invalid_proposals_return_static_reason_and_allowlisted_metadata(self):
        variants = [
            ("", "stop", "empty_content"),
            (None, "stop", "empty_content"),
            ("not JSON: private provider prose", "stop", "invalid_json"),
            ('{"value":190,"value":200}', "stop", "invalid_json"),
            (json.dumps({**self.proposal_answer(), "value": 170}), "stop", "invalid_schema"),
            (json.dumps(self.proposal_answer()), "length", "truncated_response"),
            ("not JSON", FAKE_KEY, "invalid_json"),
            ("not JSON", {"secret": FAKE_KEY}, "invalid_json"),
            ("x" * 10001, "stop", "response_too_large"),
            (FAKE_KEY, "stop", "credential_reflection"),
        ]
        for content, finish_reason, expected in variants:
            with self.subTest(expected=expected):
                self.connect_for_inference()
                self.open_request.return_value = Response({
                    "choices": [{"finish_reason": finish_reason, "message": {"content": content}}],
                    "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
                }, url=bridge.COMPLETIONS_URL)
                result = bridge.propose(self.proposal_payload())
                self.assertFalse(result["ok"])
                self.assertEqual(result["validationReason"], expected)
                self.assertEqual(result["finishReason"], finish_reason if isinstance(finish_reason, str) and finish_reason in bridge.FINISH_REASONS else None)
                self.assertEqual(result["usage"]["totalTokens"], 30)
                self.assertNotIn(FAKE_KEY, json.dumps(result))
                self.assertNotIn("private provider prose", json.dumps(result))
                self.open_request.assert_called_once()

    def test_invalid_completion_usage_is_bounded_before_failure_is_returned(self):
        self.connect_for_inference()
        self.open_request.return_value = Response({
            "choices": [{"finish_reason": "length", "message": {"content": ""}}],
            "usage": {"prompt_tokens": FAKE_KEY, "completion_tokens": True, "total_tokens": 10**30},
        }, url=bridge.COMPLETIONS_URL)
        result = bridge.propose(self.proposal_payload())
        self.assertFalse(result["ok"])
        self.assertIsNone(result["usage"])
        self.assertNotIn(FAKE_KEY, json.dumps(result))

    def test_invalid_game_inputs_do_not_consume_calls(self):
        self.configure()
        invalid = [
            {**self.decision_payload(), "obstacleX": 200},
            {**self.decision_payload(), "screenshot": "data:image/png;base64,bad"},
            {**self.decision_payload(), "step": True},
            {**self.decision_payload(), "previousAction": "execute"},
            {**self.decision_payload(), "screenshot": "x" * (bridge.MAX_SCREENSHOT_LENGTH + 1)},
        ]
        for payload in invalid:
            self.assertFalse(bridge.decide(payload)["ok"])
        self.assertFalse(bridge.review({**self.review_payload(), "actions": self.review_payload()["actions"] * 41})["ok"])
        self.assertEqual(bridge.connection_status()["callsRemaining"], 20)
        self.open_request.assert_not_called()

    def test_failed_attempts_consume_limit_and_are_not_retried(self):
        self.configure()
        def respond(request):
            if request.get_method() == "GET":
                return Response()
            raise TimeoutError(FAKE_KEY)
        self.open_request.side_effect = respond
        for _ in range(20):
            bridge.check_connection()
            result = bridge.decide(self.decision_payload())
            self.assertFalse(result["ok"])
            self.assertNotIn(FAKE_KEY, json.dumps(result))
        result = bridge.review(self.review_payload())
        self.assertFalse(result["ok"])
        self.assertEqual(result["callsRemaining"], 0)
        self.assertEqual(sum(call.args[0].get_method() == "POST" for call in self.open_request.call_args_list), 20)
        # A model-list check does not use or restore the inference budget.
        self.open_request.side_effect = None
        self.open_request.return_value = Response()
        status = bridge.check_connection()
        self.assertEqual(status["callsRemaining"], 0)
        self.assertFalse(status["ready"])

    def test_invalid_action_or_secret_reflection_is_never_returned(self):
        answers = [
            {"action": "execute", "frames": 10, "reason": "wrong"},
            {"action": "jump", "frames": 31, "reason": "too long"},
            {"action": "jump", "frames": True, "reason": "wrong type"},
            {"action": "jump", "frames": 10, "reason": FAKE_KEY},
            {"action": "jump", "frames": 10, "reason": "ok", "extra": "field"},
        ]
        for answer in answers:
            self.connect_for_inference()
            self.open_request.return_value = self.completion(answer)
            result = bridge.decide(self.decision_payload())
            self.assertFalse(result["ok"])
            self.assertNotIn("action", result)
            self.assertNotIn(FAKE_KEY, json.dumps(result))

    def test_http_inference_error_exposes_bounded_parameter_diagnostics_without_retry(self):
        self.connect_for_inference()
        class TrackedBody(io.BytesIO):
            def read(self, size=-1):
                self.read_size = size
                return super().read(size)
        body = TrackedBody(json.dumps({"error": {
            "message": "Unsupported parameter: 'response_format'. Use a supported response format.",
            "type": "invalid_request_error", "code": "unsupported_parameter", "param": "response_format",
        }}).encode())
        error = HTTPError(bridge.COMPLETIONS_URL, 400, FAKE_KEY,
                          {"x-middleware-request-id": "mreq_parameter_error"}, body)
        self.open_request.side_effect = error
        result = bridge.review(self.saved_review_payload())
        self.assertFalse(result["ok"])
        self.assertEqual(result["httpStatus"], 400)
        self.assertEqual(result["errorCode"], "unsupported_parameter")
        self.assertEqual(result["errorType"], "invalid_request_error")
        self.assertEqual(result["errorParam"], "response_format")
        self.assertIn("Unsupported parameter", result["providerMessage"])
        self.assertIn("The provider rejected", result["message"])
        self.assertEqual(result["requestId"], "mreq_parameter_error")
        self.assertEqual(result["callsRemaining"], 19)
        self.assertLessEqual(body.read_size, bridge.MAX_RESPONSE_BYTES)
        self.assertTrue(body.closed)
        self.open_request.assert_called_once()
        self.assertFalse(bridge.connection_status()["verified"])
        self.assertNotIn(FAKE_KEY, json.dumps(result))

    def test_http_error_diagnostics_drop_credential_bearing_fields(self):
        for secret in (FAKE_KEY, "mbt_other_private_key", "sk-other-private-key", "Bearer private-token"):
            with self.subTest(kind=secret[:3]):
                self.connect_for_inference()
                body = {"error": {field: secret for field in ("message", "code", "type", "param")}}
                self.open_request.side_effect = HTTPError(
                    bridge.COMPLETIONS_URL, 400, secret,
                    {"x-middleware-request-id": "mreq_safe_error"}, io.BytesIO(json.dumps(body).encode()))
                result = bridge.review(self.review_payload())
                for field in ("errorCode", "errorType", "errorParam", "providerMessage"):
                    self.assertIsNone(result[field])
                self.assertEqual(result["httpStatus"], 400)
                self.assertNotIn(secret, json.dumps(result))
                self.assertNotIn(secret, json.dumps(bridge.connection_status()))
                self.open_request.assert_called_once()

    def test_http_error_body_limits_and_malformed_json_do_not_leak(self):
        oversized = json.dumps({"error": {"message": "x" * bridge.MAX_RESPONSE_BYTES + FAKE_KEY}}).encode()
        for raw, headers in ((oversized, {}), (oversized, {"Content-Length": str(len(oversized))}),
                             (b"not json " + FAKE_KEY.encode(), {}), (b'{"error": []}', {})):
            with self.subTest(size=len(raw), has_length=bool(headers)):
                self.connect_for_inference()
                body = io.BytesIO(raw)
                self.open_request.side_effect = HTTPError(
                    bridge.COMPLETIONS_URL, 400, FAKE_KEY,
                    {"x-middleware-request-id": "mreq_safe_error", **headers}, body)
                result = bridge.review(self.review_payload())
                self.assertIsNone(result["providerMessage"])
                self.assertIsNone(result["errorCode"])
                self.assertNotIn(FAKE_KEY, json.dumps(result))
                self.assertTrue(body.closed)
                self.open_request.assert_called_once()

    def test_completion_without_mubit_header_is_not_verified(self):
        self.connect_for_inference()
        self.open_request.return_value = self.completion(headers={})
        self.assertFalse(bridge.decide(self.decision_payload())["ok"])
        self.assertFalse(bridge.connection_status()["verified"])

    def test_unlisted_model_cannot_send_inference(self):
        self.configure("not-in-list")
        status = bridge.check_connection()
        self.assertTrue(status["verified"])
        self.assertFalse(status["ready"])
        self.open_request.reset_mock()
        self.assertFalse(bridge.decide(self.decision_payload())["ok"])
        self.open_request.assert_not_called()
        self.assertEqual(bridge.connection_status()["callsRemaining"], 20)

    def test_realtime_mode_is_sent_without_changing_system_instruction(self):
        self.connect_for_inference()
        self.open_request.return_value = self.completion()
        self.assertTrue(bridge.decide({**self.decision_payload(), "mode": "realtime"})["ok"])
        content = json.loads(self.open_request.call_args.args[0].data)["messages"]
        self.assertEqual(json.loads(content[2]["content"][0]["text"])["mode"], "realtime")

    def test_configuration_change_during_inference_discards_action(self):
        self.connect_for_inference()
        def change_config(request):
            os.environ["DINO_MUBIT_MODEL"] = "changed-model"
            return self.completion()
        self.open_request.side_effect = change_config
        result = bridge.decide(self.decision_payload())
        self.assertFalse(result["ok"])
        self.assertNotIn("action", result)
        self.assertEqual(result["callsRemaining"], 19)
        self.assertFalse(bridge.connection_status()["ready"])

    def test_bad_png_crc_is_rejected_without_network(self):
        self.configure()
        payload = self.decision_payload()
        raw = bytearray(bridge.base64.b64decode(payload["screenshot"].split(",", 1)[1]))
        raw[20] ^= 1
        payload["screenshot"] = "data:image/png;base64," + bridge.base64.b64encode(raw).decode()
        self.assertFalse(bridge.decide(payload)["ok"])
        self.open_request.assert_not_called()

    def test_busy_inference_does_not_send_or_consume_attempt(self):
        self.connect_for_inference()
        bridge._probe_lock.acquire()
        try:
            result = bridge.decide(self.decision_payload())
            self.assertFalse(result["ok"])
            self.assertEqual(result["callsRemaining"], 20)
            self.open_request.assert_not_called()
        finally:
            bridge._probe_lock.release()

    def test_budget_survives_configuration_change(self):
        self.connect_for_inference()
        self.open_request.return_value = self.completion()
        self.assertTrue(bridge.decide(self.decision_payload())["ok"])
        os.environ["DINO_MUBIT_MODEL"] = "changed-model"
        status = bridge.connection_status()
        self.assertFalse(status["verified"])
        self.assertEqual(status["callsRemaining"], 19)

    def test_failure_requires_check_before_another_inference(self):
        self.connect_for_inference()
        self.open_request.side_effect = HTTPError(bridge.COMPLETIONS_URL, 401, FAKE_KEY, {}, None)
        self.assertFalse(bridge.decide(self.decision_payload())["ok"])
        self.assertFalse(bridge.connection_status()["ready"])
        self.assertFalse(bridge.decide(self.decision_payload())["ok"])
        self.assertEqual(self.open_request.call_count, 1)
        self.assertEqual(bridge.connection_status()["callsRemaining"], 19)

    def test_duplicate_action_keys_are_rejected(self):
        self.connect_for_inference()
        self.open_request.return_value = Response({"choices": [{"message": {"content":
            '{"action":"wait","action":"jump","frames":10,"reason":"test"}'
        }}]}, url=bridge.COMPLETIONS_URL)
        self.assertFalse(bridge.decide(self.decision_payload())["ok"])

    def test_invalid_review_response_is_not_returned(self):
        self.connect_for_inference()
        self.open_request.return_value = self.completion({"summary": "Review", "suggestions": ["a"] * 4})
        result = bridge.review(self.review_payload())
        self.assertFalse(result["ok"])
        self.assertNotIn("summary", result)

    def test_missing_model_does_not_mean_authentication_failed(self):
        os.environ["MUBIT_API_KEY"] = FAKE_KEY
        status = bridge.check_connection()
        self.assertTrue(status["configured"])
        self.assertTrue(status["verified"])
        self.assertFalse(status["ready"])
        self.assertIsNone(status["model"])

    def test_keychain_fallback_is_exact_and_captured_without_output(self):
        self.keychain.stop()
        try:
            with patch.object(bridge.sys, "platform", "darwin"), \
                 patch.object(bridge.getpass, "getuser", return_value="local-test-user"), \
                 patch.object(bridge.subprocess, "run") as command:
                command.return_value.returncode = 0
                command.return_value.stdout = FAKE_KEY + "\n"
                status = bridge.connection_status()
                self.assertTrue(status["configured"])
                self.assert_no_secret(status)
                command.assert_called_once_with(
                    ["/usr/bin/security", "find-generic-password", "-s", "com.local-dino.mubit-api-key",
                     "-a", "local-test-user", "-w"],
                    capture_output=True, text=True, timeout=2, check=False,
                )
        finally:
            self.keychain.start()


if __name__ == "__main__":
    unittest.main()
