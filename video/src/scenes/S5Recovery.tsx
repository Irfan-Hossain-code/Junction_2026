import React from "react";
import { useCurrentFrame } from "remotion";
import { pop, ramp, windowed } from "../anim";
import { Calendar } from "../components/Calendar";
import { SceneFrame } from "../components/Frame";
import { PathwayTimeline, type TNode } from "../components/PathwayTimeline";
import { blinkAt, LOOKS, Person, POSES } from "../components/Person";
import { Phone } from "../components/Phone";
import { Building, World } from "../components/Places";
import { ActLabel, EventChip, Tap, Toast } from "../components/ui";
import { SCENES } from "../config";
import { MSG } from "../data/messages";
import { appt, D, fmtDay, fmtTime, step, story } from "../data/story";
import { FONT, MONO, patient, system } from "../theme";

const cfg = SCENES.find((x) => x.id === "S5-Recovery")!;
export const B5 = { back: 0.4, rescheduled: 1.8, tapConfirm: 6.6, waiting: 9.0, missed: 11.2, team: 13.2, log: 17.0, authorise: 23.4, end: 30 };

const GROUND = 800;
const rebook = story.rebook_after_outage!;
const newColo = appt("COLONOSCOPY", "attended")!;
const cancelled = appt("COLONOSCOPY", "cancelled")!;

