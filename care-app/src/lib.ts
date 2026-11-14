import { useCallback, useEffect, useRef, useState } from "react";
import { api, subscribe, StreamMsg } from "./api";

// ------------------------------------------------------------------ stage labels from the pathway pack
let labelCache: Promise<Record<string, string>> | null = null;
function loadLabels() {
  labelCache ??= api.pack("colorectal")
    .then((p) => {
      const out: Record<string, string> = {};
      for (const [id, s] of Object.entries({ ...(p.universal_stages || {}), ...(p.stages || {}) })) out[id] = (s as any).label;
      return out;
    })
    .catch(() => { labelCache = null; return {}; });
  return labelCache;
}
/** Returns a lookup from stage id to its label in the pack (falls back to a prettified id). */
export function useStageLabel(): (id: string) => string {
  const [labels, setLabels] = useState<Record<string, string>>({});
  useEffect(() => { loadLabels().then(setLabels); }, []);
  return (id: string) => labels[id] || (id ? id.charAt(0) + id.slice(1).toLowerCase().replace(/_/g, " ") : "–");
}

// ------------------------------------------------------------------ facility names (for "who is responsible")
let facCache: Promise<Record<string, string>> | null = null;
function loadFacilities() {
  facCache ??= api.facilities()
    .then((fs) => Object.fromEntries(fs.map((f) => [f.id, f.name])))
    .catch(() => { facCache = null; return {}; });
  return facCache;
}
/** Lookup from facility id to its name, e.g. "fi-hosp-pkks" → "North Karelia Central Hospital (Joensuu)". */
export function useFacilityName(): (id: string | null | undefined) => string | null {
  const [names, setNames] = useState<Record<string, string>>({});
  useEffect(() => { loadFacilities().then(setNames); }, []);
  return (id) => (id ? names[id] ?? null : null);
}

/** Who an alert's `responsible_role` refers to: clinician wording, and the softer patient wording. */
export const ROLE_LABEL: Record<string, { staff: string; patient: string }> = {
  gp: { staff: "GP", patient: "your GP" },
  screening_nurse: { staff: "Screening nurse", patient: "your screening nurse" },
  specialist: { staff: "Specialist", patient: "your specialist" },
  endoscopy_unit: { staff: "Endoscopy unit", patient: "the endoscopy unit" },
  pathology: { staff: "Pathology lab", patient: "the pathology laboratory" },
  radiology: { staff: "Radiology", patient: "the imaging (radiology) department" },
  mdt: { staff: "Cancer team (MDT)", patient: "your cancer care team" },
  surgery: { staff: "Surgery team", patient: "your surgical team" },
  oncology: { staff: "Oncology", patient: "the oncology (cancer treatment) team" },
};
export function roleLabel(role: string | null | undefined, who: "staff" | "patient"): string {
  if (!role) return who === "staff" ? "Care team" : "your care team";
  return ROLE_LABEL[role]?.[who] ?? (who === "staff" ? role.replace(/_/g, " ") : `the ${role.replace(/_/g, " ")} team`);
}

// ------------------------------------------------------------------ hash routing (works on any static host)
export function useRoute(): string[] {
  const [hash, setHash] = useState(location.hash);
  useEffect(() => {
    const f = () => setHash(location.hash);
    window.addEventListener("hashchange", f);
    return () => window.removeEventListener("hashchange", f);
  }, []);
  return hash.replace(/^#\/?/, "").split("/").filter(Boolean);
}
export const go = (path: string) => { location.hash = path.startsWith("/") ? path : `/${path}`; };

// ------------------------------------------------------------------ data loading with live refresh
/**
 * Loads `fetcher()` and reloads it whenever a /stream message passes `shouldReload`.
 * Keeps the previous data visible while reloading, so live updates don't flash.
 */
export function useLive<T>(fetcher: () => Promise<T>, deps: unknown[], shouldReload: (m: StreamMsg) => boolean) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const seq = useRef(0);
  const fetchRef = useRef(fetcher); fetchRef.current = fetcher;
  const filterRef = useRef(shouldReload); filterRef.current = shouldReload;

  const reload = useCallback(async () => {
    const my = ++seq.current;
    setLoading(true);
    try {
      const d = await fetchRef.current();
      if (my === seq.current) { setData(d); setError(null); }
    } catch (e: any) {
      if (my === seq.current) setError(e?.message || String(e));
    } finally {
      if (my === seq.current) setLoading(false);
    }
  }, []);

  useEffect(() => { setData(null); reload(); }, deps); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    let t: number | undefined;
    const unsub = subscribe((m) => {
      if (!filterRef.current(m)) return;
      window.clearTimeout(t);
      t = window.setTimeout(reload, 150); // coalesce bursts (e.g. clock rescans)
    });
    return () => { unsub(); window.clearTimeout(t); };
  }, [reload]);

  return { data, setData, error, loading, reload };
}

// ------------------------------------------------------------------ dates
const DAY = 86_400_000;
export const daysBetween = (a: string | Date, b: string | Date) =>
  (new Date(b).getTime() - new Date(a).getTime()) / DAY;

export function fmtDate(iso: string | null | undefined, withTime = false): string {
  if (!iso) return "–";
  const d = new Date(iso);
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) +
    (withTime ? " " + d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }) : "");
}

export function fmtDay(iso: string): string {
  return new Date(iso).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
}

/** "today", "3 days ago", "in 5 days", relative to `now` (the server's demo clock). */
export function relDays(iso: string | null | undefined, now: string): string {
  if (!iso) return "";
  const d = Math.round(daysBetween(now, iso));
  if (d === 0) return "today";
  if (d === 1) return "tomorrow";
  if (d === -1) return "yesterday";
  return d > 0 ? `in ${d} days` : `${-d} days ago`;
}

