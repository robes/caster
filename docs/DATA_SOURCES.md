# Data sources

DVC pins what bytes a dataset is. This directory documents what the dataset
*is*: where it came from, how to cite it, what license governs it, and how to
obtain it again. Caster aligns that documentation with **Croissant**, the
MLCommons metadata standard for ML datasets, so the records are interoperable
rather than invented.

Listing `data_sources/` should always answer: **what external data does this
project use, and how would someone else get it?**

## Why Croissant

Croissant is an open, community-built metadata vocabulary for ML datasets,
built as an extension of schema.org and serialized as JSON-LD. It is already
emitted by Hugging Face, Kaggle, and OpenML across hundreds of thousands of
datasets, loadable by TensorFlow / JAX / PyTorch, and discoverable through
Google Dataset Search. It builds on the PROV-O (provenance) and DUO (data-use)
ontologies, so license and lineage are first-class. Adopting it means Caster's
data documentation is a recognized format an outside reader and outside tooling
already understand — and it costs nothing extra, because the fields we need
(name, description, URL, citation, license, distribution) are exactly what
Croissant already carries.

## Scope: what gets a record

A `data_sources/<name>` record is for any dataset whose bytes you depend on and
that is **not** transparently fetched by a managed library at run time.

Gets a record:

- Data from research repositories (Zenodo, OSF, Dataverse, FaceBase,
  institutional storage).
- Lab data: collected, generated, or assembled in-house.
- Tarballs, zips, BagIt bags from any source not wrapped by a library.
- Data fetched by hand from a portal, or received by request from an author.

Does not need one:

- Datasets loaded by a library that pins its own version (a Hugging Face
  `datasets` load with a revision, a TorchVision dataset). The library
  identifier and revision travel in the Hydra config that consumes them. (You
  *may* still keep a Croissant record for documentation; you need not.)

A pre-commit / CI check enforces the rule: every DVC-tracked dataset directory
must have a matching `data_sources/` record.

## Two source types

