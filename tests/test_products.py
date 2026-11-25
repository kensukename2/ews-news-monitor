"""AI products page: status rule, merging of names, Claude analysis and output."""

import copy
import json
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest

from ews_news import db, products
from ews_news.config import load_config
from ews_news.pipeline import save_classification, store_item

NOW = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def ai_config(tmp_path):
    c = copy.deepcopy(load_config("ai"))
    c.settings["paths"] = {"database": str(tmp_path / "ai.sqlite"), "db_dump": str(tmp_path / "ai.sql"),
                           "run_logs": str(tmp_path / "logs"), "site_data": str(tmp_path / "ai_news" / "data")}
    return c


def dep(stage, type_="NMHS", name="JMA"):
    return {"institution": name, "short_name": name, "type": type_, "country": "JP",
            "stage": stage, "eligible": type_ in products.ELIGIBLE}


@pytest.mark.parametrize("deployments,released,expected", [
    ([dep("operational")], False, 1),                                  # NMHS operational
    ([dep("operational", "WMO_centre", "ECMWF")], True, 1),            # WMO centre counts
    ([dep("testing"), dep("research", "research", "MIT")], False, 2),  # NMHS testing
    ([dep("operational", "private_sector", "Weathernews")], True, 4),  # company only
    ([], True, 4),                                                     # released, no NMHS use
    ([dep("planned")], True, 4),                                       # NMHS only planned
    ([dep("research", "research", "MIT")], False, 3),                  # research only
    ([dep("testing"), dep("operational", "WMO_centre", "ECMWF")], True, 1),  # most advanced wins
])
def test_status_rule(deployments, released, expected):
    assert products.status_of({"deployments": deployments, "released": released}) == expected


def mention(name, stage="testing", type_="NMHS", inst="KMA", aliases=(), maturity="research", named=True):
    return {"name": name, "aliases": list(aliases), "is_named": named, "developer": "Dev",
            "developer_type": "research", "product_type": "weather_model", "areas": ["forecasting"],
            "maturity": maturity, "deployments": [
                {"institution": inst, "short_name": inst, "type": type_, "country": "KR", "stage": stage}]}


def add_article(conn, config, url, mentions, lang="en"):
    store_item(conn, config, {"id": "t", "prefilter": "relaxed"},
               {"url": url, "title": "T " + url, "language": lang, "published_at": NOW, "snippet": ""}, "2000")
    item_id = conn.execute("SELECT id FROM items WHERE url = ?", (url,)).fetchone()[0]
    save_classification(conn, item_id, "test", {"relevant": True, "confidence": 0.9, "review": False,
                                                "title_en": "Title " + url, "summary_en": "Summary."})
    products.ensure_tables(conn)
    conn.execute("INSERT INTO product_mentions (item_id, analysed_at, data_json) VALUES (?,?,?)",
                 (item_id, NOW, db.dumps({"products": mentions})))
    return item_id


def test_aggregate_merges_aliases_and_keeps_most_advanced_stage(ai_config):
    conn = db.connect(ai_config.path("database"))
    add_article(conn, ai_config, "https://a/1", [mention("PBC model", "research", "research", "MIT")])
    add_article(conn, ai_config, "https://a/2", [mention("Probabilistic Bias Correction framework", "testing")])
    add_article(conn, ai_config, "https://a/3", [mention("Mazu", "operational", inst="CMA")])
    add_article(conn, ai_config, "https://a/4", [mention("MAZU", "testing", inst="CMA")], lang="ja")
    assert products.aggregate(conn, ai_config) == 2
    out = products.write_json(conn, ai_config)
    by_name = {p["name"]: p for p in out["products"]}
    pbc = by_name["Probabilistic Bias Correction (PBC)"]           # from product_aliases.yaml
    assert pbc["n_articles"] == 2 and pbc["status"] == 2
    mazu = by_name["MAZU"]
    assert mazu["status"] == 1
    assert [d["stage"] for d in mazu["deployments"]] == ["operational"]   # CMA: operational kept
    assert out["counts"] == {1: 1, 2: 1, 3: 0, 4: 0}
    assert "item_id" not in mazu["evidence"][0]


