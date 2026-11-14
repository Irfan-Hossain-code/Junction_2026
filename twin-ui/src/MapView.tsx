import { useEffect, useMemo, useRef, useState } from "react";
import DeckGL from "@deck.gl/react";
import { FlyToInterpolator, WebMercatorViewport } from "@deck.gl/core";
import { GeoJsonLayer, PathLayer, ScatterplotLayer, TextLayer } from "@deck.gl/layers";
import { Map } from "react-map-gl/maplibre";
import "maplibre-gl/dist/maplibre-gl.css";
import { CAP_LABEL, FacilityNode, Network, Selection, num, pct, shortCounty } from "./api";

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
};

export default function MapView({ geo, net, baseNet, healthCentres, selection, onSelect, mode, capability, loading }: Props) {
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
    // keep the selection card (right, ~340 px) from covering the zoomed region
    const right = padding > 30 && size.width > 800 ? 350 : p;
    const { longitude, latitude, zoom } = vp.fitBounds(b, { padding: { top: p + 30, bottom: p, left: p, right } });
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
        getLineColor: (f: any) => (f.id === selCounty ? [125, 211, 252, 255] : [90, 130, 180, 110]),
        getLineWidth: (f: any) => (f.id === selCounty ? 2.5 : 0.8), lineWidthUnits: "pixels",
        updateTriggers: { getFillColor: [mode, net?.run_id, baseNet?.run_id, selCounty], getLineColor: [selCounty], getLineWidth: [selCounty] },
        transitions: { getFillColor: 600 },
        onClick: (i: any) => onSelect({ scope: "county", id: i.object.id }),
      }),
      new ScatterplotLayer({
        id: "health-centres", data: healthCentres, getPosition: (d: any) => [d.lon, d.lat], getRadius: 900,
        radiusMinPixels: 1.1, radiusMaxPixels: 3, getFillColor: [148, 170, 205, 110], pickable: false,
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
          const k = Math.max(1, Math.min(4, Math.round(f.flow_per_week / 3)));
          return Array.from({ length: k }, (_, i) => ({ f, off: i / k }));
        }),
        getPosition: (d: any) => along(d.f.path, ((tick / 4000) * (0.6 + d.f.seed) + d.off + d.f.seed) % 1),
        getRadius: 1, radiusUnits: "pixels", radiusMinPixels: 1.8,
        getFillColor: (d: any) => (d.f.cross_county ? [255, 200, 110, 230] : [150, 245, 230, 220]),
        updateTriggers: { getPosition: [tick] },
      }),
      new ScatterplotLayer({
        id: "hospital-glow", data: hospitals, getPosition: (d: FacilityNode) => [d.lon, d.lat],
        getRadius: (d: FacilityNode) => 5500 + 2600 * Math.sqrt(capUtil(d, capability)?.capacity_per_week ?? 1),
        radiusMinPixels: 9, getFillColor: (d: FacilityNode) => [...utilColor(capUtil(d, capability)?.sim.utilisation?.mean), 45] as RGBA,
        updateTriggers: { getFillColor: [net?.run_id, capability], getRadius: [net?.run_id, capability] }, transitions: { getFillColor: 600, getRadius: 600 },
      }),
      new ScatterplotLayer({
        id: "hospital-pulse",
        data: hospitals.filter(d => (capUtil(d, capability)?.sim.utilisation?.mean ?? 0) > 0.93 || d.live.critical_alerts > 0),
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
        getFillColor: (d: FacilityNode) => [...utilColor(capUtil(d, capability)?.sim.utilisation?.mean), capUtil(d, capability) ? 235 : 120] as RGBA,
        getLineColor: (d: FacilityNode) => (selection.scope === "facility" && d.id === selection.id ? [255, 255, 255, 255] : [8, 14, 26, 255]),
        getLineWidth: (d: FacilityNode) => (selection.scope === "facility" && d.id === selection.id ? 3 : 1), lineWidthUnits: "pixels",
        updateTriggers: { getFillColor: [net?.run_id, capability], getRadius: [net?.run_id, capability], getLineColor: [selection.id], getLineWidth: [selection.id] },
        transitions: { getFillColor: 600, getRadius: 600 },
        onClick: (i: any) => onSelect({ scope: "facility", id: i.object.id }),
      }),
      new TextLayer({
        id: "labels", data: viewState.zoom >= 5.4 ? hospitals.filter(h => h.kind !== "regional_hospital" || viewState.zoom >= 6.5) : [],
        getPosition: (d: FacilityNode) => [d.lon, d.lat], getText: (d: FacilityNode) => d.name.replace(/\s*\(.*\)/, ""),
        getSize: 11, getColor: [200, 215, 235, 220], getPixelOffset: [0, -16], fontFamily: "Inter, sans-serif", fontWeight: 600,
        outlineWidth: 3, outlineColor: [5, 10, 20, 255], fontSettings: { sdf: true },
      }),
    ];
  }, [geo, net, baseNet, healthCentres, flows, tick, mode, selection.scope, selection.id, selCounty, capability, viewState.zoom >= 5.4, viewState.zoom >= 6.5]);

  const tooltip = ({ object, layer }: any) => {
    if (!object) return null;
    const style = { background: "rgba(9,16,30,.95)", border: "1px solid #283754", borderRadius: "8px", color: "#e7eef8", fontSize: "12px", padding: "8px 10px" };
    if (layer.id === "counties") {
      const c = counties[object.id], v = countyValue(object.id);
      const lbl = mode === "sim" ? pct(v) + " within 42 d (simulated)" : mode === "diff" ? (v == null ? "–" : `${(100 * v).toFixed(1)} pp vs original (simulated)`)
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
    </div>
  );
}
