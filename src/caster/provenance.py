"""The run-stamp.

Writes `_provenance.json` into a run directory: the git commit, the actual
environment, what was invoked, a completion marker, and the run's headline
stats. This is the one piece of Caster that no library in the stack supplies —
the link between a specific run directory and the commit + environment that
produced it.

Two entry points:

  * `RunStampCallback` — a Hydra callback. Register it once in the base config
    and every Hydra run is stamped automatically (start fields on job start, the
    completion marker + stats on job end). Nothing to remember at run time.

  * `python -m caster.provenance stamp <dir> [--stats k=v ...]` — for pipeline
    stages that do not go through Hydra (e.g. a DVC stage command). Appending it
    after the real command also gives the completion marker DVC's
    abort-on-failure semantics: a failed first command never reaches the stamp.

The environment is recorded as a *binding to the lockfile* plus an observed
snapshot, not just a package dump. `uv.lock` is a declarative resolution of the
project's constraints; it answers "what would a clean install produce?" and is
the wrong object to overwrite with run state. So the stamp records the lock's
hash and uv's own verdict on whether the running environment matches it (the
strong claims), and stores observed package versions only where the lock cannot
supply them.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from caster._cli import emit_json, info

STAMP_FILENAME = "_provenance.json"
_GIT_TIMEOUT = 10


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------


def _git(*args: str) -> str | None:
    """Run a git command; return stripped stdout, or None on any failure."""
    try:
        r = subprocess.run(
            ["git", *args], capture_output=True, text=True, check=False, timeout=_GIT_TIMEOUT
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


# Run directories are written *during* the run, so by the time the stamp is
# taken the job's own outputs are sitting there untracked. Excluding the output
# roots keeps `dirty` about the code, while still seeing an untracked source file
# a run imported — which plain `--untracked-files=no` would miss.
_OUTPUT_DIRS = ("outputs", "multirun")
_DIRTY_FILES_MAX = 50


def _load_yaml(path: Path) -> Any:
    """Parsed YAML, or None if absent or unreadable (which must not fail a stamp)."""
    try:
        import yaml

        return yaml.safe_load(path.read_text()) if path.is_file() else None
    except Exception:
        return None


def _declared_paths(entries: Any, wdir: Path) -> list[Path]:
    """Root-relative paths of a dvc.yaml `deps`/`outs`/`metrics`/`plots` list.
    Templated (`${...}`) entries are skipped: they are not resolved here."""
    out = []
    for e in entries if isinstance(entries, list) else []:
        p = next(iter(e), None) if isinstance(e, dict) else e
        if isinstance(p, str) and "${" not in p:
            out.append(Path(os.path.normpath(wdir / p)))
    return out


def _dvc_spec() -> tuple[Path, dict] | None:
    """The repository root and its parsed root `dvc.yaml`, if there is one."""
    top = _git("rev-parse", "--show-toplevel")
    spec = _load_yaml(Path(top) / "dvc.yaml") if top is not None else None
    return (Path(top), spec) if isinstance(spec, dict) else None


def _stages(spec: dict) -> dict[str, dict]:
    stages = spec.get("stages")
    if not isinstance(stages, dict):
        return {}
    return {name: s for name, s in stages.items() if isinstance(s, dict)}


def _pipeline_paths() -> list[str]:
    """Files `dvc repro` writes into the working tree, as root-relative paths.

    DVC rewrites `dvc.lock` after every stage, and a stage's `outs`, `metrics`
    and `plots` (with the `.gitignore` DVC keeps beside a cached out) land in the
    tree before the next stage starts. Without excluding them, every stage after
    the first in a repro would stamp itself dirty. Only paths the root
    `dvc.yaml` declares are excluded, so a hand-edited tracked file elsewhere
    still counts. Templated (`${...}`) and `foreach`/`matrix` paths are not
    resolved and stay in the check.
    """
    found = _dvc_spec()
    if found is None:
        return []
    _, spec = found
    paths = ["dvc.lock"]
    for stage in _stages(spec).values():
        wdir = Path(stage.get("wdir", "."))
        for p in _declared_paths(stage.get("outs"), wdir):
            paths += [p.as_posix(), (p.parent / ".gitignore").as_posix()]
        for key in ("metrics", "plots"):
            paths += [p.as_posix() for p in _declared_paths(stage.get(key), wdir)]
    for key in ("metrics", "plots"):
        paths += [p.as_posix() for p in _declared_paths(spec.get(key), Path("."))]
    return paths


def _dirty_pathspecs(exclude: tuple[str, ...] = ()) -> list[str]:
    """Pathspecs limiting the dirty check to everything but the output roots
    and the files the DVC pipeline writes.

    `:/` anchors at the repository root so the check does not depend on the
    working directory; `top` makes each exclusion root-relative too.
    """
    roots = dict.fromkeys((*_OUTPUT_DIRS, *exclude, *_pipeline_paths()))  # ordered, de-duplicated
    return [":/", *(f":(exclude,top,literal){root}" for root in roots)]


def git_state(exclude: tuple[str, ...] = ()) -> dict[str, Any]:
    """Capture commit, branch, and dirty flag. Values are None when unavailable
    (e.g. running from a release bundle that is not a git checkout).

    `exclude` names extra output roots to keep out of the dirty check, for a
    project whose run directories are not under `outputs/` or `multirun/`. The
    files `dvc.yaml` declares as pipeline outputs are always excluded.

    `dirty_files` (only when dirty) names what differs, so a dirty run can be
    diagnosed later. When the working `dvc.lock` is not the commit's, the fields
    that pin the pipeline data instead are added — see `_dvc_lock_state`.
    """
    commit = _git("rev-parse", "HEAD")
    if commit is None:
        return {"commit": None, "branch": None, "dirty": None}
    specs = _dirty_pathspecs(exclude)
    changed = _git("diff", "--name-only", "HEAD", "--", *specs)
    untracked = _git("ls-files", "--others", "--exclude-standard", "--", *specs)
    state: dict[str, Any] = {
        "commit": commit,
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": None,
    }
    if changed is not None and untracked is not None:
        files = [f for f in (*changed.splitlines(), *untracked.splitlines()) if f]
        state["dirty"] = bool(files)
        if files:
            extra = len(files) - _DIRTY_FILES_MAX
            state["dirty_files"] = files[:_DIRTY_FILES_MAX] + (
                [f"… {extra} more"] if extra > 0 else []
            )
    state.update(_dvc_lock_state())
    return state


def _dvc_lock_state() -> dict[str, Any]:
    """What pins the pipeline data when the commit's `dvc.lock` does not.

    The dirty check ignores `dvc.lock` because `dvc repro` rewrites it between
    stages, so a run can start from a working lock that is not the commit's.
    Nothing is recorded when the lock is the commit's (the commit pins it).
    Otherwise:

      * inside `dvc repro` (DVC sets `DVC_STAGE`): `dvc_stage` and `dvc_inputs`,
        the DVC hash of each of the stage's deps that another stage produced,
        read from the working lock. These are content addresses in the DVC
        cache, so they identify the data read whatever is committed later —
        the lock this stage saw is rewritten when it finishes and never
        committed as such;
      * any other run: `dvc_lock`, the git object id of the working lock. That
        lock is normally the one committed next, and
        `git log --all --find-object=<id>` finds it.
    """
    found = _dvc_spec()
    if found is None:
        return {}
    top, spec = found
    lock_path = top / "dvc.lock"
    if not lock_path.is_file():
        return {}
    blob = _git("hash-object", "--", str(lock_path))
    if blob is None or blob == _git("rev-parse", "HEAD:dvc.lock"):
        return {}
    stage_name = os.environ.get("DVC_STAGE", "").removeprefix("dvc.yaml:")
    stage = _stages(spec).get(stage_name)
    if stage is None:
        return {"dvc_lock": blob}
    lock = _load_yaml(lock_path) or {}
    produced: dict[str, str] = {}
    for name, entry in (lock.get("stages") or {}).items():
        wdir = Path((_stages(spec).get(name) or {}).get("wdir", "."))
        for out in (entry or {}).get("outs") or []:
            if isinstance(out, dict) and out.get("path") and out.get("md5"):
                produced[Path(os.path.normpath(wdir / out["path"])).as_posix()] = out["md5"]
    deps = _declared_paths(stage.get("deps"), Path(stage.get("wdir", ".")))
    inputs = {p.as_posix(): produced[p.as_posix()] for p in deps if p.as_posix() in produced}
    return {"dvc_stage": stage_name, "dvc_inputs": inputs}


# ---------------------------------------------------------------------------
# environment
# ---------------------------------------------------------------------------


def _uv_lock_sha256() -> str | None:
    """SHA-256 of the committed uv.lock, or None if there isn't one.

    Ties the run to the exact lock it was meant to use. Because uv.lock is
    committed, the git commit already pins it; this hash is a cheap cross-check
    that catches a lock edited but not committed.
    """
    lock = Path("uv.lock")
    return hashlib.sha256(lock.read_bytes()).hexdigest() if lock.exists() else None


# Extras selections tried, in order, when checking the venv against the lock. The
# check is exact, so a selection that differs from what was synced fails in both
# directions (missing or extraneous packages): a wrong candidate can cost a
# fallback to the package snapshot, never a false pass.
_SYNC_SELECTIONS: tuple[tuple[str, ...], ...] = ((), ("--all-extras",))


def _uv(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess | None:
    """Run a uv command offline; None if uv is missing or does not answer."""
    try:
        return subprocess.run(
            ["uv", *args, "--offline"],
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT,
            env=env,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None


def _check_result(r: subprocess.CompletedProcess | None) -> bool | None:
    """uv's --check exit codes: 0 up to date, 1 out of date, anything else an error."""
    if r is None or r.returncode not in (0, 1):
        return None
    return r.returncode == 0


