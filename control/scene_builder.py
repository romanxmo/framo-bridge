"""Build a Framo scene description in Blender, and read the scene back.

Nothing here knows about HTTP, threads or the add-on's UI: it takes a scene and
a validated description and changes the file, so the same builder can run in a
headless Blender on a server later. Call it on Blender's main thread only.

What must stay true:

- **The user's objects are context, never targets.** Every object Framo makes
  carries a ``framo_id`` custom property and the name Framo gave it
  (``framo_name``). An object without both -- including a copy the user made of
  a Framo object, which keeps the property but gets a new name -- is never
  moved, edited or deleted.
- **Idempotent by id.** Applying the same description twice changes nothing;
  an edited description changes only what was edited. What each object was
  built from is stored on it, so the builder knows what changed without
  guessing from the geometry.
- Metres, Z up. ``position`` is the centre of an object's base; rotations are
  degrees, Euler XYZ, about that base point.
"""

import json
import math

import bmesh
import bpy
from mathutils import Vector

from . import asset_source

COLLECTION_NAME = "Framo Scene"

PROP_ID = "framo_id"
PROP_ROLE = "framo_role"
PROP_NAME = "framo_name"
PROP_SOURCE = "framo_source"
PROP_PLACEMENT = "framo_placement"

ROLE_OBJECT = "object"
ROLE_LIGHT = "light"
ROLE_CAMERA = "camera"
ROLE_CAMERA_TARGET = "camera_target"
ROLE_ASSET_PART = "asset_part"
_ROOT_ROLES = (ROLE_OBJECT, ROLE_LIGHT, ROLE_CAMERA, ROLE_CAMERA_TARGET)

# Not spec ids (a spec id may well be "__camera__"); camera lookups key on the
# role as well, so the two can never meet.
CAMERA_ID = "__camera__"
CAMERA_TARGET_ID = "__camera_target__"

DEFAULT_COLOR = "#cccccc"
DEFAULT_ROUGHNESS = 0.5
DEFAULT_METALLIC = 0.0
DEFAULT_LIGHT_COLOR = "#ffffff"

_LIGHT_TYPES = {"sun": "SUN", "point": "POINT", "area": "AREA", "spot": "SPOT"}

# Types whose bound_box describes real geometry; anything else (empties,
# lights, cameras) counts as a point at its origin.
_GEOMETRY_TYPES = {
    "MESH", "CURVE", "SURFACE", "META", "FONT", "CURVES", "POINTCLOUD", "VOLUME",
    "GPENCIL", "GREASEPENCIL", "LATTICE", "ARMATURE",
}

_SEGMENTS = 32
_EPSILON = 1e-5


class BuildError(Exception):
    """A refusal phrased for the model."""


# --------------------------------------------------------------------------
# Ownership


def is_framo_owned(obj):
    """Framo made this object, and it is still the object Framo made."""
    return PROP_ID in obj and obj.get(PROP_NAME) == obj.name


def _tag(id_block, framo_id, role=None):
    id_block[PROP_ID] = framo_id
    if role is not None:
        id_block[PROP_ROLE] = role
        id_block[PROP_NAME] = id_block.name


def _take_base_name(obj, framo_id):
    """After a rebuild, give the new object the name the old one held ("Framo <id>")."""
    obj.name = f"Framo {framo_id}"
    obj[PROP_NAME] = obj.name


def framo_roots(scene):
    """{(role, id): object} for every Framo root object in the scene."""
    roots = {}
    for obj in scene.objects:
        if is_framo_owned(obj) and obj.get(PROP_ROLE) in _ROOT_ROLES:
            roots.setdefault((obj[PROP_ROLE], obj[PROP_ID]), obj)
    return roots


def _owned_tree(root):
    return [c for c in root.children_recursive if is_framo_owned(c)] + [root]


