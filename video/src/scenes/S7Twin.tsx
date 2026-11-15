import React from "react";
import { AbsoluteFill, Easing, interpolate, OffthreadVideo, useCurrentFrame } from "remotion";
import { clamp, pop, ramp, s, windowed } from "../anim";
import { asset } from "../assets";
import { demoHospital, FinlandMap, MAP } from "../components/FinlandMap";
import { SceneFrame } from "../components/Frame";
import { PathwayTimeline, type TNode } from "../components/PathwayTimeline";
import { blinkAt, LOOKS, Person, POSES } from "../components/Person";
import { ActLabel, Tag } from "../components/ui";
import { SCENES } from "../config";
import twins from "../data/twins.json";
import { FONT, MONO, patient, system } from "../theme";

const cfg = SCENES.find((x) => x.id === "S7-Twin")!;
/** clip = when the twin screen recording takes over; stats = the 36.6 → 53.3 overlay; back = "brings us back to Emma". */
export const B7 = { shrink: 1.2, lanes: 2.2, map: 7.0, clip: 9.8, statA: 32.6, statB: 39.4, statOut: 49.6, back: 57.0, end: 60 };

const T = twins as { baseline: number; disruption: number; recovery: number; metric: string; source: string };

/** Abstract pathway lanes (illustrative only: "hundreds of patients"); deterministic pseudo-random progress. */
const LANES = Array.from({ length: 34 }, (_, i) => ({ y: i, p: ((i * 37) % 100) / 100, speed: 0.04 + ((i * 13) % 7) / 100, warn: i % 9 === 4 }));

