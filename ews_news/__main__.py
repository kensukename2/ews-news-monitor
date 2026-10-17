"""Command line interface.

Without --topic, commands apply to every topic in config/topics/ (ews, ai).

    python -m ews_news run                 # full daily pipeline, all topics
    python -m ews_news run --topic ai      # only AI News
    python -m ews_news run --topic ews --source nhk_news --source gdelt_en
    python -m ews_news run --no-classify   # collect + publish only (no API cost)
    python -m ews_news publish             # rebuild the JSON for the pages
    python -m ews_news recluster           # rebuild all stories (after changing cluster settings)
    python -m ews_news recluster --use-claude   # ... asking Claude about unclear matches (small cost)
    python -m ews_news report              # last run summary and spending
    python -m ews_news check-sources       # test every feed / list page
    python -m ews_news daily-gate          # exit 0 if it is >= 05:00 Zurich and no run yet today (CI)
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from . import cluster, db, publish
from .config import load_config, topic_names
from .pipeline import run_all


def main(argv=None):
    load_dotenv()
    topics = topic_names()
    p = argparse.ArgumentParser(prog="ews_news")
    p.add_argument("--topic", choices=topics, action="append",
                   help="topic(s) to work on (default: all)")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--source", action="append", help="only run these source ids")
    r.add_argument("--no-collect", action="store_true")
    r.add_argument("--no-classify", action="store_true")
    sub.add_parser("publish")
    rc = sub.add_parser("recluster")
    rc.add_argument("--use-claude", action="store_true")
    sub.add_parser("report")
    cs = sub.add_parser("check-sources")
    cs.add_argument("--all", action="store_true", help="include disabled sources")
    g = sub.add_parser("daily-gate")
    g.add_argument("--hour", type=int, default=5)
    args = p.parse_args(argv)
    selected = args.topic or topics

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.cmd == "run":
        results = run_all(selected, collect=not args.no_collect, classify=not args.no_classify,
                          only_sources=args.source)
        for topic, result in results.items():
            print(f"== {topic}")
            print(json.dumps({k: result[k] for k in ("run_id", "status", "by_language", "classify",
                                                      "stories_touched")},
                             ensure_ascii=False, indent=2))
        sys.exit(1 if any(r["status"] == "failed" for r in results.values()) else 0)

    if args.cmd == "daily-gate":
        # GitHub's scheduler runs in UTC and may start late, so the workflow is
        # triggered at 03:00 and 04:00 UTC; run once per local day from 05:00.
        # The EWS topic's run history decides (all topics run together).
        config = load_config("ews")
        db.restore(config.path("database"), config.path("db_dump"))
        tz = ZoneInfo(config.settings["timezone"])
        now = datetime.now(tz)
        conn = db.connect(config.path("database"))
        last = conn.execute("SELECT started_at FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        last_local = (datetime.fromisoformat(last[0].replace("Z", "+00:00")).astimezone(tz)
                      if last else None)
        ok = now.hour >= args.hour and (last_local is None or last_local.date() < now.date())
        print(f"local time {now:%Y-%m-%d %H:%M %Z}; last run "
              f"{last_local:%Y-%m-%d %H:%M}" if last_local else f"local time {now:%Y-%m-%d %H:%M %Z}; no runs yet")
        print("run" if ok else "skip")
        sys.exit(0 if ok else 1)

    for topic in selected:
        config = load_config(topic)
        print(f"== {topic}")
        if args.cmd == "check-sources":
            from .check import check_sources
            for row in check_sources(config, args.all):
                print(f"{row['status']:8} {row['language']} {row['id']:22} entries={row['entries']:<4} "
                      f"newest={(row['newest'] or '-')[:10]}  {row['detail']}")
            continue

        db.restore(config.path("database"), config.path("db_dump"))
        conn = db.connect(config.path("database"))
        if args.cmd == "publish":
            m = publish.publish(conn, config)
            print(f"published {m['total_stories']} stories ({m['new_today']} new today)")
        elif args.cmd == "recluster":
            matcher = None
            if args.use_claude:
                import anthropic
                from .budget import Budget
                from .matcher import StoryMatcher
                matcher = StoryMatcher(config, Budget(conn, config.settings), anthropic.Anthropic())
            n = cluster.recluster(conn, config, matcher)
            m = publish.publish(conn, config)
            db.dump(conn, config.path("db_dump"))
            print(f"rebuilt {n} stories; published {m['total_stories']}")
        elif args.cmd == "report":
            last = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
            month = datetime.utcnow().strftime("%Y-%m-01")
            spent = conn.execute("SELECT COALESCE(SUM(cost_usd),0) FROM api_usage WHERE ts >= ?",
                                 (month,)).fetchone()[0]
            print(f"spent this month: ${spent:.4f} of ${config.settings['budget']['monthly_usd']}")
            if last:
                print(f"last run #{last['id']} {last['status']} {last['started_at']}")
                print(json.dumps(db.loads(last["stats_json"]), ensure_ascii=False, indent=2))
        conn.close()


if __name__ == "__main__":
    main()
