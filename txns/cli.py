"""CLI: maps commands to modules and outcomes to exit codes (FR-A1, FR-A4).

Primary test seam: `main(argv, today=..., env=..., cwd=..., stdout=..., stderr=...)`
runs a command in-process with an injected clock, environment and working folder.
`author` also takes `transport=` (test seam 2): the LLM connection, a scripted fake in tests,
and `sleep=`: how to wait between LLM retries (tests record the waits instead).

Adding a command: create `txns/commands/<name>.py` with `add_arguments(parser)`
and `run(args, rt) -> int`, then add it to COMMANDS below.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, TextIO

from txns import __version__
from txns.commands import Runtime
from txns.commands import author as author_cmd
from txns.commands import generate as generate_cmd
from txns.errors import ExitCode, TxnsError

COMMANDS = {
    "author": (author_cmd, "read the ledgers, draft the catalog and item text (promotion comes later)"),
    "generate": (generate_cmd, "write a CSV and run.json from the latest bundle"),
}


class _UsageError(Exception):
    pass


class _ExitRequest(Exception):
    def __init__(self, status: int):
        self.status = status


class _Parser(argparse.ArgumentParser):
    """argparse that raises instead of calling sys.exit inside main()."""

    def error(self, message: str):
        raise _UsageError(f"{self.prog}: {message}")

    def exit(self, status: int = 0, message: str | None = None):
        if message:
            raise _UsageError(message.strip())
        raise _ExitRequest(status)


def _parser() -> _Parser:
    p = _Parser(prog="txns", description="Realistic, deterministic spend-ledger CSV generator.")
    p.add_argument("--version", action="version", version=f"txns {__version__}")
    sub = p.add_subparsers(dest="command", metavar="COMMAND", parser_class=_Parser)
    for name, (module, help_text) in COMMANDS.items():
        module.add_arguments(sub.add_parser(name, help=help_text, description=help_text))
    return p


def main(
    argv: Sequence[str] | None = None,
    *,
    today: date | None = None,
    env: Mapping[str, str] | None = None,
    cwd: Path | str | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    transport: Any = None,
    sleep: Callable[[float], None] | None = None,
) -> int:
    stdout = stdout if stdout is not None else sys.stdout
    stderr = stderr if stderr is not None else sys.stderr
    rt = Runtime(
        today=today if today is not None else date.today(),
        env=dict(env) if env is not None else dict(os.environ),
        cwd=Path(cwd) if cwd is not None else Path.cwd(),
        stdout=stdout,
        stderr=stderr,
        transport=transport,
        **({"sleep": sleep} if sleep is not None else {}),
    )
    parser = _parser()
    try:
        # argparse prints help/version to sys.stdout; route it to the injected stream.
        old_stdout, sys.stdout = sys.stdout, stdout
        try:
            args = parser.parse_args(list(argv) if argv is not None else sys.argv[1:])
        finally:
            sys.stdout = old_stdout
    except _ExitRequest as req:
        return req.status
    except _UsageError as exc:
        print(parser.format_usage().rstrip(), file=stderr)
        print(f"error: {exc}", file=stderr)
        return ExitCode.MISSING_INPUT
    if not args.command:
        print(parser.format_help().rstrip(), file=stderr)
        return ExitCode.MISSING_INPUT
    module, _ = COMMANDS[args.command]
    try:
        return int(module.run(args, rt))
    except TxnsError as exc:
        print(f"error: {exc}", file=stderr)
        return int(exc.exit_code)


def run() -> None:
    """Console-script entry point (`txns`)."""
    sys.exit(main())
