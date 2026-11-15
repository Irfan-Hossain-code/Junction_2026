/**
 * THE EDIT POINT. Scene order, durations, narration text, clip and audio files all live here.
 *
 * - Reorder / drop scenes: edit SCENES (the Full composition follows it).
 * - Retime: change durationSec, and the `at` (seconds from scene start) of each narration line / beat.
 * - Irfan's recordings: drop files in public/audio/ or public/clips/ with the names below, then run
 *   `npm run assets` (or just restart the studio). A missing file shows a labelled placeholder instead.
 * - Captions: on by default as the placeholder for narration; set CAPTIONS=false once the voice-over is in.
 *   Voice-over: public/audio/vo-part1.mp3 (S2+S3) and vo-part2.mp3 (S5–S7), each scene plays its audioFrom→audioTo slice.
 *   Line `at` values and scene beats were aligned to the recorded read with word timestamps (faster-whisper).
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
  /** cut list for a clip scene assembled from several takes, played back to back (seconds within each file) */
  segments?: { file: string; from: number; to: number }[];
  /** narration audio in public/audio/ (Irfan's VO). Plays from scene start; audioFrom/audioTo pick a slice of a longer take. */
  audio?: string | null;
  audioFrom?: number;
  audioTo?: number;
  narration: Line[];
}