def _uv_version() -> str | None:
    r = _uv("--version")
    return r.stdout.split()[1] if r is not None and r.returncode == 0 else None


def lock_check() -> dict[str, Any]:
    """Verify the environment against uv.lock, instead of trusting the launch path.

    How the venv was created cannot be observed from inside the process, but its
    result can. Two read-only checks:

      * `lock_current` — `uv lock --check`: uv.lock is up to date with
        pyproject.toml (what `uv sync --locked` would have asserted).
      * `env_synced` — `uv sync --frozen --check`: the running interpreter's
        environment matches uv.lock exactly, for the extras in `sync_selection`
        (what `uv sync --frozen` would have produced). Extraneous packages fail
        it, so an ad-hoc install into the venv is caught.

    Each is None when the check could not run (uv missing, no project, error).
    When no selection matched, `sync_selection` is the nearest one and
    `sync_drift` holds uv's description of the difference from it. Neither check
    sees a file edited inside site-packages.
    """
    # Check the environment this process is running in, not whichever one uv
    # would pick for the project.
    env = {**os.environ, "UV_PROJECT_ENVIRONMENT": sys.prefix}
    result: dict[str, Any] = {
        "lock_current": _check_result(_uv("lock", "--check")),
        "env_synced": None,
        "sync_selection": None,
        "sync_drift": None,
    }
    for selection in _SYNC_SELECTIONS:
        r = _uv("sync", "--frozen", "--check", *selection, env=env)
        synced = _check_result(r)
        if synced is None:
            return result  # an error, not drift; another selection won't fix it
        if synced:
            result.update(env_synced=True, sync_selection=list(selection), sync_drift=None)
            return result
        drift = [
            line.strip() for line in r.stderr.splitlines() if line.startswith((" +", " -", " ~"))
        ]
        # keep the nearest selection's diff; the others mostly list its extras
        if result["sync_drift"] is None or len(drift) < len(result["sync_drift"]):
            result.update(env_synced=False, sync_selection=list(selection), sync_drift=drift)
    return result


