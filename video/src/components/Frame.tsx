import React from "react";
import { AbsoluteFill, Audio, OffthreadVideo, interpolate, useCurrentFrame, useVideoConfig } from "remotion";
import { ramp, s } from "../anim";
import { asset } from "../assets";
import { CAPTIONS, type SceneCfg } from "../config";
import { FONT, MONO, system } from "../theme";

/** Every scene sits in this: narration audio (if recorded), captions (placeholder for VO), edge fades. */
export const SceneFrame: React.FC<{ cfg: SceneCfg; dark?: boolean; children: React.ReactNode; captionsDark?: boolean }> = ({ cfg, children, captionsDark }) => {
  const f = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const fade = Math.min(interpolate(f, [0, 10], [0, 1], { extrapolateRight: "clamp" }), interpolate(f, [durationInFrames - 10, durationInFrames], [1, 0], { extrapolateLeft: "clamp" }));
  const audio = asset.audio(cfg.audio);
  return (
    <AbsoluteFill style={{ opacity: fade }}>
      {children}
      {audio && <Audio src={audio} />}
      {CAPTIONS && <Captions lines={cfg.narration} dark={captionsDark} />}
    </AbsoluteFill>
  );
};

export const Captions: React.FC<{ lines: SceneCfg["narration"]; dark?: boolean }> = ({ lines, dark = true }) => {
  const f = useCurrentFrame();
  const t = f / 30;
  const i = lines.findIndex((l, k) => t >= l.at && t < (l.dur !== undefined ? l.at + l.dur : lines[k + 1]?.at ?? Infinity));
  if (i < 0) return null;
  const l = lines[i];
  const o = Math.min(ramp(f, l.at, 0.2), 1);
  return (
    <div style={{ position: "absolute", left: 0, right: 0, bottom: 44, display: "flex", justifyContent: "center", pointerEvents: "none" }}>
      <div style={{
        maxWidth: 1400, fontFamily: FONT, fontSize: 34, lineHeight: 1.35, fontWeight: 500, textAlign: "center", padding: "12px 26px", borderRadius: 14,
        color: dark ? "#f8fafc" : "#0f172a", background: dark ? "rgba(7,13,24,.72)" : "rgba(255,255,255,.88)", opacity: o,
      }}>{l.text}</div>
    </div>
  );
};

/** Live-action / screen-recording slot. Missing file → clear placeholder card naming what to record. */
export const ClipSlot: React.FC<{ cfg: SceneCfg; children?: React.ReactNode }> = ({ cfg, children }) => {
  const src = asset.clip(cfg.clip?.file);
  const f = useCurrentFrame();
  if (src) return (
    <AbsoluteFill style={{ background: "#000" }}>
      <OffthreadVideo src={src} startFrom={s(cfg.clip?.startFromSec ?? 0)} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
      {children}
    </AbsoluteFill>
  );
  return (
    <AbsoluteFill style={{ background: `radial-gradient(ellipse at 50% 40%, #13213a 0%, ${system.bg} 70%)`, fontFamily: FONT, color: system.text }}>
      <div style={{ position: "absolute", inset: 60, border: `3px dashed ${system.border2}`, borderRadius: 28 }} />
      <div style={{ position: "absolute", top: "50%", left: "50%", transform: "translate(-50%, -50%)", textAlign: "center", width: 1300 }}>
        <div style={{ fontFamily: MONO, fontSize: 24, color: system.warn, letterSpacing: 3 }}>CLIP TO RECORD · public/clips/{cfg.clip?.file}</div>
        <div style={{ fontSize: 62, fontWeight: 800, marginTop: 18 }}>{cfg.title}</div>
        <div style={{ fontSize: 30, color: system.text2, marginTop: 16 }}>{cfg.clip?.what}</div>
        <div style={{ fontFamily: MONO, fontSize: 22, color: system.muted, marginTop: 30 }}>{(f / 30).toFixed(1)} s / {cfg.durationSec} s</div>
      </div>
      {children}
    </AbsoluteFill>
  );
};
