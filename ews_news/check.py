"""Check that every feed / list-page source in config/sources.yaml is reachable.

    python -m ews_news check-sources

For each source: robots.txt permission, number of entries and the date of the
newest entry (RSS feeds that stopped updating are flagged as stale).
API-based sources (GDELT, web search) are checked by a normal run instead.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from .collectors import COLLECTORS
from .http import PoliteSession, RobotsDisallowed


def check_sources(config, include_disabled=False) -> list:
    s = config.settings
    http = PoliteSession(s["user_agent"], 15, 0.5)
    stale = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    report = []
    for src in config.sources:
        if not src.get("url") or src["type"] not in ("rss", "html_links", "jma_press"):
            continue
        if not src["enabled"] and not include_disabled:
            continue
        row = {"id": src["id"], "language": src["language"], "enabled": src["enabled"],
               "url": src["url"], "status": "ok", "entries": 0, "newest": None, "detail": ""}
        try:
            items = COLLECTORS[src["type"]](src, SimpleNamespace(http=http, config=config))
            dates = sorted(i["published_at"] for i in items if i["published_at"])
            row["entries"] = len(items)
            row["newest"] = dates[-1] if dates else None
            if not items:
                row.update(status="error", detail="no entries (feed moved or page changed?)")
            elif src["type"] == "rss" and row["newest"] and row["newest"][:10] < stale:
                # (list pages only show dates mentioned in titles, so not checked)
                row.update(status="stale", detail="no new entries for 30+ days")
        except RobotsDisallowed:
            row.update(status="blocked", detail="robots.txt disallows")
        except Exception as e:
            row.update(status="error", detail=f"{type(e).__name__}: {e}"[:120])
        report.append(row)
    return report
