"""The scene description Framo sends, validated again on this side.

Mirror of ``shared/blender-scene.ts`` in the Framo app (``parseBlenderSceneSpec``):
the same fields, the same limits, and the same error messages, so a model that
learned to fix one side's complaints can fix the other's. Framo validates before
it queues a command; the bridge validates again because it must not trust
whatever reaches its port.

``tests/fixtures/scene_spec_cases.json`` holds cases whose expected output was
produced by running the TypeScript parser, and ``tests/test_scene_spec.py``
holds this module to them. When the TypeScript side changes, regenerate the
fixtures (see that test's docstring) rather than editing them by hand.

Pure Python, no ``bpy``, no imports from the add-on: the tests load it by path.

Units and axes are Blender's: metres, Z up. An object's ``position`` is the
centre of its BASE; rotations are degrees, Euler XYZ, about that base point.
"""

import math
import re

BLENDER_PRIMITIVES = ("box", "sphere", "cylinder", "cone", "plane")
BLENDER_LIGHT_TYPES = ("sun", "point", "area", "spot")
BLENDER_RENDER_ENGINES = ("workbench", "eevee", "cycles", "scene")

LIMITS = {
    "maxObjects": 200,
    "maxLights": 16,
    "maxKeys": 64,
    "maxFrame": 1200,
    "maxFps": 60,
    "minResolution": 64,
    "maxResolution": 1920,
    # Largest absolute coordinate, in metres. A city block, not a planet.
    "maxCoordinate": 10000,
    "maxSize": 1000,
    "maxIdLength": 64,
}

# fullmatch, not match: Python's "$" also matches before a trailing newline,
# which JavaScript's does not.
_HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}")
_ID_PATTERN = re.compile(r"[A-Za-z0-9_.-]+")
_ARRAY_INDEX = re.compile(r"0|[1-9][0-9]*")

# "absent" and "invalid" are different answers: the TypeScript uses undefined
# for the first and null for the second, and so does every reader below.
ABSENT = object()


def _is_record(value):
    return isinstance(value, dict)


def _finite(value):
    # bool is an int subclass in Python; JSON true is not a number.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except OverflowError:
        # An integer too large for a double is Infinity in JavaScript.
        return False


def _is_integer(value):
    return isinstance(value, int) or float(value).is_integer()


def _js_length(text):
    """String length as JavaScript counts it, in UTF-16 code units."""
    return len(text.encode("utf-16-le")) // 2


