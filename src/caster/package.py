"""Archival release packaging.

The `rocrate` and `bagit` libraries do the bundle mechanics. This module is the
connective code they cannot supply: pinning the source distribution to the
commit recorded in the run's stamp, knowing which directories form this
project's provenance chain, and mapping each dataset's disposition, checked
against its `data_sources/` record, onto the libraries' options.

Layout produced (a BagIt bag whose payload is an RO-Crate):

    <release>/                       bag root
      bagit.txt, bag-info.txt, manifest-sha256.txt   (bagit lib)
      data/                          payload == RO-Crate root
        ro-crate-metadata.json       (rocrate lib)
        model/                       checkpoint bytes + .sha256
        provenance/{config,run,data_sources,eval}/
        data_manifest.sha256         portable data hashes (no DVC remote dep)
        splits/                      split CSVs (bytes by default)
        datasets/<name>/             only datasets explicitly embedded
        source/<project>-<tag>.tar.gz   git archive at the run commit

Disposition (per dataset, from the release config; the default follows the
record's source type):
    reference -> an RO-Crate entity whose @id is the record's url (DOI); no bytes.
                 Default for a `reference` source; an error for a `local` one.
    embed     -> bytes pulled at the run commit (dvc get --rev) into the payload.
    omit      -> no bytes and no pointer; the record and the data_manifest hash
                 travel. Default for a `local` source; a warning for a reference.

CLI:
    python -m caster.package <release_config.yaml>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

from caster._cli import emit_json, info
from caster.provenance import STAMP_FILENAME, write_stamp

_CHUNK = 1 << 20
_VALID_DISPOSITIONS = ("reference", "embed", "omit")
_DEFAULT_DISPOSITION = {"reference": "reference", "local": "omit"}
_CARD_REQUIRED = (
    "Model details",
    "Intended use",
    "Training data",
    "Evaluation",
    "Limitations, risks, and ethical considerations",
)
_DOI = re.compile(r"10\.\d{4,9}/[^\s,}\"']+")


class DatasetError(ValueError):
    """The release's datasets don't line up with their data-source records."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _run(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    cp = subprocess.run(args, capture_output=True, text=True)
    if check and cp.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(args)}\n{cp.stderr.strip()}")
    return cp


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _require_libs():
    try:
        import bagit
        from rocrate.rocrate import ROCrate
    except ImportError as exc:
        raise ImportError(
            "Packaging requires the 'rocrate' and 'bagit' libraries: pip install rocrate bagit"
        ) from exc
    return bagit, ROCrate


# ---------------------------------------------------------------------------
# the one piece no library knows: the run's commit, from its stamp
# ---------------------------------------------------------------------------


def _run_commit(run_dir: Path) -> str | None:
    """The commit that produced this run, read from its _provenance.json stamp."""
    stamp = run_dir / STAMP_FILENAME
    if stamp.exists():
        return (json.loads(stamp.read_text()).get("git") or {}).get("commit")
    return None


def _extract_data_manifest(repo_root: Path) -> str:
    """Build a portable `sha256`-style data manifest from the project's integrity
    record, with no remote dependency.

    Reads md5/sha hashes out of DVC pointer files (`*.dvc`, `dvc.lock`) and/or a
    flat `data/**/manifest.sha256`, and emits `<hash>  <path>` lines. This is the
    data *identity*, extracted for a reader who lacks the DVC remote — the
    pointers themselves (which only address that remote) are not shipped.
    """
    lines: list[str] = []
    # 1. DVC pointer files: pull out the recorded md5/hash + path.
    for dvc in sorted(repo_root.rglob("*.dvc")):
        try:
            doc = yaml.safe_load(dvc.read_text()) or {}
        except Exception:
            continue
        for out in doc.get("outs", []):
            h = out.get("md5") or out.get("sha256") or out.get("hash")
            p = out.get("path")
            if h and p:
                lines.append(f"{h}  {p}")
    # 2. dvc.lock stage outputs.
    lock = repo_root / "dvc.lock"
    if lock.exists():
        try:
            doc = yaml.safe_load(lock.read_text()) or {}
            for stage in (doc.get("stages") or {}).values():
                for out in stage.get("outs", []):
                    h = out.get("md5") or out.get("hash")
                    p = out.get("path")
                    if h and p:
                        lines.append(f"{h}  {p}")
        except Exception:
            pass
    # 3. Any flat sha256 manifests the project keeps for untracked data.
    for man in sorted(repo_root.rglob("manifest.sha256")):
        try:
            lines.extend(ln for ln in man.read_text().splitlines() if ln.strip())
        except Exception:
            pass
    # Deduplicate, stable order.
    seen, out_lines = set(), []
    for ln in lines:
        if ln not in seen:
            seen.add(ln)
            out_lines.append(ln)
    return ("\n".join(out_lines) + "\n") if out_lines else ""


