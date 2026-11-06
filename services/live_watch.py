"""Change tracking: while Framo is following live, send it what changes here.

Nobody presses a button. The editor's ``?live=1`` polls switch this on
(services/live_link.py); from then on every change to what is in the
"To Framo" collection (services/framo_collection.py) goes to the server, where
the editor picks it up.

What must stay true:

- Tracking never gets in the way. The export runs in another process
  (services/live_worker.py), so Blender does not wait for it. A send moves nobody
  out of Edit Mode, changes no selection and adds no undo step, and waits while
  a drag, a brush stroke or a render is running, so what Framo receives is a
  finished action.
- A move does not cost an export. Where things stand is published as a table
  (``live_link.publish_moves``), which is cheap enough to do during a drag, so
  Framo follows the hand. Only a true move qualifies: anything that also changes
  geometry, and lights and cameras, whose nodes the exporter turns, take the
  whole-model path. Every whole model publishes the table too, which is what
  keeps a move made while one was exporting from being undone when it lands.
- Only what affects the send counts. Selecting, or moving anything outside the
  collection, sends nothing. Which update means what was measured, not assumed:
  selecting and hiding tag only the Scene; a move tags the Object's transform;
  a material or a node value tags the Object's shading next to the Material; a
  structural change tags every object's shading with no Material beside it,
  which is why shading counts only with a watched Material present.
- A failed send does not repeat by itself. The next change is the retry.
"""

import time
import traceback

import bpy
from bpy.app.handlers import persistent
from mathutils import Matrix

from . import framo_collection, live_link, live_worker
from ..control.commands import main_window_context
from ..utils.logging_config import get_logger
from ..utils.thread_safety import request_redraw

log = get_logger()

TICK_S = 0.05

# Blender's Z-up to glTF's Y-up, the turn the exporter gives everything it writes.
_TO_GLTF = Matrix(((1, 0, 0, 0), (0, 0, 1, 0), (0, -1, 0, 0), (0, 0, 0, 1)))
_FROM_GLTF = _TO_GLTF.inverted()

# What a depsgraph update comes to.
_NOTHING, _MOVED, _WHOLE_MODEL = range(3)

# The exporter's `collection` option, which is how a live send picks its objects.
MIN_BLENDER = (4, 2, 0)

# Modal operators that change the scene while they run: a send waits for them,
# so it never sees a half-finished drag or stroke. Add-ons' modals are not here
# on purpose: some run for the whole session, and a send has to get past them.
_EDITING_MODALS = (
    "ARMATURE_OT_", "CURVE_OT_", "CURVES_OT_", "GPENCIL_OT_", "GREASE_PENCIL_OT_",
    "MESH_OT_", "NODE_OT_", "OBJECT_OT_", "PAINT_OT_", "POSE_OT_", "SCULPT_OT_",
    "TRANSFORM_OT_", "UV_OT_",
)

_pacer = live_link.SendPacer()
_following = False
# True for the length of a snapshot, so anything it tags is never read as an edit.
_sending = False
# Something went out in this run, so an emptied collection is news: Framo must empty too.
_sent_something = False
_object_names = set()
_material_names = set()
_contents = ()
# Objects Framo holds one node for, placed by a move: not what an instance shows, not a light or a camera.
_movable_names = set()
_moved = False
_status = {"objects": 0, "error": None}


def is_supported():
    return bpy.app.version >= MIN_BLENDER


def status():
    """What the panel shows: ``following``, and the last send's ``objects`` and ``error``."""
    return {"following": _following, **_status}


def _refresh_contents(view_layer):
    """Re-read the collection; True when what it holds is different."""
    global _object_names, _material_names, _contents, _movable_names
    objects = framo_collection.visible_objects(view_layer)
    contents = tuple(obj.name_full for obj in objects)
    _object_names, _material_names = framo_collection.watched_names(objects)
    _movable_names = {obj.name_full for obj in objects if obj.type not in ('LIGHT', 'CAMERA')}
    changed = contents != _contents
    _contents = contents
    return changed


def _gltf_matrix(obj):
    """Where `obj` stands, in glTF's axes, column by column, as a glTF matrix is written."""
    m = _TO_GLTF @ obj.matrix_world @ _FROM_GLTF
    return [round(m[row][column], 6) for column in range(4) for row in range(4)]


def _publish_moves():
    objects = {}
    for name in _movable_names:
        obj = bpy.data.objects.get(name)
        if obj is not None:
            objects[name] = _gltf_matrix(obj)
    live_link.publish_moves(objects)


