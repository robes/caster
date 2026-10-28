# Release packaging

A release has two distinct jobs, and Caster treats them separately because the
ecosystem already solves the first one well:

1. **Versioning, registry, retrieval** — "tag this model as v1.2, find it later,
   pull its bytes." Delegated entirely to **GTO** and **DVC**.
2. **Archival bundle** — "produce one self-contained, verifiable object that
   lets an outside reader reproduce the reported result." The `rocrate` and
   `bagit` libraries do the bundle mechanics; Caster supplies the connective
   code they can't — pinning source to the run commit, and knowing the project's
   provenance layout.

## Job 1 — versioning and retrieval (GTO + DVC)

GTO (Git Tag Ops) builds a model registry out of git tags — no server, no
database. It pins a release to a commit:

```bash
gto register my-model --version v1.2.0   # creates a commit-pinned git tag (SemVer, v-prefixed)
gto assign   my-model v1.2 --stage prod
```

Retrieval resolves the tag and pulls the bytes through DVC:

```bash
dvc get <repo> <model-path> --rev my-model@v1.2
# or, by registry name:
dvc artifacts get <repo> my-model
```

GTO does commit-pinned release-tagging correctly and is maintained as part of
the DVC ecosystem, so Caster delegates versioning to it entirely.

GTO deliberately does **not** deliver a bundle — it registers and resolves, and
leaves byte delivery to DVC. That boundary is precisely why Job 2 exists as a
separate Caster step.

## Job 2 — archival assembly (RO-Crate + BagIt, composed)

For the outside reader, a release that ships only weights + metrics is not
reproducible: verifying the numbers needs the code, the eval setup, and the test
data references. Caster gathers those into a single object whose provenance
travels with it — using the standard libraries for the heavy lifting and adding
only what those libraries cannot know.

The mechanism is not bespoke. The `rocrate` Python library builds and writes the
RO-Crate (`crate.add_file`, `crate.add_directory`, `crate.add_file(url,
fetch_remote=False)` for referenced data, `crate.write_zip`); the `bagit`
library writes the BagIt manifests and `bdbag` validates them. Caster does not
reimplement any of that. What Caster contributes is the part no library has:

- **Pinning to the run's commit.** The source distribution is `git archive` at
  the commit recorded in the run's `_provenance.json` — not the live working
  tree. This is what makes the bundle *the experiment that ran*, and it is only
  possible because the run-stamp recorded the commit in the first place.
- **Knowing the project's provenance layout.** That `.hydra/`, the stamp,
  `dvc.lock`/`*.dvc`, the eval dir, and the `data_sources/` records are the
  things to register — and which to embed versus reference — is project-shape
  knowledge Caster encodes and feeds to `rocrate`.
- **Mapping dataset disposition.** Translating omit/fetch/embed (below) onto
  `rocrate`'s `fetch_remote=` and `dvc get --rev <commit>`.

Everything else is the two libraries doing their jobs.

### Two standards, two layers — they compose

A common misconception is that RO-Crate and BagIt are competing choices. They
are not; they address different concerns and are explicitly designed to be used
together:

- **RO-Crate** is the *description* layer: rich, machine-readable metadata
  (schema.org / JSON-LD) describing every item that contributed to the result —
  data, code, config, environment, people, license — and how they relate.
- **BagIt** (and **BDBag**, its "big data" profile) is the *transfer* layer:
  checksum manifests and a fixed payload layout that let a recipient verify
  nothing was corrupted or lost in transit.

The RO-Crate specification states the two have largely separate concerns and
combine simply by placing the RO-Crate files inside the BagIt payload (`data/`)
directory. The RO-Crate originators' own stated vision is a separation between
packaging and description, relying on BagIt/BDBag when the crate must be
shipped, archived, or deposited. There is a reference `bagit-ro` tool and worked
examples (e.g. BioCompute Objects) doing exactly this. Caster follows that
model: the RO-Crate carries the description, and the BagIt/BDBag wrapper is the
outer transfer layer around the `ro-crate-metadata.json`.

### What travels, and why

The bundle is for an **outside reader** who has only the bundle — not your DVC
remote, not your cluster. So the guiding rule is: *everything in the bundle must
be meaningful to someone with only the bundle.* That rules out shipping
`.dvc`/`dvc.lock` pointer files — they are addresses into a DVC remote the reader
cannot reach, dangling references in an artifact meant to be self-contained.

