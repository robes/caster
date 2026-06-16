"""Preprocess (raw -> processed) — a Hydra app, pipeline stage.

Consumes the raw data produced by the fetch stage and writes the processed
dataset. Purely a transformation: no data generation or acquisition lives here
(that's fetch.py). Being a Hydra app, the run is auto-stamped, so preprocessing
is reproducible like every other stage.

Run:
    python scripts/preprocess.py
"""

from __future__ import annotations

from pathlib import Path

import hydra
from omegaconf import DictConfig

from myproject.datasets.synthetic import load_csv, preprocess

CONFIG_PATH = str(Path(__file__).resolve().parent.parent / "configs")


@hydra.main(version_base=None, config_path=CONFIG_PATH, config_name="config")
def run_preprocess(cfg: DictConfig) -> dict:
    raw = load_csv(Path(cfg.data.raw_dir) / "data.csv")
    processed = preprocess(raw)

    out_dir = Path(cfg.data.processed_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    processed.to_csv(out_dir / "data.csv", index=False)

    print(f"preprocess: wrote {len(processed)} rows -> {out_dir}/data.csv")
    return {"n_processed": int(len(processed))}


if __name__ == "__main__":
    run_preprocess()
