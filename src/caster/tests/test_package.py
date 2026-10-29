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


def test_each_release_keeps_its_own_stamp(git_project, run_dir):
    """Two releases in one output_dir: each bundle sits in its own directory with
    its packaging stamp, so the second build does not overwrite the first's."""
    _stamp_run(run_dir)
    cfg = _release_cfg(git_project, run_dir)
    first = PKG.package(cfg, repo_root=git_project)
    second = PKG.package({**cfg, "model_id": "other"}, repo_root=git_project)

    out = git_project / "releases"
    assert (first["asset"], second["asset"]) == (
        "v1.0_main/v1.0_main.zip",
        "v1.0_other/v1.0_other.zip",
    )
    assert not (out / P.STAMP_FILENAME).exists()
    for stats in (first, second):
        stamp = json.loads((out / stats["asset"]).with_name(P.STAMP_FILENAME).read_text())
        assert stamp["stats"]["asset"] == stats["asset"]


def _unzip(git_project, tmp_path) -> Path:
    bundle = next((git_project / "releases").glob("*/*.zip"))
    zipfile.ZipFile(bundle).extractall(tmp_path / "x")
    return next((tmp_path / "x").iterdir()) / "data"


def test_reference_names_the_dataset_without_bytes(git_project, run_dir, tmp_path):
    """A reference source defaults to 'reference': the crate and the card name
    its url and citation DOI; no bytes, no fetch.txt; only listed records travel."""
    _stamp_run(run_dir)
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
    card = PKG.package(cfg, repo_root=git_project, card_only=True)["card"]
    assert (
        "- **pub** — not included; obtain it from <https://doi.org/10.25550/3-HXMC>. "
        "Cite <https://doi.org/10.1038/s41597-022-01338-x>." in card
    )
    copied = sorted(p.name for p in (payload / "provenance" / "data_sources").iterdir())
    assert copied == ["pub.json", "pub.yaml"]


def test_local_defaults_to_omit(git_project, run_dir):
    _stamp_run(run_dir)
    cfg = _release_cfg(git_project, run_dir)
    cfg["datasets"] = [{"name": "synthetic"}]
    result = PKG.package(cfg, repo_root=git_project, dry_run=True)
    assert result["datasets"] == {"synthetic": "omit"}
    assert not any("synthetic" in w for w in result["warnings"])


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


def _bundle_card(git_project, stats) -> str:
    with zipfile.ZipFile(git_project / "releases" / stats["asset"]) as z:
        return z.read(next(n for n in z.namelist() if n.endswith("data/MODEL_CARD.md"))).decode()


def test_model_card_assembles_facts_and_prose(git_project, run_dir):
    """The project's plain-Markdown card supplies the prose; the frame supplies
    the title, section order and facts. Prompts (comments) and the project's own
    title are dropped; an extra section stays after the one it follows."""
    _stamp_run(run_dir)  # stamps stats={"val_accuracy": 0.9}
    (git_project / "MODEL_CARD.md").write_text(
        "# My title\n\nA short summary.\n\n"
        "## Intended use\n\n<!-- prompt -->\nClassifying widgets.\n\n"
        "## Funding\n\nGrant 42.\n\n"
        "## Model details\n\nA logistic regression.\n\n"
        "```\n## not a heading\n```\n"
    )
    cfg = _release_cfg(git_project, run_dir)
    cfg["license"] = "MIT"
    stats = PKG.package(cfg, repo_root=git_project)
    card = _bundle_card(git_project, stats)
    assert stats["model_card"] == "MODEL_CARD.md"
    assert card.startswith("# Model Card: demo v1.0\n\nA short summary.\n\n## Model details")
    assert "My title" not in card and "prompt" not in card
    details = card.index("- **License:** MIT")
    assert details < card.index("A logistic regression.") < card.index("## not a heading")
    assert card.index("## Intended use") < card.index("Classifying widgets.")
    assert card.index("Classifying widgets.") < card.index("## Funding") < card.index("Grant 42.")
    assert card.index("Grant 42.") < card.index("## Training data")
    assert "| val_accuracy | 0.9 |" in card
    assert card.rstrip().endswith("RO-Crate metadata at the bundle root.")


