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

## Three situations

Real projects span all three. Each needs a different amount of effort — and only
one needs any Caster code at all.

### Situation 1 — the dataset already has Croissant (just download it)

Hugging Face, Kaggle, and OpenML emit Croissant for their datasets, as a plain
JSON-LD document at a stable URL. The handle is the **repository dataset ID**,
not a DOI — these platforms key on their own IDs, and most do not mint DOIs.
(DOIs show up on archival repositories; see Situation 2.)

There is nothing to import: you download the file and drop it in `data_sources/`.

```bash
# Hugging Face
curl -L https://huggingface.co/api/datasets/<owner>/<name>/croissant \
  -o data_sources/<name>.json

# Kaggle  (or use the "Croissant" button on the dataset page)
curl -L https://www.kaggle.com/datasets/<owner>/<name>/croissant/download \
  -o data_sources/<name>.json

# OpenML  — use the Croissant download on the dataset page
```

The downloaded record carries the dataset's own declared provenance and license;
no hand-authoring is needed. Caster does not wrap this in a command — fetching a
public file at a known URL is a one-liner, and a wrapper around it would be
ceremony with nothing to maintain.

Two things are worth doing after the download, and neither is import-specific:

- **Validate it** — the same `data_sources/` validator that runs in pre-commit
  checks any record, however it arrived (see [Validation](#validation)).
- **Pin which version you documented** — HF/Kaggle regenerate Croissant from the
  live dataset, so record the dataset revision alongside the file (the DVC
  revision of the bytes, or the HF commit), so "which version of the dataset is
  this describing?" stays answerable.

You still `dvc add` (or document-as-untracked) the actual bytes separately —
Croissant describes, DVC pins.

### Situation 2 — a DOI / archival source, but no Croissant (author it)

The dataset lives on Zenodo, Dataverse, OSF, or an institutional repository,
has a DOI, but no ready Croissant record. Here there *is* a real
transformation, so Caster provides a helper: you write a short YAML, and Caster
emits valid Croissant JSON-LD from it. The YAML is a thin, human-friendly
surface over the standard — you fill in what you know, Caster expands it:

```yaml
name: brain_mri
description: >
  T1-weighted brain MRI volumes with per-subject segmentation labels.
url: https://doi.org/10.5281/zenodo.0000000
license: CC-BY-4.0
citation:
  doi: 10.5281/zenodo.0000000
  bibtex: |
    @dataset{doe2024brainmri, ... }
distribution:
  - name: brain_mri_bag.zip
    url: https://zenodo.org/.../brain_mri_bag.zip   # required: Croissant needs a contentUrl
    sha256: <archive digest>          # integrity check before extraction
    # (a top-level `url:` is used as the default if a per-entry url is omitted;
    #  for data with no public URL, use `distribution: local` instead of a list)
fetch: |
  Distributed as a BagIt bag. Download the bag zip from the URL above,
  then materialize:
    bdbag --resolve-fetch all <bag_dir>
    bdbag --validate fast    <bag_dir>
  Move resolved contents into data/raw/brain_mri/.
```

```bash
caster data emit brain_mri      # -> data_sources/brain_mri.json (Croissant)
```

`emit` is a thin surface over the `mlcroissant` library's builder API
(`mlc.nodes.Metadata(...).to_json()`). It exists so you can document a dataset
by filling in a handful of familiar YAML keys instead of learning the library's
node classes — it maps those keys onto `mlcroissant` and lets the library
generate and validate the JSON-LD. It covers the common case: the flat
descriptive metadata (name, description, license, citation, distribution,
provenance prose) that a research dataset actually needs. It deliberately does
*not* try to author rich `RecordSet`/`Field` structure (per-column types, nested
relations) — if you need that level of detail, the MLCommons **Croissant
editor** is the right tool, and `emit`'s output is a valid starting point you can
load into it.

The `fetch:` prose is deliberately free-form. Real-world acquisition is too
varied to wrap in a generic fetcher — some sources need `wget`, some a BagIt
client, some authenticated S3, some manual approval. Documenting the procedure
honestly beats a fetcher that handles every case except yours.

### Situation 3 — data that exists nowhere public (assemble it)

The common research case: data you gathered in the lab, generated from a
simulation, or received privately, that has no canonical web location at all.
There is nothing to download and no DOI to point at. You author the metadata
locally — either by hand in the same lightweight YAML as Situation 2, or with
the MLCommons **Croissant editor**.

```yaml
name: clinic_cohort_2026
description: >
  De-identified cohort assembled in-house from three clinical sources
  (see notes). Not publicly redistributable.
license: proprietary
citation: ~                              # no prior publication; omit
provenance: |
  Assembled 2026-02 from:
    - Source A export (IRB #1234), columns X,Y,Z
    - Source B registry pull, joined on subject hash
    - Manual adjudication of 142 ambiguous records
  Reassembly requires institutional access; contact <PI email>.
distribution: local                      # bytes live only in data/ + DVC remote
```

The MLCommons **Croissant editor** (an existing, hosted tool — Caster does not
ship its own) can load your assembled files and derive a starting RecordSet
(column types, structure), which you then refine, so even home-grown data gets
standards-shaped metadata without writing JSON-LD by hand. Run the result
through `caster data emit` (if you authored YAML) or straight through
the validator (if you authored JSON-LD).

Here Croissant documents *what the data is and how it was built*; DVC (with a
private or local remote) versions the bytes; the `provenance:` prose carries the
reassembly recipe that no hash can capture. For data that genuinely cannot be
shared, this is the honest ceiling of reproducibility: a reader can verify
*your* run against the pinned bytes, and understand how the data was made, even
if they cannot independently re-obtain it.

## Validation

One piece of Caster code touches every `data_sources/` record regardless of how
it got there: a validator, wired into pre-commit and CI. It does two things:

- **Schema check** — every record is well-formed Croissant, validated by the
  `mlcroissant` library (Caster does not write its own validator — it calls
  `mlcroissant`), whether the record was downloaded (Situation 1), emitted
  (Situation 2), or authored (Situation 3).
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
