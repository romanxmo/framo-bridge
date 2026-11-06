"""Export live sends in a second Blender, so the person's Blender never waits.

A glTF export has to run where the data is, on Blender's main thread, and on a
real scene that is seconds: long enough to freeze the interface on every change.
bpy cannot be used from another thread, so the work moves to another process.
All the person's Blender does is write a snapshot of the To Framo collection,
which measured 11 ms where the export it replaces measured 3.6 s. A background
Blender (services/live_worker_main.py) exports the snapshot, and a thread here
hands the file to the server.

What must stay true:

- Nothing here blocks the main thread for longer than the snapshot. The worker
  is started before it is needed, and everything that waits on it is on a thread.
- One send at a time. Changes made meanwhile are one send when it is done, so
  a slow scene falls behind by one send, never by a queue.
- The threads never touch bpy. What a send needs from Blender is read by
  ``submit``, on the main thread, before the thread starts.
"""

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time

import bpy

from . import export_service, framo_collection
from ..utils.logging_config import get_logger
from ..utils.thread_safety import request_redraw

log = get_logger()

JOB_TIMEOUT_S = 300.0

_WORKER_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "live_worker_main.py")
_DONE = "FRAMO_WORKER_DONE"
_FAILED = "FRAMO_WORKER_FAILED"
# No console window, and below the person's Blender when both want the processor.
_CREATION_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)

_lock = threading.Lock()
_spare = None
_running = None
_error = None


class _Job:
    __slots__ = ("process", "directory", "output", "metadata", "started")

    def __init__(self, process, directory, output, metadata):
        self.process = process
        self.directory = directory
        self.output = output
        self.metadata = metadata
        self.started = time.monotonic()


def _start_worker():
    return subprocess.Popen(
        [bpy.app.binary_path, "-b", "--factory-startup", "--disable-autoexec", "--python", _WORKER_SCRIPT],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=_CREATION_FLAGS,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def warm_up():
    """Have a worker waiting, so a send does not pay for Blender's start. Main thread only."""
    global _spare
    if _spare is None or _spare.poll() is not None:
        _spare = _start_worker()


def busy():
    """True while a send is being exported. One that has hung is stopped here."""
    with _lock:
        job = _running
    if job is None:
        return False
    if time.monotonic() - job.started > JOB_TIMEOUT_S:
        job.process.kill()
    return True


def take_error():
    """The last send's failure, once; None when it went through."""
    global _error
    with _lock:
        error, _error = _error, None
    return error


def _finish(job):
    """On a thread: wait for the worker, then hand its file to the server."""
    global _running, _error
    failure = "the export stopped without saying why"
    try:
        for line in job.process.stdout:
            if line.startswith(_DONE):
                failure = None
            elif line.startswith(_FAILED):
                failure = line[len(_FAILED):].strip()
        job.process.wait()
        if failure is None:
            with open(job.output, "rb") as f:
                export_service.publish(f.read(), job.metadata)
    except Exception as e:
        failure = str(e)
    finally:
        shutil.rmtree(job.directory, ignore_errors=True)
        if failure is not None:
            log.error(f"Live send failed: {failure}")
        with _lock:
            _running = None
            _error = failure
        request_redraw()


def submit(context, objects):
    """Start exporting what is in the collection now. Main thread only; returns once the snapshot is written."""
    global _running, _spare
    settings = context.scene.framo_export_settings
    collection = framo_collection.find()
    directory = tempfile.mkdtemp(prefix="framo_live_")
    snapshot = os.path.join(directory, "scene.blend")
    output = os.path.join(directory, "scene.glb")
    # Absolute paths: the snapshot sits elsewhere, and a texture's relative path would stop meaning anything.
    bpy.data.libraries.write(snapshot, {collection, *collection.all_objects}, path_remap='ABSOLUTE', fake_user=True, compress=False)

    params = export_service.gltf_params(output)
    # The worker picks the image format, and falls back through them.
    del params['export_image_format']
    params.update({
        'collection': framo_collection.NAME,
        'use_selection': False,
        'use_visible': True,
        'export_current_frame': True,
        # The scene's lights and cameras belong in a scene. A send by the button becomes a model in
        # the library, whose preview would light it with Blender's lamps at full strength.
        'export_lights': True,
        'export_cameras': True,
        # A lamp that looks right in Blender is tens of thousands of candela in physical units,
        # which turns the editor's scene white. Unitless keeps what the person sees.
        'export_import_convert_lighting_mode': 'COMPAT',
        # Baking animation costs a pass over every frame; Framo shows the frame Blender is on.
        'export_animations': False,
        # The pose lives in the mesh, not in a skin Framo would have to play.
        'export_skins': False,
        'export_draco_mesh_compression_enable': False,
    })
    job = {
        "snapshot": snapshot,
        "collection": framo_collection.NAME,
        "objects": [obj.name for obj in objects],
        "frame": context.scene.frame_current,
        "max_texture": int(settings.texture_max_size) if settings.enable_texture_optimization else 0,
        "excluded_materials": [item.material_name for item in settings.texture_exclude_materials if item.material_name],
        "params": params,
    }
    metadata = export_service.send_metadata(context, len(objects), True, False)

    warm_up()
    process, _spare = _spare, None
    try:
        process.stdin.write(json.dumps(job) + "\n")
        process.stdin.close()
    except OSError as e:
        shutil.rmtree(directory, ignore_errors=True)
        raise Exception(f"the export process stopped: {e}")
    running = _Job(process, directory, output, metadata)
    with _lock:
        _running = running
    threading.Thread(target=_finish, args=(running,), daemon=True).start()
    warm_up()


def shutdown():
    """Stop both workers. A worker with no job leaves by itself when its stdin closes."""
    global _spare
    if _spare is not None:
        try:
            _spare.stdin.close()
        except OSError:
            pass
        _spare = None
    with _lock:
        job = _running
    if job is not None:
        job.process.kill()
