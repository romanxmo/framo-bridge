"""The open file's shader graphs, for Framo's New material dialog to compile.

``GET /shader-graphs`` answers with this. Without the add-on's server, the same
dump can be written to a file, which the dialog also opens:

    blender -b scene.blend --python services/shader_graphs.py -- out.json

Holds only what Framo's compiler reads (components/rt/procedural/compile.ts in
framo-app): socket ids, every enum, and ramps and curves sampled to 256-entry
tables, which is how Cycles evaluates them. Node groups come along once each,
by name. The answer goes out as JSON, so a value JSON cannot hold is null: an
infinity would make the whole answer unreadable, and a datablock socket holds
nothing the compiler can use.

bpy and the standard library only, so this file also runs on its own.
"""
import json
import math
import sys

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


def shader_graphs():
    return {
        "m": {m.name: tree_json(m.node_tree) for m in bpy.data.materials if m.node_tree},
        "g": {g.name: tree_json(g) for g in bpy.data.node_groups if g.bl_idname == "ShaderNodeTree"},
    }


if __name__ == "__main__":
    out = sys.argv[sys.argv.index("--") + 1]
    dump = shader_graphs()
    with open(out, "w", encoding="utf8") as f:
        json.dump(dump, f)
    print(f"wrote {len(dump['m'])} materials, {len(dump['g'])} groups to {out}")
