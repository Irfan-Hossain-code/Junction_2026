"""Reset Emma's demo story to its deterministic initial state (story day 0, 08:30; nothing booked yet).

    python reset_story.py            # from the repo root (uses DATABASE_URL from .env)

Touches ONLY the story namespace: ops_* tables (resources, slots, disruptions, interventions, notifications, story
state), persona agents of OPS-* patients, the demo clock. Previous story pathways are closed with a WITHDRAWN event
(the event log is append-only, nothing is deleted). National scenarios, sim runs, other patients and Shams's data are
untouched. Idempotent: running it twice gives the same initial state (new run number, identical content).
"""
from backend.db.conn import connect
from backend.ops import story


def main() -> dict:
    with connect() as conn:
        st = story.reset(conn)
        ov = story.overview(conn)
    print(f"story reset: run {st['run']}, Emma = {st['emma_patient']}, {len(st['cohort'])} other patients on Endoscopy Unit 2, "
          f"clock = story day {ov['story_day']}, next step = {ov['next']}")
    return st


if __name__ == "__main__":
    main()
