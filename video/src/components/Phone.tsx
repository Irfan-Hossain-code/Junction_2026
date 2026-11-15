import React from "react";
import { Img, useCurrentFrame } from "remotion";
import { asset } from "../assets";
import type { Msg } from "../data/messages";
import { FONT, MONO, patient } from "../theme";
import { ramp } from "../anim";
import { MARK_RECREATED } from "../config";

/**
 * Phone frame. Content priority:
 *  1. a real screenshot from public/screens/<screen>.png (captured by scripts/capture_screens.mjs)
 *  2. otherwise a rebuild of the care app's "Messages" inbox (same structure as care-app PatientHome <Inbox/>)
 *     showing only real backend notification texts, marked "recreated" while MARK_RECREATED is on.
 * Multiple screens cross-fade: pass `screens` with start times.
 */
export const PHONE_W = 392, PHONE_H = 848;

export const Phone: React.FC<{
  screens?: { at: number; file: string; fallback?: Msg[]; scroll?: number }[];
  height?: number; children?: React.ReactNode; style?: React.CSSProperties;
}> = ({ screens = [], height = PHONE_H, children, style }) => {
  const f = useCurrentFrame();
  const k = height / PHONE_H;
  return (
    <div style={{ width: PHONE_W * k, height, position: "relative", ...style }}>
      <div style={{ width: PHONE_W, height: PHONE_H, transform: `scale(${k})`, transformOrigin: "top left", position: "absolute",
        borderRadius: 54, background: "#0b1220", padding: 14, boxShadow: "0 40px 90px rgba(15,23,42,.35), inset 0 0 0 2px #1f2a3d" }}>
        <div style={{ position: "relative", width: "100%", height: "100%", borderRadius: 42, overflow: "hidden", background: patient.bg }}>
          {screens.map((sc, i) => {
            const next = screens[i + 1];
            const o = Math.min(ramp(f, sc.at, 0.35), next ? 1 - ramp(f, next.at, 0.35) : 1);
            if (o <= 0) return null;
            const src = asset.screen(`${sc.file}.png`);
            return (
              <div key={i} style={{ position: "absolute", inset: 0, opacity: o }}>
                {src ? <Img src={src} style={{ width: "100%", transform: `translateY(${-(sc.scroll ?? 0)}px)` }} />
                  : <InboxScreen msgs={sc.fallback ?? []} file={sc.file} />}
              </div>
            );
          })}
          {children}
          <div style={{ position: "absolute", top: 10, left: "50%", marginLeft: -60, width: 120, height: 30, borderRadius: 16, background: "#0b1220" }} />
        </div>
      </div>
    </div>
  );
};

/** Care-app inbox rebuilt with real notification text (fallback when no screenshot was captured). */
export const InboxScreen: React.FC<{ msgs: Msg[]; file: string }> = ({ msgs, file }) => (
  <div style={{ fontFamily: FONT, color: patient.text, fontSize: 15, height: "100%", background: patient.bg }}>
    <div style={{ display: "flex", alignItems: "center", gap: 9, padding: "48px 18px 12px", background: patient.panel, borderBottom: `1px solid ${patient.border}` }}>
      <span style={{ width: 11, height: 11, borderRadius: 6, background: patient.accent, boxShadow: `0 0 10px ${patient.accent}` }} />
      <b style={{ fontSize: 16 }}>CancerFlow</b>
      <span style={{ marginLeft: "auto", fontSize: 12, color: patient.muted }}>My care</span>
    </div>
    <div style={{ padding: "16px 16px" }}>
      <div style={{ fontSize: 25, fontWeight: 700, margin: "4px 0 12px" }}>Hello, Emma</div>
      <div style={{ background: patient.panel, border: `1px solid ${patient.border}`, borderRadius: 14, padding: 14 }}>
        <div style={{ fontWeight: 700, marginBottom: 6 }}>Messages</div>
        {msgs.map((m, i) => (
          <div key={i} style={{ borderLeft: `3px solid ${m.kind === "critical" ? "#ef5350" : m.kind === "warning" ? "#f5a524" : patient.border}`,
            padding: "6px 10px", margin: "8px 0", background: i === 0 ? "rgba(13,148,136,.06)" : undefined, borderRadius: i === 0 ? 6 : 0 }}>
            <b style={{ fontSize: 14.5 }}>{m.title}</b>
            <p style={{ margin: "4px 0", fontSize: 13.5, lineHeight: 1.4, color: patient.text2 }}>{m.body}</p>
          </div>
        ))}
      </div>
    </div>
    {MARK_RECREATED && (
      <div style={{ position: "absolute", bottom: 14, left: 14, right: 14, fontFamily: MONO, fontSize: 10.5, color: "#a16207", background: "#fff6e6",
        border: "1px dashed #f5a524", borderRadius: 8, padding: "6px 8px", textAlign: "center" }}>
        recreated UI · replace with screens/{file}.png
      </div>
    )}
  </div>
);
