import { useState } from "react";
import { api, OpsNotification, PathwayState, uuid } from "../api";
import { useAuth } from "../auth";
import { ClockBar, ErrorNote, Header, Spinner, Tag, Timeline } from "../components";
import { fmtDay, PATIENT_ACTION, PHASES, phaseOf, relDays, roleLabel, sevRank, STAGE_EXPLAIN, useFacilityName, useLive, useStageLabel } from "../lib";

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** Patient view: phone-first, calm, plain language. */
export default function PatientHome() {
  const { user } = useAuth();
  const list = useLive(() => api.myPathways(), [user?.id], (m) => m.kind === "pathway_created" || m.kind === "clock");
  const pathways = list.data || [];
  const [chosen, setChosen] = useState<string | null>(null);
  const active = pathways.find((p) => p.id === chosen) || pathways.find((p) => p.status === "active") || pathways[0];

  return (
    <div className="page patient">
      <Header subtitle="My care" />
      <main className="container narrow">
        <h1 className="greeting">Hello, {user?.display_name.split(" ")[0]}</h1>
        {list.error && <ErrorNote msg={list.error} onRetry={list.reload} />}
        {!list.data && !list.error && <div className="center pad"><Spinner /></div>}
        {list.data && pathways.length === 0 && (
          <div className="card"><p>You don't have an active care pathway. If you have symptoms, contact your health centre.</p>
            {user?.id === "pat-emma" && <SymptomsButton onDone={list.reload} />}</div>
        )}
        <Inbox />
        {pathways.length > 1 && (
          <div className="row wrap">
            {pathways.map((p) => (
              <button key={p.id} className={`chip ${p.id === active?.id ? "on" : ""}`} onClick={() => setChosen(p.id)}>
                {p.current_stage_label} · {p.status}
              </button>
            ))}
          </div>
        )}
        {active && <PatientPathway key={active.id} id={active.id} />}
      </main>
    </div>
  );
}