def _lock_committed() -> bool:
    """uv.lock is tracked and unmodified, so the commit alone recovers it."""
    if not _git("ls-files", "--", ":(top)uv.lock"):
        return False
    return _git("status", "--porcelain", "--", ":(top)uv.lock") == ""


def _package_name(entry: str) -> str:
    """Normalized distribution name of a freeze line or a uv drift line."""
    entry = entry.lstrip("+-~ ").removeprefix("-e ").strip()
    name = re.split(r"[=@<>!~ ;\[]", entry, maxsplit=1)[0]
    return re.sub(r"[-_.]+", "-", name).lower()


def _observed_packages() -> list[str]:
    """Ground-truth snapshot of what is actually importable.

    Prefer `uv pip freeze`; fall back to importlib.metadata if uv isn't the
    runner. This is the tripwire that catches what the lock hash cannot see — an
    ad-hoc install into the venv, a stale lock, a run outside the managed env.
    """
    try:
        r = subprocess.run(
            ["uv", "pip", "freeze", "--python", sys.executable],
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip().splitlines()
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        pass
    return sorted(
        f"{d.metadata['Name']}=={d.version}"
        for d in importlib.metadata.distributions()
        if d.metadata["Name"]
    )


def environment_state() -> dict[str, Any]:
    """The environment record.

    The package list is stored only to the extent uv.lock cannot supply it. With
    uv.lock committed and unmodified, the locked set is reconstructible from the
    lock at the commit and `sync_selection` (`uv export --frozen`, with each
    line's markers evaluated for the recorded python and platform), so:

      * verified in sync — nothing more is stored;
      * drifted — `drift_installed` holds what is actually installed for each
        package in `sync_drift` (uv's drift line names the locked version, not
        the installed one); lock + selection + this is the environment;
      * unverifiable, or the lock is not at the commit — the full
        `packages_observed` snapshot.
    """
    state = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "uv_version": _uv_version(),
        "uv_lock_sha256": _uv_lock_sha256(),
        **lock_check(),
    }
    if state["env_synced"] is None or not _lock_committed():
        state["packages_observed"] = _observed_packages()
    elif state["env_synced"] is False:
        drifted = {_package_name(line) for line in state["sync_drift"]}
        state["drift_installed"] = [p for p in _observed_packages() if _package_name(p) in drifted]
    return state


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------

