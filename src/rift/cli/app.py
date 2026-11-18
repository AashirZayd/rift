"""Typer application: the `rift` command-line entry point.

M0 exposes only `--help` and `version`. Commands are thin: parse, call one service, render.
"""

import json
from typing import Annotated

import typer

from rift.services.version import version_info

app = typer.Typer(
    name="rift",
    help="RIFT: proof-carrying changes for modern software.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)


@app.callback()
def main() -> None:
    """RIFT: proof-carrying changes for modern software."""


@app.command()
def version(
    as_json: Annotated[
        bool, typer.Option("--json", help="Print exactly one JSON document to stdout.")
    ] = False,
) -> None:
    """Print the RIFT version."""
    info = version_info()
    if as_json:
        payload = {"name": info.name, "version": info.version}
        typer.echo(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    else:
        typer.echo(f"{info.name} {info.version}")
