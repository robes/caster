```
 ██████╗ █████╗ ███████╗████████╗███████╗██████╗                           _||_
██╔════╝██╔══██╗██╔════╝╚══██╔══╝██╔════╝██╔══██╗                         |____|
██║     ███████║███████╗   ██║   █████╗  ██████╔╝                         /    \
██║     ██╔══██║╚════██║   ██║   ██╔══╝  ██╔══██╗                  ·   · |  ()  |
╚██████╗██║  ██║███████║   ██║   ███████╗██║  ██║          ·   ·   ·   · |  ()  |
 ╚═════╝╚═╝  ╚═╝╚══════╝   ╚═╝   ╚══════╝╚═╝  ╚═╝  ·   ·   ·   ·   ·   ·  \____/
```

# Caster

**C**ode, **A**rtifact, and **S**tatistic **T**racking for **E**xperimental **R**eproducibility.

Caster is a project *template* for machine-learning model development by
research teams. The name is the idea: a caster is a small, lightweight wheel
that makes heavy furniture easy to move. Caster does the same for an ML project.
It carries the weight of reproducibility — every result traceable to the exact
code, config, data, and environment that produced it — without the project ever
feeling heavy.

Caster stands on mature, widely-adopted open tools and standards, and adds only
the thin layer of glue and convention they leave missing. You get full-lifecycle
reproducibility while leaning on software that is better-maintained than anything
a template could ship, and your project stays interoperable with the wider
ecosystem.

> [!IMPORTANT]
> This `README.md` describes the template itself. When you build a real
> project from Caster, replace it with [`README.project.md`](README.project.md).

## Design objectives

Caster pursues three objectives of equal weight — none traded away for another:

- **Reproducibility** — any result traces back to the exact commit, config, data
  version, and environment that produced it.
- **Transparency** — a collaborator can open a run directory or a data-source
  record and understand what produced it, without asking.
- **Simplicity** — client-side tools, git as the source of truth, no mandatory
  servers. Caster sets sensible defaults and stays unopinionated where it can: a
  DVC local remote (just a directory) is as welcome as an S3 bucket; local
  experiment tracking as welcome as a tracking server.

Everything below — the stack, the conventions, the small amount of Caster's own
code — is in service of these three.

## The stack

A small team gets full-lifecycle reproducibility from a handful of tools working
together. Caster wires them up and makes the integration decisions for you:

| Concern | Tool / standard | Why this one |
|---|---|---|
| Code versioning | **git** | Universal; the source of truth everything else hangs off. |
| Configuration & run dirs | **Hydra** | Composes configs, writes the exact composed config per run, drives sweeps. |
| Data & artifact versioning | **DVC** | Content-addresses data and models; restores any version; remote-agnostic. |
| Data documentation | **Croissant** (MLCommons) | The open standard for ML dataset metadata, provenance, and licensing. |
| Release versioning | **GTO** (Git Tag Ops) | Commit-pinned model registry built from git tags; no server. |
| Archival packaging | **RO-Crate + BagIt** | RO-Crate describes; BagIt verifies transfer. They compose. |

Caster pre-wires these, makes the few integration decisions they leave open, and
contributes the small amount of code that has no home in any of them.

## What Caster adds

The tools above carry most of the weight. Caster's own code is justified only
where it encodes knowledge no library has, which comes to three small things:

1. **A run-stamp** — a Hydra callback that writes `_provenance.json` (commit,
   environment, completion, stats) into every run directory, closing the one
   gap the stack leaves: the link from a run to *its* commit and actual
   environment.
2. **The `data_sources/` convention** — one Croissant record per external
   dataset, with a validator and a lightweight `emit` helper, so "what data does
   this use, and how do I get it?" is always answerable.
3. **Release packaging** — connective code over RO-Crate + BagIt that pins the
   source to the run's commit and arranges the provenance chain into a verifiable
   archival bundle.

The reasoning for *why* each has no home in the tools is in
[`ARCHITECTURE.md`](docs/ARCHITECTURE.md); the *how* of each is in its duty doc
([`PROVENANCE.md`](docs/PROVENANCE.md),
[`DATA_SOURCES.md`](docs/DATA_SOURCES.md),
[`PACKAGING.md`](docs/PACKAGING.md)).

## Repository layout

At a glance: `configs/` (Hydra config tree) · `src/` (the `caster` package +
your project code) · `scripts/` (pipeline entry points) · `data_sources/`
(Croissant records) · `data/` (DVC-tracked data) · `dvc.yaml` (the pipeline) ·
`docs/` (everything below).

The full annotated layout, and the conventions behind each directory, are in
[`PROJECT_STRUCTURE.md`](PROJECT_STRUCTURE.md).

## Getting started

This walks a fresh copy of the template all the way through running the example
pipeline once and producing a release — so you see the whole loop before
replacing the example with your own work. (To take your *own* project through the
same three phases end to end, see [`HOWTO.md`](docs/HOWTO.md).)

**1. Get the code and initialize git.**

```bash
# from a clone: git clone <repo> && cd <repo>
# from the template zip: unzip, then cd into it
git init            # DVC needs a git repo; skip if you cloned an existing one
```

**2. Install the environment.**

```bash
uv sync --extra dev --extra croissant --extra packaging
#   dev = pytest/ruff/pre-commit; croissant = data tooling; packaging = releases
#   (minimal core only: `uv sync`; with pip: `pip install -e ".[dev,croissant,packaging]"`)
uv run pre-commit install
```

