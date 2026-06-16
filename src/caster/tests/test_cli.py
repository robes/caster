"""The unified `caster` dispatcher routes groups to the module CLIs."""

from __future__ import annotations

import pytest

from caster import cli


def test_no_args_shows_usage(capsys):
    rc = cli.main([])
    assert rc == 1
    assert "usage: caster" in capsys.readouterr().out


def test_help_flag_shows_usage(capsys):
    rc = cli.main(["-h"])
    assert rc == 0
    assert "groups:" in capsys.readouterr().out


def test_unknown_group_errors(capsys):
    rc = cli.main(["bogus"])
    assert rc == 2
    assert "unknown group" in capsys.readouterr().err


def test_dispatches_to_data_validate(git_project, capsys):
    # routes to caster.data main(); validate on the empty fixture is clean (rc 0)
    rc = cli.main(["data", "validate"])
    assert rc == 0


def test_dispatches_to_package_lone_command(git_project):
    # a lone command: a bad config still reaches package.main and raises there
    with pytest.raises(FileNotFoundError):
        cli.main(["package", str(git_project / "nope.yaml")])
