"""Add a short description to items that have none (e.g. from GDELT).

Reads only the start of the page and keeps only the publisher's own
<meta name="description"> / og:description text. Full article text is
never stored. robots.txt is respected.
"""

import re

from .http import RobotsDisallowed
from .textutil import clean_text

_META = re.compile(
    r'<meta\s+[^>]*(?:property|name)\s*=\s*["\'](?:og:description|description|twitter:description)["\']'
    r'[^>]*>', re.I)
_CONTENT = re.compile(r'content\s*=\s*(["\'])(.*?)\1', re.I | re.S)


def extract_description(html_text: str) -> str:
    for tag in _META.findall(html_text):
        m = _CONTENT.search(tag)
        if m and m.group(2).strip():
            return clean_text(m.group(2))
    return ""


def fetch_description(http, url: str, max_bytes: int = 300_000) -> str:
    try:
        r = http.get(url, stream=True)
        if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
            return ""
        chunk = r.raw.read(max_bytes, decode_content=True)
        r.close()
        return extract_description(chunk.decode(r.encoding or "utf-8", errors="replace"))
    except RobotsDisallowed:
        return ""
    except Exception:
        return ""
