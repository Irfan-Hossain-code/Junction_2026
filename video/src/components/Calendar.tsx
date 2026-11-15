import React from "react";
import { interpolate, useCurrentFrame } from "remotion";
import { s } from "../anim";
import { dayParts } from "../data/story";
import { FONT, patient } from "../theme";

/** Tear-off calendar. `dates` = [{at: sec, iso}] — flips to each date at its time. */
export const Calendar: React.FC<{ dates: { at: number; iso: string; note?: string; tone?: "ok" | "warn" | "bad" }[]; size?: number }> = ({ dates, size = 220 }) => {
  const f = useCurrentFrame();
  let idx = 0;
  dates.forEach((d, i) => { if (f >= s(d.at)) idx = i; });
  const cur = dates[idx];
  const prev = dates[idx - 1];
  const flip = prev ? interpolate(f, [s(cur.at), s(cur.at) + 10], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }) : 1;
  const k = size / 220;
  const page = (iso: string, tone?: string, note?: string) => {
    const p = dayParts(iso);
    const head = tone === "bad" ? patient.bad : tone === "warn" ? "#d97706" : patient.accent;
    return (
      <div style={{ position: "absolute", inset: 0, borderRadius: 22 * k, overflow: "hidden", background: "#fff", boxShadow: "0 14px 36px rgba(15,23,42,.14)", fontFamily: FONT }}>
        <div style={{ background: head, color: "#fff", fontWeight: 700, fontSize: 26 * k, letterSpacing: 2 * k, textAlign: "center", padding: `${10 * k}px 0` }}>{p.month.toUpperCase()} {p.year}</div>
        <div style={{ textAlign: "center", fontSize: 104 * k, fontWeight: 800, color: patient.text, lineHeight: 1.05, marginTop: 6 * k }}>{p.day}</div>
        <div style={{ textAlign: "center", fontSize: 24 * k, color: patient.muted, fontWeight: 600 }}>{p.dow}{note ? ` · ${note}` : ""}</div>
      </div>
    );
  };
  return (
    <div style={{ width: size, height: size * 1.05, position: "relative", perspective: 800 }}>
      {page(cur.iso, cur.tone, cur.note)}
      {prev && flip < 1 && (
        <div style={{ position: "absolute", inset: 0, transformOrigin: "top center", transform: `rotateX(${flip * 100}deg)`, opacity: 1 - flip * 0.6 }}>
          {page(prev.iso, prev.tone, prev.note)}
        </div>
      )}
      {[0.28, 0.72].map((x) => <div key={x} style={{ position: "absolute", top: -8 * k, left: `${x * 100}%`, width: 14 * k, height: 26 * k, borderRadius: 7 * k, background: "#334155" }} />)}
    </div>
  );
};
