"""Tests for archival packaging (caster.package)."""

from __future__ import annotations

import glob
import json
import os
import tempfile
import zipfile
from pathlib import Path

import pytest

from caster import provenance as P

pytest.importorskip("rocrate")
pytest.importorskip("bagit")
from caster import package as PKG  # noqa: E402

LOCAL_RECORD = "name: synthetic\ntype: local\ndescription: generated.\n"
REFERENCE_RECORD = """name: pub
type: reference
format: bdbag
url: https://doi.org/10.25550/3-HXMC
description: a published bag.
"""


def _record(git_project, name, text, json_doc=None):
    sources = git_project / "data_sources"
    sources.mkdir(exist_ok=True)
    (sources / f"{name}.yaml").write_text(text)
    if json_doc is not None:
        (sources / f"{name}.json").write_text(json.dumps(json_doc))


def _release_cfg(git_project, run_dir) -> dict:
    _record(git_project, "synthetic", LOCAL_RECORD)
    return {
        "project_name": "demo",
        "tag": "v1.0",
        "model_id": "main",
        "description": "test release",
        "run_dir": str(run_dir),
        "checkpoint": "model.skops",
        "eval_dir": str(run_dir),
        "output_dir": str(git_project / "releases"),
        "organization": "Example Lab",
        "contact_email": "lab@example.org",
        "datasets": [{"name": "synthetic", "disposition": "omit"}],
    }


def _stamp_run(run_dir):
    """Stamp the run so the packager can read its commit."""
    P.write_stamp(run_dir, stats={"val_accuracy": 0.9})


def test_package_builds_valid_bag(git_project, run_dir):
    _stamp_run(run_dir)
    cfg = _release_cfg(git_project, run_dir)
    stats = PKG.package(cfg, repo_root=git_project)

    zip_path = git_project / "releases" / stats["asset"]
    assert zip_path.exists()

    import bagit

    with tempfile.TemporaryDirectory() as tmp:
        zipfile.ZipFile(zip_path).extractall(tmp)
        root = glob.glob(os.path.join(tmp, "*"))[0]
        bagit.Bag(root).validate()  # raises if invalid

        # RO-Crate metadata is at the payload root
        from rocrate.rocrate import ROCrate

        ROCrate(os.path.join(root, "data"))

        # provenance chain present
        payload = Path(root) / "data"
        assert (payload / "model" / "model.skops").exists()
        assert (payload / "provenance" / "run" / "_provenance.json").exists()
        assert (payload / "provenance" / "config" / "config.yaml").exists()
        assert glob.glob(str(payload / "source" / "*.tar.gz"))
        # no raw DVC pointers travel; a portable hash manifest does instead
        assert not (payload / "provenance" / "dvc").exists()


def test_source_is_pinned_to_run_commit(git_project, run_dir):
    _stamp_run(run_dir)
    expected = P.git_state()["commit"]
    cfg = _release_cfg(git_project, run_dir)
    stats = PKG.package(cfg, repo_root=git_project)
    assert stats["source_ref"] == expected


def test_package_requires_org_and_email(git_project, run_dir):
    _stamp_run(run_dir)
    cfg = _release_cfg(git_project, run_dir)
    del cfg["organization"]
    with pytest.raises(ValueError, match="organization"):
        PKG.package(cfg, repo_root=git_project)


def test_bad_disposition_rejected(git_project, run_dir):
    _stamp_run(run_dir)
    cfg = _release_cfg(git_project, run_dir)
    cfg["datasets"] = [{"name": "synthetic", "disposition": "nonsense"}]
    with pytest.raises(ValueError, match="disposition"):
        PKG.package(cfg, repo_root=git_project)


def _unzip(git_project, tmp_path) -> Path:
    bundle = next((git_project / "releases").glob("*.zip"))
    zipfile.ZipFile(bundle).extractall(tmp_path / "x")
    return next((tmp_path / "x").iterdir()) / "data"