Every dataset has a `data_sources/<name>.yaml` whose `type` says where the data
lives. A release checks its per-dataset disposition against that type (see
[PACKAGING](PACKAGING.md#dataset-disposition)).

### `reference` — published elsewhere

The dataset lives where a reader can reach it by a DOI or URL: Hugging Face,
Kaggle, OpenML, Zenodo, Dataverse, FaceBase, an institutional repository. `url`
is that DOI or URL, which by definition is enough to find and obtain the data;
`format` says what the reader gets there: `croissant`, `bdbag`, `rocrate`, or
`other`.

**If it already publishes Croissant** (Hugging Face, Kaggle, OpenML emit it as a
plain JSON-LD document; their handle is the repository dataset ID, usually not a
DOI), the YAML is thin and the record is the upstream file, downloaded:

```yaml
name: <name>
type: reference
format: croissant
url: https://huggingface.co/datasets/<owner>/<name>
```

```bash
# Hugging Face
curl -L https://huggingface.co/api/datasets/<owner>/<name>/croissant \
  -o data_sources/<name>.json
# Kaggle (or the "Croissant" button on the dataset page)
curl -L https://www.kaggle.com/datasets/<owner>/<name>/croissant/download \
  -o data_sources/<name>.json
# OpenML: the Croissant download on the dataset page
caster data emit <name>     # validates the download; never overwrites it
```

The committed copy pins the description you used: HF/Kaggle regenerate Croissant
from the live dataset, so also record the dataset revision (the DVC revision of
the bytes, or the HF commit). You still `dvc add` (or document-as-untracked)
the bytes separately: Croissant describes, DVC pins.

**Otherwise** (a DOI on Zenodo, Dataverse, OSF or FaceBase; a BDBag; an
RO-Crate) you write the YAML and Caster emits the Croissant from it:

```yaml
name: brain_mri
type: reference
format: bdbag
description: >
  T1-weighted brain MRI volumes with per-subject segmentation labels.
url: https://doi.org/10.5281/zenodo.0000000
license: CC-BY-4.0
citation:
  doi: 10.5281/zenodo.0000000
  bibtex: |
    @dataset{doe2024brainmri, ... }
distribution:                       # optional for a reference: files with digests
  - name: brain_mri_bag.zip
    url: https://zenodo.org/.../brain_mri_bag.zip
    sha256: <archive digest>
fetch: |
  Download the bag from the DOI above, then materialize:
    bdbag --resolve-fetch all <bag_dir>
    bdbag --validate fast    <bag_dir>
  Move resolved contents into data/raw/brain_mri/.
```

```bash
caster data emit brain_mri      # -> data_sources/brain_mri.json (Croissant)
```

`emit` is a thin surface over the `mlcroissant` library's builder API: it maps a
handful of familiar YAML keys onto `mlcroissant` and lets the library generate
and validate the JSON-LD. It covers the flat descriptive metadata a research
dataset needs (name, description, license, citation, distribution, provenance
prose) and deliberately does *not* author rich `RecordSet`/`Field` structure;
for that, load `emit`'s output into the MLCommons **Croissant editor**.

The `fetch:` prose is deliberately free-form. Real-world acquisition is too
varied to wrap in a generic fetcher (`wget`, a BagIt client, authenticated S3,
manual approval), and Caster fetches nothing from `url`. Documenting the
procedure honestly beats a fetcher that handles every case except yours.

### `local` — produced by the project

Data the lab collected, curated, acquired privately or generated: images of
biosamples, an EHR extract from the medical school, a simulation's output, the
template's synthetic example. There is nothing public to point at. You author
the YAML by hand (or start from the MLCommons **Croissant editor**, which can
derive a RecordSet from your files) and run it through `caster data emit`.

```yaml
name: clinic_cohort_2026
type: local
description: >
  De-identified cohort assembled in-house from three clinical sources
  (see notes). Not publicly redistributable.
license: proprietary
provenance: |
  Assembled 2026-02 from:
    - Source A export (IRB #1234), columns X,Y,Z
    - Source B registry pull, joined on subject hash
    - Manual adjudication of 142 ambiguous records
  Reassembly requires institutional access; contact <PI email>.
```

Here Croissant documents *what the data is and how it was built*; DVC (with a
private or local remote) versions the bytes; the `provenance:` prose carries the
reassembly or regeneration recipe that no hash can capture. For data that
genuinely cannot be shared, this is the honest ceiling of reproducibility: a
reader can verify *your* run against the pinned bytes, and understand how the
data was made, even if they cannot independently re-obtain it. A release can
still carry the bytes when they are small and open (`embed`).

## Validation

One piece of Caster code touches every `data_sources/` record regardless of how
it got there: a validator, wired into pre-commit and CI. It does three things:

- **Type check** — every YAML states `type`, and a `reference` has a `url` and
  a `format`.
- **Schema check** — every record is well-formed Croissant, validated by the
  `mlcroissant` library (Caster does not write its own validator — it calls
  `mlcroissant`), whether it was downloaded or emitted.
- **Coverage check** — every DVC-tracked dataset directory under `data/` has a
  matching record in `data_sources/`, so a tracked dataset can never be
  undocumented.

This is deliberately separate from acquisition. Downloading a public Croissant
file needs no Caster tool; *trusting* that file — and the rest of the directory
— is what the validator is for.

Run it with `caster data validate` (exit 0 if clean, non-zero with the
problems listed otherwise). For scripted/agent use, `--json` emits the standard
`{"ok", "command", "result", "problems"}` envelope on stdout only, so each
problem can be handled programmatically.

## The relationship to DVC, in one line

**Croissant says what the data is; DVC says which bytes it is; the `fetch:` /
`provenance:` prose says how to get or rebuild it.** The three are
complementary, and a complete `data_sources/` record uses as many as apply.

## Where the data goes

The bytes live at `data/<name>/` — DVC-tracked (committed `.dvc` pointer, bytes
in the remote) or, for deliberately-untracked huge sources, present-but-ignored
with a documented fetch procedure. The internal structure is whatever the
dataset naturally has; Caster enforces no `raw/` vs `processed/` sub-naming. A
preprocessed dataset is its own sibling directory and its own pipeline artifact.
