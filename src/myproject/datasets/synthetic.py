"""Example dataset logic.

For the template this generates a small synthetic classification dataset so the
pipeline runs end to end with no external download. Replace with your real data
loading — the only contract the scripts rely on is the function signatures here.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.datasets import make_classification


def generate_raw(n_samples: int, n_features: int, seed: int) -> pd.DataFrame:
    """Produce a synthetic raw dataset as a DataFrame (stand-in for real source data)."""
    X, y = make_classification(
        n_samples=n_samples,
        n_features=n_features,
        n_informative=max(2, n_features // 2),
        random_state=seed,
    )
    cols = [f"f{i}" for i in range(n_features)]
    df = pd.DataFrame(X, columns=cols)
    df["target"] = y
    return df


def preprocess(df: pd.DataFrame) -> pd.DataFrame:
    """A trivial preprocessing step: standardize features (mean 0, unit variance).

    Real projects do their cleaning/feature-engineering here. Kept deterministic
    so a stage re-run reproduces byte-for-byte.
    """
    feature_cols = [c for c in df.columns if c != "target"]
    out = df.copy()
    out[feature_cols] = (df[feature_cols] - df[feature_cols].mean()) / df[feature_cols].std(ddof=0)
    return out


def load_csv(path: str | Path) -> pd.DataFrame:
    return pd.read_csv(path)


def features_and_target(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    feature_cols = [c for c in df.columns if c != "target"]
    return df[feature_cols].to_numpy(), df["target"].to_numpy()
