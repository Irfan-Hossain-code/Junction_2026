import React from "react";
import { Easing, interpolate, useCurrentFrame } from "remotion";
import { clamp, pop, ramp, s, windowed } from "../anim";
import { Calendar } from "../components/Calendar";
import { demoHospital, FinlandMap, MAP } from "../components/FinlandMap";
import { SceneFrame } from "../components/Frame";
import { PathwayTimeline, type TNode } from "../components/PathwayTimeline";
import { blinkAt, LOOKS, Person, POSES } from "../components/Person";
import { World } from "../components/Places";
import { ActLabel, EventChip, Tag, Toast } from "../components/ui";
import { SCENES } from "../config";
import { MSG } from "../data/messages";
import { D, fmtDay, fmtTime, step, story } from "../data/story";
import { FONT, MONO, system } from "../theme";

const cfg = SCENES.find((x) => x.id === "S3-Disruption")!;
export const B3 = { colo: 0.4, dark: 5.1, board: 5.8, outage: 10.1, zoom: 13.8, end: 18.6 };   // aligned to the recorded voice-over (see config.ts)

// Unit 2 bookings in the outage window (backend/ops/world.py PERSONAS[].slot; Emma = story day 7 slot 0). 4 slots/day → 08:00, 09:45, 11:30, 13:15
const TIMES = ["08:00", "09:45", "11:30", "13:15"];
const first = (n: string) => n.split(" ")[0];
const LEFT_LABEL = new Set(["Emma", "Jari"]);   // labels placed left of the dot to avoid overlaps around Joensuu
const others = story.outage.others;
const UNIT2: { day: 0 | 1; i: number; name: string; urgent: boolean; emma?: boolean }[] = [
  ...others.slice(0, 4).map((p, i) => ({ day: 0 as const, i, name: first(p.name), urgent: p.priority === "urgent" })),
  { day: 1, i: 0, name: "Emma", urgent: true, emma: true },
  ...others.slice(4).map((p, i) => ({ day: 1 as const, i: i + 1, name: first(p.name), urgent: p.priority === "urgent" })),
];