export const S5Recovery: React.FC = () => {
  const f = useCurrentFrame();
  const t = f / 30;
  const authorised = t >= B5.authorise;

  const coloSub = authorised ? "authorised by clinician" : t >= B5.missed ? "missed" : t >= B5.tapConfirm ? `${rebook.facility} · confirmed ✓` : t >= B5.rescheduled ? `${rebook.facility} · rescheduled` : "Endoscopy Unit 2 · cancelled (outage)";
  const coloDate = authorised ? fmtTime(newColo.at) : t >= B5.rescheduled ? fmtTime(rebook.at) : fmtTime(cancelled.at);
  const nodes: TNode[] = [
    { label: "GP visit", date: fmtDay(D.gp), states: [[0, "done"]] },
    { label: "Referral", date: fmtDay(D.referral), states: [[0, "done"]] },
    { label: "Specialist", date: fmtDay(D.specialist), states: [[0, "done"]] },
    { label: "Colonoscopy", date: coloDate, sub: coloSub, states: [[0, "warn"], [B5.rescheduled, "current"], [B5.missed, "bad"], [B5.authorise, "current"]] },
    { label: "Diagnosis", states: [] },
    { label: "Treatment", states: [] },
  ];

  const chip = ([[B5.rescheduled, step("recover").event], [B5.tapConfirm, step("confirm_new").event], [B5.missed, step("miss").event],
    [B5.authorise, step("followup_rebook").event]] as [number, string][]).filter(([a]) => t >= a).pop();

  const phoneScreens = [
    { at: 0, file: "phone-recover", fallback: [MSG.rescheduled, MSG.colonoscopyBooked] },
    { at: B5.tapConfirm + 0.6, file: "phone-confirm_new", fallback: [MSG.rescheduled, MSG.colonoscopyBooked] },
    { at: B5.missed + 0.4, file: "phone-miss", fallback: [MSG.missedPatient, MSG.rescheduled] },
    { at: B5.authorise + 0.6, file: "phone-followup_rebook", fallback: [MSG.newTime, MSG.missedPatient, MSG.rescheduled] },
  ];

  // the Savonlinna slot passing by with nobody there
  const slotO = windowed(f, B5.waiting, B5.team + 0.2, 0.4);
  const clock = Math.min(1, Math.max(0, (t - B5.waiting) / (B5.missed - B5.waiting)));
  const teamIn = pop(f, B5.team, 16);
  const logLines: { at: number; when: string; type: string; text: string; tone?: string }[] = [
    { at: B5.log, when: fmtTime(D.outage), type: "APPOINTMENT_STATUS", text: "cancelled · resource offline (disruption)", tone: system.warn },
    { at: B5.log + 0.5, when: fmtTime(D.outage), type: "APPOINTMENT_BOOKED", text: `${rebook.facility}, ${fmtTime(rebook.at)} · recovery plan` },
    { at: B5.log + 1.0, when: fmtTime(step("confirm_new").sim_at!), type: "NOTE", text: "patient confirmed attendance" },
    { at: B5.log + 1.5, when: fmtTime(story.missed_at!), type: "APPOINTMENT_STATUS", text: "missed · pathway kept open", tone: system.bad },
    { at: B5.authorise + 0.5, when: fmtTime(story.missed_at!), type: "APPOINTMENT_BOOKED", text: `${newColo.facility}, ${fmtTime(newColo.at)} · authorised by clinician`, tone: system.good },
  ];

  return (
    <SceneFrame cfg={cfg}>
      <World groundY={GROUND}>
        <ActLabel act="Act IV · Emma doesn't disappear" title="The recovery" />
        <div style={{ position: "absolute", left: 140, top: 175 }}><PathwayTimeline nodes={nodes} width={1050} /></div>
        <div style={{ position: "absolute", left: 110, top: 372 }}>{chip && <EventChip key={chip[1] + chip[0]} type={chip[1]} at={chip[0]} color={chip[1] === "appointment.missed" ? patient.bad : patient.accent} />}</div>

        <div style={{ position: "absolute", left: 240, top: GROUND - 240 }}><Building kind="home" label="Emma's home, Joensuu" /></div>
        <div style={{ position: "absolute", left: 300, top: GROUND - 362 }}>
          <Person look={LOOKS.emma} height={360} pose={{ ...(t > B5.tapConfirm - 0.5 && t < B5.tapConfirm + 0.4 ? POSES.phoneTap : t < B5.waiting || t > B5.authorise ? POSES.phone : POSES.stand), blink: blinkAt(f) }}
            prop={t < B5.waiting || t > B5.authorise ? "phone" : null} phoneGlow={1} mood={t > B5.authorise + 1 ? "smile" : t > B5.missed ? "worried" : "neutral"} />
        </div>

        {/* the rescheduled slot passes */}
        {slotO > 0 && (
          <div style={{ position: "absolute", left: 560, top: 430, opacity: slotO, fontFamily: FONT, width: 400 }}>
            <div style={{ background: "#fff", borderRadius: 18, padding: "18px 22px", boxShadow: "0 14px 34px rgba(15,23,42,.14)", borderLeft: `6px solid ${t >= B5.missed ? patient.bad : patient.accent}` }}>
              <div style={{ fontFamily: MONO, fontSize: 18, color: patient.muted }}>{fmtTime(rebook.at)}</div>
              <div style={{ fontSize: 26, fontWeight: 700, marginTop: 4 }}>Colonoscopy · {rebook.facility}</div>
              <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 10 }}>
                <svg width={44} height={44} viewBox="-22 -22 44 44"><circle r={19} fill="none" stroke={patient.border2} strokeWidth={4} />
                  <circle r={19} fill="none" stroke={t >= B5.missed ? patient.bad : patient.accent} strokeWidth={4} strokeDasharray={2 * Math.PI * 19} strokeDashoffset={(1 - clock) * 2 * Math.PI * 19} transform="rotate(-90)" /></svg>
                <span style={{ fontSize: 22, fontWeight: 700, color: t >= B5.missed ? patient.bad : patient.text2 }}>{t >= B5.missed ? "Not attended" : "Waiting for patient…"}</span>
              </div>
            </div>
          </div>
        )}

        {/* care team view (clinician worklist look: twin palette) */}
        {t > B5.team - 0.1 && (
          <div style={{ position: "absolute", left: 600, top: 336, width: 780, fontFamily: FONT, opacity: Math.min(1, teamIn * 1.4), transform: `translateY(${(1 - teamIn) * 40}px)` }}>
            <div style={{ background: system.panel, border: `1px solid ${system.border2}`, borderRadius: 16, padding: "18px 22px", color: system.text, boxShadow: "0 30px 70px rgba(7,13,24,.35)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 18, color: system.muted }}>
                <span style={{ width: 10, height: 10, borderRadius: 5, background: system.accent, boxShadow: `0 0 10px ${system.accent}` }} />
                <b style={{ color: system.text2 }}>Care team · Needs attention</b><span style={{ marginLeft: "auto", fontFamily: MONO }}>North Karelia Central Hospital</span>
              </div>
              <div style={{ borderLeft: `4px solid ${system.bad}`, padding: "8px 14px", marginTop: 12, background: "rgba(239,83,80,.08)", borderRadius: 6 }}>
                <div style={{ fontWeight: 700, fontSize: 23 }}>{MSG.missedTeam.title}</div>
                <div style={{ fontSize: 18.5, color: system.text2, marginTop: 4, lineHeight: 1.4 }}>{MSG.missedTeam.body}</div>
                <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 10 }}>
                  {authorised
                    ? <span style={{ color: system.good, fontWeight: 700, fontSize: 19, opacity: ramp(f, B5.authorise + 0.1, 0.3) }}>Authorised by clinician ✓ · Emma notified</span>
                    : <span style={{ background: system.accent, color: "#04121f", fontWeight: 700, fontSize: 18, padding: "8px 16px", borderRadius: 8 }}>Authorise earliest replacement slot</span>}
                </div>
              </div>
              {/* append-only pathway event log */}
              {t > B5.log - 0.1 && (
                <div style={{ marginTop: 14, opacity: ramp(f, B5.log - 0.1, 0.4) }}>
                  <div style={{ fontFamily: MONO, fontSize: 14, color: system.muted, letterSpacing: 2, marginBottom: 6 }}>EMMA'S PATHWAY · APPEND-ONLY EVENT LOG</div>
                  {logLines.filter((l) => t >= l.at - 0.05).map((l) => (
                    <div key={l.type + l.at} style={{ display: "grid", gridTemplateColumns: "170px 230px 1fr", gap: 10, fontSize: 16.5, padding: "4px 0", borderTop: `1px solid ${system.border}`,
                      opacity: ramp(f, l.at, 0.3), fontFamily: MONO, color: system.text2 }}>
                      <span style={{ color: system.muted }}>{l.when}</span><span style={{ color: l.tone ?? system.accent }}>{l.type}</span>
                      <span style={{ fontFamily: FONT, color: system.text }}>{l.text}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
            <Tap x={640} y={196} at={B5.authorise} />
          </div>
        )}
        {/* the nurse who authorises */}
        <div style={{ position: "absolute", left: 470, top: GROUND - 352, opacity: windowed(f, B5.team + 0.6, undefined, 0.5) }}>
          <Person look={LOOKS.nurse} height={350} pose={{ ...(t > B5.authorise - 0.6 && t < B5.authorise + 0.6 ? POSES.explain : POSES.stand), look: 0.7, blink: blinkAt(f, 55) }} mood="smile" />
        </div>

        <div style={{ position: "absolute", left: 1262, top: 54 }}>
          <Calendar size={150} dates={[{ at: 0, iso: D.outage }, { at: B5.waiting, iso: rebook.at }, { at: B5.missed, iso: story.missed_at!, tone: "bad", note: "missed" }]} />
        </div>
        <div style={{ position: "absolute", left: 1450, top: 120 }}>
          <Phone height={820} screens={phoneScreens}>
            {t > B5.rescheduled && t < B5.tapConfirm + 1.4 && <div style={{ position: "absolute", top: 46, left: 10 }}>
              <Toast m={MSG.rescheduled} at={B5.rescheduled} width={560} scale={0.62} from="top" action="Confirm" tapAt={B5.tapConfirm} done="Confirmed ✓" /></div>}
            {t > B5.missed && t < B5.missed + 3.2 && <div style={{ position: "absolute", top: 46, left: 10 }}><Toast m={MSG.missedPatient} at={B5.missed} width={560} scale={0.62} from="top" /></div>}
            {t > B5.authorise && t < B5.end && <div style={{ position: "absolute", top: 46, left: 10 }}><Toast m={MSG.newTime} at={B5.authorise + 0.4} width={560} scale={0.62} from="top" /></div>}
          </Phone>
          <Tap x={308} y={46 + 265 * 0.62} at={B5.tapConfirm} />
        </div>
      </World>
    </SceneFrame>
  );
};
