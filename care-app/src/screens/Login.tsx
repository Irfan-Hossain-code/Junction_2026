import { useEffect, useState } from "react";
import { api, Role, User } from "../api";
import { useAuth } from "../auth";
import { ErrorNote, Spinner, Tag } from "../components";

// Short context for the four scripted demo stories (docs/HANDOFF.md).
const DEMO_NOTES: Record<string, string> = {
  "pat-d-0001": "Positive FIT, no colonoscopy for 40 days",
  "pat-d-0002": "Referral sent, never received",
  "pat-d-0003": "On track, MDT decision done",
  "pat-d-0004": "Histology result pending too long",
  "nurse-pkks-scr": "Worklist shows Matti's overdue colonoscopy",
};

export default function Login() {
  const { login } = useAuth();
  const [role, setRole] = useState<Role>("patient");
  const [users, setUsers] = useState<User[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [q, setQ] = useState("");

  const load = () => {
    setUsers(null); setErr(null);
    api.users(role).then(setUsers).catch((e) => setErr(e.message));
  };
  useEffect(load, [role]);
  useEffect(() => { document.documentElement.dataset.mode = role; }, [role]);

  const pick = async (id: string) => {
    setBusy(id); setErr(null);
    try { await login(id); } catch (e: any) { setErr(e.message); setBusy(null); }
  };

  const shown = (users || [])
    .filter((u) => !q || `${u.display_name} ${u.facility_name ?? ""} ${u.id}`.toLowerCase().includes(q.toLowerCase()))
    .sort((a, b) => Number(!!DEMO_NOTES[b.id]) - Number(!!DEMO_NOTES[a.id]));

  return (
    <div className="login">
      <div className="login-card">
        <div className="brand big"><span className="dot" />CancerFlow</div>
        <h1>{role === "patient" ? "Your care, step by step" : "Care continuity worklist"}</h1>
        <p className="muted">
          {role === "patient"
            ? "See where you are in your bowel cancer pathway, what happens next and how long it should take."
            : "Every colorectal pathway at your unit, with overdue steps and missing follow-ups flagged first."}
        </p>

        <div className="seg" role="tablist" aria-label="I am a">
          <button role="tab" aria-selected={role === "patient"} className={role === "patient" ? "on" : ""} onClick={() => setRole("patient")}>Patient</button>
          <button role="tab" aria-selected={role === "doctor"} className={role === "doctor" ? "on" : ""} onClick={() => setRole("doctor")}>Clinician</button>
        </div>

        <p className="small muted">
          Demo login, no password. All people here are <Tag t="SYNTHETIC" /> demo data.{" "}
          <a href="#/demo" className="linkish">Open the side-by-side demo →</a>
        </p>

        {(users?.length ?? 0) > 8 && (
          <input className="input" placeholder="Search by name or unit…" value={q} onChange={(e) => setQ(e.target.value)} />
        )}
        {err && <ErrorNote msg={err} onRetry={load} />}
        {!users && !err && <div className="center"><Spinner /></div>}

        <ul className="user-list">
          {shown.map((u) => (
            <li key={u.id}>
              <button className="user-btn" disabled={!!busy} onClick={() => pick(u.id)}>
                <span className="avatar">{u.display_name.split(" ").map((w) => w[0]).slice(0, 2).join("")}</span>
                <span className="user-text">
                  <b>{u.display_name}</b>
                  <span className="muted small">
                    {DEMO_NOTES[u.id] || [u.staff_role?.replace(/_/g, " "), u.facility_name].filter(Boolean).join(" · ") || u.id}
                  </span>
                </span>
                {busy === u.id ? <Spinner /> : <span className="chev">›</span>}
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
