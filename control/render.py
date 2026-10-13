"""Stills and preview clips rendered from the scene camera.

Every render setting changed here is put back afterwards, to the value the
user had stored, on every exit path: these renders borrow the user's scene,
they do not reconfigure it.

The clip render blocks Blender's main thread while it runs (it is started from
a timer, never from the HTTP thread). Blender releases the Python lock during
a render, so the server keeps answering ``GET /render/<jobId>`` meanwhile.

Blender API differences handled here:

- EEVEE's engine id is ``BLENDER_EEVEE_NEXT`` in 4.2-4.x and ``BLENDER_EEVEE``
  again from 5.0; the dynamic engine enum cannot be listed, so each id is tried.
- 5.0 split video out of ``file_format`` into ``image_settings.media_type``
  ('IMAGE' / 'MULTI_LAYER_IMAGE' / 'VIDEO'); before 5.0 a video is
  ``file_format = 'FFMPEG'``. Setting media_type also replaces file_format, and
  while media_type is 'VIDEO' the only file_format it accepts is 'FFMPEG', so
  both are remembered before either is touched and put back media type first.
"""

import base64
import os
import shutil
import tempfile
import threading
import uuid

import bpy

from .scene_builder import BuildError

# "scene" renders with whatever engine and settings the scene already has,
# which is how an agent that configured Cycles through run_python gets it.
ENGINES = ("workbench", "eevee", "cycles", "scene")
SHADINGS = ("solid", "rendered")
MAX_SCREENSHOT_WIDTH = 1024
MIN_SCREENSHOT_WIDTH = 64
DEFAULT_SCREENSHOT_WIDTH = 768
# The tab writes the answer back through Convex; keep the image well under that.
MAX_SCREENSHOT_BASE64 = 700_000
_KEEP_FINISHED_JOBS = 5


class _Borrowed:
    """Set RNA attributes for a while, then put back what was stored.

    Restores in the order they were first set, which is what 5.0's
    media_type/file_format pair needs: media type first, then the format.
    """

    def __init__(self):
        self._saved = {}

    def remember(self, owner, attr):
        key = (owner.as_pointer(), attr)
        if key not in self._saved:
            self._saved[key] = (owner, attr, getattr(owner, attr))

    def set(self, owner, attr, value):
        self.remember(owner, attr)
        setattr(owner, attr, value)

    def restore(self):
        errors = []
        for owner, attr, value in self._saved.values():
            try:
                setattr(owner, attr, value)
            except Exception as e:  # keep restoring the rest
                errors.append(f"{attr}: {e}")
        self._saved.clear()
        if errors:
            print(f"Framo Bridge: could not restore render settings: {'; '.join(errors)}")


def _isolate_render(b, scene):
    """Render only the camera: no sequencer strips, no compositor, an opaque film."""
    r = scene.render
    b.set(r, "resolution_percentage", 100)
    b.set(r, "use_sequencer", False)
    b.set(r, "use_compositing", False)
    b.set(r, "film_transparent", False)


def _workbench_look(b, scene):
    """Solid shading with material colours, studio light and shadows."""
    shading = scene.display.shading
    b.set(scene.render, "engine", "BLENDER_WORKBENCH")
    b.set(shading, "light", "STUDIO")
    b.set(shading, "color_type", "MATERIAL")
    b.set(shading, "show_shadows", True)


def _set_engine(b, scene, engine):
    if engine == "scene":
        return
    if engine == "workbench":
        _workbench_look(b, scene)
        return
    if engine == "cycles":
        b.set(scene.render, "engine", "CYCLES")
        return
    for engine_id in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        try:
            b.set(scene.render, "engine", engine_id)
            return
        except TypeError:
            continue
    raise BuildError("this Blender has no EEVEE render engine")


def _has_media_type(image_settings):
    return "media_type" in image_settings.bl_rna.properties


def _remember_output_format(b, im):
    """Both halves of the output format, media type first: that is the restore order."""
    if _has_media_type(im):
        b.remember(im, "media_type")
    b.remember(im, "file_format")


def _set_still_output(b, scene, quality):
    im = scene.render.image_settings
    _remember_output_format(b, im)
    for attr in ("color_mode", "quality"):
        b.remember(im, attr)
    if _has_media_type(im):
        b.set(im, "media_type", "IMAGE")
    b.set(im, "file_format", "JPEG")
    b.set(im, "color_mode", "RGB")
    b.set(im, "quality", quality)


