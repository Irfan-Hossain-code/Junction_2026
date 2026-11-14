"""Apply SQL migrations in backend/db/migrations in order. Idempotent.

    python -m backend.db.migrate            # apply pending
    python -m backend.db.migrate --reset    # DROP schema cf and re-apply (dev only)
"""
from __future__ import annotations

import argparse
from pathlib import Path

from backend.db.conn import connect

MIG_DIR = Path(__file__).resolve().parent / "migrations"


def applied(conn) -> set[str]:
    conn.execute("CREATE TABLE IF NOT EXISTS public.schema_migrations (name text PRIMARY KEY, applied_at timestamptz DEFAULT now())")
    return {r[0] for r in conn.execute("SELECT name FROM public.schema_migrations")}


def migrate(reset: bool = False) -> list[str]:
    done_now = []
    with connect() as conn:
        if reset:
            conn.execute("DROP SCHEMA IF EXISTS cf CASCADE")
            conn.execute("DROP TABLE IF EXISTS public.schema_migrations")
            conn.commit()
        have = applied(conn)
        for f in sorted(MIG_DIR.glob("*.sql")):
            if f.name in have:
                continue
            with conn.transaction():
                conn.execute(f.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO public.schema_migrations(name) VALUES (%s)", (f.name,))
            done_now.append(f.name)
    return done_now


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    a = ap.parse_args()
    out = migrate(reset=a.reset)
    print("applied:", out or "nothing (up to date)")
