"""Thin client for the Nemotron (OpenAI-compatible) endpoint + numeric grounding check."""
from __future__ import annotations

import json
import os
import re
from typing import Optional

import httpx

from backend.db.conn import ROOT  # noqa: F401  (loads .env)


def available() -> bool:
    return bool(os.environ.get("VERDA_BASE_URL") and os.environ.get("VERDA_INFERENCE_KEY"))


def chat(messages: list[dict], max_tokens: int = 1500, temperature: float = 0.2, json_mode: bool = False,
         timeout: float = 45.0, think: bool = False) -> Optional[str]:
    """Nemotron is a reasoning model; thinking costs ~1k tokens and 2-3x latency, so it is OFF by default for
    these grounded, short tasks (summaries over given facts, text -> fixed JSON schema)."""
    if not available():
        return None
    body = {"model": os.environ.get("VERDA_MODEL", "nemotron-3.5-super-vl"), "messages": messages,
            "max_tokens": max_tokens, "temperature": temperature,
            "chat_template_kwargs": {"enable_thinking": think}}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    try:
        r = httpx.post(os.environ["VERDA_BASE_URL"].rstrip("/") + "/chat/completions", json=body, timeout=timeout,
                       headers={"Authorization": f"Bearer {os.environ['VERDA_INFERENCE_KEY']}"})
        r.raise_for_status()
        return (r.json()["choices"][0]["message"].get("content") or "").strip()
    except Exception:
        return None


def extract_json(text: Optional[str]) -> Optional[dict]:
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


NUM = re.compile(r"(?<![\w.])[-+]?\d+(?:[.,]\d+)?")


def allowed_numbers(facts) -> set[float]:
    """Every number that appears in the facts, plus percent/rounded variants."""
    out: set[float] = set()

    def walk(x):
        if isinstance(x, bool):
            return
        if isinstance(x, (int, float)):
            v = float(x)
            for cand in (v, v * 100):
                out.update({round(cand, 0), round(cand, 1), round(cand, 2)})
        elif isinstance(x, str):
            for n in NUM.findall(x):
                walk(float(n.replace(",", ".")))
        elif isinstance(x, dict):
            for k, v in x.items():
                walk(k)
                walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v)
    walk(facts)
    return out


def ungrounded_numbers(text: str, facts) -> list[str]:
    ok = allowed_numbers(facts)
    bad = []
    for n in NUM.findall(text):
        v = abs(float(n.replace(",", ".")))
        if v <= 10 and float(v).is_integer():        # small counts / ordinals ("3 recommendations")
            continue
        if 2000 <= v <= 2040:                        # years
            continue
        if not any(abs(v - a) <= 0.6 for a in ok):
            bad.append(n)
    return bad