def _set_video_output(b, scene):
    r = scene.render
    im = r.image_settings
    ff = r.ffmpeg
    # Switching media_type to 'VIDEO' rewrites the ffmpeg container and codec
    # too (5.2: MPEG1/NONE become MKV/H264), so everything it may touch is
    # remembered before the switch, or the restore would put back its values.
    _remember_output_format(b, im)
    for attr in ("color_mode",):
        b.remember(im, attr)
    for attr in ("format", "codec", "constant_rate_factor", "ffmpeg_preset", "audio_codec"):
        b.remember(ff, attr)
    if _has_media_type(im):
        b.set(im, "media_type", "VIDEO")
    else:
        b.set(im, "file_format", "FFMPEG")
    if im.file_format != "FFMPEG":
        raise BuildError(f"could not switch the output to video (format is {im.file_format})")
    b.set(im, "color_mode", "RGB")
    b.set(ff, "format", "MPEG4")
    b.set(ff, "codec", "H264")
    b.set(ff, "constant_rate_factor", "MEDIUM")
    b.set(ff, "ffmpeg_preset", "GOOD")
    b.set(ff, "audio_codec", "NONE")
    b.set(r, "use_file_extension", True)


def _render_result():
    image = bpy.data.images.get("Render Result")
    if image is None:
        raise BuildError("the render produced no image")
    return image


# --------------------------------------------------------------------------
# Screenshot


def _collection_objects(collection):
    """Every object in a collection and its children, by pointer."""
    found = {o.as_pointer() for o in collection.objects}
    for child in collection.children:
        found |= _collection_objects(child)
    return found


def screenshot(scene, width=DEFAULT_SCREENSHOT_WIDTH, shading="solid", camera_name=None, collection_name=None):
    """One JPEG still from a camera at ``width`` keeping the aspect.

    ``solid`` is Workbench: fast, flat, for checking layout. ``rendered`` uses
    the scene's own engine and settings, so lighting and materials show as
    they will in the final render.

    ``camera_name`` shoots through that camera instead of the scene's, and
    ``collection_name`` renders only that collection's objects. Both are
    borrowed for this one still and put back, inside the one main-thread
    job, so two agents each modelling their own object in the same Blender
    never see each other's view.
    """
    if shading not in SHADINGS:
        raise BuildError(f"shading must be one of {', '.join(SHADINGS)}")
    if camera_name is not None:
        camera = bpy.data.objects.get(camera_name)
        if camera is None or camera.type != "CAMERA":
            raise BuildError(f"no camera object named {camera_name!r}")
    else:
        camera = scene.camera
    if camera is None:
        raise BuildError("no camera; add one with build_scene")
    keep = None
    if collection_name is not None:
        collection = bpy.data.collections.get(collection_name)
        if collection is None:
            raise BuildError(f"no collection named {collection_name!r}")
        keep = _collection_objects(collection)
    r = scene.render
    aspect = (r.resolution_y * r.pixel_aspect_y) / (r.resolution_x * r.pixel_aspect_x)
    height = max(2, int(round(width * aspect / 2.0)) * 2)

    out_dir = tempfile.mkdtemp(prefix="framo_shot_")
    path = os.path.join(out_dir, "shot.jpg")
    b = _Borrowed()
    try:
        _isolate_render(b, scene)
        if camera is not scene.camera:
            b.set(scene, "camera", camera)
        if keep is not None:
            for obj in scene.objects:
                if obj.as_pointer() not in keep and not obj.hide_render:
                    b.set(obj, "hide_render", True)
        if shading == "solid":
            _workbench_look(b, scene)
        b.set(r, "resolution_x", width)
        b.set(r, "resolution_y", height)
        b.set(r, "pixel_aspect_x", 1.0)
        b.set(r, "pixel_aspect_y", 1.0)
        _set_still_output(b, scene, 80)
        bpy.ops.render.render(write_still=False)
        image = _render_result()
        encoded = None
        for quality in (80, 65, 50, 35):
            scene.render.image_settings.quality = quality
            image.save_render(path, scene=scene)
            with open(path, "rb") as f:
                data = f.read()
            encoded = base64.b64encode(data).decode("ascii")
            if len(encoded) <= MAX_SCREENSHOT_BASE64:
                break
        if encoded is None or len(encoded) > MAX_SCREENSHOT_BASE64:
            raise BuildError("the screenshot is too large even at low quality; ask for a smaller width")
        return {
            "imageBase64": encoded,
            "mediaType": "image/jpeg",
            "width": width,
            "height": height,
            "camera": camera.name,
        }
    finally:
        b.restore()
        shutil.rmtree(out_dir, ignore_errors=True)


# --------------------------------------------------------------------------
# Preview clips

_jobs_lock = threading.Lock()
_jobs = {}  # job id -> dict; read by the HTTP thread, written on the main thread

_PUBLIC_FIELDS = ("state", "frame", "totalFrames", "error", "width", "height", "durationSec", "bytes")


