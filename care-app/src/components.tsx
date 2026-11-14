import { ReactNode, useEffect, useState } from "react";
import { api, Alert, Clock, onLiveStatus, PathwayEvent, Severity } from "./api";
import { useAuth } from "./auth";
import { daysBetween, fmtDate, plural, relDays, roleLabel, useFacilityName } from "./lib";

// ------------------------------------------------------------------ small pieces
export function Tag({ t, title }: { t: string; title?: string }) {
  return <span className={`tag ${t}`} title={title || TAG_HELP[t] || t}>{t.replace("_", " ")}</span>;
}
const TAG_HELP: Record<string, string> = {
  SYNTHETIC: "Generated demo data, not a real person or event",
  REFERENCE: "Official target or norm",
  EXTERNAL_PROXY: "Value from another country used as a stand-in",
  HYPOTHESIS: "Working assumption, not established",
  OBSERVED: "Published statistic",
};

export function SevBadge({ s }: { s: Severity | null | undefined }) {
  if (!s) return null;
  return <span className={`sev ${s}`}>{s}</span>;
}

export function Spinner() { return <span className="spinner" aria-label="Loading" />; }

export function ErrorNote({ msg, onRetry }: { msg: string; onRetry?: () => void }) {
  return (
    <div className="error-note" role="alert">
      <span>{msg}</span>
      {onRetry && <button className="btn small" onClick={onRetry}>Try again</button>}
    </div>
  );
}

export function LiveDot() {
  const [live, setLive] = useState(false);
  useEffect(() => onLiveStatus(setLive), []);
  return (
    <span className={`live ${live ? "on" : ""}`} title={live ? "Live updates connected" : "Live updates reconnecting…"}>
      <i />{live ? "Live" : "Offline"}
    </span>
  );
}

// ------------------------------------------------------------------ header + demo clock
export function Header({ subtitle, children }: { subtitle?: string; children?: ReactNode }) {
  const { user, logout } = useAuth();
  return (
    <header className="topbar">
      <div className="brand"><span className="dot" />CancerFlow <small>{subtitle}</small></div>
      <div className="spacer" />
      {children}
      <LiveDot />
      <DemoClock />
      {user && (
        <div className="who">
          <span className="who-name">{user.display_name}</span>
          <button className="btn ghost small" onClick={logout}>Log out</button>
        </div>
      )}
    </header>
  );
}

