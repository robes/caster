"""Training entry point — a Hydra app and a DVC pipeline stage.

Auto-stamped by the run-stamp callback (commit, environment, completion, and the
returned validation stats). Trains on the train split, reports validation
metrics in the stamp.

Writes the checkpoint to TWO places, on purpose:
  * cfg.model.output_dir  — a stable path DVC tracks as the train stage's `outs`,
    so `dvc repro` produces a canonical pipeline model. This is what releases
    package by default.
  * the Hydra run dir       — for ad-hoc experiments (`train.py +experiment=...`)
    that aren't the pipeline's canonical model; the run dir also holds the stamp.

Run:
    dvc repro                                # produces the canonical model
    python scripts/train.py +experiment=baseline   # an ad-hoc experiment run
"""

from __future__ import annotations

from pathlib import Path

import hydra
from hydra.core.hydra_config import HydraConfig
from omegaconf import DictConfig

from myproject.datasets.synthetic import load_csv
from myproject.models.logreg import save_model
from myproject.training.loops import train_model

CONFIG_PATH = str(Path(__file__).resolve().parent.parent / "configs")


@hydra.main(version_base=None, config_path=CONFIG_PATH, config_name="config")
def train(cfg: DictConfig) -> dict:
    run_dir = Path(HydraConfig.get().runtime.output_dir)

    df = load_csv(Path(cfg.data.processed_dir) / "data.csv")
    split = load_csv(Path(cfg.split.output_dir) / cfg.split.filename)

    hparams = {"C": cfg.model.C, "max_iter": cfg.model.max_iter}
    model, stats = train_model(df, split, hparams, seed=cfg.seed)

    ckpt_name = cfg.model.checkpoint
    save_model(model, run_dir / ckpt_name)  # provenance copy
    stable = Path(cfg.model.output_dir)
    stable.mkdir(parents=True, exist_ok=True)
    save_model(model, stable / ckpt_name)  # canonical pipeline output

    print(f"train: {stats}  -> {stable / ckpt_name} (+ run dir)")
    return stats  # recorded in _provenance.json by the run-stamp callback


if __name__ == "__main__":
    train()
