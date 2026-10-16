"""Write the static JSON files that the /ews_news page reads.

Output (see docs/data-schema.md):
  manifest.json            last update, run status, list of monthly files, filter values
  stories-YYYY-MM.json     stories first seen in that month, newest first
"""

import json
from collections import defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import db

SCHEMA_VERSION = 1


def _write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    tmp.replace(path)


def local_time(iso_utc: str, tz: str) -> str:
    dt = datetime.strptime(iso_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo(tz)).isoformat(timespec="minutes")


def publish(conn, config) -> dict:
    out_dir = config.path("site_data")
    tz = config.settings["timezone"]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    today_local = datetime.now(ZoneInfo(tz)).date().isoformat()

    by_month = defaultdict(list)
    facets = defaultdict(set)
    for row in conn.execute("SELECT data_json FROM stories WHERE data_json != '{}'"):
        s = db.loads(row["data_json"])
        s["first_seen_local"] = local_time(s["first_seen"], tz)
        s["is_new"] = s["first_seen_local"][:10] == today_local
        by_month[s["first_seen"][:7]].append(s)
        facets["hazards"].update(s["hazards"])
        facets["wmo_regions"].update(s["wmo_regions"])
        facets["countries"].update(f"{c['code']}|{c['name']}" for c in s["countries"])
        facets["agency_types"].update(s["agency_types"])
        facets["pillars"].update(s["pillars"])
        facets["outcomes"].add(s["outcome"])
        facets["languages"].update(s["languages"])

    months = []
    for month in sorted(by_month, reverse=True):
        stories = sorted(by_month[month], key=lambda s: s["last_updated"], reverse=True)
        fname = f"stories-{month}.json"
        _write(out_dir / fname, {"schema_version": SCHEMA_VERSION, "month": month,
                                 "stories": stories})
        months.append({"month": month, "file": fname, "count": len(stories)})

    last_run = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    last_ok = conn.execute(
        "SELECT finished_at FROM runs WHERE status IN ('success', 'partial') "
        "ORDER BY id DESC LIMIT 1").fetchone()
    countries = sorted(facets.pop("countries", set()), key=lambda x: x.split("|")[1])
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now,
        "generated_at_local": local_time(now, tz),
        "timezone": tz,
        "today_local": today_local,
        "stale_after_hours": config.settings["publish"]["stale_after_hours"],
        "last_run": {
            "status": last_run["status"],
            "started_at": last_run["started_at"],
            "finished_at": last_run["finished_at"],
            "stats": db.loads(last_run["stats_json"]),
        } if last_run else None,
        "last_success_at": last_ok["finished_at"] if last_ok else None,
        "total_stories": sum(m["count"] for m in months),
        "new_today": sum(1 for m in by_month.values() for s in m if s["is_new"]),
        "months": months,
        "facets": {
            **{k: sorted(v, key=str) for k, v in facets.items()},
            "countries": [{"code": c.split("|")[0], "name": c.split("|")[1]} for c in countries],
        },
    }
    _write(out_dir / "manifest.json", manifest)
    return manifest
