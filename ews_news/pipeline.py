"""The daily pipeline: collect -> pre-filter -> classify -> cluster -> publish."""

import json
import logging
import os
import traceback
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import anthropic

from . import cluster, db, enrich, prefilter, publish
from .budget import Budget, BudgetExceeded
from .classify import Classifier
from .collectors import COLLECTORS
from .http import PoliteSession
from .matcher import StoryMatcher
from .textutil import canonical_url, domain, title_key, utcnow

log = logging.getLogger("ews_news")


def make_http(config) -> PoliteSession:
    s = config.settings
    return PoliteSession(s["user_agent"], s["collect"]["request_timeout"],
                         s["collect"]["min_seconds_per_host"])


def make_context(config, conn, run_id, http=None):
    s = config.settings
    http = http or make_http(config)
    client = anthropic.Anthropic() if os.environ.get("ANTHROPIC_API_KEY") else None
    return SimpleNamespace(config=config, conn=conn, run_id=run_id, http=http,
                           anthropic=client, budget=Budget(conn, s, run_id))


# --------------------------------------------------------------------- collect

def _source_rules_ok(source: dict, text: str) -> bool:
    """Optional per-source word lists in sources.yaml: require_any / exclude_any.
    Whole-word matching, so "red" does not match "occurred"."""
    rules = {"word_boundaries": True, "fold_accents": True}
    req = prefilter.hits(text, source.get("require_any"), rules)
    exc = prefilter.hits(text, source.get("exclude_any"), rules)
    return (not source.get("require_any") or bool(req)) and not exc


