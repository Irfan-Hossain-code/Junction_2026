import React from "react";
import { FONT, patient } from "../theme";

/** Flat building illustrations. Origin = bottom-centre; `lit` 0..1 warms the windows (activity). */
export const Building: React.FC<{ kind: "home" | "clinic" | "hospital"; label?: string; lit?: number; alert?: number; scale?: number }> = ({ kind, label, lit = 0, alert = 0, scale = 1 }) => {
  const win = (x: number, y: number, w = 34, h = 40, key?: React.Key) => (
    <rect key={key ?? `${x}-${y}`} x={x} y={y} width={w} height={h} rx={5} fill={lit > 0 ? `rgba(253,224,71,${0.35 + 0.5 * lit})` : "#dbeafe"} stroke="#94a3b8" strokeWidth={2} />
  );
  let body: React.ReactNode, w = 300, h = 300;
  if (kind === "home") {
    w = 260; h = 240;
    body = (<g>
      <path d="M 10 110 L 130 20 L 250 110 Z" fill="#b45309" />
      <rect x={30} y={105} width={200} height={135} fill="#fde68a" />
      <rect x={110} y={160} width={44} height={80} rx={4} fill="#92400e" />
      {win(50, 130, 44, 40)}{win(170, 130, 44, 40)}
      <rect x={175} y={40} width={26} height={50} fill="#78350f" />
    </g>);
  } else if (kind === "clinic") {
    w = 320; h = 230;
    body = (<g>
      <rect x={10} y={60} width={300} height={170} rx={8} fill="#f8fafc" stroke="#cbd5e1" strokeWidth={3} />
      <rect x={10} y={48} width={300} height={22} rx={6} fill={patient.accent} />
      <g transform="translate(160 20)"><rect x={-26} y={-8} width={52} height={52} rx={10} fill="#fff" stroke={patient.accent} strokeWidth={4} />
        <path d="M -6 2 h 12 v 10 h 10 v 12 h -10 v 10 h -12 v -10 h -10 v -12 h 10 Z" fill={patient.accent} /></g>
      {[40, 100, 196, 256].map((x) => win(x, 100, 34, 40))}
      <rect x={138} y={160} width={44} height={70} rx={4} fill="#99f6e4" stroke={patient.accent} strokeWidth={3} />
    </g>);
  } else {
    w = 380; h = 380;
    body = (<g>
      <rect x={20} y={60} width={340} height={320} rx={8} fill="#f1f5f9" stroke="#cbd5e1" strokeWidth={3} />
      <rect x={120} y={10} width={140} height={60} rx={6} fill="#e2e8f0" stroke="#cbd5e1" strokeWidth={3} />
      <g transform="translate(190 40)"><circle r={22} fill="#fff" stroke="#ef4444" strokeWidth={3} />
        <path d="M -5 -14 h 10 v 9 h 9 v 10 h -9 v 9 h -10 v -9 h -9 v -10 h 9 Z" fill="#ef4444" /></g>
      {[0, 1, 2, 3].map((r) => [0, 1, 2, 3, 4].map((c) => win(46 + c * 62, 90 + r * 62, 36, 40, `${r}-${c}`)))}
      <rect x={160} y={320} width={60} height={60} rx={4} fill="#bae6fd" stroke="#64748b" strokeWidth={3} />
      {alert > 0 && <rect x={20} y={60} width={340} height={320} rx={8} fill={`rgba(239,83,80,${0.25 * alert})`} />}
    </g>);
  }
  return (
    <div style={{ position: "relative", width: w * scale, height: h * scale, transform: "translateX(-50%)" }}>
      <svg viewBox={`0 0 ${kind === "hospital" ? 380 : w} ${h}`} width={w * scale} height={h * scale} style={{ overflow: "visible" }}>{body}</svg>
      {label && (
        <div style={{ position: "absolute", top: "100%", left: "50%", transform: "translateX(-50%)", marginTop: 10, whiteSpace: "nowrap",
          fontFamily: FONT, fontSize: 19 * Math.max(0.8, scale), fontWeight: 600, color: patient.text2, background: "rgba(255,255,255,.85)", padding: "4px 12px", borderRadius: 8 }}>{label}</div>
      )}
    </div>
  );
};

/** Soft patient-world backdrop: sky gradient, distant hills, ground line. */
export const World: React.FC<{ groundY?: number; children?: React.ReactNode; tint?: number }> = ({ groundY = 900, children, tint = 0 }) => (
  <div style={{ position: "absolute", inset: 0, background: `linear-gradient(180deg, #eef6f8 0%, ${patient.bg} 60%)` }}>
    <svg width={1920} height={1080} style={{ position: "absolute", inset: 0 }}>
      <path d={`M 0 ${groundY - 120} Q 300 ${groundY - 220} 620 ${groundY - 130} T 1300 ${groundY - 140} T 1920 ${groundY - 150} L 1920 ${groundY} L 0 ${groundY} Z`} fill="#dcefe9" />
      <path d={`M 0 ${groundY - 60} Q 420 ${groundY - 130} 900 ${groundY - 70} T 1920 ${groundY - 80} L 1920 ${groundY} L 0 ${groundY} Z`} fill="#cde7df" />
      <rect x={0} y={groundY} width={1920} height={1080 - groundY} fill="#e2e8f0" />
      <rect x={0} y={groundY} width={1920} height={4} fill="#cbd5e1" />
    </svg>
    {tint > 0 && <div style={{ position: "absolute", inset: 0, background: `rgba(7,13,24,${tint})` }} />}
    {children}
  </div>
);
