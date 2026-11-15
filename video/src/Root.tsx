import React from "react";
import { Composition, Folder } from "remotion";
import { SCENES } from "./config";
import { Full, fullDuration } from "./Full";
import { CharacterSheet } from "./scenes/CharacterSheet";
import { SCENE_COMPONENTS } from "./scenes";
import { FPS, H, W } from "./theme";

export const RemotionRoot: React.FC = () => (
  <>
    <Composition id="Full" component={Full} durationInFrames={fullDuration()} fps={FPS} width={W} height={H} />
    <Folder name="Scenes">
      {SCENES.map((sc) => (
        <Composition key={sc.id} id={sc.id} component={SCENE_COMPONENTS[sc.id]} durationInFrames={Math.round(sc.durationSec * FPS)} fps={FPS} width={W} height={H} />
      ))}
    </Folder>
    <Folder name="Design">
      <Composition id="CharacterSheet" component={CharacterSheet} durationInFrames={90} fps={FPS} width={W} height={H} />
    </Folder>
  </>
);
