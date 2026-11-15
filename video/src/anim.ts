import { Easing, interpolate, spring } from "remotion";
import { FPS } from "./theme";

/** seconds -> frames */
export const s = (sec: number) => Math.round(sec * FPS);

const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;

/** 0..1 ramp between two times (seconds), eased. */
export function ramp(frame: number, fromSec: number, durSec = 0.5, ease = Easing.out(Easing.cubic)) {
  return interpolate(frame, [s(fromSec), s(fromSec + durSec)], [0, 1], { ...clamp, easing: ease });
}

/** Visible window: fades in at `inSec`, out at `outSec` (if given). */
export function windowed(frame: number, inSec: number, outSec?: number, fade = 0.4) {
  const a = ramp(frame, inSec, fade);
  const b = outSec === undefined ? 1 : 1 - ramp(frame, outSec, fade, Easing.in(Easing.cubic));
  return Math.min(a, b);
}

/** Springy pop-in starting at `atSec`. */
export function pop(frame: number, atSec: number, damping = 14) {
  return spring({ frame: frame - s(atSec), fps: FPS, config: { damping, stiffness: 140, mass: 0.8 } });
}

export function lerp(a: number, b: number, t: number) {
  return a + (b - a) * t;
}

export { clamp };