def test_analysis_once_per_article_and_assessment_cached(ai_config):
    conn = db.connect(ai_config.path("database"))
    products.ensure_tables(conn)
    store_item(conn, ai_config, {"id": "t", "prefilter": "relaxed"},
               {"url": "https://x/1", "title": "気象庁がAIを試験運用", "language": "ja",
                "published_at": NOW, "snippet": ""}, "2000")
    save_classification(conn, 1, "claude-haiku-5-5", {"relevant": True, "confidence": 0.9, "review": False,
                                                      "title_en": "JMA tests AI", "summary_en": "JMA tests AI."})
    calls = []

    def create(**kw):
        calls.append(kw)
        if "KNOWN PRODUCTS" in kw["messages"][0]["content"]:
            text = json.dumps({"products": [mention("JMA AI nowcast (unnamed)", "testing", inst="JMA", named=False)]})
        else:
            text = json.dumps({"assessment": "JMA is testing it.", "status_basis": "Testing at JMA"})
        return NS(content=[NS(type="text", text=text)],
                  usage=NS(input_tokens=100, output_tokens=50, cache_read_input_tokens=0,
                           cache_creation_input_tokens=0))

    client = NS(messages=NS(create=create))
    budget = NS(check=lambda e: None, cost=lambda *a, **k: 0.0, record=lambda *a, **k: 0.0, spent_run=0.0)
    stats = products.run_weekly(conn, ai_config, budget, client)
    assert stats["analysis"]["analysed"] == 1 and stats["assessment"]["assessed"] == 1
    p = products.write_json(conn, ai_config)["products"][0]
    assert p["status"] == 2 and p["is_named"] is False and p["assessment"] == "JMA is testing it."

    # Second week: nothing new -> no API calls at all.
    calls.clear()
    products.run_weekly(conn, ai_config, budget, client)
    assert calls == []


def test_placeholder_institutions_dropped_and_db_merges(ai_config):
    conn = db.connect(ai_config.path("database"))
    add_article(conn, ai_config, "https://b/1", [mention("Tool X", "research", "research", "Not specified in article")])
    add_article(conn, ai_config, "https://b/2", [mention("MIT subseasonal tool (unnamed)", "testing", inst="KMSA",
                                                         named=False)])
    products.aggregate(conn, ai_config)
    assert products.write_json(conn, ai_config)["products"][1]["deployments"] == [] or \
        all("specified" not in d["institution"] for p in products.write_json(conn, ai_config)["products"]
            for d in p["deployments"])
    conn.execute("INSERT INTO product_merges (variant_key, canonical, decided_at) VALUES (?,?,?)",
                 (products._key("MIT subseasonal tool (unnamed)"), "Tool X", NOW))
    assert products.aggregate(conn, ai_config) == 1
    p = products.write_json(conn, ai_config)["products"][0]
    assert p["name"] == "Tool X" and p["n_articles"] == 2 and p["status"] == 2


def test_unnamed_product_merged_into_named_one_in_same_story(ai_config):
    conn = db.connect(ai_config.path("database"))
    ids = [add_article(conn, ai_config, "https://c/1", [mention("PBC", "testing", inst="KMSA")]),
           add_article(conn, ai_config, "https://c/2", [mention("MIT subseasonal tool (unnamed)", "research",
                                                                "research", "MIT", named=False)]),
           add_article(conn, ai_config, "https://c/3", [mention("Other flood AI (unnamed)", "research",
                                                                "research", "UCL", named=False)])]
    for sid in (6, 7):
        conn.execute("INSERT INTO stories (id, event_key, hazard, first_seen, last_updated, event_date, data_json)"
                     " VALUES (?, '', 'forecasting', ?, ?, ?, '{}')", (sid, NOW, NOW, NOW))
    conn.execute("UPDATE items SET story_id = 6 WHERE id IN (?, ?)", (ids[0], ids[1]))
    conn.execute("UPDATE items SET story_id = 7 WHERE id = ?", (ids[2],))
    assert products.aggregate(conn, ai_config) == 2
    names = {p["name"]: p["n_articles"] for p in products.write_json(conn, ai_config)["products"]}
    assert names == {"Probabilistic Bias Correction (PBC)": 2, "Other flood AI (unnamed)": 1}
