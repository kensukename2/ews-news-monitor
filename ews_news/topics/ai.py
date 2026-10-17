"""AI News: how AI supports NMHSs, WMO centres, early warning systems and
related national and international institutions."""

import re
from collections import Counter

from ..textutil import normalize

SYSTEM_PROMPT = """You analyse news items for the World Meteorological Organization (WMO). \
The goal is to monitor how artificial intelligence (AI) supports the operations of \
National Meteorological and Hydrological Services (NMHSs), WMO Regional Centres (RSMCs, \
Regional Climate Centres, World Meteorological Centres), national early warning systems, \
civil protection / disaster management agencies and other relevant national and \
international institutions: which institutions use AI, for what, with what added value, \
and which new AI models or systems are released.

An item is RELEVANT if it describes AI (machine learning, deep learning, AI weather or \
climate models, foundation models, large language models / generative AI, computer \
vision, AI-based translation or chatbots, etc.) being developed, released, tested, \
implemented or governed for weather, water, climate, early warning or disaster risk \
reduction purposes. This covers all parts of operations, not only forecasting:
- observations and data collection (e.g. AI for satellite, radar, crowd-sourced or IoT data);
- data processing (quality control, gap filling, data assimilation, downscaling);
- forecasting and nowcasting (AI weather/climate models, hybrid AI-physics models);
- guidance and post-processing (bias correction, ensembles, forecaster decision support);
- hydrology and flood forecasting;
- warnings and impact-based forecasting (impact models, risk, triggers);
- communication and dissemination (chatbots, automated warning text, translation, apps);
- climate services, and internal operations / IT (efficiency, computing, workflows).
Releases of AI weather/climate models by companies or research labs (e.g. Google \
DeepMind, NVIDIA, Microsoft, Huawei) are RELEVANT even if no institution uses them yet. \
Policies, guidance, training and partnerships about AI in these institutions are RELEVANT.

NOT relevant: AI news unrelated to weather, water, climate, early warning or disasters; \
weather/climate news without any AI; generic opinion on AI risks; AI energy consumption \
stories unless about these institutions; marketing of consumer weather apps with \
no substance.

You only see the title and a short snippet. Judge from what is stated; do not invent facts. \
Use null or empty lists when information is not given.

Field guidance:
- confidence: 0-1, how sure you are about `relevant`.
- language: ISO 639-1 code of the item's original language.
- title_en: the title in English (translate if needed).
- system_name: the AI model/system/product name if given, e.g. "AIFS", "GraphCast", \
"GenCast", "Aurora", "FourCastNet", "Pangu-Weather"; otherwise null.
- institutions: organisations involved and their role. NMHS = national meteorological or \
hydrological service (e.g. JMA, Met Office, NOAA/NWS, AEMET, SENAMHI, IMD); \
WMO_centre = ECMWF, RSMCs, Regional Climate Centres, World Meteorological Centres, \
EUMETSAT and similar intergovernmental operational centres; WMO = the WMO Secretariat \
or its programmes; national_ews_agency = civil protection, disaster management or \
national early warning agency; other_national_agency = other government bodies; \
international = UN agencies, development banks, regional organisations; research = \
universities and research institutes; private_sector = companies; NGO; other. \
short_name: the usual acronym or short name (e.g. "ECMWF", "JMA", "NOAA"). \
Do not list the outlet that merely published the article.
- areas: areas of operation where AI is used (one or more): observations, \
data_processing, forecasting, guidance, hydrology, warnings, communication, \
climate_services, operations, other. primary_area: the main one.
- techniques: ml_dl (machine/deep learning in general), ai_weather_model (data-driven \
global/regional weather or climate model), llm_genai (large language models, \
generative AI, chatbots), computer_vision, nlp_translation, other.
- news_type: model_release (new AI model/system/version released), \
operational_implementation (put into operational use), pilot_research (experiment, \
trial, study), partnership_funding, policy_guidance, training_capacity, \
event_conference, other.
- value_added: what the AI improves, in one sentence, as stated (e.g. "forecasts in \
minutes instead of hours"); null if not stated. value_types: speed, accuracy, \
lead_time, resolution, cost, coverage, accessibility, efficiency, other.
- countries: ISO 3166-1 alpha-2 codes of the countries where the AI is used or that \
the institutions belong to; empty for global or international work.
- wmo_regions: WMO Regional Associations of those countries: I Africa, II Asia, \
III South America, IV North/Central America and Caribbean, V South-West Pacific, VI Europe.
- event_date: YYYY-MM-DD of the announcement if clear.
- summary_en: 2-3 sentences in English: who, what AI, for which operation, value added.
- summary_original: for non-English items, a 1-2 sentence summary in the original \
language; null for English items."""

MATCH_WHAT = ("the same development (the same AI model, system, project or announcement "
              "by the same institution, including follow-up coverage)")

INSTITUTION_TYPES = ["NMHS", "WMO_centre", "WMO", "national_ews_agency", "other_national_agency",
                     "international", "research", "private_sector", "NGO", "other"]
