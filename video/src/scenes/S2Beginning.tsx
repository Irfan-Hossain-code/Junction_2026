import React from "react";
import { Easing, interpolate, useCurrentFrame } from "remotion";
import { clamp, pop, ramp, s, windowed } from "../anim";
import { Calendar } from "../components/Calendar";
import { SceneFrame } from "../components/Frame";
import { PathwayTimeline, type TNode } from "../components/PathwayTimeline";
import { blinkAt, LOOKS, Person, POSES, walkPose } from "../components/Person";
import { Phone } from "../components/Phone";
import { Building, World } from "../components/Places";
import { ActLabel, EventChip, Tap, Toast } from "../components/ui";
import { SCENES } from "../config";
import { MSG } from "../data/messages";
import { D, fmtDay, step } from "../data/story";
import { FONT, patient } from "../theme";

const cfg = SCENES.find((x) => x.id === "S2-Beginning")!;

/** Beat times (seconds from scene start). Edit here to retime; narration lines live in config.ts. */
export const B2 = {
  intro: 0.6, phoneUp: 2.4, tapBook: 4.6, booked: 5.2, toGp: 8.6, atGp: 11.0, referralSent: 12.0, referralFly: 15.0, specBooked: 16.6,
  reminder: 19.6, tapConfirm: 25.0, advance: 27.4, end: 34,
};

const X = { home: 260, clinic: 720, hospital: 1170 };
const GROUND = 800;

