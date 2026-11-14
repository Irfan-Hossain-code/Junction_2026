"""Escalation channels for urgent recommendations that cannot wait for routine review.

Recommendations are NEVER executed automatically. An escalation only notifies a human (on-call clinician /
coordinator) who decides. A voice channel (e.g. ElevenLabs text-to-speech call) is a FUTURE option that would
only deliver the same notification; it grants no authority to transfer patients.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class EscalationChannel(Protocol):
    def notify(self, action: dict) -> dict: ...


@dataclass
class LogChannel:
    """Default: record the escalation (in-memory; the API exposes it). No external calls."""
    sent: list = field(default_factory=list)

    def notify(self, action: dict) -> dict:
        rec = {"channel": "log", "pid": action["pid"], "reason": action.get("escalation_reason")}
        self.sent.append(rec)
        return rec


@dataclass
class VoiceCallChannel:
    """Placeholder for a future ElevenLabs voice call to on-call staff. Disabled: never places a call."""
    enabled: bool = False

    def notify(self, action: dict) -> dict:
        return {"channel": "voice", "pid": action["pid"], "status": "not_configured" if not self.enabled else "unimplemented"}
