"""Headless end-to-end check of the Framo control channel, in real Blender.

    /Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup \\
        --python-exit-code 1 --python tests/headless/run.py

Add ``--online-mode`` to also build a (cached, never downloaded) library asset
through build_scene; without it Blender's online access is off and the test
asserts the refusal instead.

It loads the add-on from this working tree as a package named ``framo_bridge``
(symlinked into a temp dir, without ``logs/`` so the run writes no log into
the repo), registers it -- which starts the server on localhost:8080 -- and
drives the ops directly and over HTTP. Background Blender runs no timers, so
the test pumps ``process_pending_jobs()`` on the main thread while a client
thread makes each request, exactly as the timer does in a normal session.

Needs port 8080 free: with another Blender's Bridge listening there, the HTTP
half would talk to that Blender instead, so the run refuses up front.
"""

import base64
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)

FRAMO = "https://framo.app"
PASSED = []
FAILED = []


def check(name, condition, detail=""):
    if condition:
        PASSED.append(name)
        print(f"  ok   {name}")
    else:
        FAILED.append(f"{name} {detail}".strip())
        print(f"  FAIL {name} {detail}")


def section(title):
    print(f"\n== {title}")


# ---------------------------------------------------------------------------
# Load the working tree as framo_bridge


def port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


if port_in_use(8080):
    raise SystemExit("port 8080 is in use (another Blender's Bridge?); close it and run again")

PKG_ROOT = tempfile.mkdtemp(prefix="framo_bridge_test_")
PKG = os.path.join(PKG_ROOT, "framo_bridge")
os.mkdir(PKG)
for entry in ("__init__.py", "control", "core", "processing", "services", "ui", "utils", "icons"):
    os.symlink(os.path.join(REPO, entry), os.path.join(PKG, entry))
sys.path.insert(0, PKG_ROOT)

import framo_bridge  # noqa: E402
from framo_bridge.control import asset_source, commands, render, scene_builder  # noqa: E402
from framo_bridge.services import preview_server  # noqa: E402
from framo_bridge.utils import thread_safety  # noqa: E402

framo_bridge.register()


def pump_while(fn):
    """Run fn on a client thread while the main thread plays the timer."""
    box = {}

    def target():
        try:
            box["value"] = fn()
        except Exception as e:  # surfaced below
            box["error"] = e
            traceback.print_exc()

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    deadline = time.time() + 120
    while thread.is_alive() and time.time() < deadline:
        commands.process_pending_jobs()
        time.sleep(0.02)
    thread.join(1)
    if "error" in box:
        raise box["error"]
    return box.get("value")


def run_op(op, payload):
    return commands._run(op, payload)