def js_num(value):
    """Format a number the way a JavaScript template literal would."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _js_key_order(keys):
    """Object.keys order: integer-like keys ascending first, then insertion order."""
    keys = list(keys)
    index_keys = sorted((k for k in keys if _ARRAY_INDEX.fullmatch(k) and int(k) < 2**32 - 1), key=int)
    rest = [k for k in keys if k not in index_keys]
    return index_keys + rest


class _Reader:
    """Each read pushes an error phrased for the model and returns None.

    One bad field does not hide the next: the agent gets every problem in one
    tool result and fixes them in one retry.
    """

    def __init__(self):
        self.errors = []

    def fail(self, path, message):
        self.errors.append(f"{path}: {message}")
        return None

    def vec3(self, raw, path, limit, positive=False):
        if not isinstance(raw, list) or len(raw) != 3 or not all(_finite(n) for n in raw):
            return self.fail(path, "must be three numbers [x, y, z]")
        if any(abs(n) > limit for n in raw):
            return self.fail(path, f"values must be within ±{js_num(limit)}")
        if positive and any(n < 0 for n in raw):
            return self.fail(path, "values must not be negative")
        return [raw[0], raw[1], raw[2]]

    def number(self, raw, path, lo, hi):
        if not _finite(raw):
            return self.fail(path, "must be a number")
        if raw < lo or raw > hi:
            return self.fail(path, f"must be between {js_num(lo)} and {js_num(hi)}")
        return raw

    def integer(self, raw, path, lo, hi):
        n = self.number(raw, path, lo, hi)
        if n is None:
            return None
        if not _is_integer(n):
            return self.fail(path, "must be a whole number")
        return n

    def color(self, raw, path):
        if not isinstance(raw, str) or not _HEX_COLOR.fullmatch(raw):
            return self.fail(path, 'must be a hex colour like "#a0b1c2"')
        return raw.lower()

    def id(self, raw, path, seen):
        max_len = LIMITS["maxIdLength"]
        if not isinstance(raw, str) or _js_length(raw) == 0 or _js_length(raw) > max_len:
            return self.fail(path, f"must be a string of 1–{max_len} characters")
        if not _ID_PATTERN.fullmatch(raw):
            return self.fail(path, "may only use letters, digits, '_', '-' and '.'")
        if raw in seen:
            return self.fail(path, f'"{raw}" is used twice; ids must be unique')
        seen.add(raw)
        return raw

    def unknown_keys(self, raw, allowed, path):
        for key in _js_key_order(raw.keys()):
            if key not in allowed:
                self.fail(f"{path}.{key}", "is not a field this scene format has")


def _optional(raw, key, read):
    """ABSENT when the key is missing, else whatever ``read`` returns (None on failure)."""
    if key not in raw:
        return ABSENT
    return read(raw[key])


def _read_object_keys(r, raw, path):
    if not isinstance(raw, list):
        return r.fail(path, "must be a list of keyframes")
    if len(raw) > LIMITS["maxKeys"]:
        return r.fail(path, f"at most {LIMITS['maxKeys']} keyframes")
    keys = []
    for i, k in enumerate(raw):
        p = f"{path}[{i}]"
        if not _is_record(k):
            r.fail(p, "must be an object")
            continue
        r.unknown_keys(k, ("frame", "position", "rotation"), p)
        frame = r.integer(k.get("frame"), f"{p}.frame", 0, LIMITS["maxFrame"])
        position = _optional(k, "position", lambda v: r.vec3(v, f"{p}.position", LIMITS["maxCoordinate"]))
        rotation = _optional(k, "rotation", lambda v: r.vec3(v, f"{p}.rotation", 3600))
        if "position" not in k and "rotation" not in k:
            r.fail(p, "a keyframe needs a position, a rotation, or both")
        if frame is None or position is None or rotation is None:
            continue
        key = {"frame": frame}
        if position is not ABSENT:
            key["position"] = position
        if rotation is not ABSENT:
            key["rotation"] = rotation
        keys.append(key)
    return keys


def _read_object(r, raw, path, ids):
    if not _is_record(raw):
        return r.fail(path, "must be an object")
    is_asset = "asset" in raw
    is_primitive = "primitive" in raw
    if is_asset == is_primitive:
        return r.fail(path, 'needs exactly one of "asset" (a catalog id) or "primitive"')
    r.unknown_keys(
        raw,
        ("id", "asset", "height", "position", "rotation", "keys")
        if is_asset
        else ("id", "primitive", "size", "color", "roughness", "metallic", "position", "rotation", "keys"),
        path,
    )
    obj_id = r.id(raw.get("id"), f"{path}.id", ids)
    position = r.vec3(raw.get("position"), f"{path}.position", LIMITS["maxCoordinate"])
    rotation = _optional(raw, "rotation", lambda v: r.vec3(v, f"{path}.rotation", 3600))
    keys = _optional(raw, "keys", lambda v: _read_object_keys(r, v, f"{path}.keys"))

    if is_asset:
        asset = raw["asset"]
        if not (isinstance(asset, str) and len(asset) > 0):
            asset = r.fail(f"{path}.asset", "must be a catalog id from search_assets")
        height = _optional(raw, "height", lambda v: r.number(v, f"{path}.height", 0.01, LIMITS["maxSize"]))
        if None in (obj_id, position, rotation, keys, asset, height):
            return None
        out = {"id": obj_id, "asset": asset, "position": position}
        if rotation is not ABSENT:
            out["rotation"] = rotation
        if height is not ABSENT:
            out["height"] = height
        if keys is not ABSENT:
            out["keys"] = keys
        return out

    primitive = raw["primitive"]
    if not (isinstance(primitive, str) and primitive in BLENDER_PRIMITIVES):
        primitive = r.fail(f"{path}.primitive", f"must be one of {', '.join(BLENDER_PRIMITIVES)}")
    size = r.vec3(raw.get("size"), f"{path}.size", LIMITS["maxSize"], True)
    color = _optional(raw, "color", lambda v: r.color(v, f"{path}.color"))
    roughness = _optional(raw, "roughness", lambda v: r.number(v, f"{path}.roughness", 0, 1))
    metallic = _optional(raw, "metallic", lambda v: r.number(v, f"{path}.metallic", 0, 1))
    if None in (obj_id, position, rotation, keys, primitive, size, color, roughness, metallic):
        return None
    out = {"id": obj_id, "primitive": primitive, "size": size, "position": position}
    if rotation is not ABSENT:
        out["rotation"] = rotation
    if color is not ABSENT:
        out["color"] = color
    if roughness is not ABSENT:
        out["roughness"] = roughness
    if metallic is not ABSENT:
        out["metallic"] = metallic
    if keys is not ABSENT:
        out["keys"] = keys
    return out


def _read_light(r, raw, path, ids):
    if not _is_record(raw):
        return r.fail(path, "must be an object")
    r.unknown_keys(raw, ("id", "type", "position", "rotation", "strength", "color", "size"), path)
    light_id = r.id(raw.get("id"), f"{path}.id", ids)
    light_type = raw.get("type")
    if not (isinstance(light_type, str) and light_type in BLENDER_LIGHT_TYPES):
        light_type = r.fail(f"{path}.type", f"must be one of {', '.join(BLENDER_LIGHT_TYPES)}")
    position = r.vec3(raw.get("position"), f"{path}.position", LIMITS["maxCoordinate"])
    rotation = _optional(raw, "rotation", lambda v: r.vec3(v, f"{path}.rotation", 3600))
    strength = r.number(raw.get("strength"), f"{path}.strength", 0, 1000000)
    color = _optional(raw, "color", lambda v: r.color(v, f"{path}.color"))
    size = _optional(raw, "size", lambda v: r.number(v, f"{path}.size", 0, 180))
    if None in (light_id, light_type, position, rotation, strength, color, size):
        return None
    out = {"id": light_id, "type": light_type, "position": position, "strength": strength}
    if rotation is not ABSENT:
        out["rotation"] = rotation
    if color is not ABSENT:
        out["color"] = color
    if size is not ABSENT:
        out["size"] = size
    return out


def _read_camera(r, raw):
    if not _is_record(raw):
        return r.fail("camera", "must be an object")
    r.unknown_keys(raw, ("lens", "keys"), "camera")
    lens = r.number(raw.get("lens"), "camera.lens", 8, 300)
    raw_keys = raw.get("keys")
    if not isinstance(raw_keys, list) or len(raw_keys) == 0:
        return r.fail("camera.keys", "must be a list of at least one keyframe")
    if len(raw_keys) > LIMITS["maxKeys"]:
        return r.fail("camera.keys", f"at most {LIMITS['maxKeys']} keyframes")
    keys = []
    frames = set()
    for i, k in enumerate(raw_keys):
        p = f"camera.keys[{i}]"
        if not _is_record(k):
            r.fail(p, "must be an object")
            continue
        r.unknown_keys(k, ("frame", "position", "target"), p)
        frame = r.integer(k.get("frame"), f"{p}.frame", 0, LIMITS["maxFrame"])
        position = r.vec3(k.get("position"), f"{p}.position", LIMITS["maxCoordinate"])
        target = r.vec3(k.get("target"), f"{p}.target", LIMITS["maxCoordinate"])
        if frame is not None and frame in frames:
            r.fail(f"{p}.frame", f"frame {js_num(frame)} is keyed twice")
            continue
        if frame is None or position is None or target is None:
            continue
        if all(position[axis] == target[axis] for axis in range(3)):
            r.fail(p, "position and target are the same point, so the camera has no direction")
            continue
        frames.add(frame)
        keys.append({"frame": frame, "position": position, "target": target})
    if lens is None:
        return None
    return {"lens": lens, "keys": sorted(keys, key=lambda key: key["frame"])}


def _read_world(r, raw):
    if not _is_record(raw):
        return r.fail("world", "must be an object")
    r.unknown_keys(raw, ("color", "strength"), "world")
    color = r.color(raw.get("color"), "world.color")
    strength = r.number(raw.get("strength"), "world.strength", 0, 100)
    if color is None or strength is None:
        return None
    return {"color": color, "strength": strength}


def _read_render(r, raw):
    if not _is_record(raw):
        return r.fail("render", "must be an object")
    r.unknown_keys(raw, ("fps", "frames", "resolution"), "render")
    fps = r.integer(raw.get("fps"), "render.fps", 1, LIMITS["maxFps"])

    frames = None
    raw_frames = raw.get("frames")
    if not isinstance(raw_frames, list) or len(raw_frames) != 2:
        r.fail("render.frames", "must be [first, last]")
    else:
        first = r.integer(raw_frames[0], "render.frames[0]", 0, LIMITS["maxFrame"])
        last = r.integer(raw_frames[1], "render.frames[1]", 0, LIMITS["maxFrame"])
        if first is not None and last is not None:
            if last >= first:
                frames = [first, last]
            else:
                r.fail("render.frames", "last frame must not come before the first")

    resolution = None
    raw_res = raw.get("resolution")
    if not isinstance(raw_res, list) or len(raw_res) != 2:
        r.fail("render.resolution", "must be [width, height]")
    else:
        w = r.integer(raw_res[0], "render.resolution[0]", LIMITS["minResolution"], LIMITS["maxResolution"])
        h = r.integer(raw_res[1], "render.resolution[1]", LIMITS["minResolution"], LIMITS["maxResolution"])
        if w is not None and h is not None:
            # H.264 needs even dimensions; refusing here beats a failed encode later.
            if w % 2 == 0 and h % 2 == 0:
                resolution = [w, h]
            else:
                r.fail("render.resolution", "width and height must be even numbers")

    if fps is None or frames is None or resolution is None:
        return None
    return {"fps": fps, "frames": frames, "resolution": resolution}


def parse_scene_spec(raw):
    """Validate a scene description; every problem is reported, not just the first.

    Returns ``{"ok": True, "value": spec}`` or ``{"ok": False, "errors": [...]}``,
    the same shape as the TypeScript ``ParseResult``.
    """
    r = _Reader()
    if not _is_record(raw):
        return {"ok": False, "errors": ["scene: must be an object"]}
    r.unknown_keys(raw, ("objects", "lights", "camera", "world", "render"), "scene")

    ids = set()
    objects = []
    raw_objects = raw.get("objects")
    if not isinstance(raw_objects, list):
        r.fail("objects", "must be a list (it may be empty)")
    elif len(raw_objects) > LIMITS["maxObjects"]:
        r.fail("objects", f"at most {LIMITS['maxObjects']} objects")
    else:
        for i, o in enumerate(raw_objects):
            obj = _read_object(r, o, f"objects[{i}]", ids)
            if obj:
                objects.append(obj)

    lights = []
    if "lights" in raw:
        raw_lights = raw["lights"]
        if not isinstance(raw_lights, list):
            r.fail("lights", "must be a list")
        elif len(raw_lights) > LIMITS["maxLights"]:
            r.fail("lights", f"at most {LIMITS['maxLights']} lights")
        else:
            for i, l in enumerate(raw_lights):
                light = _read_light(r, l, f"lights[{i}]", ids)
                if light:
                    lights.append(light)

    camera = _read_camera(r, raw["camera"]) if "camera" in raw else None
    world = _read_world(r, raw["world"]) if "world" in raw else None
    render = _read_render(r, raw["render"]) if "render" in raw else None

    if r.errors:
        return {"ok": False, "errors": r.errors}
    value = {"objects": objects}
    if "lights" in raw:
        value["lights"] = lights
    if camera:
        value["camera"] = camera
    if world:
        value["world"] = world
    if render:
        value["render"] = render
    return {"ok": True, "value": value}


def scene_asset_ids(spec):
    """The catalog ids a spec names, deduplicated, in first-use order."""
    ids = []
    for obj in spec["objects"]:
        if "asset" in obj and obj["asset"] not in ids:
            ids.append(obj["asset"])
    return ids
