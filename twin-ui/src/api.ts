// Typed client for the CancerFlow /twin/* API. The frontend never computes predictions; it only displays these.
export const API: string = (import.meta.env.VITE_API as string) || "http://localhost:8000";

export type Metric = { mean: number; ci: [number | null, number | null]; tag?: string };
export type MetricMap = Record<string, Metric>;

export type Indicator = { value: number; year: number; tag: string; source: string };
export type CountyNode = {
  id: string; name: string; yta: string; population: number; lon: number; lat: number;
  indicators: Record<string, Indicator>; sim: MetricMap;
  model: { m_primary_care: number; m_specialist: number; capacity_factor: number; tag: string };
};
export type FacilityCap = { capability: string; capacity_per_week: number; capacity_tag: string; sim: MetricMap };
export type FacilityNode = {
  id: string; name: string; kind: string; lon: number; lat: number; county: string; yta: string;
  capabilities: FacilityCap[];
  live: { active_pathways: number; open_alerts: number; critical_alerts: number; tag: string };
};
export type Flow = { from_county: string; to_facility: string; capability: string; flow_per_week: number; cross_county: boolean };
export type Network = {
  capability: string; run_id: string;
  national: { indicators: Record<string, Indicator>; sim: MetricMap };
  counties: CountyNode[]; facilities: FacilityNode[]; flows: Flow[];
};
export type Scenario = { id: string; name: string; description: string; is_baseline: boolean; latest_run: string | null; created_at: string };

export type Compliance = {
  p_within_target: number | null; ci: [number | null, number | null] | null; cohort_n_per_year: number | null;
  exceeding_per_year: number | null; fit_to_colonoscopy_p_within: number | null; target_days: number; target_tag: string;
};
export type Delays = {
  median_days: number | null; p90_days: number | null; median_ci: [number, number] | null;
  fit_to_colonoscopy_median_days: number | null; stall_days_mean: number | null; stalls_per_year: number | null;
  stages: { stage: string; label: string; mean_days: number }[];
  rtt_histogram: { bin: number; share: number }[];
};
export type Station = {
  facility_id: string; facility: string; capability: string; capability_label: string; utilisation: number | null;
  mean_wait_days: number | null; p90_wait_days: number | null; capacity_per_week: number | null; throughput_per_week: number | null;
};
export type Recommendation = {
  id: string; title: string; action: string; rationale: string; params: any;
  controls: { path: string; label: string; min: number; max: number; step: number; value: number }[];
};
export type Context = {
  context: { scope: Scope; id: string; name: string; county: string | null; county_name?: string; kind?: string };
  run_id: string; base_run_id: string | null;
  compliance: Compliance; delays: Delays; bottlenecks: Station[];
  observed: Record<string, Indicator>;
  live: { active_pathways: number; open_alerts: number; critical_alerts: number };
  primary_issue: { headline: string; top_station: Station | null };
  ranking: { county_id: string; name: string; p_within_target: number; ci: [number, number]; median_days: number }[] | null;
  national_compliance: Compliance;
  base?: { compliance: Compliance; delays: Delays; bottlenecks: Station[]; national_compliance: Compliance };
  recommendations: Recommendation[];
};
export type Summary = { mode: "llm" | "fallback"; text: string; facts: any; rejected: { reason: string; numbers: string[] } | null; model?: string };
export type Interpretation = {
  mode: "llm" | "fallback"; params: any; actions: { type: string; label: string }[]; errors: string[];
  unsupported: string[]; explanation: string; assumptions: Record<string, number>; simulatable: boolean;
};
export type SimulateResult = { scenario_id: string; run_id: string; base_run_id: string; seconds: number; headline: Record<string, any> };

export type Scope = "national" | "county" | "facility";
export type Selection = { scope: Scope; id?: string };

async function j<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(API + path, { ...init, headers: { "Content-Type": "application/json", ...(init?.headers || {}) } });
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail ?? detail; } catch { /* ignore */ }
    throw new Error(`${r.status}: ${detail}`);
  }
  return r.json();
}

const q = (o: Record<string, string | undefined | null>) =>
  "?" + Object.entries(o).filter(([, v]) => v != null && v !== "").map(([k, v]) => `${k}=${encodeURIComponent(v as string)}`).join("&");

export const api = {
  geo: () => j<any>("/twin/geo/counties"),
  healthCentres: () => j<any[]>("/facilities?kind=health_center"),
  network: (run: string, capability: string) => j<Network>("/twin/network" + q({ run_id: run, capability })),
  scenarios: () => j<Scenario[]>("/twin/scenarios"),
  context: (sel: Selection, run: string, base?: string | null) =>
    j<Context>("/twin/context" + q({ scope: sel.scope, id: sel.id, run_id: run, base_run_id: base ?? undefined })),
  summary: (sel: Selection, run: string, base?: string | null) =>
    j<Summary>("/twin/summary", { method: "POST", body: JSON.stringify({ scope: sel.scope, id: sel.id, run_id: run, base_run_id: base }) }),
  interpret: (text: string, sel: Selection) =>
    j<Interpretation>("/twin/interpret", { method: "POST", body: JSON.stringify({ text, scope: sel.scope, id: sel.id }) }),
  simulate: (name: string, params: any, reps = 4) =>
    j<SimulateResult>("/twin/simulate", { method: "POST", body: JSON.stringify({ name, params, reps }) }),
  assumptions: () => j<any>("/twin/assumptions"),
};

// ---------------------------------------------------------------- helpers
export const pct = (x: number | null | undefined, d = 1) => (x == null ? "–" : `${(100 * x).toFixed(d)}%`);
export const num = (x: number | null | undefined, d = 1) => (x == null ? "–" : x.toFixed(d));
export const shortCounty = (n: string) => n.replace(" wellbeing services county", "").replace("City of ", "");
export const CAP_LABEL: Record<string, string> = {
  colonoscopy: "Colonoscopy", ct_imaging: "CT staging", mdt_crc: "MDT", crc_surgery: "Surgery",
  oncology: "Oncology", radiotherapy: "Radiotherapy", pathology: "Pathology",
};

export function mergeParams(list: any[]): any {
  const out: any = {};
  for (const p of list) {
    if (!p) continue;
    if (p.capacity) out.capacity = [...(out.capacity || []), ...p.capacity];
    if (p.routing) out.routing = [...(out.routing || []), ...p.routing];
    if (p.routing_set) out.routing_set = { ...(out.routing_set || {}), ...p.routing_set };
    if (p.screening_factor) out.screening_factor = { ...(out.screening_factor || {}), ...p.screening_factor };
    if (p.monitoring) out.monitoring = true;
  }
  return out;
}