def _gather_card_facts(cfg: dict, run_dir: Path, commit: str | None, datasets: list[dict]) -> dict:
    """Collect the auto-fillable *facts* for the model card from provenance the
    packager already has: the release config, the run-stamp (metrics, commit),
    the held-out eval, and the resolved datasets. Human-judgment sections are NOT
    gathered here — they come from the project's MODEL_CARD.md prose."""
    from datetime import datetime, timezone

    # metrics: prefer the held-out eval result, else the train run's stamp stats.
    metrics: dict = {}
    eval_name = None
    eval_dir = cfg.get("eval_dir")
    if eval_dir:
        eval_dir = Path(eval_dir)
        eval_name = eval_dir.name
        for cand in sorted(eval_dir.glob("*.json")):
            # skip Caster's own provenance/bookkeeping files — not metrics
            if cand.name.startswith("_"):
                continue
            try:
                d = json.loads(cand.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(d, dict):
                numeric = {k: v for k, v in d.items() if isinstance(v, (int, float))}
                if numeric:  # a metrics file looks like flat numbers
                    metrics = numeric
                    break
    if not metrics:
        stamp = run_dir / STAMP_FILENAME
        if stamp.exists():
            try:
                stats = json.loads(stamp.read_text()).get("stats") or {}
                metrics = {k: v for k, v in stats.items() if isinstance(v, (int, float))}
            except (OSError, json.JSONDecodeError):
                metrics = {}

    return {
        "project_name": cfg.get("project_name", "model"),
        "version": cfg.get("tag", ""),
        "model_id": cfg.get("model_id", "main"),
        "organization": cfg.get("organization"),
        "contact_email": cfg.get("contact_email"),
        "license": cfg.get("license"),
        "description": cfg.get("description", ""),
        "commit": commit,
        "date_packaged": datetime.now(timezone.utc).date().isoformat(),
        "datasets": datasets,
        "metrics": metrics,
        "eval_name": eval_name,
    }


def _model_card_path(repo_root: Path, model_id: str) -> Path | None:
    """The project's card for this model: `MODEL_CARD.<model_id>.md`, else the
    single `MODEL_CARD.md`."""
    for name in (f"MODEL_CARD.{model_id}.md", "MODEL_CARD.md"):
        if (repo_root / name).is_file():
            return repo_root / name
    return None


def _card_sections(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Split Markdown into (text before the first `## ` heading, [(heading,
    body)]). Headings inside fenced code blocks don't count."""
    preamble: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    fenced = False
    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        if not fenced and line.startswith("## "):
            sections.append((line[3:].strip(), []))
        else:
            (sections[-1][1] if sections else preamble).append(line)
    return "\n".join(preamble).strip(), [(h, "\n".join(b).strip()) for h, b in sections]


def _heading_key(heading: str) -> str:
    return " ".join(heading.lower().split())


def _assemble_card(facts: dict, prose: str, label: str) -> tuple[str, list[str]]:
    """Build the packaged card: Caster's frame (model_card.md.j2) supplies the
    title, the section order and each section's facts; the project's plain
    Markdown card supplies what follows them under the same `##` heading.

    A project section the frame doesn't have stays after the section it follows
    in the project's card. HTML comments (the prompts) are dropped, and the
    project's own `# ` title gives way to the frame's. Returns (card, warnings):
    a frame section the project left missing or empty is incomplete."""
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    env = Environment(
        loader=FileSystemLoader(str(Path(__file__).parent)),
        autoescape=select_autoescape(enabled_extensions=()),  # Markdown, not HTML
        trim_blocks=True,
        lstrip_blocks=True,
    )
    title, frame = _card_sections(env.get_template("model_card.md.j2").render(**facts))

    prose = re.sub(r"<!--.*?-->", "", prose, flags=re.S)
    preamble, own = _card_sections(prose)
    preamble = "\n".join(ln for ln in preamble.splitlines() if not ln.startswith("# ")).strip()
    known = {_heading_key(h) for h, _ in frame}
    bodies: dict[str, list[str]] = {}
    extras: dict[str | None, list[str]] = {}
    after = None
    for heading, body in own:
        key = _heading_key(heading)
        if key in known:
            bodies.setdefault(key, []).append(body)
            after = key
        else:
            extras.setdefault(after, []).append(f"## {heading}\n\n{body}")

    parts = [title, preamble, *extras.get(None, [])]
    for heading, generated in frame:
        key = _heading_key(heading)
        parts += [f"## {heading}", generated, *bodies.get(key, []), *extras.get(key, [])]
    card = re.sub(r"\n{3,}", "\n\n", "\n\n".join(p for p in parts if p)).strip() + "\n"

    warnings = []
    for heading in _CARD_REQUIRED:
        key = _heading_key(heading)
        if key not in bodies:
            warnings.append(f"{label}: no '## {heading}' section; the card is incomplete")
        elif not any(bodies[key]):
            warnings.append(f"{label}: '## {heading}' is still empty; the card is incomplete")
    if "{{" in prose or "{%" in prose:
        warnings.append(
            f"{label} contains Jinja markers; the card is plain Markdown and they are "
            f"shown as written (the facts come from caster)"
        )
    return card, warnings


def _git_archive(ref: str, prefix: str, dest: Path) -> str:
    """git archive the repo at `ref` to `dest`; fall back to HEAD. Return ref used."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    probe = _run("git", "rev-parse", "--verify", "--quiet", ref, check=False)
    used = ref if probe.returncode == 0 else "HEAD"
    if used != ref:
        print(f"caster: ref {ref!r} not resolvable; archiving HEAD", file=sys.stderr)
    _run("git", "archive", f"--prefix={prefix}/", "--format=tar.gz", "-o", str(dest), used)
    return used


# ---------------------------------------------------------------------------
# provenance chain assembly (project-layout knowledge)
# ---------------------------------------------------------------------------


def _copy_into(src: Path, dest: Path) -> bool:
    if src.is_dir():
        shutil.copytree(src, dest, dirs_exist_ok=True)
        return True
    if src.is_file():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        return True
    return False


def _stage_payload(
    cfg: dict,
    repo_root: Path,
    run_dir: Path,
    commit: str | None,
    payload: Path,
    datasets: list[dict],
    card: str | None,
) -> dict:
    """Lay out the RO-Crate payload directory. Returns a summary dict."""
    summary: dict = {}

    # checkpoint as opaque bytes + sha256 sidecar.
    # Bytes come from `checkpoint_path` if set (the stable pipeline model
    # produced by `dvc repro`), else from run_dir/checkpoint. Provenance
    # (config, stamp, commit) always comes from run_dir — so a release can pair
    # the canonical pipeline model with the train run that produced it.
    ckpt_src = (
        Path(cfg["checkpoint_path"]) if cfg.get("checkpoint_path") else run_dir / cfg["checkpoint"]
    )
    if not ckpt_src.exists():
        raise FileNotFoundError(f"checkpoint not found: {ckpt_src}")
    model_dir = payload / "model"
    model_dir.mkdir(parents=True, exist_ok=True)
    ckpt_dest = model_dir / ("model" + ckpt_src.suffix)
    shutil.copy2(ckpt_src, ckpt_dest)
    digest = _sha256(ckpt_dest)
    ckpt_dest.with_name(ckpt_dest.name + ".sha256").write_text(f"{digest}  {ckpt_dest.name}\n")
    summary["checkpoint_sha256"] = digest

    prov = payload / "provenance"
    # config: Hydra composed config + overrides
    if _copy_into(run_dir / ".hydra", prov / "config"):
        summary["config"] = True
    # run: the stamp itself, plus split properties if this was a split run
    _copy_into(run_dir / STAMP_FILENAME, prov / "run" / STAMP_FILENAME)
    from caster.splits import SPLIT_PROPERTIES_FILENAME

    if _copy_into(run_dir / SPLIT_PROPERTIES_FILENAME, prov / "run" / SPLIT_PROPERTIES_FILENAME):
        summary["split_properties"] = True
    # Portable data integrity manifest, NOT the DVC pointers. The bundle is for
    # a reader without our DVC remote, so we extract the *hashes* (data identity)
    # from whatever integrity record the project uses and write them to a plain,
    # remote-independent file. The pointer-as-address is deliberately dropped.
    manifest = _extract_data_manifest(repo_root)
    if manifest:
        (payload / "data_manifest.sha256").write_text(manifest)
        summary["data_manifest_entries"] = manifest.count("\n")
    # data_sources: the records of the datasets this release lists
    for d in datasets:
        for f in (d["record"], d["record"].with_suffix(".json")):
            _copy_into(f, prov / "data_sources" / f.name)
    # eval artifact (whole)
    if "eval_dir" in cfg and _copy_into(Path(cfg["eval_dir"]), prov / "eval"):
        summary["eval"] = Path(cfg["eval_dir"]).name

    # Model card, assembled in package(); omitted if the project has none.
    if card is not None:
        (payload / "MODEL_CARD.md").write_text(card)

    # source distribution pinned to the run commit
    project = cfg.get("project_name") or repo_root.name
    tag = cfg.get("tag", "untagged")
    ref_used = _git_archive(
        commit or "HEAD", f"{project}-{tag}", payload / "source" / f"{project}-{tag}.tar.gz"
    )
    summary["source_ref"] = ref_used
    return summary


def _citation_doi(spec: dict, json_path: Path) -> str | None:
    """The DOI to cite: the YAML's `citation.doi`, else the first DOI in the
    Croissant record's `citeAs`."""
    doi = (spec.get("citation") or {}).get("doi")
    if doi:
        return str(doi)
    try:
        cite = json.loads(json_path.read_text()).get("citeAs") or ""
    except (OSError, json.JSONDecodeError, AttributeError):
        return None
    m = _DOI.search(str(cite))
    return m.group(0) if m else None


def _resolve_datasets(cfg: dict, repo_root: Path) -> tuple[list[dict], list[str]]:
    """Check each listed dataset's disposition against its data-source record.

    Returns (datasets, warnings); raises DatasetError listing every problem.
    Runs before a dry run returns, so a dry run refuses what a real run would.
    """
    from caster.data import check_spec

    sources = repo_root / "data_sources"
    datasets: list[dict] = []
    problems: list[str] = []
    warnings: list[str] = []
    for entry in cfg.get("datasets", []):
        name = entry.get("name")
        record = sources / f"{name}.yaml"
        if not record.exists():
            problems.append(f"dataset {name!r}: no data_sources/{name}.yaml record")
            continue
        spec = yaml.safe_load(record.read_text()) or {}
        spec_problems = check_spec(spec, f"data_sources/{name}.yaml")
        if spec_problems:
            problems += spec_problems
            continue
        kind = spec["type"]
        disp = entry.get("disposition", _DEFAULT_DISPOSITION[kind])
        if disp == "fetch":
            problems.append(f"dataset {name!r}: 'fetch' was replaced by 'reference'")
            continue
        if disp not in _VALID_DISPOSITIONS:
            problems.append(
                f"dataset {name!r}: bad disposition {disp!r} "
                f"(use one of {', '.join(_VALID_DISPOSITIONS)})"
            )
            continue
        if kind == "local" and disp == "reference":
            problems.append(
                f"dataset {name!r}: a local source has nothing to reference; use embed or omit"
            )
            continue
        if kind == "reference" and disp == "omit":
            warnings.append(
                f"dataset {name!r} is published at {spec['url']}; "
                f"'reference' would name it in the bundle, 'omit' does not."
            )
        datasets.append(
            {
                "name": name,
                "type": kind,
                "disposition": disp,
                "identifier": spec.get("url") if kind == "reference" else None,
                "format": spec.get("format"),
                "citation_doi": _citation_doi(spec, record.with_suffix(".json")),
                "path": entry.get("path", f"data/{name}"),
                "record": record,
            }
        )
    if problems:
        raise DatasetError(problems)
    return datasets, warnings


def _stage_datasets(datasets: list[dict], repo_root: Path, commit: str, payload: Path) -> list[str]:
    """Pull the bytes of embedded datasets into the payload. Returns their names."""
    embedded: list[str] = []
    for d in datasets:
        if d["disposition"] == "embed":
            name, path = d["name"], d["path"]
            dest = payload / "datasets" / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            cp = _run("dvc", "get", ".", path, "--rev", commit, "-o", str(dest), check=False)
            if cp.returncode != 0:
                print(
                    f"caster: dvc get failed for {path!r}; copying workspace bytes (unversioned)",
                    file=sys.stderr,
                )
                _copy_into(repo_root / path, dest)
            embedded.append(name)
    return embedded


# ---------------------------------------------------------------------------
# RO-Crate metadata (rocrate lib) + BagIt wrap (bagit lib)
# ---------------------------------------------------------------------------


def _write_crate_metadata(ROCrate, payload: Path, cfg: dict, datasets: list[dict]) -> None:
    """Register payload contents as RO-Crate entities and write the metadata.

    Each dataset with a published identifier (reference, or a reference source
    that is embedded) becomes a contextual entity keyed by that identifier and
    linked from the root as `isBasedOn`, so the dependency is machine-readable.
    """
    from rocrate.model.contextentity import ContextEntity

    crate = ROCrate()
    crate.name = f"{cfg.get('project_name', payload.name)} {cfg.get('tag', '')}".strip()
    crate.description = cfg.get("description", "Caster archival release")
    # register the main directories as Dataset entities and the model as a File
    for sub in ("model", "provenance", "source", "datasets"):
        d = payload / sub
        if d.is_dir():
            crate.add_directory(str(d), dest_path=sub)
    based_on = []
    for d in datasets:
        if d["identifier"]:
            props = {"@type": "Dataset", "name": d["name"]}
            if d["citation_doi"]:
                props["citation"] = f"https://doi.org/{d['citation_doi']}"
            based_on.append(crate.add(ContextEntity(crate, d["identifier"], properties=props)))
    if based_on:
        crate.root_dataset["isBasedOn"] = based_on
    # the metadata file lands at payload/ro-crate-metadata.json
    crate.write(str(payload))


def package(
    cfg: dict,
    repo_root: Path | None = None,
    *,
    dry_run: bool = False,
    as_json: bool = False,
    card_only: bool = False,
) -> dict:
    """Build the bag. Returns a stats dict for the packaging step's own stamp.

    `card_only` returns the model card the bundle would carry, and its warnings,
    instead; like `dry_run` it writes nothing."""
    bagit, ROCrate = _require_libs()
    repo_root = repo_root or Path(_run("git", "rev-parse", "--show-toplevel").stdout.strip())

    org = cfg.get("organization")
    email = cfg.get("contact_email")
    if not org or not email:
        raise ValueError("release config requires 'organization' and 'contact_email'")

    if not cfg.get("run_dir"):
        raise ValueError(
            "release config requires 'run_dir' (the run whose "
            "provenance to package — its .hydra config and stamp)"
        )
    run_dir = Path(cfg["run_dir"])
    if not run_dir.exists():
        raise FileNotFoundError(
            f"run_dir does not exist: {run_dir}. Set 'run_dir' in the release "
            f"config to a real train run directory (e.g. outputs/<date>/<time>/) "
            f"before packaging — the placeholder path is not a real run."
        )
    # A bundle's purpose is to carry the provenance chain. Missing pieces are not
    # silently-skippable: collect a warning for each, so a provenance-stripped
    # bundle is never produced unnoticed.
    warnings: list[str] = []
    if not (run_dir / ".hydra").exists():
        warnings.append(
            f"{run_dir} has no .hydra/ config; the bundle will lack the composed run configuration."
        )
    if not (run_dir / STAMP_FILENAME).exists():
        warnings.append(
            f"{run_dir} has no {STAMP_FILENAME}; the bundle will lack "
            f"the run-stamp (commit + environment provenance)."
        )
    eval_dir = cfg.get("eval_dir")
    if eval_dir and not Path(eval_dir).exists():
        warnings.append(
            f"eval_dir {eval_dir} does not exist; the bundle will lack "
            f"the held-out evaluation result."
        )
    datasets, dataset_warnings = _resolve_datasets(cfg, repo_root)
    warnings += dataset_warnings
    commit = _run_commit(run_dir)
    if commit is None:
        warnings.append(
            "no commit in the run stamp; the source archive will fall "
            "back to HEAD, which may not be the code that produced this run."
        )
    model_id = cfg.get("model_id", "main")
    card_path = _model_card_path(repo_root, model_id)
    facts = _gather_card_facts(cfg, run_dir, commit, datasets)
    if card_path is None:
        warnings.append(
            f"no MODEL_CARD.{model_id}.md or MODEL_CARD.md; the bundle will have no model card."
        )
        card, _ = _assemble_card(facts, "", "MODEL_CARD.md")  # facts only, for --card
    else:
        card, card_warnings = _assemble_card(facts, card_path.read_text(), card_path.name)
        warnings += card_warnings
    if not as_json:
        for w in warnings:
            info(f"caster: WARNING — {w}")
    tag = cfg.get("tag", "untagged")
    asset_stem = f"{tag}_{model_id}"
    out = Path(cfg.get("output_dir", "releases"))

    if card_only:
        return {
            "model_card": card_path.name if card_path else None,
            "card": card,
            "warnings": warnings,
        }
    if dry_run:
        # Report what would be packaged and which warnings would fire; write nothing.
        return {
            "dry_run": True,
            "asset": f"{asset_stem}.zip",
            "output_dir": str(out),
            "run_dir": str(run_dir),
            "would_use_commit": commit,
            "datasets": {d["name"]: d["disposition"] for d in datasets},
            "model_card": card_path.name if card_path else None,
            "warnings": warnings,
        }

    out.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        # Build the RO-Crate at the bag root. bagit.make_bag then relocates
        # everything into data/, so the BagIt payload (data/) IS the RO-Crate
        # root and ro-crate-metadata.json lands directly in data/.
        bag_root = Path(tmp) / asset_stem
        bag_root.mkdir(parents=True)
        payload = bag_root

        summary = _stage_payload(
            cfg, repo_root, run_dir, commit, payload, datasets, card if card_path else None
        )
        embedded = _stage_datasets(datasets, repo_root, summary["source_ref"], payload)
        _write_crate_metadata(ROCrate, payload, cfg, datasets)

        # wrap as a bag: bagit moves the payload into data/ and writes manifests.
        bagit.make_bag(
            str(bag_root),
            checksums=["sha256"],
            bag_info={
                "Organization": org,
                "Contact-Email": email,
                "External-Description": f"{cfg.get('project_name', asset_stem)} {tag}",
                "Source-Commit": summary["source_ref"],
            },
        )

        shutil.make_archive(str(out / asset_stem), "zip", tmp, asset_stem)

    stats = {
        "platform": "rocrate-bagit",
        "asset": f"{asset_stem}.zip",
        "source_ref": summary["source_ref"],
        "checkpoint_sha256": summary["checkpoint_sha256"],
        "model_card": card_path.name if card_path else None,
        "datasets": {d["name"]: d["disposition"] for d in datasets},
        "embedded_datasets": embedded,
        "warnings": warnings,
    }
    # the packaging step is itself provenance-tracked
    write_stamp(out, stats=stats)
    return stats


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Caster archival packaging.")
    parser.add_argument("config", help="path to a release config YAML")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be packaged (and any warnings); write nothing",
    )
    parser.add_argument(
        "--card",
        action="store_true",
        help="print the model card the bundle would carry (and any warnings); write nothing",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output on stdout")
    args = parser.parse_args(argv)
    cfg = yaml.safe_load(Path(args.config).read_text()) or {}
    try:
        stats = package(cfg, dry_run=args.dry_run, as_json=args.json, card_only=args.card)
    except DatasetError as exc:
        if args.json:
            emit_json("package", ok=False, problems=exc.problems)
        else:
            for p in exc.problems:
                info(f"FAIL  {p}")
        return 1

    if args.json:
        emit_json("package", ok=True, result=stats, problems=stats.get("warnings", []))
        return 0
    if args.card:
        print(stats["card"], end="")
        return 0
    if args.dry_run:
        info(
            f"DRY RUN — would write {stats['output_dir']}/{stats['asset']} "
            f"(source @ {(stats['would_use_commit'] or 'HEAD')[:7]}); "
            f"{len(stats['warnings'])} warning(s). Nothing written."
        )
        return 0
    out = Path(cfg.get("output_dir", "releases"))
    print(f"wrote {out / stats['asset']}  (source @ {stats['source_ref'][:7]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
