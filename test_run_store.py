"""Local storage tests; no browser, credentials or network."""
import base64
from concurrent.futures import ThreadPoolExecutor
import copy
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

import run_store as store


def png():
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    raw = b"\x89PNG\r\n\x1a\n"
    raw += chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
    raw += chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff\xff"))
    raw += chunk(b"IEND", b"")
    return "data:image/png;base64," + base64.b64encode(raw).decode()


def record(run_id="run_test"):
    return {"schemaVersion": 1, "id": run_id, "controller": "local-vision-rules", "policyVersion": "vision-v1",
            "mode": "realtime", "startedAt": "2026-10-24T10:00:00.000Z", "endedAt": "2026-10-24T10:00:10.000Z",
            "score": 90, "durationMs": 10000, "outcome": "collision",
            "actions": [{"tMs": 1000, "action": "jump", "score": 10, "reason": "Visible cactus approaching"}],
            "frames": [{"tMs": 1000, "score": 10, "image": png()}]}


class RunStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "runs"
        self.patch = patch.object(store, "RUNS_DIR", self.path)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.directory.cleanup()

    def test_roundtrip_and_export_enforce_local_provenance(self):
        result = store.save_run(record())
        self.assertTrue(result["ok"])
        self.assertEqual(result["run"]["frameCount"], 1)
        self.assertEqual(result["run"]["provenance"], store.PROVENANCE)
        self.assertNotIn("frames", result["run"])
        saved = store.get_run("run_test")
        self.assertEqual(saved["run"]["frames"], record()["frames"])
        self.assertEqual(saved, store.export_run("run_test"))
        self.assertEqual((self.path / "run_test.json").stat().st_mode & 0o777, 0o600)

    def test_duplicate_is_immutable(self):
        store.save_run(record())
        before = (self.path / "run_test.json").read_bytes()
        changed = record(); changed["score"] = 999
        self.assertEqual(store.save_run(changed)["code"], "conflict")
        self.assertEqual(before, (self.path / "run_test.json").read_bytes())

    def test_policy_versions_remain_distinct_and_unknown_is_rejected(self):
        for version in ("vision-v1", "vision-v2"):
            value = record(version); value["policyVersion"] = version
            self.assertTrue(store.save_run(value)["ok"])
            self.assertEqual(store.get_run(version)["run"]["policyVersion"], version)
        value = record("unknown"); value["policyVersion"] = "vision-v99"
        self.assertFalse(store.save_run(value)["ok"])

    def test_identical_retry_is_idempotent_and_preserves_original_saved_time(self):
        original = store.save_run(record())
        before = (self.path / "run_test.json").read_bytes()
        retried = store.save_run(record())
        self.assertTrue(retried["ok"])
        self.assertTrue(retried["alreadySaved"])
        self.assertEqual(retried["run"], original["run"])
        self.assertEqual(before, (self.path / "run_test.json").read_bytes())

    def test_concurrent_publish_never_overwrites(self):
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _: store.save_run(record()), range(8)))
        self.assertTrue(all(result["ok"] for result in results))
        self.assertEqual(sum(result.get("alreadySaved", False) for result in results), 7)
        self.assertFalse(list(self.path.glob(".pending-*")))

    def test_identifier_path_traversal_and_secret_rejected(self):
        for identifier in ("../outside", "/absolute", "abc.json", "mbt_private_key", "a" * 81):
            self.assertFalse(store.save_run(record(identifier))["ok"])
            self.assertEqual(store.get_run(identifier)["code"], "invalid_id")
        self.assertFalse(self.path.exists())

    def test_unknown_fields_and_provider_claims_rejected(self):
        for change in ({"provenance": store.PROVENANCE}, {"key": "private"}, {"mode": "paused"},
                       {"controller": "mubit"}, {"schemaVersion": True}):
            data = record(); data.update(change)
            self.assertFalse(store.save_run(data)["ok"])

    def test_secretlike_reasons_rejected_without_echo(self):
        for secret in ("mbt_unit_private", "Bearer abc123", "api_key=secret", "sk-abcdefghijk"):
            data = record(); data["actions"][0]["reason"] = secret
            result = store.save_run(data)
            self.assertFalse(result["ok"])
            self.assertNotIn(secret, json.dumps(result))

    def test_invalid_time_and_score_bounds(self):
        cases = [{"score": True}, {"durationMs": float("nan")}, {"durationMs": -1},
                 {"startedAt": "2026-10-24T10:00:00"}, {"endedAt": "2026-10-24T09:00:00Z"},
                 {"durationMs": 30000}, {"outcome": "success"}]
        for change in cases:
            data = record(); data.update(change)
            self.assertFalse(store.save_run(data)["ok"], change)
        for field, value in (("tMs", 10001), ("score", 91), ("tMs", True)):
            data = record(); data["actions"][0][field] = value
            self.assertFalse(store.save_run(data)["ok"])

    def test_sequence_order_and_collection_limits(self):
        data = record(); data["actions"] *= 1201
        self.assertFalse(store.save_run(data)["ok"])
        data = record(); data["frames"] *= 61
        self.assertFalse(store.save_run(data)["ok"])
        for name in ("actions", "frames"):
            data = record(); extra = copy.deepcopy(data[name][0]); extra["tMs"] = 1
            data[name].append(extra)
            self.assertFalse(store.save_run(data)["ok"])

    def test_png_validation_and_request_limit(self):
        for image in ("data:text/plain;base64,SGk=", "data:image/png;base64,AAAA", png() + "!",
                      "data:image/png;base64," + "A" * store.MAX_IMAGE_BYTES):
            data = record(); data["frames"][0]["image"] = image
            self.assertFalse(store.save_run(data)["ok"])
        with patch.object(store, "MAX_BYTES", 100):
            self.assertFalse(store.save_run(record())["ok"])

    def test_unreadable_records_are_explicit_not_deleted(self):
        store.save_run(record())
        (self.path / "broken.json").write_text("not JSON")
        listing = store.list_runs()
        self.assertEqual(len(listing["runs"]), 1)
        self.assertEqual(listing["unavailable"][0]["id"], "broken")
        self.assertEqual(store.get_run("broken")["code"], "unavailable")
        self.assertTrue((self.path / "broken.json").exists())

    def test_changed_provenance_or_duplicate_fields_are_corrupt(self):
        store.save_run(record())
        path = self.path / "run_test.json"
        data = json.loads(path.read_text()); data["provenance"]["mubitUsed"] = True
        path.write_text(json.dumps(data))
        self.assertEqual(store.get_run("run_test")["code"], "unavailable")
        path.write_text('{"id":"x","id":"y"}')
        self.assertEqual(store.get_run("run_test")["code"], "unavailable")

    def test_sorting_uses_timestamp_not_string_offset(self):
        first = record("first")
        second = record("second")
        second.update(startedAt="2026-10-24T11:30:00+02:00", endedAt="2026-10-24T11:30:10+02:00")
        store.save_run(second); store.save_run(first)
        self.assertEqual([run["id"] for run in store.list_runs()["runs"]], ["first", "second"])

    def test_retention_refuses_without_deleting_even_corrupt_records(self):
        with patch.object(store, "MAX_RUNS", 2):
            store.save_run(record("first"))
            (self.path / "broken.json").write_text("unknown contents")
            result = store.save_run(record("third"))
        self.assertEqual(result["code"], "storage_full")
        self.assertEqual(sorted(p.name for p in self.path.glob("*.json")), ["broken.json", "first.json"])

    def test_storage_failure_is_unknown_not_empty(self):
        self.path.parent.joinpath("runs").write_text("not a directory")
        self.assertFalse(store.list_runs()["ok"])
        self.assertEqual(store.save_run(record())["code"], "storage_unavailable")

    def test_atomic_publish_failure_leaves_no_record_or_pending_file(self):
        with patch.object(store.os, "link", side_effect=OSError("disk error")):
            self.assertFalse(store.save_run(record())["ok"])
        self.assertFalse(list(self.path.glob("*.json")))
        self.assertFalse(list(self.path.glob(".pending-*")))

    def test_symlink_records_and_directory_are_not_followed(self):
        store.save_run(record())
        outside = self.path.parent / "outside.json"; outside.write_text("do not read")
        (self.path / "link.json").symlink_to(outside)
        self.assertEqual(store.get_run("link")["code"], "unavailable")
        self.assertEqual(store.save_run(record("link"))["code"], "unavailable")
        self.assertEqual(outside.read_text(), "do not read")
        with patch.object(store, "RUNS_DIR", self.path.parent / "alias"):
            store.RUNS_DIR.symlink_to(self.path, target_is_directory=True)
            self.assertFalse(store.list_runs()["ok"])

    def test_nonregular_record_does_not_block_listing(self):
        store.save_run(record())
        os.mkfifo(self.path / "pipe.json")
        self.assertEqual(store.get_run("pipe")["code"], "unavailable")
        listing = store.list_runs()
        self.assertEqual(len(listing["runs"]), 1)
        self.assertEqual(listing["unavailable"][0]["id"], "pipe")


if __name__ == "__main__":
    unittest.main()
