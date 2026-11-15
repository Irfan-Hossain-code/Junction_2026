import { useCallback, useEffect, useRef, useState } from "react";
import "./ops.css";
import { Conversation } from "@elevenlabs/client";
import { API } from "./api";
import { Tag } from "./Panels";
import { VoiceOrchestrator } from "./voice/orchestrator";

// Operations command centre: ONE shared backend state (/ops/*) for map, buttons, text commands and the voice agent.
// Nothing here changes operational data except Approve / Reject (human decisions), which call the backend.

export async function ops<T = any>(path: string, body?: any): Promise<T> {
  const r = await fetch(API + path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) { let d = r.statusText; try { d = (await r.json()).detail || d; } catch { /* */ } throw new Error(typeof d === "string" ? d : JSON.stringify(d)); }
  return r.json();
}

export function useOps() {
  const [state, setState] = useState<any>(null);
  const timer = useRef<number>();
  const refresh = useCallback(() => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => ops("/ops/state").then(setState).catch(() => {}), 250);
  }, []);
  useEffect(() => {
    refresh();
    let ws: WebSocket | null = null, alive = true, retry: number | undefined;
    const connect = () => {
      ws = new WebSocket(API.replace(/^http/, "ws") + "/stream");
      ws.onmessage = e => { try { const m = JSON.parse(e.data); if (["ops", "event", "notification", "clock", "pathway_created"].includes(m.kind)) refresh(); } catch { /* */ } };
      ws.onclose = () => { if (alive) retry = window.setTimeout(connect, 3000); };
    };
    connect();
    const poll = window.setInterval(refresh, 8000);           // fallback if the socket drops
    return () => { alive = false; ws?.close(); window.clearInterval(poll); window.clearTimeout(retry); };
  }, [refresh]);
  return { state, refresh };
}

const STEPS = ["DETECTED", "PROPOSED", "EVALUATED", "AWAITING_APPROVAL", "APPROVED", "APPLIED"];
const OPT_ORDER = ["baseline", "no_action", "optimised", "admin", "keep_current", "replan"];
const OPT_SHORT: Record<string, string> = { baseline: "Before breakdown", no_action: "Local repair", optimised: "CancerFlow plan", admin: "Your proposal",
  keep_current: "Keep booking", replan: "Re-plan" };
