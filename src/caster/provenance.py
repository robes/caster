"""The run-stamp.

Writes `_provenance.json` into a run directory: the git commit, the actual
environment, a completion marker, and the run's headline stats. This is the one
piece of Caster that no library in the stack supplies — the link between a
specific run directory and the commit + environment that produced it.

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
hash and the sync mode (the strong claims) alongside `uv pip freeze` (the
tripwire that catches drift the lock cannot see).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
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


def git_state() -> dict[str, Any]:
    """Capture commit, branch, and dirty flag. Values are None when unavailable
    (e.g. running from a release bundle that is not a git checkout)."""
    commit = _git("rev-parse", "HEAD")
    if commit is None:
        return {"commit": None, "branch": None, "dirty": None}
    status = _git("status", "--porcelain", "--untracked-files=no")
    return {
        "commit": commit,
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(status) if status is not None else None,
    }


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


def _sync_mode() -> str:
    """How the environment was established, as reported by the launch path.

    This cannot be observed from inside the process after the fact — by the time
    Python is running you cannot tell how the venv was created. Caster's `uv
    sync --frozen` / `--locked` entry points set CASTER_SYNC_MODE; absent that,
    the honest default is 'unlocked' (a plain run that may have drifted).
    """
    return os.environ.get("CASTER_SYNC_MODE", "unlocked")


def _observed_packages() -> list[str]:
    """Ground-truth snapshot of what is actually importable.

    Prefer `uv pip freeze`; fall back to importlib.metadata if uv isn't the
    runner. This is the tripwire that catches what the lock hash cannot see — an
    ad-hoc install into the venv, a stale lock, a run outside the managed env.
    """
    try:
        r = subprocess.run(
            ["uv", "pip", "freeze"],
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
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "uv_lock_sha256": _uv_lock_sha256(),
        "sync_mode": _sync_mode(),
        "packages_observed": _observed_packages(),
    }


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# writing the stamp
# ---------------------------------------------------------------------------


def write_stamp(run_dir: str | Path, stats: dict | None = None) -> Path:
    """Write `_provenance.json` into run_dir and return its path.

    The presence of this file is the completion signal: it is written only on a
    successful finish, so its absence means the run did not complete.
    """
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    # Record the run dir relative to cwd so the stamp is self-locating and
    # portable if the tree moves; fall back to the given path if it's not under
    # cwd (e.g. an absolute path elsewhere).
    try:
        rel_dir = run_dir.resolve().relative_to(Path.cwd().resolve())
    except ValueError:
        rel_dir = run_dir
    stamp = {
        "git": git_state(),
        "environment": environment_state(),
        "completed": True,
        "created_at": _utc_now_iso(),
        "run_dir": str(rel_dir),
        "stats": stats or {},
    }
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
    with no stamp — exactly the failure signal Caster relies on. Headline stats
    are taken from the job's return value when it is a dict (or a dict under a
    'stats' key), so a step that returns metrics gets them recorded with no extra
    wiring.
    """

    def on_job_end(self, config, job_return, **kwargs) -> None:
        # Resolve the run directory from the Hydra config without importing
        # Hydra at module load time (keeps this file usable as a plain CLI too).
        run_dir = self._resolve_output_dir(config)
        stats = self._extract_stats(job_return)
        try:
            write_stamp(run_dir, stats=stats)
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
    def on_job_start(self, config, **kwargs) -> None: ...

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

    path = write_stamp(args.run_dir, stats=_parse_stats(args.stats))
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
