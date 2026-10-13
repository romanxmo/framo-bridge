// Regenerates scene_spec_cases.json from the TypeScript parser in the Framo app.
//
//   node tests/fixtures/generate_scene_spec_cases.mjs <path to framo-app>/shared/blender-scene.ts
//
// Needs Node 23.6+ (runs the .ts file with built-in type stripping). Reads the
// inputs from scene_spec_inputs.json, runs parseBlenderSceneSpec on each, and
// writes each case's name and expected output to scene_spec_cases.json.
// The inputs stay in their own file: JSON.stringify would turn the Infinity
// case below into null, so the cases file never carries an input. The Python
// mirror (control/scene_spec.py) is held to that file by tests/test_scene_spec.py.
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const tsPath = process.argv[2];
if (!tsPath) {
  console.error("usage: node generate_scene_spec_cases.mjs <framo-app>/shared/blender-scene.ts");
  process.exit(2);
}
const here = dirname(fileURLToPath(import.meta.url));
const { parseBlenderSceneSpec } = await import(pathToFileURL(resolve(tsPath)).href);

// The inputs file is written by Python's json, which spells an integer too big
// for a double as digits; JSON.parse reads that as Infinity, which is exactly
// the case the Python side has to treat the same way.
const inputs = JSON.parse(readFileSync(join(here, "scene_spec_inputs.json"), "utf8"));
const cases = inputs.map(({ name, input }) => ({ name, expected: parseBlenderSceneSpec(input) }));
writeFileSync(join(here, "scene_spec_cases.json"), JSON.stringify(cases, null, 1) + "\n");
console.log(`wrote ${cases.length} cases`);
