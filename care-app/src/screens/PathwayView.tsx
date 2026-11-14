import { FormEvent, useEffect, useState } from "react";
import { api, Appointment, Facility, NewEvent, PathwayState, Stage, uuid } from "../api";
import { useAuth } from "../auth";
import { AlertCard, ClockBar, ErrorNote, Header, Spinner, Tag, Timeline } from "../components";
import { EXIT_STAGES, fmtDate, fromLocalInput, go, relDays, ROUTE_LABEL, toLocalInput, useLive, useStageLabel } from "../lib";

/** Clinician view of one pathway: record the next step, book, note, acknowledge. */
export default function PathwayView({ id }: { id: string }) {
  const stageLabel = useStageLabel();
  const st = useLive(() => api.pathway(id), [id],
    (m) => (m.kind === "event" && m.pathway_id === id) || m.kind === "clock" || m.kind === "alerts_rescanned");
  const [flash, setFlash] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // One place for every write: shows the backend's reason on 409, swaps in the returned state on success.
  const send = async (ev: NewEvent, eventId: string, ok: string): Promise<boolean> => {
    setBusy(true); setErr(null); setFlash(null);
    try {
      const r = await api.postEvent(id, ev, eventId);
      st.setData(r.state);
      setFlash(r.result.duplicate ? "Already recorded (duplicate ignored)." : ok);
      return true;
    } catch (e: any) {
      setErr(e.status === 409 ? `Not allowed: ${e.message}` : e.message);
      return false;
    } finally { setBusy(false); }
  };
  const ack = async (alertId: string) => {
    setBusy(true); setErr(null);
    try { st.setData(await api.acknowledge(alertId)); setFlash("Alert acknowledged."); }
    catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };

  const s = st.data;
  return (
    <div className="page doctor">
      <Header subtitle="Pathway">
        <button className="btn ghost small" onClick={() => go("/worklist")}>‹ Worklist</button>
      </Header>
      <main className="container">
        {st.error && !s && <ErrorNote msg={st.error} onRetry={st.reload} />}
        {!s && !st.error && <div className="center pad"><Spinner /></div>}
        {s && (
          <>
            <PatientHead s={s} />
            {(flash || err) && (
              <div className="flash-wrap">
                {flash && <div className="ok-note" role="status">{flash}</div>}
                {err && <ErrorNote msg={err} />}
              </div>
            )}
            <div className="pw-grid">
              <div className="col">
                <NextStep s={s} busy={busy} send={send} />
                <OpenAlerts s={s} busy={busy} onAck={ack} />
                <section className="panel">
                  <div className="panel-h"><h3>Target clocks</h3></div>
                  <div className="panel-b">
                    {s.clocks.filter((c) => c.status !== "cancelled").map((c) => <ClockBar key={c.id} c={c} now={s.as_of} />)}
                  </div>
                </section>
                <Appointments s={s} busy={busy} send={send} stageLabel={stageLabel} />
                <Tools s={s} busy={busy} send={send} />
              </div>
              <section className="panel">
                <div className="panel-h"><h3>Timeline</h3><span className="ctx">{s.timeline.length} events · append-only</span></div>
                <div className="panel-b"><Timeline events={s.timeline} stageLabel={stageLabel} now={s.as_of} /></div>
              </section>
            </div>
          </>
        )}
      </main>
    </div>
  );
}

type Send = (ev: NewEvent, eventId: string, ok: string) => Promise<boolean>;

function PatientHead({ s }: { s: PathwayState }) {
  const p = s.patient;
  const age = p.birth_year ? new Date(s.as_of).getFullYear() - p.birth_year : null;
  return (
    <div className="pw-head">
      <div>
        <h1>{p.display_name} <Tag t={p.tag} /></h1>
        <p className="muted">
          <span className="mono">{p.id}</span>
          {age != null && <> · {age} y</>}{p.sex && <> · {p.sex}</>}
          {" · "}{ROUTE_LABEL[s.entry_route] || s.entry_route}
          {" · "}opened {fmtDate(s.timeline[0]?.occurred_at)}
        </p>
      </div>
      <div className={`stage-pill ${s.status}`}>
        <small>{s.status === "closed" ? "Closed at" : "Current stage"}</small>
        <b>{s.current_stage?.label ?? "–"}</b>
      </div>
    </div>
  );
}

