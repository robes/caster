"""Evaluation entry point — a single Hydra app, two roles by config.

The scoring logic is identical regardless of which split it runs against; the
split is a config parameter (cfg.eval.split_name), not a code difference. The
two *roles* are distinguished by configuration and by pipeline membership:

  * VALIDATION (`+eval=validation`, split_name=val) — a DVC pipeline stage, run
    every `dvc repro`, used to compare configurations and select a model.
  * HELD-OUT TEST (`+eval=holdout`, split_name=test) — deliberately NOT in the
    pipeline. Run ONCE, at publication, to honestly report your final number.
    Running it during model selection leaks the test set; that it is out of
    `dvc repro` (a repeatedly-run command) is the safeguard.

Run:
    python scripts/evaluate.py +eval=validation run_dir=outputs/<run>/   # in-DAG role
    python scripts/evaluate.py +eval=holdout    run_dir=outputs/<run>/   # run once
"""

from __future__ import annotations

import json
from pathlib import Path

import hydra
from hydra.core.hydra_config import HydraConfig
from omegaconf import DictConfig

from myproject.datasets.synthetic import load_csv
from myproject.models.logreg import load_model
from myproject.training.loops import evaluate_model

CONFIG_PATH = str(Path(__file__).resolve().parent.parent / "configs")


@hydra.main(version_base=None, config_path=CONFIG_PATH, config_name="config")
def evaluate(cfg: DictConfig) -> dict:
    out_dir = Path(HydraConfig.get().runtime.output_dir)
    run_dir = Path(cfg.run_dir)

    model = load_model(run_dir / cfg.checkpoint)
    df = load_csv(Path(cfg.data.processed_dir) / "data.csv")
    split = load_csv(Path(cfg.split.output_dir) / cfg.split.filename)

    preds, metrics = evaluate_model(model, df, split, split_name=cfg.eval.split_name)
    preds.to_csv(out_dir / "predictions.csv", index=False)
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")

    # Optional stable metrics path, for use as a DVC stage `metrics:` output.
    metrics_out = cfg.eval.get("metrics_out") if hasattr(cfg.eval, "get") else None
    if metrics_out:
        mp = Path(metrics_out)
        mp.parent.mkdir(parents=True, exist_ok=True)
        mp.write_text(json.dumps(metrics, indent=2) + "\n")

    print(f"evaluate[{cfg.eval.split_name}]: {metrics}  -> {out_dir}")
    return metrics  # recorded in _provenance.json


if __name__ == "__main__":
    evaluate()
