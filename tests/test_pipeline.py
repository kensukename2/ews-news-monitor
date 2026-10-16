"""Storage, de-duplication, cross-language clustering, budget and publishing."""

import json
from datetime import datetime, timezone

from conftest import sample_classification

from ews_news import cluster, db, publish
from ews_news.budget import Budget, BudgetExceeded
from ews_news.pipeline import copy_to_duplicates, save_classification, store_item

NOW = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _raw(url, title, lang, snippet=""):
    return {"url": url, "title": title, "language": lang, "published_at": NOW,
            "snippet": snippet, "source_name": None}


def test_store_dedup_by_url_and_headline(tmp_config):
    conn = db.connect(tmp_config.path("database"))
    src = {"id": "t", "prefilter": "strict"}
    r1 = _raw("https://a.example/x?utm_source=rss", "Hurricane warning issued for Jamaica", "en")
    assert store_item(conn, tmp_config, src, r1, "2000") == "pending"
    assert store_item(conn, tmp_config, src, dict(r1, url="https://a.example/x"), "2000") is None
    r2 = _raw("https://b.example/y", "Hurricane warning issued for Jamaica - Other Outlet", "en")
    assert store_item(conn, tmp_config, src, r2, "2000") == "duplicate"
    assert store_item(conn, tmp_config, src, _raw("https://c/z", "Football results", "en"),
                      "2000") == "filtered_out"


def test_event_key_cross_language():
    a = cluster.event_key(sample_classification(event_name="Hurricane Melissa", hazard="tropical_cyclone"))
    b = cluster.event_key(sample_classification(event_name="Major Hurricane Melissa (2026)",
                                                hazard="storm_surge"))
    assert a == b == "named|melissa"
    c = cluster.event_key(sample_classification(event_name=None, hazard="flash_flood",
                                                countries=["PE"], event_location="Piura, Peru"))
    d = cluster.event_key(sample_classification(event_name=None, hazard="flood",
                                                countries=["PE"], event_location="Piura"))
    assert c == d == "flood|PE|piura"


def test_cross_language_story_and_publish(tmp_config):
    conn = db.connect(tmp_config.path("database"))
    src = {"id": "t", "prefilter": "relaxed"}
    ids = []
    for url, title, lang in [
        ("https://en.example/melissa", "Jamaica evacuates ahead of Hurricane Melissa", "en"),
        ("https://ja.example/melissa", "ハリケーン・メリッサ、ジャマイカで避難指示", "ja"),
        ("https://es.example/melissa", "Huracán Melissa: Jamaica ordena evacuaciones", "es"),
        ("https://en.example/dup", "Jamaica evacuates ahead of Hurricane Melissa - Wire", "en"),
    ]:
        store_item(conn, tmp_config, src, _raw(url, title, lang), "2000")
        ids.append(conn.execute("SELECT id FROM items WHERE url = ?", (url,)).fetchone()[0])
    for item_id, lang, summ in [(ids[0], "en", None), (ids[1], "ja", "避難指示が出された。"),
                                (ids[2], "es", "Se ordenaron evacuaciones.")]:
        save_classification(conn, item_id, "test", sample_classification(
            language=lang, event_name="Hurricane Melissa", countries=["JM"],
            wmo_regions=["IV"], summary_original=summ, review=False))
    assert copy_to_duplicates(conn) == 1
    assert cluster.assign_stories(conn, tmp_config) == 1

    m = publish.publish(conn, tmp_config)
    assert m["total_stories"] == 1 and m["new_today"] == 1
    month = json.loads((tmp_config.path("site_data") / m["months"][0]["file"]).read_text(encoding="utf-8"))
    story = month["stories"][0]
    assert story["languages"] == ["en", "es", "ja"]
    assert len(story["sources"]) == 4
    assert story["summaries_original"] == {"ja": "避難指示が出された。", "es": "Se ordenaron evacuaciones."}
    assert story["countries"] == [{"code": "JM", "name": "Jamaica"}]
    assert "IV" in m["facets"]["wmo_regions"]


def test_irrelevant_items_are_not_published(tmp_config):
    conn = db.connect(tmp_config.path("database"))
    store_item(conn, tmp_config, {"id": "t", "prefilter": "relaxed"},
               _raw("https://x/1", "Some text", "en"), "2000")
    save_classification(conn, 1, "test", sample_classification(relevant=False, confidence=0.95,
                                                               review=False))
    assert cluster.assign_stories(conn, tmp_config) == 0


