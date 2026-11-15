import { useEffect, useMemo, useRef, useState } from "react";
import DeckGL from "@deck.gl/react";
import { FlyToInterpolator, WebMercatorViewport } from "@deck.gl/core";
import { GeoJsonLayer, PathLayer, ScatterplotLayer, TextLayer } from "@deck.gl/layers";
import { Map } from "react-map-gl/maplibre";
import "maplibre-gl/dist/maplibre-gl.css";
import { AgentDetail, AgentSummary, Alarm, CAP_LABEL, FacilityNode, HcFlow, Network, Selection, num, pct, shortCounty } from "./api";

export type LayersOn = { primary: boolean; private: boolean; patients: boolean; alarms: boolean };
const niceUnit = (x: number) => [0.25, 0.5, 1, 2, 5, 10, 20, 50].find(u => u >= x) ?? 100;
const SEV: Record<string, number[]> = { critical: [239, 83, 80], warning: [245, 165, 36], info: [148, 163, 184] };
const outcomeColor = (a: AgentSummary): number[] =>
  a.lost ? [148, 60, 60] : a.rtt_status === "met" ? [45, 212, 191] : a.rtt_status === "missed" ? [239, 83, 80] : [120, 140, 170];

const STYLE = "https://basemaps.cartocdn.com/gl/dark-matter-nolabels-gl-style/style.json";
const FIN_BOUNDS: [[number, number], [number, number]] = [[19.8, 59.6], [31.6, 70.1]];
type RGBA = [number, number, number, number];

export type MapMode = "sim" | "diff" | "obs:sotkanet:3336" | "obs:sotkanet:6103" | "obs:sotkanet:6411";

// ---------------------------------------------------------------- colour scales
function lerp(a: number[], b: number[], t: number) { return a.map((x, i) => x + (b[i] - x) * t); }
function ramp(stops: [number, number[]][], v: number): number[] {
  if (v <= stops[0][0]) return stops[0][1];
  for (let i = 1; i < stops.length; i++) {
    if (v <= stops[i][0]) return lerp(stops[i - 1][1], stops[i][1], (v - stops[i - 1][0]) / (stops[i][0] - stops[i - 1][0]));
  }
  return stops[stops.length - 1][1];
}
const BAD = [239, 83, 80], WARN = [245, 165, 36], MID = [52, 66, 92], GOOD = [45, 212, 191], GREEN = [52, 211, 153];
export const WITHIN_STOPS: [number, number[]][] = [[0.3, BAD], [0.6, WARN], [0.8, MID], [0.95, GOOD]];
export const DIFF_STOPS: [number, number[]][] = [[-0.1, BAD], [0, MID], [0.3, GREEN]];
const OBS: Record<string, { stops: [number, number[]][]; label: string; fmt: (v: number) => string }> = {
  "obs:sotkanet:3336": { stops: [[5, GOOD], [40, MID], [100, BAD]], label: "Waited > 6 mo for specialist care /10k (THL, all specialties)", fmt: v => v.toFixed(0) },
  "obs:sotkanet:6103": { stops: [[69, BAD], [73, MID], [78, GOOD]], label: "Colorectal screening participation % (THL)", fmt: v => v.toFixed(1) + "%" },
  "obs:sotkanet:6411": { stops: [[35, GOOD], [55, MID], [80, BAD]], label: "GP non-urgent visits waiting > 7 d % (THL)", fmt: v => v.toFixed(0) + "%" },
};
export const legendFor = (mode: MapMode) =>
  mode === "sim" ? { stops: WITHIN_STOPS, label: "Simulated % treated within 42 d (reference target)", lo: "30%", hi: "95%" }
    : mode === "diff" ? { stops: DIFF_STOPS, label: "Modified − Original, percentage points (simulated)", lo: "−10 pp", hi: "+30 pp" }
      : { stops: OBS[mode].stops, label: OBS[mode].label, lo: String(OBS[mode].stops[0][0]), hi: String(OBS[mode].stops[OBS[mode].stops.length - 1][0]) };

const utilColor = (u: number | undefined | null): number[] =>
  u == null ? [120, 135, 160] : u > 0.93 ? BAD : u > 0.8 ? WARN : [56, 189, 248];