/** Demo: Emma reports symptoms and books a GP visit (creates her pathway in the shared backend). */
function SymptomsButton({ onDone }: { onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  return (<>
    <button className="action" disabled={busy} onClick={async () => {
      setBusy(true); setErr(null);
      try { await api.reportSymptoms(); onDone(); } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
    }}><b>{busy ? "Booking…" : "I have symptoms: book a GP appointment"}</b>
      <span className="muted small">Rectal bleeding for 3 weeks, tiredness. The earliest slot at Niinivaara health station.</span></button>
    {err && <ErrorNote msg={err} />}
  </>);
}

/** Messages from CancerFlow (reminders, rescheduling, follow-up). Confirming updates the shared appointment. */
export function Inbox() {
  const { user } = useAuth();
  const n = useLive(() => api.notifications().catch(() => [] as OpsNotification[]), [user?.id],
    (m) => m.kind === "notification" || m.kind === "event" || m.kind === "clock" || m.kind === "ops");
  const [busy, setBusy] = useState<string | null>(null);
  const [done, setDone] = useState<Record<string, boolean>>({});
  const items = (n.data || []).slice(0, 4);
  if (!items.length) return null;
  const confirm = async (x: OpsNotification) => {
    setBusy(x.id);
    try { await api.confirmAppointment(x.payload.appointment_id); setDone((d) => ({ ...d, [x.payload.appointment_id]: true })); n.reload(); }
    finally { setBusy(null); }
  };
  return (
    <section className="card inbox">
      <h3>Messages</h3>
      {items.map((x, i) => {
        const needs = (x.payload?.needs_confirmation || x.payload?.needs_checkin) && !done[x.payload?.appointment_id];
        return (
          <div key={x.id} className={`inbox-item ${x.severity} ${i === 0 ? "latest" : ""}`}>
            <b>{x.title}</b>
            <p>{x.body}</p>
            <div className="row">
              <span className="muted small">{new Date(x.created_at).toLocaleString("en-GB", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}</span>
              {needs && <button className="btn primary small" disabled={busy === x.id} onClick={() => confirm(x)}>
                {x.payload?.needs_checkin ? "I'm prepared and coming" : "Confirm"}</button>}
              {x.kind === "missed" && x.audience === "care_team" && x.payload?.intervention_id && !done[x.id] &&
                <button className="btn primary small" disabled={busy === x.id} onClick={async () => {
                  setBusy(x.id);
                  try { await api.authoriseFollowup(x.payload.intervention_id); setDone((d) => ({ ...d, [x.id]: true })); } finally { setBusy(null); }
                }}>Authorise earliest replacement slot</button>}
              {!needs && x.payload?.appointment_id && ((x as any).confirmed || done[x.payload.appointment_id]) && (x as any).appointment_status === "booked" && <span className="ok-note small">Confirmed ✓</span>}
              {(x as any).appointment_status === "cancelled" && <span className="muted small">replaced</span>}
            </div>
          </div>);
      })}
    </section>);
}

function PatientPathway({ id }: { id: string }) {
  const stageLabel = useStageLabel();
  const facName = useFacilityName();
  const st = useLive(() => api.pathway(id), [id],
    (m) => (m.kind === "event" && m.pathway_id === id) || m.kind === "clock" || m.kind === "alerts_rescanned");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [sent, setSent] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);

  if (st.error && !st.data) return <ErrorNote msg={st.error} onRetry={st.reload} />;
  if (!st.data) return <div className="center pad"><Spinner /></div>;
  const s: PathwayState = st.data;
  const now = s.as_of;
  const cur = s.current_stage;
  const phase = phaseOf(cur?.id);
  const closed = s.status === "closed";
  // The most urgent open alert decides who the patient is told is handling it.
  const lead = s.alerts.filter((a) => a.status !== "resolved")
    .sort((a, b) => sevRank(b.severity) - sevRank(a.severity) || (a.due_at ?? "").localeCompare(b.due_at ?? ""))[0];
  const upcoming = s.appointments.filter((a) => a.status === "booked" && a.scheduled_for >= now)
    .sort((a, b) => a.scheduled_for.localeCompare(b.scheduled_for));
  const myActions = s.allowed_next.filter((n) => n.actors.includes("patient") && PATIENT_ACTION[n.id]);
  const nextSteps = s.allowed_next.filter((n) => !["DECEASED", "WITHDRAWN", "DECLINED"].includes(n.id));
  // Patients only see clocks that have started; the rest are clinical detail and would just be noise.
  const clocks = s.clocks.filter((c) => c.status === "running" || c.status === "met" || c.status === "missed");

  const post = async (ev: Parameters<typeof api.postEvent>[1], okText: string): Promise<boolean> => {
    setBusy(true); setErr(null); setSent(null);
    try { const r = await api.postEvent(s.id, ev, uuid()); st.setData(r.state); setSent(okText); setConfirming(null); return true; }
    catch (e: any) { setErr(e.message); return false; }
    finally { setBusy(false); }
  };

  return (
    <>
      {/* where am I */}
      <section className="card hero">
        <div className="eyebrow">{closed ? "Pathway closed" : "Where you are now"}</div>
        <h2>{cur?.label ?? "Getting started"}</h2>
        <p>{(cur && STAGE_EXPLAIN[cur.id]) || "Your care team will be in touch about the next step."}</p>
        {!closed && phase >= 0 && (
          <ol className="stepper" aria-label="Your care journey">
            {PHASES.map((p, i) => (
              <li key={p.label} className={i < phase ? "done" : i === phase ? "now" : ""}>
                <span className="step-dot">{i < phase ? "✓" : i + 1}</span>
                <span className="step-label">{p.label}</span>
              </li>
            ))}
          </ol>
        )}
      </section>

      {/* reassurance when something is late: the system has already told the care team */}
      {lead && !closed && (
        <section className="card notice">
          <b>A step is taking longer than expected</b>
          <p>
            <b className="who-acts">{cap(roleLabel(lead.responsible_role, "patient"))}</b>
            {facName(lead.responsible_facility_id) && <> at <b className="who-acts">{facName(lead.responsible_facility_id)}</b></>}
            {" "}has been notified automatically and should contact you. If you haven't heard from them in the next few days, call your clinic.
          </p>
        </section>
      )}

      {upcoming.length > 0 && (
        <section className="card">
          <h3>Upcoming appointments</h3>
          <ul className="appts">
            {upcoming.map((a) => (
              <li key={a.id}>
                <div className="appt-date"><b>{fmtDay(a.scheduled_for)}</b>
                  <span>{new Date(a.scheduled_for).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}</span></div>
                <div><b>{stageLabel(a.stage)}</b><div className="muted small">{facName(a.facility_id) || ""} · {relDays(a.scheduled_for, now)}</div>
                  {a.confirmed_at ? <span className="ok-note small">Confirmed ✓</span>
                    : <button className="btn primary small" onClick={() => api.confirmAppointment(a.id).then(st.reload)}>Confirm</button>}</div>
              </li>
            ))}
          </ul>
        </section>
      )}

      {!closed && nextSteps.length > 0 && (
        <section className="card">
          <h3>What happens next</h3>
          <ul className="next">
            {nextSteps.map((n) => <li key={n.id}>{n.label}</li>)}
          </ul>
        </section>
      )}

      {clocks.length > 0 && (
        <section className="card">
          <h3>Waiting times</h3>
          <p className="muted small">Finnish and Danish targets for how long each part of the pathway should take.</p>
          {clocks.map((c) => <ClockBar key={c.id} c={c} now={now} plain />)}
        </section>
      )}

      {!closed && (
        <section className="card">
          <h3>Let your care team know</h3>
          {myActions.map((a) => {
            const meta = PATIENT_ACTION[a.id];
            if (confirming === a.id) return (
              <div key={a.id} className="confirm">
                <p>{meta.confirm}</p>
                <div className="row">
                  <button className="btn danger" disabled={busy} onClick={() => post({ type: "STAGE_ENTERED", stage: a.id }, "Your care team has been informed.")}>Yes, confirm</button>
                  <button className="btn ghost" onClick={() => setConfirming(null)}>Cancel</button>
                </div>
              </div>
            );
            return (
              <button key={a.id} className={`action ${a.id === "DECLINED" ? "quiet" : ""}`} disabled={busy}
                      onClick={() => meta.confirm ? setConfirming(a.id) : post({ type: "STAGE_ENTERED", stage: a.id }, "Thanks, your care team can see this now.")}>
                <b>{meta.label}</b><span className="muted small">{meta.hint}</span>
              </button>
            );
          })}
          <form className="msg-form" onSubmit={(e) => {
            e.preventDefault();
            if (msg.trim()) post({ type: "NOTE", payload: { text: msg.trim() } }, "Message sent to your care team.").then((ok) => { if (ok) setMsg(""); });
          }}>
            <label htmlFor="msg">Message</label>
            <textarea id="msg" className="input" rows={3} placeholder="e.g. I haven't received my appointment letter yet"
                      value={msg} onChange={(e) => setMsg(e.target.value)} maxLength={1000} />
            <button className="btn primary" disabled={busy || !msg.trim()}>Send message</button>
          </form>
          {sent && <div className="ok-note" role="status">{sent}</div>}
          {err && <ErrorNote msg={err} />}
        </section>
      )}

      <section className="card">
        <h3>Your history</h3>
        <Timeline events={s.timeline} stageLabel={stageLabel} now={now} />
      </section>

      <p className="footnote muted small">
        {s.patient.display_name} is a <Tag t={s.patient.tag} /> demo patient. Targets are tagged <Tag t="REFERENCE" /> (Finland) or <Tag t="EXTERNAL_PROXY" /> (Denmark).
      </p>
    </>
  );
}
