import { useEffect, useState } from "react";
import { api, User } from "../api";
import { DemoClock, ErrorNote, LiveDot, Spinner } from "../components";

// Presenter view: clinician on the left, the patient's phone on the right, both live.
// Each frame is pinned to its user with `?as=` (see auth.tsx), so the presenter's own login is untouched.
// ?patient=pat-emma&doctor=gp-ops-joensuu (hash route #/demo) picks the pair for the final storyline
const qp = new URLSearchParams(location.search);
const DEFAULT_DOCTOR = qp.get("doctor") || "nurse-pkks-scr";
const DEFAULT_PATIENT = qp.get("patient") || "pat-d-0001";

export default function Demo() {
  const [doctors, setDoctors] = useState<User[]>([]);
  const [patients, setPatients] = useState<User[]>([]);
  const [doc, setDoc] = useState(DEFAULT_DOCTOR);
  const [pat, setPat] = useState(DEFAULT_PATIENT);
  const [pathwayId, setPathwayId] = useState<string | null | undefined>(undefined); // undefined = loading
  const [err, setErr] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => { document.documentElement.dataset.mode = "doctor"; }, []);
  useEffect(() => {
    Promise.all([api.users("doctor"), api.users("patient")])
      .then(([d, p]) => { setDoctors(d); setPatients(p); })
      .catch((e) => setErr(e.message));
  }, []);

  // open the doctor's frame directly on this patient's (active) pathway
  useEffect(() => {
    const u = patients.find((x) => x.id === pat);
    if (!u) return;
    if (!u.patient_id) { setPathwayId(null); return; }
    setPathwayId(undefined);
    api.patientPathways(u.patient_id)
      .then((ps) => setPathwayId((ps.find((x) => x.status === "active") || ps[0])?.id ?? null))
      .catch(() => setPathwayId(null));
  }, [pat, patients]);

  const page = location.pathname;
  const docSrc = `${page}?as=${encodeURIComponent(doc)}#/${pathwayId ? `p/${pathwayId}` : "worklist"}`;
  const patSrc = `${page}?as=${encodeURIComponent(pat)}#/`;

  return (
    <div className="demo">
      <header className="topbar">
        <div className="brand"><span className="dot" />CancerFlow <small>Side-by-side demo</small></div>
        <label className="demo-pick">Clinician
          <select className="input" value={doc} onChange={(e) => setDoc(e.target.value)}>
            {doctors.map((u) => <option key={u.id} value={u.id}>{u.display_name}{u.facility_name ? ` · ${u.facility_name}` : ""}</option>)}
          </select>
        </label>
        <label className="demo-pick">Patient
          <select className="input" value={pat} onChange={(e) => setPat(e.target.value)}>
            {patients.map((u) => <option key={u.id} value={u.id}>{u.display_name}</option>)}
          </select>
        </label>
        <div className="spacer" />
        <button className="btn ghost small" onClick={() => setNonce((n) => n + 1)} title="Reload both screens">↻ Reload</button>
        <LiveDot />
        <DemoClock />
        <a className="btn ghost small" href={page}>Exit</a>
      </header>
      {err && <div className="container"><ErrorNote msg={err} /></div>}
      <main className="demo-stage">
        <section className="demo-desk">
          <div className="demo-label">Clinician · {doctors.find((u) => u.id === doc)?.display_name ?? doc}</div>
          {pathwayId === undefined
            ? <div className="center demo-frame"><Spinner /></div>
            : <iframe key={`d-${docSrc}-${nonce}`} className="demo-frame" src={docSrc} title="Clinician view" />}
        </section>
        <section className="demo-phone-col">
          <div className="demo-label">Patient's phone · {patients.find((u) => u.id === pat)?.display_name ?? pat}</div>
          <div className="phone">
            <iframe key={`p-${patSrc}-${nonce}`} src={patSrc} title="Patient view" />
          </div>
        </section>
      </main>
      <p className="demo-foot muted small">
        Steps recorded here are real events in the shared (append-only) database. Rehearse with a patient you don't need for the live demo.
      </p>
    </div>
  );
}
