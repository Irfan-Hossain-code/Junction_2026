import { staticFile } from "remotion";
import manifest from "./data/assets.json";

const has = (dir: "clips" | "audio" | "screens", f?: string | null) => !!f && (manifest as Record<string, string[]>)[dir].includes(f);

export const asset = {
  clip: (f?: string | null) => (has("clips", f) ? staticFile(`clips/${f}`) : null),
  audio: (f?: string | null) => (has("audio", f) ? staticFile(`audio/${f}`) : null),
  screen: (f?: string | null) => (has("screens", f) ? staticFile(`screens/${f}`) : null),
};
