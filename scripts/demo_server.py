#!/usr/bin/env python3
"""Run the app with temporary records and no provider access for local captures."""
import argparse
import functools
import os
from pathlib import Path
import signal
import sys
import tempfile
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import serve


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=4041)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Choose a local port from 1024 to 65535.")
    hosts = {f"127.0.0.1:{args.port}", f"localhost:{args.port}"}
    serve.ORIGINS = {f"http://{host}" for host in hosts}

    class DemoHandler(serve.Handler):
        def valid_host(self):
            return self.headers.get("Host") in hosts

        def log_message(self, *_):
            pass

    # This separate process must not use the account's real key or settings.
    os.environ.pop("MUBIT_API_KEY", None)
    os.environ.pop("DINO_MUBIT_MODEL", None)
    os.environ.pop("DINO_COMPARISON_CASE_ID", None)
    serve.load_model_setting = lambda: None
    serve.mubit_bridge._keychain_key = lambda: ""
    serve.key_store.save_key = lambda *_: {"ok": False, "message": "This temporary demo does not accept credentials."}

    def no_provider_request(*_, **__):
        raise RuntimeError("Provider access is disabled in this temporary demo.")

    serve.mubit_bridge._open_request = no_provider_request
    serve.provider_comparison._open_request = no_provider_request

    with tempfile.TemporaryDirectory(prefix="dino-demo-") as directory:
        temporary = Path(directory)
        for module, attribute, folder in [
            (serve.run_store, "RUNS_DIR", "runs"),
            (serve.experiment_store, "EXPERIMENTS_DIR", "experiments"),
            (serve.learning_store, "LEARNING_DIR", "learning"),
            (serve.review_task_store, "REVIEW_TASKS_DIR", "review_tasks"),
            (serve.review_target_store, "REVIEW_TARGETS_DIR", "review_targets"),
            (serve.memory_review_store, "MEMORY_REVIEWS_DIR", "memory_reviews"),
            (serve.local_adapter, "ADAPTERS_DIR", "local_adapters"),
            (serve.provider_comparison, "COMPARISONS_DIR", "provider_comparisons"),
        ]:
            setattr(module, attribute, temporary / folder)
        server = ThreadingHTTPServer(("127.0.0.1", args.port),
            functools.partial(DemoHandler, directory=str(ROOT / "public")))
        signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
        print(f"Temporary local demo: http://127.0.0.1:{args.port}/", flush=True)
        print("Provider access is disabled. Records are removed when this process stops.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
