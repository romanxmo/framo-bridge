"""The operations a Framo agent may ask Blender for, and nothing else.

Each handler takes the command's payload (already a dict) and returns the
result dict, or raises ``BuildError`` with a message phrased for the model.
Handlers run on Blender's main thread only (see ``commands.process_pending_jobs``).

``run_python`` runs whatever Python the agent sends, with the full Blender API.
That is the point of it: modelling, materials, lighting and render settings
are far beyond what a fixed scene description can say. It is gated by the
same session-only switch as every other op, and nothing else: anything the
switch allows, the agent may do.
"""

import contextlib
import io
import math
import os
import random
import traceback

import bpy

from . import render, scene_builder
from .scene_builder import BuildError
from .scene_spec import parse_scene_spec, scene_asset_ids
from ..utils.constants import ADDON_VERSION

MAX_ADDONS = 200


def bridge_version():
    return ".".join(str(n) for n in ADDON_VERSION)


def blender_version():
    return ".".join(str(n) for n in bpy.app.version)


def _check_keys(payload, allowed):
    unknown = sorted(set(payload) - set(allowed))
    if unknown:
        raise BuildError(f"unknown payload field(s): {', '.join(unknown)}")


def _scene():
    scene = bpy.context.scene
    if scene is None:
        raise BuildError("Blender has no active scene")
    return scene


def _version_string(version):
    if isinstance(version, (tuple, list)):
        return ".".join(str(n) for n in version)
    return "" if version is None else str(version)


def list_addons(limit=MAX_ADDONS):
    """Installed add-ons and extensions, enabled ones first, capped."""
    import addon_utils

    enabled = set(bpy.context.preferences.addons.keys())
    found = []
    for module in addon_utils.modules(refresh=False):
        info = addon_utils.module_bl_info(module) if hasattr(addon_utils, "module_bl_info") else getattr(module, "bl_info", {})
        found.append({
            "module": module.__name__,
            "name": str(info.get("name") or module.__name__),
            "version": _version_string(info.get("version")),
            "enabled": module.__name__ in enabled,
        })
    found.sort(key=lambda a: (not a["enabled"], a["name"].lower()))
    return found[:limit]


def op_status(payload):
    _check_keys(payload, ())
    scene = _scene()
    users, framo = scene_builder.count_objects(scene)
    return {
        "bridgeVersion": bridge_version(),
        "blenderVersion": blender_version(),
        "fileName": os.path.basename(bpy.data.filepath) or None,
        "sceneName": scene.name,
        "userObjectCount": users,
        "framoObjectCount": framo,
        "addons": list_addons(),
    }


def op_get_scene(payload):
    _check_keys(payload, ())
    return scene_builder.read_scene(_scene())


def op_build_scene(payload):
    _check_keys(payload, ("spec", "assets"))
    parsed = parse_scene_spec(payload.get("spec"))
    if not parsed["ok"]:
        raise BuildError("the scene description is invalid:\n" + "\n".join(parsed["errors"]))
    spec = parsed["value"]
    assets = payload.get("assets", {})
    if not isinstance(assets, dict):
        raise BuildError("assets must be an object mapping catalog ids to {url, name, size}")
    result = scene_builder.build_scene(_scene(), spec, assets)
    missing = [a for a in scene_asset_ids(spec) if a not in assets]
    if missing:
        # Each affected object already carries its own warning; this names the cause once.
        result["warnings"].append(f"assets not resolved by Framo: {', '.join(missing)}")
    _undo_push("Framo: build scene")
    return result


MAX_PYTHON_OUTPUT = 20_000

# One namespace for the session, so helpers the agent defines in one call are
# there in the next. Reset when the add-on is reloaded.
_PYTHON_NAMESPACE = {}


def _python_namespace():
    if not _PYTHON_NAMESPACE:
        import bmesh
        import mathutils

        _PYTHON_NAMESPACE.update({
            "__name__": "framo_agent",
            "bpy": bpy,
            "bmesh": bmesh,
            "mathutils": mathutils,
            "Vector": mathutils.Vector,
            "Matrix": mathutils.Matrix,
            "Euler": mathutils.Euler,
            "math": math,
            "random": random,
        })
    return _PYTHON_NAMESPACE


def _clip(text):
    if len(text) <= MAX_PYTHON_OUTPUT:
        return text
    return text[:MAX_PYTHON_OUTPUT // 2] + "\n... [output cut] ...\n" + text[-MAX_PYTHON_OUTPUT // 2:]


def op_run_python(payload):
    _check_keys(payload, ("code",))
    code = payload.get("code")
    if not isinstance(code, str) or not code.strip():
        raise BuildError("code must be a non-empty string of Python")
    out = io.StringIO()
    try:
        compiled = compile(code, "<framo>", "exec")
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            exec(compiled, _python_namespace())
    except Exception:  # the agent's code failed: report it so it can fix it
        _undo_push("Framo: Python (failed)")
        raise BuildError(_clip(out.getvalue() + traceback.format_exc(limit=8)))
    _undo_push("Framo: Python")
    return {"output": _clip(out.getvalue())}


def op_screenshot(payload):
    _check_keys(payload, ("width", "shading", "camera", "collection"))
    width = payload.get("width", render.DEFAULT_SCREENSHOT_WIDTH)
    if isinstance(width, bool) or not isinstance(width, int) or not (
            render.MIN_SCREENSHOT_WIDTH <= width <= render.MAX_SCREENSHOT_WIDTH):
        raise BuildError(
            f"width must be a whole number from {render.MIN_SCREENSHOT_WIDTH} to {render.MAX_SCREENSHOT_WIDTH}")
    for key in ("camera", "collection"):
        if key in payload and (not isinstance(payload[key], str) or not payload[key]):
            raise BuildError(f"{key} must be the name of a {key} in the scene")
    return render.screenshot(_scene(), width, payload.get("shading", "solid"),
                             payload.get("camera"), payload.get("collection"))


def op_render_start(payload):
    _check_keys(payload, ("engine",))
    return render.start_render(_scene(), payload.get("engine", "workbench"))


def _undo_push(message):
    """Let the user take a build back with Ctrl+Z. Background Blender keeps no undo."""
    if bpy.app.background:
        return
    try:
        bpy.ops.ed.undo_push(message=message)
    except RuntimeError as e:
        print(f"Framo Bridge: undo step not recorded: {e}")


OPS = {
    "status": op_status,
    "get_scene": op_get_scene,
    "build_scene": op_build_scene,
    "screenshot": op_screenshot,
    "render_start": op_render_start,
    "run_python": op_run_python,
}

# Seconds the HTTP request waits for Blender to finish the op.
OP_TIMEOUTS = {
    "status": 30,
    "get_scene": 30,
    # A rendered still can take a while in Cycles.
    "screenshot": 150,
    "build_scene": 90,
    "render_start": 10,
    "run_python": 100,
}
