import React from "react";
import { useCurrentFrame } from "remotion";
import { pop, ramp } from "../anim";
import { FONT, MONO, patient, system } from "../theme";

export type NodeState = "pending" | "done" | "current" | "warn" | "bad";
export interface TNode { label: string; date?: string; sub?: string; /** seconds → state changes */ states: [number, NodeState][]; appearAt?: number }

const COLOR = (dark: boolean): Record<NodeState, string> => ({
  pending: dark ? system.border2 : patient.border2, done: dark ? system.teal : patient.accent, current: dark ? system.accent : patient.accent,
  warn: "#f5a524", bad: dark ? system.bad : patient.bad,
});

function stateAt(n: TNode, t: number): NodeState {
  let st: NodeState = "pending";
  for (const [at, x] of n.states) if (t >= at) st = x;
  return st;
}

/** Emma's pathway as a horizontal track (the patient app's journey stepper, scaled up for video). */
export const PathwayTimeline: React.FC<{ nodes: TNode[]; width?: number; dark?: boolean; highlight?: number; compact?: boolean }> = ({ nodes, width = 1500, dark = false, compact = false }) => {
  const f = useCurrentFrame();
  const t = f / 30;
  const gap = width / Math.max(1, nodes.length - 1);
  const C = COLOR(dark);
  const states = nodes.map((n) => stateAt(n, t));
  // progress line reaches the last non-pending node
  const last = states.reduce((acc, st, i) => (st !== "pending" ? i : acc), -1);
  const r = compact ? 13 : 18;
  return (
    <div style={{ position: "relative", width, height: compact ? 90 : 150, fontFamily: FONT }}>
      <div style={{ position: "absolute", left: 0, right: 0, top: r - 2, height: 4, borderRadius: 2, background: C.pending }} />
      <div style={{ position: "absolute", left: 0, width: Math.max(0, last) * gap, top: r - 2, height: 4, borderRadius: 2, background: C.done, transition: "none" }} />
      {nodes.map((n, i) => {
        const st = states[i];
        const appear = n.appearAt === undefined ? 1 : pop(f, n.appearAt);
        const col = C[st];
        const pulse = st === "current" || st === "warn" || st === "bad" ? 0.5 + 0.5 * Math.sin(f / 6) : 0;
        return (
          <div key={i} style={{ position: "absolute", left: i * gap, top: 0, transform: `translateX(-50%) scale(${appear})`, opacity: appear, display: "flex", flexDirection: "column", alignItems: "center", width: 220 }}>
            <div style={{
              width: r * 2, height: r * 2, borderRadius: r, background: st === "pending" ? (dark ? system.panel : "#fff") : col, border: `3px solid ${col}`,
              boxShadow: pulse ? `0 0 0 ${6 + pulse * 6}px ${col}33` : undefined, display: "flex", alignItems: "center", justifyContent: "center",
              color: "#fff", fontWeight: 800, fontSize: r * 1.05,
            }}>{st === "done" ? "✓" : st === "bad" ? "!" : st === "warn" ? "!" : ""}</div>
            {!compact && <>
              <div style={{ marginTop: 10, fontWeight: 700, fontSize: 21, color: st === "pending" ? (dark ? system.muted : patient.muted) : dark ? system.text : patient.text, textAlign: "center", lineHeight: 1.2 }}>{n.label}</div>
              {n.date && <div style={{ fontFamily: MONO, fontSize: 16, marginTop: 4, color: st === "bad" ? C.bad : dark ? system.text2 : patient.muted }}>{n.date}</div>}
              {n.sub && <div style={{ fontSize: 15, marginTop: 2, color: st === "bad" ? C.bad : st === "warn" ? "#b45309" : dark ? system.muted : patient.muted, textAlign: "center" }}>{n.sub}</div>}
            </>}
          </div>
        );
      })}
    </div>
  );
};

export const fadeIn = (f: number, at: number) => ramp(f, at, 0.5);