// quadratic bezier between two points, bent sideways -> reads as a network edge, not a straight line
function curve(a: [number, number], b: [number, number], bend = 0.18, n = 24): [number, number][] {
  const mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2;
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const c: [number, number] = [mx - dy * bend, my + dx * bend];
  const pts: [number, number][] = [];
  for (let i = 0; i <= n; i++) {
    const t = i / n, u = 1 - t;
    pts.push([u * u * a[0] + 2 * u * t * c[0] + t * t * b[0], u * u * a[1] + 2 * u * t * c[1] + t * t * b[1]]);
  }
  return pts;
}
function along(path: [number, number][], t: number): [number, number] {
  const f = t * (path.length - 1), i = Math.floor(f), r = f - i;
  const a = path[Math.min(i, path.length - 1)], b = path[Math.min(i + 1, path.length - 1)];
  return [a[0] + (b[0] - a[0]) * r, a[1] + (b[1] - a[1]) * r];
}

function bbox(geom: any): [[number, number], [number, number]] {
  let x0 = 180, y0 = 90, x1 = -180, y1 = -90;
  const walk = (c: any) => {
    if (typeof c[0] === "number") { x0 = Math.min(x0, c[0]); x1 = Math.max(x1, c[0]); y0 = Math.min(y0, c[1]); y1 = Math.max(y1, c[1]); }
    else c.forEach(walk);
  };
  walk(geom.coordinates);
  return [[x0, y0], [x1, y1]];
}

export function capUtil(f: FacilityNode, cap: string) {
  return f.capabilities.find(c => c.capability === cap);
}

type Props = {
  geo: any; net: Network | null; baseNet: Network | null; healthCentres: any[];
  selection: Selection; onSelect: (s: Selection) => void; mode: MapMode; capability: string; loading: boolean;
  privateClinics: any[]; alarms: Alarm[]; selectedAlarm: Alarm | null; onSelectAlarm: (a: Alarm) => void;
  agents: AgentSummary[]; agent: AgentDetail | null; onSelectAgent: (pid: number) => void; hcFlows: HcFlow[]; layersOn: LayersOn;
  ops?: any; onOpsFacility?: (facilityId: string) => void; opsFocus?: number;
};

// Operations overlay (shared /ops state): resource status, affected patients, proposed vs applied routes
const EAST_BOUNDS: [[number, number], [number, number]] = [[25.2, 61.3], [31.2, 63.9]];
function opsLayers(ops: any, tick: number, onFacility?: (f: string) => void) {
  if (!ops?.initialised) return [];
  const byFac: Record<string, any> = {};
  for (const r of ops.resources || []) {
    if (r.capability !== "colonoscopy") continue;
    const f = byFac[r.facility_id] || (byFac[r.facility_id] = { id: r.facility_id, short: r.short, lon: r.lon, lat: r.lat, offline: 0, units: [], free: 0, slots: 0 });
    f.units.push(r); f.free += r.free_72h; f.slots += r.slots_72h; if (r.status === "offline") f.offline++;
  }
  const facs = Object.values(byFac);
  const iv = ops.intervention;
  const applied = iv?.status === "APPLIED";
  const live = iv && ["EVALUATED", "AWAITING_APPROVAL", "APPLIED", "APPROVED"].includes(iv.status) && iv.rows?.length;
  const routes = live ? iv.rows.filter((r: any) => r.home?.lon && r.to?.facility_id).map((r: any) => {
    const to = byFac[r.to.facility_id];
    return to ? { ...r, path: [[r.home.lon, r.home.lat], [to.lon, to.lat]] } : null;
  }).filter(Boolean) : [];
  const affected = (ops.disruption?.summary?.affected || []).length ? (iv?.rows || []) : [];
  const pulse = 0.5 + 0.5 * Math.sin(tick / 260);
  const emmaId = ops.emma?.patient_id;
  return [
    new ScatterplotLayer({
      id: "ops-pulse", data: facs.filter((f: any) => f.offline), getPosition: (f: any) => [f.lon, f.lat],
      getRadius: 9000 + 9000 * pulse, radiusUnits: "meters", radiusMinPixels: 14 + 10 * pulse, filled: true,
      getFillColor: [239, 83, 80, Math.round(60 + 60 * (1 - pulse))], updateTriggers: { getRadius: tick, getFillColor: tick },
    }),
    new PathLayer({
      id: "ops-routes", data: routes, getPath: (d: any) => d.path, widthUnits: "pixels",
      getWidth: (d: any) => (d.transfer ? 3.5 : 2), getColor: (d: any) => applied ? (d.transfer ? [52, 211, 153, 230] : [125, 211, 252, 170])
        : (d.transfer ? [245, 165, 36, 220] : [148, 163, 184, 120]), pickable: true,
      updateTriggers: { getColor: [applied, iv?.id] },
    }),
    new ScatterplotLayer({
      id: "ops-patients", data: affected.filter((r: any) => r.home?.lon), pickable: true, getPosition: (r: any) => [r.home.lon, r.home.lat],
      getRadius: (r: any) => (r.patient_id === emmaId ? 9 : 6), radiusUnits: "pixels", stroked: true, lineWidthUnits: "pixels", getLineWidth: 2,
      getFillColor: (r: any) => r.patient_id === emmaId ? [232, 121, 249, 255] : r.priority === "urgent" ? [251, 146, 60, 255] : [226, 232, 240, 230],
      getLineColor: [8, 14, 26, 255], updateTriggers: { getRadius: emmaId, getFillColor: emmaId },
    }),
    new ScatterplotLayer({
      id: "ops-facilities", data: facs, pickable: true, getPosition: (f: any) => [f.lon, f.lat], radiusUnits: "pixels",
      getRadius: (f: any) => 9 + Math.min(10, f.free / 2), stroked: true, lineWidthUnits: "pixels", getLineWidth: 3, filled: true,
      getFillColor: (f: any) => f.offline ? [239, 83, 80, 255] : f.free === 0 ? [245, 165, 36, 255] : [52, 211, 153, 255],
      getLineColor: [255, 255, 255, 230], onClick: (i: any) => onFacility && onFacility(i.object.id),
      updateTriggers: { getFillColor: [ops.now], getRadius: [ops.now] },
    }),
    new TextLayer({
      id: "ops-labels", data: [...facs.map((f: any) => ({ p: [f.lon, f.lat], t: `${f.short}${f.offline ? " · UNIT OFFLINE" : ` · ${f.free} free / 72 h`}`, bad: f.offline })),
        ...affected.filter((r: any) => r.patient_id === emmaId && r.home?.lon).map((r: any) => ({ p: [r.home.lon, r.home.lat], t: "Emma", emma: true }))],
      getPosition: (d: any) => d.p, getText: (d: any) => d.t, getSize: (d: any) => (d.emma ? 13 : 12), fontWeight: 700,
      getColor: (d: any) => d.bad ? [255, 140, 140, 255] : d.emma ? [240, 171, 252, 255] : [230, 240, 250, 240], getPixelOffset: (d: any) => d.emma ? [0, -16] : [0, 20],
      fontFamily: "Inter, sans-serif", outlineWidth: 3, outlineColor: [5, 10, 20, 255], fontSettings: { sdf: true },
    }),
  ];
}

