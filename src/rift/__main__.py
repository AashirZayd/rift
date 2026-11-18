"""Allow `python -m rift`."""

from rift.cli.app import app

if __name__ == "__main__":
    app(prog_name="rift")