_DATA_TIMEOUT = 600


def data_state() -> dict[str, Any]:
    """Whether the workspace data matches what git and DVC record (opt-in).

    The commit names a run's inputs — `.dvc` pointers, `dvc.lock`, the run dirs
    in its overrides — but cannot say the bytes on disk are those: a model
    overwritten by hand or a partial repro looks the same to git. `dvc status`
    can, by hashing the tracked data, which is why it is off by default (about
    12 s on a large project). `in_sync` is None when the check could not run.
    """
    top = _git("rev-parse", "--show-toplevel")
    try:
        r = subprocess.run(
            [sys.executable, "-m", "dvc", "status", "--json"],
            cwd=top,
            capture_output=True,
            text=True,
            check=False,
            timeout=_DATA_TIMEOUT,
        )
        status = json.loads(r.stdout or "{}") if r.returncode == 0 else None
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        status = None
    if not isinstance(status, dict):
        return {"in_sync": None}
    state: dict[str, Any] = {"in_sync": not status}
    if status:
        state["out_of_sync"] = sorted(status)[:_DIRTY_FILES_MAX]
    return state


# ---------------------------------------------------------------------------
# invocation
# ---------------------------------------------------------------------------


def invocation_state() -> dict[str, Any] | None:
    """What was run: the script, the config name, and this job's overrides.

    Together with the commit this is the irreducible half of the record — Hydra
    composition is a pure function of (config tree, config_name, overrides), so
    a stamp carrying these needs no `.hydra/` directory committed to say what a
    run used. Returns None outside a Hydra job (the `caster.provenance stamp`
    CLI path has no invocation to record).

    Three details matter:

      * the overrides come from `hydra.overrides.task`, never `sys.argv`: under
        `--multirun` argv holds the sweep expression and is identical for every
        job, while `task` holds the job's own resolved overrides;
      * `config_name` is recorded because `--config-name` is a CLI flag that
        never appears in the overrides, and recomposing without it is wrong;
      * `command` reproduces *this job* alone, so it carries no `-m`; the
        `multirun` flag says the job came from a sweep.
    """
    try:
        from hydra.core.hydra_config import HydraConfig

        hc = HydraConfig.get()
        overrides = [str(o) for o in hc.overrides.task]
        script = sys.argv[0]
        return {
            "script": script,
            "config_name": hc.job.config_name,
            "overrides": overrides,
            "command": shlex.join(["python", script, *overrides]),
            "multirun": str(hc.mode).endswith("MULTIRUN"),
        }
    except Exception:
        return None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# writing the stamp
# ---------------------------------------------------------------------------


def _relative_run_dir(run_dir: Path) -> Path:
    """The run dir relative to cwd, so the stamp is self-locating and portable
    if the tree moves; the given path if it's not under cwd (e.g. an absolute
    path elsewhere)."""
    try:
        return run_dir.resolve().relative_to(Path.cwd().resolve())
    except ValueError:
        return run_dir


