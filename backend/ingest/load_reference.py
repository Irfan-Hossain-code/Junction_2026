"""Load real Finnish reference data into the DB (idempotent: wipes + reloads reference tables).

    python -m backend.ingest.load_reference

Real (OBSERVED): country/YTA/county/municipality regions (Sotkanet + Statistics Finland boundaries),
population, Sotkanet indicators, hospital + health-centre locations (OpenStreetMap).
Labelled HYPOTHESIS: capabilities we believe but could not source. SYNTHETIC: capacity + staff (not public).
DERIVED: travel times (straight-line x road factor, stated method).
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date
from pathlib import Path

from backend.db.conn import connect

RAW = Path(__file__).resolve().parent / "raw"
ROOT = Path(__file__).resolve().parents[2]
SOTKANET_CACHE = ROOT / "data_visualization" / "data" / "sotkanet_cache.json"
TODAY = date.today()

SOURCES = [
    ("sotkanet", "THL Sotkanet indicator bank (open API)", "THL", "https://sotkanet.fi/rest/1.1", "statistic"),
    ("statfi-geo", "Statistics Finland statistical area boundaries 2025 (WFS, 1:1M)", "Tilastokeskus",
     "https://geo.stat.fi/geoserver/tilastointialueet/wfs", "dataset"),
    ("osm", "OpenStreetMap contributors, hospitals & health centres in Finland (Overpass, ODbL)", "OpenStreetMap",
     "https://www.openstreetmap.org/copyright", "dataset"),
    ("hus-rt", "HUS: Radiation therapy (HUS is Finland's largest radiotherapy unit)", "HUS",
     "https://www.hus.fi/en/node/2672", "report"),
    ("phhva-rt", "Päijät-Häme wellbeing county: new radiotherapy device at central hospital (2026-08-19)", "PHHYKY",
     "https://www.sttinfo.fi/tiedote/72243478/paijat-hameen-hyvinvointialue-uusi-sadehoitolaite-vahvistaa-syovanhoitoa?lang=fi", "report"),
    ("yle-kymsotu-rt", "Yle: Kymenlaakso central hospital radiotherapy unit (Kotka)", "Yle",
     "https://yle.fi/a/3-11040311", "report"),
    ("docrates", "Docrates Cancer Hospital (private, Helsinki): radiotherapy and oncology", "Docrates/Mehiläinen",
     "https://docrates.mehilainen.fi", "report"),
    ("cf-capability-assumption", "CancerFlow assumption: standard services of Finnish university/central/regional hospitals "
     "(high confidence, not individually sourced, verify)", None, None, "model"),
    ("cf-capacity-model", "CancerFlow synthetic capacity model: weekly capacity = catchment demand / target utilisation. "
     "Demand from Cancer Registry screening volumes + population. NOT real hospital data.", None, None, "model"),
    ("cf-travel-model", "CancerFlow travel model: great-circle distance x 1.3 road factor at 70 km/h", None, None, "model"),
]

CAPABILITIES = {
    "primary_care": "Primary care (health centre GP)",
    "colonoscopy": "Colonoscopy / endoscopy",
    "ct_imaging": "CT / MRI imaging",
    "pathology": "Histopathology",
    "mdt_crc": "Colorectal multidisciplinary team",
    "crc_surgery": "Colorectal cancer surgery",
    "oncology": "Medical oncology (systemic therapy)",
    "radiotherapy": "Radiotherapy",
}
FULL = ["colonoscopy", "ct_imaging", "pathology", "mdt_crc", "crc_surgery", "oncology"]

# curated specialist hospitals (OSM element ids verified 2026-11-14)
# (osm_type, osm_id, our_id, display name, kind, radiotherapy_source or None, rt_tag)
SPECIALIST = [
    ("w", 442872034, "fi-hosp-hus", "HUS Helsinki University Hospital (Meilahti)", "university_hospital", "hus-rt", "OBSERVED"),
    ("w", 640611323, "fi-hosp-tays", "Tays Tampere University Hospital", "university_hospital", "cf-capability-assumption", "HYPOTHESIS"),
    ("r", 12396991, "fi-hosp-tyks", "Tyks Turku University Hospital", "university_hospital", "cf-capability-assumption", "HYPOTHESIS"),
    ("w", 592461677, "fi-hosp-kys", "KYS Kuopio University Hospital", "university_hospital", "cf-capability-assumption", "HYPOTHESIS"),
    ("w", 50123447, "fi-hosp-oys", "OYS Oulu University Hospital", "university_hospital", "cf-capability-assumption", "HYPOTHESIS"),
    ("w", 1120192611, "fi-hosp-phks", "Päijät-Häme Central Hospital (Lahti)", "central_hospital", "phhva-rt", "OBSERVED"),
    ("w", 413112118, "fi-hosp-kymks", "Kymenlaakso Central Hospital (Kotka)", "central_hospital", "yle-kymsotu-rt", "OBSERVED"),
    ("w", 1019105781, "fi-hosp-nova", "Central Finland Hospital Nova (Jyväskylä)", "central_hospital", "cf-capability-assumption", "HYPOTHESIS"),
    ("w", 156291258, "fi-hosp-seks", "Seinäjoki Central Hospital", "central_hospital", "cf-capability-assumption", "HYPOTHESIS"),
    ("w", 708319387, "fi-hosp-vks", "Vaasa Central Hospital", "central_hospital", "cf-capability-assumption", "HYPOTHESIS"),
    ("n", 13034415669, "fi-hosp-satas", "Satasairaala (Pori)", "central_hospital", "cf-capability-assumption", "HYPOTHESIS"),
    ("w", 49083505, "fi-hosp-pkks", "North Karelia Central Hospital (Joensuu)", "central_hospital", "cf-capability-assumption", "HYPOTHESIS"),
    ("n", 3004516194, "fi-hosp-ekks", "South Karelia Central Hospital (Lappeenranta)", "central_hospital", None, None),
    ("w", 46599245, "fi-hosp-mks", "Mikkeli Central Hospital", "central_hospital", None, None),
    ("w", 50284801, "fi-hosp-lks", "Lapland Central Hospital (Rovaniemi)", "central_hospital", None, None),
    ("w", 398676288, "fi-hosp-kaks", "Kainuu Central Hospital (Kajaani)", "central_hospital", None, None),
    ("w", 426248176, "fi-hosp-kpks", "Central Ostrobothnia Central Hospital (Kokkola)", "central_hospital", None, None),
    ("r", 17009812, "fi-hosp-khks", "Kanta-Häme Central Hospital (Hämeenlinna)", "central_hospital", None, None),
    ("w", 1363312081, "fi-hosp-lpks", "Länsi-Pohja Central Hospital (Kemi)", "central_hospital", None, None),
    ("w", 27746622, "fi-hosp-hyvinkaa", "Hyvinkää Hospital", "regional_hospital", None, None),
    ("w", 245472846, "fi-hosp-lohja", "Lohja Hospital", "regional_hospital", None, None),
    ("w", 260411981, "fi-hosp-porvoo", "Porvoo Hospital", "regional_hospital", None, None),
    ("w", 499397116, "fi-hosp-jorvi", "Jorvi Hospital (Espoo)", "regional_hospital", None, None),
    ("w", 449900889, "fi-hosp-savonlinna", "Savonlinna Hospital", "regional_hospital", None, None),
    ("w", 399193083, "fi-hosp-salo", "Salo Regional Hospital", "regional_hospital", None, None),
    ("w", 128405772, "fi-hosp-rauma", "Rauma Hospital", "regional_hospital", None, None),
    ("w", 448731780, "fi-hosp-forssa", "Forssa Hospital", "regional_hospital", None, None),
    ("w", 1368054185, "fi-hosp-raahe", "Raahe Hospital", "regional_hospital", None, None),
    ("w", 401417681, "fi-hosp-raasepori", "Raasepori Hospital", "regional_hospital", None, None),
    ("w", 96112818, "fi-hosp-docrates", "Docrates Cancer Hospital (private)", "private_clinic", "docrates", "OBSERVED"),
]
REGIONAL_CAPS = ["colonoscopy", "ct_imaging"]
EXCLUDE = re.compile(r"psykiatr|psychiatr|hammas|eläin|vet|päihde|kuntoutus|rehab|Kainuun prikaatin|Docrates|"
                     r"Seinäjoen keskussairaala,|Tays R-|OYS Psykiatria|HYKS Vanhuspsyk", re.I)
HC = re.compile(r"terveysasema|terveyskeskus|hälsostation|hälsocentral|sosiaali- ja terveys|terveys- ja hyvinvointi|"
                r"hyvinvointikeskus|lähiklinikka|perhekeskus|terveysasemapalvelut|health centre", re.I)


def osm_point(e):
    if e["type"] == "node":
        return e["lon"], e["lat"]
    return e["center"]["lon"], e["center"]["lat"]


def main():
    sot_regions = json.loads((RAW / "sotkanet_regions.json").read_text(encoding="utf-8"))
    by_id = {r["id"]: r for r in sot_regions}
    counties_geo = json.loads((RAW / "statfi_counties_2025.json").read_text(encoding="utf-8"))
    munis_geo = json.loads((RAW / "statfi_municipalities_2025.json").read_text(encoding="utf-8"))
    pop = {r["region"]: r["value"] for r in json.loads((RAW / "sotkanet_population_2024.json").read_text())}
    osm = json.loads((RAW / "osm_health_fi.json").read_text(encoding="utf-8"))["elements"]
    cache = json.loads(SOTKANET_CACHE.read_text(encoding="utf-8"))

    with connect() as conn:
        with conn.transaction():
            # wipe reference data (operational tables reference facility/region -> only if empty)
            used = conn.execute("SELECT count(*) FROM event").fetchone()[0]
            conn.execute("TRUNCATE travel_time, staff, facility_capacity, facility_capability, indicator_value, indicator")
            if used == 0:
                conn.execute("TRUNCATE app_user, appointment, alert, pathway_clock, pathway, patient CASCADE")
                conn.execute("DELETE FROM facility")
                conn.execute("UPDATE region SET parent_id=NULL")
                conn.execute("DELETE FROM region")
            for sid, title, pub, url, kind in SOURCES:
                conn.execute("""INSERT INTO source(id,title,publisher,url,accessed_on,kind) VALUES (%s,%s,%s,%s,%s,%s)
                                ON CONFLICT (id) DO UPDATE SET title=EXCLUDED.title, url=EXCLUDED.url, accessed_on=EXCLUDED.accessed_on""",
                             (sid, title, pub, url, TODAY, kind))
            for code, desc in CAPABILITIES.items():
                conn.execute("INSERT INTO capability(code,description) VALUES (%s,%s) ON CONFLICT (code) DO UPDATE SET description=EXCLUDED.description",
                             (code, desc))

            # ---------------- regions
            conn.execute("""INSERT INTO region(id,kind,name_fi,name_en,sotkanet_id,population,source_id)
                            VALUES ('fi','country','Suomi','Finland',NULL,%s,'sotkanet') ON CONFLICT (id) DO NOTHING""",
                         (sum(v for k, v in pop.items() if by_id.get(k, {}).get("category") == "HYVINVOINTIALUE"),))
            for r in sot_regions:
                if r["category"] == "YTA" and r["title"]["fi"] != "Ahvenanmaa":
                    conn.execute("""INSERT INTO region(id,kind,name_fi,name_en,parent_id,sotkanet_id,source_id)
                                    VALUES (%s,'yta',%s,%s,'fi',%s,'sotkanet') ON CONFLICT (id) DO NOTHING""",
                                 (f"yta-{r['id']}", r["title"]["fi"], r["title"].get("en"), r["id"]))
            cgeo = {f["properties"]["hyvinvointialue"]: f for f in counties_geo["features"]}
            for r in sot_regions:
                if r["category"] != "HYVINVOINTIALUE" or r["code"] not in cgeo:
                    continue
                yta = next((f"yta-{m}" for m in r["memberOf"] if by_id.get(m, {}).get("category") == "YTA"
                            and by_id[m]["title"]["fi"] != "Ahvenanmaa"), None)
                g = json.dumps(cgeo[r["code"]]["geometry"])
                conn.execute("""INSERT INTO region(id,kind,name_fi,name_en,parent_id,sotkanet_id,population,boundary,centroid,source_id)
                                VALUES (%s,'wellbeing_county',%s,%s,%s,%s,%s, ST_GeomFromGeoJSON(%s)::geography,
                                        ST_PointOnSurface(ST_GeomFromGeoJSON(%s))::geography,'statfi-geo')
                                ON CONFLICT (id) DO UPDATE SET population=EXCLUDED.population""",
                             (f"hva-{r['id']}", r["title"]["fi"], r["title"].get("en"), yta, r["id"], pop.get(r["id"]), g, g))
            mgeo = {f["properties"]["kunta"]: f for f in munis_geo["features"]}
            n_mun = 0
            for r in sot_regions:
                if r["category"] != "KUNTA" or r["code"] not in mgeo:
                    continue
                hva = next((f"hva-{m}" for m in r["memberOf"] if by_id.get(m, {}).get("category") == "HYVINVOINTIALUE"), None)
                if hva is None:
                    continue   # Åland municipalities
                g = json.dumps(mgeo[r["code"]]["geometry"])
                conn.execute("""INSERT INTO region(id,kind,name_fi,name_en,parent_id,sotkanet_id,population,boundary,centroid,source_id)
                                VALUES (%s,'municipality',%s,%s,%s,%s,%s, ST_GeomFromGeoJSON(%s)::geography,
                                        ST_PointOnSurface(ST_GeomFromGeoJSON(%s))::geography,'statfi-geo')
                                ON CONFLICT (id) DO UPDATE SET population=EXCLUDED.population""",
                             (f"kunta-{r['code']}", r["title"]["fi"], r["title"].get("en"), hva, r["id"], pop.get(r["id"]), g, g))
                n_mun += 1

            # ---------------- indicators (from the data_visualization Sotkanet snapshot)
            known = {r[0]: r[1] for r in conn.execute("SELECT sotkanet_id, id FROM region WHERE sotkanet_id IS NOT NULL")}
            maa = next(r["id"] for r in sot_regions if r["category"] == "MAA")
            known[maa] = "fi"
            n_ind = 0
            for ind, blob in cache["indicators"].items():
                title = (blob.get("title") or {}).get("en") or f"Sotkanet {ind}"
                conn.execute("INSERT INTO indicator(id,title,source_id) VALUES (%s,%s,'sotkanet')", (f"sotkanet:{ind}", title))
                rows = [(f"sotkanet:{ind}", known[x["region"]], x["year"], x["gender"], x["value"])
                        for x in blob["rows"] if x["region"] in known]
                with conn.cursor() as cur:
                    cur.executemany("""INSERT INTO indicator_value(indicator_id,region_id,year,gender,value) VALUES (%s,%s,%s,%s,%s)
                                       ON CONFLICT DO NOTHING""", rows)
                n_ind += len(rows)

            # ---------------- facilities
            osm_by = {(e["type"][0], e["id"]): e for e in osm}
            spec_ids = {(t, i) for t, i, *_ in SPECIALIST}
            fac_rows = []
            for t, i, fid, name, kind, rt_src, rt_tag in SPECIALIST:
                e = osm_by.get((t, i))
                if not e:
                    print("  !! missing OSM element", t, i, name)
                    continue
                lon, lat = osm_point(e)
                fac_rows.append((fid, name, kind, lon, lat, f"{e['type']}/{e['id']}"))
            seen = set()
            for e in osm:
                if (e["type"][0], e["id"]) in spec_ids:
                    continue
                name = e["tags"].get("name")
                if not name or EXCLUDE.search(name):
                    continue
                kind = "health_center" if HC.search(name) else None
                if kind is None:
                    continue          # keep the twin readable: specialist hospitals are curated, rest = health centres
                lon, lat = osm_point(e)
                key = (name.lower(), round(lon, 2), round(lat, 2))
                if key in seen:
                    continue
                seen.add(key)
                fac_rows.append((f"fi-hc-osm-{e['type'][0]}{e['id']}", name, kind, lon, lat, f"{e['type']}/{e['id']}"))
            with conn.cursor() as cur:
                cur.executemany("""INSERT INTO facility(id,name,kind,geom,tag,source_id,external_ref)
                                   VALUES (%s,%s,%s,ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography,'OBSERVED','osm',%s)
                                   ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name, kind=EXCLUDED.kind, geom=EXCLUDED.geom""",
                                [(a, b, c, d, e_, f) for a, b, c, d, e_, f in fac_rows])
            # spatial join -> county + YTA
            conn.execute("""UPDATE facility f SET region_id = r.id, yta_id = r.parent_id
                            FROM region r WHERE r.kind='wellbeing_county'
                              AND ST_Intersects(r.boundary::geometry, f.geom::geometry)""")
            # points just outside simplified coastlines -> nearest county
            conn.execute("""UPDATE facility f SET (region_id, yta_id) = (
                              SELECT r.id, r.parent_id FROM region r WHERE r.kind='wellbeing_county'
                              ORDER BY ST_Distance(r.boundary, f.geom) LIMIT 1)
                            WHERE f.region_id IS NULL""")

            # ---------------- capabilities
            caps = []
            for _, _, fid, name, kind, rt_src, rt_tag in SPECIALIST:
                if kind in ("university_hospital", "central_hospital"):
                    tag = "HYPOTHESIS"
                    caps += [(fid, c, tag, "cf-capability-assumption", "standard service of a central/university hospital") for c in FULL]
                elif kind == "regional_hospital":
                    caps += [(fid, c, "HYPOTHESIS", "cf-capability-assumption", "regional hospital endoscopy/imaging") for c in REGIONAL_CAPS]
                elif kind == "private_clinic":
                    caps += [(fid, "oncology", "OBSERVED", "docrates", None)]
                if rt_src:
                    caps.append((fid, "radiotherapy", rt_tag, rt_src, None))
            caps += [(r[0], "primary_care", "OBSERVED", "osm", "tagged as health centre in OSM")
                     for r in conn.execute("SELECT id FROM facility WHERE kind='health_center'")]
            with conn.cursor() as cur:
                cur.executemany("""INSERT INTO facility_capability(facility_id,capability,tag,source_id,note) VALUES (%s,%s,%s,%s,%s)
                                   ON CONFLICT (facility_id,capability) DO UPDATE SET tag=EXCLUDED.tag, source_id=EXCLUDED.source_id""", caps)

            # ---------------- travel times (municipality centroid -> specialist facilities)
            conn.execute("""INSERT INTO travel_time(origin_region_id,facility_id,minutes,km,method,tag)
                            SELECT m.id, f.id,
                                   ST_Distance(m.centroid, f.geom)/1000*1.3/70*60,
                                   ST_Distance(m.centroid, f.geom)/1000*1.3,
                                   'great-circle x1.3 @70km/h (cf-travel-model)', 'DERIVED'
                            FROM region m, facility f
                            WHERE m.kind='municipality' AND f.kind <> 'health_center'""")

            synthetic_capacity(conn)

        counts = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                  for t in ["region", "indicator_value", "facility", "facility_capability", "travel_time", "facility_capacity", "staff"]}
        print("loaded:", counts, f"(municipalities: {n_mun}, indicator rows: {n_ind})")


def synthetic_capacity(conn):
    """SYNTHETIC weekly capacity per (hospital, capability). Clearly a model, not data.

    demand_r(cap) = sum over municipalities whose NEAREST capable hospital is r of  pop_m * annual_rate(cap) / 52
    capacity = demand / target_utilisation (0.85), range +-20 %.
    Annual rates per 100k population (assumptions, documented):
      colonoscopy: screening 10,244/yr (OBSERVED, FCR 2024) scaled x3 for symptomatic+surveillance (ASSUMPTION)
      others: derived from ~4,000 colorectal cancers/yr nationally (ASSUMPTION, order of magnitude)
    """
    fin_pop = conn.execute("SELECT population FROM region WHERE id='fi'").fetchone()[0] or 5.6e6
    per100k = {
        "colonoscopy": 10244 * 3 / fin_pop * 1e5,
        "ct_imaging": 4000 * 2.0 / fin_pop * 1e5,
        "pathology": 10244 * 3 * 0.5 / fin_pop * 1e5,
        "mdt_crc": 4000 * 1.2 / fin_pop * 1e5,
        "crc_surgery": 4000 * 0.7 / fin_pop * 1e5,
        "oncology": 4000 * 0.6 / fin_pop * 1e5,
        "radiotherapy": 4000 * 0.15 / fin_pop * 1e5,
    }
    rows = conn.execute("""
        WITH nearest AS (
          SELECT DISTINCT ON (m.id, fc.capability) m.id AS mun, m.population AS pop, fc.capability, t.facility_id
          FROM region m JOIN travel_time t ON t.origin_region_id = m.id
          JOIN facility_capability fc ON fc.facility_id = t.facility_id
          WHERE m.kind='municipality' AND fc.capability <> 'primary_care'
          ORDER BY m.id, fc.capability, t.minutes)
        SELECT facility_id, capability, sum(coalesce(pop,0)) FROM nearest GROUP BY 1,2""").fetchall()
    out, staff = [], {}
    rng_seed = 7
    for fid, cap, catchment in rows:
        if cap not in per100k:
            continue
        demand_wk = float(catchment) / 1e5 * per100k[cap] / 52
        cap_wk = demand_wk / 0.85
        # deterministic +-15 % jitter so not every hospital is identically balanced
        h = int(hashlib.md5(f"{fid}|{cap}|{rng_seed}".encode()).hexdigest(), 16)
        j = 1 + 0.08 * math.sin(h % 1000)
        mid = max(cap_wk * j, 0.5)
        out.append((fid, cap, round(mid * 0.8, 2), round(mid * 1.2, 2)))
        if cap == "colonoscopy":
            staff[(fid, "gastroenterologist")] = round(mid / 25, 1)     # ~25 colonoscopies / FTE / week (ASSUMPTION)
        if cap == "crc_surgery":
            staff[(fid, "colorectal_surgeon")] = round(max(mid / 3, 0.5), 1)
        if cap == "oncology":
            staff[(fid, "oncologist")] = round(max(mid / 4, 0.5), 1)
    with conn.cursor() as cur:
        cur.executemany("""INSERT INTO facility_capacity(facility_id,capability,per_week_low,per_week_high,tag,source_id)
                           VALUES (%s,%s,%s,%s,'SYNTHETIC','cf-capacity-model')""", out)
        cur.executemany("""INSERT INTO staff(facility_id,role,fte,tag,source_id) VALUES (%s,%s,%s,'SYNTHETIC','cf-capacity-model')""",
                        [(f, r, v) for (f, r), v in staff.items()])


if __name__ == "__main__":
    main()
