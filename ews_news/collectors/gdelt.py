"""GDELT DOC 2.0 API (https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/).

GDELT indexes English machine translations of non-English news, so the query
keywords are English and the language is selected with `sourcelang:`.
GDELT returns titles but no snippet.
"""

import re
import time

from ..textutil import clean_text, parse_date

API = "https://api.gdeltproject.org/api/v2/doc/doc"


def tidy_title(title: str) -> str:
    """GDELT titles have spaces around punctuation ("4 , 100 - metre")."""
    t = re.sub(r"(\d) ([,.]) (\d)", r"\1\2\3", title)
    t = re.sub(r" - ", "-", t) if re.search(r"\w - \w+ - \w", t) else t
    return re.sub(r"\s+([,.:;!?%])", r"\1", t)


def parse_response(data: dict, source: dict) -> list:
    items = []
    for a in data.get("articles", []) or []:
        if not a.get("url") or not a.get("title"):
            continue
        items.append({
            "url": a["url"],
            "title": tidy_title(clean_text(a["title"], 400)),
            "source_name": a.get("domain") or source["name"],
            "language": source["language"],
            "published_at": parse_date(a.get("seendate")),
            "snippet": "",
        })
    return items


def collect(source: dict, ctx) -> list:
    lang = ctx.config.languages[source["language"]]
    gcfg = lang.get("gdelt", {})
    days = ctx.config.settings["collect"]["lookback_days"]
    interval = ctx.config.settings["collect"]["gdelt_min_interval"]
    items = []
    for query in gcfg.get("queries", []):
        params = {
            "query": f"{query} sourcelang:{gcfg['sourcelang']}",
            "mode": "ArtList",
            "format": "json",
            "maxrecords": "75",
            "timespan": f"{days}d",
            "sort": "DateDesc",
        }
        for attempt in range(4):
            r = ctx.http.get(API, params=params, check_robots=False,
                             min_interval=interval, timeout=60)
            if r.status_code != 429:
                break
            time.sleep(20 * (attempt + 1))  # GDELT throttles hard after a 429
        r.raise_for_status()
        if not r.text.lstrip().startswith("{"):
            # GDELT reports query errors as plain text with HTTP 200.
            raise ValueError(f"GDELT: {r.text[:200]}")
        items.extend(parse_response(r.json(), source))
    return items
