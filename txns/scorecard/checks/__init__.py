"""One scorecard check per module; every module here is imported by the scorecard."""

import importlib
import pkgutil


def load_all() -> None:
    for mod in sorted(m.name for m in pkgutil.iter_modules(__path__)):
        importlib.import_module(f"{__name__}.{mod}")
