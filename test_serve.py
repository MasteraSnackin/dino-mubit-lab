"""Local route checks. All Mubit functions are mocked; no provider calls."""
import http.client
import json
import socket
from pathlib import Path
from http.server import ThreadingHTTPServer
import threading
import tempfile
import unittest
from unittest.mock import patch

import serve
from test_run_store import record


class QuietHandler(serve.Handler):
    def log_message(self, *_):
        pass


class LocalRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def call(self, method, path, body=None, headers=None, include_headers=False):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        merged = {"Host": "127.0.0.1:4040", **(headers or {})}
        connection.request(method, path, body, merged)
        response = connection.getresponse()
        status, payload = response.status, response.read()
        response_headers = dict(response.getheaders())
        connection.close()
        if include_headers:
            return status, payload, response_headers
        return status, payload

    @patch.object(serve.mubit_bridge, "connection_status", return_value={"configured": False, "verified": False})
    def test_status_is_explicitly_disconnected(self, status):
        code, body = self.call("GET", "/api/mubit/status")
        self.assertEqual(code, 200)
        self.assertFalse(json.loads(body)["verified"])
        status.assert_called_once()

    @patch.object(serve.mubit_bridge, "check_connection")
    def test_foreign_origin_cannot_trigger_provider(self, check):
        code, _ = self.call("POST", "/api/mubit/check", "{}", {
            "Origin": "https://example.invalid", "Content-Type": "application/json"})
        self.assertEqual(code, 403)
        check.assert_not_called()

    @patch.object(serve.mubit_bridge, "check_connection")
    def test_missing_origin_cannot_trigger_provider(self, check):
        code, _ = self.call("POST", "/api/mubit/check", "{}", {"Content-Type": "application/json"})
        self.assertEqual(code, 403)
        check.assert_not_called()

    @patch.object(serve.mubit_bridge, "check_connection", return_value={"verified": False, "message": "Key missing"})
    def test_local_explicit_check(self, check):
        code, body = self.call("POST", "/api/mubit/check", "{}", {
            "Origin": "http://127.0.0.1:4040", "Content-Type": "application/json"})
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["message"], "Key missing")
        check.assert_called_once()

    @patch.object(serve.mubit_bridge, "decide")
    def test_oversize_request_is_rejected_before_provider(self, decide):
        code, _ = self.call("POST", "/api/mubit/decide", "{}", {
            "Origin": "http://127.0.0.1:4040", "Content-Type": "application/json", "Content-Length": "1100001"})
        self.assertEqual(code, 413)
        decide.assert_not_called()

    @patch.object(serve.mubit_bridge, "decide", side_effect=RuntimeError("secret-shaped-upstream-error"))
    def test_upstream_exception_is_not_reflected(self, decide):
        code, body = self.call("POST", "/api/mubit/decide", "{}", {
            "Origin": "http://127.0.0.1:4040", "Content-Type": "application/json"})
        self.assertEqual(code, 500)
        self.assertNotIn(b"secret-shaped", body)

    def test_unrecognised_host_is_rejected(self):
        code, _ = self.call("GET", "/api/mubit/status", headers={"Host": "example.invalid"})
        self.assertEqual(code, 403)

    @patch.object(serve, "load_model_setting")
    @patch.object(serve.experiment_store, "create_experiment")
    def test_ambiguous_nonfinite_and_deep_json_never_reaches_a_route(self, create, settings):
        bodies = (
            '{"seed":1,"seed":2}',
            '{"settings":{"seed":1,"seed":2}}',
            '{"seed":NaN}',
            '{"seed":Infinity}',
            '{"seed":-Infinity}',
            '{"seed":1e999}',
            '{"value":' + '[' * 1500 + '0' + ']' * 1500 + '}',
        )
        for body in bodies:
            with self.subTest(body_length=len(body)):
                code, response = self.call("POST", "/api/experiments", body, {
                    "Origin": "http://127.0.0.1:4040", "Content-Type": "application/json"})
                self.assertEqual(code, 400)
                self.assertEqual(json.loads(response), {
                    "ok": False, "message": "The request could not be read."})
        create.assert_not_called()
        settings.assert_not_called()

    @patch.object(serve, "load_model_setting")
    @patch.object(serve.experiment_store, "create_experiment")
    def test_early_eof_never_dispatches_a_short_complete_json_value(self, create, settings):
        with socket.create_connection(("127.0.0.1", self.server.server_port), timeout=2) as connection:
            connection.sendall(
                b"POST /api/experiments HTTP/1.1\r\nHost: 127.0.0.1:4040\r\n"
                b"Origin: http://127.0.0.1:4040\r\nContent-Type: application/json\r\n"
                b"Content-Length: 20\r\nConnection: close\r\n\r\n{}")
            connection.shutdown(socket.SHUT_WR)
            response = http.client.HTTPResponse(connection)
            response.begin()
            self.assertEqual(response.status, 400)
            self.assertFalse(json.loads(response.read())["ok"])
        create.assert_not_called()
        settings.assert_not_called()

    @patch.object(serve, "load_model_setting")
    @patch.object(serve.experiment_store, "create_experiment", return_value={"ok": True})
    def test_valid_nested_json_still_reaches_route_unchanged(self, create, settings):
        payload = {"settings": {"lead": 1.5}, "notes": ["NaN and duplicate words are text", None, True]}
        code, response = self.call("POST", "/api/experiments", json.dumps(payload), {
            "Origin": "http://127.0.0.1:4040", "Content-Type": "application/json"})
        self.assertEqual(code, 200)
        self.assertTrue(json.loads(response)["ok"])
        create.assert_called_once_with(payload)

    @patch.object(serve.provider_comparison, "run")
    @patch.object(serve.provider_comparison, "status", return_value={"ok": True, "comparison": {"status": "not_started"}})
    @patch.object(serve.provider_comparison, "export", return_value={"ok": False, "message": "No completed comparison."})
    def test_provider_comparison_reads_never_start_requests(self, export, status, run):
        code, body = self.call("GET", "/api/provider-comparison")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["comparison"]["status"], "not_started")
        code, _, headers = self.call("GET", "/api/provider-comparison/export", include_headers=True)
        self.assertEqual(code, 409)
        self.assertNotIn("Content-Disposition", headers)
        run.assert_not_called()

    @patch.object(serve.provider_comparison, "run")
    def test_provider_comparison_requires_local_origin_and_bounded_key_body(self, run):
        route = "/api/provider-comparison"
        for origin in (None, "https://example.invalid"):
            headers = {"Content-Type": "application/json"}
            if origin:
                headers["Origin"] = origin
            code, _ = self.call("POST", route, "{}", headers)
            self.assertEqual(code, 403)
        code, _ = self.call("POST", route, "{}", {"Origin": "http://127.0.0.1:4040", "Content-Type": "application/json", "Content-Length": "4097"})
        self.assertEqual(code, 413)
        run.assert_not_called()

    @patch.object(serve.provider_comparison, "run", side_effect=RuntimeError("private-test-credential"))
    def test_provider_comparison_does_not_reflect_credentials_on_failure(self, run):
        payload = {"openaiKey": "private-test-credential", "expectedInputHash": "a" * 64}
        code, body = self.call("POST", "/api/provider-comparison", json.dumps(payload), {
            "Origin": "http://127.0.0.1:4040", "Content-Type": "application/json"})
        self.assertEqual(code, 500)
        self.assertNotIn(b"private-test-credential", body)
        run.assert_called_once_with(payload)

    @patch.object(serve.review_task_store, "request_review")
    @patch.object(serve.review_task_store, "get_case_review", return_value={"ok": True, "task": {"attempt": None}})
    def test_text_review_preview_and_export_never_request_inference(self, get, request):
        for suffix in ("", "/export"):
            code, body, headers = self.call("GET", "/api/review-task/cases/case_test" + suffix, include_headers=True)
            self.assertEqual(code, 200)
            self.assertIsNone(json.loads(body)["task"]["attempt"])
            self.assertEqual(headers["Cache-Control"], "no-store")
            if suffix:
                self.assertIn("dino-text-review-case_test.json", headers["Content-Disposition"])
        self.assertEqual(get.call_count, 2)
        get.assert_called_with("case_test")
        request.assert_not_called()

    @patch.object(serve.review_task_store, "request_review", return_value={"ok": True, "task": {"attempt": {"status": "succeeded"}}})
    def test_text_review_explicit_action_dispatches_case_and_revision(self, request):
        code, body = self.call("POST", "/api/review-task/cases/case_test/review", '{"expectedRevision":3}', {
            "Origin": "http://127.0.0.1:4040", "Content-Type": "application/json"})
        self.assertEqual(code, 200)
        self.assertTrue(json.loads(body)["ok"])
        request.assert_called_once_with("case_test", {"expectedRevision": 3})

    @patch.object(serve.review_task_store, "request_review")
    def test_text_review_rejects_foreign_origin_and_large_body(self, request):
        route = "/api/review-task/cases/case_test/review"
        code, _ = self.call("POST", route, "{}", {"Origin": "https://example.invalid", "Content-Type": "application/json"})
        self.assertEqual(code, 403)
        code, _ = self.call("POST", route, "{}", {"Origin": "http://127.0.0.1:4040", "Content-Type": "application/json", "Content-Length": "4097"})
        self.assertEqual(code, 413)
        request.assert_not_called()

    @patch.object(serve.review_task_store, "get_case_review", return_value={"ok": False, "message": "Record unavailable."})
    def test_text_review_unknown_record_is_not_exported(self, get):
        code, body, headers = self.call("GET", "/api/review-task/cases/case_missing/export", include_headers=True)
        self.assertEqual(code, 409)
        self.assertFalse(json.loads(body)["ok"])
        self.assertNotIn("Content-Disposition", headers)

    @patch.object(serve.review_target_store, "get_target", return_value={"ok": True, "target": {"revision": 0}})
    @patch.object(serve.review_target_store, "save_target")
    def test_matched_target_read_never_creates_draft(self, save, get):
        code, body = self.call("GET", "/api/review-target/cases/case_test")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["target"]["revision"], 0)
        get.assert_called_once_with("case_test")
        save.assert_not_called()

    @patch.object(serve.review_target_store, "save_target", return_value={"ok": True})
    @patch.object(serve.review_target_store, "approve_target", return_value={"ok": True})
    def test_matched_target_mutations_require_local_origin_and_exact_payload(self, approve, save):
        local = {"Origin": "http://127.0.0.1:4040", "Content-Type": "application/json"}
        draft = {"expectedRevision": 0, "answer": {"assessment": "unchanged"}, "reason": "Review evidence"}
        code, _ = self.call("POST", "/api/review-target/cases/case_test/draft", json.dumps(draft), local)
        self.assertEqual(code, 200)
        save.assert_called_once_with("case_test", draft)
        code, _ = self.call("POST", "/api/review-target/cases/case_test/approve", '{"expectedRevision":1,"confirm":true}', local)
        self.assertEqual(code, 200)
        approve.assert_called_once_with("case_test", {"expectedRevision": 1, "confirm": True})
        for path in ("draft", "approve"):
            code, _ = self.call("POST", "/api/review-target/cases/case_test/" + path, "{}", {
                "Origin": "https://example.invalid", "Content-Type": "application/json"})
            self.assertEqual(code, 403)
        self.assertEqual(save.call_count, 1)
        self.assertEqual(approve.call_count, 1)

    @patch.object(serve.review_target_store, "save_target")
    def test_matched_target_rejects_oversize_body(self, save):
        code, _ = self.call("POST", "/api/review-target/cases/case_test/draft", "{}", {
            "Origin": "http://127.0.0.1:4040", "Content-Type": "application/json", "Content-Length": "32769"})
        self.assertEqual(code, 413)
        save.assert_not_called()

    @patch.object(serve.review_target_store, "export_target", return_value={"ok": False, "message": "Approval required."})
    def test_unapproved_matched_target_has_no_download(self, export):
        code, body, headers = self.call("GET", "/api/review-target/cases/case_test/export", include_headers=True)
        self.assertEqual(code, 409)
        self.assertFalse(json.loads(body)["ok"])
        self.assertNotIn("Content-Disposition", headers)

    @patch.object(serve.local_adapter, "status", return_value={"ok": True, "localAdapter": {"status": "not_started", "weightsChanged": False}})
    @patch.object(serve.local_adapter, "export", return_value={"ok": False, "message": "No completed local adapter."})
    def test_local_adapter_routes_only_read_status_and_existing_receipt(self, export, status):
        code, body = self.call("GET", "/api/local-adapter/status")
        self.assertEqual(code, 200)
        self.assertFalse(json.loads(body)["localAdapter"]["weightsChanged"])
        code, _, headers = self.call("GET", "/api/local-adapter/export", include_headers=True)
        self.assertEqual(code, 409)
        self.assertNotIn("Content-Disposition", headers)
        status.assert_called_once_with()
        export.assert_called_once_with()
        code, _ = self.call("POST", "/api/local-adapter/train", "{}", {
            "Origin": "http://127.0.0.1:4040", "Content-Type": "application/json"})
        self.assertEqual(code, 404)

    @patch.object(serve.memory_review_store, "get_case_review", return_value={"ok": True, "memoryReview": {"attempt": None, "weightUpdate": False}})
    @patch.object(serve.memory_review_store, "request_review")
    def test_memory_preview_and_export_do_not_infer(self, request, get):
        for suffix in ("", "/export"):
            code, body = self.call("GET", "/api/memory-review/cases/case_test" + suffix)
            self.assertEqual(code, 200)
            self.assertFalse(json.loads(body)["memoryReview"]["weightUpdate"])
        self.assertEqual(get.call_count, 2)
        request.assert_not_called()

    @patch.object(serve.memory_review_store, "request_review", return_value={"ok": True})
    def test_memory_call_requires_local_origin_and_forwards_preview_binding(self, request):
        route = "/api/memory-review/cases/case_test/review"
        payload = {"expectedRevision": 3, "expectedInputHash": "a" * 64}
        code, _ = self.call("POST", route, json.dumps(payload), {
            "Origin": "https://example.invalid", "Content-Type": "application/json"})
        self.assertEqual(code, 403)
        request.assert_not_called()
        code, _ = self.call("POST", route, json.dumps(payload), {
            "Origin": "http://127.0.0.1:4040", "Content-Type": "application/json"})
        self.assertEqual(code, 200)
        request.assert_called_once_with("case_test", payload)
        code, _ = self.call("POST", route, "{}", {
            "Origin": "http://127.0.0.1:4040", "Content-Type": "application/json", "Content-Length": "4097"})
        self.assertEqual(code, 413)
        self.assertEqual(request.call_count, 1)

    @patch.object(serve.mubit_bridge, "review")
    def test_save_list_get_and_export_are_local_only(self, review):
        with tempfile.TemporaryDirectory() as temp, patch.object(serve.run_store, "RUNS_DIR", Path(temp) / "runs"):
            headers = {"Origin": "http://127.0.0.1:4040", "Content-Type": "application/json"}
            code, body = self.call("POST", "/api/runs", json.dumps(record()), headers)
            self.assertEqual(code, 200)
            self.assertTrue(json.loads(body)["ok"])
            for path in ("/api/runs", "/api/runs/run_test", "/api/runs/run_test/export"):
                code, body = self.call("GET", path)
                self.assertEqual(code, 200)
                self.assertTrue(json.loads(body)["ok"])
            code, body = self.call("POST", "/api/runs", json.dumps(record()), headers)
            self.assertTrue(json.loads(body)["alreadySaved"])
        review.assert_not_called()

    @patch.object(serve.mubit_bridge, "review", return_value={"ok": True, "summary": "Test reply"})
    def test_saved_review_uses_authoritative_record_and_original_count(self, review):
        saved = record()
        saved["actions"] = [{"tMs": n * 100, "action": "jump", "score": 10, "reason": "Observed pixels"} for n in range(45)]
        with patch.object(serve.run_store, "get_run", return_value={"ok": True, "run": saved}):
            code, body = self.call("POST", "/api/runs/run_test/review", "{}", {
                "Origin": "http://127.0.0.1:4040", "Content-Type": "application/json"})
        self.assertEqual(code, 200)
        sent = review.call_args.args[0]
        self.assertEqual(sent["source"], "saved-local-run")
        self.assertEqual(sent["actionCount"], 45)
        self.assertTrue(sent["actionsTruncated"])
        self.assertEqual(sent["actions"][0]["tMs"], 500)
        self.assertNotIn("frames", sent)
        self.assertNotIn("frames", sent["actions"][0])

    @patch.object(serve.mubit_bridge, "review")
    def test_saved_review_cannot_override_record_or_review_missing_run(self, review):
        self.assertFalse(serve.review_saved_run("run_test", {"score": 9999})["ok"])
        with patch.object(serve.run_store, "get_run", return_value={"ok": False, "message": "Unavailable"}):
            self.assertFalse(serve.review_saved_run("missing", {})["ok"])
        review.assert_not_called()

    @patch.object(serve.run_store, "save_run")
    def test_foreign_origin_cannot_create_local_run(self, save):
        code, _ = self.call("POST", "/api/runs", "{}", {
            "Origin": "https://example.invalid", "Content-Type": "application/json"})
        self.assertEqual(code, 403)
        save.assert_not_called()

    @patch.object(serve.mubit_bridge, "review")
    def test_generic_review_cannot_fabricate_a_saved_local_record(self, review):
        code, body = self.call("POST", "/api/mubit/review", json.dumps({
            "source": "saved-local-run", "runId": "fabricated", "score": 9999}), {
                "Origin": "http://127.0.0.1:4040", "Content-Type": "application/json"})
        self.assertEqual(code, 200)
        self.assertFalse(json.loads(body)["ok"])
        review.assert_not_called()

    @patch.object(serve.key_store, "save_key", return_value={"ok": True, "message": "Saved"})
    @patch.object(serve.mubit_bridge, "check_connection")
    def test_explicit_local_key_save_never_checks_provider(self, check, save):
        code, body = self.call("POST", "/api/mubit/key", json.dumps({"key": "mbt_test_fixture_only"}), {
            "Origin": "http://127.0.0.1:4040", "Content-Type": "application/json"})
        self.assertEqual(code, 200)
        self.assertTrue(json.loads(body)["ok"])
        self.assertNotIn(b"mbt_test_fixture_only", body)
        save.assert_called_once_with({"key": "mbt_test_fixture_only"})
        check.assert_not_called()

    @patch.object(serve.key_store, "save_key")
    def test_key_save_rejects_foreign_origin_and_large_body(self, save):
        code, _ = self.call("POST", "/api/mubit/key", "{}", {
            "Origin": "https://example.invalid", "Content-Type": "application/json"})
        self.assertEqual(code, 403)
        code, _ = self.call("POST", "/api/mubit/key", "{}", {
            "Origin": "http://127.0.0.1:4040", "Content-Type": "application/json", "Content-Length": "4097"})
        self.assertEqual(code, 413)
        save.assert_not_called()

    @patch.object(serve.experiment_store, "create_experiment", return_value={"ok": True, "experiment": {"id": "exp_test"}})
    @patch.object(serve.mubit_bridge, "propose")
    def test_experiment_creation_is_explicit_and_local(self, propose, create):
        code, body = self.call("POST", "/api/experiments", "{}", {
            "Origin": "http://127.0.0.1:4040", "Content-Type": "application/json"})
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["experiment"]["id"], "exp_test")
        create.assert_called_once_with({})
        propose.assert_not_called()

    @patch.object(serve.experiment_store, "list_experiments", return_value={"ok": True, "experiments": []})
    @patch.object(serve.experiment_store, "get_experiment", return_value={"ok": True, "experiment": {"id": "exp_test"}})
    @patch.object(serve.mubit_bridge, "propose")
    def test_experiment_reads_and_export_never_call_provider(self, propose, get, listing):
        for path in ("/api/experiments", "/api/experiments/exp_test", "/api/experiments/exp_test/export"):
            code, body = self.call("GET", path)
            self.assertEqual(code, 200)
            self.assertTrue(json.loads(body)["ok"])
        self.assertEqual(get.call_count, 2)
        listing.assert_called_once()
        propose.assert_not_called()

    @patch.object(serve.experiment_store, "save_run", return_value={"ok": True})
    @patch.object(serve.experiment_store, "propose_change", return_value={"ok": True})
    def test_experiment_routes_pass_id_and_payload(self, propose, save):
        headers = {"Origin": "http://127.0.0.1:4040", "Content-Type": "application/json"}
        self.assertEqual(self.call("POST", "/api/experiments/exp_test/runs", '{"slot":"baseline-a"}', headers)[0], 200)
        self.assertEqual(self.call("POST", "/api/experiments/exp_test/proposal", '{}', headers)[0], 200)
        save.assert_called_once_with("exp_test", {"slot": "baseline-a"})
        propose.assert_called_once_with("exp_test", {})

    @patch.object(serve.experiment_store, "save_run")
    @patch.object(serve.experiment_store, "propose_change")
    def test_foreign_origin_cannot_save_or_coach_experiment(self, propose, save):
        headers = {"Origin": "https://example.invalid", "Content-Type": "application/json"}
        for suffix in ("runs", "proposal"):
            self.assertEqual(self.call("POST", f"/api/experiments/exp_test/{suffix}", "{}", headers)[0], 403)
        save.assert_not_called()
        propose.assert_not_called()

    @patch.object(serve.experiment_store, "save_run")
    def test_experiment_run_size_is_bounded(self, save):
        code, _ = self.call("POST", "/api/experiments/exp_test/runs", "{}", {
            "Origin": "http://127.0.0.1:4040", "Content-Type": "application/json", "Content-Length": str(12*1024*1024+1)})
        self.assertEqual(code, 413)
        save.assert_not_called()

    @patch.object(serve.experiment_store, "get_experiment", return_value={"ok": False, "message": "Preserved but unreadable"})
    def test_missing_experiment_is_not_success(self, get):
        self.assertEqual(self.call("GET", "/api/experiments/exp_test/export")[0], 404)

    @patch.object(serve.experiment_store, "reuse_baselines", return_value={"ok": True, "experiment": {"id": "exp_new"}})
    @patch.object(serve.mubit_bridge, "propose")
    def test_reusing_baselines_requires_local_action_and_never_requests_review(self, propose, reuse):
        headers = {"Origin": "http://127.0.0.1:4040", "Content-Type": "application/json"}
        code, body = self.call("POST", "/api/experiments/exp_test/reuse-baselines", "{}", headers)
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["experiment"]["id"], "exp_new")
        reuse.assert_called_once_with("exp_test", {})
        headers["Origin"] = "https://example.invalid"
        self.assertEqual(self.call("POST", "/api/experiments/exp_test/reuse-baselines", "{}", headers)[0], 403)
        reuse.assert_called_once()
        propose.assert_not_called()


    @patch.object(serve.mubit_bridge, "propose")
    def test_learning_reads_and_generic_export_are_local(self, propose):
        for route, name, response in (
            ("/api/learning/status", "status", {"ok": True, "status": {"caseCount": 0}}),
            ("/api/learning/cases", "list_cases", {"ok": True, "cases": []}),
            ("/api/learning/cases/case_test", "get_case", {"ok": True, "case": {"id": "case_test"}}),
            ("/api/learning/export", "export_training", {"ok": True, "export": {"exampleCount": 0}}),
        ):
            with patch.object(serve.learning_store, name, return_value=response) as operation:
                code, body, headers = self.call("GET", route, include_headers=True)
                self.assertEqual(code, 200)
                self.assertEqual(json.loads(body), response)
                operation.assert_called_once()
                if name == "export_training":
                    self.assertIn("dino-approved-corrections.json", headers["Content-Disposition"])
        with patch.object(serve.learning_store, "export_training", return_value={"ok": False, "code": "unavailable_records"}):
            code, _, headers = self.call("GET", "/api/learning/export", include_headers=True)
            self.assertEqual(code, 409)
            self.assertNotIn("Content-Disposition", headers)
        propose.assert_not_called()

    @patch.object(serve.mubit_bridge, "propose")
    def test_learning_mutations_require_local_origin_and_exact_payload_dispatch(self, propose):
        operations = (
            ("experiments/exp_test/import", "import_experiment", "exp_test", {}),
            ("cases/case_test/draft", "save_draft", "case_test", {"expectedRevision": 3, "reason": "Measured evidence"}),
            ("cases/case_test/result-draft", "create_result_draft", "case_test", {"expectedRevision": 3}),
            ("cases/case_test/approve", "approve_correction", "case_test", {"expectedRevision": 4}),
            ("plan/train-02/experiment", "create_planned_experiment", "train-02", {}),
        )
        for suffix, name, identifier, payload in operations:
            with self.subTest(name=name), patch.object(serve.learning_store, name, return_value={"ok": True}) as operation:
                headers = {"Origin": "https://example.invalid", "Content-Type": "application/json"}
                self.assertEqual(self.call("POST", "/api/learning/" + suffix, json.dumps(payload), headers)[0], 403)
                operation.assert_not_called()
                headers["Origin"] = "http://127.0.0.1:4040"
                self.assertEqual(self.call("POST", "/api/learning/" + suffix, json.dumps(payload), headers)[0], 200)
                operation.assert_called_once_with(identifier, payload)
        propose.assert_not_called()

    @patch.object(serve.learning_store, "save_draft")
    def test_learning_size_and_content_type_are_checked_before_write(self, save):
        headers = {"Origin": "http://127.0.0.1:4040", "Content-Type": "application/json", "Content-Length": "32769"}
        self.assertEqual(self.call("POST", "/api/learning/cases/case_test/draft", "{}", headers)[0], 413)
        headers.pop("Content-Length")
        headers["Content-Type"] = "text/plain"
        self.assertEqual(self.call("POST", "/api/learning/cases/case_test/draft", "{}", headers)[0], 415)
        save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
