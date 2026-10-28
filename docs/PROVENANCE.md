# Provenance

Recording the provenance of your workflow — so any result traces back to the
exact code, configuration, data, and environment that produced it — is one of
Caster's three duties (alongside [data sources](DATA_SOURCES.md) and
[packaging](PACKAGING.md)).

Most of this duty is carried by the stack, not by Caster. Each tool owns a layer:

- **git** pins the *code*. The commit is the anchor everything else hangs off.
- **Hydra** pins the *configuration*. The composed `.hydra/config.yaml` written
  into every run directory is the complete record of the parameters a run used.
- **DVC** pins the *data and pipeline*. `dvc.lock` and the `.dvc` pointer files
  tie a run to exact input bytes and stage outputs, content-addressed.
- **`uv.lock`** pins the *declared environment*.

Together these already record code, config, data, and intended environment.
What none of them records is the **seam** between them: the link from *a
specific run directory on disk* to *the commit and the actual environment that
produced it*. Hydra writes the run directory but not the commit (a
[long-standing, explicitly-requested gap in Hydra
itself](RELATED_WORK.md#the-commit-to-run-dir-gap)); DVC's linkage is keyed to
pipeline stages, not to an arbitrary Hydra run folder; logging the commit to a
tracker like W&B or MLflow works but forces a dependency a small project should
not need.

Caster's contribution to this duty is small and specific: a **run-stamp** that
fills that seam. It is connective tissue over the four tools above, not a
replacement for them — your provenance rests on mature, widely-used software,
and the run-stamp just welds the last joint.

## The run-stamp

The run-stamp writes one file, `_provenance.json`, into every run directory,
binding it to its commit and actual environment with a completion marker and the
run's headline stats — no tracking server required:

```json
{
  "git": {
    "commit": "39abc278f5b8…",
    "branch": "feat/new-aug",
    "dirty": false
  },
  "environment": {
    "python": "3.11.8",
    "platform": "Linux-6.5.0-x86_64",
    "uv_version": "0.12.10",
    "uv_lock_sha256": "a1b2c3…",
    "lock_current": true,
    "env_synced": true,
    "sync_selection": ["--all-extras"],
    "sync_drift": null
  },
  "invocation": {
    "script": "scripts/train.py",
    "config_name": "config",
    "overrides": ["model.C=0.5"],
    "command": "python scripts/train.py model.C=0.5",
    "multirun": false
  },
  "completed": true,
  "created_at": "2026-05-29T18:04:11Z",
  "stats": { "val_accuracy": 0.91 }
}
```

- **`git`** — the commit is the reproducibility anchor; `dirty: true` means
  uncommitted code was in effect and the run is not exactly reproducible.
  `dirty` is about code: it ignores the output roots (`outputs/`, `multirun/`,
  the run's own directory) and what `dvc repro` writes between stages
  (`dvc.lock` and the outputs the root `dvc.yaml` declares), but still sees an
  untracked source file. Under Hydra it is taken when the job starts, so a run
  that rewrites a tracked report does not flag itself; the `caster provenance
  stamp` CLI takes it at stamp time. Two fields appear only when needed:
  - **`dirty_files`** — when dirty, the paths that differ.
  - **`dvc_stage` + `dvc_inputs`** (inside `dvc repro`) or **`dvc_lock`**
    (otherwise) — when the working `dvc.lock` is not the commit's, what pins the
    data the run read: the DVC hashes of the stage's inputs, or the lock's git
    object id, which `git log --all --find-object=<id>` finds once committed.
- **`environment`** — the binding between the run and `uv.lock`, not a package
  dump. `uv.lock` is a declarative resolution of your `pyproject.toml`: it says
  what a clean install *would* produce, so the stamp records what the run
  actually *had*:
  - **`uv_lock_sha256`** — the lock's hash, a cross-check on the commit.
  - **`lock_current`** — `uv lock --check`: the lock matches `pyproject.toml`.
  - **`env_synced`** / **`sync_selection`** — `uv sync --frozen --check` against
    the running interpreter: the venv matches the lock exactly for that extras
    selection (the default, then `--all-extras`). `null` means the check could
    not run.
  - **Package versions only where the lock can't supply them** — none when
    synced; otherwise **`sync_drift`** (uv's diff) and **`drift_installed`**
    (the installed versions of those packages); the full **`packages_observed`**
    when the check could not run or `uv.lock` is not committed unmodified.

  None of these sees a file edited inside `site-packages`. Stamps written
  before these checks carry `sync_mode` and a full `packages_observed` instead.
  The principle: the lockfile reproduces the *intended* environment; the stamp
  witnesses the *actual* one.
- **`invocation`** — the script, `config_name`, this job's own `overrides`
  (Hydra's per-job list, so correct under `--multirun`), and a single-job
  `command`. With the commit, enough to recompose the config without
  `.hydra/`. Hydra runs only.
- **`data`** — *opt-in*: `dvc status` at job start says whether the data on disk
  matches what git and DVC record (`in_sync`, plus `out_of_sync` entries). It
  hashes the tracked data, so it can be slow; turn it on for the runs that
  matter, such as the held-out eval, with
  `hydra.callbacks.run_stamp.check_data=true` (or `--check-data` on the CLI).
- **`completed`** — written only on success. Its *absence* means the run did not
  finish: "did this run complete?" is a file-existence check, with no status
  field to misread.
- **`created_at`** — UTC, ISO-8601, so two readers in different time zones agree
  on the instant.
- **`stats`** — whatever the step returns as headline metrics; the basis for any
  regenerated experiment ledger.

Neither this nor `uv.lock` captures CUDA / driver / BLAS versions, which is the
usual cause of small cross-machine metric drift. The environment claim is about
Python packages only; system libraries and hardware are out of scope for the
stamp, the lock, and the freeze alike. Do not oversell any of them; expect
metrics to match within ~1–2 decimal places, not bit-exactly.

### How the environment is checked

The sync verdicts are not taken from how the run was launched — that cannot be
observed from inside the process. The stamp asks uv, read-only and offline:

```bash
uv lock --check --offline                            # -> lock_current
UV_PROJECT_ENVIRONMENT=<running venv> \
  uv sync --frozen --check --offline [--all-extras]  # -> env_synced
```

Exit 0 is a pass, 1 is drift, anything else is `null`. Both take well under a
second.

## How it fires — without anyone remembering

### Hydra runs: a callback, registered once

Caster ships a Hydra callback and registers it in the base config:

```yaml
# configs/hydra/default.yaml
hydra:
  callbacks:
    run_stamp:
      _target_: caster.provenance.RunStampCallback
    split_properties:
      _target_: caster.splits.SplitPropertiesCallback
```

Hydra invokes `on_job_start` / `on_job_end` for every job, so the stamp is
written for **every** run, sweep included, with nothing added to your training
code and nothing to remember at the command line. The completion marker is
written in `on_job_end`, so a crashed run has no `_provenance.json` at all —
exactly the failure signal you want.

This is the answer to "does the user have to remember?": yes, exactly once, in
config, and never again.

### Split runs: a second record, automatically

A run that produces a data split carries a second provenance question the stamp
doesn't answer: *what is this split, structurally?* — because a split is
reproducible by regeneration, but rarely bit-identically (seeds don't guarantee
determinism, and a re-run can assign different rows while staying equivalent). So
a second registered callback, `SplitPropertiesCallback`, writes
`_split_properties.json` into the run directory whenever the run's config has a
`split:` block (a no-op otherwise — it costs nothing for non-split runs).

It records a **universal structural tier** (per-partition sizes, fractions, total
N, seed, and an order-invariant hash of each partition's index set) plus, *only
when the config declares the relevant column*, the two common task tiers:
`class_column` → per-partition class balance, `group_column` → cross-partition
group disjointness (a leakage check). Anything else is left to a free-form
`equivalence_notes` field it prompts for. The capture is best-effort: a missing
field or unreadable artifact yields a partial record and a warning, never a
failed run. This is how verification stays *tolerant* — a reader checks a
regenerated split for equivalence (matching sizes/balance), not bit-identity.

> **Naming convention.** A leading underscore marks a Caster-generated provenance
> record that lands in a run directory — `_provenance.json`,
> `_split_properties.json` — as distinct from user/dataset *content* like the
> Croissant files under `data_sources/`, which have no underscore. The packager
> collects the underscore files from run directories into a release bundle.

### Non-Hydra stages: append to the DVC command

A `dvc.yaml` stage that does not go through Hydra appends the stamp as a second
command:

```yaml
stages:
  preprocess:
    cmd:
      - python scripts/preprocess.py --out data/processed
      - caster provenance stamp data/processed
    deps: [scripts/preprocess.py, data/raw]
    outs: [data/processed]
```

DVC runs the commands in order and aborts the stage if the first fails, so the
stamp is reached only on success — and `dvc.lock` is rewritten only on success
too. The completion semantics are thus enforced twice over. (Split generation is
best done as a Hydra app so the split-properties callback fires; the example
`split.py` demonstrates this.)

## Reproducing from a stamp

```bash
# 1. Read what produced the artifact.
cat <run_dir>/_provenance.json          # commit, environment, invocation, stats
#   invocation.command re-runs that job; invocation.overrides + the commit
#   recompose its config, so .hydra/config.yaml need not have been kept.

# 2. Restore code state.
git checkout <commit-from-stamp>

# 3. Recreate the environment, with the stamp's sync_selection.
uv sync --frozen                         # e.g. --all-extras
#   env_synced false -> then apply sync_drift, at the drift_installed versions.
#   packages_observed present -> it is ground truth; reconcile against it.
#   Confirm the lock is the run's: sha256sum uv.lock = environment.uv_lock_sha256

# 4. Restore data to the run's version (with dvc_inputs / dvc_lock in the
#    stamp, the commit's dvc.lock is not the one the run read; see `git` above).
dvc checkout                             # or `dvc pull` from the remote

# 5. Re-run into a fresh output dir and compare.
python scripts/<step>.py
diff -r <run_dir>/ <run_dir>_verify/      # modulo timestamps in metadata
```

If the metrics agree within tolerance, the result reproduces. A `dirty: true`
stamp means step 2 cannot fully restore the code; document and re-establish.

## Optional: regenerating an experiment ledger

Teams that want a flat, human-readable `experiments.md` can regenerate one from
the stamps — each run dir contributes a row (timestamp, commit, stats, run dir):

```bash
caster provenance ledger              # to stdout
caster provenance ledger -o experiments.md
caster provenance ledger --json        # structured: one object per run
```

It scans `outputs/` for `_provenance.json` files and emits a Markdown table. The
stamps are the durable record; the ledger is a convenience view, regenerable any
time and never the source of truth. Stats vary per project, so they render as a
compact JSON blob in one column; a run with no commit (made before the first
commit, or outside git) renders a dash rather than failing. `dvc exp show` covers
the same need for DVC-experiment runs; this covers plain Hydra runs in
`outputs/`.

`--json` emits the same data as a machine-readable list (the standard
`{"ok", "command", "result", "problems"}` envelope, stdout only) — useful for
selecting a run programmatically, e.g. by best metric. Separately, every Hydra
run prints a final `CASTER_RUN_DIR=<path>` line on stdout so a script or agent
can capture the exact directory that run produced rather than inferring it from
timestamps; the same path is also stored in the stamp's `run_dir` field.