def test_reference_names_the_dataset_without_bytes(git_project, run_dir, tmp_path):
    """A reference source defaults to 'reference': the crate and the card name
    its url and citation DOI; no bytes, no fetch.txt; only listed records travel."""
    _stamp_run(run_dir)
    (git_project / "MODEL_CARD.md").write_text(
        "{% for d in datasets %}{{ d.name }} {{ d.identifier }} {{ d.citation_doi }}{% endfor %}"
    )
    cfg = _release_cfg(git_project, run_dir)
    _record(
        git_project,
        "pub",
        REFERENCE_RECORD,
        json_doc={"name": "pub", "citeAs": "@article{x, doi = {10.1038/s41597-022-01338-x}}"},
    )
    _record(git_project, "unlisted", LOCAL_RECORD.replace("synthetic", "unlisted"))
    cfg["datasets"] = [{"name": "pub"}]
    stats = PKG.package(cfg, repo_root=git_project)
    assert stats["datasets"] == {"pub": "reference"}
    assert stats["embedded_datasets"] == []

    payload = _unzip(git_project, tmp_path)
    assert not (payload.parent / "fetch.txt").exists()
    assert not (payload / "datasets").exists()
    graph = {
        e["@id"]: e for e in json.loads((payload / "ro-crate-metadata.json").read_text())["@graph"]
    }
    doi = "https://doi.org/10.25550/3-HXMC"
    assert graph[doi]["citation"] == "https://doi.org/10.1038/s41597-022-01338-x"
    assert {"@id": doi} in graph["./"]["isBasedOn"]
    card = (payload / "MODEL_CARD.md").read_text()
    assert "pub https://doi.org/10.25550/3-HXMC 10.1038/s41597-022-01338-x" in card
    copied = sorted(p.name for p in (payload / "provenance" / "data_sources").iterdir())
    assert copied == ["pub.json", "pub.yaml"]


def test_local_defaults_to_omit(git_project, run_dir):
    _stamp_run(run_dir)
    cfg = _release_cfg(git_project, run_dir)
    cfg["datasets"] = [{"name": "synthetic"}]
    result = PKG.package(cfg, repo_root=git_project, dry_run=True)
    assert result["datasets"] == {"synthetic": "omit"}
    assert result["warnings"] == []


@pytest.mark.parametrize(
    "entry, record, message",
    [
        ({"name": "synthetic", "disposition": "reference"}, None, "nothing to reference"),
        ({"name": "missing"}, None, "no data_sources/missing.yaml"),
        ({"name": "synthetic", "disposition": "fetch"}, None, "replaced by 'reference'"),
        ({"name": "untyped"}, "name: untyped\n", "'type' must be one of"),
        ({"name": "nourl"}, "name: nourl\ntype: reference\nformat: other\n", "needs a 'url'"),
    ],
)
def test_dry_run_refuses_what_a_real_run_would(git_project, run_dir, entry, record, message):
    _stamp_run(run_dir)
    cfg = _release_cfg(git_project, run_dir)
    if record:
        _record(git_project, entry["name"], record)
    cfg["datasets"] = [entry]
    for dry_run in (True, False):
        with pytest.raises(PKG.DatasetError, match=message):
            PKG.package(cfg, repo_root=git_project, dry_run=dry_run)
    assert not (git_project / "releases").exists()


def test_reference_source_omitted_warns(git_project, run_dir):
    _stamp_run(run_dir)
    cfg = _release_cfg(git_project, run_dir)
    _record(git_project, "pub", REFERENCE_RECORD)
    cfg["datasets"] = [{"name": "pub", "disposition": "omit"}]
    result = PKG.package(cfg, repo_root=git_project, dry_run=True)
    assert any("'reference' would name it" in w for w in result["warnings"])


def test_cli_reports_dataset_problems_as_json(git_project, run_dir, capsys):
    _stamp_run(run_dir)
    cfg = _release_cfg(git_project, run_dir)
    cfg["datasets"] = [{"name": "missing"}, {"name": "synthetic", "disposition": "reference"}]
    path = git_project / "release.yaml"
    path.write_text(json.dumps(cfg))
    assert PKG.main([str(path), "--dry-run", "--json"]) == 1
    obj = json.loads(capsys.readouterr().out)
    assert obj["ok"] is False and len(obj["problems"]) == 2


def test_citation_doi_prefers_yaml_then_cite_as(tmp_path):
    doc = tmp_path / "r.json"
    doc.write_text(json.dumps({"citeAs": "see https://doi.org/10.5281/zenodo.123, 2024"}))
    assert PKG._citation_doi({"citation": {"doi": "10.1/x"}}, doc) == "10.1/x"
    assert PKG._citation_doi({}, doc) == "10.5281/zenodo.123"
    assert PKG._citation_doi({}, tmp_path / "absent.json") is None


def test_checkpoint_path_decouples_bytes_from_provenance(git_project, run_dir, tmp_path):
    """Bytes can come from a stable path while provenance comes from run_dir."""
    _stamp_run(run_dir)
    # a separate 'stable' checkpoint, distinct from the one in run_dir
    stable = git_project / "models" / "model.skops"
    stable.parent.mkdir(parents=True)
    stable.write_bytes(b"\x01" * 300)
    cfg = _release_cfg(git_project, run_dir)
    cfg["checkpoint_path"] = str(stable)
    stats = PKG.package(cfg, repo_root=git_project)
    # the packaged checkpoint hash should match the stable bytes, not run_dir's
    import hashlib

    expected = hashlib.sha256(stable.read_bytes()).hexdigest()
    assert stats["checkpoint_sha256"] == expected


