Compact tested-change review
===========================

Task version: tested-review-text-v1

This recurring task asks Mubit to assess one already tested controller change.
Its input contains the original bounded proposal, the baseline and candidate
settings, and the score, survival ticks and outcome from two saved courses.
It runs after the normal-speed game trials. It does not control or pause play.

The gateway receives text only. Screenshots, movement logs, hidden obstacle
coordinates, case/experiment identifiers, correction reasons, the approved
correction and the expected assessment are excluded. A stable review identifier
is used for request tracing. The source is an existing saved learning case;
browser-supplied measurements and target answers are not accepted.

Local approval is a prerequisite for a request, but it is not model training.
The older proposal-review correction is kept only in the private local receipt
to preserve what was approved at the time. It uses a different answer schema
and is not approval of the later compact model answer. The application does
not claim that Mubit received that target, retained a training example, changed an adapter or published a
new model version. Those claims require a separately verified provider contract.

Public store API
----------------

get_case_review(case_id)
  Returns an input preview and any saved attempt. No inference is triggered.
  An unapproved case may be previewed, with requestAllowed false.

request_review(case_id, {"expectedRevision": <current integer revision>})
  Requires the current saved revision to be approved and the existing Mubit
  connection to be ready. A pending receipt is written before calling the
  gateway. The gateway checks the expected model under its configuration lock
  and retains the existing shared call limit and one-request-at-a-time rule.

Both return {ok: true, task: {...}} on a readable task. The task contains:
  taskVersion, caseId, caseRevision, model, configuredModel, input, attempt,
  expectedAssessment, connectionVerified, requestAllowed,
  approvedCorrectionSent: false, modelUpdateVerified: false, message.

An attempt has status pending, succeeded or failed; source case ID, revision
and hash; the original approved target; input and input hash; bound model;
timestamps; safe request ID, latency, usage and remaining call allowance; and
result plus assessmentMatchesMeasurements when a valid answer was returned.
Usage is null or contains promptTokens, completionTokens and totalTokens;
individual token counts may be null when the provider did not establish them.

The result is exactly:
  assessment: improved_on_both | unchanged | worse_on_both | mixed | inconclusive
  decision: retain_baseline | test_more | consider_candidate
  rationale: bounded text
  caveat: bounded text

expectedAssessment stays local. It is read from the saved comparison and
checked against both courses' survival-tick changes. A structurally valid but
incorrect model assessment is retained and scores false for this narrow local
check. This check does not grade the rationale, prove general game quality or
measure training improvement. There is no trained-model before/after result.

Reviewing the exact compact answer
---------------------------------

The "Review this saved answer" editor is attached to a successful compact reply.
Its source is that exact task version, input hash, request ID, model and answer.
The initial fields show the model answer, but no draft or approval is created
until explicitly saved. Save a reason with the answer, inspect the saved draft,
then approve that exact revision separately. Changing it later invalidates the
approval. Unsaved edits prevent selection changes and conflicting actions.

The target schema is exactly assessment, decision, rationale and caveat.
It is not the earlier proposal-review schema kind/rationale/caveat/evidenceRefs.
An unchanged approved answer is a confirmed_reference. An edited approved answer
is a corrected_answer. The former is not counted as fixing a model error.
Approval is local; no signed-in reviewer identity is verified. Record whether
the reviewer was a person or an AI assistant in the review reason.

review_target_store exposes get_target, save_target, approve_target and
export_target. Private review_targets/ records use 0700 directories, 0600 files,
atomic writes, locks, bounded revision histories and hash validation. They do
not mutate the inference receipt or the earlier correction records.

The approved export contains the compact input, grouped messages, original
answer, approved answer, reason, source snapshot/hash and approval revision.
Its format is dino-compact-review-target-v1. It remains a generic local format:
providerCompatible:false, submitted:false and modelUpdateVerified:false.
Exporting does not make a provider request or start training.

One attempt and private custody
------------------------------

review_tasks/ is private local evidence: directory mode 0700, files 0600.
Do not serve, commit or publish this directory. The store uses process/thread
locks, bounded JSON, schema checks, content hashes and atomic file writes.
Its limit is 100 attempts. Corrupt or unknown records fail closed; they are not
silently treated as an empty history.

The compact input hash excludes the tracing runId. Case IDs, source run IDs
and correction revisions never enter the input. Identical text input therefore
resolves to the same attempt even after a correction changes or is imported
under a different case. The original receipt's source revision and approved
target remain attached to that attempt; the current case is not substituted.

There are no automatic or manual retries for a saved pending, failed or
succeeded attempt. A pending record after interruption means the provider
outcome is unknown. Reloading or exporting the receipt never resends it.
Do not delete receipts or change input text merely to repeat a provider call.

The first request binds this task version to its configured model in task.json.
Changing the configured model blocks new requests for this task. Existing
receipts remain readable. A future trained model comparison needs its own
explicit evaluation design rather than silently rebinding this task.

Reserved evaluation seeds are blocked, including a mixed pair containing only
one reserved course. The inference gateway receives no approved target and is
not an evaluation-run collector.

Offline checks
--------------

Run: python3 -m unittest -v test_review_task_store
     python3 -m unittest -v test_review_target_store

Tests use temporary directories and mock connection/provider responses, with
network and Keychain guards. They cover source-file custody, target exclusion,
pending-before-request behaviour, concurrent and repeated requests, revision
checks, model binding, held-out seeds, incorrect answers, safe metadata, corrupt
storage and receipt-write failures. Passing these checks is local evidence;
it does not establish a live Mubit response or a model update.
