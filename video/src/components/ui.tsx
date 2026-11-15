import React from "react";
import { interpolate, useCurrentFrame } from "remotion";
import { pop, ramp, s } from "../anim";
import type { Msg } from "../data/messages";
import { FONT, MONO, patient, system, TAG } from "../theme";

/** Epistemic tag pill, as in the apps (SYNTHETIC / SIMULATED / ...). */
export const Tag: React.FC<{ t: keyof typeof TAG; dark?: boolean; size?: number }> = ({ t, dark = true, size = 18 }) => (
  <span style={{
    fontFamily: MONO, fontSize: size, letterSpacing: 1, padding: `${size * 0.2}px ${size * 0.55}px`, borderRadius: 6,
    color: TAG[t], border: `1.5px solid ${TAG[t]}`, background: dark ? "rgba(0,0,0,.25)" : "rgba(255,255,255,.7)", whiteSpace: "nowrap",
  }}>{t}</span>
);

/** Mono chip with the director's event type, e.g. appointment.confirmed. */
export const EventChip: React.FC<{ type: string; at: number; color?: string; dark?: boolean }> = ({ type, at, color = patient.accent, dark }) => {
  const f = useCurrentFrame();
  const p = pop(f, at);
  return (
    <div style={{
      display: "inline-flex", alignItems: "center", gap: 10, fontFamily: MONO, fontSize: 20, padding: "8px 14px", borderRadius: 999,
      background: dark ? "rgba(14,22,38,.92)" : "rgba(255,255,255,.95)", color: dark ? system.text2 : patient.text2,
      border: `1.5px solid ${color}`, boxShadow: "0 6px 20px rgba(15,23,42,.10)",
      opacity: Math.min(1, p * 1.4), transform: `translateY(${(1 - p) * 16}px) scale(${0.9 + 0.1 * p})`,
    }}>
      <span style={{ width: 10, height: 10, borderRadius: 5, background: color }} />{type}
    </div>
  );
};

const KIND_COLOR: Record<string, string> = { warning: "#f5a524", critical: "#ef5350", booking: patient.accent, referral: patient.accent, result: patient.accent };

/** A CancerFlow notification, styled like the care app's inbox item (care-app/src/styles.css .inbox-item). */
export const Toast: React.FC<{
  m: Msg; at: number; width?: number; dark?: boolean; action?: string; tapAt?: number; done?: string; from?: "top" | "right" | "left"; scale?: number;
}> = ({ m, at, width = 560, dark = false, action, tapAt, done, from = "right", scale = 1 }) => {
  const f = useCurrentFrame();
  const p = pop(f, at, 16);
  const tapped = tapAt !== undefined && f >= s(tapAt);
  const press = tapAt === undefined ? 0 : interpolate(f, [s(tapAt) - 4, s(tapAt), s(tapAt) + 6], [0, 1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const off = { top: `translateY(${(1 - p) * -60}px)`, right: `translateX(${(1 - p) * 120}px)`, left: `translateX(${(1 - p) * -120}px)` }[from];
  const c = dark ? system : patient;
  return (
    <div style={{
      width, fontFamily: FONT, background: c.panel, color: c.text, borderRadius: 18, padding: "20px 24px 18px",
      borderLeft: `6px solid ${KIND_COLOR[m.kind] ?? c.border2}`, boxShadow: dark ? "0 20px 50px rgba(0,0,0,.45)" : "0 18px 44px rgba(15,23,42,.16)",
      border: dark ? `1px solid ${system.border2}` : undefined, opacity: Math.min(1, p * 1.5), transform: `${off} scale(${scale})`, transformOrigin: "top left",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6, color: c.muted, fontSize: 17 }}>
        <span style={{ width: 12, height: 12, borderRadius: 6, background: dark ? system.accent : patient.accent, boxShadow: `0 0 10px ${dark ? system.accent : patient.accent}` }} />
        <b style={{ color: c.text2 }}>CancerFlow</b><span>· now</span>
      </div>
      <div style={{ fontWeight: 700, fontSize: 25, lineHeight: 1.25 }}>{m.title}</div>
      <div style={{ fontSize: 20, lineHeight: 1.4, color: c.text2, marginTop: 6 }}>{m.body}</div>
      {action && (
        <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 12 }}>
          {tapped && done ? (
            <span style={{ color: dark ? system.good : patient.good, fontWeight: 700, fontSize: 21, opacity: ramp(f, tapAt! + 0.15, 0.3) }}>{done}</span>
          ) : (
            <span style={{
              background: dark ? system.accent : patient.accent, color: dark ? "#04121f" : "#fff", fontWeight: 700, fontSize: 21, padding: "9px 20px",
              borderRadius: 10, transform: `scale(${1 - press * 0.08})`, boxShadow: press ? `0 0 0 ${press * 10}px rgba(13,148,136,.18)` : undefined,
            }}>{action}</span>
          )}
        </div>
      )}
    </div>
  );
};

/** Finger tap ripple at an absolute position. */
export const Tap: React.FC<{ x: number; y: number; at: number }> = ({ x, y, at }) => {
  const f = useCurrentFrame();
  const t = (f - s(at)) / 18;
  if (t < -0.6 || t > 1) return null;
  const appear = Math.min(1, Math.max(0, (t + 0.6) / 0.6));
  return (
    <div style={{ position: "absolute", left: x, top: y, pointerEvents: "none" }}>
      <div style={{ position: "absolute", left: -22, top: -22, width: 44, height: 44, borderRadius: 22, background: "rgba(15,23,42,.28)", opacity: t < 0 ? appear : 1 - t, transform: `scale(${t < 0 ? 1 : 1 - t * 0.3})` }} />
      {t > 0 && <div style={{ position: "absolute", left: -40, top: -40, width: 80, height: 80, borderRadius: 40, border: "3px solid rgba(13,148,136,.7)", opacity: 1 - t, transform: `scale(${0.5 + t})` }} />}
    </div>
  );
};

/** Small heading label used at the top-left of scenes ("Act II · Emma's story"). */
export const ActLabel: React.FC<{ act: string; title: string; dark?: boolean }> = ({ act, title, dark }) => {
  const f = useCurrentFrame();
  const o = ramp(f, 0.2, 0.6);
  const c = dark ? system : patient;
  return (
    <div style={{ position: "absolute", left: 72, top: 56, fontFamily: FONT, opacity: o, transform: `translateY(${(1 - o) * -10}px)` }}>
      <div style={{ fontFamily: MONO, fontSize: 18, letterSpacing: 3, color: dark ? system.accent : patient.accent, textTransform: "uppercase" }}>{act}</div>
      <div style={{ fontSize: 34, fontWeight: 700, color: c.text, marginTop: 4 }}>{title}</div>
    </div>
  );
};
