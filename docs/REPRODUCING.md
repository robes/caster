# How-to: from package to a reproduced result

This is the consumer's counterpart to [`HOWTO.md`](HOWTO.md): you have a Caster
release bundle — handed to you, or downloaded from a DOI archive (Zenodo, OSF,
Dataverse) — and you want to **verify and reproduce** its result. It assumes
nothing about your having the original repository, DVC remote, or compute: only
the bundle. For *why* the bundle is shaped this way and what "reproduce" means
here (equivalence, not bit-identity), see [`PACKAGING.md`](PACKAGING.md).

A bundle is a BagIt bag whose payload is an RO-Crate. Inside `data/` you will
find: `MODEL_CARD.md`, `model/` (the checkpoint + its `.sha256`), `provenance/`
(the run-stamp, composed config, eval, and Croissant `data_sources/`),
`data_manifest.sha256`, `splits/`, `source/` (a git archive at the release
commit), and — for embedded datasets — `datasets/<name>/`.

---

## 1. Verify the bundle is intact

Before trusting anything inside, confirm nothing was corrupted in transit. The
BagIt manifest covers every payload file:

```bash
bdbag --validate full <bundle_dir>     # or: check manifest-sha256.txt by hand
```

A clean validation means the bytes you have are the bytes that were deposited.

## 2. Read before you run

Orient yourself in what you're about to reproduce:

- `data/MODEL_CARD.md` — what the model is, its intended use, and the reported
  metrics.
- `data/provenance/run/_provenance.json` — the **target**: the commit, the
  recorded environment, and the headline stats you expect to reproduce.
- `data/ro-crate-metadata.json` — the structured description tying it together.

This is your triage: what data, what environment, what numbers.

## 3. Recreate the environment

The bundle's `data/source/` holds a git archive of the project at the release
commit, as a tarball. Extract it and build the environment from its lockfile:

```bash
tar xf <bundle>/data/source/<project>-<version>.tar.gz -C ./reproduce
cd ./reproduce/<project>-<version>
uv sync --extra dev --extra packaging        # recreate from the pinned deps
```

Cross-check against the `environment` block in `_provenance.json` (Python
version, the recorded `uv_lock_sha256`, and `env_synced` with any drifted
packages). Mismatches here are the first thing to suspect if results later
diverge.

## 4. Restore the data — do **not** let the pipeline re-acquire it

This is the step that matters most, and the one that is easy to get wrong.

The pipeline's first stage (`fetch`) *acquires* data — for a real project it
downloads; for the worked example it generates from a seed. If you simply run
`dvc repro`, DVC will run `fetch` and **re-acquire**, ignoring the data that came
in the bundle — re-downloading (which may fail or return different bytes) or
regenerating. That is not reproducing *this* release; it is making a new one.

Instead, restore the bundled data and tell DVC the acquisition stage is already
satisfied, so it runs only the stages *downstream* of it:

```bash
# put the bundled raw data where the pipeline expects it
mkdir -p data/raw/synthetic
cp -r <bundle>/data/datasets/synthetic/* data/raw/synthetic/

# record the restored data as the current 'fetch' output, then reproduce.
# DVC sees fetch's output already matches and skips it, running preprocess →
# split → train → evaluate on the *bundled* bytes.
uv run dvc commit fetch -f
uv run dvc repro
```

You should see `Stage 'fetch' didn't change, skipping`, then the downstream
stages run. If you instead see `Running stage 'fetch'`, stop — the bundled data
is being regenerated, and you are no longer reproducing from what was shipped.

(For a dataset shipped with `fetch` disposition rather than `embed`, follow the
URLs in `fetch.txt` / the Croissant records to obtain the bytes, place them the
same way, then `dvc commit fetch` as above. Datasets shipped as `omit` are not
in the bundle at all — obtain them via their Croissant `data_sources/` record.)

## 5. Check equivalence

Compare what you regenerated against what the bundle recorded. Reproduction here
means **equivalence, not byte-identity** — see PACKAGING's
[verification model](PACKAGING.md#verification-is-tolerant-not-bit-exact) for the
rungs. Concretely:

- **Bytes** — compare your checkpoint and restored data against the bundle's
  `model/*.sha256` and `data_manifest.sha256`. Exact matches are the strongest
  claim (and the restored data should match by construction).
- **Structure** — compare your run's `_split_properties.json` to the bundle's:
  matching index hashes mean *identical* splits; matching sizes/fractions with
  different hashes mean *structurally equivalent* ones.
- **Metrics** — compare your reproduced numbers to the recorded stats in
  `_provenance.json` / `eval/`, expecting agreement to ~1–2 decimal places, not
  bit-for-bit.

A hash mismatch is a signal to **investigate**, not a verdict of failure: it may
be benign nondeterminism (seeds don't guarantee cross-environment determinism).
Each split's `equivalence_notes` states the task-level notion of "the same
split" to judge against.

---

You have now reproduced the release from the bundle alone — no original remote,
no original machine — and checked your result against the recorded one. That
round trip, producible by [`HOWTO.md`](HOWTO.md) and reproducible here, is the
point of the whole template.

This exact round trip is automated for the worked example in
[`tests/closed_loop.sh`](../tests/closed_loop.sh) (`make closed-loop`): it
produces a release, reproduces it from the bundle alone, and asserts the metric
matches — a living check that the loop stays closed.
