# Data sources

One Croissant record per external dataset. See
[`../docs/DATA_SOURCES.md`](../docs/DATA_SOURCES.md) for the three situations
(download / emit / author) and the convention.

- `<name>.yaml` — the lightweight authoring surface (Situations 2 and 3).
- `<name>.json` — Croissant JSON-LD: downloaded directly (Situation 1) or
  emitted from the YAML with `caster data emit <name>`.

Validate everything (and check that every DVC-tracked dataset under `data/raw/`
has a record):

```bash
caster data validate
```

The synthetic example dataset ships with a record here as a worked example.