def test_low_confidence_goes_to_review_not_dropped(tmp_config):
    conn = db.connect(tmp_config.path("database"))
    store_item(conn, tmp_config, {"id": "t", "prefilter": "relaxed"},
               _raw("https://x/2", "Some text", "en"), "2000")
    save_classification(conn, 1, "test", sample_classification(relevant=False, confidence=0.4,
                                                               review=True))
    assert cluster.assign_stories(conn, tmp_config) == 1


def test_budget_caps_daily_spending(tmp_config):
    conn = db.connect(tmp_config.path("database"))
    day = datetime(2026, 10, 2, tzinfo=timezone.utc)   # 30 days left in October
    b = Budget(conn, tmp_config.settings, now=day)
    assert abs(b.allowance - 5.0 / 30) < 1e-9
    b.check(0.1)
    b.record("classify", "claude-haiku-5-5", {"input_tokens": 1_000_000, "output_tokens": 0})
    try:
        b.check(0.1)  # 0.10 spent + 0.10 > 0.1667
    except BudgetExceeded:
        pass
    else:
        raise AssertionError("budget not enforced")


def test_db_dump_and_restore(tmp_config):
    conn = db.connect(tmp_config.path("database"))
    store_item(conn, tmp_config, {"id": "t", "prefilter": "relaxed"},
               _raw("https://x/3", "台風で避難指示", "ja"), "2000")
    conn.commit()
    db.dump(conn, tmp_config.path("db_dump"))
    conn.close()
    tmp_config.path("database").unlink()
    assert db.restore(tmp_config.path("database"), tmp_config.path("db_dump"))
    conn = db.connect(tmp_config.path("database"))
    assert conn.execute("SELECT title FROM items").fetchone()[0] == "台風で避難指示"


def test_unnamed_event_joins_by_title_similarity(tmp_config):
    conn = db.connect(tmp_config.path("database"))
    src = {"id": "t", "prefilter": "relaxed"}
    titles = [
        ("https://a/1", "Japan mistakenly issues warning for magnitude 9.9 earthquake", "en", "Tokyo, Japan"),
        ("https://b/2", "緊急地震速報、初の警報取り消し M9.9と過大推定", "ja", "Japan"),
        ("https://c/3", "Kenya floods displace thousands", "en", "Nairobi, Kenya"),
    ]
    en_titles = ["Japan mistakenly issues warning for magnitude 9.9 earthquake",
                 "Japan earthquake warning for magnitude 9.9 cancelled after overestimate",
                 "Kenya floods displace thousands"]
    for (url, title, lang, loc), title_en in zip(titles, en_titles):
        store_item(conn, tmp_config, src, _raw(url, title, lang), "2000")
        item_id = conn.execute("SELECT id FROM items WHERE url = ?", (url,)).fetchone()[0]
        kenya = "Kenya" in title_en
        save_classification(conn, item_id, "test", sample_classification(
            language=lang, title_en=title_en, event_name=None, event_location=loc,
            hazard="flood" if kenya else "earthquake", countries=["KE" if kenya else "JP"],
            review=False))
    assert cluster.assign_stories(conn, tmp_config) == 2
    assert conn.execute("SELECT COUNT(*) FROM stories").fetchone()[0] == 2


def test_baseline_first_run_and_source_rules(tmp_config):
    conn = db.connect(tmp_config.path("database"))
    src = {"id": "list", "prefilter": "relaxed"}
    undated = dict(_raw("https://x/old", "Senamhi emite aviso por lluvias", "es"), published_at=None)
    assert store_item(conn, tmp_config, src, undated, "2000", baseline=True) == "baseline"
    later = dict(_raw("https://x/new", "Senamhi emite aviso por heladas", "es"), published_at=None)
    assert store_item(conn, tmp_config, src, later, "2000", baseline=False) == "pending"
    gdacs = {"id": "gdacs", "prefilter": "relaxed", "require_any": ["orange", "red"]}
    assert store_item(conn, tmp_config, gdacs, _raw("https://g/1", "Green notification for tropical cyclone", "en"),
                      "2000") == "filtered_out"
    assert store_item(conn, tmp_config, gdacs, _raw("https://g/2", "Red alert for tropical cyclone MELISSA", "en"),
                      "2000") == "pending"
