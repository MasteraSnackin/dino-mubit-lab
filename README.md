# Dino Mubit Lab

A local dinosaur game and an experiment lab for recurring AI reviews. Play the game, test a suggested controller change on repeatable courses, review the answer against measured results, and preserve approved corrections for later use.

The project keeps three mechanisms visible: Mubit inference, approved examples added to a later prompt, and optional local model training. Each has its own records and evidence limits.

## Run locally

The tested platform is macOS. The local server uses Python's standard library; it needs no Python packages for gameplay, Mubit requests or review storage. Use a current Python 3 installation. Node.js is needed only for the JavaScript tests. Windows is not supported by the current POSIX file-locking implementation.

```sh
git clone https://github.com/MasteraSnackin/dino-mubit-lab.git
cd dino-mubit-lab
python3 serve.py --open
```

Open **http://127.0.0.1:4040/** if the browser does not open automatically. Press **Space** or **Up** to start and jump; use **Down** to duck. On macOS, you can also double-click `Start Dino.command`. Stop the server with **Ctrl+C** in its Terminal window.

The game and local rule-based player work without an API key or an internet connection. A fresh checkout starts with no saved runs, review examples, comparison results or downloaded models. If another copy of this app already owns port 4040, the launcher opens that running copy; stop it first to run this checkout.

The server listens on this computer only and serves files from `public/`. This repository is source code for a local app. The complete app cannot run on GitHub Pages because its API and private storage require the Python server.

## Connect Mubit

Create a Mubit project inference key and obtain an available model ID from your account, then run this command in a normal Terminal window:

```sh
python3 configure_mubit.py
```

The setup prompts for the model ID and then accepts the key with hidden input. The key is stored in macOS Keychain; the non-secret model selection is written to the ignored `mubit-settings.json`. The app also provides **Save key locally** for key entry. After setup, choose **Check connection** in the game.

An existing process environment can supply `MUBIT_API_KEY` and `DINO_MUBIT_MODEL`. A saved model setting takes precedence over the environment model. Do not put credentials in source files or commit them; `.env` files are ignored but are not loaded automatically by this app.

Saving a key and checking connectivity do not start gameplay or inference. Play and review controls send the displayed game evidence through Mubit and may use credit. The bridge permits up to 20 inference attempts per server session. Connection readiness is shown separately from a successful model response.

## What the lab does

| Workflow | Behaviour | Evidence limit |
| --- | --- | --- |
| Play manually or use the local player | Runs the dinosaur game locally; the local player chooses jumps and ducks from canvas observations. | Local play does not use Mubit. |
| Let Mubit play | Sends screenshots and game state for model decisions. Normal-speed mode continues while replies arrive. | Late replies can miss obstacles. A separately labelled paused mode is also available. |
| Test a Mubit suggestion | Repeats a baseline course, checks consistency, then tests one bounded timing change on two fixed courses at normal speed. | Two courses do not establish a generally better player. |
| Review and correct | Saves the original suggestion, measurements, draft revisions and explicit approvals. A compact text task reviews the already-tested change. | Saving or approving a correction does not train a model. |
| Reuse approved answers | Adds eligible approved compact answers to a subsequent Mubit request. | This is prompt context, with no verified model-weight update. |
| Train locally | Runs an optional MLX LoRA pilot on approved compact review answers and checks saved parameter changes. | Updates a separate local model, not the Mubit-hosted model. Training-example replay is not independent evaluation. |
| Compare providers | Sends the same compact request three times through Mubit and three times directly to OpenAI, recording tokens, latency and limited answer checks. | One input and six responses cannot establish a general quality, cost or latency advantage. |

## Recurring-task walkthrough

1. Create a comparison and run the baseline courses. Keep the page visible while trials run.
2. Check the Mubit connection and ask for one change. Test the proposal on the same courses.
3. Import the completed comparison into the correction workflow. Inspect the evidence, save a review and approve that exact revision.
4. Request one compact text review of the measured result. Review the saved answer, save any correction and approve it. The app distinguishes an edited answer from an unchanged confirmed reference.
5. Use the approved compact answer as memory for a later request, or prepare the optional local training pilot. Preserve the corresponding receipts and compare results before claiming an improvement.

The scenario plan separates training courses from reserved evaluation courses. Its planned counts are not collected-example counts. Requests and runs retain failed or interrupted attempts, and reloading does not automatically repeat a saved provider request.

This implements the collection, correction, approval and local update portions of a recurring-task demonstration. A verified Mubit correction-to-training contract and Mubit model-version update remain unestablished. A strong improvement claim still needs multiple genuine corrections and evaluation on independent cases.

## Optional experiments

**Provider comparison:** finish an eligible local compact review first, then follow [the six-request comparison guide](PROVIDER-COMPARISON-README.txt). The panel previews the identical request before sending it. It needs a separate OpenAI API key, used in memory for that test and not saved. Starting the test may consume credit on both routes. Receipts include token counts and measured response times; the app does not infer billed costs or prove that the two routes use identical model weights.

**Local training:** use a dedicated Python 3.12 environment on Apple silicon and the pinned [training dependencies](requirements-local-training.lock.txt). The optional base model must be downloaded separately and pinned to a full revision. Keep downloads outside this repository or in the ignored `local_models/` directory. Follow [the local adapter guide](LOCAL-ADAPTER-README.txt) for preparation, the explicit training command and evidence export. Neither opening the app nor reading status downloads a model or starts training. Model weights and local training records are excluded from Git.

## Data and credentials

Runs, screenshots, experiments, corrections, provider receipts and training output stay in private directories outside `public/`. Those directories and local settings are ignored by Git. Export files may contain review content and source identifiers; inspect them before sharing. No real keys, personal run records, downloaded models or historical private receipts are included in this repository.

Mubit receives evidence only through the explicit play or review actions described in the UI. The provider may retain recorded calls under its own service arrangements. This app does not establish whether those calls became training data or changed a hosted model.

## Tests

Run these from the repository directory:

```sh
python3 -m unittest discover -p 'test_*.py'
node --test public/*.test.cjs
```

Tests use temporary stores, synthetic game data and mocked provider responses. They cover request validation, storage integrity, revision conflicts, course consistency, controller behaviour, recovery and the comparison workflow. They do not require a live provider key or demonstrate model training by Mubit.

## Detailed guides

- [Fixed-course suggestion testing](COMPARISON-README.txt)
- [Correction workflow and reserved scenarios](TRACK1-README.txt)
- [Compact review task and answer approvals](REVIEW-TASK-README.txt)
- [Local LoRA preparation and evidence](LOCAL-ADAPTER-README.txt)
- [Mubit versus direct OpenAI comparison](PROVIDER-COMPARISON-README.txt)

## Credits and licence

The game is derived from [wayou/t-rex-runner](https://github.com/wayou/t-rex-runner/tree/5455bfa408ec6b707c7300ff194b7390733a766d), which extracts the Chromium dinosaur game. Original Chromium copyright notices and the upstream [BSD 3-Clause licence](LICENSE) are retained, including the copy in [`public/LICENSE`](public/LICENSE).

This project adds the local server, players, Mubit integration, review workflow and experiment tooling. It is an independent prototype and is not endorsed by Google, Chromium or Mubit.