export const S3Disruption: React.FC = () => {
  const f = useCurrentFrame();
  const t = f / 30;
  const dark = ramp(f, B3.dark, 0.8);
  const down = ramp(f, B3.outage, 0.35);
  const boardO = windowed(f, B3.board, B3.zoom, 0.5);
  const mapO = ramp(f, B3.zoom - 0.2, 0.8);

  const nodes: TNode[] = [
    { label: "GP visit", date: fmtDay(D.gp), states: [[0, "done"]] },
    { label: "Referral", date: fmtDay(D.referral), states: [[0, "done"]] },
    { label: "Specialist", date: fmtDay(D.specialist), states: [[0, "done"]] },
    { label: "Colonoscopy", date: fmtTime(D.firstColo), sub: "Endoscopy Unit 2", states: [[B3.colo, "current"]] },
    { label: "Diagnosis", states: [] },
    { label: "Treatment", states: [] },
  ];

  // map camera: tight on Joensuu → East Finland
  const pk = demoHospital("pkks");
  const z = interpolate(f, [s(B3.zoom), s(B3.zoom + 3.2)], [14, 4.3], { ...clamp, easing: Easing.inOut(Easing.cubic) });
  const cy = interpolate(f, [s(B3.zoom), s(B3.zoom + 3.2)], [pk.y, pk.y - 8], clamp);
  const cx = interpolate(f, [s(B3.zoom), s(B3.zoom + 3.2)], [pk.x, pk.x - 40], clamp);

  return (
    <SceneFrame cfg={cfg}>
      <World groundY={930} tint={0} />
      {/* A: light — the colonoscopy booking */}
      <div style={{ position: "absolute", inset: 0, opacity: 1 - dark }}>
        <ActLabel act="Act II · Emma's story" title="The disruption" />
        <div style={{ position: "absolute", left: 210, top: 250 }}><PathwayTimeline nodes={nodes} width={1150} /></div>
        <div style={{ position: "absolute", left: 300, top: 510 }}>
          <Person look={LOOKS.emma} height={420} pose={{ ...POSES.phone, blink: blinkAt(f) }} prop="phone" phoneGlow={1} mood="neutral" />
        </div>
        <div style={{ position: "absolute", left: 640, top: 560 }}><Toast m={MSG.colonoscopyBooked} at={B3.colo + 0.6} width={640} /></div>
        <div style={{ position: "absolute", left: 640, top: 800 }}><EventChip type={step("attend_specialist").event} at={B3.colo + 0.8} /></div>
      </div>

      {/* B: dark — the endoscopy schedule board */}
      <div style={{ position: "absolute", inset: 0, background: system.bg, opacity: dark }} />
      {boardO > 0 && (
        <div style={{ position: "absolute", inset: 0, opacity: boardO, fontFamily: FONT }}>
          <ActLabel act="North Karelia Central Hospital · Joensuu" title="Endoscopy schedule" dark />
          <div style={{ position: "absolute", left: 120, top: 290, width: 1160 }}>
            <div style={{ display: "grid", gridTemplateColumns: "250px 1fr 1fr", gap: 18, color: system.muted, fontSize: 22, marginBottom: 12, fontFamily: MONO }}>
              <span /><span>{fmtDay(step("disruption").sim_at!)}</span><span>{fmtDay(D.firstColo)}</span>
            </div>
            {["Endoscopy Unit 1", "Endoscopy Unit 2"].map((unit, u) => {
              const isDown = u === 1;
              return (
                <div key={unit} style={{ display: "grid", gridTemplateColumns: "250px 1fr 1fr", gap: 18, alignItems: "center", marginBottom: 22,
                  background: isDown ? `rgba(239,83,80,${0.14 * down})` : "transparent", borderRadius: 14, padding: "10px 0" }}>
                  <div style={{ fontSize: 26, fontWeight: 700, color: isDown && down > 0.5 ? system.bad : system.text, paddingLeft: 12 }}>
                    {unit}{isDown && down > 0 && <div style={{ fontSize: 18, fontFamily: MONO, color: system.bad, opacity: down }}>OFFLINE · {story.outage.hours} h</div>}
                  </div>
                  {[0, 1].map((day) => (
                    <div key={day} style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10 }}>
                      {TIMES.map((tm, i) => {
                        const b = isDown ? UNIT2.find((x) => x.day === day && x.i === i) : undefined;
                        const emmaGlow = b?.emma ? 0.5 + 0.5 * Math.sin(f / 5) : 0;
                        return (
                          <div key={tm} style={{
                            height: 92, borderRadius: 10, padding: "8px 10px", border: `2px solid ${b?.emma ? system.teal : isDown && down > 0.5 ? system.bad : system.border2}`,
                            background: b ? (down > 0.5 ? "rgba(245,165,36,.12)" : "rgba(56,189,248,.10)") : system.panel2,
                            boxShadow: emmaGlow ? `0 0 ${10 + 14 * emmaGlow}px rgba(45,212,191,.55)` : undefined, opacity: pop(f, B3.board + 0.1 + (day * 4 + i) * 0.05),
                          }}>
                            <div style={{ fontFamily: MONO, fontSize: 15, color: system.muted }}>{tm}</div>
                            <div style={{ fontSize: 20, fontWeight: 700, color: b ? (b.emma ? system.teal : system.text) : system.muted, marginTop: 4,
                              textDecoration: b && down > 0.5 ? "line-through" : undefined }}>{b ? b.name : "booked"}</div>
                            {b?.urgent && <div style={{ fontSize: 14, color: system.warn, fontWeight: 600 }}>urgent</div>}
                          </div>
                        );
                      })}
                    </div>
                  ))}
                </div>
              );
            })}
            <div style={{ color: system.muted, fontSize: 19, marginTop: 6, display: "flex", gap: 12, alignItems: "center" }}>
              Unit 1 is fully booked these days · <Tag t="SYNTHETIC" size={14} /> capacity and patients
            </div>
          </div>
          {/* outage stamp + admin alert */}
          {down > 0 && (
            <div style={{ position: "absolute", left: 400, top: 720, transform: `rotate(-4deg) scale(${1.6 - 0.6 * pop(f, B3.outage, 10)})`, opacity: down,
              border: `6px solid ${system.bad}`, color: system.bad, fontWeight: 900, fontSize: 54, padding: "4px 24px", borderRadius: 14, letterSpacing: 4, background: "rgba(7,13,24,.6)" }}>
              OUT OF SERVICE
            </div>
          )}
          <div style={{ position: "absolute", left: 1340, top: 250 }}>{t > B3.outage && <Toast m={MSG.outageAdmin} at={B3.outage + 0.5} width={500} dark />}</div>
          <div style={{ position: "absolute", left: 1340, top: 560 }}>{t > B3.outage && <EventChip type={step("disruption").event} at={B3.outage + 0.7} color={system.bad} dark />}</div>
          <div style={{ position: "absolute", left: 1700, top: 48 }}>
            <Calendar size={130} dates={[{ at: 0, iso: D.specialist }, { at: B3.board, iso: step("attend_specialist").sim_at! }, { at: B3.outage, iso: D.outage, tone: "bad" }]} />
          </div>
        </div>
      )}

      {/* C: zoom out — East Finland, everyone booked on Unit 2 */}
      {mapO > 0 && (
        <div style={{ position: "absolute", inset: 0, opacity: mapO }}>
          <FinlandMap cx={cx} cy={cy} zoom={z} highlightCounty={["hva-972"]}>
            {(p, zoom) => {
              const [hx, hy] = p(pk.x, pk.y);
              const lines = ramp(f, B3.zoom + 1.2, 1.2);
              return (
                <svg width={1920} height={1080} style={{ position: "absolute", inset: 0, fontFamily: FONT }}>
                  {MAP.demo_hospitals.filter((h) => h.id !== pk.id).map((h) => {
                    const [x, y] = p(h.x, h.y);
                    return <g key={h.id} opacity={ramp(f, B3.zoom + 2, 0.6)}><circle cx={x} cy={y} r={9} fill={system.accent} /><text x={x + 14} y={y + 6} fill={system.text2} fontSize={20}>{h.name}</text></g>;
                  })}
                  {MAP.patients.map((pt, i) => {
                    const [x, y] = p(pt.x, pt.y);
                    const l = Math.min(1, Math.max(0, lines * 1.3 - i * 0.04));
                    return (
                      <g key={pt.name}>
                        <line x1={x} y1={y} x2={x + (hx - x) * l} y2={y + (hy - y) * l} stroke={pt.emma ? system.teal : system.warn} strokeWidth={pt.emma ? 4 : 2.5} strokeDasharray="8 6" opacity={0.85} />
                        <circle cx={x} cy={y} r={pt.emma ? 11 : 8} fill={pt.emma ? system.teal : pt.priority === "urgent" ? system.warn : "#e2e8f0"} />
                        {zoom < 6 && <text x={x + (LEFT_LABEL.has(first(pt.name)) ? -16 : 14)} y={y + (pt.emma ? -18 : pt.muni === "Joensuu" ? -16 : -10)} textAnchor={LEFT_LABEL.has(first(pt.name)) ? "end" : "start"}
                          fill={pt.emma ? system.teal : system.text2} fontSize={pt.emma ? 26 : 18} fontWeight={pt.emma ? 700 : 500}
                          opacity={ramp(f, B3.zoom + 2.2 + i * 0.08, 0.4)}>{pt.emma ? "Emma" : `${first(pt.name)} · ${pt.muni}`}</text>}
                      </g>
                    );
                  })}
                  <circle cx={hx} cy={hy} r={18 + 10 * (0.5 + 0.5 * Math.sin(f / 4))} fill="none" stroke={system.bad} strokeWidth={3} opacity={0.8} />
                  <circle cx={hx} cy={hy} r={13} fill={system.bad} />
                  <text x={hx + 24} y={hy + 40} fill={system.bad} fontSize={22} fontWeight={700}>Joensuu (PKKS) · Endoscopy Unit 2 offline</text>
                </svg>
              );
            }}
          </FinlandMap>
          <div style={{ position: "absolute", right: 90, top: 90, fontFamily: FONT, textAlign: "right", opacity: ramp(f, B3.zoom + 2.4, 0.6) }}>
            <div style={{ fontSize: 96, fontWeight: 800, color: system.warn, lineHeight: 1 }}>{story.outage.affected}</div>
            <div style={{ fontSize: 30, color: system.text, fontWeight: 600 }}>appointments affected</div>
            <div style={{ fontSize: 26, color: system.text2, marginTop: 6 }}>{story.outage.urgent} urgent · Emma is one of them</div>
            <div style={{ marginTop: 12 }}><Tag t="SYNTHETIC" size={16} /></div>
          </div>
        </div>
      )}
    </SceneFrame>
  );
};