def _run_git_state(run_dir: Path) -> dict[str, Any]:
    """`git_state` for a run. A project whose run dirs are not under outputs/
    still gets a dirty flag about its code, not about the run it is stamping."""
    rel_dir = _relative_run_dir(run_dir)
    own_root = (rel_dir.parts[0],) if not rel_dir.is_absolute() and rel_dir.parts else ()
    return git_state(exclude=own_root)


def write_stamp(
    run_dir: str | Path,
    stats: dict | None = None,
    invocation: dict | None = None,
    git: dict | None = None,
    data: dict | None = None,
) -> Path:
    """Write `_provenance.json` into run_dir and return its path.

    The presence of this file is the completion signal: it is written only on a
    successful finish, so its absence means the run did not complete.

    `git` is the state the run started from, when the caller took it then;
    without it the state is taken now. `data` is a `data_state()` result, when
    the data check was asked for.
    """
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    rel_dir = _relative_run_dir(run_dir)
    stamp = {
        "git": git if git is not None else _run_git_state(run_dir),
        "environment": environment_state(),
        "completed": True,
        "created_at": _utc_now_iso(),
        "run_dir": str(rel_dir),
        "stats": stats or {},
    }
    if invocation is not None:
        stamp["invocation"] = invocation
    if data is not None:
        stamp["data"] = data
    path = run_dir / STAMP_FILENAME
    path.write_text(json.dumps(stamp, indent=2) + "\n")
    return path


# ---------------------------------------------------------------------------
# Hydra callback
# ---------------------------------------------------------------------------


class RunStampCallback:
    """Hydra callback that stamps every run directory.

    Register once in the base config::

        hydra:
          callbacks:
            run_stamp:
              _target_: caster.provenance.RunStampCallback

    The completion marker is written in on_job_end, so a crash leaves a run dir
    with no stamp — exactly the failure signal Caster relies on. The job's
    invocation (script, config name, overrides) is recorded alongside it, so the
    stamp says what ran without anything from `.hydra/` being committed. Headline
    stats are taken from the job's return value when it is a dict (or a dict
    under a 'stats' key), so a step that returns metrics gets them recorded with
    no extra wiring.

    The git state is taken in on_job_start: the code a run executes is the tree
    it started from. A job that writes tracked files outside its run directory
    (a report it refreshes, say) would otherwise stamp itself dirty.

    `check_data: true` adds the opt-in `data_state()` check, also at job start.
    Turn it on per run for the ones that matter (a held-out eval, a publication
    script): `hydra.callbacks.run_stamp.check_data=true`.
    """

    def __init__(self, check_data: bool = False) -> None:
        self.check_data = check_data
        self._git_at_start: dict | None = None
        self._data_at_start: dict | None = None

    def on_job_start(self, config, **kwargs) -> None:
        try:
            self._git_at_start = _run_git_state(self._resolve_output_dir(config))
        except Exception:  # fall back to the state at job end
            self._git_at_start = None
        self._data_at_start = data_state() if self.check_data else None

    def on_job_end(self, config, job_return, **kwargs) -> None:
        # Resolve the run directory from the Hydra config without importing
        # Hydra at module load time (keeps this file usable as a plain CLI too).
        run_dir = self._resolve_output_dir(config)
        stats = self._extract_stats(job_return)
        git, self._git_at_start = self._git_at_start, None
        data, self._data_at_start = self._data_at_start, None
        try:
            write_stamp(run_dir, stats=stats, invocation=invocation_state(), git=git, data=data)
        except Exception as exc:  # never let stamping fail the job
            print(f"caster: run-stamp could not be written: {exc}", file=sys.stderr)
        # Announce the run directory on stdout so an agent (or script) can capture
        # the exact path this invocation produced — no need to guess "the latest"
        # run dir by globbing timestamps, which ties under concurrent/same-second
        # runs. Printed as-is (absolute) on its own greppable line.
        print(f"CASTER_RUN_DIR={run_dir}")

    # Hydra invokes the full callback protocol; define the rest as no-ops so it
    # doesn't warn about missing hooks. Stamping happens only on job end.
    def on_run_start(self, config, **kwargs) -> None: ...
    def on_run_end(self, config, **kwargs) -> None: ...
    def on_multirun_start(self, config, **kwargs) -> None: ...
    def on_multirun_end(self, config, **kwargs) -> None: ...

    @staticmethod
    def _resolve_output_dir(config) -> Path:
        try:
            from hydra.core.hydra_config import HydraConfig

            return Path(HydraConfig.get().runtime.output_dir)
        except Exception:
            # Fallback: Hydra has already chdir'd into the run dir by default.
            return Path.cwd()

    @staticmethod
    def _extract_stats(job_return) -> dict:
        ret = getattr(job_return, "return_value", None)
        if isinstance(ret, dict):
            inner = ret.get("stats")
            return inner if isinstance(inner, dict) else ret
        return {}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_stats(items: list[str] | None) -> dict:
    """Parse `--stats key=value` pairs; values are coerced to float when possible."""
    out: dict[str, Any] = {}
    for item in items or []:
        key, _, value = item.partition("=")
        try:
            out[key] = float(value)
        except ValueError:
            out[key] = value
    return out


