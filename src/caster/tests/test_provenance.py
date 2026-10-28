"""Tests for the run-stamp (caster.provenance)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

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


def test_git_state_detects_untracked_source(git_project):
    """An uncommitted module a run could have imported is real dirtiness."""
    (git_project / "new_module.py").write_text("X = 1\n")
    assert P.git_state()["dirty"] is True


def test_git_state_ignores_output_roots(git_project):
    """Run dirs are written during the run; they are not code drift."""
    for root in ("outputs", "multirun"):
        d = git_project / root / "2026-01-01" / "00-00-00"
        d.mkdir(parents=True)
        (d / "_provenance.json").write_text("{}\n")
        (d / "metrics.json").write_text("{}\n")
    assert P.git_state()["dirty"] is False


def test_git_state_is_independent_of_cwd(git_project, monkeypatch):
    """The check is anchored at the repo root, not the working directory."""
    (git_project / "new_module.py").write_text("X = 1\n")
    sub = git_project / "sub"
    sub.mkdir()
    monkeypatch.chdir(sub)
    assert P.git_state()["dirty"] is True


def test_write_stamp_excludes_its_own_run_root(git_project):
    """A project whose runs land outside outputs/ is not dirtied by them."""
    rd = git_project / "results" / "run1"
    rd.mkdir(parents=True)
    (rd / "metrics.json").write_text("{}\n")
    P.write_stamp(rd)
    assert read_stamp(rd)["git"]["dirty"] is False


DVC_YAML = """
stages:
  prepare:
    cmd: python prepare.py
    outs:
      - data/prepared
  train:
    cmd: python train.py
    wdir: sub
    outs:
      - ../models/model.bin
    metrics:
      - ../models/metrics.json:
          cache: false
  sweep:
    foreach: [a, b]
    do:
      cmd: echo ${item}
      outs:
        - ${item}.txt
