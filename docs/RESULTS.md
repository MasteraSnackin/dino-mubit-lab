# Mubit versus OpenAI directly: recorded results

[Back to the README](../README.md) · [Comparison protocol](../PROVIDER-COMPARISON-README.txt) · [Machine-readable measurements](results/provider-pilot-summary.json)

In this six-request pilot, Mubit used **1.066% more total tokens** and had **6.774% lower mean request latency** than OpenAI directly. Both routes passed the same local answer checks. This is one observed comparison of repeated responses to one input; it does not establish general token savings, faster responses or better answers.

The pilot completed on **24 October 2026 at 23:52 UTC**. Publishing this report made no new provider calls.

## What was compared

One frozen text review asked whether a tested gameplay suggestion improved the recorded outcome. The expected assessment was `unchanged`, with the decision `retain_baseline`.

The app sent three matched pairs of requests, alternating the order: Mubit first, direct first, then Mubit first. Both routes received the same grouped messages, requested `gpt-6-luna`, required a JSON answer, capped completion tokens at 2,000 and used `store: false`. Temperature was not explicitly set. No memory examples were added and no inference request was automatically retried. All six responses succeeded and returned the requested model identifier.

## Measurements

Token counts are provider-reported. Latency is elapsed request time measured by the local app. Means are rounded to three decimal places.

| Mean per response | Through Mubit | OpenAI directly |
| --- | ---: | ---: |
| Prompt tokens | 689.000 | 689.000 |
| Completion tokens | 227.667 | 218.000 |
| Total tokens | 916.667 | 907.000 |
| Request latency | 3,133.333 ms | 3,361.000 ms |

Across all three responses, Mubit used **2,750 total tokens**, compared with **2,721** directly. Monetary cost was not measured.

| Matched pair | Mubit total tokens | Direct total tokens | Mubit latency | Direct latency |
| --- | ---: | ---: | ---: | ---: |
| 1 | 912 | 928 | 2,995 ms | 4,402 ms |
| 2 | 921 | 933 | 3,091 ms | 3,083 ms |
| 3 | 917 | 860 | 3,314 ms | 2,598 ms |

Mubit was faster in one of the three pairs. The slower first direct response accounts for the lower overall mean through Mubit; this small sample does not establish a consistent speed advantage.

## Answer checks and limits

Both routes passed **3 out of 3** responses on each local check: valid JSON/schema, the expected assessment, the expected decision and mention of all required measurements. These checks do not establish general answer quality or validate the reasoning behind an answer.

Three repetitions of one input are not three independent test cases. Output length, reasoning, caching, network conditions and server scheduling can affect the results. Matching model identifiers do not prove identical backend weights or serving configuration. This comparison did not verify model training or a hosted model update.

The report was derived from saved request and response records whose stored integrity hashes were checked before publication. The public JSON contains allowlisted measurements and settings. It omits credentials, raw prompts and replies, private identifiers, local paths and model weights. Readers can check the arithmetic, but this summary alone does not allow independent reproduction of the original requests.

The [recorded demo](https://github.com/MasteraSnackin/dino-mubit-lab/releases/tag/demo-video-v2) also shows these saved results; it is not a new live comparison.
