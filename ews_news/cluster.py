"""Group classified articles about the same event into stories.

Works across languages because Claude returns English names and titles for
every article. The topic module (ews_news/topics/<topic>.py) decides what
"the same story" means:
  * topic.event_key(): articles with the same key (e.g. "named|melissa" for
    EWS, "aifs|operational_implementation" for AI) within
    `cluster.window_days` join the same story;
  * otherwise topic.candidate_ok() selects candidate stories within
    `cluster.similar_days`, which are compared by Claude (matcher.py, a short
    question costing ~$0.0001) when available, else by English title word
    overlap (`cluster.min_title_similarity`).
"""

import logging
import re
from collections import Counter
from datetime import datetime, timedelta

from . import db
from .textutil import domain, normalize, utcnow

log = logging.getLogger("ews_news")

_STOPWORDS = set("""a an and are as at be by for from has have in into is it its of on or over
the to was were will with after amid as new says said about after than that this their""".split())


def title_tokens(text: str) -> set:
    words = re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", normalize(text or "", fold_accents=True))
    return {w for w in words if w not in _STOPWORDS and (len(w) > 2 or w[0].isdigit())}


def similarity(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _date(item) -> str:
    return item["published_at"] or item["collected_at"]


def _within(a: str, b: str, days: int) -> bool:
    fa = datetime.strptime(a[:10], "%Y-%m-%d")
    fb = datetime.strptime(b[:10], "%Y-%m-%d")
    return abs(fa - fb) <= timedelta(days=days)


def _candidates(conn, topic, c: dict, when: str, ccfg: dict) -> list:
    """Existing stories this article could belong to, newest first (max 8)."""
    out = []
    for r in conn.execute("SELECT id, data_json FROM stories WHERE data_json != '{}'"):
        s = db.loads(r["data_json"])
        if not topic.candidate_ok(c, s):
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

    matcher: optional StoryMatcher (Claude) for articles without a key match.
    """
    topic = config.module
    ccfg = config.settings["cluster"]
    names = {k: v["name"] for k, v in config.countries.items()}
    rows = conn.execute(
        "SELECT i.*, c.data_json, c.relevant, c.review FROM items i "
        "JOIN classifications c ON c.item_id = i.id "
        "WHERE i.story_id IS NULL AND i.status = 'classified' "
        "AND (c.relevant = 1 OR c.review = 1) ORDER BY i.published_at").fetchall()
    touched = set()
    for row in rows:
        c = db.loads(row["data_json"])
        key = topic.event_key(c, names)
        when = _date(row)
        story_id = None
        same_key = conn.execute("SELECT id, event_date, last_updated FROM stories WHERE event_key = ?",
                                (key,)).fetchall() if key else []
        for s in same_key:
            if _within(s["event_date"] or s["last_updated"], when, ccfg["window_days"]):
                story_id = s["id"]
                break
        if story_id is None:
            candidates = _candidates(conn, topic, c, when, ccfg)
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
                " VALUES (?,?,?,?,?,?)", (key or "", topic.category(c), now, now, when, "{}"))
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


def rebuild_story(conn, config, story_id: int) -> dict:
    """Recompute a story's fields from all of its articles."""
    topic = config.module
    rows = conn.execute(
        "SELECT i.*, c.data_json, c.confidence, c.review FROM items i "
        "JOIN classifications c ON c.item_id = i.id WHERE i.story_id = ? "
        "ORDER BY i.published_at", (story_id,)).fetchall()
    members = [(r, db.loads(r["data_json"])) for r in rows]
    # Most confident first, English preferred; the first one represents the story.
    ranked = sorted(members, key=lambda m: (m[1]["confidence"], m[1]["language"] == "en"),
                    reverse=True)
    best = ranked[0][1]

    summaries_original = {}
    for _, c in ranked:
        if c.get("summary_original") and c["language"] != "en":
            summaries_original.setdefault(c["language"], c["summary_original"])

    countries = sorted({cc for _, c in members for cc in c.get("countries", [])})
    data = {
        "id": story_id,
        "title": best["title_en"],
        "countries": [{"code": cc, "name": config.countries.get(cc, {}).get("name", cc)}
                      for cc in countries],
        "wmo_regions": sorted({r for _, c in members for r in c.get("wmo_regions", [])},
                              key=["I", "II", "III", "IV", "V", "VI"].index),
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
    data.update(topic.aggregate([c for _, c in ranked]))
    category = Counter(topic.category(c) for _, c in members).most_common(1)[0][0]
    conn.execute(
        "UPDATE stories SET hazard = ?, last_updated = ?, event_date = ?, data_json = ? WHERE id = ?",
        (category, data["last_updated"], data["event_date"], db.dumps(data), story_id))
    return data
