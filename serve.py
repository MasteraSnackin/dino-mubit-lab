#!/usr/bin/env python3
"""Serve the standalone dinosaur game on this Mac only."""
import argparse
import functools
import json
import os
import re
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen
import webbrowser

import mubit_bridge
import run_store
import key_store
import experiment_store
import learning_store
import review_task_store
import review_target_store
import memory_review_store
import local_adapter
import provider_comparison

ROOT = Path(__file__).resolve().parent
URL = "http://127.0.0.1:4040/"
IDENTITY = b"dino-local-5455bfa-local-lab-v2\n"
ORIGINS = {"http://127.0.0.1:4040", "http://localhost:4040"}


def load_model_setting():
    """Only non-secret model selection is read from this local file."""
    try:
        settings = json.loads((ROOT / "mubit-settings.json").read_text())
        model = settings.get("model")
        if isinstance(model, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}", model) and "mbt_" not in model:
            os.environ["DINO_MUBIT_MODEL"] = model
        else:
            os.environ.pop("DINO_MUBIT_MODEL", None)
    except FileNotFoundError:
        return
    except (OSError, ValueError, AttributeError):
        os.environ.pop("DINO_MUBIT_MODEL", None)


class Handler(SimpleHTTPRequestHandler):
    def valid_host(self):
        return self.headers.get("Host") in {"127.0.0.1:4040", "localhost:4040"}

    def json_reply(self, value, status=200, download=None):
        data = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(data)))
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{download}"')
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        if not self.valid_host():
            return self.json_reply({"ok": False, "message": "Local access only."}, 403)
        if self.path == "/api/mubit/status":
            load_model_setting()
            return self.json_reply(mubit_bridge.connection_status())
        if self.path == "/api/runs":
            return self.json_reply(run_store.list_runs())
        if self.path == "/api/experiments":
            return self.json_reply(experiment_store.list_experiments())
        if self.path == "/api/learning/status":
            return self.json_reply(learning_store.status())
        if self.path == "/api/learning/cases":
            return self.json_reply(learning_store.list_cases())
        if self.path == "/api/local-adapter/status":
            return self.json_reply(local_adapter.status())
        if self.path == "/api/local-adapter/export":
            result = local_adapter.export()
            return self.json_reply(result, 200 if result.get("ok") else 409,
                                   "dino-local-adapter-receipt.json" if result.get("ok") else None)
        if self.path in {"/api/provider-comparison", "/api/provider-comparison/export"}:
            load_model_setting()
            exporting = self.path.endswith("/export")
            result = provider_comparison.export() if exporting else provider_comparison.status()
            return self.json_reply(result, 200 if result.get("ok") else 409,
                                   "dino-provider-comparison.json" if exporting and result.get("ok") else None)
        if self.path == "/api/learning/export":
            result = learning_store.export_training({})
            return self.json_reply(result, 200 if result.get("ok") else 409,
                                   "dino-approved-corrections.json" if result.get("ok") else None)
        learning_case = re.fullmatch(r"/api/learning/cases/([A-Za-z0-9_-]{1,80})", self.path)
        if learning_case:
            result = learning_store.get_case(learning_case[1])
            return self.json_reply(result, 200 if result.get("ok") else 404)
        text_review = re.fullmatch(r"/api/review-task/cases/([A-Za-z0-9_-]{1,80})(/export)?", self.path)
        if text_review:
            load_model_setting()
            result = review_task_store.get_case_review(text_review[1])
            return self.json_reply(result, 200 if result.get("ok") else 409,
                                   f"dino-text-review-{text_review[1]}.json" if text_review[2] and result.get("ok") else None)
        review_target = re.fullmatch(r"/api/review-target/cases/([A-Za-z0-9_-]{1,80})(/export)?", self.path)
        if review_target:
            load_model_setting()
            operation = review_target_store.export_target if review_target[2] else review_target_store.get_target
            result = operation(review_target[1])
            return self.json_reply(result, 200 if result.get("ok") else 409,
                                   f"dino-reviewed-answer-{review_target[1]}.json" if review_target[2] and result.get("ok") else None)
        memory_review = re.fullmatch(r"/api/memory-review/cases/([A-Za-z0-9_-]{1,80})(/export)?", self.path)
        if memory_review:
            load_model_setting()
            result = memory_review_store.get_case_review(memory_review[1])
            return self.json_reply(result, 200 if result.get("ok") else 409,
                                   f"dino-memory-review-{memory_review[1]}.json" if memory_review[2] and result.get("ok") else None)
        experiment = re.fullmatch(r"/api/experiments/([A-Za-z0-9_-]{1,80})(/export)?", self.path)
        if experiment:
            result = experiment_store.get_experiment(experiment[1])
            return self.json_reply(result, 200 if result.get("ok") else 404,
                                   f"dino-comparison-{experiment[1]}.json" if experiment[2] and result.get("ok") else None)
        match = re.fullmatch(r"/api/runs/([A-Za-z0-9_-]{1,80})(/export)?", self.path)
        if match:
            result = run_store.get_run(match[1])
            return self.json_reply(result, 200 if result.get("ok") else 404,
                                   f"dino-run-{match[1]}.json" if match[2] and result.get("ok") else None)
        if self.path == "/__dino_health":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(IDENTITY)))
            self.end_headers()
            self.wfile.write(IDENTITY)
        else:
            super().do_GET()

    def do_POST(self):
        if not self.valid_host() or self.headers.get("Origin") not in ORIGINS:
            return self.json_reply({"ok": False, "message": "Open the local game to use this action."}, 403)
        routes = {
            "/api/mubit/check": lambda _: mubit_bridge.check_connection(),
            "/api/mubit/decide": mubit_bridge.decide,
            "/api/mubit/review": review_browser_run,
            "/api/mubit/key": key_store.save_key,
            "/api/provider-comparison": provider_comparison.run,
            "/api/runs": run_store.save_run,
            "/api/experiments": experiment_store.create_experiment,
        }
        experiment_run = re.fullmatch(r"/api/experiments/([A-Za-z0-9_-]{1,80})/runs", self.path)
        experiment_proposal = re.fullmatch(r"/api/experiments/([A-Za-z0-9_-]{1,80})/proposal", self.path)
        experiment_reuse = re.fullmatch(r"/api/experiments/([A-Za-z0-9_-]{1,80})/reuse-baselines", self.path)
        if experiment_run:
            routes[self.path] = lambda payload: experiment_store.save_run(experiment_run[1], payload)
        if experiment_proposal:
            routes[self.path] = lambda payload: experiment_store.propose_change(experiment_proposal[1], payload)
        if experiment_reuse:
            routes[self.path] = lambda payload: experiment_store.reuse_baselines(experiment_reuse[1], payload)
        learning_import = re.fullmatch(r"/api/learning/experiments/([A-Za-z0-9_-]{1,80})/import", self.path)
        learning_edit = re.fullmatch(r"/api/learning/cases/([A-Za-z0-9_-]{1,80})/(draft|result-draft|approve)", self.path)
        learning_plan = re.fullmatch(r"/api/learning/plan/([A-Za-z0-9_-]{1,80})/experiment", self.path)
        if learning_import:
            routes[self.path] = lambda payload: learning_store.import_experiment(learning_import[1], payload)
        if learning_edit:
            operation = {"draft": learning_store.save_draft, "result-draft": learning_store.create_result_draft,
                         "approve": learning_store.approve_correction}[learning_edit[2]]
            routes[self.path] = lambda payload: operation(learning_edit[1], payload)
        if learning_plan:
            routes[self.path] = lambda payload: learning_store.create_planned_experiment(learning_plan[1], payload)
        text_review = re.fullmatch(r"/api/review-task/cases/([A-Za-z0-9_-]{1,80})/review", self.path)
        if text_review:
            routes[self.path] = lambda payload: review_task_store.request_review(text_review[1], payload)
        review_target = re.fullmatch(r"/api/review-target/cases/([A-Za-z0-9_-]{1,80})/(draft|approve)", self.path)
        if review_target:
            operation = review_target_store.save_target if review_target[2] == "draft" else review_target_store.approve_target
            routes[self.path] = lambda payload: operation(review_target[1], payload)
        memory_review = re.fullmatch(r"/api/memory-review/cases/([A-Za-z0-9_-]{1,80})/review", self.path)
        if memory_review:
            routes[self.path] = lambda payload: memory_review_store.request_review(memory_review[1], payload)
        saved_review = re.fullmatch(r"/api/runs/([A-Za-z0-9_-]{1,80})/review", self.path)
        if saved_review:
            routes[self.path] = lambda payload: review_saved_run(saved_review[1], payload)
        if self.path not in routes:
            return self.json_reply({"ok": False, "message": "Unknown action."}, 404)
        if self.headers.get_content_type() != "application/json":
            return self.json_reply({"ok": False, "message": "JSON is required."}, 415)
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            size = -1
        maximum = (12 * 1024 * 1024 if self.path == "/api/runs" or experiment_run
                   else 4096 if self.path in {"/api/mubit/key", "/api/provider-comparison"} or text_review or memory_review
                   else 32768 if self.path.startswith("/api/learning/") or review_target else 1_100_000)
        if size < 1 or size > maximum or self.headers.get("Transfer-Encoding"):
            return self.json_reply({"ok": False, "message": "The request is too large or incomplete."}, 413)
        self.connection.settimeout(10)
        try:
            payload = json.loads(self.rfile.read(size))
        except (ValueError, OSError):
            return self.json_reply({"ok": False, "message": "The request could not be read."}, 400)
        if not isinstance(payload, dict):
            return self.json_reply({"ok": False, "message": "A JSON object is required."}, 400)
        load_model_setting()
        try:
            return self.json_reply(routes[self.path](payload))
        except Exception:
            # Never forward exception text: it could contain a credential.
            return self.json_reply({"ok": False, "message": "The local service could not complete this action."}, 500)

    def list_directory(self, path):
        self.send_error(404)
        return None


