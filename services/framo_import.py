"""Bring a model from Framo into Blender: the way back.

What comes back is usually the person's own model with a new look, so that is
where it goes: an object that came from To Framo and returns with the same
surface keeps its mesh and modifiers and takes the material. Landing a copy would
put a baked twin exactly on top of the original, fighting it for every pixel.

"Same surface" is measured, never assumed from the name: same size, same UV
layout. A texture is only right on the UVs it was painted on, so anything else
(the mesh changed in Framo, or an object Blender has never seen) stays a separate
object in a "From Framo" collection, where nothing of the person's is touched. That
collection is not To Framo, so what lands there is never sent out again, and each
send replaces the one before.

Runs on the main thread, from the queue in control/commands.py: importing has to
happen where the data is, and it was asked for, so it may take a moment.
"""

import re

import bpy
import numpy as np

from . import framo_collection
from ..control.scene_builder import BuildError

NAME = "From Framo"

# A material that came from Framo: replacing it needs no keeping, unlike the person's own.
_FROM_FRAMO = "framo_painted"
# How much of two UV layouts must coincide. Triangulating adds no corners, but rounding can split a few.
_SAME_UVS = 0.95
_SAME_SIZE = 0.01


def _clear(collection):
    """Remove what the last send left here, and the data nothing else uses."""
    objects = list(collection.all_objects)
    _remove(objects)


def _remove(objects):
    meshes = {obj.data for obj in objects if obj.type == 'MESH'}
    materials = {slot.material for obj in objects for slot in obj.material_slots if slot.material is not None}
    images = {
        node.image
        for material in materials if material.node_tree is not None
        for node in material.node_tree.nodes if node.type == 'TEX_IMAGE' and node.image is not None
    }
    bpy.data.batch_remove(objects)
    for group in (meshes, materials, images):
        unused = [data for data in group if data.users == 0]
        if unused:
            bpy.data.batch_remove(unused)


def _surface(obj, depsgraph):
    """What a paint is laid on: the UV corners, and the size of the evaluated mesh."""
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        points = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
        mesh.vertices.foreach_get("co", points)
        points = points.reshape(-1, 3)
        size = points.max(axis=0) - points.min(axis=0) if len(points) else np.zeros(3)
        layer = mesh.uv_layers.active
        if layer is None:
            return size, set()
        uvs = np.empty(len(mesh.loops) * 2, dtype=np.float32)
        layer.uv.foreach_get("vector", uvs)
        rounded = np.round(uvs.reshape(-1, 2), 3)
        return size, {tuple(row) for row in np.unique(rounded, axis=0)}
    finally:
        evaluated.to_mesh_clear()


def _same_surface(a, b, depsgraph):
    size_a, uvs_a = _surface(a, depsgraph)
    size_b, uvs_b = _surface(b, depsgraph)
    largest = max(float(size_a.max()), float(size_b.max()), 1e-6)
    if float(np.abs(size_a - size_b).max()) > largest * _SAME_SIZE:
        return False
    if not uvs_a and not uvs_b:
        return True
    return len(uvs_a & uvs_b) / max(len(uvs_a | uvs_b), 1) >= _SAME_UVS


def _look(material):
    """What the importer can say about a material: (has images, colour, metal, roughness), or None."""
    if material is None or material.node_tree is None:
        return None
    nodes = material.node_tree.nodes
    bsdf = next((n for n in nodes if n.type == 'BSDF_PRINCIPLED'), None)
    if bsdf is None:
        return None
    return (
        any(n.type == 'TEX_IMAGE' and n.image is not None for n in nodes),
        tuple(round(c, 2) for c in bsdf.inputs["Base Color"].default_value[:3]),
        round(bsdf.inputs["Metallic"].default_value, 2),
        round(bsdf.inputs["Roughness"].default_value, 2),
    )


def _changed(incoming, current):
    """Whether `incoming` is news. A material the importer cannot read is left alone unless Framo textured it."""
    look = _look(incoming)
    if look is None:
        return False
    if look[0]:
        return True
    known = _look(current)
    return known is not None and known != look


def _wear(original, imported):
    """Put on `original` the materials that are new on `imported`. True when one was."""
    changed = False
    for index, slot in enumerate(imported.material_slots):
        incoming = slot.material
        if incoming is None:
            continue
        if index >= len(original.material_slots):
            original.data.materials.append(incoming)
            incoming[_FROM_FRAMO] = True
            changed = True
            continue
        current = original.material_slots[index].material
        if not _changed(incoming, current):
            continue
        # Their own material outlives this: a save would otherwise drop it for having no users.
        if current is not None and not current.get(_FROM_FRAMO):
            current.use_fake_user = True
        original.material_slots[index].material = incoming
        incoming[_FROM_FRAMO] = True
        changed = True
    return changed


def _original_of(imported, ours):
    """The object this one came from: Blender renames an import that lands on a taken name."""
    original = bpy.data.objects.get(re.sub(r"\.\d{3}$", "", imported.name))
    if original is None or original is imported or original not in ours or original.type != imported.type:
        return None
    return original


def _settle(imported, depsgraph):
    """Decide each imported object: absorbed by its original, or kept. Returns (names worn, objects absorbed)."""
    to_framo = framo_collection.find()
    ours = set(to_framo.all_objects) if to_framo is not None else set()
    worn = []
    absorbed = []
    for obj in imported:
        original = _original_of(obj, ours)
        if original is None:
            continue
        if obj.type != 'MESH':
            absorbed.append(obj)
        elif _same_surface(obj, original, depsgraph):
            if _wear(original, obj):
                worn.append(original.name)
            absorbed.append(obj)
    return worn, absorbed


def _without_empty_parents(objects):
    """`objects` minus the groups left holding nothing, which are removed."""
    kept = list(objects)
    while True:
        bare = [obj for obj in kept if obj.type == 'EMPTY' and not obj.children and obj.instance_collection is None]
        if not bare:
            return kept
        kept = [obj for obj in kept if obj not in bare]
        bpy.data.batch_remove(bare)


def import_model(path):
    context = bpy.context
    if context.mode != 'OBJECT':
        raise BuildError("Leave Edit Mode in Blender, then send again.")
    view_layer = context.view_layer
    selected = [obj.name for obj in context.selected_objects]
    active = view_layer.objects.active.name if view_layer.objects.active else None

    collection = bpy.data.collections.get(NAME)
    if collection is None:
        collection = bpy.data.collections.new(NAME)
    if collection not in context.scene.collection.children_recursive:
        context.scene.collection.children.link(collection)
    _clear(collection)

    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path, import_pack_images=True)
    imported = [obj for obj in bpy.data.objects if obj not in before]
    for obj in imported:
        for owner in list(obj.users_collection):
            owner.objects.unlink(obj)
        collection.objects.link(obj)
    view_layer.update()

    worn, absorbed = _settle(imported, context.evaluated_depsgraph_get())
    kept = [obj for obj in imported if obj not in absorbed]
    _remove(absorbed)
    kept = _without_empty_parents(kept)
    if not kept:
        bpy.data.collections.remove(collection)

    # The importer selects what it brings; the person's selection is theirs.
    for obj in kept:
        obj.select_set(False)
    for name in selected:
        obj = bpy.data.objects.get(name)
        if obj is not None and obj.name in view_layer.objects:
            obj.select_set(True)
    view_layer.objects.active = bpy.data.objects.get(active) if active else None

    bpy.ops.ed.undo_push(message="From Framo")
    return {"worn": worn, "kept": len(kept)}
