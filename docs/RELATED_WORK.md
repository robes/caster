# Related work

This document situates Caster among existing tools, standards, and templates. It
serves two purposes: an honest account of what already exists (so Caster builds
on it rather than duplicating it), and the trade-off record behind each design
choice. It is the "related work" section a write-up of Caster would draw on.

## Project templates (the closest prior art)

The git + Hydra + DVC project template is well-trodden ground.

- **Cookiecutter Data Science (CCDS)** — DrivenData's long-standing, widely
  adopted structure (V2 released 2024). Establishes layout conventions; pipeline
  orchestration historically via `make`. Does not, on its own, integrate Hydra +
  DVC or address run-level provenance.
- **khuyentran1401 / data-science-template**, **CodeCutTech /
  data-science-template** — Hydra + DVC + pre-commit + (Poetry/uv) cookiecutters.
  Very close to Caster's *base* stack. They stop at structure and tooling; they
  do not add a commit↔run-dir stamp, a standards-aligned data-source convention,
  or archival packaging.
- **ashleve / lightning-hydra-template** (and its many forks: HelmholtzAI,
  gorodnitskiy's "yet-another", etc.) — the canonical *research* template,
  thousands of stars. Wraps training in a `task_wrapper` that handles failure
  and "saves info about the crash" (a partial analogue of the completion
  marker), and some forks include an explicit `log_metadata` step. Two gaps
  relative to Caster's objectives: it is **PyTorch-Lightning-coupled** (fails
  framework-agnosticism), and its provenance capture leans on a configured
  logger (W&B / MLflow / Neptune), reintroducing an experiment-tracker
  dependency.

**Where Caster sits:** it is a *framework-agnostic* template that captures
commit + environment provenance **without** requiring a logger, plus the
data-source and packaging layers the above omit. The base stack is not novel and
should be started from one of these; the additions are the contribution.

## The commit-to-run-dir gap

That the run-stamp is needed at all is corroborated by Hydra's own issue
tracker: there is a standing feature request to store the git commit hash in the
`.hydra` output folder at launch, on the explicit premise that commit-hash +
composed config together would let one reproduce most experiments from the
`.hydra` folder alone. The feature is not built into Hydra. Existing templates
that *do* capture the commit generally route it through an experiment-tracking
logger. Caster's contribution here is a small, logger-independent callback that
writes the commit and the actual environment into every run dir by default.

## Configuration management

- **Hydra** — composes configs from groups, writes the composed config and
  overrides per run, drives multirun sweeps. Caster uses it as the configuration
  layer, and treats the composed-config dump as the authoritative record of a
  run's parameters.
- **Plain YAML + argparse** — fewer concepts, but no composition, no sweeps, and
  the "frozen config is the complete record" guarantee must be enforced by
  discipline rather than provided by the tool. Trade-off: Hydra's learning cost
  is modest and it *provides* the run-dir and composed-config capture; plain YAML
  wins only for projects that never want config composition. Caster chooses
  Hydra.

## Data versioning and provenance

- **DVC** — content-addresses data and models, restores any version, is
  remote-agnostic (local directory, S3, GCS, SSH). Caster uses it as the
  byte-versioning layer; the `.dvc` / `dvc.lock` files serve as the per-file
  integrity manifest.
- **git-LFS** — versions large files in git, but lacks DVC's pipeline DAG,
  experiment tooling, and flexible remotes. Not chosen as the primary data layer.
- **Croissant (MLCommons)** — the open metadata standard for ML datasets;
  schema.org-based JSON-LD, built on PROV-O and DUO for provenance and data-use.
  Emitted by Hugging Face, Kaggle, OpenML; loadable by TF/JAX/PyTorch;
  discoverable via Google Dataset Search. Caster adopts it as the schema for
  `data_sources/`, with a thin YAML authoring surface over it for the cases where
  no Croissant record exists yet.
- **Datasheets for Datasets** (Gebru et al.) — the conceptual ancestor:
  human-authored dataset documentation. Croissant operationalizes the same
  intent in machine-readable form; Caster uses Croissant for that reason.
- **DCAT**, **Frictionless Data Packages** — general dataset-description
  vocabularies. Croissant is the ML-specialized choice and interoperates with
  these (it is translatable to other vocabularies).

**Trade-off recorded:** DVC *stores and restores* bytes; Croissant *describes*
them; neither captures the human recipe for assembling private real-world data.
Caster keeps a free-form `provenance:` / `fetch:` prose field for exactly that —
the honest alternative to a generic fetcher that cannot cover every acquisition
path.

## Release versioning and packaging

- **GTO (Git Tag Ops)** — commit-pinned model registry from git tags, no server;
  retrieval via `dvc get`/`dvc artifacts get`. Caster uses it as the
  versioning/registry layer. GTO explicitly does *not* deliver bundles — it
  leaves byte delivery to DVC, which is why a separate assembly step is
  warranted.
- **MLflow Model Registry**, **Hugging Face Hub**, **W&B Artifacts** — registry
  + hosting, but each is a service (or a hosted hub). GTO keeps the registry
  inside the git repo with no extra service, matching the "no mandatory server"
  priority. HF Hub remains a fine *publication* target reachable from the
  packaging step.
- **RO-Crate** — packages all items contributing to a research outcome with
  schema.org/JSON-LD metadata; the description layer of Caster's archival bundle.
  Its Workflow-Run profile aligns to W3C PROV and is implemented by several
  workflow systems.
- **BagIt / BDBag** — checksum-manifested transfer format; the verifiable-
  transfer layer. **Composes with RO-Crate** (RO-Crate files inside the BagIt
  `data/` payload), per the RO-Crate spec's own guidance and the `bagit-ro`
  reference tooling. Caster's bundle places the `ro-crate-metadata.json` inside
  the BagIt payload, using each standard for what it is built for.

**Trade-off recorded:** a published checkpoint alone (HF / a bare release asset)
lets a reader *use* a model but not *verify* its numbers, which needs the code
and test-data references. The RO-Crate + BagIt bundle is the artifact that
carries enough to verify, at the cost of being assembled and deposited
deliberately at release time.

## Heavier alternatives deliberately not adopted as core

- **MLflow / experiment-tracking servers** — valuable at larger scale; a
  mandatory server at small-team scale is the operational cost Caster avoids.
  Remains optional via a Hydra logger config.
- **Kubeflow / Metaflow / orchestration frameworks** — full pipeline
  orchestration; far beyond a small research team's needs and the "no
  infrastructure" priority.
- **Snakemake / Nextflow** — workflow managers with strong provenance (Nextflow
  even emits Workflow-Run RO-Crate). A reasonable alternative DAG layer; Caster
  uses DVC's lighter `dvc.yaml` to keep one tool covering both data and
  pipeline, but a project already invested in these could substitute them.

## Summary of the contribution

Caster is not the first git+Hydra+DVC template, the first ML dataset metadata
standard, or the first model registry — and it builds on the best existing tool
for each of those. Its contribution is the **integration**: a coherent,
framework-agnostic, server-optional assembly of these tools, plus the three thin
additions none of them provides on its own — a logger-independent run-stamp, a
Croissant-aligned data-source convention (with a validator and a YAML-to-Croissant
`emit` helper for the author-from-scratch case), and a
provenance-carrying RO-Crate/BagIt release assembly. The contribution is in the
selection and the seams, not in any single component, and that is the intended
outcome of the design.
