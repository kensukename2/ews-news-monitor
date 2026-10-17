"""AI News topic: schema, pre-filter (en/ja/es), clustering and story fields."""

import copy

import pytest

from ews_news import cluster, db, prefilter, publish
from ews_news.config import load_config
from ews_news.pipeline import save_classification, store_item
from ews_news.schema import validate
from ews_news.topics import ai


@pytest.fixture
def ai_config(tmp_path):
    c = copy.deepcopy(load_config("ai"))
    c.settings["paths"] = {"database": str(tmp_path / "ai.sqlite"), "db_dump": str(tmp_path / "ai.sql"),
                           "run_logs": str(tmp_path / "logs"), "site_data": str(tmp_path / "site")}
    return c


def sample(**overrides):
    d = {
        "relevant": True, "confidence": 0.9, "language": "en",
        "title_en": "ECMWF makes its AI forecasting system AIFS operational",
        "system_name": "AIFS",
        "institutions": [{"name": "European Centre for Medium-Range Weather Forecasts",
                          "short_name": "ECMWF", "type": "WMO_centre", "country": None}],
        "areas": ["forecasting"], "primary_area": "forecasting",
        "techniques": ["ai_weather_model"], "news_type": "operational_implementation",
        "value_added": "Forecasts in minutes using about 1000 times less energy.",
        "value_types": ["speed", "efficiency"], "countries": [], "wmo_regions": [],
        "event_date": "2026-10-12",
        "summary_en": "ECMWF put AIFS into operations.", "summary_original": None,
    }
    d.update(overrides)
    return d


def test_config_loads_ai_topic(ai_config):
    assert ai_config.topic == "ai"
    assert ai_config.settings["budget"]["monthly_usd"] == 5.0
    assert ai_config.module is ai
    assert set(ai_config.languages) == {"en", "ja", "es"}
    assert load_config("ai").settings["paths"]["site_data"] == "site/ai_news/data"


def test_schema_valid_and_strict():
    assert validate(sample(), ai.SCHEMA) == []
    assert validate(sample(news_type="rumour"), ai.SCHEMA)
    assert validate(sample(areas=["astrology"]), ai.SCHEMA)

    def walk(s):
        if s.get("type") == "object":
            assert s.get("additionalProperties") is False
            assert set(s["required"]) == set(s["properties"])
            for v in s["properties"].values():
                walk(v)
        if "items" in s:
            walk(s["items"])
    walk(ai.SCHEMA)


def test_postprocess_adds_primary_area():
    d = ai.postprocess(sample(areas=["communication"], primary_area="warnings"), {})
    assert d["areas"] == ["warnings", "communication"]


@pytest.mark.parametrize("lang,title,expected", [
    ("en", "Met Office uses machine learning to improve rainfall forecasts", True),
    ("en", "New AI chatbot helps students write essays", False),          # AI but no domain
    ("en", "Heavy rain warning for Scotland", False),                     # domain but no AI
    ("en", "GraphCast beats traditional models", True),                   # strong term
    ("ja", "気象庁、AIで線状降水帯の予測精度を向上", True),
    ("ja", "生成AIで議事録を自動作成", False),                             # AI, no domain
    ("ja", "MAILサーバーの障害で気象情報の配信が遅延", False),           # "AI" inside "MAIL"
    ("es", "El SENAMHI usa inteligencia artificial para mejorar el pronóstico", True),
    ("es", "La IA generativa transforma la publicidad", False),
    ("es", "Pronostico con IA para lluvias intensas", True),              # accent folding
])
def test_ai_prefilter(lang, title, expected):
    lang_cfg = load_config("ai").languages[lang]
    assert prefilter.score(title, "", lang_cfg, "strict")[0] is expected


def test_event_key_and_candidates():
    assert ai.event_key(sample()) == "aifs|operational_implementation"
    assert ai.event_key(sample(system_name=None)) is None
    story = {"system_names": [], "institutions": [{"name": "ECMWF", "short_name": "ECMWF",
                                                   "type": "WMO_centre", "country": None}]}
    assert ai.candidate_ok(sample(system_name=None), story)
    other = sample(system_name=None, institutions=[{"name": "Japan Meteorological Agency",
                                                    "short_name": "JMA", "type": "NMHS", "country": "JP"}])
    assert not ai.candidate_ok(other, story)


def test_cross_language_story_and_publish(ai_config):
    conn = db.connect(ai_config.path("database"))
    src = {"id": "t", "prefilter": "relaxed"}
    rows = [("https://a/1", "ECMWF's AIFS goes operational", "en", None),
            ("https://b/2", "ECMWF、AI予報システムAIFSを運用開始", "ja", "AIFSの運用を開始した。"),
            ("https://c/3", "El ECMWF pone en operación su sistema de IA AIFS", "es", "Se puso en operación.")]
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for url, title, lang, summ in rows:
        store_item(conn, ai_config, src, {"url": url, "title": title, "language": lang,
                                          "published_at": now, "snippet": ""}, "2000")
        item_id = conn.execute("SELECT id FROM items WHERE url = ?", (url,)).fetchone()[0]
        save_classification(conn, item_id, "test", sample(language=lang, summary_original=summ,
                                                          review=False))
    assert cluster.assign_stories(conn, ai_config) == 1
    m = publish.publish(conn, ai_config)
    assert m["topic"] == "ai" and m["total_stories"] == 1
    assert m["facets"]["areas"] == ["forecasting"]
    assert m["facets"]["institution_types"] == ["WMO_centre"]
    import json
    story = json.loads((ai_config.path("site_data") / m["months"][0]["file"]).read_text())["stories"][0]
    assert story["languages"] == ["en", "es", "ja"]
    assert story["system_names"] == ["AIFS"]
    assert story["news_type"] == "operational_implementation"
    assert story["value_added"].startswith("Forecasts in minutes")


def test_vague_article_can_match_same_area():
    story = {"system_names": [], "areas": ["forecasting"],
             "institutions": [{"name": "MIT", "short_name": "MIT", "type": "research", "country": "US"}]}
    vague = sample(system_name=None, institutions=[])
    assert ai.candidate_ok(vague, story)
    assert not ai.candidate_ok(sample(system_name=None, institutions=[], primary_area="hydrology",
                                      areas=["hydrology"]), story)


def test_specific_article_can_join_vague_story():
    vague_story = {"system_names": [], "areas": ["forecasting"], "institutions": []}
    assert ai.candidate_ok(sample(system_name=None), vague_story)
