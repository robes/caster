# Contributing

## Setup

```bash
git init                         # if working from the template zip (DVC needs git); skip if cloned
uv sync --extra dev --extra croissant --extra packaging   # env + all optional layers
uv run pre-commit install
uv run dvc init                  # initialize DVC (once); then set a remote:
uv run dvc remote add -d storage ../caster-dvc-remote   # local dir, or s3://… etc.
git add -A && git commit -m "initial"   # commit before `dvc repro`
```

Not using uv? Plain pip (25.1+) works:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,croissant,packaging]"
pre-commit install
```

The optional extras are split so a minimal install stays light: `croissant`
(data-source validation/emit), `packaging` (RO-Crate/BagIt releases), `all`
(both), `dev` (pytest, ruff, pre-commit).

## Running things

The `caster` tools run via the unified `caster` command (each is also runnable as
`python -m caster.<module>`):

```bash
caster provenance stamp <dir> [--stats k=v]   # stamp a non-Hydra step
caster data validate                          # check data_sources/ + coverage
caster data emit <name>                        # YAML -> Croissant JSON-LD
caster package <release_config.yaml>           # build an archival bundle
```

See the [command reference](docs/COMMANDS.md) for the full list and arguments.

Your pipeline:

```bash
dvc repro                                  # run out-of-date stages in order
python scripts/train.py +experiment=baseline
python scripts/evaluate.py +eval=validation run_dir=outputs/<run>/   # compare on val
# held-out test (run ONCE, at publication): +eval=holdout run_dir=models
```

## Tests

```bash
uv run pytest                    # the caster package's own tests
```

The suite lives in `src/caster/tests/` and exercises the run-stamp, the
data-source validate/emit, and the release packager.

## Conventions

- **Scripts are entry points only.** Real logic goes in `src/myproject/`.
- **Seed everything**; the seed lives in the config.
- **Never edit artifact directories or DVC outputs by hand** — make a new run.
- **A missing `_provenance.json` means a run did not complete.**
- Use feature branches and PRs even for small teams.
- `ruff` (lint + format) runs in pre-commit, so style never enters review.

## Using hydra-zen instead of YAML configs

Caster uses plain Hydra by default, but it does not stand in the way of
[hydra-zen](https://mit-ll-responsible-ai.github.io/hydra-zen/), which lets you
author configs in Python instead of YAML. hydra-zen runs *on top of* Hydra, so
everything Caster relies on still works:

- The run-stamp callback rides on Hydra's runtime, which hydra-zen preserves —
  no change needed.
- The composed `.hydra/config.yaml` is still written, so the provenance and
  packaging chain is unaffected.

To switch, you do the normal hydra-zen work (author your config groups in a
Python module via `builds()`/`store()`, and optionally wrap your script entry
points with `zen()` instead of `@hydra.main`). The one Caster-specific thing to
carry over is the callback registration in `configs/hydra/default.yaml` — keep
it, expressed via hydra-zen's `HydraConf`/`hydra_defaults` mechanism. It is
isolated in that one file precisely so this is the only thing you re-express.
