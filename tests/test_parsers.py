"""Feed/API parsers, including Japanese and Spanish samples."""

import json

from conftest import FIXTURES

from ews_news.collectors import gdelt, rss
from ews_news.enrich import extract_description
from ews_news.textutil import canonical_url, parse_date, title_key


def test_rss_japanese_rdf():
    items = rss.parse_feed((FIXTURES / "ja_asahi.rdf").read_bytes(),
                           {"name": "朝日新聞", "language": "ja"})
    assert len(items) == 3
    first = items[0]
    assert first["title"].startswith("線状降水帯が発生")
    assert first["language"] == "ja"
    assert first["published_at"] == "2026-10-15T21:30:00Z"  # +09:00 -> UTC
    assert "大雨特別警報" in first["snippet"]
    assert items[1]["snippet"] == ""


def test_rss_spanish_html_and_entities():
    items = rss.parse_feed((FIXTURES / "es_feed.xml").read_bytes(),
                           {"name": "Diario Ejemplo", "language": "es"})
    assert len(items) == 4
    s = items[0]
    assert "<p>" not in s["snippet"] and "Meteorología" in s["snippet"]
    assert s["published_at"] == "2026-10-15T19:00:00Z"
    assert s["source_name"] == "Diario Ejemplo"


def test_rss_english():
    items = rss.parse_feed((FIXTURES / "en_feed.xml").read_bytes(),
                           {"name": "Example", "language": "en"})
    assert items[0]["title"].startswith("Cyclone Dana")


def test_gdelt_spanish():
    data = json.loads((FIXTURES / "gdelt_es.json").read_text(encoding="utf-8"))
    items = gdelt.parse_response(data, {"name": "GDELT", "language": "es"})
    assert len(items) == 2  # the entry without URL is skipped
    assert items[0]["published_at"] == "2026-10-15T18:30:00Z"
    assert items[0]["source_name"] == "example.mx"
    assert items[0]["title"].startswith("Huracán")


def test_canonical_url_strips_tracking():
    a = canonical_url("https://diario.example.pe/x?utm_source=rss&id=3#top")
    b = canonical_url("http://www.diario.example.pe/x/?id=3")
    assert a == b == "https://diario.example.pe/x?id=3"


def test_title_key_handles_japanese_and_outlet_suffix():
    assert title_key("ＮＴＴドコモ、緊急地震速報が配信されず") == title_key("NTTドコモ 緊急地震速報が配信されず")
    assert title_key("Floods hit Peru - Reuters") == title_key("Floods hit Peru")


def test_parse_date_formats():
    assert parse_date("20261015T183000Z") == "2026-10-15T18:30:00Z"
    assert parse_date("Thu, 15 Oct 2026 14:00:00 -0500") == "2026-10-15T19:00:00Z"
    assert parse_date("2026-10-16T06:30:00+09:00") == "2026-10-15T21:30:00Z"
    assert parse_date("2 days ago") is None


def test_extract_meta_description():
    html = ('<html><head><meta charset="utf-8"><meta property="og:description" '
            'content="気象庁は大雨特別警報を発表した。"></head>')
    assert extract_description(html) == "気象庁は大雨特別警報を発表した。"


def test_gdelt_title_tidy():
    assert gdelt.tidy_title("GLOF system at 4 , 100 - metre - high glacier") == "GLOF system at 4,100-metre-high glacier"
    assert gdelt.tidy_title("Super Niño 2026 : crece") == "Super Niño 2026: crece"


def test_websearch_keeps_only_real_recent_results(config):
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace as NS
    from ews_news.collectors import websearch

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    old = (datetime.now(timezone.utc) - timedelta(days=60)).strftime("%Y-%m-%d")
    picks = {"articles": [
        {"url": "https://a.example/new", "published_date": today, "snippet": "気象庁が特別警報を発表。"},
        {"url": "https://a.example/old", "published_date": old, "snippet": "old"},
        {"url": "https://a.example/undated", "published_date": None, "snippet": "?"},
        {"url": "https://invented.example/x", "published_date": today, "snippet": "not in results"},
    ]}
    results = [NS(type="web_search_result", url=u, title="T " + u, page_age=None)
               for u in ("https://a.example/new", "https://a.example/old", "https://a.example/undated")]
    message = NS(content=[NS(type="web_search_tool_result", content=results),
                          NS(type="text", text=json.dumps(picks))],
                 usage=NS(input_tokens=1, output_tokens=1, cache_read_input_tokens=0,
                          cache_creation_input_tokens=0, server_tool_use=NS(web_search_requests=1)))
    budget = NS(check=lambda e: None, cost=lambda *a, **k: 0.0, record=lambda *a, **k: 0.0)
    ctx = NS(config=config, anthropic=NS(messages=NS(create=lambda **k: message)), budget=budget)
    items = websearch.collect(config.source("websearch_ja"), ctx)
    assert [i["url"] for i in items] == ["https://a.example/new"]
    assert items[0]["snippet"] == "気象庁が特別警報を発表。"


def test_websearch_relative_page_age():
    from datetime import datetime, timezone
    from ews_news.collectors.websearch import page_age_date, query_vars
    now = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
    assert page_age_date("3 days ago", now) == "2026-10-13T12:00:00Z"
    assert page_age_date("October 14, 2026", now) is None or page_age_date("2026-10-14", now) == "2026-10-14T00:00:00Z"
    assert query_vars(now)["month_ja"] == "2026年10月"
    assert query_vars(now)["month_es"] == "octubre 2026"
