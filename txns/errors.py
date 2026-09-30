"""Exit codes (FR-A4) and the exception that carries one up to the CLI."""

from enum import IntEnum


class ExitCode(IntEnum):
    OK = 0
    SCORECARD_FAILED = 1  # CSV still written
    MISSING_INPUT = 2  # missing/invalid config, missing or unknown bundle
    LLM_UNREACHABLE = 3
    BUNDLE_INVALID = 4
    GAPS_IMPOSSIBLE = 5
    TARGET_UNSATISFIABLE = 6


class TxnsError(Exception):
    """A failure that maps to a non-zero exit code. The CLI prints the message."""

    exit_code: ExitCode = ExitCode.MISSING_INPUT

    def __init__(self, message: str, exit_code: ExitCode | None = None):
        super().__init__(message)
        if exit_code is not None:
            self.exit_code = exit_code


class MissingInput(TxnsError):
    exit_code = ExitCode.MISSING_INPUT


class LLMUnreachable(TxnsError):
    exit_code = ExitCode.LLM_UNREACHABLE


class BundleInvalid(TxnsError):
    exit_code = ExitCode.BUNDLE_INVALID


class GapsImpossible(TxnsError):
    exit_code = ExitCode.GAPS_IMPOSSIBLE


class TargetUnsatisfiable(TxnsError):
    exit_code = ExitCode.TARGET_UNSATISFIABLE
