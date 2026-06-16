"""Tests for the run-stamp (caster.provenance)."""

from __future__ import annotations

from caster import provenance as P
from caster.tests.conftest import read_stamp


def test_git_state_captures_commit_and_clean(git_project):
    state = P.git_state()
    assert state["commit"] is not None
    assert len(state["commit"]) == 40
    assert state["branch"] in ("main", "master")
    assert state["dirty"] is False  # fresh commit, nothing changed


def test_git_state_detects_dirty(git_project):
    (git_project / "README.md").write_text("# changed\n")
    assert P.git_state()["dirty"] is True


def test_environment_records_lock_hash_and_sync_mode(git_project, monkeypatch):
    monkeypatch.setenv("CASTER_SYNC_MODE", "frozen")
    env = P.environment_state()
    assert env["sync_mode"] == "frozen"
    assert env["uv_lock_sha256"] is not None and len(env["uv_lock_sha256"]) == 64
    assert isinstance(env["packages_observed"], list) and env["packages_observed"]
    assert env["python"]


def test_sync_mode_defaults_to_unlocked(git_project, monkeypatch):
    monkeypatch.delenv("CASTER_SYNC_MODE", raising=False)
    assert P.environment_state()["sync_mode"] == "unlocked"


def test_write_stamp_creates_completion_file_with_stats(git_project):
    out = git_project / "outputs" / "run1"
    P.write_stamp(out, stats={"val_accuracy": 0.91})
    stamp = read_stamp(out)
    assert stamp["completed"] is True
    assert stamp["stats"] == {"val_accuracy": 0.91}
    assert stamp["git"]["commit"] is not None
    assert stamp["created_at"].endswith("Z")


def test_cli_stamp_parses_stats(git_project):
    out = git_project / "outputs" / "run2"
    rc = P.main(["stamp", str(out), "--stats", "acc=0.8", "note=baseline"])
    assert rc == 0
    stamp = read_stamp(out)
    assert stamp["stats"]["acc"] == 0.8  # coerced to float
    assert stamp["stats"]["note"] == "baseline"  # left as string


def test_callback_extracts_dict_return_value(git_project, run_dir):
    """The callback records a dict return value under stats."""
    cb = P.RunStampCallback()

    class _JobReturn:
        return_value = {"val_accuracy": 0.88, "epochs": 5}

    # Minimal stand-in: the callback resolves the dir from HydraConfig, which
    # isn't running here, so it falls back to cwd. Point cwd at the run dir.
    import os

    os.chdir(run_dir)
    cb.on_job_end(config=None, job_return=_JobReturn())
    stamp = read_stamp(run_dir)
    assert stamp["stats"] == {"val_accuracy": 0.88, "epochs": 5}
    assert stamp["completed"] is True


def test_callback_handles_stats_key(git_project, run_dir):
    """A return value with a nested 'stats' key uses that."""
    cb = P.RunStampCallback()

    class _JobReturn:
        return_value = {"stats": {"f1": 0.7}, "other": 1}

    import os

    os.chdir(run_dir)
    cb.on_job_end(config=None, job_return=_JobReturn())
    assert read_stamp(run_dir)["stats"] == {"f1": 0.7}


def test_callback_noop_hooks_exist():
    """Hydra invokes the full protocol; the no-op hooks must not raise."""
    cb = P.RunStampCallback()
    cb.on_run_start(config=None)
    cb.on_run_end(config=None)
    cb.on_multirun_start(config=None)
    cb.on_multirun_end(config=None)
    cb.on_job_start(config=None)


def test_ledger_builds_table_from_stamps(git_project):
    """build_ledger turns run stamps into a Markdown table, one row per run."""
    P.write_stamp(git_project / "outputs" / "a", stats={"val_acc": 0.9})
    P.write_stamp(git_project / "outputs" / "b", stats={"val_acc": 0.85, "epochs": 10})
    table = P.build_ledger(git_project / "outputs")
    assert "| timestamp | commit | stats | run dir |" in table
    assert table.count("\n| ") >= 2  # header sep + 2 data rows (at least)
    assert "val_acc" in table


def test_ledger_tolerates_null_commit(tmp_path):
    """A stamp with commit: null renders as a dash, not a crash."""
    import json

    run = tmp_path / "outputs" / "r"
    run.mkdir(parents=True)
    (run / "_provenance.json").write_text(
        json.dumps(
            {"git": {"commit": None}, "created_at": "2026-01-01T00:00:00Z", "stats": {"x": 1}}
        )
    )
    table = P.build_ledger(tmp_path / "outputs")
    assert "| — |" in table  # null commit rendered as dash
    assert "2026-01-01T00:00:00Z" in table


def test_ledger_escapes_pipes_in_stats(tmp_path):
    """Stats containing a pipe can't break the Markdown table."""
    import json

    run = tmp_path / "outputs" / "r"
    run.mkdir(parents=True)
    (run / "_provenance.json").write_text(
        json.dumps({"git": {"commit": "abc123"}, "created_at": "t", "stats": {"note": "a|b"}})
    )
    table = P.build_ledger(tmp_path / "outputs")
    assert "a\\|b" in table  # pipe escaped


def test_stamp_records_relative_run_dir(tmp_path, monkeypatch):
    """The stamp self-locates via a run_dir field, stored relative to cwd."""
    import json as _json

    monkeypatch.chdir(tmp_path)
    rd = tmp_path / "outputs" / "run1"
    P.write_stamp(rd, stats={"acc": 0.5})
    stamp = _json.loads((rd / "_provenance.json").read_text())
    assert stamp["run_dir"] == "outputs/run1"  # relative, not absolute
