"""Shared CLI helpers for Caster's commands.

Agent-friendliness: every command that supports ``--json`` emits a single JSON
object on stdout and *nothing else* there (all human/informational text goes to
stderr), so an agent can pipe stdout straight to a parser. The envelope shape is
the same across commands::

    {"ok": bool, "command": str, "result": {...}, "problems": [...]}

so an agent learns one shape. ``ok`` mirrors the process exit code (0 ⇔ ok True).
"""

from __future__ import annotations

import json
import sys


def emit_json(
    command: str, ok: bool, result: dict | None = None, problems: list | None = None
) -> None:
    """Print the standard envelope as the only thing on stdout."""
    obj = {
        "ok": ok,
        "command": command,
        "result": result or {},
        "problems": problems or [],
    }
    print(json.dumps(obj, indent=2))


def info(*args, **kwargs) -> None:
    """Human/informational output — always to stderr, so stdout stays clean for
    results (and is pure JSON under ``--json``)."""
    kwargs.setdefault("file", sys.stderr)
    print(*args, **kwargs)
