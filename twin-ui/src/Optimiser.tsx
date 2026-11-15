import { useEffect, useState } from "react";
import { API } from "./api";
import { Tag } from "./Panels";

// National pathway optimiser ("the brain"): counterfactual benchmark from /mpc/brain/* (CANCERFLOW_MPC=1).
const NAMES: Record<string, string> = {
  baseline_fifo: "Baseline (FIFO)", priority_greedy: "Priority greedy", diagnostics_first: "Diagnostics-first (conventional)",
  A_local_pathway: "A · Local pathway", B_national_pathway: "B · National pathway", C_resource_movement: "C · + Resource movement",
};
const m = (k: any) => (k && k.mean != null ? k.mean : null);
const f = (x: number | null, d = 1) => (x == null ? "–" : x.toFixed(d));

export default function Optimiser({ onClose }: { onClose: () => void }) {
  const [doc, setDoc] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [exp, setExp] = useState<any>(null);
  const [cl, setCl] = useState<any>(null);
  useEffect(() => { fetch(API + "/mpc/brain/closed_loop").then(r => r.ok ? r.json() : null).then(setCl).catch(() => {}); }, []);
  useEffect(() => {
    fetch(API + "/mpc/brain/benchmark").then(async r => {
      if (!r.ok) throw new Error(r.status === 404 ? "The optimiser API is not enabled. Start the backend with CANCERFLOW_MPC=1." : r.statusText);
      setDoc(await r.json());
    }).catch(e => setErr(String(e.message || e)));
  }, []);
  const explain = () => {
    setExp("loading");
    fetch(API + "/mpc/brain/explain", { method: "POST" }).then(r => r.json()).then(setExp).catch(() => setExp(null));
  };
  const S = doc?.strategies || {};
  const rec = doc?.recommendation;
  const best = rec ? S[rec.recommended_strategy] : null;
  const base = S.baseline_fifo, diag = S.diagnostics_first;
  return (
    <div className="opt-veil" onClick={onClose}>
      <div className="opt" onClick={e => e.stopPropagation()}>
        <div className="opt-h">
          <div><b>National pathway optimiser</b> <span className="muted">· end-to-end, existing resources first</span> <Tag t="SIMULATED" /> <Tag t="SYNTHETIC" /></div>
          <button className="btn" onClick={onClose}>Close</button>
        </div>
        {err && <div className="err" style={{ padding: 16 }}>{err}</div>}
        {!doc && !err && <div style={{ padding: 16 }}><div className="skeleton" /><div className="skeleton" /></div>}
        {doc && (
          <div className="opt-b">
            <div className="opt-hero">
              <div className="hero-k"><div className="l">Within target, baseline</div><div className="v num">{f(m(base.kpis.pct_within_target))}%</div></div>
              <div className="hero-arrow">→</div>
              <div className="hero-k good"><div className="l">{NAMES[rec.recommended_strategy]}</div><div className="v num">{f(m(best.kpis.pct_within_target))}%</div>
                <div className="s">no additional capacity · {Object.entries(rec.summary_actions).map(([k, v]) => `${v} ${k.replace(/_/g, " ")}`).join(" · ")}</div></div>
              <div className="hero-note">{doc.instance.name}: {doc.instance.patients} synthetic patients ({doc.instance.urgent} urgent), {doc.evaluation.replications} paired stochastic replications. Every action needs human approval.</div>
            </div>

            <div className="opt-grid">
              <section className="card act2">
                <h4>Why local queue optimisation fails</h4>
                <p>The conventional approach books colonoscopies faster:</p>
                <div className="kv">
                  <span className="k">Colonoscopy day</span><span className="v num">{f(m(base.kpis.mean_colonoscopy_day))} → <b className="up">{f(m(diag.kpis.mean_colonoscopy_day))}</b></span>
                  <span className="k">Diagnosis day</span><span className="v num">{f(m(base.kpis.mean_diagnosis_day))} → {f(m(diag.kpis.mean_diagnosis_day))}</span>
                  <span className="k">Treatment day</span><span className="v num">{f(m(base.kpis.mean_treatment_day))} → {f(m(diag.kpis.mean_treatment_day))}</span>
                </div>
                <p className="muted">The shared pathology service is the downstream constraint, so earlier appointments do not mean earlier treatment.
                  The pathway optimiser routes samples and reschedules end-to-end: diagnosis day <b>{f(m(best.kpis.mean_diagnosis_day))}</b>, treatment day <b>{f(m(best.kpis.mean_treatment_day))}</b>.</p>
              </section>

              <section className="card">
                <h4>Remaining bottlenecks (after the best existing-resource plan)</h4>
                {doc.bottlenecks_after_best_plan.slice(0, 5).map((b: any) => (
                  <div key={b.provider_id + b.capability} className="bar-row" style={{ gridTemplateColumns: "1fr 90px 44px" }}>
                    <span className="bl">{b.provider.replace(/\s*\(.*\)/, "")} · {b.capability}</span>
                    <span className="util-bar" style={{ width: 90 }}><i style={{ width: `${Math.min(100, 100 * b.planned_utilisation)}%`, background: b.binding ? "var(--bad)" : b.planned_utilisation > 0.75 ? "var(--warn)" : "var(--accent)" }} /></span>
                    <span className="bv num">{Math.round(100 * b.planned_utilisation)}%</span>
                  </div>))}
                <h4 style={{ marginTop: 10 }}>Investment, only after the above <Tag t="HYPOTHESIS" /></h4>
                {doc.investment_pass.map((i: any, k: number) => (
                  <div key={k} className="kv"><span className="k">{i.option || i.option_evaluated || Object.keys(i.chosen_investments).join(", ")}: {i.spent_eur ? `${i.spent_eur.toLocaleString()} EUR` : `not worth buying (budget ${i.budget_eur.toLocaleString()} EUR)`}</span>
                    <span className="v num">{f(i.breaches_avoided, 2)} breaches avoided</span></div>))}
              </section>
            </div>

            <section className="card">
              <h4>Counterfactual comparison: same patients, same capacity, same random streams</h4>
              <div style={{ overflowX: "auto" }}>
                <table className="captable opt-table">
                  <thead><tr><th>Strategy</th><th className="r">Within target</th><th className="r">Breaches</th><th className="r">Urgent br.</th><th className="r">Severe</th>
                    <th className="r">Treatment day</th><th className="r">Transfers</th><th className="r">Samples routed</th><th className="r">Staff moves</th><th className="r">Violations</th></tr></thead>
                  <tbody>
                    {Object.entries(S).map(([k, v]: any) => (
                      <tr key={k} className={k === rec.recommended_strategy ? "rec" : ""} title={v.label}>
                        <td>{NAMES[k] || k}{k === rec.recommended_strategy && <span className="tag LLM" style={{ marginLeft: 6 }}>recommended</span>}</td>
                        <td className="r num">{f(m(v.kpis.pct_within_target))}% <span className="muted">[{v.kpis.pct_within_target.ci95.map((x: number) => x.toFixed(0)).join("–")}]</span></td>
                        <td className="r num">{f(m(v.kpis.breaches))}</td><td className="r num">{f(m(v.kpis.urgent_breaches))}</td>
                        <td className="r num">{f(m(v.kpis.severe_delays_over_14d))}</td><td className="r num">{f(m(v.kpis.mean_treatment_day))}</td>
                        <td className="r num">{f(m(v.kpis.transfers))}</td><td className="r num">{f(m(v.kpis.routed_samples))}</td>
                        <td className="r num">{f(m(v.kpis.staff_relocation_weeks), 0)}</td>
                        <td className="r num" style={{ color: v.constraint_violations.length ? "var(--bad)" : "var(--good)" }}>{v.constraint_violations.length}</td>
                      </tr>))}
                  </tbody>
                </table>
              </div>
              <details style={{ marginTop: 6 }}><summary>Why the alternatives were rejected</summary>
                <ul className="muted" style={{ fontSize: 11.5 }}>{Object.entries(rec.rejected_alternatives).map(([k, v]: any) => <li key={k}><b>{NAMES[k] || k}:</b> {v.join("; ")}</li>)}</ul>
              </details>
            </section>

            {cl && (
              <section className="card">
                <h4>Closed-loop MPC: re-planning on observed state <Tag t="SIMULATED" /></h4>
                <p className="muted">{cl.disruption.label}. Same patients, random numbers and outage. Open loop plans once on nominal capacity;
                  closed loop re-solves every {cl.replan_every_days} days and when the outage is observed ({cl.closed_loop.epochs?.length} control epochs).</p>
                <table className="captable opt-table"><thead><tr><th /><th className="r">Within target</th><th className="r">Weighted lateness</th>
                  <th className="r">Treatment day</th><th className="r">Urgent br.</th><th className="r">Samples routed</th><th className="r">Overbooked</th></tr></thead>
                  <tbody>{(["open_loop", "closed_loop"] as const).map(k => (
                    <tr key={k} className={k === "closed_loop" ? "rec" : ""}><td>{k === "open_loop" ? "Open loop (plan once)" : "Closed loop (receding horizon)"}</td>
                      <td className="r num">{f(cl[k].kpis.pct_within_target)}%</td><td className="r num">{f(cl[k].kpis.weighted_lateness_days, 0)}</td>
                      <td className="r num">{f(cl[k].kpis.mean_treatment_day)}</td><td className="r num">{f(cl[k].kpis.urgent_breaches)}</td>
                      <td className="r num">{f(cl[k].kpis.routed_samples)}</td><td className="r num">{f(cl[k].kpis.overbooked_slots, 0)}</td></tr>))}</tbody></table>
              </section>)}

            {doc.patient_story && (
              <section className="card">
                <h4>One patient: {doc.patient_story.pid} · {doc.patient_story.priority} · {doc.patient_story.region} <Tag t="SYNTHETIC" /></h4>
                <div className="opt-grid">
                  {(["original_plan", "recommended_plan"] as const).map(k => (
                    <div key={k} className="journey">
                      <div className="jt">{k === "original_plan" ? "Baseline" : "Recommended"} · treatment day {f(doc.patient_story[k === "original_plan" ? "original_outcome" : "recommended_outcome"].treatment_day_mean)} (target day {doc.patient_story.deadline_day})</div>
                      <ol>{doc.patient_story[k].map((s: any, i: number) => (
                        <li key={i}><span className="d num">d{Math.round(s.planned_day)}</span>{s.stage} · {s.provider}{s.tentative && <span className="muted"> (tentative: diagnosis not yet known)</span>}</li>))}</ol>
                    </div>))}
                </div>
              </section>)}

            <section className="card">
              <button className="btn" onClick={explain} disabled={exp === "loading"}>{exp === "loading" ? "Explaining…" : "Explain this recommendation"}</button>
              {exp && exp !== "loading" && <div className="explain"><span className={`tag ${exp.mode === "llm" ? "LLM" : "FALLBACK"}`}>{exp.mode === "llm" ? "nemotron · grounded in solver output" : "deterministic"}</span> {exp.text}</div>}
              <div className="muted" style={{ fontSize: 10.5, marginTop: 6 }}>Illustrative East Finland instance: real places, ASSUMED capacities, probabilities and costs, SYNTHETIC patients. Results are not evidence of real Finnish performance.</div>
            </section>
          </div>)}
      </div>
    </div>);
}
