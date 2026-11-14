import { FormEvent, useMemo, useState } from "react";
import { Alert, api, PathwaySummary } from "../api";
import { useAuth } from "../auth";
import { AlertCard, ErrorNote, Header, SevBadge, Spinner, Tag } from "../components";
import { daysBetween, go, plural, ROUTE_LABEL, sevRank, useLive } from "../lib";

type Filter = "all" | "alerts" | "critical";

/** Clinician worklist: alerts first, then every active pathway at the user's facility. */
export default function DoctorWorklist() {
  const { user } = useAuth();
  // My unit's alerts come from /worklist. "Waiting on other units" = alerts on MY patients' pathways that another
  // unit is responsible for: the handovers (e.g. referral sent, never received) where patients fall through the cracks.
  const wl = useLive(async () => {
    const [w, all] = await Promise.all([api.worklist(), api.openAlerts()]);
    const mine = new Set(w.pathways.map((p) => p.id));
    const others = all.filter((a) => a.pathway_id && mine.has(a.pathway_id) && a.responsible_facility_id !== user?.facility_id);
    return { ...w, others };
  }, [user?.id], (m) => ["event", "pathway_created", "clock", "alerts_rescanned"].includes(m.kind));
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [alertTab, setAlertTab] = useState<"mine" | "others">("mine");
  const [acking, setAcking] = useState<string | null>(null);
  const [ackErr, setAckErr] = useState<string | null>(null);
  const [showNew, setShowNew] = useState(false);

  const sortAlerts = (xs: Alert[]) => xs
    .filter((a) => a.status !== "resolved")
    .sort((a, b) => sevRank(b.severity) - sevRank(a.severity) || Number(a.status === "acknowledged") - Number(b.status === "acknowledged")
      || a.raised_at.localeCompare(b.raised_at));
  const myAlerts = useMemo(() => sortAlerts(wl.data?.alerts || []), [wl.data]);
  const otherAlerts = useMemo(() => sortAlerts(wl.data?.others || []), [wl.data]);
  const alerts = myAlerts;
  const shownAlerts = alertTab === "mine" ? myAlerts : otherAlerts;

  const pathways = useMemo(() => {
    const ql = q.trim().toLowerCase();
    return (wl.data?.pathways || []).filter((p) =>
      (!ql || `${p.patient_name} ${p.patient_id} ${p.current_stage_label}`.toLowerCase().includes(ql)) &&
      (filter === "all" || (filter === "alerts" ? p.open_alerts > 0 : p.max_alert_severity === "critical")));
  }, [wl.data, q, filter]);

  const clock = useLive(() => api.clock(), [], (m) => m.kind === "clock");
  const now = clock.data?.now ?? new Date().toISOString(); // server demo clock, so "open for" grows on fast-forward
  const nCrit = alerts.filter((a) => a.severity === "critical").length;
  const nWarn = alerts.filter((a) => a.severity === "warning").length;

  const ack = async (id: string) => {
    setAcking(id); setAckErr(null);
    try { await api.acknowledge(id); await wl.reload(); } catch (e: any) { setAckErr(e.message); } finally { setAcking(null); }
  };

  return (
    <div className="page doctor">
      <Header subtitle={user?.facility_name || "Care continuity"} />
      <main className="container">
        <div className="page-h">
          <div>
            <h1>Worklist</h1>
            <p className="muted">{user?.display_name}{user?.staff_role ? ` · ${user.staff_role.replace(/_/g, " ")}` : ""}</p>
          </div>
          <button className="btn primary" onClick={() => setShowNew(true)}>+ New pathway</button>
        </div>

        {wl.error && <ErrorNote msg={wl.error} onRetry={wl.reload} />}
        {!wl.data && !wl.error && <div className="center pad"><Spinner /></div>}

        {wl.data && (
          <>
            <div className="stats">
              <Stat label="Critical alerts" value={nCrit} tone={nCrit ? "bad" : ""} />
              <Stat label="Warnings" value={nWarn} tone={nWarn ? "warn" : ""} />
              <Stat label="Waiting on other units" value={otherAlerts.length} tone={otherAlerts.length ? "warn" : ""} />
              <Stat label="Active pathways" value={wl.data.pathways.length} />
            </div>

            <div className="wl-grid">
              <section className="panel">
                <div className="panel-h">
                  <h3>Needs attention</h3>
                  <div className="seg small right">
                    <button className={alertTab === "mine" ? "on" : ""} onClick={() => setAlertTab("mine")}>My unit ({myAlerts.length})</button>
                    <button className={alertTab === "others" ? "on" : ""} onClick={() => setAlertTab("others")}>Waiting on others ({otherAlerts.length})</button>
                  </div>
                </div>
                <div className="panel-b alerts-col">
                  <p className="muted small tab-hint">
                    {alertTab === "mine"
                      ? "Overdue steps your unit is responsible for."
                      : "Your patients, stuck at another unit. Chase the handover before it becomes a gap."}
                  </p>
                  {ackErr && <ErrorNote msg={ackErr} />}
                  {shownAlerts.length === 0 && (
                    <p className="muted">{alertTab === "mine" ? "Nothing overdue for your unit." : "No patient of yours is waiting on another unit."}</p>
                  )}
                  {shownAlerts.map((a) => (
                    <AlertCard key={a.id} a={a} now={now} busy={acking === a.id}
                               onOpen={a.pathway_id ? () => go(`/p/${a.pathway_id}`) : undefined}
                               onAck={alertTab === "mine" ? () => ack(a.id) : undefined} />
                  ))}
                </div>
              </section>

              <section className="panel">
                <div className="panel-h">
                  <h3>Active pathways</h3><span className="ctx">{pathways.length} shown</span>
                </div>
                <div className="toolbar">
                  <input className="input" placeholder="Search patient or stage…" value={q} onChange={(e) => setQ(e.target.value)} />
                  <div className="seg small">
                    {(["all", "alerts", "critical"] as Filter[]).map((f) => (
                      <button key={f} className={filter === f ? "on" : ""} onClick={() => setFilter(f)}>
                        {f === "all" ? "All" : f === "alerts" ? "With alerts" : "Critical"}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="panel-b">
                  <table className="ptable">
                    <thead><tr><th>Patient</th><th>Current stage</th><th>Route</th><th className="r">Open for</th><th>Alerts</th></tr></thead>
                    <tbody>
                      {pathways.map((p) => <Row key={p.id} p={p} now={now} />)}
                    </tbody>
                  </table>
                  {pathways.length === 0 && <p className="muted pad">No pathways match.</p>}
                </div>
              </section>
            </div>
            <p className="footnote muted small">Patients, events and alerts are <Tag t="SYNTHETIC" /> demo data. Dwell thresholds are tagged <Tag t="HYPOTHESIS" />.</p>
          </>
        )}
      </main>
      {showNew && <NewPathway onClose={() => setShowNew(false)} facilityId={user?.facility_id ?? undefined} />}
    </div>
  );
}

function Stat({ label, value, tone = "" }: { label: string; value: number; tone?: string }) {
  return <div className={`stat ${tone}`}><span className="num">{value}</span><small>{label}</small></div>;
}

function Row({ p, now }: { p: PathwaySummary; now: string }) {
  const open = () => go(`/p/${p.id}`);
  return (
    <tr className={`clickable sev-row-${p.max_alert_severity ?? "none"}`} onClick={open} tabIndex={0}
        onKeyDown={(e) => { if (e.key === "Enter") open(); }}>
      <td data-l="Patient"><b>{p.patient_name}</b><div className="muted small mono">{p.patient_id}</div></td>
      <td data-l="Stage">{p.current_stage_label}</td>
      <td data-l="Route" className="muted">{ROUTE_LABEL[p.entry_route] || p.entry_route}</td>
      <td data-l="Open for" className="r num">{Math.max(0, Math.round(daysBetween(p.opened_at, now)))} d</td>
      <td data-l="Alerts">{p.open_alerts > 0 ? <><SevBadge s={p.max_alert_severity} /> <span className="num">{p.open_alerts}</span></> : <span className="muted">–</span>}</td>
    </tr>
  );
}

/** Register a new patient and open a colorectal pathway for them at this facility. */
function NewPathway({ onClose, facilityId }: { onClose: () => void; facilityId?: string }) {
  const [name, setName] = useState("");
  const [year, setYear] = useState("");
  const [sex, setSex] = useState<"" | "F" | "M" | "X">("");
  const [route, setRoute] = useState<"symptomatic" | "screening">("symptomatic");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr(null);
    try {
      const yr = year ? Number(year) : undefined;
      const pat = await api.createPatient({ display_name: name.trim(), birth_year: yr, sex: sex || undefined });
      const pw = await api.createPathway({ patient_id: pat.id, entry_route: route, facility_id: facilityId });
      go(`/p/${pw.id}`);
    } catch (e: any) { setErr(e.message); setBusy(false); }
  };

  return (
    <div className="modal-back" onClick={onClose}>
      <form className="modal" onClick={(e) => e.stopPropagation()} onSubmit={submit}>
        <h3>New colorectal pathway</h3>
        <p className="muted small">Creates a <Tag t="SYNTHETIC" /> patient record and opens a pathway at your unit.</p>
        <label>Name<input className="input" required value={name} onChange={(e) => setName(e.target.value)} autoFocus /></label>
        <div className="row">
          <label className="grow">Birth year<input className="input" type="number" min={1900} max={2026} value={year} onChange={(e) => setYear(e.target.value)} /></label>
          <label className="grow">Sex
            <select className="input" value={sex} onChange={(e) => setSex(e.target.value as any)}>
              <option value="">–</option><option value="F">Female</option><option value="M">Male</option><option value="X">Other</option>
            </select>
          </label>
        </div>
        <label>Entry route
          <select className="input" value={route} onChange={(e) => setRoute(e.target.value as any)}>
            <option value="symptomatic">Symptoms → GP</option><option value="screening">Screening programme (FIT)</option>
          </select>
        </label>
        {err && <ErrorNote msg={err} />}
        <div className="row end">
          <button type="button" className="btn ghost" onClick={onClose}>Cancel</button>
          <button className="btn primary" disabled={busy || !name.trim()}>{busy ? "Creating…" : "Create pathway"}</button>
        </div>
      </form>
    </div>
  );
}