def test_model_card_reports_missing_and_empty_sections(git_project, run_dir):
    _stamp_run(run_dir)
    (git_project / "MODEL_CARD.md").write_text(
        "## Model details\n\nx\n\n## Intended use\n\n<!-- only a prompt -->\n\n"
        "## Training data\n\nx\n\n## Evaluation\n\nx {{ version }}\n"
    )
    cfg = _release_cfg(git_project, run_dir)
    result = PKG.package(cfg, repo_root=git_project, card_only=True)
    card_warnings = [w for w in result["warnings"] if w.startswith("MODEL_CARD.md")]
    assert card_warnings == [
        "MODEL_CARD.md: '## Intended use' is still empty; the card is incomplete",
        "MODEL_CARD.md: no '## Limitations, risks, and ethical considerations' section; "
        "the card is incomplete",
        "MODEL_CARD.md contains Jinja markers; the card is plain Markdown and they are "
        "shown as written (the facts come from caster)",
    ]
    assert "x {{ version }}" in result["card"]  # not rendered
    assert not (git_project / "releases").exists()  # the preview writes nothing


def test_model_card_omitted_when_absent(git_project, run_dir):
    """A project with no card ships none, and says so; --card still previews
    the facts."""
    _stamp_run(run_dir)
    cfg = _release_cfg(git_project, run_dir)
    dry = PKG.package(cfg, repo_root=git_project, dry_run=True)
    assert dry["model_card"] is None
    assert any("no MODEL_CARD.main.md or MODEL_CARD.md" in w for w in dry["warnings"])
    assert (
        "- **Release identifier:** main"
        in PKG.package(cfg, repo_root=git_project, card_only=True)["card"]
    )
    stats = PKG.package(cfg, repo_root=git_project)
    assert stats["model_card"] is None
    with zipfile.ZipFile(git_project / "releases" / stats["asset"]) as z:
        assert not any(n.endswith("MODEL_CARD.md") for n in z.namelist())


def test_model_card_per_model_id(git_project, run_dir):
    """MODEL_CARD.<model_id>.md is this model's card; MODEL_CARD.md is the
    fallback for every other model."""
    _stamp_run(run_dir)
    (git_project / "MODEL_CARD.md").write_text("## Intended use\n\nshared prose\n")
    (git_project / "MODEL_CARD.gene.md").write_text("## Intended use\n\ngene prose\n")
    cfg = _release_cfg(git_project, run_dir)
    for model_id, prose in [("gene", "gene prose"), ("main", "shared prose")]:
        cfg["model_id"] = model_id
        card = _bundle_card(git_project, PKG.package(cfg, repo_root=git_project))
        assert f"- **Release identifier:** {model_id}" in card
        assert prose in card


def test_card_cli_prints_the_card(git_project, run_dir, capsys):
    _stamp_run(run_dir)
    cfg = _release_cfg(git_project, run_dir)
    path = git_project / "release.yaml"
    path.write_text(json.dumps(cfg))
    assert PKG.main([str(path), "--card"]) == 0
    assert capsys.readouterr().out.startswith("# Model Card: demo v1.0\n")


def test_shipped_model_card_assembles(git_project, run_dir):
    """The project's MODEL_CARD.md assembles with the frame: every required
    section present, datasets listed one per line. The template ships the
    sections empty; a project that has written them gets no warnings at all."""
    from pathlib import Path as _P

    shipped = _P(PKG.__file__).resolve().parents[2] / "MODEL_CARD.md"
    if not shipped.exists():
        pytest.skip("no shipped MODEL_CARD.md in this layout")
    datasets = [
        {"name": n, "disposition": d, "identifier": i, "citation_doi": c}
        for n, d, i, c in [
            ("pub", "reference", "https://doi.org/10.1/x", "10.1/y"),
            ("pubcopy", "embed", "https://doi.org/10.1/z", None),
            ("lab", "omit", None, None),
        ]
    ]
    facts = PKG._gather_card_facts(_release_cfg(git_project, run_dir), run_dir, "abc1234", datasets)
    card, warnings = PKG._assemble_card(facts, shipped.read_text(), "MODEL_CARD.md")
    assert card.startswith("# Model Card:")
    assert all("is still empty" in w for w in warnings), warnings
    assert "<!--" not in card
    assert "obtain it from <https://doi.org/10.1/x>. Cite <https://doi.org/10.1/y>." in card
    assert "included under `datasets/pubcopy/`; published at" in card
    assert "\n- **lab** — not included; see its record" in card  # one item per line
