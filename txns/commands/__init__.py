"""One module per `txns` command. Each exposes `add_arguments(parser)` and `run(args, rt) -> int`."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping, TextIO


@dataclass(frozen=True)
class Runtime:
    """What a command may read from the outside world; injected by tests (primary seam)."""

    today: date
    env: Mapping[str, str]
    cwd: Path
    stdout: TextIO
    stderr: TextIO
    transport: Any = None  # an llm.Transport injected by tests (seam 2); None = the real connection

    def out(self, msg: str) -> None:
        print(msg, file=self.stdout)

    def warn(self, msg: str) -> None:
        print(f"warning: {msg}", file=self.stderr)

    def path(self, p: str | Path) -> Path:
        """Resolve a user-given path against the working folder."""
        p = Path(p)
        return p if p.is_absolute() else self.cwd / p
