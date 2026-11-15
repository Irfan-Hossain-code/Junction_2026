"""Create / update the ElevenLabs conversational agent for Donny (the regional care coordinator).

    python -m backend.ops.voice_setup          # creates the agent once, writes ELEVENLABS_AGENT_ID to .env.local

The agent only talks; every number and every action goes through CLIENT TOOLS that the browser executes against
our backend (/ops/*), the same endpoints the map buttons use. So voice and map share one proposal state.
"""
from __future__ import annotations

import os
import pathlib

from backend.db.conn import ROOT  # noqa: F401  (loads .env and .env.local)

PROMPT = """# Personality
You are CancerFlow, the operations assistant of Finland's cancer-care coordination system. Calm, concise, precise.
You are on a live call with Donny, the regional care coordinator for Eastern Finland.

# Environment
A diagnostic resource has failed. Patients are SYNTHETIC demo patients. Donny's map shows the same plans you discuss.
You have REAL tools connected to CancerFlow's whole-pathway MPC. Use them; never answer from memory.

# Tone
- Short spoken sentences, at most three per turn. Say numbers plainly. Never read IDs aloud.

# Goal
1. Start with get_situation. Brief Donny with its numbers only.
2. "Use MPC", "find a solution", "optimise": call run_mpc. Do NOT ask Donny to name hospitals first.
3. "Which option is best / maximises everyone's outcome": call compare_plans and report the top-ranked option and
   the tier that decided it. The ranking follows the clinical hierarchy: urgent patients first, then severe delays,
   then everyone within target, then lateness, treatment start, travel. If every option is 0 % within target they are
   NOT equal: say which one has the least urgent lateness and severe delay.
4. Metrics: colonoscopy day = diagnostic appointment; diagnosis day = histology result; TREATMENT day = when treatment
   starts. When Donny asks about treatment, use mean_treatment_day / expected_treatment_day_if_needed, never colonoscopy.
   If a metric is missing, say so.
5. Donny's own allocation ("half to Kuopio..."): propose_allocation, then report the verdict honestly.
6. Approval: only after Donny clearly says yes to a SPECIFIC plan. Call approve_plan with that plan_id (from the latest
   tool result). Only say it is done if the result has committed = true. If committed = false, say the error category
   in plain words; if it is stale_plan, explain that a fresh plan was computed and ask Donny to approve new_plan_id.
   Never call approve_plan more than twice for the same request. Use check_approval if unsure.
7. After approval: appointments are re-booked and patients and receiving hospitals are notified.

# Conversation control
- Intent first: question, hypothetical ("what if everyone went to Kuopio"), evaluation, recommendation, execution, cancel, goodbye.
- propose_allocation and run_mpc are ALWAYS hypothetical evaluations; say so if Donny sounds like he wants to execute.
- "Don't submit any plans" / "hold everything": call set_hold with hold=true. Only Donny lifting it (set_hold hold=false) re-enables approval.
- If Donny's words mix "don't submit" with "redirect everybody", ask ONE short question: evaluate it, or actually execute it?
- approve_plan needs donny_words = Donny's exact authorising sentence. If it returns needs_explicit_authorization, ask once.
- If a tool result says stale: true, ignore it completely and never mention it.
- If a tool result says unavailable: true or available_tools marks a tool false, do not offer that capability; say why.
- For long evaluations give ONE short acknowledgement, then wait. Do not announce the same evaluation twice.
- Do not say "Understood" repeatedly, do not apologise generically, do not ask for approval after every answer.
- When Donny says goodbye or ends the call, say one short closing sentence and call end_call. Say nothing after that.

# Guardrails
- Never invent numbers, patients, hospitals or outcomes. Every number comes from a tool result.
- Never change clinical priorities or make clinical decisions; you coordinate scheduling only.
- Counties: North Savo = Kuopio. South Savo = Mikkeli and Savonlinna. Central Finland = Jyväskylä. North Karelia = Joensuu.
"""

_T = lambda name, desc, props=None, req=None, timeout=30: {
    "type": "client", "name": name, "description": desc, "expects_response": True, "response_timeout_secs": timeout,
    "parameters": {"type": "object", "properties": props or {}, "required": req or []}}
TOOLS = [
    _T("get_situation", "Active outage, affected patients, current plan (id, status, KPIs, provenance) and objective hierarchy."),
    _T("run_mpc", "Run the whole-pathway MPC now on the current state; returns a fresh plan and a ranking of options.", timeout=90),
    _T("compare_plans", "Rank all evaluated options (local repair, MPC plan, Donny's proposals) by the clinical hierarchy."),
    _T("get_plan_details", "Per-patient details of a plan: destination, colonoscopy date, expected treatment day, chance within target.",
       {"plan_id": {"type": "string", "description": "plan id from a previous tool result; empty for the recommended plan"}}),
    _T("propose_allocation", "Evaluate Donny's own allocation of the affected patients (backend computes feasibility and outcomes).",
       {"instruction": {"type": "string", "description": "Donny's instruction in his words"}}, ["instruction"], 90),
    _T("approve_plan", "Approve and execute ONE explicitly confirmed plan. Returns committed true/false and an error category.",
       {"plan_id": {"type": "string", "description": "the plan id Donny approved"},
        "donny_words": {"type": "string", "description": "Donny's exact sentence authorising execution, verbatim"},
        "justification": {"type": "string", "description": "Donny's reason in his words"}}, ["plan_id", "donny_words"], 60),
    _T("set_hold", "Put all plan approvals on hold (hold=true) when Donny says not to submit/execute anything; hold=false lifts it.",
       {"hold": {"type": "boolean", "description": "true = hold, false = lift"}, "reason": {"type": "string", "description": "Donny's words"}}, ["hold"]),
    _T("check_approval", "Check whether a plan approval was committed.", {"plan_id": {"type": "string", "description": "plan id"}}, ["plan_id"]),
    _T("reject_plan", "Reject the plan under discussion.", {"reason": {"type": "string", "description": "why"}}),
    _T("explain_plan", "Why the outcomes of the plan under discussion differ from the alternatives.",
       {"question": {"type": "string", "description": "Donny's question"}}),
]

CONFIG = {
    "agent": {
        "first_message": "Donny, this is CancerFlow. {{headline}} I have a recovery plan ready. Would you like to hear it?",
        "language": "en",
        "dynamic_variables": {"dynamic_variable_placeholders": {"headline": "There is a disruption in the endoscopy service."}},
        "prompt": {"prompt": PROMPT, "llm": "gemini-2.5-flash", "temperature": 0.3, "tools": TOOLS,
                   "built_in_tools": {"end_call": {"name": "end_call", "description": "End the call after Donny says goodbye.",
                                                   "params": {"system_tool_type": "end_call"}}}},
    },
    "tts": {"voice_id": "onwK4e9ZLuTAKqWW03F9"},
}


def main():
    from elevenlabs import ElevenLabs
    c = ElevenLabs()
    agent_id = os.environ.get("ELEVENLABS_AGENT_ID")
    if agent_id:
        c.conversational_ai.agents.update(agent_id, name="CancerFlow Ops (Donny)", conversation_config=CONFIG)
        print("updated", agent_id)
        return
    a = c.conversational_ai.agents.create(name="CancerFlow Ops (Donny)", conversation_config=CONFIG)
    env = pathlib.Path(ROOT) / ".env.local"
    with env.open("a", encoding="utf-8") as f:
        f.write(f"\nELEVENLABS_AGENT_ID={a.agent_id}\n")
    print("created", a.agent_id)


if __name__ == "__main__":
    main()
