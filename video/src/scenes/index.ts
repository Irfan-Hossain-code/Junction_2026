import type React from "react";
import { S1Intro, S4Donny, S8Ending } from "./ClipScenes";
import { S2Beginning } from "./S2Beginning";
import { S3Disruption } from "./S3Disruption";
import { S5Recovery } from "./S5Recovery";
import { S6Diagnosis } from "./S6Diagnosis";
import { S7Twin } from "./S7Twin";

/** Scene id (config.ts SCENES) → component. Add a scene: write it, register it here, list it in SCENES. */
export const SCENE_COMPONENTS: Record<string, React.FC> = {
  "S1-Intro": S1Intro,
  "S2-Beginning": S2Beginning,
  "S3-Disruption": S3Disruption,
  "S4-Donny": S4Donny,
  "S5-Recovery": S5Recovery,
  "S6-Diagnosis": S6Diagnosis,
  "S7-Twin": S7Twin,
  "S8-Ending": S8Ending,
};
