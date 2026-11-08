"""Shader graphs for Framo to compile into its own shaders.

Every send carries the graphs of the materials in it, inside the GLB at
``extras.framoBlenderShaders`` (``embed``), so the model opens in Framo with its
shaders and nobody has to ask for them (framo-app's components/rt/doc/blenderShaders.ts).

Holds only what Framo's compiler reads (components/rt/procedural/compile.ts in
framo-app): socket ids, every enum, and ramps and curves sampled to 256-entry
tables, which is how Cycles evaluates them. Node groups come along once each,
by name. The graphs go out as JSON, so a value JSON cannot hold is null: an
infinity would make the whole file unreadable, and a datablock socket holds
nothing the compiler can use.

bpy and the standard library only.
"""
import json
import math
import struct

import bpy

SKIP = {
    "rna_type", "name", "label", "parent", "location", "location_absolute", "width", "height", "dimensions",
    "inputs", "outputs", "internal_links", "select", "show_options", "show_preview", "show_texture", "hide",
    "color", "use_custom_color", "bl_idname", "bl_label", "bl_description", "bl_icon", "bl_static_type",
    "bl_width_default", "bl_width_min", "bl_width_max", "bl_height_default", "bl_height_min", "bl_height_max",
    "type", "warning_propagation", "color_tag", "debug_zone_body_lazy_function_graph", "debug_zone_lazy_function_graph",
}


def plain(v):
    if isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if hasattr(v, "__len__"):
        return [plain(x) for x in v]
    return None


def value(socket):
    try:
        return plain(socket.default_value)
    except AttributeError:
        return None


def props(node):
    found = {}
    for p in node.bl_rna.properties:
        if p.identifier in SKIP or p.type in ("POINTER", "COLLECTION"):
            continue
        v = getattr(node, p.identifier, None)
        if isinstance(v, (bool, int, float, str)):
            found[p.identifier] = plain(v)
    return found


def curve_table(mapping):
    mapping.initialize()
    lo, hi = mapping.clip_min_x, mapping.clip_max_x
    return {"min": lo, "max": hi, "v": [mapping.evaluate(mapping.curves[0], lo + (hi - lo) * i / 255) for i in range(256)]}


def node_json(n):
    d = {"name": n.name, "type": n.bl_idname, "mute": n.mute, "p": props(n)}
    if n.bl_idname == "ShaderNodeGroup" and n.node_tree:
        d["group"] = n.node_tree.name
    if n.bl_idname == "ShaderNodeValToRGB":
        r = n.color_ramp
        d["ramp"] = {"interp": r.interpolation, "mode": r.color_mode, "lut": [list(r.evaluate(i / 255)) for i in range(256)]}
    if n.bl_idname == "ShaderNodeFloatCurve":
        d["curve"] = {"lut": curve_table(n.mapping)}
    if n.bl_idname == "ShaderNodeTexImage" and n.image:
        d["image"] = n.image.name
    d["ins"] = {s.identifier: {"n": s.name, "v": value(s)} for s in n.inputs}
    d["outs"] = {s.identifier: s.name for s in n.outputs}
    if n.bl_idname in ("ShaderNodeValue", "ShaderNodeRGB"):
        d["outv"] = value(n.outputs[0])
    return d


def tree_json(t):
    return {
        "nodes": [node_json(n) for n in t.nodes],
        "links": [[l.from_node.name, l.from_socket.identifier, l.to_node.name, l.to_socket.identifier] for l in t.links if not l.is_muted],
    }


EXTRA = "framoBlenderShaders"


def graphs_for(names):
    """The graphs of these materials and of every group they reach, once each. Main thread only."""
    graphs = {"m": {}, "g": {}}

    def groups(tree):
        for n in tree.nodes:
            if n.bl_idname == "ShaderNodeGroup" and n.node_tree and n.node_tree.name not in graphs["g"]:
                graphs["g"][n.node_tree.name] = tree_json(n.node_tree)
                groups(n.node_tree)

    for name in names:
        material = bpy.data.materials.get(name)
        if material is not None and material.node_tree is not None:
            graphs["m"][name] = tree_json(material.node_tree)
            groups(material.node_tree)
    return graphs


def _glb_parts(glb):
    """A GLB's JSON and the bytes after its JSON chunk."""
    magic, version, _length = struct.unpack_from("<4sII", glb, 0)
    if magic != b"glTF" or version != 2:
        raise ValueError("not a glTF 2 binary")
    json_length, json_type = struct.unpack_from("<I4s", glb, 12)
    if json_type != b"JSON":
        raise ValueError("a GLB's first chunk is not JSON")
    return json.loads(glb[20:20 + json_length].decode("utf8")), glb[20 + json_length:]


def glb_material_names(glb):
    """The names of the materials a GLB holds. Touches no bpy."""
    doc, _rest = _glb_parts(glb)
    return [m["name"] for m in doc.get("materials", []) if "name" in m]


def embed(glb, graphs):
    """The GLB with ``graphs`` in its root extras. Touches no bpy, so a thread may call it."""
    if not graphs["m"]:
        return glb
    doc, rest = _glb_parts(glb)
    doc.setdefault("extras", {})[EXTRA] = graphs
    text = json.dumps(doc, separators=(",", ":"), allow_nan=False).encode("utf8")
    text += b" " * (-len(text) % 4)
    return struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(text) + len(rest)) + struct.pack("<I4s", len(text), b"JSON") + text + rest

