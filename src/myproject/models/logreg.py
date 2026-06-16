"""Example model definition.

A thin wrapper around scikit-learn's LogisticRegression. The point of the
wrapper is the save/load contract: checkpoints are written in a portable format
(skops) so release packaging can copy them as opaque bytes without importing
this module. Swap in your own architecture; keep a portable save format.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression


def build_model(C: float = 1.0, max_iter: int = 200, seed: int = 0) -> LogisticRegression:
    """Construct an (untrained) model from hyperparameters."""
    return LogisticRegression(C=C, max_iter=max_iter, random_state=seed)


def save_model(model: LogisticRegression, path: str | Path) -> None:
    """Persist the model in a portable format.

    Prefers skops (safe, portable); falls back to joblib if skops isn't present.
    Either way the release packager treats the file as opaque bytes.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import skops.io as sio

        sio.dump(model, path)
    except ImportError:
        import joblib

        joblib.dump(model, path)


def load_model(path: str | Path) -> LogisticRegression:
    path = Path(path)
    try:
        import skops.io as sio

        # skops >=0.10 requires `trusted` to be a list of type names, not a bool.
        # For a model you produced yourself this is safe; review the list if the
        # checkpoint came from an untrusted source.
        untrusted = sio.get_untrusted_types(file=path)
        return sio.load(path, trusted=untrusted)
    except ImportError:
        import joblib

        return joblib.load(path)


def predict(model: LogisticRegression, X: np.ndarray) -> np.ndarray:
    return model.predict(X)
