import React from "react";
import { useCurrentFrame } from "remotion";
import { pop, ramp } from "../anim";
import { Calendar } from "../components/Calendar";
import { SceneFrame } from "../components/Frame";
import { PathwayTimeline, type TNode } from "../components/PathwayTimeline";
import { blinkAt, LOOKS, Person, POSES } from "../components/Person";
import { Building, World } from "../components/Places";
import { ActLabel, Tag } from "../components/ui";
import { SCENES } from "../config";
import { D, DAYS_REFERRAL_TO_TREATMENT, fmtDay, fmtTime, story } from "../data/story";
import { FONT, MONO, patient } from "../theme";

const cfg = SCENES.find((x) => x.id === "S6-Diagnosis")!;
export const B6 = { colo: 0.4, cancer: 4.0, staging: 6.9, mdt: 9.9, treatment: 12.9, stat: 14.7, record: 19.6, end: 27.4 };   // aligned to the recorded voice-over (see config.ts)

/** Finnish target: diagnosis + treatment start within 6 weeks of referral (STM 2010:6 via THL; REFERENCE). */
const TARGET_DAYS = 42;

export const S6Diagnosis: React.FC = () => {
  const f = useCurrentFrame();
  const t = f / 30;
  const nodes: TNode[] = [
    { label: "Symptoms", date: fmtDay(D.start), states: [[0, "done"]] },
    { label: "GP · referral", date: fmtDay(D.referral), sub: "urgent", states: [[0, "done"]] },
    { label: "Specialist", date: fmtDay(D.specialist), states: [[0, "done"]] },
    { label: "Colonoscopy", date: fmtDay(D.newColo), sub: "biopsies taken", states: [[0, "current"], [B6.colo + 0.6, "done"]] },
    { label: "Cancer confirmed", date: fmtDay(D.histology), sub: "histology (clinician)", states: [[B6.colo + 0.6, "current"], [B6.cancer, "done"]] },
    { label: "Staging", date: fmtDay(D.staging), sub: "MRI + CT", states: [[B6.cancer, "current"], [B6.staging + 0.4, "done"]] },
    { label: "MDT decision", date: fmtDay(D.mdt), sub: "surgery", states: [[B6.staging + 0.4, "current"], [B6.mdt + 0.4, "done"]] },
    { label: "Treatment", date: fmtDay(D.surgery), sub: "surgery", states: [[B6.mdt + 0.4, "current"], [B6.treatment + 0.4, "done"]] },
  ];
  const statP = pop(f, B6.stat, 14);
  const count = Math.round(DAYS_REFERRAL_TO_TREATMENT * Math.min(1, ramp(f, B6.stat, 1.2)));
  const recO = ramp(f, B6.record, 0.6);
  // pre_procedure is left out: the director sets its clock to 18:00 the day before the rebooked slot (same day as the outage),
  // so its timestamp lands before the outage and would read as out of order. Everything else is in run order.
  const steps = story.steps.filter((x) => x.sim_at && x.key !== "pre_procedure");

  return (
    <SceneFrame cfg={cfg}>
      <World groundY={800}>
        <ActLabel act="Act IV · Emma doesn't disappear" title="Diagnosis and treatment" />
        <div style={{ position: "absolute", left: 150, top: 190, opacity: 1 - recO }}><PathwayTimeline nodes={nodes} width={1500} /></div>
        {/* the colonoscopy's history: it took four bookings to get here */}
        <div style={{ position: "absolute", left: 150 + (1500 / 7) * 3 - 160, top: 360, width: 320, opacity: ramp(f, B6.colo + 0.8, 0.5) * (1 - recO), fontFamily: FONT,
          background: "#fff", borderRadius: 14, padding: "10px 14px", boxShadow: "0 10px 26px rgba(15,23,42,.10)", fontSize: 16.5, color: patient.text2, lineHeight: 1.55 }}>
          <div style={{ fontFamily: MONO, fontSize: 13, color: patient.muted, letterSpacing: 1.5 }}>THIS STEP'S HISTORY</div>
          <div>Unit 2 booking · <b style={{ color: patient.warn }}>cancelled (outage)</b></div>
          <div>{story.rebook_after_outage?.facility} · <b style={{ color: patient.bad }}>missed</b></div>
          <div>{fmtTime(D.newColo)} · <b style={{ color: patient.good }}>attended</b></div>
        </div>

        <div style={{ position: "absolute", left: 1250, top: 400, opacity: 1 - recO }}><Building kind="hospital" label="North Karelia Central Hospital" scale={0.85} lit={t > B6.treatment ? 1 : 0} /></div>
        <div style={{ position: "absolute", left: 980, top: 800 - 362, opacity: 1 - recO }}>
          <Person look={LOOKS.emma} height={360} pose={{ ...(t > B6.treatment + 0.5 ? POSES.wave : POSES.stand), blink: blinkAt(f) }} mood={t > B6.treatment ? "smile" : "neutral"} />
        </div>

        {/* referral → treatment */}
        <div style={{ position: "absolute", left: 200, top: 470, transform: `scale(${statP})`, transformOrigin: "left center", opacity: Math.min(1, statP * 1.4) * (1 - recO), fontFamily: FONT }}>
          <div style={{ background: "#fff", borderRadius: 22, padding: "24px 30px", boxShadow: "0 18px 44px rgba(15,23,42,.14)", width: 560 }}>
            <div style={{ fontSize: 22, color: patient.muted, fontWeight: 600 }}>Referral → treatment start</div>
            <div style={{ display: "flex", alignItems: "baseline", gap: 14 }}>
              <span style={{ fontSize: 120, fontWeight: 800, color: patient.accent, lineHeight: 1.05 }}>{count}</span>
              <span style={{ fontSize: 40, fontWeight: 700, color: patient.text }}>days</span>
            </div>
            <div style={{ fontSize: 19, color: patient.text2 }}>{fmtDay(D.referral)} → {fmtDay(D.surgery)} · Finnish target ≤ {TARGET_DAYS} days</div>
            <div style={{ display: "flex", gap: 10, marginTop: 12 }}><Tag t="SYNTHETIC" dark={false} size={15} /><Tag t="REFERENCE" dark={false} size={15} /></div>
          </div>
        </div>

        <div style={{ position: "absolute", left: 1700, top: 54, opacity: 1 - recO }}>
          <Calendar size={150} dates={[{ at: 0, iso: D.newColo }, { at: B6.cancer, iso: D.histology }, { at: B6.staging, iso: D.staging }, { at: B6.mdt, iso: D.mdt }, { at: B6.treatment, iso: D.surgery, note: "treatment" }]} />
        </div>

        {/* the connected record: every director event, one log */}
        {recO > 0 && (
          <div style={{ position: "absolute", left: 160, top: 170, width: 1600, opacity: recO, fontFamily: FONT }}>
            <div style={{ fontSize: 34, fontWeight: 700, color: patient.text }}>One connected record · Emma's director events</div>
            <div style={{ fontSize: 21, color: patient.muted, marginBottom: 16 }}>The patient app, the clinician app and the operations twin all write to the same pathway</div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gridTemplateRows: `repeat(${Math.ceil(steps.length / 2)}, auto)`, gridAutoFlow: "column", columnGap: 40, rowGap: 6 }}>
              {steps.map((x, i) => (
                <div key={x.key} style={{ display: "grid", gridTemplateColumns: "190px 1fr", gap: 12, alignItems: "baseline", opacity: ramp(f, B6.record + 0.1 + i * 0.07, 0.25),
                  fontSize: 19, padding: "6px 0", borderBottom: `1px solid ${patient.border}` }}>
                  <span style={{ fontFamily: MONO, color: patient.muted, fontSize: 16 }}>{fmtTime(x.sim_at!)}</span>
                  <span><span style={{ fontFamily: MONO, color: x.key === "disruption" || x.key === "miss" ? patient.bad : patient.accent, fontSize: 16 }}>{x.event}</span>
                    <span style={{ color: patient.text2 }}> · {x.label}</span></span>
                </div>
              ))}
            </div>
          </div>
        )}
      </World>
    </SceneFrame>
  );
};
