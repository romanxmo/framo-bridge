"""The export half of a live send. Run by a background Blender, never imported by the add-on.

services/live_worker.py starts this ahead of time, so the slow part of Blender's
own start is over before a send needs it. It takes one job from stdin, exports
the snapshot the job names, and exits: every send starts from an empty file, so
nothing left by the last one can rename or change this one.
"""

import json
import sys
import traceback

import addon_utils
import bpy

DONE = "FRAMO_WORKER_DONE"
FAILED = "FRAMO_WORKER_FAILED"


def shrink_textures(max_size, excluded_materials):
    """Scale what is over ``max_size``. These images are this process's own copies, so nothing needs restoring."""
    kept = set()
    for name in excluded_materials:
        material = bpy.data.materials.get(name)
        if material is not None and material.node_tree is not None:
            kept.update(node.image for node in material.node_tree.nodes if node.type == 'TEX_IMAGE' and node.image)
    for image in bpy.data.images:
        width, height = image.size
        largest = max(width, height)
        if image in kept or largest <= max_size:
            continue
        ratio = max_size / largest
        image.scale(max(1, round(width * ratio)), max(1, round(height * ratio)))


def export(params):
    """Run the exporter, falling back through the image formats a texture may refuse."""
    for image_format in ('WEBP', 'AUTO', 'NONE'):
        try:
            bpy.ops.export_scene.gltf(export_image_format=image_format, **params)
            return
        except Exception:
            if image_format == 'NONE':
                raise
            traceback.print_exc()


def run(job):
    with bpy.data.libraries.load(job["snapshot"], link=False) as (_source, loaded):
        loaded.collections = [job["collection"]]
    collection = loaded.collections[0]
    bpy.context.scene.collection.children.link(collection)

    # What is hidden in the viewport lives in the person's view layer, which a snapshot does not carry.
    visible = set(job["objects"])
    for obj in list(collection.all_objects):
        if obj.name not in visible:
            bpy.data.objects.remove(obj)

    bpy.context.scene.frame_set(job["frame"])
    if job["max_texture"]:
        shrink_textures(job["max_texture"], job["excluded_materials"])
    export(job["params"])


def main():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    addon_utils.enable("io_scene_gltf2")
    line = sys.stdin.readline()
    if not line:
        return
    try:
        run(json.loads(line))
        print(DONE, flush=True)
    except Exception as e:
        traceback.print_exc()
        print(f"{FAILED} {e}".replace("\n", " "), flush=True)


main()