export const S7Twin: React.FC = () => {
  const f = useCurrentFrame();
  const t = f / 30;
  const clip = asset.clip(cfg.clip?.file);

  // phase 1: Emma's finished timeline shrinks into one lane among many
  const shrink = ramp(f, B7.shrink, 1.2, Easing.inOut(Easing.cubic));
  const lanesO = windowed(f, B7.lanes, B7.map + 0.4, 0.6);
  const lightO = 1 - ramp(f, B7.shrink, 0.7);
  const emmaNodes: TNode[] = ["Symptoms", "GP", "Referral", "Specialist", "Colonoscopy", "Diagnosis", "Staging", "MDT", "Treatment"].map((l) => ({ label: l, states: [[0, "done"]] }));

  // phase 2: Finland map (real county outlines, real network hospitals)
  const mapO = windowed(f, B7.map - 0.6, clip ? B7.clip + 0.6 : B7.back - 0.4, 0.8);
  const pk = demoHospital("pkks");
  const z = interpolate(f, [s(B7.map - 0.6), s(B7.map + 2.6)], [4.3, 1.0], { ...clamp, easing: Easing.inOut(Easing.cubic) });
  const cx = interpolate(f, [s(B7.map - 0.6), s(B7.map + 2.6)], [pk.x - 40, MAP.width / 2 + 120], clamp);
  const cy = interpolate(f, [s(B7.map - 0.6), s(B7.map + 2.6)], [pk.y - 8, MAP.height / 2 + 10], clamp);

  const clipO = clip ? windowed(f, B7.clip, B7.back - 0.2, 0.6) : 0;
  const statsO = windowed(f, B7.statA, B7.statOut, 0.5);
  const backO = ramp(f, B7.back - 0.2, 0.8);

  return (
    <SceneFrame cfg={cfg}>
      <AbsoluteFill style={{ background: system.bg }} />
      {/* light → dark */}
      <AbsoluteFill style={{ background: patient.bg, opacity: lightO }} />
      {lightO > 0 && <div style={{ opacity: lightO }}><ActLabel act="Act V · The bigger picture" title="Emma is one patient" /></div>}

      {/* lanes */}
      <AbsoluteFill style={{ opacity: Math.max(lanesO, t < B7.lanes ? 1 : 0) }}>
        <div style={{ position: "absolute", left: 960, top: 540, transform: `translate(-50%, -50%) scale(${1 - shrink * 0.55})` }}>
          <PathwayTimeline nodes={emmaNodes} width={1400} compact dark={lightO < 0.5} />
          <div style={{ position: "absolute", left: -20, top: -46, fontFamily: FONT, fontWeight: 700, fontSize: 30, color: lightO > 0.5 ? patient.accent : system.teal }}>Emma</div>
        </div>
        {LANES.map((l, i) => {
          const o = ramp(f, B7.lanes + (i % 12) * 0.06, 0.5);
          const y = 540 + (l.y - 17) * 28 + (l.y >= 17 ? 28 : 0);
          if (Math.abs(y - 540) < 20) return null;
          const prog = (l.p + t * l.speed) % 1;
          return (
            <div key={i} style={{ position: "absolute", left: 960 - 315, top: y, width: 630, height: 3, background: lightO > 0.5 ? patient.border2 : system.border2, opacity: o * 0.9, borderRadius: 2 }}>
              <div style={{ position: "absolute", left: `${prog * 100}%`, top: -5, width: 13, height: 13, borderRadius: 7, background: l.warn ? system.warn : system.accent, transform: "translateX(-50%)" }} />
            </div>
          );
        })}
        <div style={{ position: "absolute", right: 70, bottom: 150, fontFamily: MONO, fontSize: 16, color: system.muted, opacity: lanesO }}>illustrative</div>
      </AbsoluteFill>

      {/* national map */}
      {mapO > 0 && (
        <AbsoluteFill style={{ opacity: mapO }}>
          <FinlandMap cx={cx} cy={cy} zoom={z}>
            {(p) => (
              <svg width={1920} height={1080} style={{ position: "absolute", inset: 0 }}>
                {MAP.hospitals.map((h, i) => {
                  const [x, y] = p(h.x, h.y);
                  const pulse = 0.5 + 0.5 * Math.sin(f / 7 + i);
                  return <g key={h.id} opacity={ramp(f, B7.map + 0.4 + (i % 10) * 0.05, 0.5)}>
                    <circle cx={x} cy={y} r={10 + pulse * 8} fill={system.accent} opacity={0.15} />
                    <circle cx={x} cy={y} r={5.5} fill={system.accent} /></g>;
                })}
              </svg>
            )}
          </FinlandMap>
          <div style={{ position: "absolute", left: 72, top: 56, fontFamily: FONT, opacity: ramp(f, B7.map + 0.6, 0.6) }}>
            <div style={{ fontFamily: MONO, fontSize: 18, letterSpacing: 3, color: system.accent }}>ACT V · THE BIGGER PICTURE</div>
            <div style={{ fontSize: 40, fontWeight: 700, color: system.text, marginTop: 4 }}>A digital twin of Finland's cancer pathway network</div>
            <div style={{ fontSize: 22, color: system.text2, marginTop: 6, display: "flex", gap: 12, alignItems: "center" }}>{MAP.hospitals.length} facilities · {MAP.counties.length} regions <Tag t="OBSERVED" size={14} /> indicators <Tag t="SYNTHETIC" size={14} /> capacity</div>
          </div>
          {!clip && t > B7.clip && (
            <div style={{ position: "absolute", right: 70, top: 60, fontFamily: MONO, fontSize: 20, color: system.warn, border: `2px dashed ${system.warn}`, borderRadius: 10, padding: "10px 16px", background: "rgba(7,13,24,.7)" }}>
              insert twin screen recording · public/clips/{cfg.clip?.file}
            </div>
          )}
        </AbsoluteFill>
      )}

      {/* twin screen recording */}
      {clip && clipO > 0 && (
        <AbsoluteFill style={{ opacity: clipO }}>
          <OffthreadVideo src={clip} startFrom={s(cfg.clip?.startFromSec ?? 0)} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
        </AbsoluteFill>
      )}

      {/* simulated result overlay: verified against /twin/runs (scripts/export_twins.py) */}
      {statsO > 0 && <Stats o={statsO} />}

      {/* back to Emma */}
      {backO > 0 && (
        <AbsoluteFill style={{ background: patient.bg, opacity: backO, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <div style={{ transform: `scale(${0.6 + 0.4 * pop(f, B7.back, 14)})` }}>
            <Person look={LOOKS.emma} height={430} pose={{ ...POSES.wave, blink: blinkAt(f) }} mood="happy" />
          </div>
        </AbsoluteFill>
      )}
    </SceneFrame>
  );
};

const Stats: React.FC<{ o: number }> = ({ o }) => {
  const f = useCurrentFrame();
  const a = ramp(f, B7.statA, 1.0);
  const b = ramp(f, B7.statB, 1.2);
  const rows = [
    { label: "Baseline: normal Finland", v: T.baseline, show: 1, color: system.muted },
    { label: "Disruption: southern referral surge", v: T.disruption * a, show: a, color: system.bad },
    { label: "Recovery: CancerFlow strategy", v: T.disruption + (T.recovery - T.disruption) * b, show: b, color: system.good },
  ];
  return (
    <div style={{ position: "absolute", right: 70, top: 150, width: 720, opacity: o, fontFamily: FONT, background: "rgba(7,13,24,.9)", border: `1px solid ${system.border2}`,
      borderRadius: 20, padding: "26px 30px", boxShadow: "0 30px 80px rgba(0,0,0,.5)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <div style={{ fontSize: 24, fontWeight: 700, color: system.text }}>{T.metric}</div><span style={{ marginLeft: "auto" }}><Tag t="SIMULATED" size={16} /></span>
      </div>
      {rows.map((r) => (
        <div key={r.label} style={{ marginTop: 20, opacity: Math.max(0.15, r.show) }}>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 21, color: system.text2 }}><span>{r.label}</span>
            <b style={{ fontSize: 40, color: r.color, fontVariantNumeric: "tabular-nums" }}>{r.v.toFixed(1)} %</b></div>
          <div style={{ height: 14, background: system.panel2, borderRadius: 7, marginTop: 6 }}>
            <div style={{ width: `${r.v}%`, height: "100%", background: r.color, borderRadius: 7 }} /></div>
        </div>
      ))}
      <div style={{ fontSize: 16, color: system.muted, marginTop: 18, lineHeight: 1.45 }}>{T.source}</div>
    </div>
  );
};
