Dino: test one Mubit suggestion

Open http://127.0.0.1:4040/ and use the comparison panel.

The local rule-based player reads the game canvas and handles every jump and
duck. Mubit reviews recorded evidence between runs. Mubit does not control the
individual moves in this comparison. This local workflow does not request
training. Mubit may use recorded calls for its own training workflow; this app
has not verified a trained model version or a correction-upload contract.

Each course runs at normal speed, with a fixed 60 Hz game step and a maximum of
3,600 steps (60 seconds). New comparison uses seeds 104729 and 130363 by default.
The correction panel can prepare distinct training or reserved test course pairs.
Each saved comparison records its exact seeds. Reserved test courses cannot use
the recorded Mubit coaching route and cannot enter the correction training export.
The baseline settings are jumpLeadMs=170, duckLeadMs=330, duckExtraMs=0 and
jumpHoldMs=240. These settings remain the default after the comparison.

Workflow

1. Choose New comparison, then Run baseline courses. The player runs course A,
   repeats A, then runs B. A and its repeat must match in final step, score,
   outcome, action log and generated obstacle evidence before coaching is allowed.
2. Check the Mubit connection, then choose Ask Mubit for one change. This sends
   one explicit request containing the final six saved screenshots and up to
   60 recorded action events from each baseline course, with time and score
   labels. The repeat is excluded. Engine obstacle details are used only to
   check course consistency and are not sent to Mubit or used by the controller.
3. Mubit can propose one timing value, in increments of 10 milliseconds:
   jumpLeadMs 100–240; duckLeadMs 200–500; duckExtraMs 0–250; jumpHoldMs 120–350.
   The proposed value must differ from the baseline. Code and extra settings
   are not accepted. The rationale and caveat are saved with the proposal.
4. Choose Test proposed change. Both courses run again with only that one
   temporary change. The panel shows survival time and score for each pair.
   Survival time determines the summary; both measurements remain visible.

Records and recovery

Experiments are saved locally in the private experiments/ directory, outside the
served public/ folder. The limit is 100 experiments and five run slots per
experiment. Completed slots are immutable; submitting the same record again is
safe. Reload the page and select a saved comparison to continue its empty slots.
Export downloads the saved experiment, including its evidence and result.

Stopped, hidden, lagged, resized or failed runs remain recorded and make the
comparison inconclusive. Missing evidence, unequal baseline repeats, mismatched
course evidence or excessive timing drift also prevent a verified comparison.
An unreadable saved record is reported as unavailable and is not replaced.

A coaching attempt is recorded before it is sent. Re-clicking, reloading or an
interrupted response cannot automatically send it again. Pending after an
interruption means the outcome is unknown. Failed attempts are retained; use a
new comparison for a new attempt. For a recorded failed review, New review from
saved baselines creates a separate comparison with the same three authoritative
baseline records. The new record names its source experiment; the original failed
record is unchanged. Copying does not contact Mubit. Choose Ask Mubit for one
change separately to send the new experiment's one request. Pending or unknown
outcomes cannot use this recovery action.

Coaching shares the bridge's 20-inference-call limit with live Mubit play and other
reviews. Connection checks do not consume that limit; unsuccessful inference
attempts can consume it. A comparison proposal allows up to 3,000 completion
tokens; live play and ordinary reviews retain their 700-token allowance. Empty,
truncated or invalid replies remain failures. The saved diagnostic can contain a
static failure category, recognised finish reason, response length and bounded
token usage, but never the raw rejected reply.

What the result means

This is a two-course pilot using browser-recorded observations. Exact repeat and
course checks establish consistency of those records, not independent proof of
their authenticity. A favourable result on two chosen courses does not establish
a generally better controller. There is no automatic default-setting update,
model-weight change or claim that Mubit has learned from the experiment.
Legacy modelTraining=false fields mean this local workflow did not request a
model update; they do not establish what the provider did with recorded calls.
Current comparison views explicitly mark provider training as unverified.

The Track 1 correction workflow is described in TRACK1-README.txt. It preserves
the original answer, measured outcomes and each correction revision. Its export
task reviews an already-tested suggestion, with outcomes included in the input;
it is not the original pre-test proposal task or a verified Mubit upload format.

Validation scope

The automated backend, route and controller checks run offline with temporary
records, synthetic game fixtures and mocked provider responses. They cover
bounded inputs, repeatability, matching courses, immutable slots, recovery,
single-attempt coaching, safe diagnostics and the existing call limit. Those
checks do not demonstrate a live Mubit response or a live browser comparison.
Live acceptance results are recorded separately after the complete workflow runs.