**3. Run the tests** to confirm the install is sound (no DVC setup needed yet):

```bash
uv run pytest        # the caster package's own suite (31 tests)
```

**4. Initialize DVC and a data remote, then commit.**

```bash
uv run dvc init
uv run dvc remote add -d storage ../caster-dvc-remote   # a local dir; or s3://…, gs://…
git add -A && git commit -m "initial"
```

**5. Run the pipeline.** This executes the example end to end —
fetch → preprocess → split → train → validation eval — producing the model at
`models/model.skops`:

```bash
uv run dvc repro
```

`dvc repro` writes `dvc.lock` (the record of each stage's inputs, command, and
output hashes) and the pipeline's outputs under `models/` (the example's default
location, set in `configs/model/logreg.yaml`). Commit them to pin this run's
pipeline state in git — this is the step that makes the run reproducible from the
commit:

```bash
git add dvc.lock models/ && git commit -m "run pipeline"
```

`git add models/` stages the small git-tracked metric and the `.gitignore` DVC
generates there, while skipping the model *bytes* (DVC ignores those — they go to
the DVC remote on `dvc push`). If you change the output location in your config,
adjust the path here to match.

(If you'd rather DVC stage its bookkeeping for you, `dvc config core.autostage
true` — off by default so nothing is staged without your say-so.)

**6. Run the held-out test — once.** The final, honest number, deliberately kept
out of `dvc repro` so it isn't run during model selection:

```bash
uv run python scripts/evaluate.py +eval=holdout run_dir=models
```

**7. Package a release.** Tag the model and build a verifiable archival bundle.
Point `run_dir` (provenance) and `eval_dir` (the held-out result) in
`configs/release/v1.0_main.yaml` at your actual run directories first:

```bash
uv run gto register myproject --version v1.0.0
uv run caster package configs/release/v1.0_main.yaml
```

**8. Make it yours.** Replace this README with the project one, then swap the
example out for your code:

```bash
mv README.project.md README.md
```

Fill in the project name and description, replace `src/myproject/` with your own
package, point `data_sources/` at your real datasets, and adjust the configs.
The `caster` package and the pipeline structure stay; the example is disposable.

## Common tasks

A quick reference for the day-to-day commands. The pipeline stages are Hydra
apps under `scripts/`; the `caster` tools run via the unified `caster` command
(`caster <group> <command>`), or equivalently as `python -m caster.<module>`.

| Task | Command |
|---|---|
| Run the whole pipeline | `dvc repro` |
| Re-run one stage with a changed param | edit its config under `configs/`, then `dvc repro` |
| Train an ad-hoc experiment | `python scripts/train.py +experiment=baseline` |
| Compare experiments on validation | `python scripts/evaluate.py +eval=validation run_dir=outputs/<run>/` |
| Run the held-out test (once, at publication) | `python scripts/evaluate.py +eval=holdout run_dir=models` |
| Document a dataset (DOI → Croissant) | write `data_sources/<name>.yaml`, then `caster data emit <name>` |
| Check every tracked dataset is documented | `caster data validate` |
| Track dataset bytes | `dvc add data/raw/<name>` |
| Stamp a non-Hydra step | `caster provenance stamp <dir> --stats k=v` |
| Regenerate an experiment ledger | `caster provenance ledger -o experiments.md` |
| Tag a release version | `gto register <model> --version v1.0.0` |
| Build an archival release bundle | `caster package configs/release/<tag>_<model>.yaml` |
| Share code / data | `git push` · `dvc push` |
| Run the test suite | `pytest` |

(Prefix with `uv run` if you use uv and haven't activated the venv.)

For the full list of `caster` commands and their arguments, see the
[command reference](docs/COMMANDS.md).

## Documentation

Caster has three duties. To take your own project through them end to end, start
with the how-to; for depth on any one duty, its own doc:

- [`HOWTO.md`](docs/HOWTO.md) — the step-by-step: from acquiring data, through
  running experiments, to packaging a release — for your own project.
- [`REPRODUCING.md`](docs/REPRODUCING.md) — the reverse trip: from a downloaded
  release bundle back to a reproduced, verified result.
- [`COMMANDS.md`](docs/COMMANDS.md) — the `caster` command reference: every
  command and its arguments, in one table.
- [`UPDATING.md`](docs/UPDATING.md) — taking later template changes into a
  project made from Caster.

The three duties:

- [`DATA_SOURCES.md`](docs/DATA_SOURCES.md) — **define your data**: the two
  data-source types and the Croissant-aligned `data_sources/` convention.
- [`PROVENANCE.md`](docs/PROVENANCE.md) — **record your workflow**: how git,
  Hydra, and DVC pin code, config, and data, and how Caster's run-stamp ties each
  run to its commit and environment.
- [`PACKAGING.md`](docs/PACKAGING.md) — **release your artifacts**: GTO for
  versioning; RO-Crate + BagIt for archival release.

Design and background:

- [`ARCHITECTURE.md`](docs/ARCHITECTURE.md) — the design map: each objective
  traced to the tool that satisfies it and the glue Caster adds.
- [`RELATED_WORK.md`](docs/RELATED_WORK.md) — the surrounding landscape of
  templates, standards, and tools, and where Caster's contribution sits.
- [`AGENTS.md`](AGENTS.md) — orientation for automated coding agents: canonical
  commands, machine-readable (`--json`, `--dry-run`, `CASTER_RUN_DIR`) affordances,
  and the design invariants not to break.

## License

Apache License, Version 2.0. See [`LICENSE`](LICENSE).
