# Data sources

One Croissant record per external dataset. See
[`../docs/DATA_SOURCES.md`](../docs/DATA_SOURCES.md) for the two source types
(`reference`, `local`) and the convention.

- `<name>.yaml` — states the `type` (and, for a reference, its `url` and
  `format`); the lightweight authoring surface.
- `<name>.json` — Croissant JSON-LD: the upstream record for `format:
  croissant`, otherwise emitted from the YAML with `caster data emit <name>`.

Validate everything (and check that every DVC-tracked dataset under `data/raw/`
has a record):

```bash
caster data validate
```

The synthetic example dataset ships with a record here as a worked example.