function NextStep({ s, busy, send }: { s: PathwayState; busy: boolean; send: Send }) {
  const { user } = useAuth();
  const mine = s.allowed_next.filter((n) => n.actors.includes("doctor"));
  const forward = mine.filter((n) => !EXIT_STAGES.has(n.id));
  const exits = mine.filter((n) => EXIT_STAGES.has(n.id));
  const [pending, setPending] = useState<{ stage: Stage; eventId: string } | null>(null);
  const [when, setWhen] = useState("");
  const [note, setNote] = useState("");
  const [showExits, setShowExits] = useState(false);

  const start = (stage: Stage) => { setPending({ stage, eventId: uuid() }); setWhen(toLocalInput(s.as_of)); setNote(""); };
  const confirm = async () => {
    if (!pending) return;
    const ok = await send({
      type: "STAGE_ENTERED", stage: pending.stage.id, occurred_at: when ? fromLocalInput(when) : undefined,
      facility_id: user?.facility_id ?? undefined, payload: note.trim() ? { note: note.trim() } : {},
    }, pending.eventId, `Recorded: ${pending.stage.label}.`);
    if (ok) { setPending(null); setShowExits(false); }
  };

  if (s.status === "closed") return (
    <section className="panel"><div className="panel-b"><p className="muted">This pathway is closed. No further steps can be recorded.</p></div></section>
  );

  return (
    <section className="panel accent">
      <div className="panel-h"><h3>Record next step</h3><span className="ctx">validated against the colorectal pack</span></div>
      <div className="panel-b">
        {!pending && (
          <>
            <div className="step-btns">
              {forward.map((n) => (
                <button key={n.id} className="btn step" disabled={busy} onClick={() => start(n)}>
                  <b>{n.label}</b><small>{n.level}{n.capability ? ` · ${n.capability.replace(/_/g, " ")}` : ""}</small>
                </button>
              ))}
              {forward.length === 0 && <p className="muted">No regular next step. Use "Close pathway" if it has ended.</p>}
            </div>
            {exits.length > 0 && (
              <div className="exits">
                <button className="linkish" onClick={() => setShowExits(!showExits)}>{showExits ? "Hide" : "Close pathway…"}</button>
                {showExits && <div className="row wrap">
                  {exits.map((n) => <button key={n.id} className="btn small danger-ghost" disabled={busy} onClick={() => start(n)}>{n.label}</button>)}
                </div>}
              </div>
            )}
          </>
        )}
        {pending && (
          <div className={`confirm ${EXIT_STAGES.has(pending.stage.id) ? "danger" : ""}`}>
            <div>Record <b>{pending.stage.label}</b>{EXIT_STAGES.has(pending.stage.id) && " (this closes the pathway)"}</div>
            <div className="row wrap">
              <label className="grow">When<input className="input" type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} /></label>
              <label className="grow2">Note (optional)<input className="input" value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. rectal bleeding, refer" /></label>
            </div>
            <div className="row end">
              <button className="btn ghost" onClick={() => setPending(null)} disabled={busy}>Cancel</button>
              <button className={`btn ${EXIT_STAGES.has(pending.stage.id) ? "danger" : "primary"}`} onClick={confirm} disabled={busy}>
                {busy ? "Saving…" : "Confirm"}
              </button>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}

function OpenAlerts({ s, busy, onAck }: { s: PathwayState; busy: boolean; onAck: (id: string) => void }) {
  const open = s.alerts.filter((a) => a.status !== "resolved");
  if (open.length === 0) return null;
  return (
    <section className="panel">
      <div className="panel-h"><h3>Alerts</h3><span className="ctx">resolve automatically when the next step is recorded</span></div>
      <div className="panel-b">{open.map((a) => <AlertCard key={a.id} a={a} now={s.as_of} busy={busy} onAck={() => onAck(a.id)} />)}</div>
    </section>
  );
}

function Appointments({ s, busy, send, stageLabel }: { s: PathwayState; busy: boolean; send: Send; stageLabel: (id: string) => string }) {
  const appts = [...s.appointments].sort((a, b) => b.scheduled_for.localeCompare(a.scheduled_for));
  if (appts.length === 0) return null;
  const setStatus = (a: Appointment, status: "attended" | "cancelled" | "missed") =>
    send({ type: "APPOINTMENT_STATUS", payload: { appointment_id: a.id, status } }, uuid(), `Appointment marked ${status}.`);
  return (
    <section className="panel">
      <div className="panel-h"><h3>Appointments</h3></div>
      <div className="panel-b">
        <ul className="appts doc">
          {appts.map((a) => (
            <li key={a.id}>
              <div><b>{stageLabel(a.stage)}</b>
                <div className="muted small">{fmtDate(a.scheduled_for, true)} · {relDays(a.scheduled_for, s.as_of)}{a.facility_id ? ` · ${a.facility_id}` : ""}</div>
              </div>
              {a.status === "booked" && s.status === "active" ? (
                <div className="row">
                  <button className="btn small" disabled={busy} onClick={() => setStatus(a, "attended")}>Attended</button>
                  <button className="btn small ghost" disabled={busy} onClick={() => setStatus(a, "missed")}>Missed</button>
                  <button className="btn small ghost" disabled={busy} onClick={() => setStatus(a, "cancelled")}>Cancel</button>
                </div>
              ) : <span className={`appt-status ${a.status}`}>{a.status}</span>}
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

type ToolTab = "note" | "result" | "book";

function Tools({ s, busy, send }: { s: PathwayState; busy: boolean; send: Send }) {
  const [tab, setTab] = useState<ToolTab>("note");
  if (s.status === "closed") return null;
  return (
    <section className="panel">
      <div className="panel-h">
        <div className="seg small">
          <button className={tab === "note" ? "on" : ""} onClick={() => setTab("note")}>Note</button>
          <button className={tab === "result" ? "on" : ""} onClick={() => setTab("result")}>Result</button>
          <button className={tab === "book" ? "on" : ""} onClick={() => setTab("book")}>Book</button>
        </div>
      </div>
      <div className="panel-b">
        {tab === "note" && <NoteForm busy={busy} send={send} />}
        {tab === "result" && <ResultForm busy={busy} send={send} />}
        {tab === "book" && <BookForm key={s.current_stage?.id} s={s} busy={busy} send={send} />}
      </div>
    </section>
  );
}

function NoteForm({ busy, send }: { busy: boolean; send: Send }) {
  const [text, setText] = useState("");
  const [eventId, setEventId] = useState(uuid);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (await send({ type: "NOTE", payload: { text: text.trim() } }, eventId, "Note added.")) { setText(""); setEventId(uuid()); }
  };
  return (
    <form className="tool-form" onSubmit={submit}>
      <textarea className="input" rows={2} placeholder="e.g. Called patient, colonoscopy prep explained" value={text}
                onChange={(e) => setText(e.target.value)} maxLength={2000} />
      <div className="row end"><button className="btn primary" disabled={busy || !text.trim()}>Add note</button></div>
    </form>
  );
}

const RESULT_KINDS = ["FIT", "Colonoscopy", "Histology", "CT", "MRI", "Blood test", "Other"];
function ResultForm({ busy, send }: { busy: boolean; send: Send }) {
  const [kind, setKind] = useState("FIT");
  const [value, setValue] = useState("");
  const [eventId, setEventId] = useState(uuid);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (await send({ type: "RESULT_RECORDED", payload: { kind, value: value.trim() } }, eventId, `${kind} result recorded.`)) {
      setValue(""); setEventId(uuid());
    }
  };
  return (
    <form className="tool-form" onSubmit={submit}>
      <div className="row wrap">
        <label className="grow">Test<select className="input" value={kind} onChange={(e) => setKind(e.target.value)}>
          {RESULT_KINDS.map((k) => <option key={k}>{k}</option>)}
        </select></label>
        <label className="grow2">Result<input className="input" value={value} onChange={(e) => setValue(e.target.value)} placeholder="e.g. positive, 85 µg/g" /></label>
      </div>
      <p className="muted small">Recording a result doesn't move the pathway. Record the stage too if it changes.</p>
      <div className="row end"><button className="btn primary" disabled={busy || !value.trim()}>Record result</button></div>
    </form>
  );
}

function BookForm({ s, busy, send }: { s: PathwayState; busy: boolean; send: Send }) {
  const { user } = useAuth();
  // Stages worth booking: upcoming steps that need a service, then the current one.
  const options = [...s.allowed_next.filter((n) => n.capability && !EXIT_STAGES.has(n.id)),
    ...(s.current_stage?.capability ? [s.current_stage] : [])];
  const fallback = s.allowed_next.filter((n) => !EXIT_STAGES.has(n.id));
  const stages = options.length ? options : fallback;
  const [stage, setStage] = useState(stages[0]?.id ?? "");
  const defaultWhen = (() => { const d = new Date(s.as_of); d.setDate(d.getDate() + 7); d.setHours(9, 0, 0, 0); return toLocalInput(d.toISOString()); })();
  const [when, setWhen] = useState(defaultWhen);
  const [facilities, setFacilities] = useState<Facility[]>([]);
  const [facility, setFacility] = useState(user?.facility_id ?? "");
  const [eventId, setEventId] = useState(uuid);
  const cap = stages.find((x) => x.id === stage)?.capability;

  useEffect(() => {
    if (!cap) { setFacilities([]); setFacility(user?.facility_id ?? ""); return; }
    let live = true;
    api.facilities(cap).then((fs) => {
      if (!live) return;
      setFacilities(fs);
      setFacility(fs.some((f) => f.id === user?.facility_id) ? user!.facility_id! : fs[0]?.id ?? "");
    }).catch(() => {});
    return () => { live = false; };
  }, [cap, user?.facility_id]);

  if (stages.length === 0) return <p className="muted">Nothing to book at this stage.</p>;
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const ok = await send({
      type: "APPOINTMENT_BOOKED",
      payload: { stage, scheduled_for: fromLocalInput(when), facility_id: facility || undefined },
    }, eventId, "Appointment booked.");
    if (ok) setEventId(uuid());
  };
  return (
    <form className="tool-form" onSubmit={submit}>
      <div className="row wrap">
        <label className="grow">For<select className="input" value={stage} onChange={(e) => setStage(e.target.value)}>
          {stages.map((n) => <option key={n.id} value={n.id}>{n.label}</option>)}
        </select></label>
        <label className="grow">When<input className="input" type="datetime-local" required value={when} onChange={(e) => setWhen(e.target.value)} /></label>
      </div>
      <label>Where
        {facilities.length > 0 ? (
          <select className="input" value={facility} onChange={(e) => setFacility(e.target.value)}>
            {facilities.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
          </select>
        ) : <input className="input" value={facility} onChange={(e) => setFacility(e.target.value)} placeholder="facility id" />}
      </label>
      <div className="row end"><button className="btn primary" disabled={busy || !stage || !when}>Book appointment</button></div>
    </form>
  );
}
