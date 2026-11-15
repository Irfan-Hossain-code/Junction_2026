/** Notification texts exactly as the backend writes them (source line in each comment), filled with the exported run's
 *  values. Shown as motion-graphic cards; real phone screenshots go in public/screens/ (see scripts/capture_screens.py). */
import { D, fmtTime, story } from "./story";

const rebookFacility = story.rebook_after_outage?.facility ?? "";
const newColo = story.appointments.find((a) => a.stage === "COLONOSCOPY" && a.status === "attended")!;

export const MSG = {
  // backend/ops/story.py step_book_gp
  gpBooked: { title: "GP appointment booked", body: `Niinivaara health station, ${fmtTime(D.gp)}. Bring a list of your symptoms.`, kind: "booking" },
  // step_attend_gp
  referred: { title: "You have been referred to North Karelia Central Hospital",
    body: "Your GP sent an urgent referral. CancerFlow will track it until the hospital books you in.", kind: "referral" },
  // step_referral_received
  specialistBooked: { title: "Specialist appointment booked",
    body: `Gastroenterology, North Karelia Central Hospital, ${fmtTime(D.specialist)}. Please confirm you can attend.`, kind: "booking" },
  // step_reminder
  reminder: { title: "Reminder: please confirm tomorrow's appointment",
    body: `Specialist assessment ${fmtTime(D.specialist)}. Tap Confirm, or tell us if you need another time.`, kind: "warning" },
  // step_attend_specialist
  colonoscopyBooked: { title: "Colonoscopy booked",
    body: `Endoscopy Unit 2, North Karelia Central Hospital, ${fmtTime(D.firstColo)}. Bowel-preparation instructions follow.`, kind: "booking" },
  // backend/ops/interventions.py:212 (admin)
  outageAdmin: { title: `Endoscopy Unit 2 at Joensuu (PKKS) is offline for ${story.outage.hours} h`,
    body: `${story.outage.affected} booked colonoscopies affected (${story.outage.urgent} urgent). Recovery options are being evaluated.`, kind: "critical" },
  // interventions.py:493 (patient; a transfer adds the travel + Kela sentence; travel_min from world.travel_min)
  rescheduled: { title: "Your colonoscopy has been rescheduled",
    body: `An endoscopy unit in Joensuu is out of service. Your new appointment: ${rebookFacility}, ${fmtTime(D.rebooked)}. ` +
      `Travel about ${Math.round(story.rebook_after_outage?.travel_min ?? 0)} min; travel costs are reimbursed (Kela). Your place in the pathway and your priority are unchanged. Please confirm.`,
    kind: "warning" },
  // story.py step_pre_procedure
  checkin: { title: "Tomorrow: colonoscopy check-in",
    body: "Please confirm you have started bowel preparation and can attend. Reply if anything has changed.", kind: "warning" },
  // followup.py:96 (patient)
  missedPatient: { title: "We missed you today",
    body: "Your colonoscopy didn't take place. That's okay: your care team will call you to find a new time that works. Your referral stays active.",
    kind: "warning" },
  // followup.py:90 (care team). "3 replacement slots" = run log message for step 'miss'
  missedTeam: { title: "Emma Nieminen did not attend her colonoscopy (urgent pathway)",
    body: `Pathway is NOT closed. Clinical priority kept: urgent. ${story.replacement_slots_evaluated} replacement slots held for review. Please contact the patient and authorise a new booking.`,
    kind: "critical" },
  // followup.py:118 (patient, after clinician authorises)
  newTime: { title: "New colonoscopy time",
    body: `${newColo.facility}, ${fmtTime(newColo.at)}. Your priority is unchanged. Please confirm, or reply if you need help getting there.`,
    kind: "warning" },
  // story.py step_histology
  results: { title: "Your doctor has your results",
    body: `Your doctor will call you today. A staging MRI and CT have been booked for ${fmtTime(D.staging)}.`, kind: "result" },
  // story.py step_mdt
  plan: { title: "Treatment plan agreed", body: `Your care team recommends surgery. Operation date: ${fmtTime(D.surgery)}.`, kind: "booking" },
} as const;

export type Msg = { title: string; body: string; kind: string };
