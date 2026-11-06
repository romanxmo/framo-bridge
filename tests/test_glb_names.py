"""Renaming a GLB's nodes: services/glb_names.py.

Run with plain Python, no Blender:

    python3 -m unittest discover -s tests -p 'test_*.py'
"""

import json
import struct
import unittest

from _load import load_module

names = load_module("services/glb_names.py")


def glb(document, binary=b""):
    chunk = json.dumps(document).encode()
    chunk += b" " * (-len(chunk) % 4)
    body = struct.pack("<II", len(chunk), 0x4E4F534A) + chunk
    if binary:
        body += struct.pack("<II", len(binary), 0x004E4942) + binary
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body


def parse(data):
    magic, version, total = struct.unpack_from("<4sII", data)
    length, kind = struct.unpack_from("<II", data, 12)
    return magic, version, total, kind, json.loads(data[20:20 + length]), data[20 + length:]


class RenameNodes(unittest.TestCase):
    def test_renames_what_it_is_told_and_nothing_else(self):
        source = glb({"nodes": [{"name": "Cube.001"}, {"name": "Lamp"}]}, b"\x01\x02\x03\x04")
        out = names.rename_nodes(source, {"Cube.001": "Cube"})
        magic, version, total, kind, document, rest = parse(out)
        self.assertEqual((magic, version, kind), (b"glTF", 2, 0x4E4F534A))
        self.assertEqual(total, len(out))
        self.assertEqual([n["name"] for n in document["nodes"]], ["Cube", "Lamp"])
        self.assertEqual(rest[8:], b"\x01\x02\x03\x04")

    def test_the_json_chunk_stays_four_byte_aligned(self):
        for name in ("A", "AB", "ABC", "ABCD", "Würfel"):
            with self.subTest(name):
                out = names.rename_nodes(glb({"nodes": [{"name": "x"}]}, b"\0\0\0\0"), {"x": name})
                self.assertEqual(struct.unpack_from("<I", out, 12)[0] % 4, 0)
                self.assertEqual(parse(out)[4]["nodes"][0]["name"], name)

    def test_no_match_is_the_same_bytes(self):
        source = glb({"nodes": [{"name": "Lamp"}]})
        self.assertIs(names.rename_nodes(source, {"Cube.001": "Cube"}), source)

    def test_a_file_with_no_nodes(self):
        source = glb({"asset": {"version": "2.0"}})
        self.assertIs(names.rename_nodes(source, {"Cube.001": "Cube"}), source)


if __name__ == "__main__":
    unittest.main()
