"""End-to-end check of change tracking, in a real Blender window.

    blender --factory-startup --enable-event-simulate --window-geometry 0 0 900 600 \\
        --python tests/live/gui_check.py

It needs a window: background Blender runs no timers and no modal operators,
which are exactly what tracking depends on. ``--enable-event-simulate`` lets the
script finish a real modal drag, and shuts real input out of the window while
it runs. Exits 0 when every check passed, 1 otherwise.

The add-on is loaded from this working tree, on its own port, so it runs next
to a Blender that holds 8080. A client thread stands in for Framo's editor:
the same ``?live=1`` poll, the same Origin, the same two requests.
"""

import http.client
import json
import os
import shutil
import struct
import sys
import tempfile
import threading
import time
import traceback

import bmesh
import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
PORT = 18080
RESULTS = []

# ---------------------------------------------------------------------------
# Load the working tree as framo_bridge

PKG_ROOT = tempfile.mkdtemp(prefix="framo_bridge_live_")
PKG = os.path.join(PKG_ROOT, "framo_bridge")
os.mkdir(PKG)
shutil.copy(os.path.join(REPO, "__init__.py"), PKG)
for entry in ("control", "core", "processing", "services", "ui", "utils", "icons"):
    shutil.copytree(os.path.join(REPO, entry), os.path.join(PKG, entry), ignore=shutil.ignore_patterns("__pycache__"))
sys.path.insert(0, PKG_ROOT)

from framo_bridge.services import request_guard  # noqa: E402

request_guard.PORT = PORT
request_guard.ALLOWED_HOSTS = frozenset({f"localhost:{PORT}", f"127.0.0.1:{PORT}"})

import framo_bridge  # noqa: E402
from framo_bridge.services import framo_collection, live_watch, live_worker, preview_server  # noqa: E402
from framo_bridge.utils.thread_safety import safe_user_info_access  # noqa: E402


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))
    print(f"  {'ok  ' if condition else 'FAIL'} {name} {'' if condition else detail}", flush=True)


# ---------------------------------------------------------------------------
# The stand-in for Framo's editor


