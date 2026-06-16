"""The unified `caster` command — a thin dispatcher over the module CLIs.

Each `caster.<module>` already exposes `main(argv) -> int` with its own
subcommands; this just routes the first token to the right one. The modules are
still runnable directly as `python -m caster.<module>`, which this does not
change.

Shape (module-grouped):
    caster provenance stamp <dir> [--stats k=v]
    caster provenance ledger [--json] [-o FILE]
    caster data validate [--json]
    caster data emit <name> [--json]
    caster package <release_config.yaml> [--json] [--dry-run]
"""

from __future__ import annotations

import sys

# group token -> the module main() that owns its subcommands.
_GROUPS = {
    "provenance": "caster.provenance",
    "data": "caster.data",
    "package": "caster.package",
}

_USAGE = """\
caster — reproducible-ML project tooling

usage: caster <group> <command> [args]

groups:
  provenance   run-stamp tooling      (stamp, ledger)
  data         data-source tooling    (validate, emit)
  package      build a release bundle

Run `caster <group> -h` for a group's commands, e.g. `caster data -h`.
Each group is also runnable as `python -m caster.<module>`.
"""


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(_USAGE)
        return 0 if argv else 1
    group = argv[0]
    if group not in _GROUPS:
        print(f"caster: unknown group '{group}'\n", file=sys.stderr)
        print(_USAGE, file=sys.stderr)
        return 2
    # import the owning module lazily and hand it the remaining args; its own
    # parser produces the group-level help and errors.
    import importlib

    module = importlib.import_module(_GROUPS[group])
    return module.main(argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
