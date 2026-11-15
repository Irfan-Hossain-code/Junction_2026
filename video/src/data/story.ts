/** Typed access to the exported director run (story.json, produced by scripts/export_story.py, read-only).
 *  Every date on screen comes from here. Re-export after a new rehearsal run and the video updates. */
import raw from "./story.json";

export type StepKey =
  | "book_gp" | "attend_gp" | "referral_received" | "reminder" | "confirm_specialist" | "attend_specialist"
  | "disruption" | "recover" | "confirm_new" | "pre_procedure" | "miss" | "followup_rebook"
  | "attend_colonoscopy" | "histology" | "staging" | "mdt" | "treatment";

export interface Step { key: StepKey; label: string; actor: string; event: string; sim_at: string | null; message: string | null }
export interface Appt { id: string; stage: string; status: string; at: string; facility: string; facility_id: string }

export const story = raw as unknown as {
  source: string; run: number;
  people: Record<"patient" | "gp" | "specialist" | "admin", string>;
  places: Record<string, string>;
  outage: { resource: string; hours: number; affected: number; urgent: number; others: { name: string; muni: string; priority: string }[] };
  appointments: Appt[];
  rebook_after_outage: { facility: string; resource: string; at: string; travel_min: number } | null;
  replacement_slots_evaluated: number | null;
  missed_at: string | null;
  steps: Step[];
};

export const step = (k: StepKey): Step => {
  const x = story.steps.find((s) => s.key === k);
  if (!x) throw new Error(`story.json has no step ${k}`);
  return x;
};

const DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** Parse an ISO string with offset into its *local* (Finnish) wall-clock parts, no timezone database needed. */
function parts(iso: string) {
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/);
  if (!m) throw new Error(`bad date ${iso}`);
  const [y, mo, d, h, mi] = m.slice(1).map(Number);
  const dow = new Date(Date.UTC(y, mo - 1, d)).getUTCDay();
  return { y, mo, d, h, mi, dow };
}

/** "Sat 21 Nov 11:30": the backend's core.fmt_time format, so on-screen text matches the app. */
export function fmtTime(iso: string) {
  const p = parts(iso);
  return `${DOW[p.dow]} ${String(p.d).padStart(2, "0")} ${MON[p.mo - 1]} ${String(p.h).padStart(2, "0")}:${String(p.mi).padStart(2, "0")}`;
}
export function fmtDay(iso: string) {
  const p = parts(iso);
  return `${DOW[p.dow]} ${p.d} ${MON[p.mo - 1]}`;
}
export function dayParts(iso: string) {
  const p = parts(iso);
  return { dow: DOW[p.dow], day: p.d, month: MON[p.mo - 1], year: p.y };
}
export function daysBetween(a: string, b: string) {
  const pa = parts(a), pb = parts(b);
  return Math.round((Date.UTC(pb.y, pb.mo - 1, pb.d) - Date.UTC(pa.y, pa.mo - 1, pa.d)) / 86400000);
}

export const appt = (stage: string, status: string) => story.appointments.find((a) => a.stage === stage && a.status === status);

/** Convenience: the dates the narration refers to. */
export const D = {
  start: step("book_gp").sim_at!,                       // Sun 15 Nov
  gp: appt("GP_VISIT", "attended")!.at,                 // Mon 16 Nov 10:00
  referral: step("attend_gp").sim_at!,
  specialist: appt("SPECIALIST_ASSESSMENT", "attended")!.at,
  reminder: step("reminder").sim_at!,
  firstColo: appt("COLONOSCOPY", "cancelled")!.at,      // the Unit 2 booking the outage cancelled
  outage: step("disruption").sim_at!,
  rebooked: story.rebook_after_outage?.at ?? appt("COLONOSCOPY", "missed")!.at,
  missed: appt("COLONOSCOPY", "missed")!.at,
  newColo: appt("COLONOSCOPY", "attended")!.at,
  histology: step("histology").sim_at!,
  staging: appt("STAGING", "attended")!.at,
  mdt: step("mdt").sim_at!,
  surgery: appt("SURGERY", "attended")!.at,
};
export const DAYS_REFERRAL_TO_TREATMENT = daysBetween(D.referral, D.surgery);