export const SCENES: SceneCfg[] = [
  {
    id: "S1-Intro", title: "We cured cancer", durationSec: 106.2,
    clip: { file: "s1-intro.mp4", what: "Irfan to camera: approved intro monologue (106 s)" },
    narration: [],   // spoken on camera; the logo appears over the clip at LOGO_AT
  },
  {
    id: "S2-Beginning", title: "Emma: the beginning", durationSec: 29.2, audio: "vo-part1.mp3", audioFrom: 0.0, audioTo: 28.38,
    narration: [
      { at: 0.0, text: "This is Emma." },
      { at: 2.02, text: "She's been experiencing some worrying symptoms, so she opens CancerFlow and books an appointment with her GP." },
      { at: 7.38, text: "The next day, her doctor decides she needs further investigation and refers her to a specialist." },
      { at: 12.7, text: "The hospital receives the referral and schedules an appointment." },
      { at: 16.86, text: "And when Emma hasn't confirmed her attendance, CancerFlow sends her a reminder." },
      { at: 21.44, text: "She confirms directly through the app." },
      { at: 23.34, text: "So far, everything is going according to plan." },
    ],
  },
  {
    id: "S3-Disruption", title: "The disruption", durationSec: 18.6, audio: "vo-part1.mp3", audioFrom: 28.38, audioTo: 45.8,
    narration: [
      { at: 0.4, text: "After her specialist appointment, Emma is referred for a colonoscopy." },
      { at: 5.34, text: "But just as her investigation is progressing, something unexpected happens." },
      { at: 10.1, text: "One of the hospital's endoscopy units goes offline for 48 hours." },
      { at: 14.24, text: "And Emma isn't the only patient affected." },
    ],
  },
  {
    id: "S4-Donny", title: "Donny and the AI", durationSec: 67.4,
    clip: { file: "s4-part1.mp4", what: "Irfan as Donny + live ElevenLabs call over the twin (portrait phone footage)" },
    // Cut list (sum = durationSec). Drops the "Can you hear me?" retry and the silent wait while the plan commits.
    segments: [
      { file: "s4-part1.mp4", from: 0.0, to: 20.6 },    // incoming call → "Would you like to hear it?"
      { file: "s4-part2.mp4", from: 0.0, to: 2.3 },     // "Yeah, tell me about your plan."
      { file: "s4-part2.mp4", from: 10.6, to: 32.6 },   // the plan → "Let's execute."
      { file: "s4-part2.mp4", from: 33.0, to: 40.6 },   // "Approving the plan now…"
      { file: "s4-part2.mp4", from: 49.6, to: 64.5 },   // committed → goodbyes
    ],
    // Captions of the live call (the on-screen transcript is too small to read on the phone footage).
    narration: [
      { at: 4.2, text: "CancerFlow: Donny, this is CancerFlow. Endoscopy Unit 2 at Joensuu (PKKS) is offline for 48 hours.", dur: 8.6 },
      { at: 13.3, text: "Eight colonoscopies are affected, four of them urgent. I have a recovery plan ready. Would you like to hear it?", dur: 7.1 },
      { at: 20.6, text: "Donny: Yeah, tell me about your plan.", dur: 2.3 },
      { at: 23.2, text: "CancerFlow: The plan sends four patients to Savonlinna, two to Kuopio and two to Joensuu.", dur: 5.9 },
      { at: 29.3, text: "71.3% of patients are within target, and 46.9% of urgent patients are within target. The mean treatment day is 20.9.", dur: 10.3 },
      { at: 42.1, text: "Donny: Okay, sounds good. Let's execute.", dur: 2.8 },
      { at: 45.9, text: "CancerFlow: Approving the plan now, please wait a moment.", dur: 6.4 },
      { at: 52.9, text: "CancerFlow: The plan has been committed. Eight appointments have been moved, and patients and hospitals have been notified.", dur: 7.5 },
      { at: 60.6, text: "Donny: Thank you. Goodbye.", dur: 2.9 },
      { at: 63.7, text: "CancerFlow: Goodbye, Donny.", dur: 3.2 },
    ],
  },
  {
    id: "S5-Recovery", title: "Emma doesn't disappear", durationSec: 33.6, audio: "vo-part2.mp3", audioFrom: 0.0, audioTo: 32.84,
    narration: [
      { at: 0.0, text: "Now, back to Emma." },
      { at: 2.9, text: "Following the disruption, her appointment is rescheduled, and she receives the updated information." },
      { at: 7.56, text: "She confirms her new appointment." },
      { at: 8.78, text: "But then something else happens." },
      { at: 11.38, text: "Emma misses it." },
      { at: 12.54, text: "And this is where continuity of care becomes especially important." },
      { at: 17.52, text: "Rather than her missed appointment disappearing into an administrative backlog, the event is recorded in her pathway." },
      { at: 24.86, text: "Her care team can see what happened, authorize another appointment, and keep her investigation moving." },
    ],
  },
  {
    id: "S6-Diagnosis", title: "Diagnosis and treatment", durationSec: 27.4, audio: "vo-part2.mp3", audioFrom: 32.84, audioTo: 59.4,
    narration: [
      { at: 0.4, text: "Emma eventually attends her colonoscopy, where biopsies are taken." },
      { at: 3.98, text: "The results confirm cancer." },
      { at: 6.88, text: "She undergoes staging scans, and a multidisciplinary team of clinicians determines her treatment plan." },
      { at: 12.92, text: "And on December 17, just over a month after her first appointment, Emma begins treatment." },
      { at: 19.62, text: "Throughout the journey, CancerFlow keeps a connected record of the events that brought her here." },
    ],
  },
  {
    id: "S7-Twin", title: "The national digital twin", durationSec: 88.9, audio: "vo-part2.mp3", audioFrom: 59.4, audioTo: 147.54,
    // No twin screen recording: the scene stays on the animated Finland map + SIMULATED results card.
    // To add one later: put it in public/clips/ and set clip: { file: "s7-twin.mp4", what: "...", startFromSec: 0 }.
    clip: null,
    narration: [
      { at: 0.4, text: "Now, Emma is just one patient." },
      { at: 1.98, text: "But imagine hundreds of patients moving through dozens of hospitals, all competing for limited resources." },
      { at: 8.5, text: "That's where the other half of CancerFlow comes in." },
      { at: 11.02, text: "What you're looking at is our interactive digital twin of Finland's healthcare network." },
      { at: 15.58, text: "Here, administrators can explore hospital capacities, patient flows, and treatment bottlenecks across the entire system." },
      { at: 23.96, text: "But it's more than a visualization." },
      { at: 27.08, text: "We can introduce disruptions, simulate how they propagate through the network, and use mathematical optimization to find better ways of allocating patients." },
      { at: 37.62, text: "And unlike a conventional scheduling system, we're not just optimizing the next available appointment. We're considering the entire treatment pathway." },
      { at: 46.42, text: "For example, in one of our simulated scenarios, a major disruption reduced the percentage of patients receiving treatment within the target time to just 36.6 percent." },
      { at: 56.96, text: "After applying our recovery strategy, that increased to 53.3 percent." },
      { at: 62.38, text: "And here's the interesting part. Administrators can challenge these recommendations, explore alternative strategies, and evaluate the consequences before approving any changes." },
      { at: 72.46, text: "Right now, we're using synthetic patients and simulated hospital data. But the system is designed around structured data, creating a foundation for future integration with real healthcare systems." },
      { at: 85.44, text: "And all of this brings us back to Emma.", dur: 2.6 },
    ],
  },
  {
    id: "S8-Ending", title: "Because every day matters", durationSec: 87.5,   // 82.5 s clip + END_CARD_SEC, so the end card follows the clip instead of covering it
    clip: { file: "s8-ending.mp4", what: "Irfan to camera: approved closing (82.5 s), then the end card" },
    narration: [],
  },
];

/** Seconds into S1 when the CancerFlow logo overlays the intro clip ("Meet CancerFlow."). */
export const LOGO_AT = 16;
/** Seconds before the end of S8 when the end card takes over. */
export const END_CARD_SEC = 5;
