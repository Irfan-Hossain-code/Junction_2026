// Lists which optional media exist in public/ so scenes can show a labelled placeholder instead of a broken file.
// Runs automatically before `npm run studio` / `npm run render` (pre-scripts). Writes src/data/assets.json.
import { readdirSync, writeFileSync, existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const out = {};
for (const dir of ["clips", "audio", "screens"]) {
  const p = join(root, "public", dir);
  out[dir] = existsSync(p) ? readdirSync(p).filter((f) => !f.startsWith(".") && !f.endsWith(".md")).sort() : [];
}
writeFileSync(join(root, "src", "data", "assets.json"), JSON.stringify(out, null, 2) + "\n");
console.log(`assets: ${out.clips.length} clips, ${out.audio.length} audio, ${out.screens.length} screens`);