def review_browser_run(payload):
    if payload.get("source") == "saved-local-run":
        return {"ok": False, "message": "Select a stored run to request its review."}
    return mubit_bridge.review(payload)


def review_saved_run(run_id, payload):
    if payload:
        return {"ok": False, "message": "Saved-run reviews use the stored record only."}
    stored = run_store.get_run(run_id)
    if not stored.get("ok"):
        return stored
    run = stored["run"]
    actions = run["actions"][-40:]
    return mubit_bridge.review({
        "source": "saved-local-run", "runId": run["id"], "mode": run["mode"],
        "controller": run["controller"], "policyVersion": run["policyVersion"],
        "score": run["score"], "durationMs": run["durationMs"], "outcome": run["outcome"],
        "actionCount": len(run["actions"]), "actionsTruncated": len(run["actions"]) > len(actions),
        "actions": actions,
    })


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--open", action="store_true", help="Open in your default browser")
    args = parser.parse_args()
    try:
        server = ThreadingHTTPServer(("127.0.0.1", 4040),
            functools.partial(Handler, directory=str(ROOT / "public")))
    except OSError as exc:
        try:
            with urlopen(URL + "__dino_health", timeout=2) as response:
                is_ours = response.read(100) == IDENTITY
        except Exception:
            is_ours = False
        if not is_ours:
            raise SystemExit(f"Could not start the game: {exc}")
        print(f"Dino is already running at {URL}", flush=True)
        if args.open:
            webbrowser.open(URL)
        return
    print(f"Dino is running at {URL}\nPress Ctrl+C to stop.", flush=True)
    if args.open:
        webbrowser.open(URL)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
