# Performance and correctness review

Measurements below were made on 25 October 2026 with Python 3.14.8 on macOS arm64. They use synthetic records and no provider requests. They describe this local sample, not a hardware-independent benchmark or a model latency result.

## Ranked findings

1. **Request interpretation:** repeated JSON members, non-finite values and an incomplete body could reach a route. This was reproduced and fixed first. It is a correctness improvement with a small parsing cost.
2. **Record listing:** the run store validates each saved record when listing it. Work grows with record count and evidence size. The 100-run fixture below did not justify adding a cache that might conceal changed or corrupt evidence.
3. **Provider latency and repeated inputs:** remote response time remains outside this backend measurement. The comparison repeats one input, so more samples of that same input cannot establish general quality or token savings. Independent examples matter more than optimising a local JSON read here.

## Before and after the parser fix

Seven timing repeats each parsed the same 502-byte synthetic run 10,000 times. The reported values are the median per-call time across those repeats.

| Operation | Median time |
| --- | ---: |
| Original `json.loads()` | 1.93 microseconds |
| Strict request parser | 5.92 microseconds |
| Added validation time | 3.99 microseconds |

The fix is not a speed-up. It trades approximately four microseconds on this small fixture for an explicit validity check. The walk is linear in decoded values and keeps iterator state proportional to nesting depth. JSON decoding still allocates the parsed object before the depth check; request byte limits remain necessary.

The functional result is clearer: all four representative malformed cases reached the mocked route before the fix. After the fix, they return HTTP 400 without executing the route. [DEBUG.md](DEBUG.md) records those cases.

## Storage sample

A temporary store held 100 synthetic runs, each containing one action and one 1×1 PNG. Across 20 list operations, `run_store.list_runs()` took a median of 6.32 milliseconds, with observed values from 6.10 to 9.23 milliseconds. Every read returned all 100 records without unavailable entries.

The data is deliberately small. It does not measure 100 maximum-size screenshot recordings, cold storage, simultaneous readers, maximum request memory, browser frame rate or model inference. The result supports keeping the current implementation for this narrow workload; it does not establish a general performance ceiling.

## Reproduce the measurements

Run from the repository root:

```sh
python3 - <<'PY'
import json, statistics, tempfile, time, timeit
from pathlib import Path
from unittest.mock import patch
import run_store, serve
from test_run_store import record

raw = json.dumps(record('benchmark_fixture'), separators=(',', ':')).encode()
for name, action in [('standard', lambda: json.loads(raw)),
                     ('strict', lambda: serve._request_json(raw))]:
    samples = timeit.repeat(action, number=10000, repeat=7)
    print(name, 'median microseconds:', statistics.median(samples) * 100)

with tempfile.TemporaryDirectory() as temporary:
    with patch.object(run_store, 'RUNS_DIR', Path(temporary) / 'runs'):
        for index in range(100):
            assert run_store.save_run(record(f'benchmark_{index}'))['ok']
        samples = []
        for _ in range(20):
            start = time.perf_counter()
            result = run_store.list_runs()
            samples.append((time.perf_counter() - start) * 1000)
            assert len(result['runs']) == 100 and not result['unavailable']
        print('list median milliseconds:', statistics.median(samples))
        print('list range milliseconds:', min(samples), max(samples))
PY
```

Before optimising record reads, repeat with representative screenshots and record sizes. Any index or cache should preserve corrupt-record reporting, approval revisions and authoritative source checks. The present evidence does not justify replacing file storage or changing the controller algorithm.
