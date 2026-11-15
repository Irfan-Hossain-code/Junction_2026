/** Scenes built around Irfan's recordings: intro (S1), Donny's call (S4), ending (S8). The approved monologues are
 *  NOT rewritten here; these components only add the logo, lower-thirds and the end card on top of the clips. */
import React from "react";
import { AbsoluteFill, useCurrentFrame, useVideoConfig } from "remotion";
import { ramp, s, windowed } from "../anim";
import { ClipSlot, SceneFrame } from "../components/Frame";
import { Logo } from "../components/Logo";
import { Tag } from "../components/ui";
import { END_CARD_SEC, LOGO_AT, SCENES } from "../config";
import { FONT, MONO, system } from "../theme";

const cfg = (id: string) => SCENES.find((x) => x.id === id)!;

export const LowerThird: React.FC<{ at: number; out: number; name: string; role: string; extra?: React.ReactNode }> = ({ at, out, name, role, extra }) => {
  const f = useCurrentFrame();
  const o = windowed(f, at, out, 0.5);
  return (
    <div style={{ position: "absolute", left: 80, bottom: 150, fontFamily: FONT, opacity: o, transform: `translateX(${(1 - o) * -30}px)` }}>
      <div style={{ display: "flex", alignItems: "stretch", gap: 0, background: "rgba(7,13,24,.82)", borderRadius: 14, overflow: "hidden", border: `1px solid ${system.border2}` }}>
        <div style={{ width: 8, background: system.accent }} />
        <div style={{ padding: "16px 26px" }}>
          <div style={{ fontSize: 38, fontWeight: 800, color: "#f8fafc" }}>{name}</div>
          <div style={{ fontSize: 24, color: system.text2, marginTop: 2 }}>{role}</div>
          {extra && <div style={{ marginTop: 10 }}>{extra}</div>}
        </div>
      </div>
    </div>
  );
};

export const S1Intro: React.FC = () => {
  const c = cfg("S1-Intro");
  const f = useCurrentFrame();
  const o = windowed(f, LOGO_AT, LOGO_AT + 4.5, 0.4);
  return (
    <SceneFrame cfg={c}>
      <ClipSlot cfg={c}>
        <LowerThird at={1.5} out={6.5} name="Irfan" role="CancerFlow · Junction 2026" />
        {o > 0 && (
          <AbsoluteFill style={{ background: `rgba(7,13,24,${0.72 * o})`, display: "flex", alignItems: "center", justifyContent: "center", opacity: o }}>
            <Logo at={LOGO_AT} size={130} tagline="Connected cancer care · national digital twin" />
          </AbsoluteFill>
        )}
      </ClipSlot>
    </SceneFrame>
  );
};

export const S4Donny: React.FC = () => {
  const c = cfg("S4-Donny");
  return (
    <SceneFrame cfg={c}>
      <ClipSlot cfg={c}>
        <LowerThird at={1} out={8} name="Donny Lehtonen" role="Regional care coordinator (played by Irfan)"
          extra={<div style={{ display: "flex", gap: 10, alignItems: "center", fontFamily: MONO, fontSize: 18, color: system.text2 }}>
            <span style={{ width: 10, height: 10, borderRadius: 5, background: "#ef4444", boxShadow: "0 0 10px #ef4444" }} />
            live call · ElevenLabs voice agent → typed CancerFlow backend tools</div>} />
        <ApprovalNote />
      </ClipSlot>
    </SceneFrame>
  );
};

/** Persistent corner note during the call: the AI recommends, a human approves. */
const ApprovalNote: React.FC = () => {
  const f = useCurrentFrame();
  const o = ramp(f, 9, 0.6);
  return (
    <div style={{ position: "absolute", right: 70, top: 60, opacity: o * 0.95, fontFamily: FONT, background: "rgba(7,13,24,.78)", border: `1px solid ${system.border2}`,
      borderRadius: 12, padding: "10px 16px", color: system.text2, fontSize: 20, display: "flex", gap: 12, alignItems: "center" }}>
      <Tag t="SYNTHETIC" size={14} /><Tag t="SIMULATED" size={14} /> recommendations only · approval stays with Donny
    </div>
  );
};

export const S8Ending: React.FC = () => {
  const c = cfg("S8-Ending");
  const f = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const endAt = durationInFrames / 30 - END_CARD_SEC;
  const o = ramp(f, endAt, 0.8);
  return (
    <SceneFrame cfg={c}>
      <ClipSlot cfg={c} />
      {f >= s(endAt) - 1 && (
        <AbsoluteFill style={{ background: `radial-gradient(ellipse at 50% 45%, #10203a 0%, ${system.bg} 70%)`, opacity: o, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <Logo at={endAt + 0.3} size={140} tagline="Because every day matters." />
          <div style={{ position: "absolute", bottom: 70, fontFamily: MONO, fontSize: 18, color: system.muted, opacity: ramp(f, endAt + 1.6, 0.6), display: "flex", gap: 14, alignItems: "center" }}>
            Junction 2026 · AstraZeneca challenge · <Tag t="SYNTHETIC" size={14} /> patients · <Tag t="SIMULATED" size={14} /> outcomes
          </div>
        </AbsoluteFill>
      )}
    </SceneFrame>
  );
};