"""


def _dvc_project(project):
    (project / "dvc.yaml").write_text(DVC_YAML)
    (project / "models").mkdir()
    (project / "models" / "metrics.json").write_text('{"acc": 0.5}\n')
    (project / "dvc.lock").write_text("schema: '2.0'\n")
    subprocess.run(["git", "add", "-A"], cwd=project, check=True)
    subprocess.run(["git", "commit", "-qm", "pipeline"], cwd=project, check=True)


def test_git_state_ignores_files_the_pipeline_writes(git_project):
    """Between stages of a `dvc repro` the tree holds the earlier stages' outputs
    and a rewritten dvc.lock; the next stage must not stamp itself dirty."""
    _dvc_project(git_project)
    (git_project / "dvc.lock").write_text("schema: '2.0'\nstages: {}\n")
    (git_project / "data" / "prepared").mkdir(parents=True)
    (git_project / "data" / "prepared" / "x.csv").write_text("1\n")
    (git_project / "data" / ".gitignore").write_text("/prepared\n")
    (git_project / "models" / "model.bin").write_bytes(b"\0")
    (git_project / "models" / ".gitignore").write_text("/model.bin\n")
    (git_project / "models" / "metrics.json").write_text('{"acc": 0.9}\n')
    assert P.git_state()["dirty"] is False


def test_pipeline_exclusion_still_sees_code_and_dvc_yaml(git_project):
    _dvc_project(git_project)
    (git_project / "prepare.py").write_text("X = 1\n")
    assert P.git_state()["dirty"] is True
    (git_project / "prepare.py").unlink()
    (git_project / "dvc.yaml").write_text(DVC_YAML + "# edited\n")
    assert P.git_state()["dirty"] is True


def test_pipeline_paths_skip_templated_entries(git_project):
    _dvc_project(git_project)
    paths = P._pipeline_paths()
    assert "models/model.bin" in paths and "models/metrics.json" in paths
    assert not any("${" in p for p in paths)
    (git_project / "a.txt").write_text("unresolved, so still counted\n")
    assert P.git_state()["dirty"] is True


def test_no_dvc_yaml_excludes_nothing_extra(git_project):
    assert P._pipeline_paths() == []


def test_environment_records_lock_hash_and_snapshot_when_unverifiable(git_project):
    """The fixture's uv.lock has no pyproject, so uv's checks error: the verdicts
    are None and the package snapshot is kept."""
    env = P.environment_state()
    assert env["uv_lock_sha256"] is not None and len(env["uv_lock_sha256"]) == 64
    assert env["lock_current"] is None and env["env_synced"] is None
    assert isinstance(env["packages_observed"], list) and env["packages_observed"]
    assert env["python"]


def _fake_uv(
    sync_codes: dict[tuple[str, ...], int],
    lock_code: int = 0,
    drift: dict[tuple[str, ...], str] | None = None,
):
    """Stand-in for P._uv: `uv sync --check` exits, and reports drift, per extras
    selection."""
    calls = []

    def fake(*args, env=None):
        calls.append((args, env))
        if args[0] == "--version":
            return subprocess.CompletedProcess(args, 0, "uv 0.12.10 (x)\n", "")
        if args[0] == "lock":
            return subprocess.CompletedProcess(args, lock_code, "", "")
        selection = args[3:]
        stderr = (drift or {}).get(selection, "")
        return subprocess.CompletedProcess(args, sync_codes[selection], "", stderr)

    return fake, calls


def test_synced_environment_omits_package_snapshot(git_project, monkeypatch):
    fake, calls = _fake_uv({(): 0})
    monkeypatch.setattr(P, "_uv", fake)
    env = P.environment_state()
    assert env["lock_current"] is True and env["env_synced"] is True
    assert env["sync_selection"] == [] and env["sync_drift"] is None
    assert env["uv_version"] == "0.12.10"
    assert "packages_observed" not in env
    # the check targets the running interpreter's environment
    sync_envs = [e for a, e in calls if a[0] == "sync"]
    assert sync_envs and all(e["UV_PROJECT_ENVIRONMENT"] == sys.prefix for e in sync_envs)


def test_second_selection_can_verify(git_project, monkeypatch):
    fake, _ = _fake_uv({(): 1, ("--all-extras",): 0}, drift={(): " - pytest==8.0\n"})
    monkeypatch.setattr(P, "_uv", fake)
    env = P.environment_state()
    assert env["env_synced"] is True and env["sync_selection"] == ["--all-extras"]
    assert env["sync_drift"] is None and "packages_observed" not in env


INSTALLED = ["numpy==2.1.0", "nvidia_cusparselt_cu13==0.8.0", "pytest==8.0", "six==1.16.0"]


def test_drift_stores_only_the_drifted_packages(git_project, monkeypatch):
    """Lock + nearest selection + the installed versions of what drifted is the
    whole environment; the rest of the snapshot would only repeat the lock."""
    drift = {
        (): "Would uninstall 2 packages\n - pytest==8.0\n ~ nvidia-cusparselt-cu13==0.8.1\n",
        ("--all-extras",): "Would install 1 package\n ~ nvidia-cusparselt-cu13==0.8.1\n",
    }
    fake, _ = _fake_uv({(): 1, ("--all-extras",): 1}, lock_code=1, drift=drift)
    monkeypatch.setattr(P, "_uv", fake)
    monkeypatch.setattr(P, "_observed_packages", lambda: INSTALLED)
    env = P.environment_state()
    assert env["lock_current"] is False and env["env_synced"] is False
    assert env["sync_selection"] == ["--all-extras"]
    assert env["sync_drift"] == ["~ nvidia-cusparselt-cu13==0.8.1"]
    assert env["drift_installed"] == ["nvidia_cusparselt_cu13==0.8.0"]
    assert "packages_observed" not in env


def test_drift_with_uncommitted_lock_keeps_snapshot(git_project, monkeypatch):
    (git_project / "uv.lock").write_text("version = 1\n")
    fake, _ = _fake_uv({(): 1, ("--all-extras",): 1}, drift={(): " - pytest==8.0\n"})
    monkeypatch.setattr(P, "_uv", fake)
    monkeypatch.setattr(P, "_observed_packages", lambda: INSTALLED)
    env = P.environment_state()
    assert env["packages_observed"] == INSTALLED and "drift_installed" not in env


def test_synced_with_uncommitted_lock_keeps_snapshot(git_project, monkeypatch):
    """The lock that verified the venv is not at the commit, so it cannot be
    recovered from it: keep the snapshot."""
    (git_project / "uv.lock").write_text("version = 1\n")
    fake, _ = _fake_uv({(): 0})
    monkeypatch.setattr(P, "_uv", fake)
    monkeypatch.setattr(P, "_observed_packages", lambda: INSTALLED)
    env = P.environment_state()
    assert env["env_synced"] is True and env["packages_observed"] == INSTALLED


def test_synced_with_untracked_lock_keeps_snapshot(git_project, monkeypatch):
    subprocess.run(["git", "rm", "-q", "--cached", "uv.lock"], cwd=git_project, check=True)
    subprocess.run(["git", "commit", "-qm", "untrack lock"], cwd=git_project, check=True)
    fake, _ = _fake_uv({(): 0})
    monkeypatch.setattr(P, "_uv", fake)
    monkeypatch.setattr(P, "_observed_packages", lambda: INSTALLED)
    assert P.environment_state()["packages_observed"] == INSTALLED


def test_package_name_normalizes_freeze_and_drift_lines():
    assert P._package_name("~ nvidia-cusparselt-cu13==0.8.1") == "nvidia-cusparselt-cu13"
    assert P._package_name("Nvidia_Cusparselt.cu13==0.8.0") == "nvidia-cusparselt-cu13"
    assert P._package_name("- pytest==8.0") == "pytest"
    assert P._package_name("demo @ file:///src/demo") == "demo"


def test_check_error_is_not_drift(git_project, monkeypatch):
    fake, _ = _fake_uv({(): 2})
    monkeypatch.setattr(P, "_uv", fake)
    env = P.environment_state()
    assert env["env_synced"] is None and env["sync_drift"] is None
    assert env["packages_observed"]


def test_uv_missing_is_unverified(git_project, monkeypatch):
    monkeypatch.setattr(P, "_uv", lambda *a, env=None: None)
    env = P.environment_state()
    assert env["uv_version"] is None
    assert env["lock_current"] is None and env["env_synced"] is None
    assert env["packages_observed"]  # importlib.metadata fallback


def test_invocation_is_none_outside_hydra(git_project):
    """The plain-CLI stamp path has no invocation to record."""
    assert P.invocation_state() is None


def test_invocation_records_job_overrides(git_project, monkeypatch):
    """Overrides come from hydra's task list, and the command rebuilds one job."""
    from hydra.core.hydra_config import HydraConfig

    hc = SimpleNamespace(
        overrides=SimpleNamespace(task=["split.control_ratio=1.54", "seed=7"]),
        job=SimpleNamespace(config_name="config"),
        mode="RunMode.RUN",
    )
    monkeypatch.setattr(HydraConfig, "get", staticmethod(lambda: hc))
    monkeypatch.setattr(sys, "argv", ["scripts/cross_validate.py", "-m", "seed=7,8"])
    inv = P.invocation_state()
    assert inv == {
        "script": "scripts/cross_validate.py",
        "config_name": "config",
        "overrides": ["split.control_ratio=1.54", "seed=7"],
        "command": "python scripts/cross_validate.py split.control_ratio=1.54 seed=7",
        "multirun": False,
    }


