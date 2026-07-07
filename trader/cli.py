"""`trader` CLI — thin dispatcher.

Subcommands are discovered from the ``trader.cli_cmds`` package: every
module in it must expose ``configure(subparsers)`` which registers one (or
more) subparsers and sets a ``func`` default on each. Later milestones add
commands by dropping in new modules — no edits to this file.
"""

from __future__ import annotations

import argparse
import importlib
import pkgutil
import sys

import trader.cli_cmds as cli_cmds


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trader", description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)
    for info in sorted(pkgutil.iter_modules(cli_cmds.__path__), key=lambda m: m.name):
        module = importlib.import_module(f"{cli_cmds.__name__}.{info.name}")
        configure = getattr(module, "configure", None)
        if configure is not None:
            configure(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        print("no handler for command", file=sys.stderr)
        return 2
    return int(func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
