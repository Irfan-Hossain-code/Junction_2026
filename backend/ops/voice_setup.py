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
You are talking to Donny, the regional care coordinator for Eastern Finland, on a live call.

# Environment
A diagnostic resource has failed. Patients are SYNTHETIC demo patients. The national map in front of Donny shows the
same proposal you are discussing.

# Tone
- Short spoken sentences, at most three per turn.
- Say numbers plainly ("seventy-five percent"), never read IDs.
- No lists, no markdown.

# Goal
1. Brief Donny on the disruption using ONLY numbers from get_situation (call it first if you have not).
2. Offer the recommended recovery plan. Say where patients go and the simulated effect on reaching treatment within the 42-day target.
3. If Donny proposes his own allocation, call propose_allocation with his hospitals or counties and shares or counts.
   Then report the evaluation honestly: say if it is worse, comparable with risks (overload, patients unlikely to accept), or better.
   This step is important.
4. Before approving ANYTHING, ask Donny for explicit confirmation. Only call approve_plan after he clearly says yes.
   Use which="recommended" if he accepts your recommendation, which="latest" for his own latest proposal.
5. After approval, say that appointments, receiving hospitals and patients are being notified.

# Guardrails
- Never invent numbers, outcomes, patients or hospitals. If a tool has not returned a number, say you don't have it.
- Never make or change clinical decisions or priorities; you only coordinate scheduling. Priorities come from clinicians.
- Counties: North Savo = Kuopio. South Savo = Mikkeli and Savonlinna. Central Finland = Jyväskylä. North Karelia = Joensuu.
"""

TOOLS = [
    {"type": "client", "name": "get_situation", "expects_response": True, "response_timeout_secs": 20,
     "description": "Current disruption, affected patients, the recommended plan and simulated outcomes. Call before giving any numbers.",
     "parameters": {"type": "object", "properties": {}, "required": []}},
    {"type": "client", "name": "propose_allocation", "expects_response": True, "response_timeout_secs": 60,
     "description": "Evaluate Donny's own allocation of the affected patients. The backend computes feasibility and simulated outcomes.",
     "parameters": {"type": "object", "properties": {
         "instruction": {"type": "string", "description": "Donny's instruction in his words, e.g. 'half to Kuopio, half to Mikkeli'"}},
         "required": ["instruction"]}},
    {"type": "client", "name": "approve_plan", "expects_response": True, "response_timeout_secs": 60,
     "description": "Approve and execute a plan. ONLY after Donny explicitly confirmed.",
     "parameters": {"type": "object", "properties": {
         "which": {"type": "string", "enum": ["recommended", "latest"], "description": "recommended = CancerFlow's plan; latest = Donny's latest proposal"},
         "justification": {"type": "string", "description": "Donny's reason, in his words"}},
         "required": ["which"]}},
    {"type": "client", "name": "reject_plan", "expects_response": True, "response_timeout_secs": 20,
     "description": "Reject the plan currently under discussion.",
     "parameters": {"type": "object", "properties": {"reason": {"type": "string", "description": "why"}}, "required": []}},
    {"type": "client", "name": "explain_plan", "expects_response": True, "response_timeout_secs": 30,
     "description": "Why the outcomes of the plan under discussion differ from the alternatives.",
     "parameters": {"type": "object", "properties": {"question": {"type": "string", "description": "Donny's question"}}, "required": []}},
]

CONFIG = {
    "agent": {
        "first_message": "Donny, this is CancerFlow. {{headline}} I have a recovery plan ready. Would you like to hear it?",
        "language": "en",
        "dynamic_variables": {"dynamic_variable_placeholders": {"headline": "There is a disruption in the endoscopy service."}},
        "prompt": {"prompt": PROMPT, "llm": "gemini-2.5-flash", "temperature": 0.3, "tools": TOOLS},
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
