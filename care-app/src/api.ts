// Typed client for the CancerFlow operational API (see documentation/backend/app_backend_routes.md).
// The care app never decides pathway logic itself: the backend validates every event against the pathway pack.

const envApi = import.meta.env.VITE_API as string | undefined;
export const API: string = (envApi || `${location.protocol}//${location.hostname}:8000`).replace(/\/$/, "");

// ------------------------------------------------------------------ types
export type Role = "doctor" | "patient";
export type Severity = "info" | "warning" | "critical";

export type User = {
  id: string; role: Role; display_name: string;
  patient_id: string | null; facility_id: string | null;
  staff_role: string | null; facility_name: string | null;
};

export type Stage = { id: string; label: string; level: string; capability: string | null; actors: string[] };

export type PathwaySummary = {
  id: string; patient_id: string; patient_name: string; pack_id: string; entry_route: string;
  status: "active" | "closed"; current_stage: string | null; current_stage_label: string | null;
  opened_at: string; closed_at: string | null; last_facility_id: string | null;
  open_alerts: number; max_alert_severity: Severity | null;
};

export type Alert = {
  id: string; rule_id: string; severity: Severity; status: "open" | "acknowledged" | "resolved";
  message: string; responsible_role: string | null; responsible_facility_id: string | null;
  due_at: string | null; evidence: Record<string, any>; raised_at: string; resolved_at: string | null;
  // present on /worklist and /alerts
  pathway_id?: string; patient_id?: string; patient_name?: string; current_stage?: string;
};

export type Target = { days: number; tag: string; source: string; note?: string };
export type Clock = {
  id: string; label: string; status: "not_started" | "running" | "met" | "missed" | "cancelled";
  severity: Severity | null; started_at: string | null; stopped_at: string | null; due_at: string | null;
  elapsed_days: number | null; targets: Target[];
};

export type PathwayEvent = {
  id: string; seq: number; type: string; stage: string | null; stage_label: string | null;
  facility_id: string | null; actor_user_id: string | null; actor_role: string | null;
  occurred_at: string; recorded_at: string; payload: Record<string, any>; tag: string;
};

export type Appointment = { id: string; stage: string; facility_id: string | null; scheduled_for: string; status: string; confirmed_at?: string | null };

/** Shared operations layer (/ops): simulated notifications for patients / units. */
export type OpsNotification = { id: string; audience: string; severity: "info" | "warning" | "critical"; kind: string; title: string; body: string;
  payload: Record<string, any>; created_at: string; read_at: string | null; pathway_id: string | null };

export type Patient = {
  id: string; display_name: string; birth_year: number | null; sex: string | null; tag: string;
  lon?: number | null; lat?: number | null;
};

export type PathwayState = {
  id: string; patient: Patient; pack_id: string; pack_version: number; entry_route: string;
  status: "active" | "closed"; as_of: string;
  current_stage: Stage | null; allowed_next: Stage[];
  timeline: PathwayEvent[]; clocks: Clock[]; alerts: Alert[]; appointments: Appointment[];
};

export type Facility = { id: string; name: string; kind: string; region_id: string | null };

export type CapacityOption = {
  facility_id: string; name: string; county: string | null;
  utilisation: number | null; mean_wait_days: number | null; capacity_per_week: number | null; travel_min: number | null;
};
/** GET /care/pathways/{id}/capacity: twin data for this patient's next capacity-limited step. */
export type PathwayCapacity = {
  capability: string | null; reason?: string;
  stage?: { id: string; label: string }; steps_ahead?: number; booked?: boolean;
  current?: CapacityOption; alternatives?: CapacityOption[];
  suggestion?: { facility_id: string; wait_saving_days: number; extra_travel_min: number } | null;
};

export type NewEvent =
  | { type: "STAGE_ENTERED"; stage: string; occurred_at?: string; facility_id?: string; payload?: Record<string, any> }
  | { type: "NOTE"; payload: { text: string } }
  | { type: "RESULT_RECORDED"; payload: { kind: string; value: string } }
  | { type: "APPOINTMENT_BOOKED"; payload: { stage: string; scheduled_for: string; facility_id?: string } }
  | { type: "APPOINTMENT_STATUS"; payload: { appointment_id: string; status: "attended" | "cancelled" | "missed" } };

// ------------------------------------------------------------------ http
export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) { super(message); this.status = status; }
}

