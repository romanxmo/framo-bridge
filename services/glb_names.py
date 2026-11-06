"""Rename nodes inside a GLB's JSON chunk.

A live send made in Edit Mode exports stand-in copies of the objects being
edited (services/export_service.py), and Blender gives each copy a name of its
own. The file that reaches Framo must still say "Cube", not "Cube.001".

Pure: no bpy, so it loads in a plain-Python test.
"""

import json
import struct

_HEADER_BYTES = 12
_CHUNK_HEADER_BYTES = 8


def rename_nodes(glb, names):
    """``glb`` with each node called a key of ``names`` called its value instead."""
    json_length = struct.unpack_from("<I", glb, _HEADER_BYTES)[0]
    json_start = _HEADER_BYTES + _CHUNK_HEADER_BYTES
    json_end = json_start + json_length
    document = json.loads(glb[json_start:json_end])

    changed = False
    for node in document.get("nodes", []):
        if node.get("name") in names:
            node["name"] = names[node["name"]]
            changed = True
    if not changed:
        return glb

    chunk = json.dumps(document, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    # glTF pads the JSON chunk to four bytes, with spaces.
    chunk += b" " * (-len(chunk) % 4)
    rest = glb[json_end:]
    total = json_start + len(chunk) + len(rest)
    return b"".join((
        glb[:8],
        struct.pack("<I", total),
        struct.pack("<I", len(chunk)),
        glb[_HEADER_BYTES + 4:json_start],
        chunk,
        rest,
    ))
