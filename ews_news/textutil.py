"""Text helpers: normalisation, URL canonicalisation, dates."""

import html
import re
import unicodedata
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_TRACKING_PARAMS = re.compile(r"^(utm_|fbclid$|gclid$|mc_|ref$|cmpid$|ocid$)")


def clean_text(text: Optional[str], max_len: int = 600) -> str:
    """Strip HTML tags/entities and collapse whitespace. Keeps UTF-8 intact."""
    if not text:
        return ""
    text = html.unescape(_TAG_RE.sub(" ", text))
    text = _WS_RE.sub(" ", text).strip()
    return text[:max_len]


def normalize(text: str, fold_accents: bool = False) -> str:
    """NFKC-normalise and lowercase (full-width -> half-width for Japanese)."""
    text = unicodedata.normalize("NFKC", text or "").lower()
    if fold_accents:
        text = "".join(
            c for c in unicodedata.normalize("NFD", text)
            if unicodedata.category(c) != "Mn"
        )
        text = unicodedata.normalize("NFC", text)
    return _WS_RE.sub(" ", text).strip()


def title_key(title: str) -> str:
    """Key used to spot the same headline published by several outlets.

    Drops a trailing " - Outlet" / " | Outlet" suffix and all punctuation and
    spaces, so it works for Japanese as well as for space-separated languages.
    """
    t = re.sub(r"\s+[-|–—]\s+[^-|–—]{2,60}$", "", title or "")
    t = normalize(t, fold_accents=True)
    return "".join(c for c in t if c.isalnum())


def canonical_url(url: str) -> str:
    """Lower-case host, drop fragments and tracking parameters."""
    parts = urlsplit((url or "").strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not _TRACKING_PARAMS.match(k.lower())]
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("https" if parts.scheme in ("http", "https") else parts.scheme,
                       host, path, urlencode(query), ""))


def domain(url: str) -> str:
    host = urlsplit(url or "").netloc.lower()
    return host[4:] if host.startswith("www.") else host


def parse_date(value) -> Optional[str]:
    """Parse common feed/API date formats into an ISO-8601 UTC string."""
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip()
        dt = None
        for fmt in ("%Y%m%dT%H%M%SZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
                break
            except ValueError:
                pass
        if dt is None:
            try:
                dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            except ValueError:
                try:
                    dt = parsedate_to_datetime(s)
                except (TypeError, ValueError):
                    return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"])}
_MONTHS.update({m: i + 1 for i, m in enumerate(
    ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
     "septiembre", "octubre", "noviembre", "diciembre"])})
_DATE_PATTERNS = [
    (re.compile(r"(20\d\d)[./-](\d{1,2})[./-](\d{1,2})"), "ymd"),
    (re.compile(r"(20\d\d)年(\d{1,2})月(\d{1,2})日"), "ymd"),
    (re.compile(r"令和(\d{1,2}|元)年(\d{1,2})月(\d{1,2})日"), "reiwa"),
    (re.compile(r"(\d{1,2})(?:\s+de)?\s+([a-z]+)(?:\s+de)?\s+(20\d\d)"), "dmy_name"),
    (re.compile(r"([a-z]+)\s+(\d{1,2}),?\s+(20\d\d)"), "mdy_name"),
]


def find_date(text: str) -> Optional[str]:
    """Find a date written in text, e.g. '8 October 2026', '2026.10.16',
    '令和８年10月13日', '15 de octubre de 2026'. Returns ISO UTC midnight or None."""
    t = normalize(text or "")
    for rx, kind in _DATE_PATTERNS:
        m = rx.search(t)
        if not m:
            continue
        try:
            if kind == "ymd":
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            elif kind == "reiwa":
                y = 2018 + (1 if m.group(1) == "元" else int(m.group(1)))
                mo, d = int(m.group(2)), int(m.group(3))
            elif kind == "dmy_name":
                y, mo, d = int(m.group(3)), _MONTHS.get(m.group(2)), int(m.group(1))
            else:
                y, mo, d = int(m.group(3)), _MONTHS.get(m.group(1)), int(m.group(2))
            if not mo:
                continue
            return datetime(y, mo, d, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
    return None


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
