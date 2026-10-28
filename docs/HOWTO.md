# How-to: from data to a packaged release

This walks a real project end to end through Caster's three duties. It assumes
you have already set up the template once — see the README's
[Getting started](../README.md#getting-started), which runs the shipped *example*
(synthetic data + a scikit-learn model). This guide is the next step: replacing
that example with *your own* work, phase by phase.

Caster has three duties, and a project moves through them in order:

1. **Data sources** — define your data rigorously.
2. **Provenance** — record how your experiments were produced.
3. **Package & release** — bundle results for maximal reproducibility.

Only the first two are part of the day-to-day loop; release happens when you have
something worth publishing. Each phase below is a sequence of concrete steps;
follow the linked duty doc when you need the reasoning or the edge cases.

---

## Phase 1 — Acquire and document your data sources

The goal of this phase: every dataset your project uses is *documented* (so
anyone can tell what it is and how to obtain it) and *pinned* (so "the data" is a
checkable claim). Detail and rationale: [`DATA_SOURCES.md`](DATA_SOURCES.md).

**1. Replace the example's data acquisition.** The template's `scripts/fetch.py`
*generates* synthetic data. Replace its body with your real acquisition —
download and unzip an archive, pull from an instrument or institutional store,
materialize a BagIt/BDBag — writing the raw bytes under `data/raw/<name>/`. Keep
it a Hydra app so the run is still stamped; put acquisition parameters
(URLs, credentials references, sizes) in `configs/fetch/<name>.yaml`.

**2. Write a Croissant record** for each external dataset, under
`data_sources/<name>`, starting with a `<name>.yaml` that states its `type`:
   - *Published elsewhere* (`type: reference`, with its DOI/URL as `url`): if it
     already publishes Croissant (Hugging Face, Kaggle, OpenML), download that
     record to `data_sources/<name>.json`; otherwise (Zenodo, Dataverse, OSF, a
     BDBag) describe it in the YAML. Then `caster data emit <name>`.
   - *Produced by you* (`type: local`: collected, private or generated): author
     the metadata (the MLCommons Croissant editor can derive a starting point),
     document in its `provenance:` field how the data was built, and emit.

**3. Pin the raw bytes** so "the data" is reproducible:

```bash
dvc add data/raw/<name>          # or let the fetch stage's `outs:` track it
```

**4. Validate** that every tracked dataset has a record and vice versa:

```bash
caster data validate
```

When this passes, "what data does this project use, and how do I get it?" is
answerable from the `data_sources/` directory alone.

---

## Phase 2 — Run your experiments, with provenance recorded

The goal of this phase: produce models, and have every run carry the record of
*what produced it* — automatically, without discipline. The stack does the
recording: **git** pins code, **Hydra** pins each run's composed config, **DVC**
pins data and the pipeline, and Caster's **run-stamp** ties each run to its
commit and actual environment. Detail: [`PROVENANCE.md`](PROVENANCE.md).

**1. Adapt the pipeline to your model.** The stages are thin scripts over your
code in `src/<project>/`: edit `preprocess.py` (raw → processed), `split.py`
(train/val/test), and `train.py` (your model; write the checkpoint to the stable
`model.output_dir` and the run dir). Put tunable values in the Hydra configs
under `configs/`; the `dvc.yaml` `params:` blocks watch those same files, so a
parameter lives in exactly one place.

**2. Run the pipeline** and commit the result:

```bash
dvc repro                                  # fetch → preprocess → split → train → evaluate(val)
git add dvc.lock models/ && git commit -m "run pipeline"
```

Committing `dvc.lock` pins this run's pipeline state in git — the record a
collaborator re-runs against. `models/` is where the example writes its outputs
(set in `configs/model/logreg.yaml`; change it for your own model and adjust the
path here). `git add models/` stages the git-tracked metric and the `.gitignore`
DVC generates there while skipping the model bytes, which go to the DVC remote on
`dvc push`.

**3. Experiment.** Ad-hoc runs land in `outputs/<date>/<time>/` (not the
canonical pipeline model), each auto-stamped:

```bash
python scripts/train.py +experiment=baseline
python scripts/evaluate.py +eval=validation run_dir=outputs/<run>/   # compare on val
```

Use validation metrics — *not* the held-out test — to choose a model.

**4. Know what's recorded for you** (no action needed): each run dir gets a
`_provenance.json` stamp (commit, environment, completion, stats); split runs
also get `_split_properties.json` (sizes, fractions, class balance). A run dir
*without* a stamp means that run didn't finish. To commit a real commit into the
stamp, commit before running — a run stamped before the first commit records
`commit: null`.

**5. Optionally, regenerate a flat experiment ledger** from the stamps:

```bash
caster provenance ledger -o experiments.md
```

---

## Phase 3 — Release and package your results

The goal of this phase: turn a chosen model into a versioned, citable, verifiable
release that an outside reader can both *use* and *check* — without your DVC
remote or your cluster. Detail: [`PACKAGING.md`](PACKAGING.md).

**1. Run the held-out test — once.** This is the honest final number, kept out
of `dvc repro` so it is never run during model selection:

```bash
python scripts/evaluate.py +eval=holdout run_dir=models
```

**2. Tag the version** with GTO (a commit-pinned git tag; SemVer, `v`-prefixed):

```bash
gto register <project> --version v1.0.0
```

**3. Fill in the release config.** Copy `configs/release/_template_release.yaml`
(or edit `v1.0_main.yaml`) and set the paths to **real run directories** — the
placeholders are not real runs:
   - `run_dir` → the train run (supplies the `.hydra` config + `_provenance.json`)
   - `checkpoint_path` → the canonical `models/model.<ext>` from `dvc repro`
   - `eval_dir` → the held-out (`+eval=holdout`) run from step 1
   - `datasets` → each dataset's `disposition` (`reference`, `embed` or `omit`),
     checked against its record's `type`; see
     [PACKAGING](PACKAGING.md#dataset-disposition)

The packager refuses if `run_dir` does not exist and warns loudly if it lacks the
config or stamp — a bundle without its provenance chain is not a release.

**4. Build the bundle:**

```bash
caster package configs/release/<tag>_<model>.yaml
```

This assembles an RO-Crate (describing the whole research object) wrapped in a
BagIt bag (verifiable transfer), carrying the checkpoint, the provenance chain, a
portable data-hash manifest, a source archive pinned to the run's commit, and a
`MODEL_CARD.md`.

The card's factual fields (name, version, commit, metrics, datasets) are
auto-filled from the provenance; its judgment sections (intended use,
limitations, ethics) you author directly in the project's own `MODEL_CARD.md`
(repo root) — ideally before this step, not after — so the packaged card is
complete, not a facts-only stub. The packager renders your `MODEL_CARD.md`,
filling the facts and carrying your prose through. (A project with no
`MODEL_CARD.md` simply ships no card.)

**5. Deposit it.** The bag is suitable for a DOI-issuing archive (Zenodo, OSF,
Dataverse). An outside reader with only the deposit can obtain the data via the
Croissant records, recreate the environment, re-run, and check results for
equivalence — which is the whole point.

---

You now have a reproducible, citable release: data documented and pinned, every
run stamped with its commit and environment, and a verifiable bundle tied to the
exact code that produced it. To see the reverse trip — taking such a bundle back
to a reproduced result — see [`REPRODUCING.md`](REPRODUCING.md).

For why the stack is built this way, see [`ARCHITECTURE.md`](ARCHITECTURE.md); for
the repository layout and conventions, [`PROJECT_STRUCTURE.md`](../PROJECT_STRUCTURE.md).
