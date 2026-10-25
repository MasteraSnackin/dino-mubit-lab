# Backend implementation review

The local backend already supports gameplay records, experiments, corrections, compact reviews, memory-assisted requests and provider comparisons. The review kept those workflows and fixed the common request parser. It did not create a cloud deployment or a new provider endpoint.

## Existing responsibilities

| Module | Responsibility | Persistent data |
| --- | --- | --- |
| `serve.py` | Loopback HTTP routes, body validation and static files | No business records of its own |
| `run_store.py` | Validate and preserve local player runs | `runs/` |
| `experiment_store.py` | Fixed-course baselines, one proposal and measured comparison | `experiments/` |
| `learning_store.py` | Correction cases, approvals and reserved scenarios | `learning/` |
| `review_task_store.py` | Compact recurring review and its attempt receipt | `review_tasks/` |
| `review_target_store.py` | Draft and approve the exact compact answer | `review_targets/` |
| `memory_review_store.py` | Select approved context and preserve a later inference | `memory_reviews/` |
| `mubit_bridge.py` | Credential access, connection state and bounded Mubit inference | Session state; keys remain outside source files |
| `provider_comparison.py` | One six-request Mubit/direct-provider pilot | `provider_comparisons/` |
| `local_adapter.py` | Explicit local LoRA preparation, execution and verification | `local_adapters/` |

Every data directory in the table is outside the served `public/` directory and excluded from Git. The local trainer runs through its CLI; the HTTP server does not offer an endpoint that starts training.

## Request path verified

The route tests exercise local host and origin checks, input limits, dispatch, read/export behaviour and failure responses. They confirm that reading a receipt does not trigger inference and that a failed export does not become a successful download.

The parser fix rejects ambiguous, incomplete and excessively nested JSON before loading configuration or calling an action. The source and regression details are in [DEBUG.md](DEBUG.md).

## Completion evidence

```sh
python3 -m unittest test_serve
python3 -m unittest discover -p 'test_*.py'
```

The focused run passed 41 route tests. The full run passed 288 Python tests. Tests use temporary data and provider doubles. No real API requests, keys or original user records were needed for this review.

## Deployment boundary

`python3 serve.py --open` starts the application on `127.0.0.1:4040`. It is a local prototype with POSIX file locking and a macOS Keychain option. The current code has no hosted authentication, tenant isolation, managed database or production web server.

The supplied Builder prompt mentioned Modal and serverless endpoints. Those do not match this app's deployment and were not added. Public hosting would need a separate deployment design, durable storage and credentials held by the server. Python's own documentation warns that `http.server` is not a production server. [Python HTTP server documentation](https://docs.python.org/3.14/library/http.server.html).

## Follow-up work

The next functional extension should follow a measured requirement: for example a verified correction submission contract, or an independent evaluation workflow for the local adapter. The current review did not establish either contract or a trained Mubit model version.
