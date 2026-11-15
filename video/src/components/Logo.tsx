import React from "react";
import { useCurrentFrame } from "remotion";
import { pop, ramp } from "../anim";
import { FONT, system } from "../theme";

/** The apps' mark (dot + ring, from the favicons) with the wordmark. */
export const Logo: React.FC<{ at?: number; size?: number; color?: string; text?: string; tagline?: string; dark?: boolean }> = ({ at = 0, size = 120, color = system.accent, text = "CancerFlow", tagline, dark = true }) => {
  const f = useCurrentFrame();
  const p = pop(f, at, 12);
  const ring = ramp(f, at + 0.15, 0.7);
  const word = ramp(f, at + 0.35, 0.6);
  const tag = tagline ? ramp(f, at + 0.9, 0.6) : 0;
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", fontFamily: FONT }}>
      <div style={{ display: "flex", alignItems: "center", gap: size * 0.32 }}>
        <svg width={size} height={size} viewBox="0 0 32 32" style={{ transform: `scale(${p})`, overflow: "visible" }}>
          <circle cx={16} cy={16} r={7} fill={color} style={{ filter: `drop-shadow(0 0 4px ${color})` }} />
          <circle cx={16} cy={16} r={13} fill="none" stroke={color} strokeOpacity={0.45} strokeWidth={2}
            strokeDasharray={2 * Math.PI * 13} strokeDashoffset={(1 - ring) * 2 * Math.PI * 13} transform="rotate(-90 16 16)" />
        </svg>
        <div style={{ fontSize: size * 0.82, fontWeight: 800, letterSpacing: -1, color: dark ? "#f8fafc" : "#0f172a", opacity: word, transform: `translateX(${(1 - word) * -24}px)` }}>{text}</div>
      </div>
      {tagline && <div style={{ fontSize: size * 0.34, marginTop: size * 0.28, color: dark ? system.text2 : "#3d4b5e", opacity: tag, transform: `translateY(${(1 - tag) * 10}px)`, fontWeight: 500 }}>{tagline}</div>}
    </div>
  );
};
