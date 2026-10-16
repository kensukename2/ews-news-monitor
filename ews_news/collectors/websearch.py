"""News search through the Claude API web search tool.

Each configured query runs one web search ($0.01 each). The model then picks
the results that are recent news about early warnings and writes a short
snippet for each. Only URLs that really appeared in the search results are
kept, so the model cannot invent links.
"""

import json

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
                "required": ["url", "snippet"],
                "properties": {
                    "url": {"type": "string"},
                    "snippet": {"type": "string"},
                },
            },
        }
    },
}

PROMPT = """Run exactly one web search with this query: {query}

Look for news or official press releases from the last {days} days about how early \
warnings were provided for natural hazards: forecasts, warnings issued, how they were \
communicated, evacuations or other actions taken, and outcomes.
Then list the relevant search results. For each, give its exact URL and a factual \
1-2 sentence snippet in the article's original language, based only on the search result."""


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
            messages=[{"role": "user", "content": PROMPT.format(query=query, days=days)}],
        )
        ctx.budget.record("web_search", cfg["model"], usage_dict(message.usage),
                          web_search_count(message.usage))
        results, picks = parse_message(message)
        for p in picks:
            r = results.get(p.get("url"))
            if not r:
                continue  # not in the real search results: ignore
            items.append({
                "url": p["url"],
                "title": clean_text(r["title"], 400),
                "source_name": None,
                "language": source["language"],
                "published_at": parse_date(r.get("page_age")),
                "snippet": clean_text(p.get("snippet")),
            })
    return items
