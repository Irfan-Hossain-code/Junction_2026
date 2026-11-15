import React from "react";
import { AbsoluteFill, Series } from "remotion";
import { CROSSFADE, SCENES } from "./config";
import { SCENE_COMPONENTS } from "./scenes";
import { FPS } from "./theme";

/** The whole video: SCENES in order, overlapping by CROSSFADE frames (each scene fades its own edges). */
export const fullDuration = () => SCENES.reduce((a, sc, i) => a + Math.round(sc.durationSec * FPS) - (i ? CROSSFADE : 0), 0);

export const Full: React.FC = () => (
  <AbsoluteFill style={{ background: "#070d18" }}>
    <Series>
      {SCENES.map((sc, i) => {
        const C = SCENE_COMPONENTS[sc.id];
        return (
          <Series.Sequence key={sc.id} durationInFrames={Math.round(sc.durationSec * FPS)} offset={i ? -CROSSFADE : 0} name={sc.id}>
            <C />
          </Series.Sequence>
        );
      })}
    </Series>
  </AbsoluteFill>
);
