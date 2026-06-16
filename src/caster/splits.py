"""Split properties capture.

A split is reproducible-by-regeneration, but a regenerated split is rarely
*bit-identical* (seeds don't guarantee determinism across environments, and a
different RNG can assign different rows while preserving the split's meaning). So
the goal is not "verify identical bytes" but "verify equivalence" — and what
counts as equivalent is task-specific.

This module captures, into a run's directory, what is needed to check that:

  * A **universal structural tier**, computed for any split with no task
    knowledge: per-partition sizes, fractions, total N, the seed, and a hash of
    each partition's *sorted index set* (invariant to row order, exact on
    membership — so an identical assignment is detectable, but a reordered one
    isn't falsely flagged).

  * **Task tiers computed only when the user declares the relevant column**:
    `class_column` -> per-partition class balance; `group_column` ->
    cross-partition group disjointness (a leakage check). These are the two
    common notions of split equivalence; anything else (temporal order,
    multi-label co-occurrence) is left to the free-form `equivalence_notes`.

Everything is best-effort: missing config fields or an unreadable artifact
produce a warning and a partial record, never a failure that blocks the run.

The result is written as `_split_properties.json` into the Hydra run directory,
alongside `_provenance.json`. The leading underscore marks it as a
Caster-generated provenance record (as opposed to user/dataset content such as
the Croissant files under data_sources/). The packager collects it from the run
directory into the release bundle, the same way it collects `_provenance.json`.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

SPLIT_PROPERTIES_FILENAME = "_split_properties.json"


# ---------------------------------------------------------------------------
# capture
# ---------------------------------------------------------------------------


def _index_set_hash(values) -> str:
    """SHA-256 of the sorted, stringified index set — order-invariant, exact on
    membership."""
    joined = "\n".join(sorted(str(v) for v in values))
    return hashlib.sha256(joined.encode()).hexdigest()


def compute_properties(
    csv_path: Path,
    partition_column: str,
    index_column: str | None = None,
    class_column: str | None = None,
    group_column: str | None = None,
    seed: Any = None,
    data_csv: Path | None = None,
) -> dict:
    """Compute split properties from a CSV. Best-effort: tiers that can't be
    computed (missing column, etc.) are simply omitted with a note.

    A split CSV is typically just an index->partition map, so the class/group
    columns often live in the *data* rather than the split. If `data_csv` is
    given and `index_column` is present in both, the data is joined in on that
    index so class-balance / group-disjointness can still be computed.
    """
    import pandas as pd

    df = pd.read_csv(csv_path)

    # If declared task columns aren't in the split itself, try joining the data.
    needed = [c for c in (class_column, group_column) if c and c not in df.columns]
    if needed and data_csv is not None and index_column and Path(data_csv).exists():
        data = pd.read_csv(data_csv)
        join_cols = [c for c in needed if c in data.columns]
        if join_cols and index_column in df.columns:
            data = (
                data.reset_index().rename(columns={"index": index_column})
                if index_column not in data.columns
                else data
            )
            if index_column in data.columns:
                df = df.merge(data[[index_column, *join_cols]], on=index_column, how="left")

    props: dict = {
        "artifact": str(csv_path),
        "seed": seed,
        "n_total": int(len(df)),
        "partition_column": partition_column,
        "notes": [],
    }

    if partition_column not in df.columns:
        props["notes"].append(f"partition_column {partition_column!r} not found; only N recorded.")
        props["equivalence_notes"] = _EQUIV_PROMPT
        return props

    # --- universal structural tier ---
    idx = df[index_column] if (index_column and index_column in df.columns) else df.index
    partitions: dict = {}
    for name, group in df.groupby(partition_column):
        members = idx[group.index] if index_column and index_column in df.columns else group.index
        partitions[str(name)] = {
            "n": int(len(group)),
            "fraction": round(len(group) / len(df), 6),
            "index_sha256": _index_set_hash(members),
        }
    props["partitions"] = partitions

    # --- task tier: class balance (only if declared) ---
    if class_column:
        if class_column in df.columns:
            balance: dict = {}
            for name, group in df.groupby(partition_column):
                counts = group[class_column].value_counts().to_dict()
                balance[str(name)] = {str(k): int(v) for k, v in counts.items()}
            props["class_balance"] = {"column": class_column, "by_partition": balance}
        else:
            props["notes"].append(
                f"class_column {class_column!r} not found; class balance skipped."
            )

    # --- task tier: group disjointness (only if declared) ---
    if group_column:
        if group_column in df.columns:
            group_sets = {
                str(name): set(group[group_column]) for name, group in df.groupby(partition_column)
            }
            leaks = []
            names = list(group_sets)
            for i in range(len(names)):
                for j in range(i + 1, len(names)):
                    overlap = group_sets[names[i]] & group_sets[names[j]]
                    if overlap:
                        leaks.append(
                            {
                                "partitions": [names[i], names[j]],
                                "n_shared_groups": len(overlap),
                            }
                        )
            props["group_disjointness"] = {
                "column": group_column,
                "disjoint": not leaks,
                "leaks": leaks,
            }
        else:
            props["notes"].append(f"group_column {group_column!r} not found; disjointness skipped.")

    # Always prompt for the task-equivalence notion Caster can't infer.
    props["equivalence_notes"] = _EQUIV_PROMPT
    return props


_EQUIV_PROMPT = (
    "Structural properties above (sizes, fractions, index hashes) confirm "
    "structural equivalence only. If a regenerated split differs, describe here "
    "what makes it equivalent for THIS task (e.g. class balance preserved, "
    "groups disjoint, temporal order maintained). Declare class_column / "
    "group_column in the split config to have Caster compute those automatically."
)


def write_properties(run_dir: Path, props: dict) -> Path:
    path = Path(run_dir) / SPLIT_PROPERTIES_FILENAME
    path.write_text(json.dumps(props, indent=2, default=str) + "\n")
    return path


# ---------------------------------------------------------------------------
# Hydra callback
# ---------------------------------------------------------------------------


class SplitPropertiesCallback:
    """Hydra callback that captures split properties when a run produced a split.

    Registered by default alongside the run-stamp; it is a no-op for any run
    whose config has no `split:` block, so it costs nothing when not needed.

    Reads the produced CSV from `split.output_dir` / `split.filename`, computes
    properties (structural always; class/group tiers when those columns are
    declared in the split config), and writes `_split_properties.json` into the
    Hydra run directory. Entirely best-effort — any problem is a warning, never a
    failure.
    """

    def __init__(self):
        # Wall-clock at callback construction (≈ job start). Used to tell whether
        # *this* run actually produced the split, vs merely composing the split
        # config to read an existing split (as fetch/preprocess/train do).
        import time

        self._start = time.time()

    def on_job_end(self, config, job_return, **kwargs) -> None:
        try:
            split = self._get(config, "split")
            if not split:
                return  # no split config at all; no-op
            self._capture(config, split)
        except Exception as exc:  # never let capture fail the job
            print(f"caster: split properties not captured: {exc}", file=sys.stderr)

    def _capture(self, config, split) -> None:
        output_dir = self._get(split, "output_dir")
        if not output_dir:
            return  # can't locate an artifact; silently skip (not a split run)
        filename = self._get(split, "filename") or "splits.csv"
        csv_path = Path(output_dir) / filename
        if not csv_path.exists():
            return  # no artifact; this run didn't produce a split — silently skip
        # Only capture if THIS run produced (or refreshed) the split — i.e. the
        # artifact was written after the job started. Otherwise this is a
        # non-split stage that merely reads an existing split; stay quiet.
        try:
            if csv_path.stat().st_mtime < self._start:
                return
        except OSError:
            return

        props = compute_properties(
            csv_path,
            partition_column=self._get(split, "partition_column") or "split",
            index_column=self._get(split, "index_column"),
            class_column=self._get(split, "class_column"),
            group_column=self._get(split, "group_column"),
            seed=self._get(split, "seed"),
            data_csv=self._data_csv(config),
        )
        run_dir = self._resolve_output_dir(config)
        write_properties(run_dir, props)

    def _data_csv(self, config) -> Path | None:
        """Best-effort path to the data CSV, for joining in class/group columns
        that live in the data rather than the split."""
        data = self._get(config, "data")
        processed = self._get(data, "processed_dir") if data else None
        if processed:
            cand = Path(processed) / "data.csv"
            return cand if cand.exists() else None
        return None

    @staticmethod
    def _get(obj, key):
        """Read a key from an OmegaConf/dict/namespace, returning None if absent."""
        if obj is None:
            return None
        try:
            if hasattr(obj, "get"):
                return obj.get(key)
            return getattr(obj, key, None)
        except Exception:
            return None

    @staticmethod
    def _resolve_output_dir(config) -> Path:
        try:
            from hydra.core.hydra_config import HydraConfig

            return Path(HydraConfig.get().runtime.output_dir)
        except Exception:
            return Path.cwd()

    # Full Hydra callback protocol — no-ops except job end.
    def on_run_start(self, config, **kwargs) -> None: ...
    def on_run_end(self, config, **kwargs) -> None: ...
    def on_multirun_start(self, config, **kwargs) -> None: ...
    def on_multirun_end(self, config, **kwargs) -> None: ...
    def on_job_start(self, config, **kwargs) -> None: ...
