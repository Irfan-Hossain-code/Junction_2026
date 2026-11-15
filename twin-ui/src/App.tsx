import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AgentDetail, AgentSummary, Alarm, CAP_LABEL, Context, HcFlow, Network, Scenario, Selection, Summary, api, pct, shortCounty } from "./api";
import MapView, { LayersOn, MapMode, legendFor } from "./MapView";
import { AIPanel, CompliancePanel, DelayPanel, InterventionStudio, Tag } from "./Panels";
import SelectionCard from "./SelectionCard";
import Optimiser from "./Optimiser";
import OpsDrawer, { CallCenter, useOps } from "./Ops";

type View = "original" | "modified" | "diff";
type Twin = "baseline" | "disruption" | "recovery" | "history";
const CAPS = ["colonoscopy", "ct_imaging", "mdt_crc", "crc_surgery", "oncology", "radiotherapy"];

function useCache<T>() {
  const ref = useRef(new Map<string, Promise<T>>());
  return useCallback((key: string, fn: () => Promise<T>) => {
    if (!ref.current.has(key)) ref.current.set(key, fn().catch(e => { ref.current.delete(key); throw e; }));
    return ref.current.get(key)!;
  }, []);
}

export default function App() {
  const [geo, setGeo] = useState<any>(null);
  const [hcs, setHcs] = useState<any[]>([]);
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [modScenario, setModScenario] = useState<string | null>(null);
  const [twin, setTwin] = useState<Twin>("baseline");     // the three living twins (+ "history" = any stored run)
  const [diffOn, setDiffOn] = useState(false);
  const [histOpen, setHistOpen] = useState(false);
  const [chatAlarm, setChatAlarm] = useState<Alarm | null>(null);
  const [selection, setSelection] = useState<Selection>({ scope: "national" });
  const [capability, setCapability] = useState("colonoscopy");
  const [mapMode, setMapMode] = useState<MapMode>("sim");
  const [net, setNet] = useState<Network | null>(null);
  const [baseNet, setBaseNet] = useState<Network | null>(null);
  const [ctx, setCtx] = useState<Context | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [loadingNet, setLoadingNet] = useState(false);
  const [loadingSum, setLoadingSum] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ctlOpen, setCtlOpen] = useState(false);
  const [optOpen, setOptOpen] = useState(false);
  const [privateClinics, setPrivateClinics] = useState<any[]>([]);
  const [alarms, setAlarms] = useState<Alarm[]>([]);
  const [selectedAlarm, setSelectedAlarm] = useState<Alarm | null>(null);
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [agentPid, setAgentPid] = useState<number | null>(null);
  const [agent, setAgent] = useState<AgentDetail | null>(null);
  const [agentLoading, setAgentLoading] = useState(false);
  const [explain, setExplain] = useState<any>(null);
  const [hcFlows, setHcFlows] = useState<HcFlow[]>([]);
  const [aiTab, setAiTab] = useState<"analysis" | "alarms" | "patient">("analysis");
  const [layersOn, setLayersOn] = useState<LayersOn>({ primary: true, private: false, patients: true, alarms: true });
  const netCache = useCache<Network>();
  const { state: opsState, refresh: opsRefresh } = useOps();
  const [opsOpen, setOpsOpen] = useState(false);
  const [opsFocus, setOpsFocus] = useState(0);
  const [opsPick, setOpsPick] = useState<string | null>(null);
  const focusOps = () => { setOpsOpen(true); setOpsFocus(n => n + 1); };

  const baseline = scenarios.find(s => s.is_baseline);
  const baseRun = baseline?.latest_run ?? null;
  const mod = scenarios.find(s => s.id === modScenario);
  const twinRun = (k: string) => scenarios.find(s => s.twin === k)?.latest_run ?? null;
  const runs: Record<string, string | null> = { baseline: baseRun, disruption: twinRun("disruption"), recovery: twinRun("recovery") };
  const modRun = twin === "history" ? (mod?.latest_run ?? null) : twin === "baseline" ? null : runs[twin];
  const view: View = twin === "baseline" || !modRun ? "original" : diffOn ? "diff" : "modified";
  const activeRun = view !== "original" && modRun ? modRun : baseRun;
  // Disruption is compared with Baseline; MPC Recovery with the SAME disruption (the controller's value)
  const compareBase = view === "original" ? null : twin === "recovery" ? (runs.disruption ?? baseRun) : baseRun;
  const cmpRun = compareBase ?? baseRun;

  const loadScenarios = useCallback(async (prefer?: string) => {
    const s = await api.scenarios();
    setScenarios(s);
    const candidates = s.filter(x => !x.is_baseline && x.latest_run);
    setModScenario(m => prefer ?? m ?? (candidates.find(x => x.name.startsWith("Demo")) ?? candidates[0])?.id ?? null);
  }, []);

  useEffect(() => {
    api.geo().then(setGeo).catch(e => setError(`Cannot reach the CancerFlow API: ${e.message}`));
    api.healthCentres().then(setHcs).catch(() => {});
    api.privateClinics().then(setPrivateClinics).catch(() => {});
    loadScenarios().catch(e => setError(`Cannot reach the CancerFlow API: ${e.message}`));
  }, []);

  // networks (active + base for comparison), cached per run/capability
  useEffect(() => {
    if (!activeRun) return;
    setLoadingNet(true);
    const a = netCache(`${activeRun}|${capability}`, () => api.network(activeRun, capability));
    const b = cmpRun ? netCache(`${cmpRun}|${capability}`, () => api.network(cmpRun, capability)) : Promise.resolve(null as any);
    Promise.all([a, b]).then(([n, bn]) => { setNet(n); setBaseNet(bn); }).catch(e => setError(e.message)).finally(() => setLoadingNet(false));
  }, [activeRun, cmpRun, capability]);

  // panels + AI summary follow selection and scenario
  useEffect(() => {
    if (!activeRun) return;
    let live = true;
    setCtx(null);
    api.context(selection, activeRun, compareBase).then(c => live && setCtx(c)).catch(e => live && setError(e.message));
    setLoadingSum(true); setSummary(null);
    api.summary(selection, activeRun, compareBase).then(s => live && setSummary(s)).catch(() => {}).finally(() => live && setLoadingSum(false));
    return () => { live = false; };
  }, [selection.scope, selection.id, activeRun, compareBase]);

  useEffect(() => { if (view === "diff") setMapMode("diff"); else if (mapMode === "diff") setMapMode("sim"); }, [view]);

  // network alarms (whole network for the map; scoped list comes with the context)
  useEffect(() => {
    if (!activeRun) return;
    api.anomalies(activeRun, compareBase).then(r => setAlarms(r.alarms)).catch(() => setAlarms([]));
  }, [activeRun, compareBase]);
  const selCounty = selection.scope === "county" ? selection.id : selection.scope === "facility" ? net?.facilities.find(f => f.id === selection.id)?.county : undefined;
  // patient agents + primary-care flows at regional zoom
  useEffect(() => {
    if (!activeRun || !selCounty) { setAgents([]); setHcFlows([]); return; }
    api.agents(activeRun, selCounty).then(r => setAgents(r.patients)).catch(() => setAgents([]));
    api.hcFlows(activeRun, selCounty).then(r => setHcFlows(r.flows)).catch(() => setHcFlows([]));
  }, [activeRun, selCounty]);
  // selected agent: same pid in the other scenario (common random numbers) for comparison
  const otherRun = view === "original" ? modRun : cmpRun;
  useEffect(() => {
    if (agentPid == null || !activeRun) { setAgent(null); return; }
    setAgentLoading(true); setExplain(null);
    api.agent(activeRun, agentPid, otherRun && otherRun !== activeRun ? otherRun : null)
      .then(setAgent).catch(() => setAgent(null)).finally(() => setAgentLoading(false));
  }, [agentPid, activeRun, otherRun]);
  const onExplain = () => {
    if (agentPid == null || !activeRun) return;
    setExplain("loading");
    api.explainAgent(activeRun, agentPid, otherRun && otherRun !== activeRun ? otherRun : null).then(setExplain).catch(() => setExplain(null));
  };
  const onSelectAgent = (pid: number) => { setAgentPid(pid); setAiTab("patient"); };
  const onSelectAlarm = (a: Alarm) => {
    setSelectedAlarm(s => (s?.id === a.id ? null : a)); setAiTab("alarms");
    if (a.location.kind === "county" && a.location.county_id) setSelection({ scope: "county", id: a.location.county_id });
    else if (a.location.facility_id) setSelection({ scope: "facility", id: a.location.facility_id });
  };
  const scopedAlarms = ctx?.anomalies ?? alarms;

  const onSimulated = async (runId: string, scenarioId: string) => {
    await loadScenarios(scenarioId);
    setModScenario(scenarioId);
    setTwin("history");
  };

  const headline = useMemo(() => {
    if (!baseNet || !net || view === "original") return null;
    const a = baseNet.national.sim["referral_to_treatment.p_within_target"]?.mean, b = net.national.sim["referral_to_treatment.p_within_target"]?.mean;
    if (a == null || b == null) return null;
    let local: { a: number; b: number; nm: string } | null = null;
    if (selection.scope !== "national") {
      const cid = selection.scope === "county" ? selection.id : net.facilities.find(f => f.id === selection.id)?.county;
      const ca = baseNet.counties.find(c => c.id === cid)?.sim["referral_to_treatment.p_within_target"]?.mean;
      const cb = net.counties.find(c => c.id === cid)?.sim["referral_to_treatment.p_within_target"]?.mean;
      const nm = net.counties.find(c => c.id === cid)?.name;
      if (ca != null && cb != null && nm) local = { a: ca, b: cb, nm: shortCounty(nm) };
    }
    return { a, b, local };
  }, [baseNet, net, view, selection]);

  const crumbCounty = selection.scope === "county" ? selection.id : selection.scope === "facility" ? net?.facilities.find(f => f.id === selection.id)?.county : undefined;
  const legend = legendFor(mapMode);

  return (
    <div className="app">
      <div className="topbar">
        <div className="brand"><span className="dot" />CancerFlow<small>National colorectal cancer pathway twin · Finland</small></div>
        <button className="btn opt-btn" onClick={() => setOptOpen(true)}>National optimiser</button>
        {optOpen && <Optimiser onClose={() => setOptOpen(false)} />}
        <button className={`btn ops-btn ${opsState?.disruption ? "alarm" : ""}`} onClick={() => (opsOpen ? setOpsOpen(false) : focusOps())}>
          {opsState?.disruption ? "● " : ""}Operations</button>
        <div className="spacer" />
        {headline ? (
          <div className="delta-chip">
            <Tag t="SIMULATED" />
            <span>Finland within 42 d: <b className="num">{pct(headline.a)} → {pct(headline.b)}</b> <b className={headline.b >= headline.a ? "up" : "down"}>({headline.b >= headline.a ? "+" : ""}{(100 * (headline.b - headline.a)).toFixed(1)} pp)</b></span>
            {headline.local && <span>· {headline.local.nm}: <b className="num">{pct(headline.local.a, 0)} → {pct(headline.local.b, 0)}</b></span>}
          </div>) : <span className="meta">Original (baseline) scenario · pathway pack colorectal v1 · model crc-abm-0.4 (behavioural agents)</span>}
        <div className="spacer" />
        <span className="meta" title={mod?.name}>{mod ? `Modified = ${mod.name.slice(0, 60)}${mod.name.length > 60 ? "…" : ""}` : "no modified scenario yet"}</span>
      </div>
      {error && <div style={{ position: "fixed", bottom: 12, left: "50%", transform: "translateX(-50%)", zIndex: 99, background: "#3b1418", border: "1px solid var(--bad)", padding: "8px 14px", borderRadius: 8 }}
        onClick={() => setError(null)}>{error} (click to dismiss)</div>}
      <div className="grid">
        <div className="area-map" style={{ position: "relative" }}>
          <MapView geo={geo} net={net} baseNet={baseNet} healthCentres={hcs} selection={selection} onSelect={setSelection}
            mode={mapMode} capability={capability} loading={loadingNet || !net}
            privateClinics={privateClinics} alarms={alarms} selectedAlarm={selectedAlarm} onSelectAlarm={onSelectAlarm}
            agents={agents} agent={agent} onSelectAgent={onSelectAgent} hcFlows={hcFlows} layersOn={layersOn}
            ops={opsOpen ? opsState : null} onOpsFacility={setOpsPick} opsFocus={opsFocus} />
          {opsOpen && <OpsDrawer state={opsState} refresh={opsRefresh} onFocus={focusOps} pick={opsPick} clearPick={() => setOpsPick(null)} onClose={() => setOpsOpen(false)} />}
          <CallCenter state={opsState} refresh={opsRefresh} onFocus={focusOps} />

          <div className="overlay crumbs">
            <button onClick={() => setSelection({ scope: "national" })}>Finland</button>
            {crumbCounty && <><span className="sep">›</span>
              {selection.scope === "county" ? <span className="cur">{shortCounty(net?.counties.find(c => c.id === crumbCounty)?.name || "")}</span>
                : <button onClick={() => setSelection({ scope: "county", id: crumbCounty })}>{shortCounty(net?.counties.find(c => c.id === crumbCounty)?.name || "")}</button>}</>}
            {selection.scope === "facility" && <><span className="sep">›</span><span className="cur">{net?.facilities.find(f => f.id === selection.id)?.name.replace(/\s*\(.*\)/, "")}</span></>}
          </div>

          <div className="overlay toggle-wrap">
            <div className="seg twins">
              <button className={twin === "baseline" ? "on" : ""} onClick={() => setTwin("baseline")} title="Normal Finland: the reference simulation">Baseline</button>
              <button className={twin === "disruption" ? "on bad" : ""} disabled={!runs.disruption} onClick={() => setTwin("disruption")}
                title="SYNTHETIC referral surge in Southern Finland (same capacity)">{runs.disruption ? "Disruption" : "Disruption (computing…)"}</button>
              <button className={twin === "recovery" ? "on mod" : ""} disabled={!runs.recovery} onClick={() => setTwin("recovery")}
                title="Same surge + CancerFlow's optimised response">{runs.recovery ? "MPC Recovery" : "MPC Recovery (computing…)"}</button>
              <button className={`diff-t ${diffOn && twin !== "baseline" ? "on diff" : ""}`} disabled={twin === "baseline"} onClick={() => setDiffOn(d => !d)}
                title="Colour the map by the change versus the comparison twin">Δ {twin === "recovery" ? "vs Disruption" : "vs Baseline"}</button>
            </div>
          </div>

          {selection.scope === "national" && (
            <div className="overlay scenario-pick">
              <button className="link" onClick={() => setHistOpen(o => !o)}>{histOpen ? "▾" : "▸"} Advanced · simulation history</button>
              {histOpen && <select value={twin === "history" ? (modScenario ?? "") : ""} onChange={e => { setModScenario(e.target.value || null); setTwin(e.target.value ? "history" : "baseline"); }}>
                <option value="">— choose a stored run —</option>
                {scenarios.filter(s => !s.is_baseline && !s.twin && s.latest_run).map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>}
            </div>)}

          {!(agent && aiTab === "patient") && <SelectionCard selection={selection} net={net} baseNet={view !== "original" ? baseNet : null} ctx={ctx}
            onClose={() => setSelection({ scope: "national" })} onSelect={setSelection} />}

          <div className={`overlay layer-ctl ${ctlOpen ? "" : "min"}`}>
            <button className="toggle-ctl" onClick={() => setCtlOpen(o => !o)}>{ctlOpen ? "▾ Layers & legend" : `▸ Layers · ${CAP_LABEL[capability]}`}</button>
            {ctlOpen && <>
            <div className="row"><span className="muted" style={{ width: 54 }}>Regions</span>
              {view !== "diff" ? (<>
                <button className={`chip ${mapMode === "sim" ? "on" : ""}`} onClick={() => setMapMode("sim")}>Within 42 d</button>
                <button className={`chip ${mapMode === "obs:sotkanet:3336" ? "on" : ""}`} onClick={() => setMapMode("obs:sotkanet:3336")}>Specialist backlog</button>
                <button className={`chip ${mapMode === "obs:sotkanet:6103" ? "on" : ""}`} onClick={() => setMapMode("obs:sotkanet:6103")}>Screening</button>
              </>) : <span className="chip on">Δ vs original</span>}
            </div>
            <div className="row"><span className="muted" style={{ width: 54 }}>Network</span>
              {CAPS.map(c => <button key={c} className={`chip ${capability === c ? "on" : ""}`} onClick={() => setCapability(c)}>{CAP_LABEL[c]}</button>)}
            </div>
            <div className="row"><span className="muted" style={{ width: 54 }}>Show</span>
              {([["primary", "Primary care"], ["private", "Private clinics"], ["patients", "Patient agents"], ["alarms", "Alarms"]] as const).map(([k, l]) =>
                <button key={k} className={`chip ${layersOn[k] ? "on" : ""}`} onClick={() => setLayersOn(s => ({ ...s, [k]: !s[k] }))}>{l}</button>)}
            </div>
            </>}
            <div>
              <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                <div className="ramp" style={{ background: `linear-gradient(90deg, ${legend.stops.map(([, c]) => `rgb(${c.join(",")})`).join(",")})` }} />
              </div>
              <div className="legend-labels"><span>{legend.lo}</span><span>{legend.hi}</span></div>
              <div className="muted" style={{ fontSize: 10.5 }}>{legend.label} {mapMode.startsWith("obs") ? <Tag t="OBSERVED" /> : <Tag t="SIMULATED" />}</div>
              {ctlOpen && <div className="muted" style={{ fontSize: 10.5, marginTop: 3 }}>
                ● hospital: size = synthetic {CAP_LABEL[capability].toLowerCase()} capacity, colour = utilisation (<span style={{ color: "var(--accent)" }}>ok</span> / <span style={{ color: "var(--warn)" }}>&gt;80%</span> / <span style={{ color: "var(--bad)" }}>&gt;93%</span>) ·
                <span style={{ color: "var(--teal)" }}> ━</span> in-county / <span style={{ color: "var(--warn)" }}>━</span> cross-county flow · · health centre (OSM)
              </div>}
            </div>
          </div>
        </div>

        <AIPanel ctx={ctx} summary={summary} loading={loadingSum} view={view === "original" ? "Original" : view === "modified" ? "Modified" : "Difference"}
          tab={aiTab} setTab={setAiTab} alarms={scopedAlarms} selectedAlarm={selectedAlarm} onAlarm={onSelectAlarm}
          chat={{ scope: selection.scope, id: selection.scope === "national" ? undefined : selection.id, run_id: activeRun ?? null, base_run_id: compareBase ?? null, twin }}
          chatAlarm={chatAlarm} setChatAlarm={setChatAlarm}
          agent={agent} agentLoading={agentLoading} explain={explain} onExplain={onExplain} />
        <CompliancePanel ctx={ctx} onSelect={setSelection} view={view === "original" ? "Original" : "Modified"} />
        <DelayPanel ctx={ctx} />
        <InterventionStudio ctx={ctx} selection={selection} onSimulated={onSimulated} />
      </div>
    </div>
  );
}
