# Technical research and next experiments

This review checked the implementation and the primary references below on 25 October 2026. It separates changes supported by a reproduced defect from ideas that need new data or provider access.

## Current approaches

The browser player uses canvas observations and bounded timing rules. Fixed seeds support repeated courses, and stored action/frame evidence checks consistency. The backend uses bounded JSON documents, locks, hashes and revision checks. Memory assistance supplies approved examples in a later prompt. The optional trainer changes a separate local model through MLX LoRA.

These approaches keep the prototype inspectable. They do not establish optimal gameplay, a generally better learned controller or lower provider cost.

## Quick wins

The completed quick win is strict request parsing. RFC 8259 recommends unique object member names and describes inconsistent handling when they repeat. Python documents permissive handling of repeated members and non-finite numbers. The app now rejects those values, truncated bodies and excessive nesting before dispatch. [RFC 8259](https://www.rfc-editor.org/info/rfc8259/), [Python JSON documentation](https://docs.python.org/3.14/library/json.html).

The regression tests show the behavioural change. The local measurement found about 3.99 microseconds of added parsing work on a 502-byte fixture. The measured trade-off is in [PERFORMANCE.md](PERFORMANCE.md).

## Medium efforts

**Measure representative record sizes before introducing an index.** The 100-run sample contains tiny images. A benchmark with normal screenshots, concurrent reads and altered records would reveal whether repeated full validation is a practical bottleneck. A future index must not turn unavailable evidence into apparently valid summaries. This recommendation follows the current storage design and measurement; it is not a claimed speed-up.

**Evaluate the local adapter on independent examples.** MLX's training guide supports separate training, validation and test files, with an explicit test mode. Keep the app's reserved scenarios out of approved training examples. Score both answer structure and measured outcome on an independent set; report changed answers separately from confirmed references. Do not use a replay of the training example as evidence of generalisation. [MLX LoRA guide, version 0.32.2](https://github.com/ml-explore/mlx-lm/blob/v0.32.2/mlx_lm/LORA.md).

**Verify the actual Mubit training path.** The current first-adapter guide says training must be enabled for the workspace and normally begins at 25 kept examples. Sent-call count and kept-example count differ. It describes task progress, adapter history and a comparison for a passing version. The project's workspace setting, kept examples, correction ingestion and resulting adapter version were not checked by this documentation task. [Mubit first-adapter guide](https://docs.mubit.ai/guides/first-adapter).

Mubit's import guide describes importing calls from Langfuse, Braintrust or Pydantic Logfire. That is not evidence that Dino's exported correction JSON can be uploaded directly. Preserve the local export and verify a supported mapping before implementing submission. [Mubit import guide](https://docs.mubit.ai/guides/import-past-calls).

## Research experiments

1. Compare the local rules with a trained decision policy on reserved course families. Keep observation inputs, speed and action deadlines equal. Record collisions, survival and late decisions before choosing a policy.
2. Compare Mubit with direct provider requests across distinct review inputs. Freeze the input and generation settings for each pair, record token categories and latency, and keep unavailable model provenance explicit. More repetitions of one example are not more independent cases.
3. Test memory selection with and without approved references on independent inputs. Separate wrong answers corrected by a reviewer from answers merely confirmed. Memory retrieval and model training need different claims and receipts.

These are proposed experiments. No reinforcement-learning policy, new provider workload or new training run was created during this review.

## Limits and decisions

The project remains a local Python service. A web framework or serverless move is not supported by a measured requirement here. Python's standard HTTP server is intended for basic use and is not recommended for production; public hosting needs a separate design. [Python HTTP server documentation](https://docs.python.org/3.14/library/http.server.html).

The Mubit HTTP guide confirms the provider-prefixed gateway pattern used by the bridge. It does not by itself prove that a particular request used a trained adapter. [Mubit HTTP guide](https://docs.mubit.ai/guides/connect-http).

The general API-reference fetch timed out during this review. The successfully read guides support the statements above; no missing API details were inferred from that failed fetch.