def store_item(conn, config, source: dict, raw: dict, cutoff: str, baseline: bool = False) -> str:
    """Insert one collected item. Returns its status, or None if not new/too old.

    baseline=True is used on the first run of a list-page source: undated links
    already on the page are recorded as 'baseline' (seen, never classified), so
    that old articles do not appear as today's news.
    """
    if raw.get("published_at") and raw["published_at"] < cutoff:
        return None
    url_c = canonical_url(raw["url"])
    if conn.execute("SELECT 1 FROM items WHERE url_canonical = ?", (url_c,)).fetchone():
        return None
    lang = config.languages.get(raw["language"])
    if lang is None:
        return None
    keep, score, _ = prefilter.score(raw["title"], raw.get("snippet", ""), lang,
                                     source.get("prefilter", "strict"))
    keep = keep and _source_rules_ok(source, f"{raw['title']} {raw.get('snippet', '')}")
    tkey = title_key(raw["title"])
    status, dup_of = ("pending" if keep else "filtered_out"), None
    if baseline and not raw.get("published_at"):
        status = "baseline"
    if keep and len(tkey) >= 8:
        # The same headline from another outlet: keep it as an extra source
        # for the story, but do not pay to classify it again.
        row = conn.execute(
            "SELECT id FROM items WHERE title_key = ? AND status IN ('pending','classified') "
            "AND collected_at >= ? ORDER BY id LIMIT 1",
            (tkey, (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%d"))).fetchone()
        if row:
            status, dup_of = "duplicate", row["id"]
    conn.execute(
        "INSERT INTO items (url, url_canonical, title, title_key, source_id, source_name, language,"
        " published_at, collected_at, snippet, prefilter_score, status, duplicate_of)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (raw["url"], url_c, raw["title"], tkey, source["id"],
         raw.get("source_name") or domain(raw["url"]), raw["language"], raw.get("published_at"),
         utcnow(), raw.get("snippet", ""), score, status, dup_of))
    return status


def collect_all(ctx, only=None) -> list:
    config, conn = ctx.config, ctx.conn
    cutoff = (datetime.now(timezone.utc)
              - timedelta(days=config.settings["collect"]["lookback_days"])).strftime("%Y-%m-%dT%H:%M:%SZ")
    stats = []
    for source in config.sources:
        if not source["enabled"] or (only and source["id"] not in only):
            continue
        st = {"source_id": source["id"], "language": source["language"],
              "fetched": 0, "new_items": 0, "kept": 0, "error": None}
        try:
            baseline = source["type"] == "html_links" and not conn.execute(
                "SELECT 1 FROM items WHERE source_id = ? LIMIT 1", (source["id"],)).fetchone()
            raws = COLLECTORS[source["type"]](source, ctx)
            st["fetched"] = len(raws)
            for raw in raws:
                status = store_item(conn, config, source, raw, cutoff, baseline)
                if status:
                    st["new_items"] += 1
                    st["kept"] += status in ("pending", "duplicate")
            conn.commit()
        except Exception as e:  # one broken source must not stop the run
            st["error"] = f"{type(e).__name__}: {e}"[:300]
            log.warning("source %s failed: %s", source["id"], st["error"])
        log.info("%-16s %s fetched=%d new=%d kept=%d", source["id"], source["language"],
                 st["fetched"], st["new_items"], st["kept"])
        conn.execute(
            "INSERT OR REPLACE INTO source_stats (run_id, source_id, language, fetched, new_items, kept, error)"
            " VALUES (?,?,?,?,?,?,?)", (ctx.run_id, st["source_id"], st["language"], st["fetched"],
                                        st["new_items"], st["kept"], st["error"]))
        stats.append(st)
    conn.commit()
    return stats


# -------------------------------------------------------------------- classify

def classify_pending(ctx) -> dict:
    config, conn = ctx.config, ctx.conn
    out = {"classified": 0, "relevant": 0, "review": 0, "errors": 0, "copied": 0,
           "left_pending": 0, "budget_stopped": False, "cost_usd": 0.0}
    limit = config.settings["classify"]["max_items_per_run"]
    pending = conn.execute(
        "SELECT * FROM items WHERE status = 'pending' "
        "ORDER BY prefilter_score DESC, published_at DESC LIMIT ?", (limit,)).fetchall()
    if pending and ctx.anthropic is None:
        log.warning("ANTHROPIC_API_KEY not set: %d items left pending", len(pending))
    elif pending:
        classifier = Classifier(config, ctx.budget, ctx.anthropic)
        for row in pending:
            item = dict(row)
            if not item["snippet"]:
                item["snippet"] = enrich.fetch_description(ctx.http, item["url"])
                conn.execute("UPDATE items SET snippet = ? WHERE id = ?", (item["snippet"], item["id"]))
            try:
                data = classifier.classify(item)
            except BudgetExceeded as e:
                log.warning("budget: %s", e)
                out["budget_stopped"] = True
                break
            except (anthropic.APIConnectionError, anthropic.RateLimitError,
                    anthropic.InternalServerError) as e:
                log.warning("transient API error, item %s stays pending: %s", item["id"], e)
                continue
            except Exception as e:
                log.warning("classification failed for item %s: %s", item["id"], e)
                conn.execute("UPDATE items SET status = 'error' WHERE id = ?", (item["id"],))
                out["errors"] += 1
                continue
            save_classification(conn, item["id"], config.settings["classify"]["model"], data)
            out["classified"] += 1
            out["relevant"] += bool(data["relevant"])
            out["review"] += bool(data["review"])
            conn.commit()
    out["copied"] = copy_to_duplicates(conn)
    out["left_pending"] = conn.execute(
        "SELECT COUNT(*) FROM items WHERE status = 'pending'").fetchone()[0]
    out["cost_usd"] = round(ctx.budget.spent_run, 4)
    conn.commit()
    return out


def save_classification(conn, item_id, model, data):
    conn.execute(
        "INSERT OR REPLACE INTO classifications (item_id, model, created_at, relevant, confidence,"
        " review, data_json) VALUES (?,?,?,?,?,?,?)",
        (item_id, model, utcnow(), int(bool(data["relevant"])), data["confidence"],
         int(bool(data["review"])), db.dumps(data)))
    conn.execute("UPDATE items SET status = 'classified' WHERE id = ?", (item_id,))


def copy_to_duplicates(conn) -> int:
    """Give duplicate headlines the classification of their original (free)."""
    rows = conn.execute(
        "SELECT d.id, d.title, d.language, c.data_json FROM items d "
        "JOIN classifications c ON c.item_id = d.duplicate_of "
        "WHERE d.status = 'duplicate'").fetchall()
    for r in rows:
        data = db.loads(r["data_json"])
        data["original_title"] = r["title"]
        save_classification(conn, r["id"], "copied", data)
    return len(rows)


# ------------------------------------------------------------------------- run

def run_all(topics, collect=True, classify=True, only_sources=None) -> dict:
    """Run several topics one after another, sharing one polite HTTP session."""
    from .config import load_config
    results, http = {}, None
    for topic in topics:
        config = load_config(topic)
        http = http or make_http(config)
        db.restore(config.path("database"), config.path("db_dump"))
        log.info("===== topic %s =====", topic)
        results[topic] = run(config, collect, classify, only_sources, http=http)
    return results


def run(config, collect=True, classify=True, only_sources=None, http=None) -> dict:
    conn = db.connect(config.path("database"))
    started = utcnow()
    run_id = conn.execute("INSERT INTO runs (started_at, status) VALUES (?, 'running')",
                          (started,)).lastrowid
    conn.commit()
    stats = {"sources": [], "classify": {}, "stories_touched": 0, "fatal_error": None}
    status = "success"
    try:
        ctx = make_context(config, conn, run_id, http)
        if collect:
            stats["sources"] = collect_all(ctx, only_sources)
        if classify:
            stats["classify"] = classify_pending(ctx)
        matcher = StoryMatcher(config, ctx.budget, ctx.anthropic) if ctx.anthropic else None
        stats["stories_touched"] = cluster.assign_stories(conn, config, matcher)
        failed = [s for s in stats["sources"] if s["error"]]
        if failed:
            status = "partial" if len(failed) < len(stats["sources"]) else "failed"
        stats["budget"] = {"spent_month_usd": round(ctx.budget.spent_month, 4),
                           "allowance_run_usd": round(ctx.budget.allowance, 4),
                           "monthly_usd": config.settings["budget"]["monthly_usd"]}
    except Exception as e:
        status = "failed"
        stats["fatal_error"] = f"{type(e).__name__}: {e}"
        log.error("run failed:\n%s", traceback.format_exc())

    stats["by_language"] = summarize_by_language(stats["sources"])
    conn.execute("UPDATE runs SET finished_at = ?, status = ?, stats_json = ? WHERE id = ?",
                 (utcnow(), status, db.dumps(stats), run_id))
    conn.commit()

    publish.publish(conn, config)
    db.dump(conn, config.path("db_dump"))
    write_run_log(config, run_id, started, status, stats)
    conn.close()
    return {"topic": config.topic, "run_id": run_id, "status": status, **stats}


def summarize_by_language(sources) -> dict:
    out = {}
    for s in sources:
        d = out.setdefault(s["language"], {"fetched": 0, "new_items": 0, "kept": 0, "errors": 0})
        d["fetched"] += s["fetched"]
        d["new_items"] += s["new_items"]
        d["kept"] += s["kept"]
        d["errors"] += bool(s["error"])
    return out


def write_run_log(config, run_id, started, status, stats):
    d = config.path("run_logs")
    d.mkdir(parents=True, exist_ok=True)
    name = started.replace(":", "").replace("-", "")
    with open(d / f"run-{name}-{run_id}.json", "w", encoding="utf-8") as f:
        json.dump({"run_id": run_id, "started_at": started, "status": status, **stats},
                  f, ensure_ascii=False, indent=2)
