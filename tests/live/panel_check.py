"""Draw the panel against the RNA-checking stub, on a port of its own, so it runs next to a live Bridge.

    blender -b --factory-startup --python-exit-code 1 --python tests/live/panel_check.py
"""

import os
import shutil
import sys
import tempfile

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "tests", "headless"))

ROOT = tempfile.mkdtemp(prefix="framo_panel_")
PKG = os.path.join(ROOT, "framo_bridge")
os.mkdir(PKG)
shutil.copy(os.path.join(REPO, "__init__.py"), PKG)
for entry in ("control", "core", "processing", "services", "ui", "utils", "icons"):
    shutil.copytree(os.path.join(REPO, entry), os.path.join(PKG, entry), ignore=shutil.ignore_patterns("__pycache__"))
sys.path.insert(0, ROOT)

from framo_bridge.services import request_guard  # noqa: E402

request_guard.PORT = 18081

import framo_bridge  # noqa: E402
from framo_bridge.services import framo_collection, live_watch  # noqa: E402
from framo_bridge.ui.panels import FRAMO_PT_export_panel  # noqa: E402
from stub_layout import exercise_panel  # noqa: E402

framo_bridge.register()
states = {}

# No collection in the scene yet: the panel offers to make it.
log = exercise_panel(FRAMO_PT_export_panel, bpy.context)
states["no collection"] = any("framo.create_collection" in str(entry) for entry in log)

collection = framo_collection.ensure(bpy.context.scene)
collection.objects.link(bpy.data.objects["Cube"])
log = exercise_panel(FRAMO_PT_export_panel, bpy.context)
states["with an object"] = not any("framo.create_collection" in str(entry) for entry in log)

live_watch._following = True
live_watch._status["error"] = "boom"
log = exercise_panel(FRAMO_PT_export_panel, bpy.context)
states["following, with an error"] = bool(log)
live_watch._following = False

print("PANEL", states, flush=True)
framo_bridge.unregister()
if not all(states.values()):
    raise SystemExit(1)