def _active_job():
    for job in _jobs.values():
        if job["state"] in ("queued", "rendering"):
            return job
    return None


def start_render(scene, engine):
    """Queue a clip render of the scene's frame range; it runs on a later timer tick."""
    if engine not in ENGINES:
        raise BuildError(f"engine must be one of {', '.join(ENGINES)}")
    if scene.camera is None:
        raise BuildError("no camera; add one with build_scene")
    r = scene.render
    if r.resolution_x % 2 or r.resolution_y % 2:
        raise BuildError("the render resolution must be even for H.264; set render.resolution with build_scene")
    first, last = scene.frame_start, scene.frame_end
    fps = r.fps / r.fps_base
    with _jobs_lock:
        running = _active_job()
        if running is not None:
            raise BuildError(f"a render is already running (job {running['id']}); wait for it to finish")
        job = {
            "id": uuid.uuid4().hex,
            "state": "queued",
            "frame": 0,
            "totalFrames": last - first + 1,
            "engine": engine,
            "scene": scene.name,
            "path": None,
            "dir": None,
        }
        _jobs[job["id"]] = job
    return {
        "jobId": job["id"],
        "frames": [first, last],
        "fps": int(fps) if float(fps).is_integer() else round(fps, 3),
        "resolution": [r.resolution_x, r.resolution_y],
    }


def _update_job(job_id, **fields):
    with _jobs_lock:
        _jobs[job_id].update(fields)


def run_next_render():
    """Render the oldest queued clip, if any. Main thread only. True if one ran."""
    with _jobs_lock:
        job = next((j for j in _jobs.values() if j["state"] == "queued"), None)
        if job is None:
            return False
        job["state"] = "rendering"
        job_id = job["id"]
        engine = job["engine"]
        scene_name = job["scene"]

    scene = bpy.data.scenes.get(scene_name)
    if scene is None:
        _update_job(job_id, state="failed", error=f'the scene "{scene_name}" is gone')
        return True

    out_dir = tempfile.mkdtemp(prefix="framo_render_")
    first = scene.frame_start

    def on_frame(rendered_scene, *_args):
        if rendered_scene.name == scene_name:
            _update_job(job_id, frame=max(0, rendered_scene.frame_current - first + 1))

    b = _Borrowed()
    bpy.app.handlers.render_post.append(on_frame)
    try:
        r = scene.render
        _isolate_render(b, scene)
        _set_engine(b, scene, engine)
        _set_video_output(b, scene)
        b.set(r, "filepath", os.path.join(out_dir, "framo_"))
        result = bpy.ops.render.render(animation=True, scene=scene.name)
        if "CANCELLED" in result:
            raise BuildError("the render was cancelled")
        # Ask for the name while the video settings are still in place.
        path = bpy.path.abspath(r.frame_path(frame=first))
        if not os.path.isfile(path):
            found = [f for f in os.listdir(out_dir) if f.lower().endswith(".mp4")]
            path = os.path.join(out_dir, found[0]) if found else None
        if path is None or os.path.getsize(path) == 0:
            raise BuildError("the render wrote no video file")
        fps = r.fps / r.fps_base
        frames = scene.frame_end - first + 1
        _update_job(
            job_id, state="done", frame=frames, path=path, dir=out_dir,
            width=r.resolution_x, height=r.resolution_y,
            durationSec=round(frames / fps, 3), bytes=os.path.getsize(path),
        )
    except Exception as e:
        _update_job(job_id, state="failed", error=str(e))
        shutil.rmtree(out_dir, ignore_errors=True)
    finally:
        if on_frame in bpy.app.handlers.render_post:
            bpy.app.handlers.render_post.remove(on_frame)
        b.restore()
        _forget_old_jobs()
    return True


def _forget_old_jobs():
    with _jobs_lock:
        finished = [j for j in _jobs.values() if j["state"] in ("done", "failed")]
        for job in finished[:-_KEEP_FINISHED_JOBS]:
            if job.get("dir"):
                shutil.rmtree(job["dir"], ignore_errors=True)
            del _jobs[job["id"]]


def job_status(job_id):
    """The public view of a job, or None. Safe from any thread."""
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        return {k: job[k] for k in _PUBLIC_FIELDS if job.get(k) is not None}


def job_file(job_id):
    """Path of a finished clip, or None. Safe from any thread."""
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None or job["state"] != "done":
            return None
        return job["path"]


def forget_all_jobs():
    """On unregister: drop the jobs and their files."""
    with _jobs_lock:
        for job in _jobs.values():
            if job.get("dir"):
                shutil.rmtree(job["dir"], ignore_errors=True)
        _jobs.clear()
