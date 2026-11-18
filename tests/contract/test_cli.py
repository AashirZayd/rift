"""CLI contract tests for the M0 surface: `rift --help` and `rift version [--json]`."""

import json
import runpy
import subprocess
import sys

import pytest
import typer
from typer.testing import CliRunner

import rift
from rift.cli.app import app

runner = CliRunner()


def _run_rift(*args: str) -> subprocess.CompletedProcess[str]:
    """Run the real CLI in a subprocess so stdout/stderr are genuinely separate streams."""
    return subprocess.run(
        [sys.executable, "-m", "rift", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )


def test_help_exits_zero() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "version" in result.output


def test_help_lists_only_m0_commands() -> None:
    # typer >=0.26 vendors click, so the group is typer's own Group type, not click.Group.
    group = typer.main.get_command(app)
    commands = getattr(group, "commands", None)
    assert isinstance(commands, dict)
    assert sorted(commands) == ["version"]


def test_version_human_output() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.output == f"rift {rift.__version__}\n"


def test_version_json_in_process() -> None:
    result = runner.invoke(app, ["version", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output) == {"name": "rift", "version": rift.__version__}


def test_help_subprocess_exits_zero() -> None:
    proc = _run_rift("--help")
    assert proc.returncode == 0, proc.stderr
    assert "version" in proc.stdout


def test_version_json_stdout_is_exactly_one_json_document() -> None:
    proc = _run_rift("version", "--json")
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""
    # raw_decode consumes exactly one document; nothing but the trailing newline may follow.
    document, end = json.JSONDecoder().raw_decode(proc.stdout)
    assert proc.stdout[end:] == "\n"
    assert document == {"name": "rift", "version": rift.__version__}


def test_version_json_is_canonical() -> None:
    proc = _run_rift("version", "--json")
    assert proc.stdout == '{"name":"rift","version":"' + rift.__version__ + '"}\n'


def test_python_dash_m_entry_point(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["rift", "version"])
    with pytest.raises(SystemExit) as exc_info:
        runpy.run_module("rift", run_name="__main__")
    assert exc_info.value.code == 0
    assert capsys.readouterr().out == f"rift {rift.__version__}\n"
