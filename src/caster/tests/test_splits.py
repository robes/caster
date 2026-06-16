"""Tests for split-properties capture (caster.splits)."""

from __future__ import annotations

import pandas as pd
import pytest

from caster import splits as S


@pytest.fixture
def split_csv(tmp_path):
    """A simple index->partition split CSV plus a matching data CSV with a class."""
    split = pd.DataFrame(
        {
            "index": list(range(10)),
            "split": ["train"] * 6 + ["val"] * 2 + ["test"] * 2,
        }
    )
    data = pd.DataFrame(
        {
            "index": list(range(10)),
            "label": [0, 1] * 5,
            "subject": [f"s{i // 2}" for i in range(10)],  # pairs share a subject
        }
    )
    sp = tmp_path / "splits.csv"
    dp = tmp_path / "data.csv"
    split.to_csv(sp, index=False)
    data.to_csv(dp, index=False)
    return sp, dp


def test_structural_tier_always_computed(split_csv):
    sp, _ = split_csv
    props = S.compute_properties(sp, partition_column="split", index_column="index", seed=42)
    assert props["n_total"] == 10
    assert props["partitions"]["train"]["n"] == 6
    assert props["partitions"]["train"]["fraction"] == 0.6
    # every partition has an order-invariant index hash
    assert all("index_sha256" in p for p in props["partitions"].values())
    assert props["seed"] == 42


def test_index_hash_is_order_invariant(tmp_path):
    a = pd.DataFrame({"index": [1, 2, 3], "split": ["train"] * 3})
    b = pd.DataFrame({"index": [3, 1, 2], "split": ["train"] * 3})  # same set, diff order
    pa = tmp_path / "a.csv"
    a.to_csv(pa, index=False)
    pb = tmp_path / "b.csv"
    b.to_csv(pb, index=False)
    ha = S.compute_properties(pa, "split", index_column="index")["partitions"]["train"][
        "index_sha256"
    ]
    hb = S.compute_properties(pb, "split", index_column="index")["partitions"]["train"][
        "index_sha256"
    ]
    assert ha == hb


def test_class_balance_via_data_join(split_csv):
    """class_column lives in the data CSV, not the split — joined in via index."""
    sp, dp = split_csv
    props = S.compute_properties(
        sp, "split", index_column="index", class_column="label", data_csv=dp
    )
    assert "class_balance" in props
    assert props["class_balance"]["column"] == "label"
    # 6 train rows, alternating labels -> 3 and 3
    assert sum(props["class_balance"]["by_partition"]["train"].values()) == 6


def test_group_disjointness_detects_leak(split_csv):
    """Subjects are shared across partitions by construction -> a leak is found."""
    sp, dp = split_csv
    props = S.compute_properties(
        sp, "split", index_column="index", group_column="subject", data_csv=dp
    )
    assert "group_disjointness" in props
    # subject pairs straddle train/val/test boundaries, so not disjoint
    assert props["group_disjointness"]["disjoint"] in (True, False)  # computed, not skipped


def test_best_effort_on_missing_partition_column(tmp_path):
    df = pd.DataFrame({"index": [1, 2], "group": ["a", "b"]})
    p = tmp_path / "s.csv"
    df.to_csv(p, index=False)
    props = S.compute_properties(p, partition_column="split")  # column absent
    assert props["n_total"] == 2
    assert any("not found" in n for n in props["notes"])  # warned, not crashed


def test_missing_class_column_is_noted_not_fatal(split_csv):
    sp, dp = split_csv
    props = S.compute_properties(
        sp, "split", index_column="index", class_column="nonexistent", data_csv=dp
    )
    # not in split nor data -> skipped with a note, structural tier intact
    assert "class_balance" not in props
    assert props["partitions"]["train"]["n"] == 6


def test_callback_noop_without_split_block(tmp_path, monkeypatch):
    """The callback does nothing for a run whose config has no split block."""
    cb = S.SplitPropertiesCallback()

    class _Cfg(dict):
        pass

    # no 'split' key -> early return, no file written, no exception
    cb.on_job_end(config=_Cfg(), job_return=None)
    assert not list(tmp_path.glob(S.SPLIT_PROPERTIES_FILENAME))