const when = (iso?: string | null) => iso ? new Date(iso).toLocaleString("en-GB", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "–";
const f1 = (x: any, d = 1) => (x == null ? "–" : Number(x).toFixed(d));

function Lifecycle({ status }: { status: string }) {
  const bad = status === "REJECTED" || status === "FAILED" || status === "SUPERSEDED";
  const i = STEPS.indexOf(status);
  return <div className="lc">{STEPS.map((s, k) => <span key={s} className={`lc-s ${k < i || status === "APPLIED" ? "done" : k === i ? "cur" : ""}`}>{s.replace("_", " ").toLowerCase()}</span>)}
    {bad && <span className="lc-s bad">{status.toLowerCase()}</span>}</div>;
}

function Compare({ options }: { options: Record<string, any> }) {
  const keys = OPT_ORDER.filter(k => options?.[k]);
  if (!keys.length) return null;
  const rows: [string, string, string, boolean][] = [
    ["Treated within 42 d", "pct_within_target", "%", true], ["Urgent within target", "pct_urgent_within_target", "%", true],
    ["Colonoscopy (day)", "mean_colonoscopy_day", "", false], ["Treatment (day)", "mean_treatment_day", "", false],
    ["Patient travel", "patient_travel_hours", "h", false]];
  return (
    <table className="cmp">
      <thead><tr><th /> {keys.map(k => <th key={k} className={k}>{OPT_SHORT[k]}</th>)}</tr></thead>
      <tbody>{rows.map(([lbl, m, u, higher]) => {
        const vals = keys.map(k => options[k]?.kpis?.[m]);
        const cand = keys.filter(k => !["baseline"].includes(k)).map(k => options[k]?.kpis?.[m]).filter(v => v != null);
        const best = cand.length ? (higher ? Math.max(...cand) : Math.min(...cand)) : null;
        return <tr key={m}><td>{lbl}</td>{vals.map((v, i) => <td key={i} className={`num ${keys[i]} ${v === best && keys[i] !== "baseline" && m !== "patient_travel_hours" ? "best" : ""}`}>{f1(v)}{v != null ? u : ""}</td>)}</tr>;
      })}</tbody>
    </table>);
}

type Msg = { who: "you" | "cf" | "voice-you" | "voice-cf" | "sys"; text: string; bullets?: string[]; why?: string[]; mode?: string };

export default function OpsDrawer({ state, refresh, onFocus, pick, clearPick, onClose }: {
  state: any; refresh: () => void; onFocus: () => void; pick: string | null; clearPick: () => void; onClose: () => void;
}) {
  const [tab, setTab] = useState<"incident" | "story" | "feed">("incident");
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [chat, setChat] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [why, setWhy] = useState<any>(null);
  const [showWhy, setShowWhy] = useState(false);
  const [sel, setSel] = useState<string | null>(null);
  const iv = state?.intervention;
  const d = state?.disruption;

  const run = async (label: string, fn: () => Promise<any>) => {
    setBusy(label); setErr(null);
    try { const r = await fn(); refresh(); return r; } catch (e: any) { setErr(e.message); } finally { setBusy(null); }
  };

  // map click: send the selected patient to that hospital (creates a new evaluated proposal, same state as voice)
  useEffect(() => {
    if (!pick || !sel || !iv) return;
    const row = iv.rows.find((r: any) => r.patient_id === sel);
    clearPick();
    if (!row || row.to.facility_id === pick) return;
    run("Evaluating your change…", () => ops("/ops/interventions/propose", { per_patient: { [sel]: pick }, source: "map",
      note: `moved ${row.name} on the map` })).then(r => r && setChat(c => [...c, { who: "sys", text: `Map: ${row.name} → ${r.rows.find((x: any) => x.patient_id === sel)?.to.facility}` },
      { who: "cf", text: summary(r), bullets: r.explain?.bullets?.slice(0, 2), why: r.explain?.why }]));
  }, [pick]);

  useEffect(() => { setWhy(null); setShowWhy(false); }, [iv?.id]);

  const send = async (t: string) => {
    if (!t.trim()) return;
    setChat(c => [...c, { who: "you", text: t }]); setText("");
    const r = await run("Thinking…", () => ops("/ops/command", { text: t }));
    if (r) setChat(c => [...c, { who: "cf", text: r.reply, bullets: r.explain?.bullets?.slice(0, 3), why: r.explain?.why }]);
  };
  const loadWhy = async () => {
    setShowWhy(s => !s);
    if (!why && iv) setWhy(await ops("/ops/ask", { question: "In two or three sentences: why did the outcomes change compared with the alternatives?", intervention_id: iv.id }).catch(() => null));
  };

  if (!state) return <div className="ops-drawer"><div className="skeleton" /><div className="skeleton" /></div>;
  if (!state.initialised) return (
    <div className="ops-drawer">
      <div className="ops-h"><b>Operations</b><button className="btn" onClick={onClose}>✕</button></div>
      <p className="muted">The operations demo isn't initialised.</p>
      <button className="btn-primary" disabled={!!busy} onClick={() => run("Resetting…", () => ops("/ops/demo/reset", {}))}>{busy || "Set up the East Finland demo"}</button>
      {err && <div className="err">{err}</div>}
    </div>);

  const pendingFollowup = state.interventions?.find((x: any) => x.source === "followup" && x.status === "AWAITING_APPROVAL");
  const sev = d ? "crit" : iv?.status === "APPLIED" ? "ok" : "calm";
  return (
    <div className={`ops-drawer sev-${sev}`}>
      <div className="ops-h">
        <div><b>Operations</b> <span className="muted">· Eastern Finland · story day {Math.floor(state.story_day)} · {when(state.now)}</span></div>
        <button className="btn" onClick={onClose} title="Close">✕</button>
      </div>
      <div className="seg ops-tabs">
        {(["incident", "story", "feed"] as const).map(t => <button key={t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>{t === "incident" ? "Incident" : t === "story" ? "Story" : "Notifications"}</button>)}
      </div>
      {err && <div className="err" onClick={() => setErr(null)}>{err}</div>}
      {busy && <div className="busy"><span className="spinner sm" /> {busy}</div>}

      {tab === "incident" && <>
        {!d && !iv && <div className="card calm"><h4>All resources online</h4><ul className="bl">
          <li>{state.resources.filter((r: any) => r.capability === "colonoscopy").length} endoscopy units across 5 hospitals</li>
          <li>Trigger a disruption from the library below or follow the Story tab.</li></ul></div>}

        {d && <div className="card alert-crit" onClick={onFocus}>
          <h4>⚠ {d.summary.resource} · {d.summary.facility} offline</h4>
          <ul className="bl">
            <li><b>{d.summary.affected_count}</b> booked colonoscopies affected · <b>{d.summary.urgent_count}</b> urgent</li>
            <li>{when(d.starts_at)} → {when(d.ends_at)} ({d.summary.hours} h)</li>
            <li>Downstream: pathology, MDT and surgery dates move with every delayed colonoscopy</li>
          </ul></div>}

        {iv && <div className="card">
          <div className="row-sb"><h4 style={{ margin: 0 }}>{iv.title}</h4><Tag t="SIMULATED" /></div>
          <Lifecycle status={iv.status} />
          {iv.status === "DETECTED" || iv.status === "PROPOSED" ? <div className="busy"><span className="spinner sm" /> Brain is evaluating options on live free slots…</div> : null}
          {iv.controller && <div className={`ctl-box ${iv.controller.level}`}>
            <b>{iv.controller.level === "global" ? "Inner MPC → escalated to outer whole-pathway MPC" : iv.controller.level === "local" ? "Inner MPC: local repair is enough" : "Fallback rule"}</b>
            <ul className="bl">{(iv.controller.escalation_reasons || [iv.controller.reason]).slice(0, 3).map((x: string, i: number) => <li key={i}>{x}</li>)}</ul>
            {iv.state_version && <span className="muted small">plan computed on state v{iv.state_version}</span>}</div>}
          {iv.options && Object.keys(iv.options).length > 0 && <Compare options={iv.options} />}
          {iv.review && <div className={`verdict v-${iv.review.verdict}`}>
            {iv.review.verdict === "worse_than_recommendation" ? "Worse than CancerFlow's recommendation" :
              iv.review.verdict === "comparable_with_risks" ? "Comparable on target, with risks" : "As good as or better than the recommendation"}
            <ul className="bl">{iv.review.warnings.slice(0, 4).map((w: any, i: number) => <li key={i}>{w.text}</li>)}</ul></div>}
          {iv.options_followup && <div><h4>Replacement slots (priority kept)</h4>
            {iv.options_followup.map((o: any, i: number) => <div key={i} className="kv"><span className="k">{i === 0 ? "★ " : ""}{o.facility} · {o.when}{o.p_on_time != null ? ` · ${Math.round(100 * o.p_on_time)} % on time (sim.)` : ""}</span>
              <button className="btn" disabled={!!busy || iv.status !== "AWAITING_APPROVAL"} onClick={() => run("Booking…", () => ops(`/ops/followup/${iv.id}/authorise`, { option: i }))}>Authorise</button></div>)}</div>}

          {iv.rows?.length > 0 && <>
            <h4 style={{ marginTop: 8 }}>Patients <span className="muted" style={{ fontWeight: 400 }}>· select one, then click a hospital on the map to move them</span></h4>
            <div className="prow-list">{iv.rows.map((r: any) => {
              const reply = iv.replies?.find((x: any) => x.patient_id === r.patient_id);
              return <div key={r.patient_id} className={`prow ${sel === r.patient_id ? "sel" : ""} ${r.patient_id === state.emma?.patient_id ? "emma" : ""}`} onClick={() => setSel(s => s === r.patient_id ? null : r.patient_id)}>
                <span className={`pri ${r.priority}`}>{r.priority === "urgent" ? "U" : "S"}</span>
                <span className="pn">{r.name}</span>
                <span className="pt">{r.transfer ? "→ " : ""}{r.to.facility}<small> {when(r.to.at)}</small></span>
                <span className="pp num" title="simulated P(treatment within target | needs treatment)">{r.outcome?.p_on_time != null ? Math.round(100 * r.outcome.p_on_time) + "%" : ""}</span>
                {reply && <span className={`dec ${reply.decision}`}>{reply.decision}</span>}
              </div>;
            })}</div></>}

          {iv.status === "AWAITING_APPROVAL" && iv.source !== "followup" && <div className="act-row">
            <button className="btn-primary" disabled={!!busy} onClick={() => run("Applying…", () => ops(`/ops/interventions/${iv.id}/approve`, { justification: "approved in the operations panel" })).then(onFocus)}>Approve &amp; apply</button>
            <button className="btn" disabled={!!busy} onClick={() => run("Rejecting…", () => ops(`/ops/interventions/${iv.id}/reject`, { justification: "rejected in the operations panel" }))}>Reject</button>
          </div>}
          {iv.status === "APPLIED" && <ul className="bl ok"><li>Appointments re-booked in the shared database (append-only events)</li>
            <li>Receiving hospitals and patients notified</li><li>Decided by {iv.decided_by || "–"}</li></ul>}

          {iv.replies?.length > 0 && <div className="replies"><h4>Patient replies <Tag t="SYNTHETIC" /></h4>
            {iv.replies.map((r: any) => <div key={r.patient_id} className={`reply ${r.decision}`}>
              <div className="rh"><b>{r.name}</b> <span className={`dec ${r.decision}`}>{r.decision}</span> {r.llm && <span className="tag LLM">voice: llm</span>}</div>
              <div className="rm">“{r.message}”</div>
              {r.reason && <div className="muted rr">persistent circumstance: {r.reason}{r.p_accept != null ? ` · P(accept) ${Math.round(100 * r.p_accept)} %` : ""}</div>}
            </div>)}</div>}

          {iv.options && Object.keys(iv.options).length > 0 && <div className="why">
            <button className="link" onClick={loadWhy}>{showWhy ? "▾" : "▸"} Why did the results change?</button>
            {showWhy && <div>{!why ? <div className="skeleton" /> : <>
              {why.answer?.text && <p className="ans"><span className={`tag ${why.answer.mode === "fallback" ? "FALLBACK" : "LLM"}`}>{why.answer.mode}</span> {why.answer.text}</p>}
              <ul className="bl">{why.bullets.map((b: string, i: number) => <li key={i}>{b}</li>)}</ul>
              {why.why?.length > 0 && <ul className="bl warn">{why.why.map((b: string, i: number) => <li key={i}>{b}</li>)}</ul>}
            </>}</div>}
          </div>}
        </div>}

        {(iv || d) && <div className="card chat">
          <h4>Talk to CancerFlow <span className="muted" style={{ fontWeight: 400 }}>· same proposal as voice and map</span></h4>
          <div className="msgs">{chat.map((m, i) => <div key={i} className={`msg ${m.who}`}>{m.text}
            {m.bullets && m.bullets.length > 0 && <ul className="bl">{m.bullets.map((b, k) => <li key={k}>{b}</li>)}</ul>}
            {m.why && m.why.length > 0 && <details><summary>why</summary><ul className="bl">{m.why.map((b, k) => <li key={k}>{b}</li>)}</ul></details>}</div>)}</div>
          <div className="chips">{["Move half the affected patients to North Savo and half to South Savo", "Send everyone to Kuopio", "Why?", "Approve your recommendation"].map(s =>
            <button key={s} className="chip" onClick={() => send(s)} disabled={!!busy}>{s}</button>)}</div>
          <form onSubmit={e => { e.preventDefault(); send(text); }} className="cmd"><input value={text} onChange={e => setText(e.target.value)} placeholder="e.g. send Tapio to Kuopio and the rest to Mikkeli" /><button className="btn">Send</button></form>
        </div>}

        {pendingFollowup && pendingFollowup.id !== iv?.id && <div className="card alert-warn"><h4>Follow-up waiting: {pendingFollowup.title}</h4></div>}

        <div className="card"><h4>Disruption library</h4>
          <div className="lib">
            <button className="btn" disabled={!!d || !!busy} onClick={() => run("Taking the unit offline…", () => ops("/ops/story/disruption", {})).then(onFocus)}>Endoscopy Unit 2 offline · 48 h</button>
            <button className="btn" disabled={!!busy || !state.emma?.appointments?.some((a: any) => a.stage === "COLONOSCOPY" && a.status === "booked")} onClick={() => run("Recording no-show…", () => ops("/ops/story/miss", {}))}>Patient no-show (Emma)</button>
            <button className="btn" disabled title="planned">Specialist shortage (planned)</button>
          </div></div>
      </>}

      {tab === "story" && <div className="card story">
        <div className="row-sb"><h4 style={{ margin: 0 }}>Emma's pathway · director</h4>
          <button className="btn" disabled={!!busy} onClick={() => run("Resetting the demo (≈40 s)…", () => ops("/ops/demo/reset", {}))}>Reset</button></div>
        <div className="muted small">Simulated time: <b>{when(state.now)}</b> · story day {Math.floor(state.story.story_day ?? 0)}</div>
        {state.story.next && <div className="next-app">Next: <b>{state.story.steps.find((s: any) => s.key === state.story.next)?.label}</b>
          <span> · show in {state.story.steps.find((s: any) => s.key === state.story.next)?.show_in}</span></div>}
        <ol className="steps">{state.story.steps.map((s: any) => <li key={s.key} className={s.status}>
          <span className={`who ${s.actor}`}>{s.actor}</span> {s.label} <span className="ev">{s.event}</span>
          {s.status === "current" && <button className="link" disabled={!!busy} onClick={() => run(s.label + "…", () => ops(`/ops/story/${s.key}`, {})).then(r => { if (s.key === "disruption") onFocus(); return r; })}>run ▸</button>}
          {s.error && <div className="err small">failed: {s.error}</div>}</li>)}</ol>
        <button className="btn-primary" disabled={!!busy} onClick={() => run("Next step…", () => ops("/ops/story/next", {}))}>{busy || "Next step ▸"}</button>
        {state.story.last && <p className="muted">{state.story.last.message}</p>}
        <p className="muted small">Patient's phone: open the care app as Emma (<code>pat-emma</code>). Every step is a real event in the shared database.</p>
      </div>}

      {tab === "feed" && <div className="card feed">{state.notifications.map((n: any) => <div key={n.id} className={`note ${n.severity}`}>
        <div className="nh"><span className={`aud ${n.audience}`}>{n.audience.replace("_", " ")}</span> <b>{n.title}</b> <span className="muted">{when(n.created_at)}</span></div>
        <div className="nb">{n.body}</div></div>)}</div>}
    </div>);
}

function summary(r: any) {
  const v = r.review?.verdict;
  return r.status === "REJECTED" ? "Not feasible: " + (r.log?.[r.log.length - 1]?.note || "") :
    v === "worse_than_recommendation" ? "Evaluated: worse than my recommendation." : v === "comparable_with_risks" ? "Evaluated: comparable on target, but with risks." : "Evaluated: as good as or better than my recommendation.";
}

// ------------------------------------------------------------------ incoming call (ElevenLabs, in-browser)
export function CallCenter({ state, refresh, onFocus }: { state: any; refresh: () => void; onFocus: () => void }) {
  const [ring, setRing] = useState(false);
  const [call, setCall] = useState<"idle" | "connecting" | "live" | "ended" | "failed">("idle");
  const [mode, setMode] = useState<string>("listening");
  const [lines, setLines] = useState<{ who: string; text: string }[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const conv = useRef<any>(null);
  const rung = useRef<string | null>(null);
  const audio = useRef<any>(null);
  const iv = state?.intervention;

  useEffect(() => {      // ring once per new recovery plan awaiting approval
    if (iv && iv.source === "optimiser" && iv.status === "AWAITING_APPROVAL" && rung.current !== iv.id && call === "idle") {
      rung.current = iv.id; setRing(true);
    }
  }, [iv?.id, iv?.status]);
  useEffect(() => {
    if (!ring) { audio.current?.stop?.(); return; }
    try {
      const ctx = new AudioContext(); const g = ctx.createGain(); g.gain.value = 0.04; g.connect(ctx.destination);
      let stop = false;
      const beep = () => { if (stop) return; const o = ctx.createOscillator(); o.frequency.value = 660; o.connect(g); o.start(); o.stop(ctx.currentTime + 0.35);
        const o2 = ctx.createOscillator(); o2.frequency.value = 880; o2.connect(g); o2.start(ctx.currentTime + 0.4); o2.stop(ctx.currentTime + 0.75); setTimeout(beep, 2200); };
      beep(); audio.current = { stop: () => { stop = true; ctx.close(); } };
    } catch { /* autoplay blocked: silent ring */ }
    return () => audio.current?.stop?.();
  }, [ring]);

  // every client tool goes through the orchestrator: request ids, de-duplication, supersession, no output after the call
  const orch = useRef<VoiceOrchestrator | null>(null);
  const [vstate, setVstate] = useState<string>("idle");
  const execTool = async (name: string, p: any) => {
    const r = await (async () => {
      switch (name) {
        case "get_situation": return ops("/ops/voice/situation");
        case "run_mpc": return ops("/ops/voice/optimise", {});
        case "compare_plans": return ops("/ops/voice/compare");
        case "get_plan_details": return ops(`/ops/voice/plan${p?.plan_id ? `?plan_id=${encodeURIComponent(p.plan_id)}` : ""}`);
        case "propose_allocation": {        // evaluation only: never changes appointments
          const x = await ops("/ops/command", { text: `evaluate (hypothetical, do not execute): ${p.instruction}`, source: "voice" });
          return { plan_id: x.intervention?.id, status: x.intervention?.status, hypothetical: true, reply: x.reply, verdict: x.explain?.verdict,
            key_numbers: x.explain?.bullets?.slice(0, 4), reasons: x.explain?.why?.slice(0, 3) };
        }
        case "approve_plan": return ops("/ops/voice/approve", { plan_id: p?.plan_id || null, justification: p?.justification || "", donny_words: p?.donny_words || "" });
        case "check_approval": return ops(`/ops/voice/approval/${encodeURIComponent(p.plan_id)}`);
        case "set_hold": return ops("/ops/voice/hold", { hold: p?.hold !== false, reason: p?.reason || "" });
        case "reject_plan": { const x = await ops("/ops/command", { text: `reject. ${p?.reason || ""}`, source: "voice" }); return { reply: x.reply }; }
        case "explain_plan": { const x = await ops("/ops/ask", { question: p?.question || "why?" }); return { answer: x.answer?.text, key_numbers: x.bullets, reasons: x.why }; }
        default: return { unavailable: true, tool: name };
      }
    })().catch((e: any) => ({ error: e.message, error_category: "backend_unavailable" }));
    refresh();
    return r;
  };
  const TOOLS = ["get_situation", "run_mpc", "compare_plans", "get_plan_details", "propose_allocation", "approve_plan", "check_approval", "set_hold", "reject_plan", "explain_plan"];
  const answer = async () => {
    setRing(false); setCall("connecting"); setErr(null); setLines([]); onFocus();
    const o = new VoiceOrchestrator(execTool);
    o.onChange = () => setVstate(o.status);
    orch.current = o;
    try {
      const s = await ops("/ops/voice/session");
      const clientTools = Object.fromEntries(TOOLS.map(n => [n, (p: any) => o.call(n, p || {})]));
      conv.current = await Conversation.startSession({
        signedUrl: s.signed_url,
        dynamicVariables: { headline: s.dynamic_variables.headline },
        clientTools,
        onMessage: (m: any) => { if (!o.ended) setLines(l => [...l, { who: m.source === "user" || m.role === "user" ? "Donny" : "CancerFlow", text: m.message }]); },
        onModeChange: (m: any) => { setMode(m.mode); o.setMode(m.mode); },
        onConnect: (c: any) => { o.start(c?.conversationId || `s-${Date.now()}`); setCall("live"); },
        onDisconnect: () => { o.end(); setCall(c => (c === "failed" ? c : "ended")); },
        onError: (e: any) => { setErr(String(e?.message || e)); },
      } as any);
    } catch (e: any) {
      o.end();
      setCall("failed"); setErr(`Voice unavailable (${e.message}). Use "Talk to CancerFlow" in the Operations panel - same tools, same state.`);
    }
  };
  const hang = async () => { orch.current?.end(); try { await conv.current?.endSession(); } catch { /* */ } setCall("ended"); };

  if (ring) return (
    <div className="call ring">
      <div className="call-ava">CF</div>
      <div><b>Incoming call · CancerFlow Ops</b><div className="muted">for Donny Lehtonen, regional care coordinator</div>
        <div className="call-sub">{state?.disruption?.summary?.resource} offline · {state?.disruption?.summary?.affected_count} appointments affected</div></div>
      <div className="call-btns"><button className="btn ans" onClick={answer}>Answer</button><button className="btn dec" onClick={() => setRing(false)}>Decline</button></div>
    </div>);
  if (call === "idle") return iv && iv.status === "AWAITING_APPROVAL" ? <button className="call-mini" onClick={answer} title="Call CancerFlow Ops">📞 Call CancerFlow</button> : null;
  return (
    <div className={`call live ${call}`}>
      <div className="row-sb"><b>{call === "connecting" ? "Connecting…" : call === "live" ? `On call · CancerFlow is ${mode === "speaking" ? "speaking" : "listening"}` : call === "failed" ? "Voice failed" : "Call ended"}</b>
        {call === "live" || call === "connecting" ? <button className="btn dec" onClick={hang}>Hang up</button> : <button className="btn" onClick={() => setCall("idle")}>Close</button>}</div>
      {call === "live" && <div className={`wave ${mode}`}><i /><i /><i /><i /><i /></div>}
      <div className="muted small">state: {vstate.replace(/_/g, " ")}{orch.current?.sessionId ? ` · session ${orch.current.sessionId.slice(-6)}` : ""}</div>
      {err && <div className="err small">{err}</div>}
      <div className="call-lines">{lines.slice(-6).map((l, i) => <div key={i} className={`cl ${l.who === "Donny" ? "you" : "cf"}`}><b>{l.who}:</b> {l.text}</div>)}</div>
      <div className="muted small">Every number comes from backend tools · nothing executes without your explicit yes</div>
    </div>);
}
