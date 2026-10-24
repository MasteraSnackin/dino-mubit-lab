"""Private-save checks using subprocess doubles; no real Keychain access."""

import io
import json
import shlex
import subprocess
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

import key_store


KEY = "mbt_unit_test_secret_1234567890"


def result(code=0, stdout=b"", stderr=b""):
    return subprocess.CompletedProcess([], code, stdout, stderr)


class KeyStoreTests(unittest.TestCase):
    def setUp(self):
        self.platform = patch.object(key_store.sys, "platform", "darwin")
        self.account = patch.object(key_store.getpass, "getuser", return_value="local test account")
        self.platform.start()
        self.account.start()

    def tearDown(self):
        self.account.stop()
        self.platform.stop()

    @patch.object(key_store.subprocess, "run")
    def test_saves_by_stdin_and_verifies_exact_value(self, run):
        run.side_effect = [result(stderr=b"security> "), result(stdout=(KEY + "\n").encode())]
        self.assertEqual(key_store.save_key({"key": KEY}), {
            "ok": True, "message": "Key saved in macOS Keychain."})
        write, read = run.call_args_list
        self.assertEqual(write.args[0], ["/usr/bin/security", "-i"])
        self.assertEqual(shlex.split(write.kwargs["input"].decode()), [
            "add-generic-password", "-U", "-a", "local test account", "-s",
            "com.local-dino.mubit-api-key", "-w", KEY])
        self.assertEqual(read.args[0], [
            "/usr/bin/security", "find-generic-password", "-a", "local test account",
            "-s", "com.local-dino.mubit-api-key", "-w"])
        for call in (write, read):
            self.assertNotIn(KEY, repr(call.args))
            self.assertNotIn("env", call.kwargs)
            self.assertNotIn("cwd", call.kwargs)
            self.assertFalse(call.kwargs["shell"])
            self.assertTrue(call.kwargs["capture_output"])
            self.assertGreater(call.kwargs["timeout"], 0)

    @patch.object(key_store.subprocess, "run")
    def test_invalid_payloads_never_write(self, run):
        for payload in (None, [], {}, {"key": None}, {"key": True}, {"key": KEY, "model": "x"},
                        {"key": "mbt_short"}, {"key": " " + KEY}, {"key": KEY + "\n"},
                        {"key": KEY + ";echo x"}, {"key": "mbt_" + "x" * 2045},
                        {"key": "sk_provider_secret"}):
            with self.subTest(payload_type=type(payload).__name__):
                response = key_store.save_key(payload)
                self.assertFalse(response["ok"])
                self.assertNotIn(KEY, json.dumps(response))
        run.assert_not_called()

    @patch.object(key_store.subprocess, "run")
    def test_non_macos_never_invokes_a_command(self, run):
        with patch.object(key_store.sys, "platform", "linux"):
            self.assertFalse(key_store.save_key({"key": KEY})["ok"])
        run.assert_not_called()

    @patch.object(key_store.subprocess, "run")
    def test_write_failure_is_sanitised_and_not_reported_as_saved(self, run):
        run.return_value = result(code=1, stdout=KEY.encode(), stderr=("failure " + KEY).encode())
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            response = key_store.save_key({"key": KEY})
        self.assertFalse(response["ok"])
        self.assertNotIn(KEY, json.dumps(response))
        self.assertEqual(stdout.getvalue() + stderr.getvalue(), "")
        self.assertEqual(run.call_count, 1)

    @patch.object(key_store.subprocess, "run")
    def test_different_stored_value_fails_exact_verification(self, run):
        run.side_effect = [result(), result(stdout=b"mbt_some_previous_value\n")]
        with patch.object(key_store.hmac, "compare_digest", wraps=key_store.hmac.compare_digest) as compare:
            response = key_store.save_key({"key": KEY})
        self.assertFalse(response["ok"])
        compare.assert_called_once_with(b"mbt_some_previous_value", KEY.encode())

    @patch.object(key_store.subprocess, "run")
    def test_failed_readback_never_claims_success(self, run):
        run.side_effect = [result(), result(code=44, stdout=KEY.encode(), stderr=KEY.encode())]
        response = key_store.save_key({"key": KEY})
        self.assertFalse(response["ok"])
        self.assertNotIn(KEY, json.dumps(response))

    @patch.object(key_store.subprocess, "run")
    def test_exception_content_is_not_disclosed_and_lock_is_released(self, run):
        for error in (RuntimeError(KEY), subprocess.TimeoutExpired(["security"], 10, output=KEY.encode())):
            run.side_effect = error
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                response = key_store.save_key({"key": KEY})
            self.assertFalse(response["ok"])
            self.assertNotIn(KEY, json.dumps(response))
            self.assertEqual(stdout.getvalue() + stderr.getvalue(), "")
            self.assertTrue(key_store._SAVE_LOCK.acquire(blocking=False))
            key_store._SAVE_LOCK.release()

    @patch.object(key_store.subprocess, "run")
    def test_concurrent_save_is_refused_without_writing(self, run):
        key_store._SAVE_LOCK.acquire()
        try:
            self.assertFalse(key_store.save_key({"key": KEY})["ok"])
        finally:
            key_store._SAVE_LOCK.release()
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
