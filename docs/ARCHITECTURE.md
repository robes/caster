# Architecture

This document is the design map. For each reproducibility concern, it names the
tool or standard that satisfies it, states whether Caster *uses* it directly or
*adds* something, and shows the minimal glue. The guiding rule: **build only
what no mature tool already does.**

## The map

| Concern | Satisfied by | Caster's role | Glue |
|---|---|---|---|
| Code state | git | use | none |
| Config management + composed-config record | Hydra | use | none |
| Per-run output directory | Hydra | use | base config |
| Data/model byte versioning + restore | DVC | use | none |
| Pipeline DAG + dependency-aware re-run | DVC (`dvc.yaml`, `dvc repro`) | use, optional | stage defs |
| Declared environment | `uv.lock` (or `pip`/conda) | use | none |
| **Commit ↔ run-dir + actual-environment link** | — *(nothing does this)* | **add** | run-stamp callback |
| **Dataset documentation / provenance / license** | Croissant | use standard, add convention | validator + emit |
| Release versioning + registry + retrieval | GTO + `dvc get` | use | git tags |
| **Archival bundle** | RO-Crate + BagIt (`rocrate`, `bagit` libs) | use libraries, add commit-pin + layout | packager glue |

Three rows are bold. Those are Caster's entire surface area of *new* behavior.
Everything else is the underlying tools doing their job, pre-wired.

## Why each tool is the right one for its job

**Config record.** Hydra writes `.hydra/config.yaml` — the fully *composed*
config, including overrides — into every run dir. This captures the composition,
not just the final values, and Caster treats it as the authoritative record of
what parameters a run used. No separate config-capture step is needed.

**Data hashing.** `dvc add` content-addresses every tracked file; the resulting
`.dvc` and `dvc.lock` files *are* the per-file manifest, and `dvc status` is the
verifier. The one case it does not cover — huge source files you deliberately do
not `dvc add` — is handled by documenting them in `data_sources/` and, if
integrity-checking is wanted, a plain `sha256sum` manifest alongside.

**Experiment comparison.** `dvc exp show` compares metrics across DVC-driven
runs. For teams that also want a flat, human-readable ledger, the
`_provenance.json` files make regenerating one a ~30-line script — shipped as
optional, not core.

**Release versioning.** GTO is purpose-built for this: `gto register <model> --version v1.0.0`
[<commit>]` creates a commit-pinned git tag, and retrieval is `dvc get <repo>
<path> --rev <tag>`. Caster delegates release versioning to it rather than
carrying its own.

## Why each "add" has no home in the tools

The line Caster holds throughout: **its own code is justified only where it
encodes knowledge no library has** — the link from a run to its commit, the
shape of this project's provenance, the disposition policy for datasets.
Wherever a mature library already performs the mechanism (`mlcroissant` for
Croissant generation and validation, `rocrate` for RO-Crate authoring, `bagit`
for manifests, GTO for release tagging), Caster calls it rather than
reimplementing it. The three additions below are precisely the residue that no
library can supply.

**Run-stamp.** Detailed in [`PROVENANCE.md`](PROVENANCE.md). The short version:
Hydra writes the run dir but not the commit; DVC links code↔data through the
pipeline graph, keyed to stages, not to an arbitrary Hydra run folder; the
common workaround (log the commit to W&B/MLflow) forces an experiment-tracker
dependency. Caster's callback writes commit + environment + completion + stats
into each run dir with no tracker required.

**Data-source convention.** Croissant standardizes the *vocabulary* and the
`mlcroissant` library does validation and JSON-LD generation; what neither
prescribes is a *project convention*: where the records live, that every tracked
dataset must have one, and a friendly way to author one when a dataset has only
a DOI. Caster supplies that convention, a validator that calls `mlcroissant` and
adds a coverage check (every tracked dataset has a record), and an `emit` helper
that maps a lightweight YAML onto `mlcroissant`'s builder so the user needn't
learn its node API for the common flat-metadata case. (Datasets that already
publish Croissant need no Caster code — you download the file; rich structural
authoring is deferred to the MLCommons Croissant editor.) Detailed in
[`DATA_SOURCES.md`](DATA_SOURCES.md).

**Archival assembly.** GTO versions a release but, by design, does not deliver a
bundle — it leaves retrieval to DVC. The `rocrate` and `bagit` libraries do the
bundle *mechanics*: `rocrate` builds and writes `ro-crate-metadata.json` and
registers files/directories (including referenced-but-not-embedded data);
`bagit` writes the checksum manifests. What no library knows is the part that
makes the bundle reproducible rather than merely packaged: that the source must
be `git archive`d at the commit in the run's stamp, which directories constitute
this project's provenance chain, and how omit/fetch/embed map onto the
libraries' options. Caster's packager is that connective code — it arranges the
project's provenance into the layout the libraries expect, pins the source to
the run commit, and invokes them. Detailed in [`PACKAGING.md`](PACKAGING.md).

## The unopinionated boundary

Caster sets defaults but does not lock them in:

- **DVC remote.** Default config points at a *local remote* (a directory) so a
  one-person project needs zero infrastructure. Swap in S3/GCS/Azure/SSH by
  changing one remote URL. Caster's code never assumes which.
- **Experiment tracking.** Default is none. Run directories are
  TensorBoard-compatible (point a writer at the run dir and
  `tensorboard --logdir outputs/` works) — that plus the run-stamp is enough at
  small scale, though the template installs neither and wires no logger. Wiring
  W&B/MLflow/Neptune is adding a Hydra logger config; the run-stamp keeps working
  alongside it.
- **Pipeline DAG.** `dvc.yaml` is available but optional. Run scripts by hand in
  order, or wire them into stages for `dvc repro`. The run-stamp fires either
  way.
- **Framework.** Caster never imports your model. PyTorch, JAX, TF,
  scikit-learn, XGBoost — the checkpoint is opaque bytes to every part of
  Caster that touches it.

## Sizing the contribution

The underlying tools are tens of thousands of lines, maintained by their own
communities. Caster's own code — the callback, the validator, the packager — is
on the order of a few hundred lines. That ratio is the design working: a
reproducibility template whose own code is small is one that has put the heavy
lifting where it belongs, on mature dependencies. Caster's contribution is the
integration, the standards selection, and the conventions — not the line count.
