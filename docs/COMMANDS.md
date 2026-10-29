# Command reference

The unified `caster` command dispatches to the project's tooling. Run any group
with `-h` for its options (e.g. `caster data -h`); each group is also runnable as
`python -m caster.<module>`. Commands run inside the project environment
(`uv run caster …`, or an activated venv).

| Command | Description |
|---|---|
| `caster provenance stamp <dir> [--stats k=v ...] [--check-data]` | Write a `_provenance.json` run-stamp (commit, environment, stats) into a directory — for a non-Hydra step. `--check-data` adds whether the data matches DVC (`dvc status`; slow). |
| `caster provenance ledger [-o FILE] [--json]` | Regenerate a flat experiment ledger from the run-stamps under `outputs/`. |
| `caster data validate [--json]` | Check that every `data_sources/` record is valid Croissant and every DVC-tracked dataset has one. |
| `caster data emit <name> [--json]` | Convert a lightweight `data_sources/<name>.yaml` into a full Croissant JSON-LD record. |
| `caster package <release_config.yaml> [--dry-run] [--card] [--json]` | Build the archival release bundle (RO-Crate + BagIt) described by the config; `--card` prints its model card instead. |

Common flags: `--json` (machine-readable output on stdout only; on `validate`,
`ledger`, and `package`) and `--dry-run` (on `package`: report what would be
built and any warnings, write nothing).
