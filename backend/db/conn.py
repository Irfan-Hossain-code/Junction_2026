from __future__ import annotations

import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
load_dotenv(ROOT / ".env.local")   # ElevenLabs CLI writes ELEVENLABS_API_KEY here (gitignored)


def connect(**kw) -> psycopg.Connection:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL not set (copy .env.example to .env)")
    # search_path as a startup option -> no implicit transaction is opened on connect
    return psycopg.connect(url, connect_timeout=15, options="-c search_path=cf,public", **kw)
