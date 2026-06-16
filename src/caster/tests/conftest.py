"""Shared test fixtures.

These build throwaway git projects in temporary directories so the tests can
exercise the real git/DVC-pointer/run-dir behavior without touching the
developer's working tree. Each fixture chdirs into the temp project and restores
the original cwd on teardown.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def git_project(tmp_path, monkeypatch):
    """A minimal initialized git repo with a uv.lock, cwd set to its root.

    Yields the project Path. A single commit exists so HEAD resolves.
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    _git("init", cwd=proj)
    _git("config", "user.email", "test@example.com", cwd=proj)
    _git("config", "user.name", "Test", cwd=proj)
    (proj / "uv.lock").write_text('version = 1\n[[package]]\nname = "demo"\n')
    (proj / "README.md").write_text("# test project\n")
    _git("add", "-A", cwd=proj)
    _git("commit", "-m", "init", cwd=proj)
    monkeypatch.chdir(proj)
    return proj


@pytest.fixture
def run_dir(git_project):
    """A Hydra-style run directory inside the git project, with a .hydra config
    and a fake checkpoint, but NOT yet stamped. Returns its Path."""
    d = git_project / "outputs" / "2026-01-01" / "00-00-00"
    (d / ".hydra").mkdir(parents=True)
    (d / ".hydra" / "config.yaml").write_text("seed: 42\nmodel:\n  C: 1.0\n")
    (d / ".hydra" / "overrides.yaml").write_text("[]\n")
    (d / "model.skops").write_bytes(b"\x00" * 256)
    return d


def read_stamp(run_dir: Path) -> dict:
    return json.loads((run_dir / "_provenance.json").read_text())
