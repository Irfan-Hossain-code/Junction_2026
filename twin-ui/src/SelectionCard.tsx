import { CAP_LABEL, Context, Network, Selection, num, pct, shortCounty } from "./api";
import { Tag } from "./Panels";

const ucol = (u?: number | null) => (u == null ? "#4b5a75" : u > 0.93 ? "var(--bad)" : u > 0.8 ? "var(--warn)" : "var(--accent)");

export default function SelectionCard({ selection, net, baseNet, ctx, onClose, onSelect }: {
  selection: Selection; net: Network | null; baseNet: Network | null; ctx: Context | null;
  onClose: () => void; onSelect: (s: Selection) => void;
}) {
  if (selection.scope === "national" || !net) return null;
  const compare = baseNet && baseNet.run_id !== net.run_id;

  if (selection.scope === "facility") {
    const f = net.facilities.find(x => x.id === selection.id);
    if (!f) return null;
    const bf = compare ? baseNet!.facilities.find(x => x.id === f.id) : undefined;
    const county = net.counties.find(c => c.id === f.county);
    const inflow = net.flows.filter(x => x.to_facility === f.id).sort((a, b) => b.flow_per_week - a.flow_per_week);
    return (
      <div className="overlay sel-card">
        <button className="close" onClick={onClose}>×</button>
        <h4>{f.name}</h4>
        <div className="sub">{f.kind.replace("_", " ")} · <a style={{ color: "var(--accent)", cursor: "pointer" }} onClick={() => onSelect({ scope: "county", id: f.county })}>{county ? shortCounty(county.name) : f.county}</a> · location <Tag t="OBSERVED" /></div>
        <table className="captable">
          <thead><tr><th>Resource</th><th className="r">Slots/wk</th><th>Util.</th><th className="r">Queue</th></tr></thead>
          <tbody>
            {f.capabilities.filter(c => c.capability !== "pathology").map(c => {
              const u = c.sim.utilisation?.mean, w = c.sim.mean_wait_days?.mean;
              const bc = bf?.capabilities.find(x => x.capability === c.capability);
              const bw = bc?.sim.mean_wait_days?.mean;
              return (
                <tr key={c.capability}>
                  <td>{CAP_LABEL[c.capability]}</td>
                  <td className="r num">{c.capacity_per_week.toFixed(1)}{bc && Math.abs(bc.capacity_per_week - c.capacity_per_week) > 0.05 && <span className="up"> (was {bc.capacity_per_week.toFixed(1)})</span>}</td>
                  <td><span className="util-bar"><i style={{ width: `${Math.min(100, 100 * (u ?? 0))}%`, background: ucol(u) }} /></span> <span className="num">{pct(u, 0)}</span></td>
                  <td className="r num">{num(w)} d{bw != null && w != null && Math.abs(bw - w) > 0.2 && <span className={w < bw ? "up" : "down"}> ({bw.toFixed(1)})</span>}</td>
                </tr>);
            })}
          </tbody>
        </table>
        <div className="muted" style={{ fontSize: 10.5, marginTop: 4 }}>Capacity <Tag t="SYNTHETIC" /> · utilisation &amp; queue <Tag t="SIMULATED" />{compare && " · (original in brackets)"}</div>
        <div className="kv" style={{ marginTop: 8 }}>
          <span className="k">Active pathways (ops)</span><span className="v num">{f.live.active_pathways}</span>
          <span className="k">Open alerts · critical</span><span className="v num" style={{ color: f.live.critical_alerts ? "var(--bad)" : undefined }}>{f.live.open_alerts} · {f.live.critical_alerts}</span>
        </div>
        {inflow.length > 0 && (<>
          <div className="section-l">Incoming {CAP_LABEL[net.capability].toLowerCase()} flows <Tag t="SIMULATED" /></div>
          <div className="kv">{inflow.slice(0, 6).map(x => {
            const c = net.counties.find(k => k.id === x.from_county);
            return [<span key={x.from_county + "k"} className="k">{c ? shortCounty(c.name) : x.from_county}</span>,
              <span key={x.from_county + "v"} className="v num">{x.flow_per_week.toFixed(1)}/wk</span>];
          })}</div>
        </>)}
      </div>);
  }

  const c = net.counties.find(x => x.id === selection.id);
  if (!c) return null;
  const bc = compare ? baseNet!.counties.find(x => x.id === c.id) : undefined;
  const p = c.sim["referral_to_treatment.p_within_target"]?.mean, bp = bc?.sim["referral_to_treatment.p_within_target"]?.mean;
  const hosps = net.facilities.filter(f => f.county === c.id);
  const ind = (k: string) => c.indicators[k];
  return (
    <div className="overlay sel-card">
      <button className="close" onClick={onClose}>×</button>
      <h4>{shortCounty(c.name)}</h4>
      <div className="sub">Wellbeing services county · pop. {Math.round(c.population).toLocaleString()} <Tag t="OBSERVED" /></div>
      <div className="kv">
        <span className="k">Within 42 d <Tag t="SIMULATED" /></span>
        <span className="v num">{pct(p)}{bp != null && p != null && <span className={p >= bp ? "up" : "down"}> ({pct(bp)})</span>}</span>
        <span className="k">Median referral → treatment</span><span className="v num">{num(c.sim["referral_to_treatment.median_days"]?.mean, 0)} d</span>
      </div>
      <div className="section-l">Observed (THL Sotkanet) <Tag t="OBSERVED" /></div>
      <div className="kv">
        {ind("sotkanet:6103") && <><span className="k">Colorectal screening participation</span><span className="v num">{ind("sotkanet:6103").value.toFixed(1)}% ({ind("sotkanet:6103").year})</span></>}
        {ind("sotkanet:3627") && <><span className="k">Bowel cancer 5-yr survival</span><span className="v num">{ind("sotkanet:3627").value.toFixed(0)}% ({ind("sotkanet:3627").year})</span></>}
        {ind("sotkanet:3336") && <><span className="k">Waited &gt; 6 mo, specialist /10k</span><span className="v num">{ind("sotkanet:3336").value.toFixed(0)} ({ind("sotkanet:3336").year})</span></>}
        {ind("sotkanet:6411") && <><span className="k">GP visit wait &gt; 7 d</span><span className="v num">{ind("sotkanet:6411").value.toFixed(0)}% ({ind("sotkanet:6411").year})</span></>}
      </div>
      {ctx?.live && <div className="kv"><span className="k">Live open alerts (synthetic ops)</span><span className="v num">{ctx.live.open_alerts} · {ctx.live.critical_alerts} critical</span></div>}
      <div className="section-l">Hospitals in county · click to inspect</div>
      {hosps.map(h => {
        const cu = h.capabilities.find(x => x.capability === net.capability);
        const u = cu?.sim.utilisation?.mean;
        return (
          <div key={h.id} className="bar-row click" style={{ gridTemplateColumns: "1fr 64px 40px" }} onClick={() => onSelect({ scope: "facility", id: h.id })}>
            <span className="bl">{h.name.replace(/\s*\(.*\)/, "")}</span>
            <span className="util-bar" style={{ width: 64 }}><i style={{ width: `${Math.min(100, 100 * (u ?? 0))}%`, background: ucol(u) }} /></span>
            <span className="bv num">{pct(u, 0)}</span>
          </div>);
      })}
      <div className="muted" style={{ fontSize: 10.5, marginTop: 6 }}>Bars: {CAP_LABEL[net.capability]} utilisation <Tag t="SIMULATED" /></div>
    </div>);
}
