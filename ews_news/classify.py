"""Classify and extract information from an item with the Claude API.

Only the title, source, date and short snippet are sent - never full text.
"""

import json

import anthropic

from .budget import BudgetExceeded, usage_dict
from .schema import CLASSIFICATION_SCHEMA, validate

SYSTEM_PROMPT = """You analyse news items for the World Meteorological Organization (WMO). \
The goal is to monitor how early warning services were actually provided during hazardous \
events: how National Meteorological and Hydrological Services (NMHSs), WMO Regional \
Specialized Meteorological Centres (RSMCs) and partner agencies monitored, forecast, issued \
and communicated warnings, and what happened as a result.

An item is RELEVANT if it describes, for any natural hazard, at least one of:
- monitoring or detection of a developing hazard;
- forecasts or advisories issued (including RSMC bulletins and impact-based forecasts);
- warnings issued and how they were disseminated (SMS, cell broadcast, CAP, radio, TV, \
sirens, community networks, social media...);
- preparedness or response actions triggered by warnings (evacuations, anticipatory action, \
early cash transfers, closures);
- reported outcomes of warnings: lives saved, losses reduced, or shortcomings (late, missed, \
not received, not acted on);
- new or improved early warning systems being installed or used.

NOT relevant: generic climate-change commentary; pure damage reports with no mention of \
warnings or forecasts; routine daily weather forecasts or automatic listings of current \
warnings without any news content; non-natural hazards (health, crime, finance, conflict).

An official press release from a meteorological, hydrological, geophysical or civil \
protection agency about warnings, forecasts or alert levels it issued (including follow-up \
reports such as "第2報", corrections or cancellations) is RELEVANT even if only its title \
is available.

You only see the title and a short snippet. Judge from what is stated; do not invent facts. \
Use null, empty lists or "not_reported" when information is not given.

Field guidance:
- confidence: 0-1, how sure you are about `relevant`.
- language: ISO 639-1 code of the item's original language.
- title_en: the title in English (translate if needed).
- event_name: standard English name if the event has one, e.g. "Typhoon Ragasa", \
"Hurricane Melissa", "Storm Boris"; otherwise null. Use the same name a reader in any \
language would recognise.
- event_location: main affected place in English, "Province/City, Country", e.g. \
"Piura, Peru"; null if unknown.
- event_date: YYYY-MM-DD of the hazard event if stated or clear from the publication date.
- countries: ISO 3166-1 alpha-2 codes of affected countries (e.g. "JP", "PE").
- wmo_regions: WMO Regional Associations of those countries: I Africa, II Asia, \
III South America, IV North/Central America and Caribbean, V South-West Pacific, VI Europe.
- agencies: organisations mentioned with their role. NMHS = national meteorological or \
hydrological service (e.g. JMA, IMD, PAGASA, AEMET, SENAMHI); RSMC = when acting as a WMO \
regional centre (e.g. RSMC Tokyo, RSMC Miami/NHC, RSMC New Delhi, RSMC Nadi); \
other_national_agency = civil protection, disaster management, river authorities etc.; \
international = UN, WMO, IFRC, regional bodies; NGO; media (only when the media outlet \
itself relayed warnings, e.g. a broadcaster); other (e.g. telecom operators). Do not list \
the outlet that merely published the article.
- ew4all_pillars: Early Warnings for All pillars touched: 1 risk knowledge; 2 detection, \
monitoring, analysis and forecasting; 3 warning dissemination and communication; \
4 preparedness and response.
- lead_time: as stated (e.g. "48 hours before landfall"); lead_time_hours if computable.
- outcome: positive (warnings worked: lives saved, timely evacuation), negative (late, \
missed, not received, not acted on), mixed, or not_reported; justify in one sentence.
- impacts: numbers only if stated.
- summary_en: 2-3 sentences in English focused on the early warning aspects.
- summary_original: for non-English items, a 1-2 sentence summary in the original \
language; null for English items."""


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
    """Clean up model output: clamp confidence, fix codes, fill WMO regions."""
    data["confidence"] = max(0.0, min(1.0, float(data.get("confidence") or 0)))
    data["countries"] = sorted({c.strip().upper() for c in data.get("countries", []) if c.strip()})
    regions = set(data.get("wmo_regions", []))
    for c in data["countries"]:
        if c in countries:
            regions.add(countries[c]["region"])
    order = ["I", "II", "III", "IV", "V", "VI"]
    data["wmo_regions"] = [r for r in order if r in regions]
    data["ew4all_pillars"] = sorted(set(data.get("ew4all_pillars", [])))
    if not data.get("language"):
        data["language"] = item["language"]
    data["original_title"] = item["title"]
    data["review"] = data["confidence"] < threshold
    return data


class Classifier:
    def __init__(self, config, budget, client=None):
        self.config = config
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
                "format": {"type": "json_schema", "schema": CLASSIFICATION_SCHEMA},
            },
            system=[{"type": "text", "text": SYSTEM_PROMPT,
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
        errors = validate(data)
        if errors:
            raise ValueError(f"schema errors: {errors[:3]}")
        return postprocess(data, item, self.config.countries, self.cfg["confidence_threshold"])


__all__ = ["Classifier", "BudgetExceeded", "SYSTEM_PROMPT", "postprocess", "build_user_message"]