HYDRA_APP = """
import hydra
from caster.provenance import RunStampCallback  # noqa: F401  (registered by config)

@hydra.main(version_base=None, config_path="configs", config_name="config")
def main(cfg):
    return {"seed": cfg.seed}

main()
"""

HYDRA_CONFIG = """
seed: 1
hydra:
  run:
    dir: runs/single
  sweep:
    dir: runs/sweep
  callbacks:
    run_stamp:
      _target_: caster.provenance.RunStampCallback
"""


def _run_hydra_app(project, *args):
    (project / "configs").mkdir(exist_ok=True)
    (project / "configs" / "config.yaml").write_text(HYDRA_CONFIG)
    (project / "app.py").write_text(HYDRA_APP)
    src = Path(P.__file__).resolve().parents[2]
    env = {**os.environ, "PYTHONPATH": str(src)}
    r = subprocess.run(
        [sys.executable, "app.py", *args], cwd=project, env=env, capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr
    return r


def test_invocation_recorded_by_a_real_hydra_run(git_project):
    _run_hydra_app(git_project, "seed=3")
    inv = read_stamp(git_project / "runs" / "single")["invocation"]
    assert inv["overrides"] == ["seed=3"]
    assert inv["config_name"] == "config"
    assert inv["multirun"] is False
    assert inv["command"] == "python app.py seed=3"


def test_multirun_records_each_job_not_the_sweep(git_project):
    """The argv trap: under -m every job sees the same argv, so the per-job
    overrides must come from hydra itself."""
    _run_hydra_app(git_project, "-m", "seed=4,5")
    sweep = git_project / "runs" / "sweep"
    seeds = [read_stamp(sweep / str(i))["invocation"] for i in (0, 1)]
    assert [i["overrides"] for i in seeds] == [["seed=4"], ["seed=5"]]
    assert all(i["multirun"] is True for i in seeds)
    assert "," not in seeds[0]["command"]


REPORTING_APP = """
import hydra
from pathlib import Path
from caster.provenance import RunStampCallback  # noqa: F401  (registered by config)

@hydra.main(version_base=None, config_path="configs", config_name="config")
def main(cfg):
    # Refresh a tracked report outside the run dir, as a publication script does.
    Path(hydra.utils.get_original_cwd(), "reports", "table.csv").write_text("x\\n2\\n")

main()
"""


def _committed_reporting_app(project):
    """A Hydra app that overwrites the tracked reports/table.csv, all committed."""
    (project / "configs").mkdir()
    (project / "configs" / "config.yaml").write_text(HYDRA_CONFIG)
    (project / "reports").mkdir()
    (project / "reports" / "table.csv").write_text("x\n1\n")
    (project / "app.py").write_text(REPORTING_APP)
    subprocess.run(["git", "add", "-A"], cwd=project, check=True)
    subprocess.run(["git", "commit", "-qm", "app"], cwd=project, check=True)


def _run_app(project):
    src = Path(P.__file__).resolve().parents[2]
    env = {**os.environ, "PYTHONPATH": str(src)}
    r = subprocess.run(
        [sys.executable, "app.py"], cwd=project, env=env, capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr
    return read_stamp(project / "runs" / "single")["git"]


def test_run_from_clean_tree_is_not_dirtied_by_its_own_report_writes(git_project):
    """The stamp records the tree the run started from, not what it left behind."""
    _committed_reporting_app(git_project)
    git = _run_app(git_project)
    assert P.git_state()["dirty"] is True  # the report write is visible afterwards...
    assert git["dirty"] is False  # ...but the run started clean


def test_run_from_uncommitted_code_change_is_dirty(git_project):
    _committed_reporting_app(git_project)
    (git_project / "app.py").write_text(REPORTING_APP + "# uncommitted\n")
    assert _run_app(git_project)["dirty"] is True


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
