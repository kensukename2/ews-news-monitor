"""Command line interface.

    python -m ews_news run                 # full daily pipeline
    python -m ews_news run --source nhk_news --source gdelt_en
    python -m ews_news run --no-classify   # collect + publish only (no API cost)
    python -m ews_news publish             # rebuild the JSON for the page
    python -m ews_news recluster           # rebuild all stories (after changing cluster settings)
    python -m ews_news report              # last run summary and spending
    python -m ews_news check-sources       # test every RSS feed in config/sources.yaml
    python -m ews_news daily-gate          # exit 0 only at 05:xx Europe/Zurich (for CI)
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from . import cluster, db, publish
from .config import load_config
from .pipeline import run


def main(argv=None):
    load_dotenv()
    p = argparse.ArgumentParser(prog="ews_news")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--source", action="append", help="only run these source ids")
    r.add_argument("--no-collect", action="store_true")
    r.add_argument("--no-classify", action="store_true")
    sub.add_parser("publish")
    sub.add_parser("recluster")
    sub.add_parser("report")
    cs = sub.add_parser("check-sources")
    cs.add_argument("--all", action="store_true", help="include disabled sources")
    g = sub.add_parser("daily-gate")
    g.add_argument("--hour", type=int, default=5)
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = load_config()

    if args.cmd == "daily-gate":
        now = datetime.now(ZoneInfo(config.settings["timezone"]))
        print(f"local time {now:%Y-%m-%d %H:%M %Z}")
        sys.exit(0 if now.hour == args.hour else 1)

    if args.cmd == "check-sources":
        from .check import check_sources
        for r in check_sources(config, args.all):
            print(f"{r['status']:8} {r['language']} {r['id']:22} entries={r['entries']:<4} "
                  f"newest={(r['newest'] or '-')[:10]}  {r['detail']}")
        return

    db.restore(config.path("database"), config.path("db_dump"))

    if args.cmd == "run":
        result = run(config, collect=not args.no_collect, classify=not args.no_classify,
                     only_sources=args.source)
        print(json.dumps({k: result[k] for k in ("run_id", "status", "by_language", "classify",
                                                  "stories_touched")},
                         ensure_ascii=False, indent=2))
        sys.exit(0 if result["status"] != "failed" else 1)
    conn = db.connect(config.path("database"))
    if args.cmd == "publish":
        m = publish.publish(conn, config)
        print(f"published {m['total_stories']} stories ({m['new_today']} new today)")
    elif args.cmd == "recluster":
        n = cluster.recluster(conn, config)
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


if __name__ == "__main__":
    main()
