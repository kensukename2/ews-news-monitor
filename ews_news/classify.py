"""Classify and extract information from an item with the Claude API.

The prompt and output schema come from the topic module
(ews_news/topics/<topic>.py). Only the title, source, date and short snippet
are sent - never full text.
"""

import json

import anthropic

from .budget import BudgetExceeded, usage_dict
from .schema import validate

REGION_ORDER = ["I", "II", "III", "IV", "V", "VI"]


def build_user_message(item: dict) -> str:
    return json.dumps({
        "title": item["title"],
        "source": item.get("source_name"),
        "published_at": item.get("published_at"),
        "language_hint": item["language"],
        "snippet": item.get("snippet") or "",
        "url": item["url"],
    }, ensure_ascii=False)


def postprocess(data: dict, item: dict, countries: dict, threshold: float) -> dict:
    """Shared clean-up: clamp confidence, fix country codes, fill WMO regions, review flag."""
    data["confidence"] = max(0.0, min(1.0, float(data.get("confidence") or 0)))
    data["countries"] = sorted({c.strip().upper() for c in data.get("countries", []) if c.strip()})
    regions = set(data.get("wmo_regions", []))
    for c in data["countries"]:
        if c in countries:
            regions.add(countries[c]["region"])
    data["wmo_regions"] = [r for r in REGION_ORDER if r in regions]
    if not data.get("language"):
        data["language"] = item["language"]
    data["original_title"] = item["title"]
    data["review"] = data["confidence"] < threshold
    return data


class Classifier:
    def __init__(self, config, budget, client=None):
        self.config = config
        self.topic = config.module
        self.cfg = config.settings["classify"]
        self.budget = budget
        self.client = client or anthropic.Anthropic()

    def estimate(self) -> float:
        # Rough upper bound per item, used for the budget check before calling.
        return self.budget.cost(self.cfg["model"],
                                {"input_tokens": 2500, "output_tokens": 900})

    def classify(self, item: dict) -> dict:
        self.budget.check(self.estimate())
        response = self.client.messages.create(
            model=self.cfg["model"],
            max_tokens=self.cfg["max_tokens"],
            thinking={"type": "disabled"},
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": self.topic.SCHEMA},
            },
            system=[{"type": "text", "text": self.topic.SYSTEM_PROMPT,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": build_user_message(item)}],
        )
        self.budget.record("classify", self.cfg["model"], usage_dict(response.usage))
        if response.stop_reason == "refusal":
            raise ValueError("model refused to classify")
        if response.stop_reason == "max_tokens":
            raise ValueError("output truncated (max_tokens)")
        text = next(b.text for b in response.content if b.type == "text")
        data = json.loads(text)
        errors = validate(data, self.topic.SCHEMA)
        if errors:
            raise ValueError(f"schema errors: {errors[:3]}")
        data = postprocess(data, item, self.config.countries, self.cfg["confidence_threshold"])
        return self.topic.postprocess(data, item)


__all__ = ["Classifier", "BudgetExceeded", "postprocess", "build_user_message"]