export const S2Beginning: React.FC = () => {
  const f = useCurrentFrame();
  const t = f / 30;

  // Emma's walk: home → clinic (toGp), clinic → hospital doorway after confirming (advance)
  const walk1 = interpolate(f, [s(B2.toGp), s(B2.toGp + 2.2)], [X.home + 120, X.clinic - 150], { ...clamp, easing: Easing.inOut(Easing.quad) });
  const walk2 = interpolate(f, [s(B2.advance + 0.6), s(B2.advance + 3.4)], [0, X.hospital - 210 - (X.clinic - 150)], { ...clamp, easing: Easing.inOut(Easing.quad) });
  const emmaX = walk1 + walk2;
  const walking = (t > B2.toGp && t < B2.toGp + 2.2) || (t > B2.advance + 0.6 && t < B2.advance + 3.4);
  const holdingPhone = (t >= B2.phoneUp && t < B2.toGp) || (t >= B2.reminder - 0.4 && t < B2.advance + 0.6);
  const tapping = (t > B2.tapBook - 0.5 && t < B2.tapBook + 0.4) || (t > B2.tapConfirm - 0.5 && t < B2.tapConfirm + 0.4);
  const pose = walking ? walkPose(t * 1.1, holdingPhone ? POSES.phone : POSES.stand) : tapping ? POSES.phoneTap : holdingPhone ? POSES.phone : t > B2.advance ? POSES.stand : POSES.stand;
  const mood = t < B2.booked ? "worried" : t > B2.tapConfirm ? "smile" : "neutral";

  const nodes: TNode[] = [
    { label: "Symptoms", date: fmtDay(D.start), states: [[0, "current"], [B2.booked, "done"]] },
    { label: "GP visit", date: fmtDay(D.gp), states: [[B2.booked, "current"], [B2.referralSent, "done"]] },
    { label: "Urgent referral", date: fmtDay(D.referral), sub: "priority set by GP", states: [[B2.referralSent, "current"], [B2.referralFly + 0.8, "done"]] },
    { label: "Specialist", date: fmtDay(D.specialist), states: [[B2.specBooked, "current"]], sub: undefined },
    { label: "Colonoscopy", states: [] },
    { label: "Diagnosis", states: [] },
    { label: "Treatment", states: [] },
  ];
  // specialist sub-label: unconfirmed → reminder → confirmed
  nodes[3].sub = t >= B2.tapConfirm ? "confirmed ✓" : t >= B2.reminder ? "not confirmed yet" : t >= B2.specBooked ? "booked" : undefined;

  // referral document flight (clinic → hospital)
  const fly = ramp(f, B2.referralFly, 1.4, Easing.inOut(Easing.cubic));
  const docX = interpolate(fly, [0, 1], [X.clinic + 60, X.hospital - 20]);
  const docY = GROUND - 330 - Math.sin(fly * Math.PI) * 170;

  const chip = ([[B2.booked, step("book_gp").event], [B2.referralSent, step("attend_gp").event], [B2.specBooked, step("referral_received").event],
    [B2.reminder, step("reminder").event], [B2.tapConfirm, step("confirm_specialist").event]] as [number, string][]).filter(([a]) => t >= a).pop();

  const phoneScreens = [
    { at: B2.phoneUp - 0.2, file: "phone-00-start" },
    { at: B2.booked, file: "phone-book_gp", fallback: [MSG.gpBooked] },
    { at: B2.referralSent + 0.6, file: "phone-attend_gp", fallback: [MSG.referred, MSG.gpBooked] },
    { at: B2.specBooked + 0.4, file: "phone-referral_received", fallback: [MSG.specialistBooked, MSG.referred, MSG.gpBooked] },
    { at: B2.reminder + 0.3, file: "phone-reminder", fallback: [MSG.reminder, MSG.specialistBooked, MSG.referred] },
    { at: B2.tapConfirm + 0.6, file: "phone-confirm_specialist", fallback: [MSG.specialistBooked, MSG.referred, MSG.gpBooked] },
  ];
  const phoneIn = pop(f, B2.phoneUp - 0.3, 15);

  return (
    <SceneFrame cfg={cfg}>
      <World groundY={GROUND}>
        <ActLabel act="Act II · Emma's story" title="The beginning" />

        {/* places */}
        <div style={{ position: "absolute", left: X.home, top: GROUND - 240 }}><Building kind="home" label="Emma's home, Joensuu" lit={t < B2.toGp ? 1 : 0} /></div>
        <div style={{ position: "absolute", left: X.clinic, top: GROUND - 230 }}><Building kind="clinic" label="Niinivaara health station" lit={t > B2.toGp + 2 && t < B2.referralFly + 1 ? 1 : 0} /></div>
        <div style={{ position: "absolute", left: X.hospital, top: GROUND - 380, transform: "scale(0.95)", transformOrigin: "bottom" }}>
          <Building kind="hospital" label="North Karelia Central Hospital" lit={t > B2.referralFly + 1.2 ? 1 : 0} />
        </div>

        {/* GP outside the clinic */}
        <div style={{ position: "absolute", left: X.clinic + 70, top: GROUND - 382, opacity: windowed(f, B2.toGp + 1.2, B2.referralFly + 1.2) }}>
          <Person look={LOOKS.gp} height={372} pose={{ ...(t > B2.atGp && t < B2.referralSent + 1.5 ? POSES.explain : POSES.stand), look: -0.7, blink: blinkAt(f, 40) }}
            mood={t > B2.atGp && t < B2.referralSent + 1.5 ? "talk" : "smile"} talk={t > B2.atGp && t < B2.referralSent + 1.5 ? (Math.sin(f / 2.6) + 1) / 2 : 0} />
          <div style={{ position: "absolute", top: -34, left: 0, right: 0, fontFamily: FONT, fontSize: 18, fontWeight: 600, color: patient.text2, textAlign: "center", whiteSpace: "nowrap" }}>Dr. Anna Kettunen, GP</div>
        </div>

        {/* referral document */}
        {fly > 0 && fly < 1 && (
          <div style={{ position: "absolute", left: docX, top: docY, transform: `rotate(${(fly - 0.5) * 30}deg)`, fontFamily: FONT }}>
            <div style={{ width: 92, height: 116, background: "#fff", borderRadius: 8, boxShadow: "0 10px 26px rgba(15,23,42,.25)", padding: 10, border: `2px solid ${patient.accent}` }}>
              <div style={{ fontSize: 13, fontWeight: 800, color: patient.bad }}>URGENT</div>
              {[0, 1, 2, 3].map((i) => <div key={i} style={{ height: 6, background: "#e2e8f0", borderRadius: 3, marginTop: 9, width: `${90 - i * 12}%` }} />)}
            </div>
          </div>
        )}

        {/* Emma */}
        <div style={{ position: "absolute", left: emmaX - 110, top: GROUND - 382 }}>
          <Person look={LOOKS.emma} height={380} pose={{ ...pose, blink: blinkAt(f), look: walking ? 0.6 : 0 }} mood={mood}
            prop={holdingPhone ? "phone" : null} phoneGlow={holdingPhone ? 1 : 0} />
        </div>
        {/* symptom thought bubble: the note Emma's booking carries */}
        <div style={{ position: "absolute", left: X.home + 190, top: GROUND - 455, opacity: windowed(f, B2.intro + 0.4, B2.booked + 1.2), fontFamily: FONT }}>
          <div style={{ background: "#fff", borderRadius: 22, padding: "14px 20px", boxShadow: "0 12px 30px rgba(15,23,42,.12)", fontSize: 22, color: patient.text2, maxWidth: 300 }}>
            rectal bleeding for 3 weeks, tiredness
          </div>
        </div>

        {/* timeline */}
        <div style={{ position: "absolute", left: 150, top: 175 }}><PathwayTimeline nodes={nodes} width={1050} /></div>
        <div style={{ position: "absolute", left: 120, top: 360 }}>{chip && <EventChip key={chip[1] + chip[0]} type={chip[1]} at={chip[0]} />}</div>

        {/* calendar */}
        <div style={{ position: "absolute", left: 1262, top: 54 }}>
          <Calendar size={150} dates={[{ at: 0, iso: D.start }, { at: B2.toGp, iso: D.gp }, { at: B2.referralFly, iso: step("referral_received").sim_at! },
            { at: B2.reminder, iso: D.reminder }, { at: B2.advance + 0.4, iso: D.specialist }]} />
        </div>

        {/* phone */}
        <div style={{ position: "absolute", left: 1450, top: 120, transform: `translateY(${(1 - phoneIn) * 80}px)`, opacity: Math.min(1, phoneIn * 1.5) }}>
          <Phone height={820} screens={phoneScreens}>
            {/* push banners on top of the real screen */}
            {t > B2.booked && t < B2.booked + 3.2 && <div style={{ position: "absolute", top: 46, left: 10 }}><Toast m={MSG.gpBooked} at={B2.booked} width={560} scale={0.62} from="top" /></div>}
            {t > B2.specBooked && t < B2.specBooked + 2.8 && <div style={{ position: "absolute", top: 46, left: 10 }}><Toast m={MSG.specialistBooked} at={B2.specBooked} width={560} scale={0.62} from="top" /></div>}
            {t > B2.reminder && t < B2.tapConfirm + 1.4 && <div style={{ position: "absolute", top: 46, left: 10 }}>
              <Toast m={MSG.reminder} at={B2.reminder} width={560} scale={0.62} from="top" action="Confirm" tapAt={B2.tapConfirm} done="Confirmed ✓" /></div>}
          </Phone>
          <Tap x={196 * 0.967} y={226 * 0.967} at={B2.tapBook} />
          <Tap x={318} y={46 + 228 * 0.62} at={B2.tapConfirm} />
        </div>
      </World>
    </SceneFrame>
  );
};

