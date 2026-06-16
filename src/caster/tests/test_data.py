"""Tests for data-source validate/emit (caster.data)."""

from __future__ import annotations

import json

import pytest

from caster import data as D

pytest.importorskip("mlcroissant")


def _write_yaml(path, text):
    path.write_text(text)


def test_emit_produces_valid_croissant(git_project):
    sources = git_project / "data_sources"
    sources.mkdir()
    _write_yaml(
        sources / "brain_mri.yaml",
        """
name: brain_mri
description: T1 brain MRI volumes.
url: https://doi.org/10.5281/zenodo.0000000
license: CC-BY-4.0
version: "1.0"
date_published: "2024-06-01"
distribution:
  - name: brain_mri.zip
    sha256: "3a7bd3e2360a3d29eea436fcfb7e44c735d117c42d1c1835420b6b9942dd4f1b"
""",
    )
    out = D.emit("brain_mri", sources_dir=sources)
    doc = json.loads(out.read_text())
    assert doc["@type"] in ("Dataset", "sc:Dataset")  # mlcroissant namespaces it
    assert doc["name"] == "brain_mri"
    assert doc["distribution"][0]["encodingFormat"] == "application/zip"  # inferred
    assert doc["distribution"][0]["sha256"].startswith("3a7bd3")
    assert D.validate_record(out) == []  # round-trips clean


def test_emit_local_distribution_needs_no_url(git_project):
    sources = git_project / "data_sources"
    sources.mkdir()
    _write_yaml(
        sources / "cohort.yaml",
        """
name: cohort
description: In-house cohort, not redistributable.
license: proprietary
distribution: local
provenance: |
  Assembled in-house.
""",
    )
    out = D.emit("cohort", sources_dir=sources)
    assert D.validate_record(out) == []


def test_emit_rejects_distribution_without_url(git_project):
    sources = git_project / "data_sources"
    sources.mkdir()
    _write_yaml(
        sources / "bad.yaml",
        """
name: bad
description: missing url.
distribution:
  - name: bad.zip
    sha256: "3a7bd3e2360a3d29eea436fcfb7e44c735d117c42d1c1835420b6b9942dd4f1b"
""",
    )
    with pytest.raises(ValueError, match="needs a 'url'"):
        D.emit("bad", sources_dir=sources)


def test_emit_coerces_numeric_sha(git_project):
    """A YAML hash parsed as an int must not crash; it's coerced to str."""
    sources = git_project / "data_sources"
    sources.mkdir()
    _write_yaml(
        sources / "n.yaml",
        """
name: n
description: numeric-looking hash.
url: https://example.org/n.bin
distribution:
  - name: n.bin
    sha256: 123456
""",
    )
    out = D.emit("n", sources_dir=sources)  # must not raise
    doc = json.loads(out.read_text())
    assert doc["distribution"][0]["sha256"] == "123456"


def test_validate_coverage_flags_undocumented_tracked_dataset(git_project):
    sources = git_project / "data_sources"
    sources.mkdir()
    raw = git_project / "data" / "raw"
    raw.mkdir(parents=True)
    (raw / "orphan").mkdir()
    (raw / "orphan.dvc").write_text("outs:\n- md5: abc\n  path: orphan\n")

    problems = D.validate(data_dir=git_project / "data", sources_dir=sources)
    assert any("orphan" in p for p in problems)


def test_validate_passes_when_tracked_dataset_documented(git_project):
    sources = git_project / "data_sources"
    sources.mkdir()
    _write_yaml(
        sources / "ds.yaml",
        """
name: ds
description: a documented dataset.
license: CC0-1.0
distribution: local
""",
    )
    D.emit("ds", sources_dir=sources)

    raw = git_project / "data" / "raw"
    raw.mkdir(parents=True)
    (raw / "ds").mkdir()
    (raw / "ds.dvc").write_text("outs:\n- md5: abc\n  path: ds\n")

    problems = D.validate(data_dir=git_project / "data", sources_dir=sources)
    assert problems == []


def test_validate_json_envelope(git_project, capsys):
    """validate --json emits the standard envelope as parseable JSON on stdout."""
    rc = D.main(
        [
            "validate",
            "--json",
            "--data-dir",
            str(git_project / "data"),
            "--sources-dir",
            str(git_project / "data_sources"),
        ]
    )
    out = capsys.readouterr().out
    obj = json.loads(out)  # stdout must be pure JSON
    assert obj["command"] == "validate"
    assert obj["ok"] == (rc == 0)
    assert "problems" in obj
