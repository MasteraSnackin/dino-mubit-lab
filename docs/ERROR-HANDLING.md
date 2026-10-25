# Error handling and recovery

The application distinguishes invalid input, unavailable local evidence, failed provider calls and uncertain outcomes. It preserves an uncertain record so that a reload cannot silently turn it into another paid request.

## Request boundary

[`serve.py`](../serve.py) accepts the local host names and requires a local `Origin` for POST actions. Mutations require a JSON object. Route-specific size limits, exact body length, unique object members, finite numbers and a 32-level nesting limit are checked before dispatch.

| Condition | Response | Recovery |
| --- | --- | --- |
| Unrecognised host or mutation origin | HTTP 403 | Open the local app and use its controls |
| Unknown action | HTTP 404 | Use a documented endpoint |
| Wrong content type | HTTP 415 | Send `application/json` |
| Missing, invalid or excessive declared length; transfer encoding | HTTP 413 | Send a bounded complete body |
| Malformed, ambiguous, non-finite, too deeply nested or truncated JSON | HTTP 400 | Correct the input before another attempt |
| Unexpected exception during an action | HTTP 500 with a generic message | Read saved state before deciding whether retrying is safe |

Many domain failures return HTTP 200 with `ok: false`; some read and export routes use HTTP 404 or 409. Clients must inspect both the HTTP result and the JSON `ok` field. A transport success alone is not an application success.

## Local records

The stores use bounded records, file checks, locks and atomic publication or replacement. Revisions prevent a stale tab from approving a newer draft. Identical run saves can return the original immutable record; a conflicting save cannot replace it.

Unreadable records remain on disk and are reported as unavailable. They are not replaced with empty history. An export can stop when evidence is missing or invalid. Source hashes check that the saved answer still refers to the expected input and approval.

If a save cannot be confirmed, refresh the relevant records first. Do not delete the evidence to make an operation appear retryable. Keep a backup before any manual recovery outside the app.

## Browser recovery

When saved-run history cannot be refreshed, the workbench retains cached rows and labels them as previously loaded results. If no rows were loaded, it reports that history is unavailable. Network and malformed-response errors give a local-server check and a **Refresh runs** action instead of displaying the browser's raw error.

A failed selection hides the run details and leaves provider review disabled. Its message has an alert role; selecting another run restores the loading status role. Refresh controls become available again after the request settles. A successful refresh replaces the cached list and clears the refresh warning.

If the refreshed list is empty and no run is selected, the workbench also clears the obsolete selection error. Advancing the selection revision prevents a late response from the previous selection from restoring that error after recovery.

These behaviours were checked with an isolated UI harness using synthetic failures. Browser captures provide separate visual evidence. The application does not automatically replay the failed request as a provider action.

## Provider calls

The Mubit bridge validates bounded inputs and outputs, uses fixed HTTPS endpoints, refuses redirects and retains only permitted response metadata. A shared session allowance and request lock limit concurrent inference.

Experiment coaching, compact review, memory review and the provider comparison write attempt state before sending a request. A pending record after interruption means that the result is unknown. Reloading reads the receipt; it does not resend the call. A failed or completed single-attempt workflow remains recorded.

The supplied error-handling template includes automatic backoff and circuit-breaker examples. They were not copied into inference actions: those examples do not establish that repeating a request is safe. The current application needs durable request identity and a verified provider recovery contract before it could add automatic recovery after an uncertain send.

## Credentials and diagnostics

The UI's Mubit key entry writes to macOS Keychain. The direct OpenAI comparison key is held only for that request. Credential-bearing request bodies and raw upstream error text are not written into public diagnostics.

Expected failures use short error codes and actionable messages where the store exposes them. Broad exception handling at the outer mutation boundary deliberately returns a generic message because the exception may contain a key. Detailed local fixtures test those paths without real credentials.

## Regression checks

```sh
python3 -m unittest test_serve test_run_store test_experiment_store
python3 -m unittest test_review_task_store test_review_target_store
python3 -m unittest test_memory_review_store test_provider_comparison
python3 -m unittest test_mubit_bridge test_key_store
```

Coverage includes malformed inputs, revision conflicts, concurrent writes, corrupt or missing evidence, provider failures, duplicate prevention, source changes and credential-safe responses. The full Python suite passed 288 tests during this review. These checks use synthetic records and mocked providers.

The project does not contain the template's SQL transaction layer, Rust or Go services, distributed message queue or serverless deployment. Adding those components would require a separate need and design.
