"""Example training and evaluation logic.

Kept framework-light and deterministic. The scripts in `scripts/` call these;
the reproducibility machinery (run-stamp, configs) wraps around them.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, f1_score

from myproject.datasets.synthetic import features_and_target
from myproject.models.logreg import build_model, predict


def make_split(df: pd.DataFrame, test_frac: float, val_frac: float, seed: int) -> pd.DataFrame:
    """Assign each row to train/val/test deterministically and return a split table.

    Returns a DataFrame with columns [index, split]; deterministic given seed so
    every regeneration is byte-identical.
    """
    rng = np.random.default_rng(seed)
    n = len(df)
    perm = rng.permutation(n)
    n_test = int(round(n * test_frac))
    n_val = int(round(n * val_frac))
    split = np.empty(n, dtype=object)
    split[perm[:n_test]] = "test"
    split[perm[n_test : n_test + n_val]] = "val"
    split[perm[n_test + n_val :]] = "train"
    return pd.DataFrame({"index": np.arange(n), "split": split})


def train_model(
    df: pd.DataFrame, split: pd.DataFrame, hparams: dict, seed: int
) -> tuple[object, dict]:
    """Fit a model on the train rows; return (model, val_stats)."""
    train_idx = split.loc[split["split"] == "train", "index"].to_numpy()
    val_idx = split.loc[split["split"] == "val", "index"].to_numpy()
    X, y = features_and_target(df)

    model = build_model(seed=seed, **hparams)
    model.fit(X[train_idx], y[train_idx])

    val_pred = predict(model, X[val_idx])
    stats = {
        "val_accuracy": float(accuracy_score(y[val_idx], val_pred)),
        "val_f1": float(f1_score(y[val_idx], val_pred, average="macro")),
        "n_train": int(len(train_idx)),
        "n_val": int(len(val_idx)),
    }
    return model, stats


def evaluate_model(
    model: object, df: pd.DataFrame, split: pd.DataFrame, split_name: str = "test"
) -> tuple[pd.DataFrame, dict]:
    """Score the model on the named split; return (predictions_df, metrics)."""
    idx = split.loc[split["split"] == split_name, "index"].to_numpy()
    X, y = features_and_target(df)
    pred = predict(model, X[idx])
    preds_df = pd.DataFrame({"index": idx, "y_true": y[idx], "y_pred": pred})
    metrics = {
        "accuracy": float(accuracy_score(y[idx], pred)),
        "f1": float(f1_score(y[idx], pred, average="macro")),
        "n": int(len(idx)),
    }
    return preds_df, metrics
