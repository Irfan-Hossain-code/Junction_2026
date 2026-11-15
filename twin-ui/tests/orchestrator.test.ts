// node --test tests/orchestrator.test.ts   (Node >= 23 strips TypeScript types natively)
import { test } from "node:test";
import assert from "node:assert/strict";
import { STALE, VoiceOrchestrator } from "../src/voice/orchestrator.ts";

const deferred = () => { let r: (v: any) => void = () => {}; const p = new Promise(res => (r = res)); return { p, r }; };

test("one evaluation request -> at most one backend invocation", async () => {
  let n = 0; const d = deferred();
  const o = new VoiceOrchestrator(async () => { n++; return d.p; });
  o.start("s1");
  const a = o.call("propose_allocation", { instruction: "everyone to Kuopio" });
  const b = o.call("propose_allocation", { instruction: "everyone to Kuopio" });   // agent announces it twice
  d.r({ verdict: "worse" });
  const [ra, rb] = await Promise.all([a, b]);
  assert.equal(n, 1);
  assert.equal(rb, JSON.stringify({ verdict: "worse" }));    // latest caller gets the result...
  assert.equal(ra, STALE);                                   // ...the duplicate earlier one is not spoken twice
});

test("a changed request supersedes the old one: its result cannot produce speech", async () => {
  const slow = deferred();
  const o = new VoiceOrchestrator(async (tool, p) => (p.instruction === "everyone to Kuopio" ? slow.p : { verdict: "mpc" }));
  o.start("s1");
  const old = o.call("propose_allocation", { instruction: "everyone to Kuopio" });
  const fresh = await o.call("run_mpc", {});                  // Donny changes his mind
  slow.r({ verdict: "kuopio" });
  assert.equal(await old, STALE);
  assert.equal(fresh, JSON.stringify({ verdict: "mpc" }));
});

test("nothing is spoken or executed after the call ended", async () => {
  const slow = deferred(); let executed = 0;
  const o = new VoiceOrchestrator(async (tool) => { if (tool === "approve_plan") executed++; return slow.p; });
  o.start("s1");
  const pending = o.call("propose_allocation", { instruction: "Kuopio" });
  o.end();                                                     // Donny says goodbye / hangs up
  slow.r({ verdict: "late result" });
  assert.equal(await pending, STALE);
  assert.equal(await o.call("approve_plan", { plan_id: "x" }), STALE);
  assert.equal(executed, 0);
  assert.equal(o.status, "ended");
});

test("unavailable tools are not run and the agent is told not to offer them", async () => {
  let n = 0;
  const o = new VoiceOrchestrator(async (tool) => { n++; return tool === "get_situation" ? { available_tools: { run_mpc: false, approve_plan: false } } : {}; });
  o.start("s1");
  await o.call("get_situation");
  const r = JSON.parse(await o.call("run_mpc"));
  assert.equal(r.unavailable, true);
  assert.equal(n, 1);
});

test("ambiguous execution -> awaiting clarification (backend refuses without explicit words)", async () => {
  const o = new VoiceOrchestrator(async () => ({ committed: false, error_category: "needs_explicit_authorization" }));
  o.start("s1");
  const r = JSON.parse(await o.call("approve_plan", { plan_id: "p", donny_words: "don't submit, just redirect to Kuopio" }));
  assert.equal(r.committed, false);
  assert.equal(o.status, "awaiting_clarification");
});