def test_missing_run_dir_rejected(git_project, run_dir):
    _stamp_run(run_dir)
    cfg = _release_cfg(git_project, run_dir)
    del cfg["run_dir"]
    with pytest.raises(ValueError, match="run_dir"):
        PKG.package(cfg, repo_root=git_project)


def test_nonexistent_run_dir_refused(git_project, tmp_path):
    """A run_dir that doesn't exist (the placeholder-path mistake) is refused,
    not silently packaged into a provenance-stripped bundle."""
    cfg = _release_cfg(git_project, tmp_path / "does" / "not" / "exist")
    with pytest.raises(FileNotFoundError, match="run_dir does not exist"):
        PKG.package(cfg, repo_root=git_project)


def test_dry_run_writes_nothing(git_project, run_dir):
    """--dry-run reports but writes no bundle."""
    _stamp_run(run_dir)
    cfg = _release_cfg(git_project, run_dir)
    out = git_project / "releases"
    result = PKG.package(cfg, repo_root=git_project, dry_run=True)
    assert result["dry_run"] is True
    assert not out.exists() or not any(out.iterdir())


def test_package_json_envelope_has_warnings(git_project, tmp_path):
    """A run_dir lacking a stamp surfaces warnings in the result (not silently)."""
    bare = tmp_path / "bare"
    (bare / ".hydra").mkdir(parents=True)
    (bare / ".hydra" / "config.yaml").write_text("x: 1\n")
    cfg = _release_cfg(git_project, bare)
    result = PKG.package(cfg, repo_root=git_project, dry_run=True, as_json=True)
    assert any("run-stamp" in w or "_provenance" in w for w in result["warnings"])


def test_model_card_rendered_from_repo_card(git_project, run_dir):
    """The project's own MODEL_CARD.md is the source of truth: the packager fills
    its facts and carries the author's prose into the bundle."""
    _stamp_run(run_dir)  # stamps stats={"val_accuracy": 0.9}
    # the project authors a card with a Jinja fact + a human-authored section
    (git_project / "MODEL_CARD.md").write_text(
        "# Model Card: {{ project_name }} {{ version }}\n\n"
        "License: {{ license }}\n\n"
        "## Intended use\n\nClassifying widgets for the demo pipeline.\n"
    )
    cfg = _release_cfg(git_project, run_dir)
    cfg["license"] = "MIT"
    PKG.package(cfg, repo_root=git_project)
    import zipfile

    bundle = next((git_project / "releases").glob("*.zip"))
    with zipfile.ZipFile(bundle) as z:
        card_name = next(n for n in z.namelist() if n.endswith("MODEL_CARD.md"))
        card = z.read(card_name).decode()
    assert "MIT" in card  # fact filled
    assert "demo" in card  # project_name fact filled
    assert "Classifying widgets" in card  # author's prose carried through


def test_model_card_omitted_when_absent(git_project, run_dir):
    """A project with no MODEL_CARD.md simply ships no card (no crash)."""
    _stamp_run(run_dir)
    cfg = _release_cfg(git_project, run_dir)
    stats = PKG.package(cfg, repo_root=git_project)
    assert "model_card" not in stats


def test_shipped_model_card_template_renders(git_project, run_dir):
    """The MODEL_CARD.md shipped in the project root must be valid Jinja and
    render without error — guards against template syntax mistakes shipping."""
    import shutil
    from pathlib import Path as _P

    # locate the repo's shipped MODEL_CARD.md (two levels up from the package)
    shipped = _P(PKG.__file__).resolve().parents[2] / "MODEL_CARD.md"
    if not shipped.exists():
        import pytest as _pytest

        _pytest.skip("no shipped MODEL_CARD.md in this layout")
    shutil.copy2(shipped, git_project / "MODEL_CARD.md")
    datasets = [
        {"name": n, "disposition": d, "identifier": i, "citation_doi": c}
        for n, d, i, c in [
            ("pub", "reference", "https://doi.org/10.1/x", "10.1/y"),
            ("pubcopy", "embed", "https://doi.org/10.1/z", None),
            ("lab", "omit", None, None),
        ]
    ]
    facts = PKG._gather_card_facts(_release_cfg(git_project, run_dir), run_dir, "abc1234", datasets)
    rendered = PKG._render_model_card(facts, git_project)
    assert rendered and "# Model Card:" in rendered  # rendered, didn't raise
    assert "obtain it from <https://doi.org/10.1/x>. Cite <https://doi.org/10.1/y>." in rendered
    assert "included under `datasets/pubcopy/`; published at" in rendered
    assert "\n- **lab** — not included; see its record" in rendered  # one item per line
