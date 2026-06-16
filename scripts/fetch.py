"""Fetch (acquire raw data) — a Hydra app, first stage of the pipeline.

This stage *produces the raw data*: the pipeline's input, documented in
data_sources/ and not regenerable in general. In this template the example
"fetches" by generating a synthetic dataset, because the template must run with
no external download. Your real fetch.py does the actual acquisition — download
and unzip an archive, materialize a BagIt/BDBag, pull from an instrument or an
institutional store — following the `fetch:` instructions in the dataset's
Croissant record (here, data_sources/synthetic.yaml documents that this example
generates rather than downloads).

Being a Hydra app, the run is auto-stamped (commit, environment) like every
other stage, so even raw-data acquisition is reproducible.

Run:
    python scripts/fetch.py
    python scripts/fetch.py fetch.seed=7
"""

from __future__ import annotations

from pathlib import Path

import hydra
from omegaconf import DictConfig

from myproject.datasets.synthetic import generate_raw

CONFIG_PATH = str(Path(__file__).resolve().parent.parent / "configs")


@hydra.main(version_base=None, config_path=CONFIG_PATH, config_name="config")
def fetch(cfg: DictConfig) -> dict:
    raw_dir = Path(cfg.data.raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)

    # --- example only: generate synthetic raw data ---
    # A real fetch.py would download/materialize here instead, per the dataset's
    # Croissant `fetch:` instructions. The output contract is the same: raw bytes
    # land under cfg.data.raw_dir for the preprocess stage to consume.
    raw = generate_raw(cfg.fetch.n_samples, cfg.fetch.n_features, seed=cfg.fetch.seed)
    raw.to_csv(raw_dir / "data.csv", index=False)

    print(f"fetch: produced {len(raw)} raw rows -> {raw_dir}/data.csv")
    return {"n_raw": int(len(raw))}


if __name__ == "__main__":
    fetch()
