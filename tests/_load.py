"""Load one of the add-on's pure modules by path, without importing the add-on.

The repo root is the add-on package itself, and its ``__init__.py`` imports
``bpy``, so ``import framo_bridge.control.scene_spec`` cannot work outside
Blender. The pure modules import nothing from the add-on, which is what lets
them be loaded on their own here.
"""

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def load_module(relative_path):
    path = REPO / relative_path
    name = "framo_pure_" + relative_path.replace("/", "_").removesuffix(".py")
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
