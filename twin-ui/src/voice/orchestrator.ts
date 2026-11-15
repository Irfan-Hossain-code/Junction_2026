// Voice-call orchestration for the ElevenLabs client tools (pure, no browser APIs: unit-tested with node --test).
//
// - Session + status lifecycle: idle -> listening/responding/waiting_for_tool/... -> ending -> ended.
// - One request id per tool call; identical in-flight requests share ONE backend invocation (no duplicate evaluations).
// - A newer request in the same family supersedes older ones: their results come back as STALE (the agent must not speak them).
// - After the call ends every pending or new tool result is STALE and nothing is executed.
// - Execution tools are blocked unless the backend says they are available (capability awareness).

export type Status = "idle" | "listening" | "responding" | "waiting_for_tool" | "awaiting_clarification" | "awaiting_approval" | "ending" | "ended";
export type Exec = (tool: string, params: any) => Promise<any>;

const FAMILY: Record<string, string> = {
  run_mpc: "evaluate", propose_allocation: "evaluate", compare_plans: "evaluate",
  approve_plan: "approve", reject_plan: "approve", set_hold: "hold",
  get_situation: "info", get_plan_details: "info", check_approval: "info", explain_plan: "info",
};
export const STALE = JSON.stringify({ stale: true, instruction: "This result is out of date (superseded or the call ended). Do not mention it." });

export class VoiceOrchestrator {
  sessionId: string | null = null;
  status: Status = "idle";
  private seq = 0;
  private latest: Record<string, number> = {};
  private inflight = new Map<string, Promise<any>>();
  available: Record<string, boolean> = {};
  calls: { id: number; tool: string; state: "running" | "done" | "stale" | "blocked" }[] = [];
  onChange?: () => void;

  private exec: Exec;
  constructor(exec: Exec) { this.exec = exec; }

  start(sessionId: string) { this.sessionId = sessionId; this.status = "listening"; this.changed(); }
  setMode(mode: string) { if (this.status !== "ended" && this.status !== "ending") { this.status = mode === "speaking" ? "responding" : "listening"; this.changed(); } }
  end() { this.status = "ended"; this.inflight.clear(); this.changed(); }
  get ended() { return this.status === "ended" || this.status === "ending"; }

  private changed() { this.onChange?.(); }

  /** Called by every client tool. Returns the JSON string handed back to the agent. */
  async call(tool: string, params: any = {}): Promise<string> {
    if (this.ended) return STALE;
    const fam = FAMILY[tool] ?? tool;
    if ((tool === "run_mpc" || tool === "propose_allocation" || tool === "approve_plan") && this.available[tool] === false) {
      this.calls.push({ id: ++this.seq, tool, state: "blocked" });
      return JSON.stringify({ unavailable: true, tool, instruction: "This capability is not available right now; do not offer it. Explain why using get_situation." });
    }
    const key = tool + ":" + JSON.stringify(params ?? {});
    const id = ++this.seq;
    this.latest[fam] = id;
    const rec: { id: number; tool: string; state: "running" | "done" | "stale" | "blocked" } = { id, tool, state: "running" };
    this.calls.push(rec);
    const prev = this.status;
    this.status = "waiting_for_tool"; this.changed();
    let p = this.inflight.get(key);
    if (!p) {                                                     // identical request already running: share it
      p = this.exec(tool, params).finally(() => this.inflight.delete(key));
      this.inflight.set(key, p);
    }
    let out: any;
    try { out = await p; } catch (e: any) { out = { error: String(e?.message || e) }; }
    if (this.ended || this.latest[fam] !== id) { rec.state = "stale"; this.changed(); return STALE; }
    rec.state = "done";
    if (out && typeof out === "object" && out.available_tools) this.available = { ...this.available, ...out.available_tools };
    this.status = out?.committed === false && out?.error_category === "needs_explicit_authorization" ? "awaiting_clarification"
      : tool === "run_mpc" || tool === "propose_allocation" ? "awaiting_approval" : prev === "waiting_for_tool" ? "listening" : prev;
    this.changed();
    return JSON.stringify(out);
  }
}
