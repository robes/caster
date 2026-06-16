"""Tests for archival packaging (caster.package)."""

from __future__ import annotations

import glob
import os
import tempfile
import zipfile
from pathlib import Path

import pytest

from caster import provenance as P

pytest.importorskip("rocrate")
pytest.importorskip("bagit")
from caster import package as PKG  # noqa: E402


def _release_cfg(git_project, run_dir) -> dict:
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
    cfg["datasets"] = [{"name": "x", "disposition": "nonsense"}]
    with pytest.raises(ValueError, match="disposition"):
        PKG.package(cfg, repo_root=git_project)


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
    facts = PKG._gather_card_facts(_release_cfg(git_project, run_dir), run_dir, "abc1234")
    rendered = PKG._render_model_card(facts, git_project)
    assert rendered and "# Model Card:" in rendered  # rendered, didn't raise
