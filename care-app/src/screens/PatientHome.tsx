import { useState } from "react";
import { api, PathwayState, uuid } from "../api";
import { useAuth } from "../auth";
import { ClockBar, ErrorNote, Header, Spinner, Tag, Timeline } from "../components";
import { fmtDay, PATIENT_ACTION, PHASES, phaseOf, relDays, STAGE_EXPLAIN, useLive, useStageLabel } from "../lib";

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
          <div className="card"><p>You don't have an active care pathway. If you have symptoms, contact your health centre.</p></div>
        )}
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

function PatientPathway({ id }: { id: string }) {
  const stageLabel = useStageLabel();
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
  const openAlerts = s.alerts.filter((a) => a.status !== "resolved");
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
      {openAlerts.length > 0 && !closed && (
        <section className="card notice">
          <b>A step is taking longer than expected</b>
          <p>Your care team has been notified automatically and should contact you. If you haven't heard from them in the next few days, call your clinic.</p>
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
                <div><b>{stageLabel(a.stage)}</b><div className="muted small">{relDays(a.scheduled_for, now)}</div></div>
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