_DATA_COLLECTIONS = (
    ("meshes", bpy.types.Mesh),
    ("materials", bpy.types.Material),
    ("lights", bpy.types.Light),
    ("cameras", bpy.types.Camera),
    ("images", bpy.types.Image),
    ("actions", bpy.types.Action),
)


def _remove_datablock(block):
    """Remove a datablock Framo made, once nothing uses it any more."""
    if PROP_ID not in block or block.users > 0:
        return
    for attr, cls in _DATA_COLLECTIONS:
        if isinstance(block, cls):
            getattr(bpy.data, attr).remove(block)
            return


def remove_framo_tree(root):
    """Delete a Framo root, its Framo-made parts, and the data only they used."""
    objects = _owned_tree(root)
    if not all(is_framo_owned(o) for o in objects):
        raise BuildError("internal: refused to delete an object Framo did not make")
    # Ordered so each block's users are gone before it is checked: an object's
    # mesh, then the materials the mesh held, then the images they used.
    # Keyed by pointer, because touching a block after removing it raises.
    data = {}
    for obj in objects:
        blocks = [obj.data] if obj.data is not None else []
        if obj.animation_data is not None and obj.animation_data.action is not None:
            blocks.append(obj.animation_data.action)
        for slot in obj.material_slots:
            if slot.material is not None:
                blocks.append(slot.material)
                blocks.extend(_material_images(slot.material))
        for block in blocks:
            data.setdefault(block.as_pointer(), block)
    for obj in objects:
        bpy.data.objects.remove(obj, do_unlink=True)
    for block in data.values():
        _remove_datablock(block)


def _material_images(material):
    tree = getattr(material, "node_tree", None)
    if tree is None:
        return []
    return [n.image for n in tree.nodes if getattr(n, "image", None) is not None]


def refresh_transforms(scene):
    """Bring ``matrix_world`` up to date after edits made in this same tick.

    Setting ``location`` or a parent does not recompute world matrices until
    the depsgraph next evaluates, so anything read right after a build would
    describe where objects were before it.
    """
    view_layer = bpy.context.view_layer
    if view_layer is None or view_layer not in scene.view_layers[:]:
        view_layer = scene.view_layers[0]
    view_layer.update()


def framo_collection(scene):
    coll = bpy.data.collections.get(COLLECTION_NAME)
    if coll is None:
        coll = bpy.data.collections.new(COLLECTION_NAME)
    if coll.name not in scene.collection.children:
        scene.collection.children.link(coll)
    return coll


# --------------------------------------------------------------------------
# Small conversions


def _srgb_to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def hex_to_linear_rgb(hex_color):
    """'#rrggbb' (sRGB, as people write colours) to Blender's scene-linear RGB."""
    return tuple(_srgb_to_linear(int(hex_color[i:i + 2], 16) / 255.0) for i in (1, 3, 5))


def _radians(degrees):
    return tuple(math.radians(d) for d in degrees)


def _signature(data):
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def _close(a, b):
    return all(abs(x - y) <= _EPSILON for x, y in zip(a, b))


def _r3(values, places=3):
    # + 0.0 turns -0.0 into 0.0, which reads better to a model
    return [round(v, places) + 0.0 for v in values]


def _degrees(euler):
    return [math.degrees(a) for a in euler]


# --------------------------------------------------------------------------
# Animation


def _fcurves(id_data):
    """The F-curves animating an ID, on the action API of any version.

    Blender 4.4 introduced slotted actions and 5.0 removed ``Action.fcurves``;
    the curves now live in the channelbag of the slot the ID is assigned.
    """
    anim = id_data.animation_data
    if anim is None or anim.action is None:
        return []
    action = anim.action
    if hasattr(action, "layers"):
        from bpy_extras import anim_utils
        if hasattr(anim_utils, "action_get_channelbag_for_slot"):
            bag = anim_utils.action_get_channelbag_for_slot(action, anim.action_slot)
            return list(bag.fcurves) if bag is not None else []
    return list(action.fcurves)


