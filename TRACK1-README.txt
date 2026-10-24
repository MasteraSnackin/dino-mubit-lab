Track 1 preparation: corrections linked to evidence

This local workflow prepares the recurring task of reviewing completed Dino
runs. It preserves an original Mubit answer, measured comparison, correction
drafts and explicit approval. These records are not a trained model.

Current provider boundary

The connection from approved replacement answers to Mubit adapter training has
not been verified. Screenshot eligibility and model-version access also remain
unverified. The app reports unverified_contract, with no adapter or training job
ID. Local approval, a local export, more gameplay or a request rating must not be
presented as a model update. This workflow makes no provider calls by itself.

Correction workflow

1. Complete a comparable two-course experiment with a successful Mubit proposal.
2. Import that saved comparison. The server reads its authoritative evidence;
   browser-supplied replacements are not accepted. The original answer, model,
   request ID, baseline settings, candidate settings and measured result remain
   attached. The model input contains the baseline closing frames and actions,
   not the repeated baseline or hidden engine obstacle audit.
3. Draft a replacement recommendation and its reason. Supported answers are a
   bounded timing change, no change, or insufficient evidence. Cite at least one
   saved frame, action or comparison reference. A local result-based draft is
   labelled machine-drafted. It is not an approved human correction.
4. Review the draft and explicitly approve it. Every draft and approval appends
   a revision. The previous answer and revision history remain intact. Approval
   records an explicit local action, not a verified identity. A newer draft
   removes current export eligibility until that newer draft is approved.
5. Export approved training corrections. The export includes source and dataset
   hashes, the original answer, corrected answer, reason and approval evidence.
   It is generic local JSON, not a verified Mubit upload format. Nothing is
   uploaded and no training starts when it is exported.

The export task is review-tested-controller-change-v1: review a proposed change
after its two-course test. Its input includes the original baseline model input,
original answer, measured comparison, baseline and candidate settings, observed
runs and all supported frame/action/comparison references. A correction that
cites a candidate outcome therefore has that outcome in its input. Original
gateway task, input, model and request ID remain separate provenance. The export
does not represent pre-run timing-proposal training: that original task had no
candidate outcomes when Mubit answered. Any future provider integration must
explicitly support this after-test review task or define a separate correctly
matched dataset; it must not upload these pairs as the original gateway task.

Locked training and test scenarios

The plan reserves 40 training scenarios and 10 evaluation scenarios. Each scenario
is a pair of different course seeds: 80 training seeds and 20 evaluation seeds.
The pools are disjoint and never silently regenerated. The first training pair
uses the original 104729 and 130363 courses. The counts are a collection plan;
they do not mean that 40 examples have been collected or accepted by a provider.

Prepare courses creates or reopens the scenario's linked local comparison. It
does not play the game or contact Mubit. Creation intent is saved before creation;
an uncertain interrupted attempt cannot silently create another comparison.

Comparisons containing either reserved evaluation seed cannot be imported as
training cases, including mixed training/evaluation pairs. They cannot enter a
training export. Evaluation review must use a separately verified no-training
route; ordinary recorded provider calls must not consume reserved test inputs.

Two experiments with the same full review input produce one correction case,
even when copied to different experiment or run IDs. The identity includes the
original proposal, measured comparison, tested settings and observed evidence.
The same baseline with a different proposal or outcome is a different review
task and receives a separate case. Repeated copies of the same complete task do
not inflate the training-example count. The original baseline input fingerprint
remains provenance; taskInputFingerprint identifies the full after-test review.
Legacy cases whose IDs used the baseline fingerprint remain readable and retain
their original files and revision history. Reimport reuses a legacy case only
when its full review task matches. No automatic record migration is performed.

Persistence and recovery

Private records live in learning/, outside the public web directory. Source
snapshots are hashed, inputs are bounded, files are written atomically and access
is local. The limit is 100 cases, 200 revision events per case, 16 MiB per case and
64 MiB per export. Records contain no provider keys.

Saving requires the current revision. A stale tab cannot overwrite or approve a
newer draft. Unreadable records are preserved and reported as unknown. Export
stops if any record is unreadable rather than silently producing an incomplete
dataset. Missing or changed split manifests are not regenerated over existing
cases. A missing scenario-association file blocks new scenario preparation rather
than recreating a potentially duplicated comparison. Existing experiment files
are never changed by correction import.

What remains before a Track 1 model-update claim

Verify the supported correction-to-training contract and image eligibility with
Mubit, then show accepted examples leading to a real adapter/model version and
its training result. Compare that version on reserved cases using a verified
evaluation route. Preserve version IDs and measured results. An update is not
proven merely by showing this correction panel or its exported JSON.

Compact recurring inference check

After approving a case, the panel "Check the recurring review task" can send one
text-only review of its original proposal and measured results through Mubit.
Inspect its input first, check the Mubit connection, then choose "Ask Mubit for
one text review". The approved correction stays local. The reply is compared
with the saved outcome label, preserved and available through "Export review
receipt". Refreshing or reopening never repeats a saved attempt.

This inference check is tested-review-text-v1; it does not submit the generic
correction export or demonstrate training. See REVIEW-TASK-README.txt for its
input, model binding, duplicate protection and reserved-test exclusion.

Verification scope

Automated storage tests use temporary files, synthetic fixtures and mocked
provider responses. They test source custody, revision conflicts, approval,
deduplication, locked scenario separation, export boundaries, concurrent scenario
creation and recovery. They do not demonstrate provider training or a learned
model improvement. Live browser acceptance is recorded separately by the task.
