"""Interpreter and generator versions recorded in run.json and bundle manifests (ADR 0007)."""

import platform

from txns import __version__


def interpreter() -> str:
    """e.g. 'CPython 3.12.3'. Compared verbatim with the bundle manifest's `interpreter`."""
    return f"{platform.python_implementation()} {platform.python_version()}"


def generator() -> str:
    return f"txns {__version__}"
