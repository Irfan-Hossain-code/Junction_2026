/** Visual identity, copied from the two apps so the video matches the product:
 *  - patient world  = care-app/src/styles.css  (calm clinical light, teal)
 *  - system world   = twin-ui/src/styles.css   (dark navy dashboard, sky accent)
 */
import { loadFont as loadInter } from "@remotion/google-fonts/Inter";
import { loadFont as loadMono } from "@remotion/google-fonts/JetBrainsMono";

const inter = loadInter("normal", { weights: ["400", "500", "600", "700", "800"], subsets: ["latin", "latin-ext"] });
const mono = loadMono("normal", { weights: ["500"], subsets: ["latin"] });

export const FONT = inter.fontFamily;
export const MONO = mono.fontFamily;

export const patient = {
  bg: "#f4f7fa", panel: "#ffffff", panel2: "#f0f4f8", border: "#e1e7ef", border2: "#cfd8e3",
  text: "#17212f", text2: "#3d4b5e", muted: "#677689",
  accent: "#0d9488", accentSoft: "#e6f5f3",
  good: "#15803d", goodSoft: "#e8f6ee", warn: "#b45309", warnSoft: "#fff6e6", bad: "#c62828", badSoft: "#fdecec",
};

export const system = {
  bg: "#070d18", panel: "#0e1626", panel2: "#121c30", border: "#1d2a42", border2: "#283754",
  text: "#e7eef8", text2: "#a9b8cf", muted: "#6f819e",
  accent: "#38bdf8", teal: "#2dd4bf", good: "#34d399", warn: "#f5a524", bad: "#ef5350", violet: "#a78bfa",
};

/** Epistemic tags used across CancerFlow (every number carries one). */
export const TAG = {
  SYNTHETIC: "#fbbf24", SIMULATED: "#c084fc", OBSERVED: "#60a5fa", REFERENCE: "#2dd4bf", HYPOTHESIS: "#94a3b8",
} as const;

export const W = 1920;
export const H = 1080;
export const FPS = 30;
