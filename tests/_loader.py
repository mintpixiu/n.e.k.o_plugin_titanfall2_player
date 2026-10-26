"""Load the plugin's modules under a throwaway package for offline tests.

Core policy/vision/service tests must not need the N.E.K.O SDK (so they also run
in a plain Python checkout).  Executing ``__init__.py`` would import the SDK, so
we create a minimal package object and load the sibling modules by file path;
their relative imports keep working.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "_titanfall2_player_test"
_LOADED: dict[str, Any] = {}


def _ensure_package() -> None:
    if PACKAGE in sys.modules:
        return
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(ROOT)]  # type: ignore[attr-defined]
    package.__package__ = PACKAGE
    sys.modules[PACKAGE] = package


def load(name: str) -> Any:
    """Import one plugin module as ``<PACKAGE>.<name>``."""
    if name in _LOADED:
        return _LOADED[name]
    _ensure_package()
    module_name = f"{PACKAGE}.{name}"
    path = ROOT / f"{name}.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    _LOADED[name] = module
    return module


# Order matters only for explicit imports inside the modules; each module uses
# relative imports, and Python will call back into this package for them.
for _name in (
    "catalog",
    "vision",
    "hud",
    "policy",
    "input",
    "dxgi_capture",
    "capture",
    "discovery",
):
    load(_name)

service = load("service")
catalog = load("catalog")
policy = load("policy")
vision = load("vision")
hud = load("hud")
discovery = load("discovery")
input_mod = load("input")
capture = load("capture")
