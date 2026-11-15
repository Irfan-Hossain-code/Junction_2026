import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
import { blinkAt, LOOKS, Person, POSES, walkPose } from "../components/Person";
import { FONT, patient } from "../theme";

/** Design reference: all characters and poses side by side (not part of the video). */
export const CharacterSheet: React.FC = () => {
  const f = useCurrentFrame();
  const items: [string, React.ReactNode][] = [
    ["Emma · stand", <Person look={LOOKS.emma} pose={{ ...POSES.stand, blink: blinkAt(f) }} mood="smile" height={380} />],
    ["Emma · phone", <Person look={LOOKS.emma} pose={{ ...POSES.phone, blink: blinkAt(f, 30) }} prop="phone" phoneGlow={1} height={380} />],
    ["Emma · tap", <Person look={LOOKS.emma} pose={POSES.phoneTap} prop="phone" phoneGlow={1} mood="happy" height={380} />],
    ["Emma · walk", <Person look={LOOKS.emma} pose={walkPose(f / 30)} mood="neutral" height={380} />],
    ["Emma · worried", <Person look={LOOKS.emma} pose={POSES.phone} prop="phone" mood="worried" height={380} />],
    ["GP", <Person look={LOOKS.gp} pose={{ ...POSES.explain, look: -0.6 }} mood="talk" talk={(Math.sin(f / 3) + 1) / 2} height={380} />],
    ["Specialist", <Person look={LOOKS.specialist} pose={POSES.clipboard} prop="clipboard" mood="smile" height={380} />],
    ["Nurse", <Person look={LOOKS.nurse} pose={POSES.wave} mood="smile" height={380} />],
  ];
  return (
    <AbsoluteFill style={{ background: patient.bg, fontFamily: FONT, display: "grid", gridTemplateColumns: "repeat(4, 1fr)", padding: 40 }}>
      {items.map(([label, el]) => (
        <div key={label} style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "flex-end" }}>
          {el}
          <div style={{ fontSize: 22, color: patient.text2, marginTop: 6 }}>{label}</div>
        </div>
      ))}
    </AbsoluteFill>
  );
};
