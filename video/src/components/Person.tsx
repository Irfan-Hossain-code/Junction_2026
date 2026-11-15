/**
 * One parametric flat-style figure for every character (Emma, GP, specialist, nurse).
 * Origin = between the feet; the figure is ~420 units tall. Everything is driven by props, so poses can be
 * interpolated frame by frame (walk cycles, waving, holding the phone, tapping Confirm).
 */
import React from "react";

export type Mood = "neutral" | "smile" | "worried" | "happy" | "talk";
export type Hair = "bob" | "short" | "bun" | "crop";

export interface Look {
  skin: string; hair: string; hairStyle: Hair; top: string; pants: string; shoes: string;
  coat?: boolean; stethoscope?: boolean; badge?: boolean; scarf?: string;
}

export const LOOKS: Record<"emma" | "gp" | "specialist" | "nurse" | "patient", Look> = {
  emma: { skin: "#f2c7a5", hair: "#6b3f26", hairStyle: "bob", top: "#0d9488", pants: "#334155", shoes: "#1f2937", scarf: "#f59e0b" },
  gp: { skin: "#e8b48f", hair: "#d4a24c", hairStyle: "bun", top: "#93c5fd", pants: "#1e3a5f", shoes: "#111827", coat: true, stethoscope: true, badge: true },
  specialist: { skin: "#d9a07a", hair: "#2b2b2b", hairStyle: "short", top: "#38bdf8", pants: "#0f2a44", shoes: "#111827", coat: true, badge: true },
  nurse: { skin: "#c98d66", hair: "#3b2416", hairStyle: "crop", top: "#5eead4", pants: "#5eead4", shoes: "#f8fafc", badge: true },
  patient: { skin: "#e5b897", hair: "#57534e", hairStyle: "short", top: "#94a3b8", pants: "#475569", shoes: "#1f2937" },
};

export interface Pose {
  /** degrees, outward-positive; 0 = hanging down */
  lShoulder?: number; lElbow?: number; rShoulder?: number; rElbow?: number;
  lLeg?: number; rLeg?: number;
  /** vertical bob (units) and lean (deg) */
  bob?: number; lean?: number;
  /** 0 open .. 1 closed */
  blink?: number;
  /** -1 (looking left) .. 1 (right) */
  look?: number;
}

export const POSES = {
  stand: { lShoulder: 6, lElbow: 4, rShoulder: 6, rElbow: 4 },
  phone: { lShoulder: 6, lElbow: 4, rShoulder: 8, rElbow: -138 },
  phoneTap: { lShoulder: -6, lElbow: -128, rShoulder: 8, rElbow: -138 },
  wave: { lShoulder: 6, lElbow: 4, rShoulder: 150, rElbow: 20 },
  explain: { lShoulder: 6, lElbow: 4, rShoulder: 40, rElbow: -70 },
  clipboard: { lShoulder: 4, lElbow: -120, rShoulder: 6, rElbow: 4 },
} satisfies Record<string, Required<Pick<Pose, "lShoulder" | "lElbow" | "rShoulder" | "rElbow">>>;

export function walkPose(t: number, base: Pose = POSES.stand, amp = 1): Pose {
  // t in cycles (1 = two steps)
  const sw = Math.sin(t * Math.PI * 2);
  return {
    ...base, lLeg: 16 * sw * amp, rLeg: -16 * sw * amp,
    lShoulder: (base.lShoulder ?? 0) - 12 * sw * amp,
    rShoulder: base.rElbow !== undefined && base.rElbow < -60 ? base.rShoulder : (base.rShoulder ?? 0) + 12 * sw * amp,
    bob: -Math.abs(Math.cos(t * Math.PI * 2)) * 5 * amp,
  };
}

interface Props {
  look: Look; pose?: Pose; mood?: Mood; height?: number;
  /** 'phone' puts a phone in the right hand; 'clipboard' a clipboard in the left */
  prop?: "phone" | "clipboard" | null;
  phoneGlow?: number;
  talk?: number; // 0..1 mouth open
  style?: React.CSSProperties;
}

const SH_Y = -270, SH_X = 40, UPPER = 64, FORE = 58, HIP_Y = -150, LEG = 150;

function Arm({ side, sh, el, look, children }: { side: 1 | -1; sh: number; el: number; look: Look; children?: React.ReactNode }) {
  // side -1 = screen-left, +1 = screen-right. Outward-positive angles mirror for the right side.
  const rot = side === -1 ? sh : -sh;
  const erot = side === -1 ? el : -el;
  const sleeve = look.coat ? "#f8fafc" : look.top;
  return (
    <g transform={`translate(${side * SH_X} ${SH_Y}) rotate(${rot})`}>
      <rect x={-11} y={-6} width={22} height={UPPER + 10} rx={11} fill={sleeve} />
      <g transform={`translate(0 ${UPPER}) rotate(${erot})`}>
        <rect x={-10} y={-8} width={20} height={FORE * 0.62} rx={10} fill={sleeve} />
        <rect x={-8.5} y={FORE * 0.45} width={17} height={FORE * 0.5} rx={8.5} fill={look.skin} />
        <circle cx={0} cy={FORE} r={10} fill={look.skin} />
        {children && <g transform={`translate(0 ${FORE}) rotate(${-(rot + erot)})`}>{children}</g>}
      </g>
    </g>
  );
}

