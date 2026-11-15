import React from "react";
import map from "../data/map.json";
import { system } from "../theme";

export const MAP = map as {
  width: number; height: number;
  counties: { id: string; name: string; d: string }[];
  hospitals: { id: string; name: string; x: number; y: number }[];
  demo_hospitals: { id: string; name: string; x: number; y: number }[];
  patients: { name: string; muni: string; priority: string; emma: boolean; x: number; y: number }[];
};

/** Finland in projected map units (export_map.py). Camera = centre (cx, cy) in map units + zoom (px per unit). */
export const FinlandMap: React.FC<{
  cx: number; cy: number; zoom: number; width?: number; height?: number; highlightCounty?: string[]; countyOpacity?: number;
  children?: (p: (x: number, y: number) => [number, number], zoom: number) => React.ReactNode;
}> = ({ cx, cy, zoom, width = 1920, height = 1080, highlightCounty = [], countyOpacity = 1, children }) => {
  const tx = width / 2 - cx * zoom, ty = height / 2 - cy * zoom;
  const p = (x: number, y: number): [number, number] => [tx + x * zoom, ty + y * zoom];
  return (
    <div style={{ position: "absolute", inset: 0 }}>
      <svg width={width} height={height} style={{ position: "absolute", inset: 0 }}>
        <g transform={`translate(${tx} ${ty}) scale(${zoom})`} opacity={countyOpacity}>
          {MAP.counties.map((c) => (
            <path key={c.id} d={c.d} fill={highlightCounty.includes(c.id) ? "#13233d" : "#0d1729"} stroke={system.border2} strokeWidth={1.2 / zoom} />
          ))}
        </g>
      </svg>
      {children?.(p, zoom)}
    </div>
  );
};

export const demoHospital = (idPart: string) => MAP.demo_hospitals.find((h) => h.id.includes(idPart))!;
