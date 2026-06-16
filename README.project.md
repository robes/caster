# myproject

> One-line description of what this model does.

This project was built from the [Caster](README.md) template: full-lifecycle
reproducibility over git + Hydra + DVC, with Croissant data documentation and
RO-Crate/BagIt release packaging.

## Setup

```bash
# from a clone: `git clone <repo> && cd <repo>`
# from the template zip: unzip, then `cd` into it and `git init` (DVC needs git)
git init                                  # skip if you cloned an existing repo

uv sync --extra dev --extra croissant --extra packaging   # env + all optional layers
#   (or, minimal core only: `uv sync`; or with pip: `pip install -e ".[dev,croissant,packaging]"`)
uv run pre-commit install

uv run dvc init                           # initialize DVC in this repo (once)
uv run dvc remote add -d storage ../caster-dvc-remote   # a local remote (a dir); or s3://…, gs://…
git add -A && git commit -m "initial"     # commit before running the pipeline
```

Then run the pipeline with `uv run dvc repro` (see below).

## The pipeline

```bash
dvc repro                                 # preprocess → splits (out-of-date stages, in order)
python scripts/train.py +experiment=baseline
python scripts/evaluate.py +eval=validation run_dir=outputs/<run>/   # compare on val
# held-out test (run ONCE, at publication): +eval=holdout run_dir=models
```

Each training/eval run lands in `outputs/<date>/<time>/` with a `_provenance.json`
recording the commit, environment, and metrics. See
[`PROJECT_STRUCTURE.md`](PROJECT_STRUCTURE.md) for the full layout and
[`docs/`](docs/) for the design.

## Data

External datasets are documented in [`data_sources/`](data_sources/) as Croissant
records and tracked with DVC. See [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md).

## Release

```bash
gto register myproject --version v1.0.0              # commit-pinned release tag (SemVer, v-prefixed)
caster package configs/release/v1.0_main.yaml   # archival bundle
```

## License

[Apache-2.0](LICENSE).