let currentUserId: string | null = null;
export function setApiUser(id: string | null) { currentUserId = id; }

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (currentUserId) headers["X-User-Id"] = currentUserId;
  let r: Response;
  try {
    r = await fetch(API + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw new ApiError(0, `Can't reach the CancerFlow server (${API}). Check that the backend is running.`);
  }
  if (!r.ok) {
    let detail = r.statusText || `HTTP ${r.status}`;
    try {
      const j = await r.json();
      if (j?.detail) detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch { /* non-JSON error body */ }
    throw new ApiError(r.status, detail);
  }
  return r.json() as Promise<T>;
}

/** UUID v4. crypto.randomUUID only exists on https/localhost, so fall back for phones on plain-http LAN. */
export function uuid(): string {
  const c: Crypto = globalThis.crypto;
  if (typeof c.randomUUID === "function") return c.randomUUID();
  const b = new Uint8Array(16);
  c.getRandomValues(b);
  b[6] = (b[6] & 0x0f) | 0x40; b[8] = (b[8] & 0x3f) | 0x80;
  const h = [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

export const api = {
  users: (role: Role) => req<User[]>("GET", `/auth/users?role=${role}`),
  login: (user_id: string) => req<{ user: User }>("POST", "/auth/login", { user_id }),
  me: () => req<User>("GET", "/me"),
  myPathways: () => req<PathwaySummary[]>("GET", "/me/pathways"),
  patientPathways: (patientId: string) => req<PathwaySummary[]>("GET", `/patients/${encodeURIComponent(patientId)}/pathways`),
  worklist: () => req<{ pathways: PathwaySummary[]; alerts: Alert[] }>("GET", "/worklist"),
  pathway: (id: string) => req<PathwayState>("GET", `/pathways/${id}`),
  capacity: (id: string) => req<PathwayCapacity>("GET", `/care/pathways/${id}/capacity`),
  /** `eventId` should be generated once per user action so retries stay idempotent. */
  postEvent: (pathwayId: string, ev: NewEvent, eventId: string = uuid()) =>
    req<{ result: { duplicate: boolean; event_id: string; seq: number }; state: PathwayState }>(
      "POST", `/pathways/${pathwayId}/events`, { id: eventId, ...ev }),
  /** All unresolved alerts network-wide (used to find my patients' alerts owned by other units). */
  openAlerts: () => req<Alert[]>("GET", "/alerts"),
  acknowledge: (alertId: string) => req<PathwayState>("POST", `/alerts/${alertId}/acknowledge`),
  createPatient: (p: { display_name: string; birth_year?: number; sex?: "F" | "M" | "X" }) =>
    req<Patient>("POST", "/patients", p),
  createPathway: (p: { patient_id: string; entry_route: "screening" | "symptomatic"; facility_id?: string }) =>
    req<PathwayState>("POST", "/pathways", { pack_id: "colorectal", ...p }),
  facilities: (capability?: string) =>
    req<Facility[]>("GET", `/facilities${capability ? `?capability=${encodeURIComponent(capability)}` : ""}`),
  pack: (id: string) => req<{ stages: Record<string, { label: string }>; universal_stages?: Record<string, { label: string }> }>(
    "GET", `/packs/${id}`),
  clock: () => req<{ now: string }>("GET", "/clock"),
  notifications: () => req<OpsNotification[]>("GET", "/ops/notifications"),
  confirmAppointment: (id: string) => req<{ confirmed: boolean }>("POST", `/ops/appointments/${id}/confirm`),
  authoriseFollowup: (ivId: string) => req<any>("POST", `/ops/followup/${ivId}/authorise`, { option: 0, actor: currentUserId || "nurse-pkks-scr" }),
  reportSymptoms: () => req<{ message: string }>("POST", "/ops/story/book_gp"),
  setClock: (offset_days: number) => req<{ now: string; rescanned_pathways: number }>("POST", "/admin/clock", { offset_days }),
};

// ------------------------------------------------------------------ live stream (WS /stream)
export type StreamMsg = { kind: string; pathway_id?: string; [k: string]: any };
type Listener = (m: StreamMsg) => void;
type StatusListener = (live: boolean) => void;

const listeners = new Set<Listener>();
const statusListeners = new Set<StatusListener>();
let ws: WebSocket | null = null;
let live = false;
let retry = 0;
let retryTimer: number | undefined;

function setLive(v: boolean) { live = v; statusListeners.forEach((f) => f(v)); }

function connect() {
  if (ws || listeners.size === 0) return;
  const url = API.replace(/^http/, "ws") + "/stream";
  let s: WebSocket;
  try { s = new WebSocket(url); } catch { scheduleReconnect(); return; }
  ws = s;
  // Handlers check `ws === s` so a socket closed during a quick unsubscribe/resubscribe can't clobber its successor.
  s.onopen = () => { if (ws === s) { retry = 0; setLive(true); } };
  s.onmessage = (e) => {
    if (ws !== s) return;
    try { const m = JSON.parse(e.data) as StreamMsg; listeners.forEach((f) => f(m)); } catch { /* ignore */ }
  };
  s.onclose = () => { if (ws === s) { ws = null; setLive(false); scheduleReconnect(); } };
  s.onerror = () => s.close();
}

function scheduleReconnect() {
  if (listeners.size === 0) return;
  window.clearTimeout(retryTimer);
  retryTimer = window.setTimeout(connect, Math.min(15000, 1000 * 2 ** retry++));
}

export function subscribe(fn: Listener): () => void {
  listeners.add(fn);
  connect();
  return () => {
    listeners.delete(fn);
    if (listeners.size === 0) {
      window.clearTimeout(retryTimer);
      const s = ws; ws = null; s?.close(); setLive(false);
    }
  };
}

export function onLiveStatus(fn: StatusListener): () => void {
  statusListeners.add(fn); fn(live);
  return () => { statusListeners.delete(fn); };
}
