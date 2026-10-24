Mubit versus OpenAI directly — controlled pilot

Purpose
Test whether sending the same recurring review through Mubit changes the answer,
reported token use or observed response time relative to OpenAI directly.
This is separate from the earlier memory-versus-no-memory experiment.

First run and source selection
A fresh clone contains no saved reviews. First complete a two-course controller
comparison, import and approve its correction, then complete its compact text
review. This bounded pilot only accepts a successful review made with gpt-6-luna
whose measured survival outcome is unchanged on both courses. Other outcomes or
unfinished reviews are not eligible. The comparison remains not started until
compatible local evidence exists; it does not manufacture a sample.

By default, the most recently completed eligible review is selected. To pin a
particular locally saved case, set DINO_COMPARISON_CASE_ID to its case identifier
before starting the server. An unavailable or ineligible explicit selection does
not silently fall back to another case. Inspect the frozen input in the panel
before entering the direct OpenAI key. Once a pilot starts, its saved manifest
retains the selected case and measurements even if the live case later changes.
The Mubit connection must be verified for gpt-6-luna with at least three inference
calls remaining, and the direct account must have access to the same model.

Protocol
One frozen real tested-review input, three matched pairs, six inference requests.
The order alternates between the two routes. Both arms receive the same model,
grouped messages, JSON answer requirement, output-token cap and store:false.
No memory example is added. A request-body hash records the equality of inputs.
The selected model is gpt-6-luna. Response model and usage metadata are retained
when available. No inference request is automatically retried.

The Mubit route uses the saved Mubit key and its shared call limit. The direct
route uses an OpenAI key entered into the local page for this run only. The key
is cleared from the field when submitted, held only in server memory, and never
written to a receipt, project file or log. Keys are sent only to their own fixed
HTTPS endpoint. No redirects are followed. A direct model-access check precedes
inference. Failed setup may be corrected before a run starts; a started pilot
cannot be resumed or repeated by refreshing or clicking again.

Mubit benchmark calls use x-middleware-record:off to request that these repeated
measurements are not recorded. The app does not independently verify provider
retention or training behaviour. Existing reviews, approvals, model
adapters and gameplay settings are not changed by the comparison.

Measurements
Record prompt, completion and total tokens separately. Cache and reasoning-token
details, when returned, are subsets, not additional tokens to add to the total.
Measure elapsed request time. Check the answer format and measured outcome label;
the expected decision for this unchanged-outcome pilot is retain_baseline. The
numeric check looks for every score and survival-step count from the frozen
baseline and candidate runs, allowing comma-separated integers. Literal mention
is a limited check, not a general quality score or verification of reasoning. Missing usage or mismatched returned models stays unverified.
Exported evidence is private and contains no credentials.

Interpretation
Three repetitions of one input form a small paired pilot, not three independent
test cases. Different output lengths, reasoning, caching, network conditions and
server scheduling can affect observed counts or timing. No provider cost is
inferred from token totals, and no saved Mubit adapter is assumed to have served
a request. A valid gateway response is not evidence of model training. Broader
claims require more independent inputs and verified serving/model provenance.

Primary references checked for this comparison
https://docs.mubit.ai/reference/api
https://docs.mubit.ai/guides/connect-http
https://developers.openai.com/api/docs/models/gpt-6-luna