What the bundle carries instead is determined by whether an artifact can be
**regenerated**:

- **Raw / source data — descriptor only, never bytes.** Raw data is the
  pipeline's input; it can't be regenerated, but it's also the most likely to be
  huge, licensed, or private. The bundle carries its **Croissant record**
  (description, citation, license, hashes, and `fetch:` instructions), not its
  bytes. The reader obtains it by following those instructions.
- **Processed data — descriptor by default, bytes opt-in.** Processed data is
  regenerable by re-running preprocessing on the raw data, so it need not travel.
  The bundle carries the preprocessing config + seed so the reader can reproduce
  it; you may opt specific processed datasets *in* (bytes included) when
  preprocessing is expensive or you want the reader to skip it.
- **Splits — bytes by default.** Splits are regenerable too, but tiny (CSVs) and
  they precisely define which samples a result used, so they travel by default
  (overridable for very large splits). Each ships with its
  `_split_properties.json` (sizes, fractions, per-partition index hashes, and —
  where declared — class balance / group disjointness).
- **Checkpoint — bytes.** Copied opaquely with a SHA-256 sidecar; Caster never
  imports the model, so any framework flows through identically. The bytes come
  from `checkpoint_path` (the canonical `models/` model produced by `dvc repro`)
  while *provenance* — the `.hydra` config and `_provenance.json` — comes from
  the train `run_dir`. So a release pairs the pipeline's canonical model with the
  run that produced it.
- **Eval — the held-out result.** The packaged eval (`eval_dir`) should be a
  **held-out test** run (`+eval=holdout`), the honest final number, *not* the
  validation metric used for model selection. Validation lives in the pipeline;
  the release reports the test.
- **Source — `git archive` at the run's commit**, so the bundle is the
  experiment that ran, not the working tree.

> **Set `run_dir` and `eval_dir` to real run directories before packaging.** The
> shipped `configs/release/v1.0_main.yaml` carries placeholder paths
> (`outputs/2026-01-01/00-00-00`) so the field is visible; they are not real
> runs. `run_dir` must point at the train run that supplies the `.hydra` config
> and `_provenance.json` stamp, and `eval_dir` at a held-out (`+eval=holdout`)
> run. The packager refuses if `run_dir` does not exist, and warns loudly if it
> exists but lacks the config or stamp, or if `eval_dir` is missing — because a
> bundle without its provenance chain is just a model file in a wrapper, not a
> reproducible release. (For the commit to be captured, commit before running:
> a run stamped before the first commit records `commit: null`, and the source
> archive then falls back to HEAD with a warning.)

For the integrity of *referenced-but-not-embedded* data, the bundle carries a
portable **`data_manifest.sha256`** — the data hashes extracted from the
project's integrity record (DVC's, or a flat manifest) into a plain
remote-independent file. A reader who re-obtains the data can check it against
these hashes. This replaces the DVC pointers: the hash (identity) travels, the
remote address (infrastructure) does not.

### The bundle layout

```
<release>/                         (BagIt bag root)
  bagit.txt                        BagIt declaration
  bag-info.txt                     transfer metadata + Source-Commit
  manifest-sha256.txt              checksums of every payload file (bagit lib)
  data/                            BagIt payload  ==  RO-Crate root
    ro-crate-metadata.json         the description layer (rocrate lib)
    MODEL_CARD.md                  the human-facing model card (facts auto-filled)
    model/                         checkpoint (opaque bytes) + .sha256
    provenance/
      config/                      Hydra composed config + overrides
      run/                         _provenance.json + _split_properties.json
      data_sources/                records of the datasets the release lists
      eval/                        metrics + predictions
    data_manifest.sha256           portable hashes of referenced data (no remote)
    splits/                        split CSVs (bytes by default)
    datasets/<name>/               only datasets explicitly embedded
    source/                        git archive of the repo at the release commit
```

The contents are read from what the run already produced — the `_provenance.json`
and `_split_properties.json` stamps, the Hydra composed configs, the Croissant
records, the eval artifact, the splits, and the commit-pinned source. `rocrate`
registers each as an entity, and each published dataset as an entity keyed by
its DOI or URL, linked from the root as `isBasedOn`; `bagit`
writes the checksum manifests; `bdbag --validate full .` confirms intactness.

### The model card