def request(method, path, body=None, origin=FRAMO, host="localhost:8080", headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", 8080, timeout=120)
    conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    if host is not None:
        conn.putheader("Host", host)
    if origin is not None:
        conn.putheader("Origin", origin)
    data = b""
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
    for name, value in (headers or {}).items():
        conn.putheader(name, value)
    if method in ("POST",) and "Content-Length" not in (headers or {}):
        conn.putheader("Content-Length", str(len(data)))
    conn.endheaders()
    if data:
        try:
            conn.send(data)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the server answered without reading the body, as it should
    resp = conn.getresponse()
    payload = resp.read()
    result = (resp.status, dict(resp.getheaders()), payload)
    conn.close()
    return result


def command(op, payload, **kw):
    return request("POST", "/command", {"id": f"t-{op}-{time.time()}", "op": op, "payload": payload},
                   headers={"Content-Type": "application/json"}, **kw)


SPEC = {
    "objects": [
        {"id": "floor", "primitive": "plane", "size": [10, 10, 0], "position": [0, 0, 0], "color": "#808080"},
        {"id": "wall", "primitive": "box", "size": [4, 0.2, 2.5], "position": [0, 2, 0], "color": "#d8d2c8", "roughness": 0.8},
        {"id": "ball", "primitive": "sphere", "size": [1, 0.5, 0.2], "position": [1.5, 0, 0], "color": "#ff0000",
         "keys": [{"frame": 1, "position": [1.5, 0, 0]}, {"frame": 12, "position": [-1.5, 0, 0], "rotation": [0, 0, 90]}]},
        {"id": "post", "primitive": "cylinder", "size": [0.3, 0.2, 2], "position": [-2, 0, 0], "rotation": [0, 0, 45]},
        {"id": "cone", "primitive": "cone", "size": [0.6, 0.6, 1], "position": [2, -1, 0], "metallic": 1},
    ],
    "lights": [
        {"id": "sun", "type": "sun", "position": [0, 0, 10], "rotation": [40, 0, 30], "strength": 3, "size": 1},
        {"id": "lamp", "type": "point", "position": [1, -1, 2], "strength": 200, "color": "#ffeedd"},
    ],
    "camera": {"lens": 35, "keys": [
        {"frame": 1, "position": [0, -8, 1.6], "target": [0, 0, 1]},
        {"frame": 12, "position": [1, -6, 1.4], "target": [0, 0, 1]},
    ]},
    "world": {"color": "#202830", "strength": 0.8},
    "render": {"fps": 24, "frames": [1, 12], "resolution": [128, 72]},
}


def framo_object(framo_id, role="object"):
    return scene_builder.framo_roots(bpy.context.scene).get((role, framo_id))


try:
    scene = bpy.context.scene

    # -----------------------------------------------------------------------
    section("register")
    check("server started", preview_server.server_instance is not None)
    check("control is off on start", thread_safety.is_control_enabled() is False)
    check("WindowManager switch exists and reads off",
          bpy.context.window_manager.framo_control_enabled is False)
    bpy.context.window_manager.framo_control_enabled = True
    check("switch drives the global", thread_safety.is_control_enabled() is True)
    bpy.context.window_manager.framo_control_enabled = False

    # The user's own scene: the factory cube, plus an object whose name the
    # builder would like to use for its own wall.
    user_cube = bpy.data.objects["Cube"]
    cube_before = (user_cube.matrix_world.copy(), user_cube.data.name, len(user_cube.data.vertices),
                   user_cube.active_material.name, set(user_cube.keys()))
    squatter = bpy.data.objects.new("Framo wall", None)
    scene.collection.objects.link(squatter)

    def user_cube_untouched():
        return (user_cube.matrix_world == cube_before[0] and user_cube.data.name == cube_before[1]
                and len(user_cube.data.vertices) == cube_before[2]
                and user_cube.active_material.name == cube_before[3] and set(user_cube.keys()) == cube_before[4])

    # -----------------------------------------------------------------------
    section("build_scene")
    first = run_op("build_scene", {"spec": SPEC, "assets": {}})
    check("build ok", first["ok"], str(first))
    res = first.get("result", {})
    check("created every id", sorted(res.get("created", [])) ==
          sorted(["floor", "wall", "ball", "post", "cone", "sun", "lamp", "__camera__"]), str(res))
    check("nothing updated or removed on a fresh build", res.get("updated") == [] and res.get("removed") == [])
    check("no warnings", res.get("warnings") == [], str(res.get("warnings")))

    coll = bpy.data.collections.get("Framo Scene")
    check("Framo Scene collection linked", coll is not None and coll.name in scene.collection.children)

    def bbox(obj):
        return scene_builder.world_bbox([obj])

    def near(a, b, tol=1e-3):
        return all(abs(x - y) <= tol for x, y in zip(a, b))

    wall = framo_object("wall")
    lo, hi = bbox(wall)
    check("box: size and base at position", near(lo, [-2, 1.9, 0]) and near(hi, [2, 2.1, 2.5]), f"{lo} {hi}")
    check("framo object is in the collection", coll in wall.users_collection)
    check("framo object named after id, squatter kept its name",
          squatter.name == "Framo wall" and wall.name != "Framo wall" and wall.get("framo_name") == wall.name)
    mat = wall.active_material
    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    check("material named, base colour linear", mat.name.startswith("Framo wall")
          and near(bsdf.inputs["Base Color"].default_value[:3], scene_builder.hex_to_linear_rgb("#d8d2c8"))
          and abs(bsdf.inputs["Roughness"].default_value - 0.8) < 1e-6)
    check("viewport colour matches for Workbench", near(mat.diffuse_color[:3], bsdf.inputs["Base Color"].default_value[:3]))

    scene.frame_set(1)
    ball = framo_object("ball")
    lo, hi = bbox(ball)
    check("sphere: diameter is max(size), sits on the floor", near([hi[i] - lo[i] for i in range(3)], [1, 1, 1], 0.01)
          and near([lo[2]], [0]), f"{lo} {hi}")
    post = framo_object("post")
    check("cylinder: radius max(x,y)/2, height z", near(post.dimensions, [0.3, 0.3, 2], 0.01), str(tuple(post.dimensions)))
    cone = framo_object("cone")
    lo, hi = bbox(cone)
    check("cone: base on the floor, 1 m tall", near([lo[2], hi[2]], [0, 1]))
    floor = framo_object("floor")
    check("plane: x by y, flat", near(floor.dimensions, [10, 10, 0]))

    fcurves = scene_builder._fcurves(ball)
    check("ball keyed", len(fcurves) >= 6, str(len(fcurves)))
    check("keys are bezier, auto-clamped", all(p.interpolation == "BEZIER" and p.handle_left_type == "AUTO_CLAMPED"
                                               for fc in fcurves for p in fc.keyframe_points))
    scene.frame_set(12)
    check("ball at its last key on frame 12", near(ball.matrix_world.translation, [-1.5, 0, 0]))
    scene.frame_set(1)

    cam = framo_object(scene_builder.CAMERA_ID, "camera")
    target = framo_object(scene_builder.CAMERA_TARGET_ID, "camera_target")
    check("camera is the scene camera", cam is not None and scene.camera == cam)
    check("camera lens and track-to", cam.data.lens == 35 and cam.constraints["Framo Track"].target == target
          and cam.constraints["Framo Track"].track_axis == "TRACK_NEGATIVE_Z")
    scene.frame_set(12)
    check("camera at last key on frame 12", near(cam.matrix_world.translation, [1, -6, 1.4]))
    scene.frame_set(1)
    check("render settings applied", scene.render.fps == 24 and scene.frame_start == 1 and scene.frame_end == 12
          and scene.render.resolution_x == 128 and scene.render.resolution_y == 72)
    bg = scene_builder._world_background(scene.world)
    check("world colour and strength", near(bg.inputs["Color"].default_value[:3], scene_builder.hex_to_linear_rgb("#202830"))
          and abs(bg.inputs["Strength"].default_value - 0.8) < 1e-6 and near(scene.world.color, bg.inputs["Color"].default_value[:3]))
    sun = framo_object("sun", "light")
    check("sun light", sun.data.type == "SUN" and sun.data.energy == 3)
    check("user cube untouched after build", user_cube_untouched())

    # -----------------------------------------------------------------------
    section("idempotent")
    names_before = sorted(o.name for o in bpy.data.objects)
    datablocks_before = (len(bpy.data.meshes), len(bpy.data.materials), len(bpy.data.lights), len(bpy.data.actions))
    second = run_op("build_scene", {"spec": SPEC, "assets": {}})["result"]
    check("same spec twice changes nothing", second == {"created": [], "updated": [], "removed": [], "warnings": []}, str(second))
    check("no new objects or names", sorted(o.name for o in bpy.data.objects) == names_before)
    check("no new datablocks", (len(bpy.data.meshes), len(bpy.data.materials), len(bpy.data.lights),
                                len(bpy.data.actions)) == datablocks_before)

    section("edited spec changes only what was edited")
    edited = json.loads(json.dumps(SPEC))
    edited["objects"] = [o for o in edited["objects"] if o["id"] != "cone"]
    for o in edited["objects"]:
        if o["id"] == "wall":
            o["color"] = "#336699"
        if o["id"] == "post":
            o["position"] = [-2.5, 0, 0]
    third = run_op("build_scene", {"spec": edited, "assets": {}})["result"]
    check("updated exactly wall and post", sorted(third["updated"]) == ["post", "wall"], str(third))
    check("removed exactly cone", third["removed"] == ["cone"] and third["created"] == [], str(third))
    check("cone gone with its mesh and material",
          framo_object("cone") is None and not any(m.name.startswith("Framo cone") for m in bpy.data.materials)
          and not any(m.name.startswith("Framo cone") for m in bpy.data.meshes))
    check("rebuilt wall keeps the base name it can get", framo_object("wall").get("framo_name") == framo_object("wall").name)
    check("old wall material removed", len([m for m in bpy.data.materials if m.name.startswith("Framo wall")]) == 1)
    check("squatter still there", bpy.data.objects.get("Framo wall") == squatter and "framo_id" not in squatter)
    post_obj = framo_object("post")
    post_obj.location.x = 5  # the user drags a Framo object
    fourth = run_op("build_scene", {"spec": edited, "assets": {}})["result"]
    check("re-sending puts a dragged Framo object back", fourth["updated"] == ["post"] and abs(post_obj.location.x + 2.5) < 1e-6,
          str(fourth))

    section("the user's objects are never targets")
    copy = framo_object("ball").copy()  # Shift+D keeps the custom properties
    scene.collection.objects.link(copy)
    without_ball = json.loads(json.dumps(edited))
    without_ball["objects"] = [o for o in without_ball["objects"] if o["id"] != "ball"]
    fifth = run_op("build_scene", {"spec": without_ball, "assets": {}})["result"]
    check("ball removed", fifth["removed"] == ["ball"], str(fifth))
    check("user's copy of a Framo object left alone", copy.name in bpy.data.objects and "framo_id" in copy)
    check("user cube untouched after edits", user_cube_untouched())

    section("validation")
    bad = run_op("build_scene", {"spec": {"objects": [{"id": "x", "primitive": "torus", "size": [1, 1], "position": [0, 0, 0]}],
                                          "render": {"fps": 24, "frames": [1, 2], "resolution": [101, 64]}}})
    check("invalid spec refused with every error", not bad["ok"] and "objects[0].primitive" in bad["error"]
          and "objects[0].size" in bad["error"] and "even numbers" in bad["error"], bad.get("error", ""))
    check("unknown payload field refused", not run_op("build_scene", {"spec": SPEC, "extra": 1})["ok"])

    # -----------------------------------------------------------------------
    section("get_scene and status")
    got = run_op("get_scene", {})
    check("get_scene ok", got["ok"], str(got)[:300])
    sc = got["result"]
    users = {u["name"]: u for u in sc["userObjects"]}
    check("user cube listed with its world bbox", "Cube" in users and users["Cube"]["bboxMin"] == [-1, -1, -1]
          and users["Cube"]["bboxMax"] == [1, 1, 1] and users["Cube"]["dimensions"] == [2, 2, 2], str(users.get("Cube")))
    check("user copy of a Framo object counts as the user's", copy.name in users)
    check("no Framo object among the user's", not any(n.startswith("Framo ") and n != "Framo wall" and n != copy.name for n in users))
    ids = {o["id"]: o for o in sc["framo"]["objects"]}
    check("framo objects listed", sorted(ids) == ["floor", "post", "wall"], str(sorted(ids)))
    check("framo object entry", ids["wall"]["kind"] == "primitive" and ids["wall"]["primitive"] == "box"
          and ids["wall"]["dimensions"] == [4, 0.2, 2.5] and ids["post"]["rotation"] == [0, 0, 45], str(ids["wall"]))
    check("lights listed", sorted(l["id"] for l in sc["framo"]["lights"]) == ["lamp", "sun"])
    check("camera reported as Framo's", sc["camera"]["isFramo"] and sc["camera"]["lens"] == 35)
    check("scene facts", sc["fps"] == 24 and sc["resolution"] == [128, 72] and sc["truncated"] is False and sc["units"] == "m")

    st = run_op("status", {})
    check("status ok", st["ok"], str(st)[:300])
    st = st["result"]
    check("status versions", st["bridgeVersion"] == "0.5.0" and st["blenderVersion"] == ".".join(map(str, bpy.app.version)))
    gltf = next((a for a in st["addons"] if a["module"] == "io_scene_gltf2"), None)
    check("add-ons listed with string versions", gltf is not None and gltf["enabled"] and isinstance(gltf["version"], str)
          and gltf["version"].count(".") >= 1, str(gltf))
    check("counts", st["framoObjectCount"] == 3 and st["userObjectCount"] == len(sc["userObjects"]), str(st)[:200])

    # -----------------------------------------------------------------------
    section("screenshot")
    before = (scene.render.engine, scene.render.resolution_x, scene.render.resolution_y,
              scene.render.image_settings.file_format, scene.display.shading.light, scene.render.filepath)
    shot = run_op("screenshot", {"width": 256})
    check("screenshot ok", shot["ok"], str(shot)[:300])
    data = base64.b64decode(shot["result"]["imageBase64"])
    check("valid JPEG", data[:3] == b"\xff\xd8\xff" and data[-2:] == b"\xff\xd9" and len(data) > 500, str(len(data)))
    check("size keeps the scene aspect", shot["result"]["width"] == 256 and shot["result"]["height"] == 144,
          str((shot["result"]["width"], shot["result"]["height"])))
    check("render settings restored after screenshot", before == (
        scene.render.engine, scene.render.resolution_x, scene.render.resolution_y,
        scene.render.image_settings.file_format, scene.display.shading.light, scene.render.filepath))
    check("width out of range refused", not run_op("screenshot", {"width": 2048})["ok"])
    lit = run_op("screenshot", {"width": 128, "shading": "rendered"})
    check("rendered screenshot ok", lit["ok"], str(lit)[:300])
    check("rendered screenshot is a JPEG", lit["ok"] and base64.b64decode(lit["result"]["imageBase64"])[:3] == b"\xff\xd8\xff")
    check("render settings restored after rendered screenshot", before == (
        scene.render.engine, scene.render.resolution_x, scene.render.resolution_y,
        scene.render.image_settings.file_format, scene.display.shading.light, scene.render.filepath))
    check("unknown shading refused", not run_op("screenshot", {"shading": "toon"})["ok"])
    # One object's own view: its camera, only its collection, and the scene
    # camera and every other object's visibility back as they were.
    iso = bpy.data.collections.new("Iso Test")
    scene.collection.children.link(iso)
    iso_cam = bpy.data.objects.new("Iso Cam", bpy.data.cameras.new("Iso Cam"))
    iso.objects.link(iso_cam)
    others = [o for o in scene.objects if o.name != "Iso Cam"]
    hidden_before = [o.hide_render for o in others]
    scene_cam_before = scene.camera
    iso_shot = run_op("screenshot", {"width": 128, "camera": "Iso Cam", "collection": "Iso Test"})
    check("screenshot through a named camera of one collection ok", iso_shot["ok"], str(iso_shot)[:300])
    check("named camera reported", iso_shot["ok"] and iso_shot["result"]["camera"] == "Iso Cam")
    check("scene camera restored", scene.camera == scene_cam_before)
    check("other objects' render visibility restored", [o.hide_render for o in others] == hidden_before)
    check("unknown camera refused", not run_op("screenshot", {"camera": "Nope"})["ok"])
    check("unknown collection refused", not run_op("screenshot", {"collection": "Nope"})["ok"])
    check("a mesh is not a camera", not run_op("screenshot", {"camera": others[0].name})["ok"] if any(o.type != "CAMERA" for o in others[:1]) else True)
    bpy.data.objects.remove(iso_cam, do_unlink=True)
    bpy.data.collections.remove(iso)

    # -----------------------------------------------------------------------
    section("run_python")
    py = run_op("run_python", {"code": "o = bpy.data.objects.new('py_empty', None)\nbpy.context.scene.collection.objects.link(o)\nprint('made', o.name)"})
    check("python runs with bpy in scope", py["ok"] and "made py_empty" in py["result"]["output"], str(py)[:300])
    check("python changed the scene", bpy.data.objects.get("py_empty") is not None)
    py2 = run_op("run_python", {"code": "print(o.name, round(math.pi, 2))"})
    check("namespace persists between calls", py2["ok"] and "py_empty 3.14" in py2["result"]["output"], str(py2)[:300])
    bad = run_op("run_python", {"code": "print('before')\nraise ValueError('boom')"})
    check("an exception comes back with its output and traceback",
          not bad["ok"] and "before" in bad["error"] and "ValueError: boom" in bad["error"], str(bad)[:300])
    check("a syntax error comes back", not run_op("run_python", {"code": "def ("})["ok"])
    check("empty code refused", not run_op("run_python", {"code": "  "})["ok"])
    bpy.data.objects.remove(bpy.data.objects["py_empty"], do_unlink=True)

    # -----------------------------------------------------------------------
    section("render_start (workbench, direct)")
    im = scene.render.image_settings
    media_before = getattr(im, "media_type", None)
    before = (scene.render.engine, im.file_format, scene.render.filepath, scene.render.ffmpeg.codec,
              scene.render.ffmpeg.format, im.color_mode)
    started = run_op("render_start", {"engine": "workbench"})
    check("render_start ok", started["ok"], str(started))
    job_id = started["result"]["jobId"]
    check("render_start answer", started["result"]["frames"] == [1, 12] and started["result"]["fps"] == 24
          and started["result"]["resolution"] == [128, 72])
    check("job queued, not run yet", render.job_status(job_id)["state"] == "queued")
    check("second render refused while one is queued", not run_op("render_start", {})["ok"])
    commands.process_pending_jobs()  # an idle tick runs the render
    status = render.job_status(job_id)
    check("job done", status["state"] == "done", str(status))
    check("progress reached the last frame", status.get("frame") == 12 and status.get("totalFrames") == 12, str(status))
    path = render.job_file(job_id)
    check("mp4 written", path and path.endswith(".mp4") and os.path.getsize(path) > 0, str(path))
    if shutil.which("ffprobe") and path:
        probe = json.loads(subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries",
             "stream=codec_name,width,height,nb_read_frames", "-of", "json", path],
            capture_output=True, text=True, check=True).stdout)["streams"][0]
        check("ffprobe: h264 128x72, 12 frames", probe["codec_name"] == "h264" and probe["width"] == 128
              and probe["height"] == 72 and int(probe["nb_read_frames"]) == 12, str(probe))
    check("render settings restored after the clip", before == (
        scene.render.engine, im.file_format, scene.render.filepath, scene.render.ffmpeg.codec,
        scene.render.ffmpeg.format, im.color_mode)
        and getattr(im, "media_type", None) == media_before,
        f"{before} vs {(scene.render.engine, im.file_format, scene.render.filepath, scene.render.ffmpeg.codec, scene.render.ffmpeg.format, im.color_mode)}")

    section("render_start (eevee, 3 frames)")
    short = json.loads(json.dumps(edited))
    short["render"] = {"fps": 24, "frames": [1, 3], "resolution": [64, 64]}
    run_op("build_scene", {"spec": short, "assets": {}})
    ev = run_op("render_start", {"engine": "eevee"})
    check("eevee render_start ok", ev["ok"], str(ev))
    commands.process_pending_jobs()
    ev_status = render.job_status(ev["result"]["jobId"])
    check("eevee clip done", ev_status["state"] == "done" and ev_status["bytes"] > 0, str(ev_status))
    check("engine restored after eevee", scene.render.engine == before[0])
    run_op("build_scene", {"spec": edited, "assets": {}})

    # -----------------------------------------------------------------------
    section("assets")
    missing = run_op("build_scene", {"spec": {"objects": [
        {"id": "sofa", "asset": "cat_missing", "position": [0, 0, 0]},
        {"id": "box2", "primitive": "box", "size": [1, 1, 1], "position": [3, 3, 0]}]}, "assets": {}})["result"]
    check("unresolved asset is a warning, the rest builds", "box2" in missing["created"] and "sofa" not in missing["created"]
          and any("cat_missing" in w for w in missing["warnings"]), str(missing))
    evil = run_op("build_scene", {"spec": {"objects": [{"id": "sofa", "asset": "cat_evil", "position": [0, 0, 0]}]},
                                  "assets": {"cat_evil": {"url": "https://evil.example/sofa.glb", "name": "x", "size": [1, 1, 1]}}})["result"]
    check("non-Framo asset URL refused, online or not", any("refused" in w for w in evil["warnings"])
          and framo_object("sofa") is None, str(evil))

    # A local GLB: export a throwaway mesh, then import it as an asset.
    tmp = bpy.data.objects.new("tmp_asset", scene_builder.build_primitive_mesh("tmp_asset_mesh", "box", [1, 2, 0.5]))
    scene.collection.objects.link(tmp)
    for o in scene.objects:
        o.select_set(o == tmp)
    bpy.context.view_layer.objects.active = tmp
    glb_dir = tempfile.mkdtemp(prefix="framo_glb_")
    glb = os.path.join(glb_dir, "asset.glb")
    bpy.ops.export_scene.gltf(filepath=glb, export_format="GLB", use_selection=True)
    bpy.data.objects.remove(tmp)
    check("test GLB written", os.path.getsize(glb) > 0)

    user_cube.select_set(True)
    bpy.context.view_layer.objects.active = user_cube
    root = scene_builder.import_asset_glb(glb, "chair", scene_builder.framo_collection(scene), scale=2.0)
    parts = list(root.children_recursive)
    check("import: root Empty named and tagged", root.type == "EMPTY" and root.name == "Framo chair" and root["framo_id"] == "chair")
    check("import: parts parented, tagged, in the collection", parts and all(
        p.get("framo_role") == "asset_part" and scene_builder.is_framo_owned(p) and coll in p.users_collection for p in parts))
    check("import: scaled", tuple(root.scale) == (2.0, 2.0, 2.0))
    lo, hi = scene_builder.world_bbox(parts)
    check("import: normalized asset doubles in size", near([hi[i] - lo[i] for i in range(3)], [2, 4, 1], 0.01), f"{lo} {hi}")
    check("import: user's selection put back", user_cube.select_get() and bpy.context.view_layer.objects.active == user_cube)
    after_import = run_op("build_scene", {"spec": edited, "assets": {}})["result"]
    check("an imported asset not in the spec is removed, parts and all",
          "chair" in after_import["removed"] and bpy.data.objects.get("Framo chair") is None
          and not any(o.get("framo_role") == "asset_part" for o in bpy.data.objects), str(after_import))

    # Through build_scene: a cached asset is never downloaded.
    cached = asset_source.cache_path("framo_test_cached_asset")
    os.makedirs(os.path.dirname(cached), exist_ok=True)
    shutil.copy(glb, cached)
    with_asset = json.loads(json.dumps(edited))
    with_asset["objects"].append({"id": "chair", "asset": "framo_test_cached_asset", "position": [0, -1, 0], "height": 1})
    assets = {"framo_test_cached_asset": {"url": "https://test.convex.cloud/api/storage/x", "name": "Box", "size": [1, 2, 0.5]}}
    built = run_op("build_scene", {"spec": with_asset, "assets": assets})["result"]
    if getattr(bpy.app, "online_access", True):
        check("cached asset built through build_scene", built["created"] == ["chair"], str(built))
        chair = framo_object("chair")
        lo, hi = scene_builder.world_bbox(list(chair.children_recursive))
        check("height scales the asset uniformly", near([hi[2] - lo[2]], [1], 0.01) and near(chair.scale, [2, 2, 2]))
        again = run_op("build_scene", {"spec": with_asset, "assets": assets})["result"]
        check("asset build is idempotent", again == {"created": [], "updated": [], "removed": [], "warnings": []}, str(again))
    else:
        check("offline Blender refuses asset downloads", any("online access" in w for w in built["warnings"]), str(built))
    os.remove(cached)
    run_op("build_scene", {"spec": edited, "assets": {}})

    # -----------------------------------------------------------------------
    section("HTTP: who may talk to the server")
    status_code, headers, body = request("GET", "/ping", origin=None)
    check("GET /ping without Origin answers pong", status_code == 200 and body == b"pong"
          and "Access-Control-Allow-Origin" not in headers)
    status_code, headers, body = request("GET", "/ping", origin="https://evil.example")
    check("GET /ping from a foreign page: answered but unreadable", status_code == 200
          and "Access-Control-Allow-Origin" not in headers)
    check("POST /command without Origin refused", command("status", {}, origin=None)[0] == 403)
    check("POST /command from a foreign origin refused", command("status", {}, origin="https://evil.example")[0] == 403)
    check("GET /latest-model from a foreign origin refused", request("GET", "/latest-model", origin="https://evil.example")[0] == 403)
    check("POST /connect-user from a foreign origin refused",
          request("POST", "/connect-user", {"name": "evil"}, origin="https://evil.example")[0] == 403
          and thread_safety.get_user_info_copy()["name"] is None)
    check("bad Host (DNS rebinding) refused", command("status", {}, host="attacker.example:8080")[0] == 403)
    check("bad Host refused even for /ping", request("GET", "/ping", origin=None, host="attacker.example:8080")[0] == 403)
    status_code, headers, _ = request("OPTIONS", "/command", origin="http://localhost:3000",
                                      headers={"Access-Control-Request-Method": "POST"})
    check("preflight from Framo dev", status_code == 204 and headers.get("Access-Control-Allow-Origin") == "http://localhost:3000"
          and headers.get("Vary") == "Origin" and headers.get("Access-Control-Allow-Private-Network") == "true"
          and "X-Model-Metadata" in headers.get("Access-Control-Allow-Headers", ""), str(headers))
    check("preflight from a foreign origin refused", request("OPTIONS", "/command", origin="https://evil.example")[0] == 403)
    status_code, headers, body = request("GET", "/latest-model-info")
    check("existing route works for Framo, origin echoed", status_code == 404
          and headers.get("Access-Control-Allow-Origin") == FRAMO)
    status_code, _, body = request("POST", "/upload-model", b"glTF-test-bytes", origin=None,
                                   headers={"Content-Type": "application/octet-stream"})
    check("export's own no-Origin upload still works", status_code == 200)
    status_code, headers, body = request("GET", "/latest-model")
    check("Framo reads the uploaded model", status_code == 200 and body == b"glTF-test-bytes")
    status_code, _, body = request("POST", "/connect-user", {"name": "Roman", "email": "r@example.com"},
                                   headers={"Content-Type": "application/json"})
    check("connect-user from Framo", status_code == 200 and thread_safety.get_user_info_copy()["name"] == "Roman")
    check("disconnect-user from Framo", request("POST", "/disconnect-user")[0] == 200
          and thread_safety.get_user_info_copy()["name"] is None)

    section("HTTP: control switch and commands")
    status_code, _, body = command("status", {})
    check("control off: 403 control_disabled", status_code == 403 and json.loads(body) == {"ok": False, "error": "control_disabled"})
    info = json.loads(request("GET", "/bridge-info")[2])
    check("bridge-info", info == {"protocol": 1, "bridgeVersion": "0.5.0",
                                  "blenderVersion": ".".join(map(str, bpy.app.version)), "controlEnabled": False}, str(info))
    thread_safety.set_control_enabled(True)
    check("bridge-info reports the switch", json.loads(request("GET", "/bridge-info")[2])["controlEnabled"] is True)
    status_code, headers, body = pump_while(lambda: command("status", {}))
    answer = json.loads(body)
    check("control on: status runs on the main thread", status_code == 200 and answer["ok"]
          and answer["result"]["bridgeVersion"] == "0.5.0" and headers.get("Access-Control-Allow-Origin") == FRAMO, str(answer)[:200])
    check("panel status line recorded", thread_safety.get_control_state_copy()["last_op"] == "status")
    check("wrong content type refused", request("POST", "/command", {"id": "x", "op": "status", "payload": {}},
                                                headers={"Content-Type": "text/plain"})[0] == 415)
    check("oversized body refused unread", request("POST", "/command", b"{}", headers={
        "Content-Type": "application/json", "Content-Length": str(3 * 1024 * 1024)})[0] == 413)
    status_code, _, body = request("POST", "/command", {"id": "x", "op": "format_disk", "payload": {}},
                                   headers={"Content-Type": "application/json"})
    check("unknown op refused", status_code == 400 and "unknown op" in json.loads(body)["error"])
    check("NaN refused", request("POST", "/command", b'{"id":"x","op":"status","payload":{"a":NaN}}',
                                 headers={"Content-Type": "application/json"})[0] == 400)
    status_code, _, body = pump_while(lambda: command("build_scene", {"spec": {"objects": [{"id": "bad"}]}}))
    check("op error answered as ok:false", status_code == 200 and json.loads(body)["ok"] is False)

    late = {}
    late_thread = threading.Thread(target=lambda: late.update(commands.submit(
        "late", "build_scene", {"spec": {"objects": [{"id": "late", "primitive": "box", "size": [1, 1, 1],
                                                      "position": [0, 0, 0]}]}}, timeout=0.2)))
    late_thread.start()
    late_thread.join()
    commands.process_pending_jobs()
    check("timeout answers timeout and the command never runs late",
          late == {"ok": False, "error": "timeout"} and framo_object("late") is None, str(late))

    section("HTTP: render and download")

    def render_over_http():
        code, _, body = command("render_start", {"engine": "workbench"})
        job = json.loads(body)["result"]["jobId"]
        seen = set()
        deadline = time.time() + 120
        while time.time() < deadline:
            st = json.loads(request("GET", f"/render/{job}")[2])
            seen.add(st["state"])
            if st["state"] in ("done", "failed"):
                break
            time.sleep(0.05)
        return code, job, st, seen

    code, job, final, seen = pump_while(render_over_http)
    check("render over HTTP finished", code == 200 and final["state"] == "done" and final["totalFrames"] == 12
          and final["width"] == 128 and final["bytes"] > 0 and abs(final["durationSec"] - 0.5) < 1e-6, str(final))
    status_code, headers, body = request("GET", f"/render/{job}/file")
    check("clip downloads as video/mp4", status_code == 200 and headers.get("Content-Type") == "video/mp4"
          and len(body) == final["bytes"])
    check("file route refused to a foreign origin", request("GET", f"/render/{job}/file", origin="https://evil.example")[0] == 403)
    check("unknown job 404", request("GET", "/render/" + "0" * 32)[0] == 404)
    check("unknown job file 404", request("GET", "/render/" + "0" * 32 + "/file")[0] == 404)

    # -----------------------------------------------------------------------
    section("panel draws")
    from stub_layout import exercise_panel
    from framo_bridge.ui.panels import FRAMO_PT_export_panel
    log = exercise_panel(FRAMO_PT_export_panel, bpy.context)
    check("panel shows the control switch", ("prop", "WindowManager", "framo_control_enabled") in log)
    check("panel shows the last command", any(e[0] == "label" and e[1].startswith("Last: ") for e in log))

    check("user cube untouched at the end", user_cube_untouched())

except Exception:
    traceback.print_exc()
    FAILED.append("exception (see traceback)")
finally:
    section("unregister")
    try:
        framo_bridge.unregister()
        check("switch property removed", not hasattr(bpy.types.WindowManager, "framo_control_enabled"))
        check("timer removed", not bpy.app.timers.is_registered(commands._timer))
        check("control off after unregister", thread_safety.is_control_enabled() is False)
        check("server stopped", preview_server.server_instance is None and not port_in_use(8080))
    except Exception:
        traceback.print_exc()
        FAILED.append("unregister raised")
    shutil.rmtree(PKG_ROOT, ignore_errors=True)

print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
for failure in FAILED:
    print(f"  FAILED: {failure}")
if FAILED:
    raise SystemExit(1)