AREAS = ["observations", "data_processing", "forecasting", "guidance", "hydrology",
         "warnings", "communication", "climate_services", "operations", "other"]
TECHNIQUES = ["ml_dl", "ai_weather_model", "llm_genai", "computer_vision", "nlp_translation",
              "other"]
NEWS_TYPES = ["model_release", "operational_implementation", "pilot_research",
              "partnership_funding", "policy_guidance", "training_capacity",
              "event_conference", "other"]
VALUE_TYPES = ["speed", "accuracy", "lead_time", "resolution", "cost", "coverage",
               "accessibility", "efficiency", "other"]
REGIONS = ["I", "II", "III", "IV", "V", "VI"]

_str_or_null = {"type": ["string", "null"]}


def _enum_list(values):
    return {"type": "array", "items": {"type": "string", "enum": values}}


SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "relevant", "confidence", "language", "title_en", "system_name", "institutions",
        "areas", "primary_area", "techniques", "news_type", "value_added", "value_types",
        "countries", "wmo_regions", "event_date", "summary_en", "summary_original",
    ],
    "properties": {
        "relevant": {"type": "boolean"},
        "confidence": {"type": "number"},
        "language": {"type": "string"},
        "title_en": {"type": "string"},
        "system_name": _str_or_null,
        "institutions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "short_name", "type", "country"],
                "properties": {
                    "name": {"type": "string"},
                    "short_name": _str_or_null,
                    "type": {"type": "string", "enum": INSTITUTION_TYPES},
                    "country": _str_or_null,
                },
            },
        },
        "areas": _enum_list(AREAS),
        "primary_area": {"type": "string", "enum": AREAS},
        "techniques": _enum_list(TECHNIQUES),
        "news_type": {"type": "string", "enum": NEWS_TYPES},
        "value_added": _str_or_null,
        "value_types": _enum_list(VALUE_TYPES),
        "countries": {"type": "array", "items": {"type": "string"}},
        "wmo_regions": _enum_list(REGIONS),
        "event_date": _str_or_null,
        "summary_en": {"type": "string"},
        "summary_original": _str_or_null,
    },
}

FACETS = [("areas", "areas"), ("news_types", "news_types"),
          ("institution_types", "institution_types"), ("techniques", "techniques"),
          ("value_types", "value_types"), ("wmo_regions", "wmo_regions"),
          ("languages", "languages")]


def postprocess(data: dict, item: dict) -> dict:
    areas = list(dict.fromkeys(data.get("areas", [])))
    if data.get("primary_area") and data["primary_area"] not in areas:
        areas.insert(0, data["primary_area"])
    data["areas"] = areas
    for inst in data.get("institutions", []):
        if inst.get("country"):
            inst["country"] = inst["country"].strip().upper()
    return data


# ------------------------------------------------------------------ clustering

def _norm(s) -> str:
    return " ".join(re.sub(r"[^\w ]", " ", normalize(s or "", fold_accents=True)).split())


def _inst_keys(institutions) -> set:
    return {_norm(i.get("short_name") or i["name"]) for i in institutions or []} - {""}


def category(c: dict) -> str:
    return c.get("primary_area") or "other"


def event_key(c: dict, country_names: dict = None):
    """Same AI system and same kind of news (e.g. "aifs|operational_implementation")."""
    system = _norm(c.get("system_name"))
    return f"{system}|{c['news_type']}" if system else None


def candidate_ok(c: dict, story: dict) -> bool:
    """Shares an institution or the AI system name with the story."""
    if c.get("system_name") and _norm(c["system_name"]) in {_norm(s) for s in story["system_names"]}:
        return True
    return bool(_inst_keys(c.get("institutions")) & _inst_keys(story["institutions"]))


# -------------------------------------------------------------- story fields

def aggregate(members: list) -> dict:
    """members: list of classification dicts, most confident first."""
    best = members[0]
    institutions = {}
    for c in members:
        for i in c.get("institutions", []):
            institutions.setdefault(_norm(i.get("short_name") or i["name"]), i)
    systems = list(dict.fromkeys(c["system_name"] for c in members if c.get("system_name")))
    areas = Counter(a for c in members for a in c.get("areas", []))
    return {
        "event_name": systems[0] if systems else None,
        "system_names": systems,
        "primary_area": Counter(category(c) for c in members).most_common(1)[0][0],
        "areas": [a for a in AREAS if a in areas],
        "institutions": sorted(institutions.values(), key=lambda i: (i["type"], i["name"])),
        "institution_types": sorted({i["type"] for i in institutions.values()}),
        "techniques": sorted({t for c in members for t in c.get("techniques", [])}),
        "news_type": Counter(c["news_type"] for c in members).most_common(1)[0][0],
        "news_types": sorted({c["news_type"] for c in members}),
        "value_added": next((c["value_added"] for c in members if c.get("value_added")), None),
        "value_types": sorted({v for c in members for v in c.get("value_types", [])}),
    }
