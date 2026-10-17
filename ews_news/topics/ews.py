"""EWS News: how early warning services were provided during hazardous events."""

import re
from collections import Counter

from ..textutil import normalize

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

MATCH_WHAT = ("the same hazard event (e.g. the same storm, the same earthquake and its "
              "warning, the same flood episode in the same area)")

HAZARDS = [
    "tropical_cyclone", "flood", "flash_flood", "storm_surge", "heavy_rain",
    "heatwave", "drought", "severe_storm", "tornado", "landslide", "tsunami",
    "earthquake", "volcanic", "wildfire", "cold_wave", "snow_ice",
    "sand_dust_storm", "glacial_lake_outburst", "multi_hazard", "other",
]
AGENCY_TYPES = ["NMHS", "RSMC", "other_national_agency", "international", "NGO", "media", "other"]
CHANNELS = [
    "sms", "cell_broadcast", "cap", "radio", "tv", "sirens", "community_networks",
    "social_media", "mobile_app", "website", "loudspeakers", "door_to_door", "other",
]
OUTCOMES = ["positive", "mixed", "negative", "not_reported"]
REGIONS = ["I", "II", "III", "IV", "V", "VI"]

_int_or_null = {"type": ["integer", "null"]}
_str_or_null = {"type": ["string", "null"]}

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "relevant", "confidence", "language", "title_en", "hazard", "event_name",
        "event_location", "event_date", "countries", "wmo_regions", "agencies",
        "ew4all_pillars", "lead_time", "lead_time_hours", "dissemination_channels",
        "actions_taken", "outcome", "outcome_justification", "impacts",
        "summary_en", "summary_original",
    ],
    "properties": {
        "relevant": {"type": "boolean"},
        "confidence": {"type": "number"},
        "language": {"type": "string"},
        "title_en": {"type": "string"},
        "hazard": {"type": "string", "enum": HAZARDS},
        "event_name": _str_or_null,
        "event_location": _str_or_null,
        "event_date": _str_or_null,
        "countries": {"type": "array", "items": {"type": "string"}},
        "wmo_regions": {"type": "array", "items": {"type": "string", "enum": REGIONS}},
        "agencies": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "type", "country"],
                "properties": {
                    "name": {"type": "string"},
                    "type": {"type": "string", "enum": AGENCY_TYPES},
                    "country": _str_or_null,
                },
            },
        },
        "ew4all_pillars": {"type": "array", "items": {"type": "integer", "enum": [1, 2, 3, 4]}},
        "lead_time": _str_or_null,
        "lead_time_hours": {"type": ["number", "null"]},
        "dissemination_channels": {"type": "array", "items": {"type": "string", "enum": CHANNELS}},
        "actions_taken": {"type": "array", "items": {"type": "string"}},
        "outcome": {"type": "string", "enum": OUTCOMES},
        "outcome_justification": {"type": "string"},
        "impacts": {
            "type": "object",
            "additionalProperties": False,
            "required": ["deaths", "missing", "injured", "evacuated", "people_reached",
                         "affected", "economic_losses"],
            "properties": {
                "deaths": _int_or_null,
                "missing": _int_or_null,
                "injured": _int_or_null,
                "evacuated": _int_or_null,
                "people_reached": _int_or_null,
                "affected": _int_or_null,
                "economic_losses": _str_or_null,
            },
        },
        "summary_en": {"type": "string"},
        "summary_original": _str_or_null,
    },
}

# (facet name in manifest.json, story field) - names kept stable for the page.
FACETS = [("hazards", "hazards"), ("wmo_regions", "wmo_regions"),
          ("agency_types", "agency_types"), ("pillars", "pillars"),
          ("outcomes", "outcome"), ("languages", "languages")]


def postprocess(data: dict, item: dict) -> dict:
    data["ew4all_pillars"] = sorted(set(data.get("ew4all_pillars", [])))
    return data


# ------------------------------------------------------------------ clustering

_NAME_NOISE = re.compile(
    r"\b(super|severe|major|tropical|typhoon|hurricane|cyclone|cyclonic|storm|depression|"
    r"category \d|post|remnants?|of|the|ex|very|extremely|20\d\d)\b")
HAZARD_GROUP = {"flash_flood": "flood", "heavy_rain": "flood", "storm_surge": "tropical_cyclone",
                "tsunami": "earthquake"}


def category(c: dict) -> str:
    return c["hazard"]


def event_key(c: dict, country_names: dict = None):
    """Named events ("Hurricane Melissa") by name; others by hazard + country + place."""
    name = c.get("event_name")
    if name:
        n = re.sub(r"[^\w ]", " ", normalize(name, fold_accents=True))
        n = " ".join(_NAME_NOISE.sub(" ", n).split())
        if n:
            return f"named|{n}"
    hazard = HAZARD_GROUP.get(c["hazard"], c["hazard"])
    country = (c.get("countries") or ["XX"])[0]
    loc = normalize((c.get("event_location") or "").split(",")[0], fold_accents=True)
    if not loc or loc == normalize((country_names or {}).get(country, "")):
        return None  # too vague (no place, or just the country): use the candidate check
    return f"{hazard}|{country}|{loc}"


def candidate_ok(c: dict, story: dict) -> bool:
    """Same hazard group and (if known) a shared country."""
    group = HAZARD_GROUP.get(c["hazard"], c["hazard"])
    if group not in {HAZARD_GROUP.get(h, h) for h in story["hazards"]}:
        return False
    countries = set(c.get("countries") or [])
    return not countries or bool(countries & {x["code"] for x in story["countries"]})


# -------------------------------------------------------------- story fields

def _merge_outcome(outcomes) -> str:
    seen = {o for o in outcomes if o != "not_reported"}
    if not seen:
        return "not_reported"
    if "mixed" in seen or {"positive", "negative"} <= seen:
        return "mixed"
    return seen.pop()


def _max_impacts(impacts_list) -> dict:
    out = {}
    for imp in impacts_list:
        for k, v in (imp or {}).items():
            if v is None:
                continue
            if isinstance(v, (int, float)):
                out[k] = max(out.get(k, 0), v)
            else:
                out.setdefault(k, v)
    return out


def aggregate(members: list) -> dict:
    """members: list of classification dicts, most confident first."""
    best = members[0]
    agencies = {}
    for c in members:
        for a in c.get("agencies", []):
            agencies.setdefault(normalize(a["name"]), a)
    names = Counter(c["event_name"] for c in members if c.get("event_name"))
    return {
        "event_name": names.most_common(1)[0][0] if names else None,
        "hazard": Counter(c["hazard"] for c in members).most_common(1)[0][0],
        "hazards": sorted({c["hazard"] for c in members}),
        "event_location": best.get("event_location"),
        "agencies": sorted(agencies.values(), key=lambda a: (a["type"], a["name"])),
        "agency_types": sorted({a["type"] for a in agencies.values()}),
        "pillars": sorted({p for c in members for p in c.get("ew4all_pillars", [])}),
        "channels": sorted({ch for c in members for ch in c.get("dissemination_channels", [])}),
        "lead_time": next((c["lead_time"] for c in members if c.get("lead_time")), None),
        "actions": list(dict.fromkeys(a for c in members for a in c.get("actions_taken", [])))[:8],
        "outcome": _merge_outcome(c["outcome"] for c in members),
        "outcome_justification": best.get("outcome_justification"),
        "impacts": _max_impacts(c.get("impacts") for c in members),
    }