The bundle includes a `MODEL_CARD.md` (Mitchell et al., 2019) — the human-facing
companion to the machine-verifiable provenance. The project's own `MODEL_CARD.md`
(in the repo root) is the single source of truth: you author the judgment
sections (intended use, limitations, ethical considerations) directly in it, and
the packager renders a copy into the bundle, filling the *factual* fields it
already has — name, version, commit, license, metrics (held-out eval, else the
run-stamp's stats), and dataset names — via Jinja while carrying your prose
through unchanged. The factual fields use `{{ ... }}` / `{% ... %}` markers
(leave them); the judgment sections are marked `REQUIRED` and cannot be
auto-filled — Caster will not auto-draft plausible-looking prose for them. A card
whose REQUIRED sections are still blank is incomplete even though the facts look
done. A project that deletes its `MODEL_CARD.md` simply ships no card.

### Verification is tolerant, not bit-exact

A regenerated artifact is rarely byte-identical — seeds don't guarantee
determinism across environments, and a split regenerated with the same algorithm
can assign different individual rows while remaining equivalent (same sizes,
same balance). So the bundle's verification model is **equivalence, not
identity**, in rungs from strongest to weakest:

- **Bytes match** (checkpoint, embedded data, `data_manifest.sha256`) — exact,
  the strongest claim where it applies.
- **Structure matches** (`_split_properties.json`: sizes, fractions, index
  hashes) — a regenerated split with matching index hashes is *identical*; with
  matching sizes/fractions but different hashes it is *structurally equivalent*.
- **Task properties match** (declared class balance / group disjointness) — the
  task-level notion of "the same split."
- **Metrics within tolerance** — reproduced numbers should match the recorded
  stats to ~1–2 decimal places, not bit-for-bit.

A stored hash is therefore a strong *signal*, not a pass/fail gate: a mismatch
means "investigate," not "failed" — it may be benign nondeterminism. The bundle
states the task-equivalence notion in each split's `equivalence_notes`.

### Dataset disposition

Each listed dataset's disposition is checked against the `type` in its
[`data_sources/` record](DATA_SOURCES.md#two-source-types); the record and the
`data_manifest.sha256` entry always travel:

- **reference** — the bundle names the dataset by its record's `url` (an
  RO-Crate entity and a line in the model card); the bytes do not travel. The
  reader obtains them there.
- **embed** — the bytes are pulled at the release commit (`dvc get … --rev
  <commit>`) into `datasets/<name>/`, for a fully self-sufficient deposit.
- **omit** — neither bytes nor a pointer; the record says how the data was made.

| Source type | reference | embed | omit |
|---|---|---|---|
| `reference` | expected (default) | if you may redistribute it | warning |
| `local` | error | small or open data | default (large or controlled access) |

A dataset without a record, or a disposition its type does not allow, stops the
packager, and `--dry-run` reports it the same way.

### The reader's path

With only the bundle, a reader: (1) obtains the raw data via the Croissant
records' instructions; (2) recreates the environment from the source's lockfile;
(3) runs the pipeline — `dvc repro`, or the documented step-by-step commands if
they don't use DVC — to regenerate processed data and splits; (4) checks results
against the bundle, expecting *equivalence* within the tolerances above. The
shipped splits let them pin exactly which samples each partition used.

### Where it goes

The assembled bag is suitable for a DOI-issuing archival deposit (Zenodo, OSF,
Dataverse) — where an outside reader with only the deposit can both *use* and
*verify* the model.

## When to use which

| You want to… | Use |
|---|---|
| tag a model version and pull it later | GTO + `dvc get` |
| hand a colleague the model + how-to-verify | RO-Crate, reference published data |
| deposit for a paper, with a DOI | RO-Crate + BagIt, reference or embed redistributable data |
| nothing yet — still iterating | neither; the run-stamp already makes runs reproducible |

Packaging is opt-in and release-time only. The day-to-day reproducibility loop
needs none of it.

### Options for scripted/agent use

`caster package <config>` accepts:

- `--dry-run` — report what would be packaged and which warnings would fire
  (missing stamp/config/eval, HEAD fallback), writing nothing. Check a release
  config before building.
- `--json` — emit the standard `{"ok", "command", "result", "problems"}`
  envelope on stdout (only), with warnings under `problems`, so the asset name,
  source commit, and any provenance gaps can be read programmatically.