export default function MapView({ geo, net, baseNet, healthCentres, selection, onSelect, mode, capability, loading,
  privateClinics, alarms, selectedAlarm, onSelectAlarm, agents, agent, onSelectAgent, hcFlows, layersOn, ops, onOpsFacility, opsFocus }: Props) {
  const wrap = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 900, height: 600 });
  const [viewState, setViewState] = useState<any>({ longitude: 25.6, latitude: 64.9, zoom: 4.2, pitch: 0, bearing: 0 });
  const [tick, setTick] = useState(0);
  const fitted = useRef(false);

  useEffect(() => {
    const ro = new ResizeObserver(([e]) => setSize({ width: e.contentRect.width, height: e.contentRect.height }));
    if (wrap.current) ro.observe(wrap.current);
    return () => ro.disconnect();
  }, []);

  const fit = (b: [[number, number], [number, number]], padding = 50, duration = 900) => {
    if (size.width < 50) return;
    const vp = new WebMercatorViewport({ width: size.width, height: size.height });
    const p = Math.min(padding, size.height / 4);
    // keep the selection card (right, ~340 px) from covering the zoomed region;
    // on phones the card is a bottom sheet (~44 % of the map) and the toggles take ~90 px at the top
    const phone = size.width <= 760;
    const right = padding > 30 && size.width > 800 ? 350 : p;
    const top = phone ? 90 : p + 30;
    const bottom = phone && padding > 30 ? Math.round(size.height * 0.46) : p;
    const { longitude, latitude, zoom } = vp.fitBounds(b, { padding: { top, bottom, left: p, right: phone ? p : right } });
    setViewState((v: any) => ({ ...v, longitude, latitude, zoom, transitionDuration: duration, transitionInterpolator: new FlyToInterpolator({ speed: 1.6 }) }));
  };

  // initial fit + camera follows SELECTION only (never the Original/Modified toggle)
  useEffect(() => { if (!fitted.current && size.width > 50) { fit(FIN_BOUNDS, 20, 0); fitted.current = true; } }, [size.width]);
  useEffect(() => {
    if (!geo) return;
    if (selection.scope === "national") fit(FIN_BOUNDS, 20);
    else if (selection.scope === "county") {
      const f = geo.features.find((x: any) => x.id === selection.id);
      if (f) fit(bbox(f.geometry), 60);
    } else if (selection.scope === "facility" && net) {
      const f = net.facilities.find(x => x.id === selection.id);
      if (f) setViewState((v: any) => ({ ...v, longitude: f.lon, latitude: f.lat, zoom: Math.max(v.zoom, 7), transitionDuration: 900, transitionInterpolator: new FlyToInterpolator({ speed: 1.6 }) }));
    }
  }, [selection.scope, selection.id, geo]);

  // operations: fly to Eastern Finland when the presenter focuses the ops view (disruption, approval)
  useEffect(() => { if (opsFocus) fit(EAST_BOUNDS, 40); }, [opsFocus]);

  // particle animation (~30 fps)
  useEffect(() => {
    let raf = 0, last = 0;
    const loop = (t: number) => { if (t - last > 33) { setTick(t); last = t; } raf = requestAnimationFrame(loop); };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, []);

  const counties = useMemo(() => Object.fromEntries((net?.counties || []).map(c => [c.id, c])), [net]);
  const baseCounties = useMemo(() => Object.fromEntries((baseNet?.counties || []).map(c => [c.id, c])), [baseNet]);
  const facilities = useMemo(() => Object.fromEntries((net?.facilities || []).map(f => [f.id, f])), [net]);
  const selCounty = selection.scope === "county" ? selection.id : selection.scope === "facility" ? facilities[selection.id!]?.county : undefined;

  const countyValue = (id: string): number | null => {
    const c = counties[id];
    if (!c) return null;
    if (mode === "sim") return c.sim["referral_to_treatment.p_within_target"]?.mean ?? null;
    if (mode === "diff") {
      const a = baseCounties[id]?.sim["referral_to_treatment.p_within_target"]?.mean, b = c.sim["referral_to_treatment.p_within_target"]?.mean;
      return a == null || b == null ? null : b - a;
    }
    return c.indicators[mode.slice(4)]?.value ?? null;
  };
  const stops = mode === "sim" ? WITHIN_STOPS : mode === "diff" ? DIFF_STOPS : OBS[mode].stops;

  const flows = useMemo(() => {
    if (!net) return [];
    return net.flows.filter(f => f.flow_per_week >= 0.25 && counties[f.from_county] && facilities[f.to_facility]).map(f => {
      const c = counties[f.from_county], h = facilities[f.to_facility];
      const path = curve([c.lon, c.lat], [h.lon, h.lat], f.cross_county ? 0.22 : 0.12);
      return { ...f, path, seed: (f.from_county.charCodeAt(5) * 13 + f.to_facility.length * 7) % 100 / 100 };
    });
  }, [net, counties, facilities]);
  const flowsOn = (f: any) => !selCounty || f.from_county === selCounty || (selection.scope === "facility" && f.to_facility === selection.id);

  // particle scale: one particle = `unit` simulated referrals / week (shown in the legend badge)
  const visibleFlow = flows.filter(flowsOn).reduce((s, f: any) => s + f.flow_per_week, 0);
  const unit = niceUnit(visibleFlow / 260);
  const hcPaths = useMemo(() => (layersOn.primary && selCounty ? hcFlows : []).filter(f => f.flow_per_week >= 0.05).map(f => ({
    ...f, path: curve(f.from, f.to, 0.1, 16), seed: (f.hc.length * 31 + f.hc.charCodeAt(f.hc.length - 1)) % 100 / 100,
  })), [hcFlows, layersOn.primary, selCounty]);
  const hcUnit = niceUnit(hcPaths.reduce((s, f) => s + f.flow_per_week, 0) / 120);
  const alarmPos = (a: Alarm): [number, number] | null => {
    if (a.location.lon != null) return [a.location.lon!, a.location.lat!];
    const c = counties[a.location.county_id || ""];
    return c ? [c.lon, c.lat] : null;
  };
  const hiNodes = new Set(selectedAlarm ? selectedAlarm.nodes : []);
  const hiCounties = new Set(selectedAlarm ? [...(selectedAlarm.counties || []), ...(selectedAlarm.location.kind === "county" ? [selectedAlarm.location.county_id] : [])] : []);
  const journey = useMemo(() => {
    if (!agent) return [];
    const pts = (p: AgentDetail["patient"]) => {
      const seq: [number, number][] = [[agent.home.lon, agent.home.lat]];
      for (const e of p.journey) {
        const f = e.facility && agent.facilities[e.facility];
        if (f && (e.kind === "stage" || e.kind === "attendance") && (seq[seq.length - 1][0] !== f.lon || seq[seq.length - 1][1] !== f.lat)) seq.push([f.lon, f.lat]);
      }
      return seq;
    };
    const out = [{ id: "current", path: pts(agent.patient), color: [255, 255, 255, 230] }];
    if (agent.compare) out.push({ id: "compare", path: pts(agent.compare), color: [167, 139, 250, 200] });
    return out;
  }, [agent]);

  const layers = useMemo(() => {
    if (!geo) return [];
    const hospitals = (net?.facilities || []).filter(f => f.kind !== "health_center");
    return [
      new GeoJsonLayer({
        id: "counties", data: geo, pickable: true, stroked: true, filled: true,
        getFillColor: (f: any) => {
          const v = countyValue(f.id);
          const rgb = v == null ? [30, 40, 58] : ramp(stops, v);
          const a = selCounty ? (f.id === selCounty ? 170 : 60) : 135;
          return [...rgb, a] as RGBA;
        },
        getLineColor: (f: any) => (hiCounties.has(f.id) ? [245, 165, 36, 255] : f.id === selCounty ? [125, 211, 252, 255] : [90, 130, 180, 110]),
        getLineWidth: (f: any) => (hiCounties.has(f.id) ? 3.5 : f.id === selCounty ? 2.5 : 0.8), lineWidthUnits: "pixels",
        updateTriggers: { getFillColor: [mode, net?.run_id, baseNet?.run_id, selCounty], getLineColor: [selCounty, selectedAlarm?.id], getLineWidth: [selCounty, selectedAlarm?.id] },
        transitions: { getFillColor: 600 },
        onClick: (i: any) => onSelect({ scope: "county", id: i.object.id }),
      }),
      new ScatterplotLayer({
        id: "health-centres", data: layersOn.primary ? healthCentres : [], getPosition: (d: any) => [d.lon, d.lat], getRadius: 900,
        radiusMinPixels: selCounty ? 2 : 1.1, radiusMaxPixels: 4,
        getFillColor: (d: any) => (selCounty && d.region_id === selCounty ? [125, 211, 252, 220] : [148, 170, 205, 110]), pickable: true,
        updateTriggers: { getFillColor: [selCounty] },
      }),
      new ScatterplotLayer({
        id: "private-clinics", data: layersOn.private ? privateClinics : [], getPosition: (d: any) => [d.lon, d.lat], getRadius: 800,
        radiusMinPixels: 1.4, radiusMaxPixels: 3.5, getFillColor: [192, 132, 252, 150], pickable: true,
      }),
      new PathLayer({
        id: "hc-flows", data: hcPaths, getPath: (d: any) => d.path, widthUnits: "pixels",
        getWidth: (d: any) => Math.max(0.7, Math.min(4, Math.sqrt(d.flow_per_week) * 1.6)), getColor: [125, 211, 252, 110], pickable: true,
      }),
      new ScatterplotLayer({
        id: "hc-particles",
        data: hcPaths.flatMap((f: any) => {
          const k = Math.max(1, Math.min(8, Math.round(f.flow_per_week / hcUnit)));
          return Array.from({ length: k }, (_, i) => ({ f, off: i / k }));
        }),
        getPosition: (d: any) => along(d.f.path, ((tick / 3500) * (0.7 + d.f.seed) + d.off + d.f.seed) % 1),
        getRadius: 1, radiusUnits: "pixels", radiusMinPixels: 1.5, getFillColor: [186, 230, 253, 230],
        updateTriggers: { getPosition: [tick] },
      }),
      new PathLayer({
        id: "flows", data: flows, getPath: (d: any) => d.path, widthUnits: "pixels",
        getWidth: (d: any) => Math.max(1, Math.min(8, Math.sqrt(d.flow_per_week) * 1.4)),
        getColor: (d: any) => (d.cross_county ? [245, 165, 36, flowsOn(d) ? 170 : 25] : [45, 212, 191, flowsOn(d) ? 120 : 18]),
        capRounded: true, jointRounded: true,
        updateTriggers: { getColor: [selCounty, selection.id] },
        pickable: true,
      }),
      new ScatterplotLayer({
        id: "particles",
        data: flows.filter(flowsOn).flatMap((f: any) => {
          const k = Math.max(1, Math.min(24, Math.round(f.flow_per_week / unit)));
          return Array.from({ length: k }, (_, i) => ({ f, off: i / k }));
        }),
        getPosition: (d: any) => along(d.f.path, ((tick / 4000) * (0.6 + d.f.seed) + d.off + d.f.seed) % 1),
        getRadius: 1, radiusUnits: "pixels", radiusMinPixels: 1.8,
        getFillColor: (d: any) => (d.f.cross_county ? [255, 200, 110, 230] : [150, 245, 230, 220]),
        updateTriggers: { getPosition: [tick] },
      }),
      new ScatterplotLayer({
        id: "hospital-glow", data: mode === "diff" ? [] : hospitals, getPosition: (d: FacilityNode) => [d.lon, d.lat],
        getRadius: (d: FacilityNode) => 5500 + 2600 * Math.sqrt(capUtil(d, capability)?.capacity_per_week ?? 1),
        radiusMinPixels: 9, getFillColor: (d: FacilityNode) => [...utilColor(capUtil(d, capability)?.sim.utilisation?.mean), 45] as RGBA,
        updateTriggers: { getFillColor: [net?.run_id, capability], getRadius: [net?.run_id, capability] }, transitions: { getFillColor: 600, getRadius: 600 },
      }),
      new ScatterplotLayer({
        id: "hospital-pulse",
        // absolute-load pulse is meaningless in the Difference view (that is what made Raahe "glow red" there)
        data: mode === "diff" ? [] : hospitals.filter(d => (capUtil(d, capability)?.sim.utilisation?.mean ?? 0) > 0.93 || d.live.critical_alerts > 0),
        getPosition: (d: FacilityNode) => [d.lon, d.lat], stroked: true, filled: false, lineWidthMinPixels: 1.5,
        getRadius: () => 6000 + ((tick / 18) % 60) * 450, radiusMinPixels: 8,
        getLineColor: () => [239, 83, 80, Math.max(0, 220 - ((tick / 18) % 60) * 3.6)],
        updateTriggers: { getRadius: [tick], getLineColor: [tick] },
      }),
      new ScatterplotLayer({
        id: "hospitals", data: hospitals, pickable: true, stroked: true,
        getPosition: (d: FacilityNode) => [d.lon, d.lat],
        getRadius: (d: FacilityNode) => 2600 + 1300 * Math.sqrt(capUtil(d, capability)?.capacity_per_week ?? 1),
        radiusMinPixels: 3.5, radiusMaxPixels: 22, lineWidthMinPixels: 1,
        // Difference view: colour = CHANGE in utilisation vs the comparison twin (green relieved, red more loaded, grey unchanged)
        getFillColor: (d: FacilityNode) => {
          const u = capUtil(d, capability)?.sim.utilisation?.mean;
          if (mode === "diff" && baseNet) {
            const b = baseNet.facilities.find(x => x.id === d.id);
            const ub = b ? capUtil(b, capability)?.sim.utilisation?.mean : undefined;
            if (u == null || ub == null) return [110, 120, 140, 120] as RGBA;
            const dv = u - ub;
            return (dv <= -0.03 ? [52, 211, 153, 240] : dv >= 0.03 ? [239, 83, 80, 240] : [130, 140, 160, 200]) as RGBA;
          }
          return [...utilColor(u), capUtil(d, capability) ? 235 : 120] as RGBA;
        },
        getLineColor: (d: FacilityNode) => (selection.scope === "facility" && d.id === selection.id ? [255, 255, 255, 255] : [8, 14, 26, 255]),
        getLineWidth: (d: FacilityNode) => (selection.scope === "facility" && d.id === selection.id ? 3 : 1), lineWidthUnits: "pixels",
        updateTriggers: { getFillColor: [net?.run_id, baseNet?.run_id, capability, mode], getRadius: [net?.run_id, capability], getLineColor: [selection.id], getLineWidth: [selection.id] },
        transitions: { getFillColor: 600, getRadius: 600 },
        onClick: (i: any) => onSelect({ scope: "facility", id: i.object.id }),
      }),
      new ScatterplotLayer({
        id: "alarm-highlight", data: (net?.facilities || []).filter(f => hiNodes.has(f.id)), getPosition: (d: FacilityNode) => [d.lon, d.lat],
        stroked: true, filled: false, getRadius: 14000, radiusMinPixels: 16, lineWidthMinPixels: 2.5, getLineColor: [245, 165, 36, 255],
      }),
      new ScatterplotLayer({
        id: "agents", data: layersOn.patients ? agents : [], pickable: true, getPosition: (d: AgentSummary) => [d.lon, d.lat],
        getRadius: 1, radiusUnits: "pixels", radiusMinPixels: 3, stroked: true, lineWidthMinPixels: 1,
        getFillColor: (d: AgentSummary) => [...outcomeColor(d), 230] as RGBA,
        getLineColor: (d: AgentSummary) => (agent && d.pid === agent.patient.pid ? [255, 255, 255, 255] : d.declined || d.missed ? [245, 165, 36, 255] : [8, 14, 26, 200]),
        getLineWidth: (d: AgentSummary) => (agent && d.pid === agent.patient.pid ? 3 : 1), lineWidthUnits: "pixels",
        updateTriggers: { getLineColor: [agent?.patient.pid], getLineWidth: [agent?.patient.pid] },
        onClick: (i: any) => onSelectAgent(i.object.pid),
      }),
      new PathLayer({
        id: "journey", data: journey, getPath: (d: any) => d.path, getColor: (d: any) => d.color, widthUnits: "pixels",
        getWidth: (d: any) => (d.id === "current" ? 3 : 2), jointRounded: true, capRounded: true,
      }),
      new ScatterplotLayer({
        id: "alarms", data: layersOn.alarms && mode !== "diff" ? alarms.filter(a => alarmPos(a)) : [], pickable: true,
        getPosition: (a: Alarm) => alarmPos(a)!, getRadius: 1, radiusUnits: "pixels",
        radiusMinPixels: 9, stroked: true, lineWidthMinPixels: 2,
        getFillColor: (a: Alarm) => [...SEV[a.severity], selectedAlarm?.id === a.id ? 255 : 200] as RGBA,
        getLineColor: (a: Alarm) => (selectedAlarm?.id === a.id ? [255, 255, 255, 255] : [8, 14, 26, 255]),
        getPixelOffset: [14, -14], updateTriggers: { getFillColor: [selectedAlarm?.id], getLineColor: [selectedAlarm?.id] },
        onClick: (i: any) => onSelectAlarm(i.object),
      }),
      new TextLayer({
        id: "alarm-glyph", data: layersOn.alarms && mode !== "diff" ? alarms.filter(a => alarmPos(a)) : [], getPosition: (a: Alarm) => alarmPos(a)!,
        getText: () => "!", getSize: 13, getColor: [10, 14, 24, 255], fontWeight: 800, getPixelOffset: [14, -14], fontFamily: "Inter, sans-serif",
      }),
      new TextLayer({
        id: "labels", data: viewState.zoom >= 5.4 ? hospitals.filter(h => h.kind !== "regional_hospital" || viewState.zoom >= 6.5) : [],
        getPosition: (d: FacilityNode) => [d.lon, d.lat], getText: (d: FacilityNode) => d.name.replace(/\s*\(.*\)/, ""),
        getSize: 11, getColor: [200, 215, 235, 220], getPixelOffset: [0, -16], fontFamily: "Inter, sans-serif", fontWeight: 600,
        outlineWidth: 3, outlineColor: [5, 10, 20, 255], fontSettings: { sdf: true },
      }),
      ...opsLayers(ops, tick, onOpsFacility),
    ];
  }, [geo, net, baseNet, healthCentres, privateClinics, flows, hcPaths, tick, mode, selection.scope, selection.id, selCounty, capability,
    viewState.zoom >= 5.4, viewState.zoom >= 6.5, alarms, selectedAlarm, agents, agent, journey, layersOn, unit, hcUnit, ops]);

  const tooltip = ({ object, layer }: any) => {
    if (!object) return null;
    const style = { background: "rgba(9,16,30,.95)", border: "1px solid #283754", borderRadius: "8px", color: "#e7eef8", fontSize: "12px", padding: "8px 10px" };
    if (layer.id === "counties") {
      const c = counties[object.id], v = countyValue(object.id);
      const lbl = mode === "sim" ? pct(v) + " within 42 d (simulated)" : mode === "diff" ? (v == null ? "–" : `${(100 * v).toFixed(1)} pp vs comparison twin (simulated)`)
        : v == null ? "–" : OBS[mode].fmt(v) + " (observed)";
      return { html: `<b>${shortCounty(object.properties.name)}</b><br/>${lbl}${c ? `<br/><span style="color:#8da2c0">pop. ${Math.round(c.population).toLocaleString()}</span>` : ""}`, style };
    }
    if (layer.id === "hospitals") {
      const cu = capUtil(object, capability);
      return { html: `<b>${object.name}</b><br/>${CAP_LABEL[capability]}: ${cu ? `${pct(cu.sim.utilisation?.mean, 0)} utilised · queue ${num(cu.sim.mean_wait_days?.mean)} d` : "not available"}<br/><span style="color:#8da2c0">${object.live.open_alerts} open alerts (synthetic ops)</span>`, style };
    }
    if (layer.id === "flows") {
      return { html: `${shortCounty(counties[object.from_county]?.name || object.from_county)} → ${facilities[object.to_facility]?.name}<br/>${object.flow_per_week.toFixed(1)} patients/week (${CAP_LABEL[object.capability]}, simulated)`, style };
    }
    if (layer.id === "hc-flows") return { html: `GP referrals: ${object.hc_name} → ${facilities[object.facility]?.name || object.facility}<br/>${object.flow_per_week.toFixed(2)} /week (simulated)`, style };
    if (layer.id === "health-centres") return { html: `${object.name}<br/><span style="color:#8da2c0">health centre · primary care (OSM)</span>`, style };
    if (layer.id === "private-clinics") return { html: `${object.name}<br/><span style="color:#8da2c0">private / occupational first contact (OSM) · not routed by default</span>`, style };
    if (layer.id === "alarms") return { html: `<b style="color:${object.severity === "critical" ? "#ef5350" : "#f5a524"}">${object.severity.toUpperCase()}</b> · ${object.title}<br/><span style="color:#8da2c0">click for evidence</span>`, style };
    if (layer.id === "ops-facilities") return { html: `<b>${object.short}</b><br/>${object.units.map((u: any) => `${u.name}: ${u.status === "offline" ? "<b style='color:#ef5350'>OFFLINE</b>" : `${u.free_72h} free of ${u.slots_72h} slots (72 h)`}`).join("<br/>")}<br/><span style="color:#8da2c0">click to send the selected patient here</span>`, style };
    if (layer.id === "ops-patients") return { html: `<b>${object.name}</b> · ${object.priority}<br/>${object.from.facility} → <b>${object.to.facility}</b>${object.to.at ? ` · ${new Date(object.to.at).toLocaleString("en-GB", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}` : ""}<br/>P(accept) ${Math.round(100 * (object.accept_prob ?? 1))} % · P(on time) ${object.outcome?.p_on_time != null ? Math.round(100 * object.outcome.p_on_time) + " %" : "–"} <span style="color:#8da2c0">(simulated)</span>`, style };
    if (layer.id === "ops-routes") return { html: `${object.name}: ${object.home?.muni} → ${object.to.facility} · ${Math.round(object.travel_min || 0)} min travel`, style };
    if (layer.id === "agents") return { html: `Synthetic patient #${object.pid} · ${object.archetype.replace(/_/g, " ")}, ${object.age} y${object.urgent ? " · urgent" : ""}<br/>${object.lost ? "lost to follow-up" : object.rtt_days != null ? `referral → treatment ${object.rtt_days} d (${object.rtt_status})` : "no treatment needed / in progress"}${object.declined ? ` · declined ${object.declined} offer(s)` : ""}${object.missed ? ` · missed ${object.missed}` : ""}`, style };
    return null;
  };

  return (
    <div className="map-wrap" ref={wrap}>
      <DeckGL viewState={viewState} onViewStateChange={({ viewState: v }: any) => setViewState(v)} controller={{ doubleClickZoom: false }}
        layers={layers} getTooltip={tooltip} getCursor={({ isHovering }: any) => (isHovering ? "pointer" : "grab")}
        onClick={(info: any) => { if (!info.object) onSelect({ scope: "national" }); }}>
        <Map mapStyle={STYLE} attributionControl={false} />
      </DeckGL>
      {loading && <div className="overlay loading-veil"><div className="spinner" /></div>}
      <div className="overlay particle-key">
        <span><i style={{ background: "#96f5e6" }} /> 1 particle ≈ {unit} simulated {CAP_LABEL[capability].toLowerCase()} referrals/week</span>
        {hcPaths.length > 0 && <span><i style={{ background: "#bae6fd" }} /> 1 particle ≈ {hcUnit} GP referrals/week</span>}
        {layersOn.patients && agents.length > 0 && <span><i style={{ background: "#2dd4bf" }} /> {agents.length} sampled synthetic patients (1 in 10)</span>}
      </div>
    </div>
  );
}