/** Value for <input type="datetime-local"> in the browser's local time. */
export function toLocalInput(iso: string): string {
  const d = new Date(iso);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}
export const fromLocalInput = (v: string) => new Date(v).toISOString();

export const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? "" : "s"}`;

// ------------------------------------------------------------------ patient-facing language
/** Plain-English explanation of each colorectal stage, shown to patients. */
export const STAGE_EXPLAIN: Record<string, string> = {
  SCREENING_INVITED: "You've been invited to bowel cancer screening. Do the home stool test (FIT) and send the sample back.",
  SCREENING_NO_RESPONSE: "We didn't receive your screening sample. You can contact your clinic if you'd still like to take part.",
  FIT_RETURNED: "Your stool test sample has arrived and is being analysed in the laboratory.",
  FIT_NEGATIVE: "Your stool test was normal. You'll be invited again in the next screening round.",
  FIT_POSITIVE: "Your stool test found traces of blood. This is often caused by something other than cancer, but you'll be offered a follow-up examination to find out.",
  NURSE_ASSESSMENT: "A screening nurse will contact you to talk through your result and arrange the next examination.",
  SYMPTOMS_REPORTED: "You've reported symptoms. The next step is an appointment with your GP.",
  GP_VISIT: "Your GP has assessed you and decides whether you need tests at the hospital.",
  REFERRAL_SENT: "Your GP has sent a referral to the hospital. The hospital will confirm it and contact you.",
  REFERRAL_RECEIVED: "The hospital has received your referral and is arranging your examination.",
  SPECIALIST_ASSESSMENT: "A specialist is reviewing your case to choose the right examination.",
  COLONOSCOPY: "A colonoscopy is a camera examination of the bowel. Your care team will give you preparation instructions.",
  ALT_EXAMINATION: "You're having an alternative examination of the bowel, such as a CT scan.",
  HISTOLOGY: "Tissue samples from your examination are being analysed. Your care team will contact you with the results.",
  CANCER_CONFIRMED: "The tests show cancer. Your care team will now arrange scans to plan the best treatment for you.",
  STAGING: "You're having scans (CT, and MRI if needed) so the team can see exactly where the cancer is.",
  MDT_DECISION: "A team of specialists is meeting to agree on the best treatment plan for you.",
  NEOADJUVANT_THERAPY: "You're receiving radiotherapy and/or chemotherapy before surgery.",
  SURGERY: "Surgery is part of your treatment plan.",
  SYSTEMIC_THERAPY: "You're receiving medicine-based treatment, such as chemotherapy.",
  ADJUVANT_THERAPY: "You're receiving chemotherapy after surgery to lower the risk of the cancer coming back.",
  PALLIATIVE_CARE: "Your care focuses on comfort and quality of life.",
  FOLLOW_UP: "Your treatment is complete. You'll be invited to regular check-ups.",
  POLYP_SURVEILLANCE: "Polyps were removed. You'll be invited to check-ups to watch for new ones.",
  CANCER_EXCLUDED: "Good news: the tests found no cancer.",
  DECLINED: "You chose not to continue with further examinations. You can contact your clinic at any time if you change your mind.",
  WITHDRAWN: "Your care has moved elsewhere.",
  DECEASED: "This care pathway has been closed.",
};

/** Patient-facing wording for the actions a patient may record themselves. */
export const PATIENT_ACTION: Record<string, { label: string; hint: string; confirm?: string }> = {
  FIT_RETURNED: { label: "I've sent my stool sample", hint: "Let your care team know the test is on its way." },
  DECLINED: {
    label: "I don't want further examinations", hint: "Your care team will be informed.",
    confirm: "Are you sure? Your care team will be informed and this pathway will close. You can contact your clinic if you change your mind.",
  },
};

/** Journey phases shown as a stepper to patients. */
export const PHASES: { label: string; stages: string[] }[] = [
  { label: "First steps", stages: ["SCREENING_INVITED", "FIT_RETURNED", "FIT_POSITIVE", "SYMPTOMS_REPORTED", "GP_VISIT", "NURSE_ASSESSMENT"] },
  { label: "Referral", stages: ["REFERRAL_SENT", "REFERRAL_RECEIVED", "SPECIALIST_ASSESSMENT"] },
  { label: "Examination", stages: ["COLONOSCOPY", "ALT_EXAMINATION", "HISTOLOGY"] },
  { label: "Diagnosis & plan", stages: ["CANCER_CONFIRMED", "STAGING", "MDT_DECISION"] },
  { label: "Treatment", stages: ["NEOADJUVANT_THERAPY", "SURGERY", "SYSTEMIC_THERAPY", "ADJUVANT_THERAPY", "PALLIATIVE_CARE"] },
  { label: "Follow-up", stages: ["FOLLOW_UP", "POLYP_SURVEILLANCE", "CANCER_EXCLUDED", "FIT_NEGATIVE"] },
];
export const phaseOf = (stage: string | null | undefined) =>
  stage ? PHASES.findIndex((p) => p.stages.includes(stage)) : -1;

/** Exits that end a pathway; doctors get them behind a confirmation, separate from the normal next steps. */
export const EXIT_STAGES = new Set(["DECEASED", "WITHDRAWN", "DECLINED", "SCREENING_NO_RESPONSE"]);

export const ROUTE_LABEL: Record<string, string> = { screening: "Screening (FIT)", symptomatic: "Symptoms → GP" };

export const sevRank = (s: string | null | undefined) => (s === "critical" ? 3 : s === "warning" ? 2 : s === "info" ? 1 : 0);