def _update_matters(depsgraph):
    """What this depsgraph update comes to for a send."""
    shading = False
    material = False
    structure = False
    moved = False
    takes_moves = live_link.takes_moves()
    for update in depsgraph.updates:
        data = update.id.original
        if isinstance(data, bpy.types.Object):
            flagged = update.is_updated_transform or update.is_updated_geometry or update.is_updated_shading
            if data.name_full in _object_names:
                if update.is_updated_geometry:
                    return _WHOLE_MODEL
                if update.is_updated_transform:
                    if not takes_moves or data.name_full not in _movable_names:
                        return _WHOLE_MODEL
                    moved = True
                shading = shading or update.is_updated_shading
            # An update with no flag at all is a rename, which leaves the old name in the watched set.
            structure = structure or not flagged
        elif isinstance(data, bpy.types.Material):
            material = material or data.name_full in _material_names
        elif isinstance(data, (bpy.types.Scene, bpy.types.Collection)):
            structure = True
    if shading and material:
        return _WHOLE_MODEL
    if structure and _refresh_contents(depsgraph.view_layer):
        return _WHOLE_MODEL
    return _MOVED if moved else _NOTHING


@persistent
def _on_depsgraph_update(_scene, depsgraph):
    global _moved
    if not _following or _sending:
        return
    try:
        matters = _update_matters(depsgraph)
        if matters == _WHOLE_MODEL:
            _pacer.note_change(time.monotonic())
        elif matters == _MOVED:
            _moved = True
    except Exception:
        traceback.print_exc()


@persistent
def _on_contents_replaced(*_args):
    """A file load, an undo or a redo: anything may be different now."""
    if not _following or _sending:
        return
    _pacer.note_change(time.monotonic())


@persistent
def _on_file_loaded(*_args):
    """Every file has the collection from the moment it opens; here it joins the file's first undo step."""
    framo_collection.ensure(bpy.context.scene)
    _on_contents_replaced()


def _add_collection_on_start():
    """For the file already open when the add-on is enabled: registering may not touch data, a timer may."""
    with main_window_context():
        framo_collection.ensure(bpy.context.scene)


def _busy():
    if bpy.app.is_job_running('RENDER') or bpy.app.is_job_running('OBJECT_BAKE'):
        return True
    wm = bpy.context.window_manager
    if wm is None:
        return False
    return any(
        op.bl_idname.startswith(_EDITING_MODALS)
        for window in wm.windows
        for op in window.modal_operators
    )


def _send():
    global _sending, _sent_something
    context = bpy.context
    _refresh_contents(context.view_layer)
    objects = framo_collection.visible_objects(context.view_layer)
    started = time.monotonic()
    if not objects and not _sent_something:
        _pacer.note_sent(started, 0.0)
        return

    _sending = True
    try:
        # An edit lives in the edit mesh until it is flushed, and a snapshot reads the mesh.
        for name in _object_names:
            obj = bpy.data.objects.get(name)
            if obj is not None and obj.mode != 'OBJECT':
                obj.update_from_editmode()
        live_worker.submit(context, objects)
        _publish_moves()
        _sent_something = bool(objects)
        _status.update(objects=len(objects), error=None)
    except Exception as e:
        log.error(f"Live send failed: {e}")
        traceback.print_exc()
        _status["error"] = str(e)
    finally:
        try:
            # Flush what the snapshot tagged while the guard is up, so none of it reads as an edit later.
            context.view_layer.update()
        finally:
            _sending = False
    now = time.monotonic()
    _pacer.note_sent(now, now - started)
    request_redraw()


def _start_following():
    global _following, _sent_something
    _following = True
    _sent_something = False
    _status.update(objects=0, error=None)
    framo_collection.ensure(bpy.context.scene)
    _refresh_contents(bpy.context.view_layer)
    live_worker.warm_up()
    # Framo has just arrived: it gets what is there now, with no edit needed.
    _pacer.reset()
    _pacer.note_change(time.monotonic() - live_link.QUIET_S)
    request_redraw()


def _stop_following():
    global _following
    _following = False
    _pacer.reset()
    live_worker.shutdown()
    request_redraw()


def step():
    """One tick. Main thread only; a script calls it directly in background Blender, where timers never fire."""
    live = live_link.is_live() and is_supported()
    if not live:
        if _following:
            _stop_following()
        return
    global _moved
    with main_window_context():
        if not _following:
            _start_following()
        # Not held for a drag or a running export: reading where things stand disturbs neither.
        if _moved:
            _moved = False
            _publish_moves()
        error = live_worker.take_error()
        if error is not None:
            _status["error"] = error
        if _pacer.due(time.monotonic()) and not live_worker.busy() and not _busy():
            _send()


def _timer():
    try:
        step()
    except Exception:
        traceback.print_exc()
    return TICK_S


def register():
    if _on_depsgraph_update not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph_update)
    if _on_file_loaded not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_file_loaded)
    for handlers in (bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        if _on_contents_replaced not in handlers:
            handlers.append(_on_contents_replaced)
    bpy.app.timers.register(_add_collection_on_start, first_interval=0.0)
    if not bpy.app.timers.is_registered(_timer):
        bpy.app.timers.register(_timer, first_interval=TICK_S, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    if _on_file_loaded in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_file_loaded)
    for handlers in (bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        if _on_contents_replaced in handlers:
            handlers.remove(_on_contents_replaced)
    if _on_depsgraph_update in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph_update)
    _stop_following()
