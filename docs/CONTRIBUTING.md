# Contributing to Dino Mubit Lab

Start with the [README](../README.md) and [architecture document](../ARCHITECTURE.md). The app is a local Python and browser JavaScript project. Ordinary use needs no package installation; optional MLX training has its own environment and dependency file.

## Report a problem

Open a [GitHub issue](https://github.com/MasteraSnackin/dino-mubit-lab/issues) with:

- The workflow and control involved.
- What you expected and what happened.
- Steps that reproduce the problem.
- Your operating system, Python version and browser version.
- Relevant test output or a screenshot, after removing private information.

Do not post API keys, personal run records, raw provider responses or private export files. A short synthetic example is usually easier to investigate than a full saved dataset. Never remove or alter an uncertain request receipt merely to reproduce a provider call.

## Make a change

1. Create a branch or fork for a focused change.
2. Inspect the code and reproduce the issue before editing it.
3. Keep interface changes separate from provider, storage or training changes where possible.
4. Add a regression test when the fix changes behaviour or addresses a failure that could recur.
5. Update the relevant guide when a command, route, setting or user flow changes.
6. Run the checks below and describe their results in the pull request.

```sh
python3 -m unittest discover -p 'test_*.py'
node --test public/*.test.cjs
```

Tests should use temporary directories, synthetic records and mocked provider responses. Do not make live model calls part of the default test suite. For a visual change, also open the real app, inspect the affected states and check a narrow viewport. Test any changed keyboard interaction directly.

## Preserve the evidence rules

Saved comparisons, approvals and request receipts support the claims shown by the interface. A change must preserve their meaning:

- A confirmed reference is different from an edited correction.
- Approved examples added to a prompt do not establish a model-weight update.
- Local LoRA training does not establish a change to the Mubit-hosted model.
- Missing, corrupt and uncertain records are not an empty history or a successful outcome.
- A provider request with an unknown outcome cannot be safely repeated automatically.
- Reserved evaluation courses cannot become training examples.
- Tests with synthetic responses do not prove live provider behaviour.

Keep private storage outside `public/`, preserve bounds and revision checks, and retain the original Chromium copyright headers and licence files.

## Open a pull request

Use [GitHub pull requests](https://github.com/MasteraSnackin/dino-mubit-lab/pulls). Explain the problem, the resulting behaviour and the checks you ran. Include before-and-after captures for a visual change when they help review. State any test or provider verification you could not perform.

Before pushing, inspect the staged files and confirm that local settings, credentials, private records, downloaded weights and temporary captures are absent. Published demonstration media needs a capture record that explains what it shows and whether any provider was contacted.
