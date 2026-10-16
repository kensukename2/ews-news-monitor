"""News list pages of sites without an RSS feed (used only when no feed exists).

Configured in config/sources.yaml:
    type: html_links
    url: https://www.gob.pe/institucion/senamhi/noticias
    link_pattern: '/institucion/senamhi/noticias/\\d+'   # regex on the link's path
    strip_pattern: '^Press Release \\d+ \\w+ \\d{4}\\s*'   # optional, removed from titles
    max_links: 30                                        # optional

The page is read once per run (robots.txt is respected) and only the link
text and URL are kept. A date in the link text is used when present.
"""

import re
from html import unescape
from urllib.parse import urljoin, urlsplit

from ..textutil import clean_text, find_date

_A = re.compile(r'<a\s[^>]*href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', re.I | re.S)


def parse_links(html_text: str, source: dict) -> list:
    pattern = re.compile(source["link_pattern"])
    strip = re.compile(source["strip_pattern"]) if source.get("strip_pattern") else None
    seen, items = set(), []
    for href, inner in _A.findall(html_text):
        url = urljoin(source["url"], unescape(href))
        if not pattern.search(urlsplit(url).path) or url in seen:
            continue
        text = clean_text(unescape(inner), 400)
        if len(text) < 10:
            continue
        seen.add(url)
        title = strip.sub("", text).strip() if strip else text
        items.append({
            "url": url,
            "title": title or text,
            "source_name": source["name"],
            "language": source["language"],
            "published_at": find_date(text),
            "snippet": "",
        })
        if len(items) >= source.get("max_links", 30):
            break
    return items


def collect(source: dict, ctx) -> list:
    r = ctx.http.get(source["url"])
    r.raise_for_status()
    if r.encoding is None or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding
    return parse_links(r.text, source)
