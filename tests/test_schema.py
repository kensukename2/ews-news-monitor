"""The classification output schema and post-processing."""

import json
from types import SimpleNamespace

from conftest import sample_classification

from ews_news.classify import Classifier, postprocess
from ews_news.schema import CLASSIFICATION_SCHEMA, validate


def test_valid_sample_passes():
    assert validate(sample_classification()) == []


def test_missing_and_extra_fields_fail():
    d = sample_classification()
    del d["outcome"]
    d["extra"] = 1
    errors = validate(d)
    assert any("missing 'outcome'" in e for e in errors)
    assert any("unexpected 'extra'" in e for e in errors)


def test_enum_and_type_errors():
    assert validate(sample_classification(hazard="zombies"))
    assert validate(sample_classification(ew4all_pillars=[5]))
    assert validate(sample_classification(confidence="high"))
    assert validate(sample_classification(relevant=1))  # must be a real boolean


def test_nullable_fields_accept_null():
    assert validate(sample_classification(event_name=None, lead_time_hours=None)) == []


def test_schema_is_strict_everywhere():
    """Structured outputs need additionalProperties: false on every object."""
    def walk(s):
        if s.get("type") == "object":
            assert s.get("additionalProperties") is False
            assert set(s["required"]) == set(s["properties"])
            for v in s["properties"].values():
                walk(v)
        if "items" in s:
            walk(s["items"])
    walk(CLASSIFICATION_SCHEMA)


def test_postprocess_fills_regions_and_review(config):
    item = {"title": "原題", "language": "ja"}
    d = postprocess(sample_classification(countries=["in", "JP "], confidence=0.4,
                                          wmo_regions=["VI"]),
                    item, config.countries, threshold=0.6)
    assert d["countries"] == ["IN", "JP"]
    assert d["wmo_regions"] == ["II", "VI"]
    assert d["ew4all_pillars"] == [2, 3, 4]
    assert d["review"] is True
    assert d["original_title"] == "原題"


class _FakeBudget:
    def __init__(self):
        self.recorded = []

    def cost(self, model, usage, web_searches=0):
        return 0.0

    def check(self, estimate):
        pass

    def record(self, purpose, model, usage, web_searches=0):
        self.recorded.append((purpose, usage))


class _FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.kwargs = None
        self.messages = self

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=json.dumps(self.payload))],
            usage=SimpleNamespace(input_tokens=1200, output_tokens=400,
                                  cache_read_input_tokens=0, cache_creation_input_tokens=0))


def test_classifier_request_and_parse(config):
    client = _FakeClient(sample_classification(language="es", summary_original="Resumen."))
    budget = _FakeBudget()
    out = Classifier(config, budget, client).classify(
        {"title": "SENAMHI emite alerta roja", "language": "es", "url": "https://x", "snippet": ""})
    assert client.kwargs["model"] == config.settings["classify"]["model"]
    assert client.kwargs["output_config"]["format"]["schema"] is CLASSIFICATION_SCHEMA
    assert "SENAMHI emite alerta roja" in client.kwargs["messages"][0]["content"]
    assert out["summary_original"] == "Resumen."
    assert budget.recorded[0][1]["input_tokens"] == 1200


def test_classifier_rejects_invalid_output(config):
    bad = sample_classification()
    del bad["hazard"]
    try:
        Classifier(config, _FakeBudget(), _FakeClient(bad)).classify(
            {"title": "t", "language": "en", "url": "https://x"})
    except ValueError as e:
        assert "schema" in str(e)
    else:
        raise AssertionError("invalid output accepted")
