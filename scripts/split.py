"""Split-generation entry point — a Hydra app, pipeline stage.

Producing splits through Hydra means the split-properties callback fires
automatically and writes `_split_properties.json` into the run directory
(alongside `_provenance.json`), capturing the split's structural properties and —
because the config declares `class_column` — its per-partition class balance.

The split CSV itself is written to the stable `split.output_dir` (a DVC stage
output that training configs reference), NOT into the timestamped Hydra run dir.
Determinism (seeded) is required so a regenerated split stays equivalent.

Run:
    python scripts/split.py
    python scripts/split.py split.seed=7
"""

from __future__ import annotations

from pathlib import Path

import hydra
from omegaconf import DictConfig

from myproject.datasets.synthetic import load_csv
from myproject.training.loops import make_split

CONFIG_PATH = str(Path(__file__).resolve().parent.parent / "configs")


@hydra.main(version_base=None, config_path=CONFIG_PATH, config_name="config")
def split(cfg: DictConfig) -> dict:
    df = load_csv(Path(cfg.data.processed_dir) / "data.csv")
    splits = make_split(df, cfg.split.test_frac, cfg.split.val_frac, seed=cfg.split.seed)

    out_dir = Path(cfg.split.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    splits.to_csv(out_dir / cfg.split.filename, index=False)

    counts = splits["split"].value_counts().to_dict()
    print(f"split: wrote {out_dir}/{cfg.split.filename}  counts={counts}")
    return {f"n_{k}": int(v) for k, v in counts.items()}


if __name__ == "__main__":
    split()