class Editor(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.following = False
        self.landed = None
        self.sends = []

    def _get(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=5)
        conn.request("GET", path, headers={"Origin": "https://framo.app", "Host": f"localhost:{PORT}"})
        res = conn.getresponse()
        body = res.read()
        conn.close()
        return res.status, body

    def run(self):
        while True:
            time.sleep(0.2)
            if not self.following:
                continue
            try:
                status, body = self._get("/latest-model-info?live=1")
                if status != 200:
                    continue
                info = json.loads(body)
                if info["timestamp"] != self.landed:
                    self.landed = info["timestamp"]
                    self.sends.append((time.monotonic(), info, self._get("/latest-model")[1]))
            except OSError:
                pass


editor = Editor()


def parse(glb):
    length = struct.unpack_from("<I", glb, 12)[0]
    return json.loads(glb[20:20 + length])


def names(glb):
    return sorted(n["name"] for n in parse(glb).get("nodes", []) if "name" in n)


def node(glb, name):
    return next(n for n in parse(glb)["nodes"] if n.get("name") == name)


def image_width(glb):
    """The width of the first image inside the GLB (WebP's VP8/VP8L/VP8X headers)."""
    document = parse(glb)
    view = document["bufferViews"][document["images"][0]["bufferView"]]
    start = 20 + struct.unpack_from("<I", glb, 12)[0] + 8 + view.get("byteOffset", 0)
    data = glb[start:start + view["byteLength"]]
    if data[12:16] == b"VP8 ":
        return struct.unpack_from("<H", data, 26)[0] & 0x3FFF
    if data[12:16] == b"VP8L":
        return (struct.unpack_from("<I", data, 21)[0] & 0x3FFF) + 1
    return int.from_bytes(data[24:27], "little") + 1


def bounds(glb, name):
    document = parse(glb)
    mesh = document["meshes"][node(glb, name)["mesh"]]
    accessor = document["accessors"][mesh["primitives"][0]["attributes"]["POSITION"]]
    return accessor["min"], accessor["max"]


# ---------------------------------------------------------------------------
# Driving Blender


def window():
    return bpy.context.window_manager.windows[0]


def view_layer():
    return window().view_layer


def ui():
    w = window()
    area = next(a for a in w.screen.areas if a.type == "VIEW_3D")
    region = next(r for r in area.regions if r.type == "WINDOW")
    return bpy.context.temp_override(window=w, screen=w.screen, area=area, region=region)


def into(collection, obj):
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    collection.objects.link(obj)
    return obj


def add_mesh(name, op, collection, **kwargs):
    with ui():
        op(**kwargs)
        obj = bpy.context.view_layer.objects.active
    obj.name = name
    return into(collection, obj)


def wait(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        yield


def settle():
    quiet = time.monotonic()
    while time.monotonic() - quiet < 0.5:
        if live_worker.busy() or live_watch._pacer.pending:
            quiet = time.monotonic()
        yield


def next_send(timeout=20.0):
    """Wait for one send; the bytes, or None. A generator: use with ``yield from``."""
    seen = len(editor.sends)
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if len(editor.sends) > seen:
            # An export takes a while, so this one may predate the change: wait until nothing is left to come.
            yield from settle()
            return editor.sends[-1][2]
        yield
    return None


def no_send(seconds=1.2):
    seen = len(editor.sends)
    yield from wait(seconds)
    return len(editor.sends) == seen


def scenario():
    scene = bpy.context.scene
    settings = scene.framo_export_settings
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)
    outside = bpy.data.collections.new("Outside")
    scene.collection.children.link(outside)
    cone = add_mesh("Cone", bpy.ops.mesh.primitive_cone_add, outside)
    yield from wait(0.3)

    print("\n== Before Framo follows", flush=True)
    check("the add-on adds the collection when it starts", framo_collection.find() is not None)
    check("it is in the scene from the start", framo_collection.in_scene(scene))
    check("tracking is off", not live_watch.status()["following"])

    print("\n== Framo turns Live on", flush=True)
    editor.following = True
    yield from wait(0.8)
    check("tracking starts from the polls alone", live_watch.status()["following"])
    to_framo = framo_collection.find()
    check("an empty collection sends nothing", len(editor.sends) == 0)

    print("\n== Adding to the collection", flush=True)
    cube = add_mesh("Cube", bpy.ops.mesh.primitive_cube_add, to_framo, size=2)
    glb = yield from next_send()
    check("adding an object sends it", glb is not None)
    check("only what is in the collection goes", glb and names(glb) == ["Cube"], glb and names(glb))

    print("\n== What must not send", flush=True)
    cone.select_set(True, view_layer=view_layer())
    check("selecting sends nothing", (yield from no_send()))
    cone.select_set(False, view_layer=view_layer())
    view_layer().objects.active = cone
    check("changing the active object sends nothing", (yield from no_send()))
    cone.location.x += 3
    check("moving an object outside sends nothing", (yield from no_send()))

    print("\n== Edits inside", flush=True)
    cube.location = (1, 2, 3)
    glb = yield from next_send()
    check("a move sends", glb is not None)
    check("the move is in the send (Blender Z is glTF Y)", glb and abs(node(glb, "Cube")["translation"][1] - 3) < 1e-4)

    mat = bpy.data.materials.new("Paint")
    cube.data.materials.append(mat)
    glb = yield from next_send()
    check("adding a material sends", glb is not None)
    mat.diffuse_color = (1, 0, 0, 1)
    check("changing a material sends", (yield from next_send()) is not None)

    bevel = cube.modifiers.new("Bevel", "BEVEL")
    glb = yield from next_send()
    check("adding a modifier sends", glb is not None)
    check("modifiers are applied in the send", glb and bounds(glb, "Cube")[1][0] <= 1.0001)
    bevel.width = 0.3
    glb = yield from next_send()
    check("changing a modifier sends", glb is not None)
    cube.modifiers.remove(bevel)
    yield from next_send()

    cube.name = "Box"
    glb = yield from next_send()
    check("a rename sends", glb and names(glb) == ["Box"], glb and names(glb))
    cube.name = "Cube"
    yield from next_send()

    print("\n== Lights, cameras, nested collections", flush=True)
    lamp = bpy.data.objects.new("Lamp", bpy.data.lights.new("Lamp", "POINT"))
    to_framo.objects.link(lamp)
    glb = yield from next_send()
    check("a light sends", glb and "Lamp" in names(glb), glb and names(glb))
    check("it arrives as a light", glb and "KHR_lights_punctual" in parse(glb).get("extensions", {}))
    lamp.data.energy = 500
    glb = yield from next_send()
    check("changing a light sends", glb is not None)
    cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
    to_framo.objects.link(cam)
    glb = yield from next_send()
    check("a camera sends", glb and "cameras" in parse(glb))
    nested = bpy.data.collections.new("Nested")
    to_framo.children.link(nested)
    sphere = add_mesh("Sphere", bpy.ops.mesh.primitive_uv_sphere_add, nested)
    glb = yield from next_send()
    check("a nested collection counts", glb and "Sphere" in names(glb), glb and names(glb))

    print("\n== Hiding and removing", flush=True)
    sphere.hide_set(True, view_layer=view_layer())
    glb = yield from next_send()
    check("hiding takes it out", glb and "Sphere" not in names(glb), glb and names(glb))
    sphere.hide_set(False, view_layer=view_layer())
    glb = yield from next_send()
    check("unhiding brings it back", glb and "Sphere" in names(glb), glb and names(glb))
    into(outside, sphere)
    glb = yield from next_send()
    check("moving it out of the collection takes it out", glb and "Sphere" not in names(glb), glb and names(glb))

    print("\n== Collection instances", flush=True)
    source = bpy.data.collections.new("Source")
    scene.collection.children.link(source)
    rock = add_mesh("Rock", bpy.ops.mesh.primitive_ico_sphere_add, source)
    empty = bpy.data.objects.new("Rocks", None)
    empty.instance_type = "COLLECTION"
    empty.instance_collection = source
    to_framo.objects.link(empty)
    yield from next_send()
    rock.scale = (3, 3, 3)
    check("editing what an instance shows sends", (yield from next_send()) is not None)
    to_framo.objects.unlink(empty)
    yield from next_send()

    print("\n== Textures", flush=True)
    settings.enable_texture_optimization = True
    settings.texture_max_size = "1024"
    images_before = set(bpy.data.images)
    image = bpy.data.images.new("Big", 2048, 2048)
    image.filepath_raw = os.path.join(PKG_ROOT, "big.png")
    image.file_format = "PNG"
    image.save()
    mat.use_nodes = True
    tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
    tex.image = image
    mat.node_tree.links.new(tex.outputs[0], mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"])
    glb = yield from next_send(10)
    check("a textured material sends", glb and parse(glb).get("images"))
    check("the texture is shrunk in the send", glb and image_width(glb) == 1024, glb and image_width(glb))
    check("the original image is back on the material", tex.image == image and image.size[0] == 2048)
    check("no scaled copy is left", set(bpy.data.images) == images_before | {image}, [i.name for i in bpy.data.images])
    mat.node_tree.nodes.remove(tex)
    yield from next_send()

    print("\n== Edit Mode", flush=True)
    meshes = len(bpy.data.meshes)
    objects = len(bpy.data.objects)
    collections = len(bpy.data.collections)
    with ui():
        bpy.ops.object.select_all(action="DESELECT")
        view_layer().objects.active = cube
        cube.select_set(True)
        bpy.ops.object.mode_set(mode="EDIT")
    yield from next_send(1.0)
    with ui():
        bpy.ops.ed.undo_push(message="before the edit")
    bm = bmesh.from_edit_mesh(cube.data)
    for v in bm.verts:
        v.co.z += 10
    bmesh.update_edit_mesh(cube.data)
    with ui():
        bpy.ops.ed.undo_push(message="the edit")
    glb = yield from next_send()
    check("an edit in Edit Mode sends, with no leaving it", glb is not None)
    check("the edit is in the send", glb and bounds(glb, "Cube")[1][1] > 10.5, glb and bounds(glb, "Cube"))
    check("the node keeps its own name", glb and "Cube" in names(glb), glb and names(glb))
    check("Blender is still in Edit Mode", cube.mode == "EDIT", cube.mode)
    check("the active object is still the same", view_layer().objects.active == cube)
    check("the selection is still the same", cube.select_get(view_layer=view_layer()) and not cone.select_get(view_layer=view_layer()))
    check("no object is left behind", len(bpy.data.objects) == objects, [o.name for o in bpy.data.objects])
    check("no mesh is left behind", len(bpy.data.meshes) == meshes, [m.name for m in bpy.data.meshes])
    check("no collection is left behind", len(bpy.data.collections) == collections)
    bm = bmesh.from_edit_mesh(cube.data)
    bm.verts.ensure_lookup_table()
    check("the edit mesh is still usable after a send", len(bm.verts) == 8)
    with ui():
        bpy.ops.ed.undo()
    yield from wait(0.1)
    bm = bmesh.from_edit_mesh(cube.data)
    check("Ctrl+Z takes back the edit, not a send", max(v.co.z for v in bm.verts) < 1.5, max(v.co.z for v in bm.verts))
    glb = yield from next_send()
    check("the undo reaches Framo", glb and bounds(glb, "Cube")[1][1] < 1.5, glb and bounds(glb, "Cube"))

    print("\n== A drag", flush=True)
    with ui():
        bpy.ops.object.mode_set(mode="OBJECT")
    yield from next_send(1.0)
    before = cube.matrix_world.translation.copy()
    with ui():
        bpy.ops.transform.translate("INVOKE_DEFAULT")
    yield from wait(0.1)
    modal = [op.bl_idname for op in window().modal_operators]
    check("the drag is a running modal", "TRANSFORM_OT_translate" in modal, modal)
    w = window()
    w.event_simulate(type="MOUSEMOVE", value="NOTHING", x=420, y=320)
    yield from wait(0.1)
    w.event_simulate(type="MOUSEMOVE", value="NOTHING", x=520, y=380)
    check("nothing is sent while the drag runs", (yield from no_send()))
    w.event_simulate(type="RET", value="PRESS")
    w.event_simulate(type="RET", value="RELEASE")
    glb = yield from next_send()
    check("letting go sends", glb is not None)
    check("the drag has ended", not window().modal_operators, list(window().modal_operators))
    check("the drag moved the object", (cube.matrix_world.translation - before).length > 0.01, cube.matrix_world.translation)

    print("\n== Other modes and other objects", flush=True)
    with ui():
        view_layer().objects.active = cube
        bpy.ops.object.mode_set(mode="SCULPT")
    yield from wait(0.5)
    cube.location.x += 1
    glb = yield from next_send()
    check("a send in Sculpt Mode works", glb is not None, live_watch.status())
    check("Sculpt Mode stays on", cube.mode == "SCULPT", cube.mode)
    with ui():
        bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        view_layer().objects.active = cone
        cone.select_set(True)
        bpy.ops.object.mode_set(mode="EDIT")
    yield from wait(0.5)
    cube.location.x += 1
    glb = yield from next_send()
    check("editing something outside does not stop a send", glb is not None, live_watch.status())
    check("Edit Mode on the outside object stays on", cone.mode == "EDIT", cone.mode)
    with ui():
        bpy.ops.object.mode_set(mode="OBJECT")

    print("\n== A modifier that reads something outside", flush=True)
    cutter = add_mesh("Cutter", bpy.ops.mesh.primitive_cube_add, outside, size=1, location=cube.location)
    boolean = cube.modifiers.new("Cut", "BOOLEAN")
    boolean.object = cutter
    glb = yield from next_send()
    check("the outside object shapes the send and stays out of it", glb and "Cutter" not in names(glb) and "Cube" in names(glb), glb and names(glb))
    cube.modifiers.remove(boolean)
    bpy.data.objects.remove(cutter)
    yield from next_send()

    print("\n== A posed armature", flush=True)
    with ui():
        bpy.ops.object.armature_add(location=(0, 0, 0))
        arm = bpy.context.view_layer.objects.active
    arm.name = "Rig"
    into(to_framo, arm)
    limb = add_mesh("Limb", bpy.ops.mesh.primitive_cube_add, to_framo, size=1, location=(0, 0, 0.5))
    limb.vertex_groups.new(name="Bone").add(range(8), 1.0, "REPLACE")
    limb.modifiers.new("Armature", "ARMATURE").object = arm
    yield from next_send()
    arm.pose.bones["Bone"].location = (0, 3, 0)
    glb = yield from next_send()
    check("posing sends", glb is not None)
    check("the pose is in the send", glb and max(abs(v) for v in bounds(glb, "Limb")[1] + bounds(glb, "Limb")[0]) > 2.5, glb and bounds(glb, "Limb"))
    for o in (limb, arm):
        bpy.data.objects.remove(o)
    yield from next_send()

    print("\n== A heavy mesh", flush=True)
    heavy = add_mesh("Heavy", bpy.ops.mesh.primitive_grid_add, to_framo, x_subdivisions=360, y_subdivisions=360, size=4)
    heavy.modifiers.new("Sub", "SUBSURF").levels = 1
    yield from next_send(60)
    started = time.monotonic()
    worst = 0.0
    last = started
    seen = len(editor.sends)
    heavy.location.z = 5
    # This loop is a tick of Blender's main thread: the longest gap between ticks is the longest freeze.
    while len(editor.sends) == seen and time.monotonic() - started < 60:
        now = time.monotonic()
        worst = max(worst, now - last)
        last = now
        yield
    print(f"  edit to Framo, {len(heavy.data.polygons) * 4} faces: {editor.sends[-1][0] - started:.2f} s, longest freeze {worst * 1000:.0f} ms", flush=True)
    check("a heavy mesh reaches Framo", len(editor.sends) > seen)
    check("Blender never froze for it", worst < 0.25, worst)
    bpy.data.objects.remove(heavy)
    yield from next_send(30)

    print("\n== Emptying", flush=True)
    for obj in list(to_framo.all_objects):
        bpy.data.objects.remove(obj)
    glb = yield from next_send()
    check("an emptied collection empties Framo", glb is not None and names(glb) == [], glb and names(glb))

    print("\n== Framo turns Live off", flush=True)
    editor.following = False
    yield from wait(3.5)
    check("tracking stops by itself", not live_watch.status()["following"])
    seen = len(editor.sends)
    cone.location.x += 1
    yield from wait(0.5)
    check("nothing is sent once it is off", len(editor.sends) == seen)
    check("no failure was reported on the way", live_watch.status()["error"] is None, live_watch.status())

    print("\n== The Send to Framo button", flush=True)
    with safe_user_info_access() as user:
        user["name"] = "Test"
    with ui():
        check("the button is off with nothing in the collection", not bpy.ops.framo.export_to_web.poll())
    button = add_mesh("Button", bpy.ops.mesh.primitive_cube_add, to_framo)
    with ui():
        bpy.ops.object.select_all(action="DESELECT")
        view_layer().objects.active = cone
        cone.select_set(True)
        check("it is on with something in the collection, whatever is selected", bpy.ops.framo.export_to_web.poll())
        bpy.ops.framo.export_to_web()
    yield from wait(0.5)
    server = preview_server.server_instance
    check("it sends the collection, not the selection", names(server.latest_glb) == ["Button"], names(server.latest_glb))
    check("a send by hand is not marked live", server.latest_metadata["live"] is False)
    check("the selection comes back", cone.select_get(view_layer=view_layer()) and not button.select_get(view_layer=view_layer()))
    check("the active object comes back", view_layer().objects.active == cone)
    check("the object keeps its own mesh", not button.data.name.startswith("TEMP_DATA_"), button.data.name)
    with ui():
        bpy.ops.object.mode_set(mode="EDIT")
        check("it is off in Edit Mode", not bpy.ops.framo.export_to_web.poll())
        bpy.ops.object.mode_set(mode="OBJECT")

    print("\n== Opening a file", flush=True)
    bpy.data.collections.remove(to_framo)
    path = os.path.join(PKG_ROOT, "plain.blend")
    with ui():
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
    yield from wait(0.5)
    check("a file without the collection gets it on opening", framo_collection.in_scene(window().scene))


def finish():
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed", flush=True)
    for name, _ok, detail in failed:
        print(f"  FAIL {name} {detail}", flush=True)
    os._exit(1 if failed or not RESULTS else 0)


driver = None


def tick():
    try:
        next(driver)
    except StopIteration:
        finish()
    except Exception:
        traceback.print_exc()
        check("the scenario ran to the end", False)
        finish()
    return 0.02


def start():
    global driver
    framo_bridge.register()
    editor.start()
    driver = scenario()
    bpy.app.timers.register(tick, first_interval=0.5, persistent=True)
    return None


bpy.app.timers.register(start, first_interval=1.0)
