"""Group classified articles about the same event into stories.

Clustering works across languages because Claude returns an English event
name and an English location for every article:
  * named events ("Typhoon Ragasa", "Huracán Melissa" -> "Hurricane Melissa")
    are keyed on the name, regardless of hazard type;
  * unnamed events are keyed on hazard + country + location.
Two articles join the same story if their keys match and their dates are
within `cluster.window_days`.
"""

import re
from collections import Counter
from datetime import datetime, timedelta

from . import db
from .textutil import domain, normalize, utcnow

_NAME_NOISE = re.compile(
    r"\b(super|severe|major|tropical|typhoon|hurricane|cyclone|cyclonic|storm|depression|"
    r"category \d|post|remnants?|of|the|ex|very|extremely|20\d\d)\b")
_HAZARD_GROUP = {"flash_flood": "flood", "heavy_rain": "flood"}


def event_key(c: dict) -> str:
    name = c.get("event_name")
    if name:
        n = re.sub(r"[^\w ]", " ", normalize(name, fold_accents=True))
        n = _NAME_NOISE.sub(" ", n)
        n = " ".join(n.split())
        if n:
            return f"named|{n}"
    hazard = _HAZARD_GROUP.get(c["hazard"], c["hazard"])
    country = (c.get("countries") or ["XX"])[0]
    loc = normalize((c.get("event_location") or "").split(",")[0], fold_accents=True)
    return f"{hazard}|{country}|{loc}"


def _date(item) -> str:
    return item["published_at"] or item["collected_at"]


def _within(a: str, b: str, days: int) -> bool:
    fa = datetime.strptime(a[:10], "%Y-%m-%d")
    fb = datetime.strptime(b[:10], "%Y-%m-%d")
    return abs(fa - fb) <= timedelta(days=days)


def assign_stories(conn, config) -> int:
    """Attach every classified, unclustered, relevant (or review) item to a story."""
    window = config.settings["cluster"]["window_days"]
    rows = conn.execute(
        "SELECT i.*, c.data_json, c.relevant, c.review FROM items i "
        "JOIN classifications c ON c.item_id = i.id "
        "WHERE i.story_id IS NULL AND i.status = 'classified' "
        "AND (c.relevant = 1 OR c.review = 1) ORDER BY i.published_at").fetchall()
    touched = set()
    for row in rows:
        c = db.loads(row["data_json"])
        key = event_key(c)
        when = _date(row)
        story_id = None
        for s in conn.execute("SELECT id, event_date, last_updated FROM stories WHERE event_key = ?",
                              (key,)):
            if _within(s["event_date"] or s["last_updated"], when, window):
                story_id = s["id"]
                break
        if story_id is None:
            now = utcnow()
            cur = conn.execute(
                "INSERT INTO stories (event_key, hazard, first_seen, last_updated, event_date, data_json)"
                " VALUES (?,?,?,?,?,?)", (key, c["hazard"], now, now, when, "{}"))
            story_id = cur.lastrowid
        conn.execute("UPDATE items SET story_id = ? WHERE id = ?", (story_id, row["id"]))
        touched.add(story_id)
    for sid in touched:
        rebuild_story(conn, config, sid)
    conn.commit()
    return len(touched)


def _merge_outcome(outcomes) -> str:
    seen = {o for o in outcomes if o != "not_reported"}
    if not seen:
        return "not_reported"
    if "mixed" in seen or {"positive", "negative"} <= seen:
        return "mixed"
    return seen.pop()


def _max_impacts(impacts_list) -> dict:
    out = {}
    for imp in impacts_list:
        for k, v in (imp or {}).items():
            if v is None:
                continue
            if isinstance(v, (int, float)):
                out[k] = max(out.get(k, 0), v)
            else:
                out.setdefault(k, v)
    return out


def rebuild_story(conn, config, story_id: int) -> dict:
    """Recompute a story's aggregated fields from all of its articles."""
    rows = conn.execute(
        "SELECT i.*, c.data_json, c.confidence, c.review FROM items i "
        "JOIN classifications c ON c.item_id = i.id WHERE i.story_id = ? "
        "ORDER BY i.published_at", (story_id,)).fetchall()
    members = [(r, db.loads(r["data_json"])) for r in rows]
    # Representative article: most confident, English preferred, then earliest.
    best_row, best = max(members, key=lambda m: (m[1]["confidence"], m[1]["language"] == "en"))

    summaries_original = {}
    for r, c in sorted(members, key=lambda m: -m[1]["confidence"]):
        if c.get("summary_original") and c["language"] != "en":
            summaries_original.setdefault(c["language"], c["summary_original"])

    agencies = {}
    for _, c in members:
        for a in c.get("agencies", []):
            agencies.setdefault(normalize(a["name"]), a)

    countries = sorted({cc for _, c in members for cc in c.get("countries", [])})
    names = Counter(c["event_name"] for _, c in members if c.get("event_name"))
    data = {
        "id": story_id,
        "event_name": names.most_common(1)[0][0] if names else None,
        "title": best["title_en"],
        "hazard": Counter(c["hazard"] for _, c in members).most_common(1)[0][0],
        "hazards": sorted({c["hazard"] for _, c in members}),
        "event_location": best.get("event_location"),
        "countries": [{"code": cc, "name": config.countries.get(cc, {}).get("name", cc)}
                      for cc in countries],
        "wmo_regions": sorted({r for _, c in members for r in c.get("wmo_regions", [])},
                              key=["I", "II", "III", "IV", "V", "VI"].index),
        "agencies": sorted(agencies.values(), key=lambda a: (a["type"], a["name"])),
        "agency_types": sorted({a["type"] for a in agencies.values()}),
        "pillars": sorted({p for _, c in members for p in c.get("ew4all_pillars", [])}),
        "channels": sorted({ch for _, c in members for ch in c.get("dissemination_channels", [])}),
        "lead_time": next((c["lead_time"] for _, c in members if c.get("lead_time")), None),
        "actions": list(dict.fromkeys(a for _, c in members for a in c.get("actions_taken", [])))[:8],
        "outcome": _merge_outcome(c["outcome"] for _, c in members),
        "outcome_justification": best.get("outcome_justification"),
        "impacts": _max_impacts(c.get("impacts") for _, c in members),
        "summary_en": best["summary_en"],
        "summaries_original": summaries_original,
        "languages": sorted({c["language"] for _, c in members}),
        "confidence": round(max(c["confidence"] for _, c in members), 2),
        "review": all(c.get("review") for _, c in members),
        "sources": [{
            "url": r["url"],
            "title": r["title"],
            "title_en": c.get("title_en"),
            "source_name": r["source_name"] or domain(r["url"]),
            "language": r["language"],
            "published_at": r["published_at"],
        } for r, c in members],
        "event_date": min(_date(r) for r, _ in members)[:10],
        "first_seen": min(r["collected_at"] for r, _ in members),
        "last_updated": max(r["collected_at"] for r, _ in members),
    }
    conn.execute(
        "UPDATE stories SET hazard = ?, last_updated = ?, event_date = ?, data_json = ? WHERE id = ?",
        (data["hazard"], data["last_updated"], data["event_date"], db.dumps(data), story_id))
    return data