export const Person: React.FC<Props> = ({ look, pose = POSES.stand, mood = "neutral", height = 420, prop = null, phoneGlow = 0, talk = 0, style }) => {
  const p: Pose = { ...POSES.stand, ...pose };
  const blink = p.blink ?? 0;
  const look_ = p.look ?? 0;
  const eyeY = -338, eyeDX = 14;
  const mouth = () => {
    const y = -312;
    if (talk > 0.05 || mood === "talk") {
      const o = Math.max(talk, mood === "talk" ? 0.5 : 0);
      return <ellipse cx={look_ * 3} cy={y} rx={7} ry={2 + 6 * o} fill="#7c2d12" />;
    }
    if (mood === "smile") return <path d={`M ${-11 + look_ * 3} ${y - 2} Q ${look_ * 3} ${y + 9} ${11 + look_ * 3} ${y - 2}`} stroke="#7c2d12" strokeWidth={3.5} fill="none" strokeLinecap="round" />;
    if (mood === "happy") return <path d={`M ${-13 + look_ * 3} ${y - 4} Q ${look_ * 3} ${y + 14} ${13 + look_ * 3} ${y - 4} Z`} fill="#7c2d12" />;
    if (mood === "worried") return <path d={`M ${-9 + look_ * 3} ${y + 4} Q ${look_ * 3} ${y - 4} ${9 + look_ * 3} ${y + 4}`} stroke="#7c2d12" strokeWidth={3.5} fill="none" strokeLinecap="round" />;
    return <path d={`M ${-8 + look_ * 3} ${y} L ${8 + look_ * 3} ${y}`} stroke="#7c2d12" strokeWidth={3.5} strokeLinecap="round" />;
  };
  const brows = mood === "worried"
    ? [<path key="l" d={`M ${-eyeDX - 9 + look_ * 3} ${eyeY - 12} L ${-eyeDX + 7 + look_ * 3} ${eyeY - 17}`} stroke={look.hair} strokeWidth={4} strokeLinecap="round" />,
       <path key="r" d={`M ${eyeDX + 9 + look_ * 3} ${eyeY - 12} L ${eyeDX - 7 + look_ * 3} ${eyeY - 17}`} stroke={look.hair} strokeWidth={4} strokeLinecap="round" />]
    : [<path key="l" d={`M ${-eyeDX - 8 + look_ * 3} ${eyeY - 14} Q ${-eyeDX + look_ * 3} ${eyeY - 19} ${-eyeDX + 8 + look_ * 3} ${eyeY - 14}`} stroke={look.hair} strokeWidth={3.5} fill="none" strokeLinecap="round" />,
       <path key="r" d={`M ${eyeDX - 8 + look_ * 3} ${eyeY - 14} Q ${eyeDX + look_ * 3} ${eyeY - 19} ${eyeDX + 8 + look_ * 3} ${eyeY - 14}`} stroke={look.hair} strokeWidth={3.5} fill="none" strokeLinecap="round" />];

  const hairBack = {
    bob: <path d="M -46 -332 Q -50 -392 0 -394 Q 50 -392 46 -332 L 48 -292 Q 30 -286 22 -300 L -22 -300 Q -30 -286 -48 -292 Z" fill={look.hair} />,
    bun: <g fill={look.hair}><circle cx={0} cy={-392} r={20} /><path d="M -43 -330 Q -46 -390 0 -390 Q 46 -390 43 -330 Z" /></g>,
    short: <path d="M -42 -334 Q -44 -388 0 -390 Q 44 -388 42 -334 Z" fill={look.hair} />,
    crop: <path d="M -41 -336 Q -42 -386 0 -387 Q 42 -386 41 -336 Z" fill={look.hair} />,
  }[look.hairStyle];
  const fringe = {
    bob: <path d="M -41 -340 Q -36 -384 4 -382 Q 40 -380 42 -342 Q 18 -366 -6 -360 Q -26 -356 -41 -340 Z" fill={look.hair} />,
    bun: <path d="M -40 -344 Q -30 -382 0 -380 Q 30 -382 40 -344 Q 20 -364 0 -362 Q -20 -364 -40 -344 Z" fill={look.hair} />,
    short: <path d="M -40 -346 Q -34 -384 2 -382 Q 38 -380 40 -350 Q 24 -368 6 -364 Q -20 -372 -40 -346 Z" fill={look.hair} />,
    crop: <path d="M -39 -350 Q -30 -380 0 -380 Q 30 -380 39 -350 Q 14 -366 -39 -350 Z" fill={look.hair} />,
  }[look.hairStyle];

  const rightProp = prop === "phone" ? (
    <g transform="rotate(-8) translate(-16 -46)">
      <rect width={32} height={56} rx={6} fill="#0f172a" />
      <rect x={3} y={4} width={26} height={48} rx={3} fill={phoneGlow > 0 ? `rgba(45,212,191,${0.35 + 0.65 * phoneGlow})` : "#1e293b"} />
    </g>
  ) : null;
  const leftProp = prop === "clipboard" ? (
    <g transform="rotate(6) translate(-20 -48)">
      <rect width={48} height={62} rx={5} fill="#a16207" />
      <rect x={5} y={8} width={38} height={50} rx={2} fill="#f8fafc" />
      {[18, 28, 38, 48].map((y) => <rect key={y} x={10} y={y} width={28} height={3} rx={1.5} fill="#cbd5e1" />)}
    </g>
  ) : null;

  const scale = height / 420;
  return (
    <svg viewBox="-120 -420 240 430" width={240 * scale} height={430 * scale} style={{ overflow: "visible", ...style }}>
      <ellipse cx={0} cy={2} rx={62} ry={9} fill="rgba(15,23,42,0.14)" />
      <g transform={`translate(0 ${p.bob ?? 0}) rotate(${p.lean ?? 0} 0 -150)`}>
        {/* legs */}
        {([-1, 1] as const).map((side) => (
          <g key={side} transform={`translate(${side * 17} ${HIP_Y}) rotate(${side === -1 ? p.lLeg ?? 0 : p.rLeg ?? 0})`}>
            <rect x={-12} y={-6} width={24} height={LEG - 6} rx={11} fill={look.pants} />
            <ellipse cx={side * 6} cy={LEG - 6} rx={18} ry={9} fill={look.shoes} />
          </g>
        ))}
        {/* back arm layer is fine flat: draw arms before torso so sleeves tuck under */}
        <Arm side={-1} sh={p.lShoulder!} el={p.lElbow!} look={look}>{leftProp}</Arm>
        {/* torso */}
        <path d={`M -44 ${SH_Y - 6} Q 0 ${SH_Y - 18} 44 ${SH_Y - 6} L 36 ${HIP_Y + 4} Q 0 ${HIP_Y + 12} -36 ${HIP_Y + 4} Z`} fill={look.top} />
        {look.coat && (
          <g>
            <path d={`M -46 ${SH_Y - 6} L -6 ${SH_Y - 10} L -10 ${HIP_Y + 40} L -44 ${HIP_Y + 36} Z`} fill="#f8fafc" />
            <path d={`M 46 ${SH_Y - 6} L 6 ${SH_Y - 10} L 10 ${HIP_Y + 40} L 44 ${HIP_Y + 36} Z`} fill="#f8fafc" />
            <path d={`M -6 ${SH_Y - 10} L -18 ${SH_Y + 30}`} stroke="#e2e8f0" strokeWidth={3} />
            <path d={`M 6 ${SH_Y - 10} L 18 ${SH_Y + 30}`} stroke="#e2e8f0" strokeWidth={3} />
          </g>
        )}
        {look.stethoscope && (
          <path d={`M -16 ${SH_Y - 8} Q -26 ${SH_Y + 40} -4 ${SH_Y + 56} M 16 ${SH_Y - 8} Q 24 ${SH_Y + 30} 10 ${SH_Y + 44}`} stroke="#334155" strokeWidth={4} fill="none" strokeLinecap="round" />
        )}
        {look.badge && <rect x={18} y={SH_Y + 30} width={16} height={20} rx={3} fill="#38bdf8" stroke="#0c4a6e" strokeWidth={2} />}
        {/* neck + head */}
        <rect x={-11} y={-298} width={22} height={30} rx={8} fill={look.skin} />
        {look.scarf && <path d={`M -30 ${SH_Y - 12} Q 0 ${SH_Y + 6} 30 ${SH_Y - 12} L 26 ${SH_Y - 2} Q 0 ${SH_Y + 16} -26 ${SH_Y - 2} Z`} fill={look.scarf} />}
        {hairBack}
        <ellipse cx={0} cy={-336} rx={38} ry={42} fill={look.skin} />
        <ellipse cx={-38} cy={-332} rx={6} ry={9} fill={look.skin} />
        <ellipse cx={38} cy={-332} rx={6} ry={9} fill={look.skin} />
        {fringe}
        {/* face */}
        {[-1, 1].map((sd) => (
          <ellipse key={sd} cx={sd * eyeDX + look_ * 4} cy={eyeY} rx={4.6} ry={5.6 * (1 - blink * 0.92)} fill="#1f2937" />
        ))}
        {brows}
        <circle cx={-22} cy={-318} r={6} fill="#fb7185" opacity={0.25} />
        <circle cx={22} cy={-318} r={6} fill="#fb7185" opacity={0.25} />
        {mouth()}
        <Arm side={1} sh={p.rShoulder!} el={p.rElbow!} look={look}>{rightProp}</Arm>
      </g>
    </svg>
  );
};

/** Natural blinking: closed for ~4 frames every ~3.4 s, offset per character. */
export function blinkAt(frame: number, offset = 0) {
  const t = (frame + offset) % 103;
  return t < 2 ? t / 2 : t < 4 ? 1 - (t - 2) / 2 : 0;
}
