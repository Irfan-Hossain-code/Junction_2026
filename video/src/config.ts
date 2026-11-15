/**
 * THE EDIT POINT. Scene order, durations, narration text, clip and audio files all live here.
 *
 * - Reorder / drop scenes: edit SCENES (the Full composition follows it).
 * - Retime: change durationSec, and the `at` (seconds from scene start) of each narration line / beat.
 * - Irfan's recordings: drop files in public/audio/ or public/clips/ with the names below, then run
 *   `npm run assets` (or just restart the studio). A missing file shows a labelled placeholder instead.
 * - Captions: on by default as the placeholder for narration; set CAPTIONS=false once the voice-over is in.
 */
export const CAPTIONS = true;
/** Mark phone panels that are rebuilt from real notification text (no screenshot captured yet). Turn off for the final cut only if you accept the rebuilds. */
export const MARK_RECREATED = true;
/** Crossfade between consecutive scenes in the Full assembly (frames at 30 fps). */
export const CROSSFADE = 15;

export interface Line { at: number; text: string; dur?: number }
export interface SceneCfg {
  id: string;
  title: string;
  durationSec: number;
  /** live-action or screen-recording clip in public/clips/ (null = fully animated) */
  clip?: { file: string; what: string; startFromSec?: number } | null;
  /** narration audio in public/audio/ (Irfan's VO). Plays from scene start. */
  audio?: string | null;
  narration: Line[];
}

export const SCENES: SceneCfg[] = [
  {
    id: "S1-Intro", title: "We cured cancer", durationSec: 50,
    clip: { file: "s1-intro.mp4", what: "Irfan to camera: approved intro monologue (≈50 s)" },
    narration: [],   // spoken on camera; the logo appears over the clip at LOGO_AT
  },
  {
    id: "S2-Beginning", title: "Emma: the beginning", durationSec: 34, audio: "s2-beginning.mp3",
    narration: [
      { at: 0.6, text: "This is Emma." },
      { at: 2.4, text: "She's been experiencing some worrying symptoms, so she opens CancerFlow and books an appointment with her GP." },
      { at: 8.6, text: "The next day, her doctor decides she needs further investigation and refers her to a specialist." },
      { at: 15.0, text: "The hospital receives the referral and schedules an appointment." },
      { at: 19.6, text: "And when Emma hasn't confirmed her attendance, CancerFlow sends her a reminder." },
      { at: 25.0, text: "She confirms directly through the app." },
      { at: 29.0, text: "So far, everything is going according to plan." },
    ],
  },
  {
    id: "S3-Disruption", title: "The disruption", durationSec: 17, audio: "s3-disruption.mp3",
    narration: [
      { at: 0.4, text: "After her specialist appointment, Emma is referred for a colonoscopy." },
      { at: 4.4, text: "But just as her investigation is progressing, something unexpected happens." },
      { at: 8.4, text: "One of the hospital's endoscopy units goes offline for 48 hours." },
      { at: 12.6, text: "And Emma isn't the only patient affected." },
    ],
  },
  {
    id: "S4-Donny", title: "Donny and the AI", durationSec: 45,
    clip: { file: "s4-donny-call.mp4", what: "Irfan as Donny + live ElevenLabs call over the twin (screen + camera, ≈45 s)" },
    narration: [],
  },
  {
    id: "S5-Recovery", title: "Emma doesn't disappear", durationSec: 30, audio: "s5-recovery.mp3",
    narration: [
      { at: 0.4, text: "Back to Emma." },
      { at: 1.8, text: "Following the disruption, her appointment is rescheduled, and she receives the updated information." },
      { at: 6.6, text: "She confirms her new appointment." },
      { at: 9.0, text: "But then something else happens." },
      { at: 11.2, text: "Emma misses it." },
      { at: 13.2, text: "And this is where continuity of care becomes especially important." },
      { at: 17.0, text: "Rather than her missed appointment disappearing into an administrative backlog, the event is recorded in her pathway." },
      { at: 22.6, text: "Her care team can see what happened, authorize another appointment, and keep her investigation moving." },
    ],
  },
  {
    id: "S6-Diagnosis", title: "Diagnosis and treatment", durationSec: 22, audio: "s6-diagnosis.mp3",
    narration: [
      { at: 0.4, text: "Emma eventually attends her colonoscopy, where biopsies are taken." },
      { at: 3.8, text: "The results confirm cancer." },
      { at: 5.6, text: "She undergoes staging scans, and a multidisciplinary team of clinicians determines her treatment plan." },
      { at: 11.2, text: "And on December 17, just over a month after her first appointment, Emma begins treatment." },
      { at: 16.6, text: "Throughout the journey, CancerFlow keeps a connected record of the events that brought her here." },
    ],
  },
  {
    id: "S7-Twin", title: "The national digital twin", durationSec: 60, audio: "s7-twin.mp3",
    clip: { file: "s7-twin.mp4", what: "Screen recording of the national twin: Baseline → Disruption → MPC Recovery, Δ view, optimiser modal (≈45 s)", startFromSec: 0 },
    narration: [
      { at: 0.4, text: "Now, Emma is just one patient." },
      { at: 2.2, text: "But imagine hundreds of patients moving through dozens of hospitals, all competing for limited resources." },
      { at: 7.4, text: "That's where the other half of CancerFlow comes in." },
      { at: 9.8, text: "What you're looking at is our interactive digital twin of Finland's healthcare network." },
      { at: 13.6, text: "Here, administrators can explore hospital capacities, patient flows, and treatment bottlenecks across the entire system." },
      { at: 18.4, text: "But it's more than a visualization." },
      { at: 20.4, text: "We can introduce disruptions, simulate how they propagate through the network, and use mathematical optimization to find better ways of allocating patients." },
      { at: 26.6, text: "And unlike a conventional scheduling system, we're not just optimizing the next available appointment. We're considering the entire treatment pathway." },
      { at: 32.6, text: "For example, in one of our simulated scenarios, a major disruption reduced the percentage of patients receiving treatment within the target time to just 36.6 percent." },
      { at: 39.4, text: "After applying our recovery strategy, that increased to 53.3 percent." },
      { at: 43.0, text: "And here's the interesting part. Administrators can challenge these recommendations, explore alternative strategies, and evaluate the consequences before approving any changes." },
      { at: 50.0, text: "Right now, we're using synthetic patients and simulated hospital data. But the system is designed around structured data, creating a foundation for future integration with real healthcare systems." },
      { at: 57.0, text: "And all of this brings us back to Emma.", dur: 2.6 },
    ],
  },
  {
    id: "S8-Ending", title: "Because every day matters", durationSec: 40,
    clip: { file: "s8-ending.mp4", what: "Irfan to camera: approved closing (≈36 s), then the end card" },
    narration: [],
  },
];

/** Seconds into S1 when the CancerFlow logo overlays the intro clip ("Meet CancerFlow."). */
export const LOGO_AT = 9.5;
/** Seconds before the end of S8 when the end card takes over. */
export const END_CARD_SEC = 5;
