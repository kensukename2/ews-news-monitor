"""News search through the Claude API web search tool.

Each configured query runs one web search ($0.01 each). The model then picks
the results that are recent news about early warnings and writes a short
snippet for each. Only URLs that really appeared in the search results are
kept, so the model cannot invent links.
"""

import json
import re
from datetime import datetime, timedelta, timezone

from ..budget import usage_dict, web_search_count
from ..textutil import clean_text, parse_date

PICK_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["articles"],
    "properties": {
        "articles": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["url", "published_date", "snippet"],
                "properties": {
                    "url": {"type": "string"},
                    "published_date": {"type": ["string", "null"]},
                    "snippet": {"type": "string"},
                },
            },
        }
    },
}

PROMPT = """Today is {today}. Run exactly one web search with this query: {query}

Look for news or official press releases published on or after {cutoff} about how early \
warnings were provided for natural hazards: forecasts, warnings issued, how they were \
communicated, evacuations or other actions taken, and outcomes.
Then list the relevant search results. For each, give its exact URL, its publication \
date (YYYY-MM-DD) as shown in the search result, and a factual 1-2 sentence snippet in the \
article's original language, based only on the search result.
Leave out results published before {cutoff} and results whose date you cannot see \
(use null only if you include it by mistake)."""


MONTHS_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
             "septiembre", "octubre", "noviembre", "diciembre"]
_AGO = re.compile(r"(\d+)\s+(minute|hour|day|week|month)s?\s+ago", re.I)


def query_vars(now: datetime) -> dict:
    """Placeholders available in web_search queries in config/languages/*.yaml."""
    return {
        "today": now.strftime("%Y-%m-%d"),
        "month_en": now.strftime("%B %Y"),
        "month_ja": f"{now.year}年{now.month}月",
        "month_es": f"{MONTHS_ES[now.month - 1]} {now.year}",
    }


def page_age_date(page_age, now: datetime):
    """Search results give page_age as a date or as e.g. "3 days ago"."""
    if not page_age:
        return None
    m = _AGO.search(str(page_age))
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        hours = {"minute": 1 / 60, "hour": 1, "day": 24, "week": 168, "month": 720}[unit] * n
        return (now - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return parse_date(page_age)


def parse_message(message) -> list:
    """Return (results_by_url, picks) from a Messages API response."""
    results = {}
    picks = []
    for block in message.content:
        if block.type == "web_search_tool_result" and isinstance(block.content, list):
            for r in block.content:
                if getattr(r, "type", "") == "web_search_result":
                    results[r.url] = {"title": r.title, "page_age": getattr(r, "page_age", None)}
        elif block.type == "text":
            try:
                picks = json.loads(block.text).get("articles", [])
            except (ValueError, AttributeError):
                pass
    return results, picks


def collect(source: dict, ctx) -> list:
    if ctx.anthropic is None:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    cfg = ctx.config.settings["web_search"]
    lang = ctx.config.languages[source["language"]]
    days = ctx.config.settings["collect"]["lookback_days"]
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    cutoff = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    items = []
    for query in lang.get("web_search", {}).get("queries", []):
        ctx.budget.check(cfg["max_searches_per_query"] * ctx.config.settings["budget"]["web_search_usd"]
                         + ctx.budget.cost(cfg["model"], {"input_tokens": 30000, "output_tokens": 2000}))
        message = ctx.anthropic.messages.create(
            model=cfg["model"],
            max_tokens=4000,
            thinking={"type": "disabled"},
            output_config={"effort": "low",
                           "format": {"type": "json_schema", "schema": PICK_SCHEMA}},
            tools=[{"type": "web_search_20250305", "name": "web_search",
                    "max_uses": cfg["max_searches_per_query"]}],
            messages=[{"role": "user", "content": PROMPT.format(query=query.format(**query_vars(now)), today=today, cutoff=cutoff)}],
        )
        ctx.budget.record("web_search", cfg["model"], usage_dict(message.usage),
                          web_search_count(message.usage))
        results, picks = parse_message(message)
        for p in picks:
            r = results.get(p.get("url"))
            if not r:
                continue  # not in the real search results: ignore
            published = page_age_date(r.get("page_age"), now) or parse_date(p.get("published_date"))
            if not published or published[:10] < cutoff:
                continue  # undated or too old: would show up as "new" otherwise
            items.append({
                "url": p["url"],
                "title": clean_text(r["title"], 400),
                "source_name": None,
                "language": source["language"],
                "published_at": published,
                "snippet": clean_text(p.get("snippet")),
            })
    return items