/** Demo clock: fast-forward server time so overdue alerts appear. This is GLOBAL for everyone on the backend. */
export function DemoClock() {
  const [open, setOpen] = useState(false);
  const [now, setNow] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const refresh = () => api.clock().then((c) => setNow(c.now)).catch(() => {});
  useEffect(() => { refresh(); }, []);
  const offset = now ? Math.round(daysBetween(new Date(), now)) : 0;

  const set = async (days: number) => {
    setBusy(true); setErr(null);
    try { const r = await api.setClock(days); setNow(r.now); }
    catch (e: any) { setErr(e.message); }
    finally { setBusy(false); }
  };

  return (
    <div className="democlock">
      <button className={`btn ghost small ${offset ? "warped" : ""}`} onClick={() => { setOpen(!open); refresh(); }}
              aria-expanded={open} title="Demo clock">
        🕒 {now ? fmtDate(now) : "…"}{offset ? ` (+${offset} d)` : ""}
      </button>
      {open && (
        <div className="popover" role="dialog">
          <b>Demo clock</b>
          <p className="muted">Moves the server's "now" forward so waiting times grow and alerts appear. It affects everyone using this backend.</p>
          <div className="row">
            {[7, 14, 21].map((d) => (
              <button key={d} className="btn small" disabled={busy} onClick={() => set(d)}>+{d} days</button>
            ))}
            <button className="btn small ghost" disabled={busy || offset === 0} onClick={() => set(0)}>Reset to real time</button>
          </div>
          {err && <ErrorNote msg={err} />}
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ waiting-time clocks
/** Progress of one target clock against its first (REFERENCE) target. */
export function ClockBar({ c, now, plain = false }: { c: Clock; now: string; plain?: boolean }) {
  const target = c.targets[0];
  const elapsed = c.elapsed_days ?? (c.started_at ? daysBetween(c.started_at, c.stopped_at || now) : null);
  const pct = target && elapsed != null ? Math.min(100, (elapsed / target.days) * 100) : 0;
  const left = target && elapsed != null ? Math.round(target.days - elapsed) : null;
  const tone = c.status === "missed" || c.severity === "critical" ? "bad" : c.severity === "warning" ? "warn"
    : c.status === "met" ? "good" : "ok";

  let status: string;
  if (c.status === "not_started") status = plain ? "Not started yet" : "Not started";
  else if (c.status === "cancelled") status = "Not applicable";
  else if (c.status === "met") status = `Done in ${Math.round(elapsed ?? 0)} days`;
  else if (c.status === "missed") status = `Took ${Math.round(elapsed ?? 0)} days`;
  else if (left != null) status = left >= 0 ? `Day ${Math.round(elapsed!)} · ${left} days left` : `Day ${Math.round(elapsed!)} · ${-left} days over`;
  else status = "Running";

  return (
    <div className={`clock ${tone}`}>
      <div className="clock-top">
        <span className="clock-label">{c.label}</span>
        <span className="clock-status num">{status}</span>
      </div>
      <div className="bar"><div style={{ width: `${c.status === "not_started" ? 0 : pct}%` }} /></div>
      <div className="clock-targets">
        {c.targets.map((t, i) => (
          <span key={i} title={t.note}>
            Target {t.days} d <Tag t={t.tag} title={`${t.source}${t.note ? ": " + t.note : ""}`} />
          </span>
        ))}
        {c.due_at && c.status === "running" && <span className="muted">due {fmtDate(c.due_at)} ({relDays(c.due_at, now)})</span>}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ alerts
/** "Screening nurse · North Karelia Central Hospital · overdue by 12 days": who must act, where, and by when. */
export function Responsible({ a, now }: { a: Alert; now?: string }) {
  const { user } = useAuth();
  const facName = useFacilityName();
  const where = facName(a.responsible_facility_id) || a.responsible_facility_id;
  const mine = !!a.responsible_facility_id && a.responsible_facility_id === user?.facility_id;
  const days = a.due_at && now ? Math.floor(daysBetween(a.due_at, now)) : null;
  return (
    <div className={`resp ${mine ? "mine" : "other"}`}>
      <span className="resp-ico" aria-hidden>👤</span>
      <span>
        <b>{roleLabel(a.responsible_role, "staff")}</b>
        {where && <> · {where}</>}
        {mine && <span className="resp-you">your unit</span>}
        {days != null && (
          <span className={days > 0 ? "resp-late" : "muted"}>
            {" · "}{days > 0 ? `overdue by ${plural(days, "day")}` : days === 0 ? "due today" : `due in ${plural(-days, "day")}`}
          </span>
        )}
      </span>
    </div>
  );
}

// Backend alert text carries provenance like "(REFERENCE, FI-1)" or "(threshold 5 d, HYPOTHESIS)".
// That stays in the data and the pack; on screen it's clutter, so strip it.
const TAGS = "REFERENCE|HYPOTHESIS|EXTERNAL_PROXY|OBSERVED|DERIVED|CALIBRATED|SIMULATED|SYNTHETIC";
export function cleanAlertMessage(m: string): string {
  return m
    .replace(new RegExp(`\\s*\\((?:${TAGS})(?:,\\s*[A-Z0-9_-]+)*\\)`, "g"), "")   // "(REFERENCE, FI-1)" → ""
    .replace(new RegExp(`,\\s*(?:${TAGS})(?:,\\s*[A-Z0-9_-]+)*\\)`, "g"), ")")      // "(threshold 5 d, HYPOTHESIS)" → "(threshold 5 d)"
    .replace(/\s+\./g, ".");
}

export function AlertCard({ a, onAck, onOpen, busy, now }: {
  a: Alert; onAck?: () => void; onOpen?: () => void; busy?: boolean; now?: string;
}) {
  return (
    <div className={`alert ${a.severity} ${a.status}`}>
      <div className="alert-top">
        <SevBadge s={a.severity} />
        {a.patient_name && <b className="alert-patient">{a.patient_name}</b>}
        {a.status === "acknowledged" && <span className="muted small">acknowledged</span>}
        <span className="muted small right">raised {fmtDate(a.raised_at)}</span>
      </div>
      <div className="alert-msg">{cleanAlertMessage(a.message)}</div>
      <Responsible a={a} now={now} />
      {(onOpen || (onAck && a.status === "open")) && (
        <div className="row">
          {onOpen && <button className="btn small" onClick={onOpen}>Open pathway</button>}
          {onAck && a.status === "open" && (
            <button className="btn small ghost" disabled={busy} onClick={onAck}>Acknowledge</button>
          )}
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ timeline
export function describeEvent(e: PathwayEvent, stageLabel: (s: string) => string): { title: string; detail?: string } {
  const p = e.payload || {};
  switch (e.type) {
    case "STAGE_ENTERED": return { title: e.stage_label || e.stage || "Stage", detail: p.note };
    case "NOTE": return { title: e.actor_role === "patient" ? "Message from patient" : "Note", detail: p.text };
    case "RESULT_RECORDED": return { title: `${p.kind ?? "Test"} result: ${p.value ?? "–"}` };
    case "APPOINTMENT_BOOKED":
      return { title: `Appointment booked: ${stageLabel(p.stage)}`, detail: p.scheduled_for ? fmtDate(p.scheduled_for, true) : undefined };
    case "APPOINTMENT_STATUS": return { title: `Appointment ${p.status ?? "updated"}` };
    case "ALERT_ACKNOWLEDGED": return { title: "Alert acknowledged" };
    default: return { title: e.type };
  }
}

export function Timeline({ events, stageLabel, now }: { events: PathwayEvent[]; stageLabel: (s: string) => string; now: string }) {
  const sorted = [...events].sort((a, b) => b.occurred_at.localeCompare(a.occurred_at) || b.seq - a.seq);
  return (
    <ol className="timeline">
      {sorted.map((e) => {
        const d = describeEvent(e, stageLabel);
        return (
          <li key={e.id} className={`t-${e.type}`}>
            <span className="t-dot" />
            <div className="t-body">
              <div className="t-title">{d.title}</div>
              {d.detail && <div className="t-detail">{d.detail}</div>}
              <div className="t-meta">
                {fmtDate(e.occurred_at)} · {relDays(e.occurred_at, now)}
                {e.actor_role && <> · by {e.actor_role}</>}
              </div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
