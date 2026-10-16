"""Group classified articles about the same event into stories.

Clustering works across languages because Claude returns an English event
name and an English location for every article:
  * named events ("Typhoon Ragasa", "Huracán Melissa" -> "Hurricane Melissa")
    are keyed on the name, regardless of hazard type;
  * unnamed events are keyed on hazard + country + location.
Two articles join the same story if their keys match and their dates are
within `cluster.window_days`. Otherwise, candidate stories with the same
hazard group and a shared country within `cluster.similar_days` are compared:
by Claude (matcher.py, a short question costing ~$0.0003) when available,
else by English title word overlap (`cluster.min_title_similarity`).
"""

import logging
import re
from collections import Counter
from datetime import datetime, timedelta

from . import db
from .textutil import domain, normalize, utcnow

log = logging.getLogger("ews_news")

_NAME_NOISE = re.compile(
    r"\b(super|severe|major|tropical|typhoon|hurricane|cyclone|cyclonic|storm|depression|"
    r"category \d|post|remnants?|of|the|ex|very|extremely|20\d\d)\b")
_HAZARD_GROUP = {"flash_flood": "flood", "heavy_rain": "flood", "storm_surge": "tropical_cyclone",
                 "tsunami": "earthquake"}
_STOPWORDS = set("""a an and are as at be by for from has have in into is it its of on or over
the to was were will with after amid as new says said about after than that this their""".split())


def title_tokens(text: str) -> set:
    words = re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", normalize(text or "", fold_accents=True))
    return {w for w in words if w not in _STOPWORDS and (len(w) > 2 or w[0].isdigit())}


def similarity(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def event_key(c: dict, country_names: dict = None) -> str:
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
    if not loc or loc == normalize((country_names or {}).get(country, "")):
        return None  # too vague (no place, or just the country): use the candidate check
    return f"{hazard}|{country}|{loc}"


def _date(item) -> str:
    return item["published_at"] or item["collected_at"]


def _within(a: str, b: str, days: int) -> bool:
    fa = datetime.strptime(a[:10], "%Y-%m-%d")
    fb = datetime.strptime(b[:10], "%Y-%m-%d")
    return abs(fa - fb) <= timedelta(days=days)


def _candidates(conn, c: dict, when: str, ccfg: dict) -> list:
    """Stories with the same hazard group and a shared country, close in time."""
    group = _HAZARD_GROUP.get(c["hazard"], c["hazard"])
    countries = set(c.get("countries") or [])
    out = []
    for r in conn.execute("SELECT id, data_json FROM stories WHERE data_json != '{}'"):
        s = db.loads(r["data_json"])
        if group not in {_HAZARD_GROUP.get(h, h) for h in s["hazards"]}:
            continue
        if countries and not countries & {x["code"] for x in s["countries"]}:
            continue
        if not (_within(s["event_date"], when, ccfg["similar_days"])
                or _within(s["last_updated"], when, ccfg["similar_days"])):
            continue
        s["story_id"] = r["id"]
        out.append(s)
    out.sort(key=lambda s: s["last_updated"], reverse=True)
    return out[:8]


def _most_similar(c: dict, candidates: list, threshold: float):
    """Fallback without Claude: compare English titles word by word."""
    tokens = title_tokens(c.get("title_en"))
    best, best_sim = None, threshold
    for s in candidates:
        for src in s["sources"]:
            sim = similarity(tokens, title_tokens(src.get("title_en") or src["title"]))
            if sim >= best_sim:
                best, best_sim = s["story_id"], sim
    return best


def assign_stories(conn, config, matcher=None) -> int:
    """Attach every classified, unclustered, relevant (or review) item to a story.

    matcher: optional StoryMatcher (Claude) for articles without an event-name match.
    """
    ccfg = config.settings["cluster"]
    window = ccfg["window_days"]
    names = {k: v["name"] for k, v in config.countries.items()}
    rows = conn.execute(
        "SELECT i.*, c.data_json, c.relevant, c.review FROM items i "
        "JOIN classifications c ON c.item_id = i.id "
        "WHERE i.story_id IS NULL AND i.status = 'classified' "
        "AND (c.relevant = 1 OR c.review = 1) ORDER BY i.published_at").fetchall()
    touched = set()
    for row in rows:
        c = db.loads(row["data_json"])
        key = event_key(c, names)
        when = _date(row)
        story_id = None
        same_key = conn.execute("SELECT id, event_date, last_updated FROM stories WHERE event_key = ?",
                                (key,)).fetchall() if key else []
        for s in same_key:
            if _within(s["event_date"] or s["last_updated"], when, window):
                story_id = s["id"]
                break
        if story_id is None:
            candidates = _candidates(conn, c, when, ccfg)
            if candidates and matcher is not None:
                try:
                    story_id = matcher.match(c, candidates)
                except Exception as e:  # budget reached or API problem: fall back
                    log.warning("story matcher unavailable (%s); using title similarity", e)
                    matcher = None
            if story_id is None and candidates and matcher is None:
                story_id = _most_similar(c, candidates, ccfg["min_title_similarity"])
        if story_id is None:
            now = utcnow()
            cur = conn.execute(
                "INSERT INTO stories (event_key, hazard, first_seen, last_updated, event_date, data_json)"
                " VALUES (?,?,?,?,?,?)", (key or "", c["hazard"], now, now, when, "{}"))
            story_id = cur.lastrowid
        conn.execute("UPDATE items SET story_id = ? WHERE id = ?", (story_id, row["id"]))
        rebuild_story(conn, config, story_id)  # keep candidates up to date within this run
        touched.add(story_id)
    conn.commit()
    return len(touched)


def recluster(conn, config, matcher=None) -> int:
    """Throw away all stories and rebuild them (after changing cluster settings)."""
    conn.execute("UPDATE items SET story_id = NULL")
    conn.execute("DELETE FROM stories")
    conn.commit()
    return assign_stories(conn, config, matcher)


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