def _smooth_keys(id_data):
    """Bezier with auto-clamped handles: eases in and out, never overshoots."""
    for fcurve in _fcurves(id_data):
        for point in fcurve.keyframe_points:
            point.interpolation = "BEZIER"
            point.handle_left_type = "AUTO_CLAMPED"
            point.handle_right_type = "AUTO_CLAMPED"
        fcurve.update()


def _clear_animation(id_data):
    if id_data.animation_data is not None:
        action = id_data.animation_data.action
        id_data.animation_data_clear()
        if action is not None and action.users == 0 and PROP_ID in action:
            bpy.data.actions.remove(action)


def _tag_action(id_data, framo_id):
    if id_data.animation_data is not None and id_data.animation_data.action is not None:
        id_data.animation_data.action[PROP_ID] = framo_id


# --------------------------------------------------------------------------
# Primitives


def build_primitive_mesh(name, primitive, size):
    """A mesh with its origin at the centre of its base, built with bmesh."""
    sx, sy, sz = size
    bm = bmesh.new()
    try:
        if primitive == "box":
            bmesh.ops.create_cube(bm, size=1.0)
            bmesh.ops.scale(bm, vec=(sx, sy, sz), verts=bm.verts)
            lift = sz / 2.0
        elif primitive == "sphere":
            diameter = max(size)
            bmesh.ops.create_uvsphere(bm, u_segments=_SEGMENTS, v_segments=_SEGMENTS // 2, radius=diameter / 2.0)
            for face in bm.faces:
                face.smooth = True
            lift = diameter / 2.0
        elif primitive in ("cylinder", "cone"):
            radius = max(sx, sy) / 2.0
            bmesh.ops.create_cone(
                bm, cap_ends=True, cap_tris=False, segments=_SEGMENTS,
                radius1=radius, radius2=radius if primitive == "cylinder" else 0.0, depth=sz,
            )
            lift = sz / 2.0
        elif primitive == "plane":
            hx, hy = sx / 2.0, sy / 2.0
            verts = [bm.verts.new(co) for co in ((-hx, -hy, 0), (hx, -hy, 0), (hx, hy, 0), (-hx, hy, 0))]
            bm.faces.new(verts)
            lift = 0.0
        else:
            raise BuildError(f"unknown primitive {primitive!r}")
        if lift:
            bmesh.ops.translate(bm, vec=(0.0, 0.0, lift), verts=bm.verts)
        mesh = bpy.data.meshes.new(name)
        bm.to_mesh(mesh)
    finally:
        bm.free()
    return mesh


def build_material(name, color, roughness, metallic):
    material = bpy.data.materials.new(name)
    if bpy.app.version < (5, 0, 0):
        # 5.0 gives every new material a node tree and deprecates the switch.
        material.use_nodes = True
    bsdf = next((n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        raise BuildError("internal: a new material has no Principled BSDF")
    rgba = (*hex_to_linear_rgb(color), 1.0)
    bsdf.inputs["Base Color"].default_value = rgba
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    # The viewport (and Workbench screenshots) read these, not the node tree.
    material.diffuse_color = rgba
    material.roughness = roughness
    material.metallic = metallic
    return material


def _primitive_source(spec):
    return {
        "primitive": spec["primitive"],
        "size": spec["size"],
        "color": spec.get("color", DEFAULT_COLOR),
        "roughness": spec.get("roughness", DEFAULT_ROUGHNESS),
        "metallic": spec.get("metallic", DEFAULT_METALLIC),
    }


def _create_primitive(collection, spec):
    source = _primitive_source(spec)
    name = f"Framo {spec['id']}"
    mesh = build_primitive_mesh(name, source["primitive"], source["size"])
    _tag(mesh, spec["id"])
    material = build_material(name, source["color"], source["roughness"], source["metallic"])
    _tag(material, spec["id"])
    mesh.materials.append(material)
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    _tag(obj, spec["id"], ROLE_OBJECT)
    return obj


# --------------------------------------------------------------------------
# Assets


def import_asset_glb(filepath, framo_id, collection, scale=1.0, view_layer=None):
    """Import a GLB under an Empty named "Framo <id>" and return the Empty.

    Every object the import creates is parented under the Empty, moved into
    ``collection`` and tagged as Framo's, so removing the Empty later removes
    exactly what this import made. The user's selection is put back as it was.
    """
    view_layer = view_layer or bpy.context.view_layer
    before_objects = set(bpy.data.objects)
    before_meshes = set(bpy.data.meshes)
    before_materials = set(bpy.data.materials)
    before_images = set(bpy.data.images)
    before_collections = set(bpy.data.collections)
    selected = [o for o in view_layer.objects if o.select_get(view_layer=view_layer)]
    active = view_layer.objects.active
    try:
        result = bpy.ops.import_scene.gltf(filepath=filepath)
    except RuntimeError as e:
        raise BuildError(f"the asset file could not be imported: {e}") from e
    finally:
        for obj in view_layer.objects:
            if obj.select_get(view_layer=view_layer):
                obj.select_set(False, view_layer=view_layer)
        for obj in selected:
            if obj.name in view_layer.objects:
                obj.select_set(True, view_layer=view_layer)
        view_layer.objects.active = active if active is None or active.name in view_layer.objects else None
    if "FINISHED" not in result:
        raise BuildError("the asset file could not be imported")

    new_objects = [o for o in bpy.data.objects if o not in before_objects]
    if not new_objects:
        raise BuildError("the asset file contained no objects")

    root = bpy.data.objects.new(f"Framo {framo_id}", None)
    root.empty_display_type = "PLAIN_AXES"
    collection.objects.link(root)
    _tag(root, framo_id, ROLE_OBJECT)

    new_set = set(new_objects)
    for obj in new_objects:
        for owner in list(obj.users_collection):
            owner.objects.unlink(obj)
        collection.objects.link(obj)
        if obj.parent is None or obj.parent not in new_set:
            obj.parent = root
        _tag(obj, framo_id, ROLE_ASSET_PART)
    for block in [m for m in bpy.data.meshes if m not in before_meshes]:
        _tag(block, framo_id)
    for block in [m for m in bpy.data.materials if m not in before_materials]:
        _tag(block, framo_id)
    for block in [i for i in bpy.data.images if i not in before_images]:
        _tag(block, framo_id)
    # An importer that made its own collection leaves it empty after the move.
    for coll in [c for c in bpy.data.collections if c not in before_collections]:
        if not coll.objects and not coll.children:
            bpy.data.collections.remove(coll)

    root.scale = (scale, scale, scale)
    view_layer.update()
    return root


def _asset_source(spec, resolved):
    return {"asset": spec["asset"], "height": spec.get("height"), "size": list(resolved["size"])}


def _check_resolved_asset(asset_id, resolved):
    if not isinstance(resolved, dict):
        raise BuildError(f'asset "{asset_id}" was not resolved (not in the assets map)')
    url = resolved.get("url")
    size = resolved.get("size")
    if not isinstance(url, str) or not url:
        raise BuildError(f'asset "{asset_id}" has no download URL')
    if (not isinstance(size, list) or len(size) != 3
            or not all(isinstance(n, (int, float)) and not isinstance(n, bool) and math.isfinite(n) and n >= 0 for n in size)):
        raise BuildError(f'asset "{asset_id}" has no valid catalog size')


def _create_asset(collection, spec, resolved):
    asset_id = spec["asset"]
    _check_resolved_asset(asset_id, resolved)
    scale = 1.0
    if spec.get("height") is not None:
        catalog_height = resolved["size"][2]
        if catalog_height <= 0:
            raise BuildError(f'asset "{asset_id}" has no height in the catalog, so it cannot be scaled to {spec["height"]} m')
        scale = spec["height"] / catalog_height
    if not asset_source.is_allowed_asset_url(resolved["url"]):
        raise BuildError(asset_source.REFUSED_URL)
    if not getattr(bpy.app, "online_access", True):
        # Blender before 4.2 has no such setting, so nothing restricts it there.
        raise BuildError("Blender's online access is off (Preferences > System > Network), so assets cannot be downloaded")
    try:
        path = asset_source.fetch_asset(asset_id, resolved["url"])
    except asset_source.AssetDownloadError as e:
        raise BuildError(str(e)) from e
    return import_asset_glb(path, spec["id"], collection, scale=scale)


# --------------------------------------------------------------------------
# Placement


def _placement(spec):
    return {"position": spec["position"], "rotation": spec.get("rotation", [0, 0, 0]), "keys": spec.get("keys", [])}


def _placement_matches(obj, placement):
    if obj.get(PROP_PLACEMENT) != _signature(placement):
        return False
    if placement["keys"]:
        # Animated: the curves are the truth, and the stored signature names them.
        return True
    # Not animated: the user may have dragged it; re-sending the spec puts it back.
    return (_close(obj.location, placement["position"])
            and _close(obj.rotation_euler, _radians(placement["rotation"]))
            and obj.animation_data is None)


def _apply_placement(obj, framo_id, placement):
    _clear_animation(obj)
    obj.rotation_mode = "XYZ"
    base_location = placement["position"]
    base_rotation = _radians(placement["rotation"])
    for key in placement["keys"]:
        if "position" in key:
            obj.location = key["position"]
            obj.keyframe_insert("location", frame=key["frame"])
        if "rotation" in key:
            obj.rotation_euler = _radians(key["rotation"])
            obj.keyframe_insert("rotation_euler", frame=key["frame"])
    if placement["keys"]:
        _smooth_keys(obj)
        _tag_action(obj, framo_id)
    # A channel no key touches holds the base transform.
    obj.location = base_location
    obj.rotation_euler = base_rotation
    obj[PROP_PLACEMENT] = _signature(placement)


# --------------------------------------------------------------------------
# Lights


def _create_light(collection, spec):
    name = f"Framo {spec['id']}"
    data = bpy.data.lights.new(name, _LIGHT_TYPES[spec["type"]])
    _tag(data, spec["id"])
    data.energy = spec["strength"]
    data.color = hex_to_linear_rgb(spec.get("color", DEFAULT_LIGHT_COLOR))
    if "size" in spec:
        if spec["type"] == "sun":
            data.angle = math.radians(spec["size"])
        elif spec["type"] == "area":
            data.size = spec["size"]
        else:
            data.shadow_soft_size = spec["size"]
    obj = bpy.data.objects.new(name, data)
    collection.objects.link(obj)
    _tag(obj, spec["id"], ROLE_LIGHT)
    return obj


def _light_source(spec):
    return {k: spec.get(k) for k in ("type", "strength", "color", "size")}


# --------------------------------------------------------------------------
# Camera, world, render


def _apply_camera(scene, collection, camera_spec, roots, result):
    camera = roots.get((ROLE_CAMERA, CAMERA_ID))
    target = roots.get((ROLE_CAMERA_TARGET, CAMERA_TARGET_ID))
    created = camera is None
    if camera is None:
        data = bpy.data.cameras.new("Framo Camera")
        _tag(data, CAMERA_ID)
        camera = bpy.data.objects.new("Framo Camera", data)
        collection.objects.link(camera)
        _tag(camera, CAMERA_ID, ROLE_CAMERA)
    target_created = target is None
    if target is None:
        target = bpy.data.objects.new("Framo Camera Target", None)
        target.empty_display_type = "SPHERE"
        target.empty_display_size = 0.1
        collection.objects.link(target)
        _tag(target, CAMERA_TARGET_ID, ROLE_CAMERA_TARGET)

    track = camera.constraints.get("Framo Track")
    signature = _signature(camera_spec)
    unchanged = (
        not created and not target_created
        and camera.get(PROP_SOURCE) == signature
        and scene.camera == camera
        and track is not None and track.target == target
    )
    if unchanged:
        return

    camera.data.lens = camera_spec["lens"]
    camera.data.clip_start = 0.05
    # The default 100 m clips a street; the scene format allows a city block.
    camera.data.clip_end = 2000.0
    if track is None:
        track = camera.constraints.new("TRACK_TO")
        track.name = "Framo Track"
    track.target = target
    track.track_axis = "TRACK_NEGATIVE_Z"
    track.up_axis = "UP_Y"

    _clear_animation(camera)
    _clear_animation(target)
    camera.rotation_euler = (0.0, 0.0, 0.0)
    keys = camera_spec["keys"]
    if len(keys) > 1:
        for key in keys:
            camera.location = key["position"]
            camera.keyframe_insert("location", frame=key["frame"])
            target.location = key["target"]
            target.keyframe_insert("location", frame=key["frame"])
        _smooth_keys(camera)
        _smooth_keys(target)
        _tag_action(camera, CAMERA_ID)
        _tag_action(target, CAMERA_TARGET_ID)
    camera.location = keys[0]["position"]
    target.location = keys[0]["target"]
    camera[PROP_SOURCE] = signature
    scene.camera = camera
    (result["created"] if created else result["updated"]).append(CAMERA_ID)


def _world_background(world):
    tree = world.node_tree
    output = next((n for n in tree.nodes if n.type == "OUTPUT_WORLD" and n.is_active_output), None) \
        or next((n for n in tree.nodes if n.type == "OUTPUT_WORLD"), None)
    if output is not None and output.inputs["Surface"].is_linked:
        node = output.inputs["Surface"].links[0].from_node
        if node.type == "BACKGROUND":
            return node
    if output is None:
        output = tree.nodes.new("ShaderNodeOutputWorld")
    background = tree.nodes.new("ShaderNodeBackground")
    tree.links.new(background.outputs["Background"], output.inputs["Surface"])
    return background


def _apply_world(scene, world_spec, result):
    world = scene.world
    if world is None:
        world = bpy.data.worlds.new("Framo World")
        _tag(world, "__world__")
        scene.world = world
    if bpy.app.version < (5, 0, 0):
        world.use_nodes = True
    background = _world_background(world)
    rgb = hex_to_linear_rgb(world_spec["color"])
    color_input = background.inputs["Color"]
    if color_input.is_linked:
        # An HDRI or texture drives it; rewiring the user's world is not ours to do.
        result["warnings"].append("world: the background colour is driven by a texture (an HDRI?), so only its strength was set")
    elif not _close(color_input.default_value[:3], rgb):
        color_input.default_value = (*rgb, 1.0)
    if abs(background.inputs["Strength"].default_value - world_spec["strength"]) > _EPSILON:
        background.inputs["Strength"].default_value = world_spec["strength"]
    if not _close(world.color, rgb):
        world.color = rgb


def _apply_render(scene, render_spec):
    r = scene.render
    wanted = {
        "fps": render_spec["fps"], "fps_base": 1.0,
        "resolution_x": render_spec["resolution"][0], "resolution_y": render_spec["resolution"][1],
        "resolution_percentage": 100,
    }
    for attr, value in wanted.items():
        if getattr(r, attr) != value:
            setattr(r, attr, value)
    first, last = render_spec["frames"]
    if scene.frame_start != first or scene.frame_end != last:
        # Order matters: Blender clamps frame_start to frame_end when set.
        if first > scene.frame_end:
            scene.frame_end = last
            scene.frame_start = first
        else:
            scene.frame_start = first
            scene.frame_end = last


# --------------------------------------------------------------------------
# Build


def build_scene(scene, spec, assets):
    """Apply a validated scene description. Returns created/updated/removed/warnings.

    ``spec`` must already have passed ``scene_spec.parse_scene_spec``.
    ``assets`` maps each catalog id to ``{"url", "name", "size"}``.
    An object that cannot be built (an unresolved asset, a refused URL) is
    reported in ``warnings`` and left as it was; the rest of the scene builds.
    """
    result = {"created": [], "updated": [], "removed": [], "warnings": []}
    collection = framo_collection(scene)
    roots = framo_roots(scene)
    keep = set()

    for obj_spec in spec["objects"]:
        framo_id = obj_spec["id"]
        keep.add((ROLE_OBJECT, framo_id))
        existing = roots.get((ROLE_OBJECT, framo_id))
        is_asset = "asset" in obj_spec
        try:
            if is_asset:
                resolved = assets.get(obj_spec["asset"])
                _check_resolved_asset(obj_spec["asset"], resolved)
                source = _asset_source(obj_spec, resolved)
            else:
                source = _primitive_source(obj_spec)
            source_sig = _signature(source)
            obj = existing
            rebuilt = False
            if existing is None or existing.get(PROP_SOURCE) != source_sig:
                obj = _create_asset(collection, obj_spec, resolved) if is_asset else _create_primitive(collection, obj_spec)
                obj[PROP_SOURCE] = source_sig
                if existing is not None:
                    remove_framo_tree(existing)
                    _take_base_name(obj, framo_id)
                rebuilt = True
            placement = _placement(obj_spec)
            moved = False
            if rebuilt or not _placement_matches(obj, placement):
                _apply_placement(obj, framo_id, placement)
                moved = True
        except BuildError as e:
            result["warnings"].append(f'objects "{framo_id}": {e}')
            continue
        if existing is None:
            result["created"].append(framo_id)
        elif rebuilt or moved:
            result["updated"].append(framo_id)

    for light_spec in spec.get("lights", []):
        framo_id = light_spec["id"]
        keep.add((ROLE_LIGHT, framo_id))
        existing = roots.get((ROLE_LIGHT, framo_id))
        source_sig = _signature(_light_source(light_spec))
        obj = existing
        rebuilt = False
        if existing is None or existing.get(PROP_SOURCE) != source_sig:
            obj = _create_light(collection, light_spec)
            obj[PROP_SOURCE] = source_sig
            if existing is not None:
                remove_framo_tree(existing)
                _take_base_name(obj, framo_id)
            rebuilt = True
        placement = {"position": light_spec["position"], "rotation": light_spec.get("rotation", [0, 0, 0]), "keys": []}
        moved = False
        if rebuilt or not _placement_matches(obj, placement):
            _apply_placement(obj, framo_id, placement)
            moved = True
        if existing is None:
            result["created"].append(framo_id)
        elif rebuilt or moved:
            result["updated"].append(framo_id)

    for (role, framo_id), root in roots.items():
        if role in (ROLE_OBJECT, ROLE_LIGHT) and (role, framo_id) not in keep:
            remove_framo_tree(root)
            result["removed"].append(framo_id)

    if "camera" in spec:
        _apply_camera(scene, collection, spec["camera"], roots, result)
    if "world" in spec:
        _apply_world(scene, spec["world"], result)
    if "render" in spec:
        _apply_render(scene, spec["render"])
    refresh_transforms(scene)
    return result


# --------------------------------------------------------------------------
# Reading the scene back


def world_bbox(objects):
    """World-space axis-aligned box around the objects, or None for none."""
    lo = [math.inf] * 3
    hi = [-math.inf] * 3
    for obj in objects:
        if obj.type in _GEOMETRY_TYPES:
            points = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
        else:
            points = [obj.matrix_world.translation]
        for p in points:
            for axis in range(3):
                lo[axis] = min(lo[axis], p[axis])
                hi[axis] = max(hi[axis], p[axis])
    if lo[0] == math.inf:
        return None
    return lo, hi


def _asset_dimensions(root):
    """Size of an asset in its own frame, times its scale: what "height" set."""
    inverse = root.matrix_world.inverted_safe()
    lo = [math.inf] * 3
    hi = [-math.inf] * 3
    for obj in root.children_recursive:
        if obj.type not in _GEOMETRY_TYPES:
            continue
        for corner in obj.bound_box:
            p = inverse @ (obj.matrix_world @ Vector(corner))
            for axis in range(3):
                lo[axis] = min(lo[axis], p[axis])
                hi[axis] = max(hi[axis], p[axis])
    if lo[0] == math.inf:
        return [0.0, 0.0, 0.0]
    return [(hi[a] - lo[a]) * abs(root.scale[a]) for a in range(3)]


def is_user_object(obj):
    """Not Framo's, and not inside a Framo object (an asset's imported parts)."""
    node = obj
    while node is not None:
        if is_framo_owned(node):
            return False
        node = node.parent
    return True


def user_top_level_objects(scene):
    return [o for o in scene.objects if o.parent is None and is_user_object(o)]


def read_scene(scene, max_user_objects=150):
    """The scene as get_scene answers it: Framo's objects, and the user's as context."""
    refresh_transforms(scene)
    r = scene.render
    cam = scene.camera
    camera = None
    if cam is not None:
        camera = {
            "name": cam.name,
            "isFramo": is_framo_owned(cam),
            "lens": round(cam.data.lens, 2) if cam.type == "CAMERA" else None,
            "location": _r3(cam.matrix_world.translation),
            "rotation": _r3(_degrees(cam.matrix_world.to_euler("XYZ")), 1),
        }

    framo_objects = []
    framo_lights = []
    for (role, framo_id), obj in sorted(framo_roots(scene).items(), key=lambda item: item[0][1]):
        if role == ROLE_OBJECT:
            try:
                source = json.loads(obj.get(PROP_SOURCE, "{}"))
            except (TypeError, ValueError):
                source = {}
            entry = {
                "id": framo_id,
                "kind": "asset" if "asset" in source else "primitive",
                "position": _r3(obj.location),
                "rotation": _r3(_degrees(obj.rotation_euler), 1),
            }
            if "asset" in source:
                entry["asset"] = source["asset"]
                entry["dimensions"] = _r3(_asset_dimensions(obj))
            else:
                entry["primitive"] = source.get("primitive")
                entry["dimensions"] = _r3(obj.dimensions)
            framo_objects.append(entry)
        elif role == ROLE_LIGHT and obj.type == "LIGHT":
            framo_lights.append({
                "id": framo_id,
                "type": obj.data.type.lower(),
                "position": _r3(obj.location),
                "strength": round(obj.data.energy, 3),
            })

    users = sorted(user_top_level_objects(scene), key=lambda o: o.name)
    user_objects = []
    for obj in users[:max_user_objects]:
        box = world_bbox([obj] + list(obj.children_recursive))
        lo, hi = box if box else ([0.0] * 3, [0.0] * 3)
        user_objects.append({
            "name": obj.name,
            "type": obj.type,
            "location": _r3(obj.matrix_world.translation),
            "rotation": _r3(_degrees(obj.matrix_world.to_euler("XYZ")), 1),
            # World-space extent, children included: the footprint to build around.
            "dimensions": _r3([hi[a] - lo[a] for a in range(3)]),
            "bboxMin": _r3(lo),
            "bboxMax": _r3(hi),
            "childCount": len(obj.children),
        })

    fps = r.fps / r.fps_base
    return {
        "units": "m",
        "unitScale": scene.unit_settings.scale_length,
        "fps": int(fps) if float(fps).is_integer() else round(fps, 3),
        "frameStart": scene.frame_start,
        "frameEnd": scene.frame_end,
        "resolution": [r.resolution_x, r.resolution_y],
        "camera": camera,
        "framo": {"objects": framo_objects, "lights": framo_lights},
        "userObjects": user_objects,
        "truncated": len(users) > max_user_objects,
    }


def count_objects(scene):
    """(user top-level objects, Framo objects) -- the numbers status reports."""
    framo = sum(1 for (role, _id) in framo_roots(scene) if role == ROLE_OBJECT)
    return len(user_top_level_objects(scene)), framo