def ledger_records(outputs_dir: str | Path = "outputs") -> list[dict]:
    """The run records behind the ledger: one dict per run stamp, sorted by
    timestamp. Shared by the Markdown view and the ``--json`` output so both
    reflect the same data."""
    outputs_dir = Path(outputs_dir)
    records: list[dict] = []
    for stamp in sorted(outputs_dir.rglob(STAMP_FILENAME)):
        try:
            d = json.loads(stamp.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        records.append(
            {
                "created_at": d.get("created_at", ""),
                "commit": (d.get("git") or {}).get("commit"),
                "stats": d.get("stats", {}),
                "run_dir": d.get("run_dir") or str(stamp.parent),
            }
        )
    records.sort(key=lambda r: r["created_at"])
    return records


def build_ledger(outputs_dir: str | Path = "outputs") -> str:
    """Regenerate a flat, human-readable experiment ledger from run stamps.

    Walks every `_provenance.json` under `outputs_dir` and emits a Markdown table
    — one row per run: timestamp, commit, stats, run dir. The stamps are the
    durable record; this is a convenience view, regenerable any time and never
    the source of truth. `dvc exp show` covers the same need for DVC-experiment
    runs; this covers plain Hydra runs in `outputs/`.

    Tolerates a null/absent commit (a run made before the first commit, or
    outside a git repo, legitimately records `commit: null`). Stats vary per
    project, so they are rendered as a compact JSON blob in one column rather
    than assumed to be fixed.
    """
    lines = ["| timestamp | commit | stats | run dir |", "|---|---|---|---|"]
    for r in ledger_records(outputs_dir):
        commit = (r["commit"] or "")[:8]
        # escape pipes so stats JSON can't break the table
        stats_cell = json.dumps(r["stats"]).replace("|", "\\|")
        commit_cell = f"`{commit}`" if commit else "—"
        lines.append(f"| {r['created_at']} | {commit_cell} | {stats_cell} | {r['run_dir']} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Caster run-stamp tooling.")
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("stamp", help="write _provenance.json into a directory")
    s.add_argument("run_dir")
    s.add_argument("--stats", nargs="*", metavar="KEY=VALUE", help="headline stats to record")
    s.add_argument(
        "--check-data", action="store_true", help="record whether data matches DVC (dvc status)"
    )
    lg = sub.add_parser("ledger", help="regenerate a Markdown experiment ledger from stamps")
    lg.add_argument(
        "--outputs-dir",
        default="outputs",
        help="directory to scan for run stamps (default: outputs)",
    )
    lg.add_argument("-o", "--output", default=None, help="write to this file instead of stdout")
    lg.add_argument("--json", action="store_true", help="machine-readable output on stdout")
    args = parser.parse_args(argv)

    if args.command == "ledger":
        if args.json:
            records = ledger_records(args.outputs_dir)
            emit_json("ledger", ok=True, result={"runs": records})
            return 0
        table = build_ledger(args.outputs_dir)
        if args.output:
            Path(args.output).write_text(table)
            info(f"wrote {args.output}")
        else:
            print(table, end="")
        return 0

    data = data_state() if args.check_data else None
    path = write_stamp(args.run_dir, stats=_parse_stats(args.stats), data=data)
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
