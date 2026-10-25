# Configuration and local setup

[Back to the quick start](../README.md#quick-start) · [Technical reference](REFERENCE.md)

Run the commands below from the repository root.

## Configuration

Create a Mubit project inference key and obtain an available model ID from your account. Run this in a normal Terminal window:

```sh
python3 configure_mubit.py
```

Setup asks for the model ID, then accepts the key with hidden input. It stores the key in macOS Keychain and the non-secret model selection in the ignored `mubit-settings.json`. The app also has a **Save key locally** control. After setup, choose **Check connection** in the game.

| Setting | Purpose |
| --- | --- |
| `MUBIT_API_KEY` | Supplies a Mubit key through the process environment. Otherwise the server checks macOS Keychain. |
| `DINO_MUBIT_MODEL` | Supplies the selected model ID. A saved model setting takes precedence. |
| `mubit-settings.json` | Stores the local, non-secret model selection. Do not commit it. |
| `DINO_COMPARISON_CASE_ID` | Optionally selects a particular locally saved case for the provider comparison. Otherwise the newest eligible completed review is selected. |

`.env` files are ignored by Git but are not loaded automatically. Do not put credentials in source files or shell commands saved to history.

Saving a key and checking connectivity do not start inference or gameplay. Play and review controls send the displayed evidence through Mubit and may use credit. The bridge permits up to 20 inference attempts per server session. A verified connection is distinct from a successful model response.

Runtime runs, screenshots, experiments, corrections, provider receipts and training output stay in private directories outside `public/`. These directories and local settings are ignored by Git. Export files can contain review text and source identifiers, so inspect them before sharing. Real keys, personal run records, downloaded models and historical private receipts are excluded from this repository.

Mubit receives evidence through the explicit play and review actions described in the interface. The provider may retain recorded calls under its own service arrangements. This app does not establish whether those calls became training data or changed a hosted model.

## Startup notes

A fresh checkout contains no saved runs, review examples, comparison results or downloaded models. If another copy of this app already owns port 4040, the launcher opens that running copy; stop it first to run this checkout.

The server listens on this computer only and serves `public/`. The repository is public source code for a local app. The complete app cannot run on GitHub Pages because the API and private storage need the Python server.

## Use a temporary demo

For a separate copy of the interface with temporary records, run:

```sh
python3 scripts/demo_server.py --port 4041
```

Open **http://127.0.0.1:4041/**. This loopback server disables provider and Keychain access, starts without your real saved results and removes its temporary records when it exits normally. Use **Ctrl+C** to stop it. It is suitable for local gameplay and interface captures; it cannot demonstrate a live Mubit response.
