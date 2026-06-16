"""Data-source documentation: validate and emit Croissant records.

Caster keeps one Croissant record per external dataset under `data_sources/`.
This module is the small amount of code that convention needs — everything that
touches the Croissant *format* itself is delegated to the `mlcroissant` library.

  * `validate` — run `data_sources/` records through mlcroissant's validation,
    and check that every DVC-tracked dataset has a record (the coverage rule).
    Caster does not write its own Croissant validator; it calls mlcroissant.

  * `emit` — turn a lightweight YAML into a Croissant JSON-LD file by mapping a
    handful of familiar keys onto mlcroissant's builder API. It covers the
    common flat metadata a research dataset needs (name, description, license,
    citation, distribution, provenance prose). It deliberately does NOT author
    rich RecordSet/Field structure — for per-column types and nested relations,
    use the MLCommons Croissant editor and load emit's output as a starting
    point.

CLI:
    python -m caster.data validate [--data-dir data] [--sources-dir data_sources]
    python -m caster.data emit <name> [--sources-dir data_sources]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from caster._cli import emit_json, info

DATA_DIR = "data"
SOURCES_DIR = "data_sources"


def _require_mlcroissant():
    try:
        import mlcroissant as mlc
    except ImportError as exc:
        raise ImportError(
            "Croissant support requires mlcroissant: pip install mlcroissant"
        ) from exc
    return mlc


# ---------------------------------------------------------------------------
# emit: lightweight YAML -> Croissant JSON-LD (simple cases only)
# ---------------------------------------------------------------------------


def _guess_encoding_format(filename: str) -> str:
    """Best-effort MIME-ish encodingFormat from a filename extension.

    mlcroissant requires encodingFormat on every FileObject. The YAML can set it
    explicitly; when it doesn't, infer from the extension and fall back to a
    generic octet-stream. The author can always override in the YAML.
    """
    suffixes = Path(filename).suffixes
    ext = "".join(suffixes).lower()
    table = {
        ".zip": "application/zip",
        ".tar.gz": "application/gzip",
        ".tgz": "application/gzip",
        ".gz": "application/gzip",
        ".tar": "application/x-tar",
        ".csv": "text/csv",
        ".tsv": "text/tab-separated-values",
        ".json": "application/json",
        ".jsonl": "application/jsonlines",
        ".parquet": "application/vnd.apache.parquet",
        ".h5": "application/x-hdf5",
        ".hdf5": "application/x-hdf5",
        ".nii": "application/octet-stream",
    }
    for key, mime in table.items():
        if ext.endswith(key):
            return mime
    return "application/octet-stream"


def _build_metadata(spec: dict):
    """Map the lightweight YAML spec onto mlcroissant's Metadata builder.

    Only the flat descriptive layer is mapped. `provenance`/`fetch` prose and
    any other non-standard keys are preserved verbatim in `extra_properties` so
    nothing the author wrote is lost, even where it has no Croissant slot.
    """
    mlc = _require_mlcroissant()

    name = spec.get("name")
    if not name:
        raise ValueError("emit: the YAML must have a 'name'.")

    # distribution: a list of {name, sha256, content_size?} or a bare 'local'
    distribution = []
    dist = spec.get("distribution")
    if isinstance(dist, list):
        for d in dist:
            fname = d.get("name", name)
            enc = d.get("encoding_format") or _guess_encoding_format(fname)
            content_url = d.get("url", spec.get("url"))
            if not content_url:
                raise ValueError(
                    f"emit: distribution entry {fname!r} for dataset {name!r} "
                    f"needs a 'url' (a per-entry 'url:' or a top-level 'url:'). "
                    f"Croissant requires a contentUrl on every file. For data with "
                    f"no public URL, use 'distribution: local' instead of a list."
                )
            # YAML may parse an all-digit hash as an int; Croissant wants a str.
            sha = d.get("sha256")
            md5 = d.get("md5")
            sha_str = str(sha) if sha is not None else None
            if sha_str is not None and len(sha_str) not in (64,) and sha_str.strip("0") == "":
                print(
                    f"caster: warning: dataset {name!r} distribution "
                    f"{fname!r} has a placeholder-looking sha256 ({sha_str!r}); "
                    f"fill in the real digest.",
                    file=sys.stderr,
                )
            distribution.append(
                mlc.FileObject(
                    id=fname,
                    name=fname,
                    content_url=content_url,
                    sha256=sha_str,
                    md5=str(md5) if md5 is not None else None,
                    content_size=d.get("content_size"),
                    encoding_formats=[enc],  # mlcroissant requires encodingFormat
                )
            )
    # 'local' / None distribution -> no FileObject (bytes live only in DVC); the
    # provenance prose carries how it was built.

    citation = spec.get("citation") or {}
    cite_as = citation.get("bibtex") or citation.get("text")

    # Preserve prose and any unmapped keys so the record stays faithful.
    extra = {}
    for key in ("provenance", "fetch", "notes"):
        if spec.get(key):
            extra[key] = spec[key]

    kwargs = dict(
        name=name,
        description=spec.get("description", ""),
        url=spec.get("url"),
        license=spec.get("license"),
        cite_as=cite_as,
        date_published=spec.get("date_published"),
        version=str(spec["version"]) if spec.get("version") is not None else None,
        distribution=distribution or None,
    )
    if extra:
        kwargs["extra_properties"] = extra
    # Drop None values so mlcroissant uses its own defaults.
    kwargs = {k: v for k, v in kwargs.items() if v is not None}
    return mlc.Metadata(**kwargs)


def emit(name: str, sources_dir: str | Path = SOURCES_DIR) -> Path:
    """Read `<sources_dir>/<name>.yaml`, write `<sources_dir>/<name>.json`
    (Croissant JSON-LD), and validate the result. Returns the JSON path."""
    sources_dir = Path(sources_dir)
    yaml_path = sources_dir / f"{name}.yaml"
    if not yaml_path.exists():
        raise FileNotFoundError(f"emit: {yaml_path} not found.")
    spec = yaml.safe_load(yaml_path.read_text()) or {}

    metadata = _build_metadata(spec)
    json_path = sources_dir / f"{name}.json"

    import json as _json

    json_path.write_text(_json.dumps(metadata.to_json(), indent=2) + "\n")

    # Validate what we just wrote, so an emitted record is correct by
    # construction (mlcroissant does the checking).
    problems = validate_record(json_path)
    if problems:
        raise ValueError(
            f"emit: produced an invalid record for {name!r}:\n  " + "\n  ".join(problems)
        )
    return json_path


# ---------------------------------------------------------------------------
# validate: mlcroissant schema check + coverage check
# ---------------------------------------------------------------------------


def validate_record(json_path: str | Path) -> list[str]:
    """Validate one Croissant JSON-LD file with mlcroissant. Returns a list of
    problem strings (empty == valid)."""
    mlc = _require_mlcroissant()
    try:
        # Constructing a Dataset parses and validates the metadata.
        mlc.Dataset(jsonld=str(json_path))
        return []
    except Exception as exc:
        return [f"{Path(json_path).name}: {exc}"]


def _tracked_datasets(data_dir: Path) -> list[str]:
    """Source-dataset directories that are DVC-tracked and should have a record.

    The layout is stage-aligned: raw source datasets live under `data/raw/`
    (these are the ones that need a data_sources/ record), while `data/processed/`
    holds stage outputs (derivatives, not documented sources). So coverage scans
    `data/raw/`. A dataset is tracked by a `<name>.dvc` pointer beside it or an
    entry in dvc.lock. Single-dataset projects may track `data/raw/` directly.
    """
    raw_dir = data_dir / "raw"
    scan_dir = raw_dir if raw_dir.exists() else data_dir
    rel_prefix = f"{data_dir.name}/raw" if raw_dir.exists() else data_dir.name

    tracked: set[str] = set()
    # `<scan_dir>/<name>.dvc` pointers
    for dvc in scan_dir.glob("*.dvc"):
        tracked.add(dvc.stem)
    # directories recorded as outputs/deps in dvc.lock
    lock = Path("dvc.lock")
    lock_text = lock.read_text() if lock.exists() else ""
    for child in scan_dir.iterdir() if scan_dir.exists() else []:
        if child.is_dir() and f"{rel_prefix}/{child.name}" in lock_text:
            tracked.add(child.name)
    return sorted(tracked)


def validate(data_dir: str | Path = DATA_DIR, sources_dir: str | Path = SOURCES_DIR) -> list[str]:
    """Full check: every `data_sources/*.json` is valid Croissant, and every
    DVC-tracked dataset under data/ has a matching record. Returns problems."""
    data_dir = Path(data_dir)
    sources_dir = Path(sources_dir)
    problems: list[str] = []

    # 1. schema check every record
    records = sorted(sources_dir.glob("*.json")) if sources_dir.exists() else []
    documented = {p.stem for p in records}
    for rec in records:
        problems.extend(validate_record(rec))

    # 2. coverage: tracked dataset without a record
    for name in _tracked_datasets(data_dir):
        if name not in documented:
            problems.append(
                f"coverage: a DVC-tracked dataset {name!r} (under data/raw/) "
                f"has no data_sources/{name}.json record"
            )
    return problems


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Caster data-source tooling.")
    sub = parser.add_subparsers(dest="command", required=True)

    v = sub.add_parser("validate", help="validate records + coverage")
    v.add_argument("--data-dir", default=DATA_DIR)
    v.add_argument("--sources-dir", default=SOURCES_DIR)
    v.add_argument("--json", action="store_true", help="machine-readable output on stdout")

    e = sub.add_parser("emit", help="lightweight YAML -> Croissant JSON-LD")
    e.add_argument("name")
    e.add_argument("--sources-dir", default=SOURCES_DIR)
    e.add_argument("--json", action="store_true", help="machine-readable output on stdout")

    args = parser.parse_args(argv)
    if args.command == "emit":
        path = emit(args.name, sources_dir=args.sources_dir)
        if args.json:
            emit_json("emit", ok=True, result={"wrote": str(path)})
        else:
            print(f"wrote {path}")
        return 0

    problems = validate(data_dir=args.data_dir, sources_dir=args.sources_dir)
    if args.json:
        emit_json(
            "validate", ok=not problems, result={"n_problems": len(problems)}, problems=problems
        )
        return 1 if problems else 0
    if problems:
        for p in problems:
            info(f"FAIL  {p}")
        info(f"{len(problems)} problem(s)")
        return 1
    print("data_sources OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
