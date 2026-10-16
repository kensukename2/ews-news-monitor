"""RSS / Atom feeds."""

import feedparser

from ..textutil import clean_text, parse_date


def parse_feed(content: bytes, source: dict) -> list:
    """Parse feed bytes into raw items (separate from fetching, for tests)."""
    feed = feedparser.parse(content)
    feed_title = clean_text(feed.feed.get("title", "")) or source["name"]
    items = []
    for e in feed.entries:
        link = e.get("link")
        title = clean_text(e.get("title"), 400)
        if not link or not title:
            continue
        published = (e.get("published") or e.get("updated") or e.get("dc_date"))
        # Google-News-style feeds name the outlet in <source>; others use the feed title.
        src = e.get("source", {}).get("title") if isinstance(e.get("source"), dict) else None
        items.append({
            "url": link,
            "title": title,
            "source_name": src or source.get("name") or feed_title,
            "language": source["language"],
            "published_at": parse_date(published),
            "snippet": clean_text(e.get("summary") or e.get("description")),
        })
    return items


def collect(source: dict, ctx) -> list:
    r = ctx.http.get(source["url"])
    r.raise_for_status()
    if not feedparser.parse(r.content).version:
        # e.g. a bot-protection page served with HTTP 200 instead of the feed
        raise ValueError(f"not a feed (content-type {r.headers.get('content-type')}, "
                         f"starts {r.text[:60]!r}); blocked or moved?")
    return parse_feed(r.content, source)
