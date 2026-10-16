"""Collectors turn a source definition into a list of raw items.

Every collector returns dicts with these keys:
    url, title, source_name, language, published_at (ISO UTC or None), snippet
"""

from . import gdelt, rss, websearch

COLLECTORS = {
    "rss": rss.collect,
    "gdelt": gdelt.collect,
    "claude_search": websearch.collect,
}
