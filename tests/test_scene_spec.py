"""control/scene_spec.py answers exactly what the Framo app's TypeScript parser answers.

The expected outputs in fixtures/scene_spec_cases.json were produced by running
``parseBlenderSceneSpec`` from the Framo app's ``shared/blender-scene.ts`` on
the inputs in fixtures/scene_spec_inputs.json. When the TypeScript changes,
regenerate rather than edit:

    node tests/fixtures/generate_scene_spec_cases.mjs <framo-app>/shared/blender-scene.ts

Run with plain Python, no Blender:

    python3 -m unittest discover -s tests -p 'test_*.py'
"""

import json
import math
import unittest
from pathlib import Path

from _load import load_module

scene_spec = load_module("control/scene_spec.py")

FIXTURES = Path(__file__).parent / "fixtures"


def _normalize(value):
    """JSON-equal: 2.0 and 2 are the same number once serialized, as in JavaScript."""
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in value.items()}
    return value


class SceneSpecMatchesTypeScript(unittest.TestCase):
    def test_every_fixture_case(self):
        inputs = json.loads((FIXTURES / "scene_spec_inputs.json").read_text(encoding="utf-8"))
        cases = json.loads((FIXTURES / "scene_spec_cases.json").read_text(encoding="utf-8"))
        self.assertEqual([c["name"] for c in inputs], [c["name"] for c in cases],
                         "inputs and expected outputs are out of step; regenerate the cases")
        self.assertGreater(len(cases), 40)
        for raw, case in zip(inputs, cases):
            with self.subTest(case["name"]):
                got = _normalize(scene_spec.parse_scene_spec(raw["input"]))
                self.assertEqual(got, case["expected"])

    def test_cases_cover_both_verdicts(self):
        cases = json.loads((FIXTURES / "scene_spec_cases.json").read_text(encoding="utf-8"))
        verdicts = {c["expected"]["ok"] for c in cases}
        self.assertEqual(verdicts, {True, False})


class SceneSpecPythonOnlyEdges(unittest.TestCase):
    """Inputs JSON cannot carry, or that only Python's types can produce."""

    def base(self, **obj):
        o = {"id": "a", "primitive": "box", "size": [1, 1, 1], "position": [0, 0, 0]}
        o.update(obj)
        return {"objects": [o]}

    def test_nan_and_infinity_are_not_numbers(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            result = scene_spec.parse_scene_spec(self.base(position=[0, 0, bad]))
            self.assertFalse(result["ok"])
            self.assertEqual(result["errors"], ["objects[0].position: must be three numbers [x, y, z]"])

    def test_bool_is_not_a_number(self):
        result = scene_spec.parse_scene_spec(self.base(roughness=True))
        self.assertEqual(result["errors"], ["objects[0].roughness: must be a number"])

    def test_tuple_is_not_a_list(self):
        result = scene_spec.parse_scene_spec(self.base(size=(1, 1, 1)))
        self.assertEqual(result["errors"], ["objects[0].size: must be three numbers [x, y, z]"])

    def test_asset_ids(self):
        spec = scene_spec.parse_scene_spec({"objects": [
            {"id": "a", "asset": "x", "position": [0, 0, 0]},
            {"id": "b", "asset": "y", "position": [0, 0, 0]},
            {"id": "c", "asset": "x", "position": [0, 0, 0]},
            {"id": "d", "primitive": "box", "size": [1, 1, 1], "position": [0, 0, 0]},
        ]})["value"]
        self.assertEqual(scene_spec.scene_asset_ids(spec), ["x", "y"])


if __name__ == "__main__":
    unittest.main()
