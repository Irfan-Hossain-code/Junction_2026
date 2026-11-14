import { useEffect, useMemo, useState } from "react";
import { Context, Interpretation, Recommendation, Selection, Summary, api, mergeParams, num, pct, shortCounty } from "./api";

export const Tag = ({ t }: { t: string }) => <span className={`tag ${t}`}>{t.toLowerCase()}</span>;

function Delta({ a, b, pp = false, invert = false, d = 1, unit }: { a?: number | null; b?: number | null; pp?: boolean; invert?: boolean; d?: number; unit?: string }) {
  if (a == null || b == null) return null;
  const x = pp ? 100 * (b - a) : b - a;
  if (Math.abs(x) < Math.pow(10, -d) / 2) return <span className="muted"> ±0</span>;
  const good = invert ? x < 0 : x > 0;
  return <span className={good ? "up" : "down"}> {x > 0 ? "+" : ""}{x.toFixed(d)}{unit ?? (pp ? " pp" : " d")}</span>;
}

// ---------------------------------------------------------------- AI situation summary
export function AIPanel({ ctx, summary, loading, view }: { ctx: Context | null; summary: Summary | null; loading: boolean; view: string }) {
  const paras = (summary?.text || "").split(/\n+/).filter(Boolean);
  return (
    <div className="panel area-ai">
      <div className="panel-h">
        <h3>Situation analysis</h3>
        <span className="ctx">· {ctx?.context.name ? shortCounty(ctx.context.name) : "Finland"} · {view}</span>
        <div className="right">
          {summary && (summary.mode === "llm" ? <span className="tag LLM">nemotron · grounded</span> : <span className="tag FALLBACK">deterministic</span>)}
        </div>
      </div>
      <div className="panel-b">
        {ctx && <div className="ai-issue"><b style={{ color: "var(--warn)" }}>Primary issue · </b>{ctx.primary_issue.headline} <Tag t="SIMULATED" /></div>}
        {loading || !summary ? (<>{[90, 100, 95, 70, 98, 85, 60].map((w, i) => <div key={i} className="skeleton" style={{ width: `${w}%` }} />)}</>)
          : <div className="ai-text">{paras.map((p, i) => <p key={i}>{p}</p>)}</div>}
        {summary?.rejected && (
          <div className="muted" style={{ fontSize: 11.5, marginTop: 6 }}>
            ⚠ The LLM draft was discarded because it used numbers not present in the data ({summary.rejected.numbers.join(", ")}). Showing the deterministic analysis instead.
          </div>)}
        {summary && (
          <details style={{ marginTop: 8 }}>
            <summary>Grounding: the only facts the AI was allowed to use</summary>
            <pre className="facts">{JSON.stringify(summary.facts, null, 1)}</pre>
          </details>)}
      </div>
      <div className="ai-foot">
        <Tag t="OBSERVED" /> THL statistics <Tag t="SIMULATED" /> SimPy model output <Tag t="SYNTHETIC" /> demo patients &amp; capacity <Tag t="REFERENCE" /> 42-day target
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- compliance vs target
function Ring({ p, base, ci }: { p: number | null; base?: number | null; ci?: [number | null, number | null] | null }) {
  const R = 46, C = 2 * Math.PI * R;
  const col = p == null ? "#34425c" : p >= 0.9 ? "var(--teal)" : p >= 0.75 ? "var(--warn)" : "var(--bad)";
  return (
    <svg width="118" height="118" viewBox="0 0 118 118" role="img" aria-label={`within target ${pct(p)}`}>
      <circle cx="59" cy="59" r={R} fill="none" stroke="#16223a" strokeWidth="10" />
      {ci && ci[0] != null && ci[1] != null && (
        <circle cx="59" cy="59" r={R} fill="none" stroke={col} strokeOpacity=".25" strokeWidth="10"
          strokeDasharray={`${C * (ci[1] - ci[0])} ${C}`} strokeDashoffset={-C * ci[0]} transform="rotate(-90 59 59)" />)}
      <circle cx="59" cy="59" r={R} fill="none" stroke={col} strokeWidth="10" strokeLinecap="round"
        strokeDasharray={`${C * (p ?? 0)} ${C}`} transform="rotate(-90 59 59)" style={{ transition: "stroke-dasharray .6s ease" }} />
      {base != null && (() => {
        const a = 2 * Math.PI * base - Math.PI / 2;
        return <line x1={59 + (R - 9) * Math.cos(a)} y1={59 + (R - 9) * Math.sin(a)} x2={59 + (R + 9) * Math.cos(a)} y2={59 + (R + 9) * Math.sin(a)} stroke="#e7eef8" strokeWidth="2" opacity=".75" />;
      })()}
      <text x="59" y="57" textAnchor="middle" fill="#e7eef8" fontSize="22" fontWeight="700" className="num">{p == null ? "–" : (100 * p).toFixed(1)}</text>
      <text x="59" y="74" textAnchor="middle" fill="#8da2c0" fontSize="10">% within 42 d</text>
    </svg>
  );
}

export function CompliancePanel({ ctx, onSelect, view }: { ctx: Context | null; onSelect: (s: Selection) => void; view: string }) {
  const c = ctx?.compliance, b = ctx?.base?.compliance;
  const rows = useMemo(() => {
    if (!ctx) return [];
    if (ctx.ranking) return ctx.ranking.slice(0, 7);
    return [];
  }, [ctx]);
  return (
    <div className="panel area-comp">
      <div className="panel-h"><h3>Target compliance</h3><span className="ctx">· {ctx ? shortCounty(ctx.context.county_name || ctx.context.name) : ""}</span>
        <div className="right"><Tag t="SIMULATED" /></div></div>
      <div className="panel-b">
        {!ctx ? <div className="skeleton" /> : (<>
          <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
            <Ring p={c!.p_within_target} base={b?.p_within_target} ci={c!.ci} />
            <div style={{ flex: 1 }}>
              <div className="kv">
                <span className="k">Within 42-day reference</span><span className="v num">{pct(c!.p_within_target)}{b && <Delta a={b.p_within_target} b={c!.p_within_target} pp />}</span>
                <span className="k">95% CI</span><span className="v num muted">{c!.ci && c!.ci[0] != null ? `${pct(c!.ci[0])}–${pct(c!.ci[1])}` : "–"}</span>
                <span className="k">Exceeding / year</span><span className="v num">{num(c!.exceeding_per_year, 0)}{b && <Delta a={b.exceeding_per_year} b={c!.exceeding_per_year} invert d={0} unit="" />}</span>
                <span className="k">FIT+ → colonoscopy ≤ 28 d</span><span className="v num">{pct(c!.fit_to_colonoscopy_p_within)}{b && <Delta a={b.fit_to_colonoscopy_p_within} b={c!.fit_to_colonoscopy_p_within} pp />}</span>
                {ctx.context.scope !== "national" && <><span className="k">Finland (same scenario)</span><span className="v num">{pct(ctx.national_compliance.p_within_target)}</span></>}
              </div>
              {b && <div className="muted" style={{ fontSize: 11 }}>white tick = original scenario</div>}
            </div>
          </div>
          {rows.length > 0 && (<>
            <div className="section-l">Weakest regions · click to inspect</div>
            <div className="bars">
              {rows.map(r => {
                const br = (ctx.base as any)?.ranking?.find((x: any) => x.county_id === r.county_id);
                return (
                  <div key={r.county_id} className="bar-row click" onClick={() => onSelect({ scope: "county", id: r.county_id })}>
                    <span className="bl">{shortCounty(r.name)}</span>
                    <div className="bar-track">
                      <div className="bar-fill" style={{ width: `${100 * r.p_within_target}%`, background: r.p_within_target >= 0.9 ? "var(--teal)" : r.p_within_target >= 0.75 ? "var(--warn)" : "var(--bad)" }} />
                      {br && <div className="bar-ghost" style={{ left: `${100 * br.p_within_target}%` }} />}
                    </div>
                    <span className="bv num">{pct(r.p_within_target, 0)}</span>
                  </div>);
              })}
            </div>
          </>)}
          <div className="muted" style={{ fontSize: 10.5, marginTop: 8 }}>
            42 days = Finnish 6-week referral-to-treatment recommendation (STM 2010:6), used as a <span className="tag REFERENCE">reference</span> target, not a legal limit. {view !== "Original" && "Figures shown for the modified scenario."}
          </div>
        </>)}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- delays & pathway performance
export function DelayPanel({ ctx }: { ctx: Context | null }) {
  const d = ctx?.delays, b = ctx?.base?.delays;
  const maxStage = Math.max(1, ...(d?.stages || []).map(s => s.mean_days), ...(b?.stages || []).map(s => s.mean_days));
  const hist = d?.rtt_histogram || [];
  const bh = Object.fromEntries((b?.rtt_histogram || []).map(h => [h.bin, h.share]));
  const maxH = Math.max(0.01, ...hist.map(h => h.share), ...Object.values(bh));
  const binLabel = (x: number) => (x === 70 ? "70+" : `${x}–${x + 14}`);
  return (
    <div className="panel area-delay">
      <div className="panel-h"><h3>Treatment delays</h3><span className="ctx">· referral → treatment</span><div className="right"><Tag t="SIMULATED" /></div></div>
      <div className="panel-b">
        {!d ? <div className="skeleton" /> : (<>
          <div className="kpis">
            <div className="kpi"><div className="l">Median</div><div className="v num">{num(d.median_days, 0)} d</div><div className="s">{b && <Delta a={b.median_days} b={d.median_days} invert />}</div></div>
            <div className="kpi"><div className="l">90th pct</div><div className="v num">{num(d.p90_days, 0)} d</div><div className="s">{b && <Delta a={b.p90_days} b={d.p90_days} invert />}</div></div>
            <div className="kpi"><div className="l">Stalled step</div><div className="v num" style={{ color: (d.stall_days_mean ?? 0) > 20 ? "var(--bad)" : "var(--teal)" }}>{num(d.stall_days_mean, 0)} d</div>
              <div className="s">{b ? <Delta a={b.stall_days_mean} b={d.stall_days_mean} invert /> : <span className="muted">until noticed</span>}</div></div>
          </div>
          <div className="section-l">Time-to-treatment distribution {b && <span className="muted">· outline = original</span>}</div>
          <svg width="100%" height="64" viewBox="0 0 300 64" preserveAspectRatio="none" style={{ display: "block" }}>
            {hist.map((h, i) => {
              const w = 300 / hist.length, x = i * w, hh = (h.share / maxH) * 48, late = h.bin >= 42;
              const bb = bh[h.bin];
              return (<g key={h.bin}>
                <rect x={x + 3} y={52 - hh} width={w - 6} height={hh} rx="2" fill={late ? "#ef5350" : "#2dd4bf"} opacity={late ? 0.8 : 0.75} />
                {bb != null && <rect x={x + 3} y={52 - (bb / maxH) * 48} width={w - 6} height={(bb / maxH) * 48} rx="2" fill="none" stroke="#e7eef8" strokeOpacity=".6" strokeDasharray="3 2" />}
                <text x={x + w / 2} y="63" textAnchor="middle" fill="#6f819e" fontSize="8">{binLabel(h.bin)}</text>
              </g>);
            })}
            <line x1={150} x2={150} y1={0} y2={53} stroke="#2dd4bf" strokeDasharray="3 3" />
            <text x={153} y={9} fill="#2dd4bf" fontSize="8">42 d</text>
          </svg>
          <div className="section-l">Mean days spent at each stage {b && <span className="muted">· tick = original</span>}</div>
          <div className="bars">
            {d.stages.map(s => {
              const bs = b?.stages.find(x => x.stage === s.stage);
              return (
                <div key={s.stage} className="bar-row">
                  <span className="bl" title={s.label}>{s.label.replace("Screening nurse follow-up assessment", "Nurse assessment").replace("Multidisciplinary team decision", "MDT decision").replace("Referral received by hospital", "Referral received").replace("Referral sent to specialist care", "Referral sent").replace("Staging (CT; MRI if rectal)", "Staging")}</span>
                  <div className="bar-track">
                    <div className="bar-fill" style={{ width: `${(100 * s.mean_days) / maxStage}%`, background: s.mean_days > 12 ? "var(--warn)" : "#3b82f6" }} />
                    {bs && <div className="bar-ghost" style={{ left: `${(100 * bs.mean_days) / maxStage}%` }} />}
                  </div>
                  <span className="bv num">{s.mean_days.toFixed(1)} d</span>
                </div>);
            })}
          </div>
        </>)}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- intervention studio
type SimState = { running: boolean; started?: number; error?: string };

export function InterventionStudio({ ctx, selection, onSimulated }: {
  ctx: Context | null; selection: Selection; onSimulated: (runId: string, scenarioId: string, name: string) => void;
}) {
  const [chosen, setChosen] = useState<Record<string, boolean>>({});
  const [ctrl, setCtrl] = useState<Record<string, number>>({});
  const [text, setText] = useState("");
  const [parsed, setParsed] = useState<Interpretation | null>(null);
  const [parsing, setParsing] = useState(false);
  const [sim, setSim] = useState<SimState>({ running: false });
  const [now, setNow] = useState(Date.now());

  useEffect(() => { setChosen({}); setCtrl({}); setParsed(null); }, [selection.scope, selection.id]);
  useEffect(() => { if (!sim.running) return; const t = setInterval(() => setNow(Date.now()), 250); return () => clearInterval(t); }, [sim.running]);

  const recs = ctx?.recommendations || [];
  const applyCtrl = (r: Recommendation) => {
    const p = JSON.parse(JSON.stringify(r.params));
    for (const c of r.controls) {
      const v = ctrl[`${r.id}:${c.path}`] ?? c.value;
      const parts = c.path.split(".");
      let o = p; for (let i = 0; i < parts.length - 1; i++) o = o[isNaN(+parts[i]) ? parts[i] : +parts[i]];
      o[parts[parts.length - 1]] = v;
    }
    return p;
  };
  const selected = recs.filter(r => chosen[r.id]);
  const params = mergeParams([...selected.map(applyCtrl), parsed?.simulatable ? parsed.params : null]);
  const canRun = !sim.running && Object.keys(params).length > 0;

  const interpret = async () => {
    if (!text.trim()) return;
    setParsing(true); setParsed(null);
    try { setParsed(await api.interpret(text, selection)); } catch (e: any) { setParsed({ mode: "fallback", params: {}, actions: [], errors: [String(e.message || e)], unsupported: [], explanation: "", assumptions: {}, simulatable: false }); }
    setParsing(false);
  };
  const run = async () => {
    const names = [...selected.map(r => r.title), ...(parsed?.simulatable ? ["custom: " + text.slice(0, 50)] : [])];
    const name = `${ctx ? shortCounty(ctx.context.name) : "Finland"}: ${names.join(" + ")}`.slice(0, 140);
    setSim({ running: true, started: Date.now() });
    try {
      const r = await api.simulate(name, params, 4);
      setSim({ running: false });
      onSimulated(r.run_id, r.scenario_id, name);
    } catch (e: any) { setSim({ running: false, error: String(e.message || e) }); }
  };
  const elapsed = sim.started ? (now - sim.started) / 1000 : 0;

  return (
    <div className="panel area-studio">
      <div className="panel-h"><h3>Intervention studio</h3><span className="ctx">· {ctx ? shortCounty(ctx.context.name) : ""}</span></div>
      <div className="panel-b">
        {ctx?.primary_issue.top_station && (
          <div className="issue"><div className="lbl">Primary bottleneck</div>
            {ctx.primary_issue.top_station.capability_label} at {ctx.primary_issue.top_station.facility.replace(/\s*\(.*\)/, "")}: {pct(ctx.primary_issue.top_station.utilisation, 0)} utilised,
            {" "}{num(ctx.primary_issue.top_station.mean_wait_days)} d mean queue <Tag t="SIMULATED" />
            {ctx.delays.stall_days_mean ? <> · stalled steps {num(ctx.delays.stall_days_mean, 0)} d</> : null}
          </div>)}
        <div className="recs">
          {recs.map((r, i) => (
            <div key={r.id} className={`rec ${chosen[r.id] ? "on" : ""}`} onClick={() => setChosen(c => ({ ...c, [r.id]: !c[r.id] }))}>
              <div className="t"><span className="n">{chosen[r.id] ? "✓" : i + 1}</span>{r.title}</div>
              <div className="r">{r.rationale}</div>
              {chosen[r.id] && r.controls.map(c => {
                const k = `${r.id}:${c.path}`, v = ctrl[k] ?? c.value;
                return (<div key={k} className="ctl" onClick={e => e.stopPropagation()}>
                  {c.label}: <b className="num">{v}</b>
                  <input type="range" min={c.min} max={c.max} step={c.step} value={v} onChange={e => setCtrl(s => ({ ...s, [k]: +e.target.value }))} />
                </div>);
              })}
            </div>))}
        </div>
        <div className="nl">
          <textarea placeholder='Or describe an intervention, e.g. "Add two colonoscopy sessions per week in North Karelia and reroute patients to nearby hospitals with spare capacity"'
            value={text} onChange={e => setText(e.target.value)} onKeyDown={e => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) interpret(); }} />
          <button className="btn" onClick={interpret} disabled={parsing || !text.trim()}>{parsing ? "…" : "Interpret"}</button>
        </div>
        {parsed && (
          <div className="parsed">
            <div className="muted">Interpreted by {parsed.mode === "llm" ? "Nemotron" : "rule-based parser"} into simulator controls (no outcome predicted):</div>
            {parsed.actions.map((a, i) => <div key={i} className="a">✓ {a.label}</div>)}
            {parsed.unsupported.map((u, i) => <div key={i} className="u">✕ not modelled: {u}</div>)}
            {parsed.errors.map((u, i) => <div key={i} className="e">✕ {u}</div>)}
            {!parsed.simulatable && <div className="e">Nothing in this request maps to supported simulator controls.</div>}
            {parsed.assumptions?.SESSION_SIZE && /session/i.test(text) && <div className="muted">Assumption: 1 session ≈ {parsed.assumptions.SESSION_SIZE} patients.</div>}
          </div>)}
        <button className="btn-primary" disabled={!canRun} onClick={run}>
          {sim.running ? `Simulating… ${elapsed.toFixed(0)} s · 4 SimPy replications × ~31k patients` : "▶ Simulate intervention"}
        </button>
        {sim.running && <div className="progress"><i style={{ width: `${Math.min(97, (elapsed / 30) * 100)}%` }} /></div>}
        {sim.error && <div className="err">{sim.error}</div>}
        {!canRun && !sim.running && <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>Select one or more recommendations, or interpret a custom request.</div>}
      </div>
    </div>
  );
}
