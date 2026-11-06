"""The "To Framo" collection: what goes to Framo is what is in it.

One rule for every send, live or by the button, so a person decides what Framo
sees by what they put in the Outliner, and nothing else in the file leaves
Blender. Nested collections count. An object hidden in the viewport does not:
Framo shows what Blender shows.
"""

import bpy

NAME = "To Framo"


def find():
    """The collection, or None when this file has none."""
    return bpy.data.collections.get(NAME)


def ensure(scene):
    """The collection, created if needed and linked into ``scene``."""
    collection = find()
    if collection is None:
        collection = bpy.data.collections.new(NAME)
    if not in_scene(scene):
        scene.collection.children.link(collection)
    return collection


def in_scene(scene):
    collection = find()
    return collection is not None and collection in scene.collection.children_recursive


def visible_objects(view_layer):
    """The objects a send carries, in a stable order."""
    collection = find()
    if collection is None:
        return []
    objects = [obj for obj in collection.all_objects if obj.visible_get(view_layer=view_layer)]
    objects.sort(key=lambda obj: obj.name_full)
    return objects


def watched_names(objects):
    """What can change a send's content: the names of ``objects``, what they instance, and their materials."""
    object_names = set()
    material_names = set()
    stack = list(objects)
    while stack:
        obj = stack.pop()
        if obj.name_full in object_names:
            continue
        object_names.add(obj.name_full)
        for slot in obj.material_slots:
            if slot.material is not None:
                material_names.add(slot.material.name_full)
        if obj.instance_type == 'COLLECTION' and obj.instance_collection is not None:
            stack.extend(obj.instance_collection.all_objects)
    return object_names, material_names
