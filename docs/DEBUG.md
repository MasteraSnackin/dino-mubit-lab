# Debugging report

Reviewed on 25 October 2026. This report applies the supplied debugging brief to the local Python service. All reproductions used temporary servers and mocked actions. They did not read credentials, contact a model or modify saved user records.

## Reproduced issue

The POST boundary accepted ambiguous or incomplete JSON before dispatching an action. The handler checked the declared body size but did not compare it with the bytes received. Python's default decoder also accepted repeated field names and non-finite numbers. A deeply nested value had no application-level depth limit.

The expected behaviour was a controlled rejection before configuration reads or route execution. On Python 3.14.8, the four representative inputs below reached the mocked experiment action and returned HTTP 200.

| Input | Before the fix | After the fix |
| --- | --- | --- |
| Two `seed` members in one object | The last value reached the action | HTTP 400; no action |
| A `NaN` numeric value | The non-finite value reached the action | HTTP 400; no action |
| 1,500 nested arrays | The nested value reached the action | HTTP 400; no action |
| Body `{}` with a declared length of 20 bytes, followed by EOF | The short body reached the action | HTTP 400; no action |

These are request-boundary results. The reproduction used a mock to identify dispatch; it does not claim that a real provider accepted these inputs or that a stored record was corrupted.

## Investigation

Three explanations were considered, in order:

1. The HTTP parser accepted values that the application should reject.
2. The route's own schema validation accepted an invalid field.
3. An exception or storage failure happened after route execution.

Replacing the action with a mock isolated the first explanation. The invalid request reached that mock before domain validation or storage was involved. A raw socket with a write-side shutdown reproduced the early EOF case without waiting for a timeout.

The path was `Handler.do_POST()` → body read → permissive `json.loads()` → model setting read → route action.

## Fix

[`serve.py`](../serve.py) now compares the received and declared lengths, rejects duplicate object members, rejects non-finite numbers, and limits the decoded value to 32 nesting levels. An iterative walk checks nested values without creating a recursive Python call chain. Its extra traversal state grows with nesting depth rather than the number of sibling values.

Malformed input returns the existing generic HTTP 400 response. Decoder recursion failures are handled at the same boundary. The error response does not echo the body or exception text. Valid nested JSON continues to reach the route unchanged.

## Verification

Three HTTP tests were added to [`test_serve.py`](../test_serve.py). They cover repeated members at two levels, `NaN`, positive and negative infinity, an overflowing exponent, excessive nesting, early EOF and a valid nested request. The invalid cases assert that neither configuration loading nor the action runs.

```sh
python3 -m unittest test_serve
python3 -m unittest discover -p 'test_*.py'
```

The route suite passed 41 tests. The complete Python suite passed 288 tests. Browser screenshots do not establish this parser behaviour; the socket-level tests are the evidence. The separate visual audit covers the rendered application.

## Saved-run refresh and recovery

A failed history refresh could leave old rows visible beside a raw browser error. On an initial failure, the panel could still say it was reading history. Neither state explained whether the displayed records were current.

[`public/workbench.js`](../public/workbench.js) keeps previously loaded rows when a refresh fails and labels them as previous results. Without cached rows, it says that history is unavailable. Network and response-parsing errors tell the user to check the local server and choose **Refresh runs**. A failed run selection keeps its detail view hidden, disables its review action and exposes the failure message as an alert. A new selection returns the message to a loading status.

Recovery testing found a second issue: after the server returned an empty list, the previous failed-selection message remained. A successful empty refresh now clears that obsolete error when no run is selected and advances the selection revision so an older failed request cannot restore it.

An isolated UI harness checked five cases: cached-refresh failure, first-load failure, selected-run failure, empty-list recovery and a late selection failure after recovery. All five checks passed without a server or provider request. The complete regression run also passed 288 Python tests and 83 JavaScript tests:

```sh
python3 -m unittest discover -p 'test_*.py'
node --test public/*.test.cjs
```

Those suite counts include existing coverage; the UI harness is a separate targeted check. Native browser captures and recovery checks are recorded in the visual audit. No screen-reader acceptance claim is made from the presence of an ARIA role alone.

## Monitoring and limits

Keep the parser tests when adding endpoints. Test the request's shape and length at the common boundary, then validate the action's schema within its module. Do not turn an uncertain provider outcome into a retry merely because the browser lost its response.

The fix adds validation work rather than a speed improvement. The measured cost and a small storage workload are reported in [PERFORMANCE.md](PERFORMANCE.md). This was a bounded review of request handling and existing regression coverage, not a penetration test or a proof that every possible defect has been removed.
