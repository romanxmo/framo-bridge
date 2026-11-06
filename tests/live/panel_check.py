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
from framo_bridge.services import framo_collection, live_watch, preview_server  # noqa: E402
from framo_bridge.ui.panels import FRAMO_PT_agents, FRAMO_PT_export_panel, FRAMO_PT_settings  # noqa: E402
from stub_layout import exercise_panel  # noqa: E402

# Another Blender holds the port: this one must say so, then take over when it lets go.
import socket  # noqa: E402

other = socket.socket()
other.bind(("localhost", 18081))
other.listen()

framo_bridge.register()
states = {}
states["a taken port is not taken again"] = preview_server.server_instance is None
log = exercise_panel(FRAMO_PT_export_panel, bpy.context)
states["the panel says who has it"] = any("Another Blender" in str(entry) for entry in log)
other.close()
live_watch._next_claim = 0.0
live_watch._claim_server()
states["it takes over when the port frees"] = preview_server.server_instance is not None

# Something selected and not in the collection: the panel offers to add it.
bpy.data.objects["Cube"].select_set(True)
log = exercise_panel(FRAMO_PT_export_panel, bpy.context)
states["offers to add the selection"] = any("framo.add_selected" in str(entry) for entry in log)

bpy.ops.framo.add_selected()
states["adds it"] = bpy.data.objects["Cube"] in set(framo_collection.find().all_objects)
log = exercise_panel(FRAMO_PT_export_panel, bpy.context)
states["then only offers to send"] = not any("framo.add_selected" in str(entry) for entry in log)
states["Send to Framo is there"] = any("framo.export_to_web" in str(entry) for entry in log)
states["settings draw"] = bool(exercise_panel(FRAMO_PT_settings, bpy.context))
states["agents draw"] = bool(exercise_panel(FRAMO_PT_agents, bpy.context))

live_watch._following = True
live_watch._status["error"] = "boom"
log = exercise_panel(FRAMO_PT_export_panel, bpy.context)
states["following, with an error"] = bool(log)
states["nothing to press while following"] = not any("framo.export_to_web" in str(entry) for entry in log)
live_watch._following = False

print("PANEL", states, flush=True)
framo_bridge.unregister()
if not all(states.values()):
    raise SystemExit(1)
