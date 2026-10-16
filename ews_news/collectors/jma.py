"""気象庁 (JMA) press releases (報道発表).

JMA has no RSS feed for press releases; its press list page is built from
https://www.jma.go.jp/jma/press_list.js, a JavaScript array of
[type, url, Reiwa year, month, day, title]. Type "1" = 報道発表.
"""

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

BASE = "https://www.jma.go.jp"
_ROW = re.compile(r"array\[num\+\+\]\s*=\s*\['(\d)','([^']+)','(\d+)','(\d+)','(\d+)','([^']*)'\]")
JST = timezone(timedelta(hours=9))


def parse_press_list(js_text: str, source: dict, max_items: int = 40) -> list:
    items = []
    for kind, path, year, month, day, title in _ROW.findall(js_text):
        if kind != "1" or not title.strip():
            continue
        try:
            date = datetime(2018 + int(year), int(month), int(day), tzinfo=JST)
        except ValueError:
            continue
        items.append({
            "url": urljoin(BASE, path),
            "title": title.strip(),
            "source_name": "気象庁 (JMA)",
            "language": source["language"],
            "published_at": date.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "snippet": "",
        })
        if len(items) >= max_items:
            break
    return items


def collect(source: dict, ctx) -> list:
    r = ctx.http.get(source.get("url", BASE + "/jma/press_list.js"))
    r.raise_for_status()
    r.encoding = "utf-8"
    return parse_press_list(r.text, source)
