from conftest import FIXTURES

from ews_news import prefilter
from ews_news.collectors import rss


def _keep(config, lang, title, snippet="", mode="strict"):
    return prefilter.score(title, snippet, config.languages[lang], mode)[0]


def test_japanese_without_spaces(config):
    items = rss.parse_feed((FIXTURES / "ja_asahi.rdf").read_bytes(), {"name": "x", "language": "ja"})
    keep = [_keep(config, "ja", i["title"], i["snippet"]) for i in items]
    assert keep == [True, True, False]  # special warning, EEW failure, baseball


def test_japanese_fullwidth_normalised(config):
    # Full-width "Ｊアラート" must match the half-width config term "Jアラート".
    assert _keep(config, "ja", "Ｊアラートで避難呼びかけ")


def test_spanish(config):
    items = rss.parse_feed((FIXTURES / "es_feed.xml").read_bytes(), {"name": "x", "language": "es"})
    keep = [_keep(config, "es", i["title"], i["snippet"]) for i in items]
    # red alert kept; unaccented "Inundacion" + "alerta" kept; health alert and football dropped
    assert keep == [True, True, False, False]


def test_english_word_boundaries_and_excludes(config):
    items = rss.parse_feed((FIXTURES / "en_feed.xml").read_bytes(), {"name": "x", "language": "en"})
    keep = [_keep(config, "en", i["title"], i["snippet"]) for i in items]
    assert keep == [True, False, False]  # cyclone kept; profit warning excluded; "Warner" != "warn"


def test_relaxed_mode_only_applies_excludes(config):
    assert _keep(config, "en", "Some unrelated headline", mode="relaxed")
    assert not _keep(config, "es", "Alerta sanitaria por dengue", mode="relaxed")


def test_loose_mode_needs_one_term(config):
    assert _keep(config, "es", "Actualizaron las previsiones del río: crecida en Concordia", mode="loose")
    assert not _keep(config, "es", "La carrera es entre el PP y Vox", mode="loose")
    assert not _keep(config, "en", "A New Homeowner Guide to Professional Roofing Services", mode="loose")
