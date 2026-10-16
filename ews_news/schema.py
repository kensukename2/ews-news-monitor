"""JSON schema for the classification output, plus a small validator.

The same schema is sent to the Claude API (structured outputs), so the model
is constrained to produce exactly this shape. `validate()` re-checks it
locally (and is used by the tests).
"""

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

CLASSIFICATION_SCHEMA = {
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

_PY_TYPES = {
    "object": dict, "array": list, "string": str, "boolean": bool,
    "integer": int, "number": (int, float), "null": type(None),
}


def _type_ok(value, t: str) -> bool:
    if t in ("integer", "number") and isinstance(value, bool):
        return False
    return isinstance(value, _PY_TYPES[t])


def validate(value, schema: dict = CLASSIFICATION_SCHEMA, path: str = "$") -> list:
    """Return a list of error strings (empty list = valid)."""
    errors = []
    types = schema.get("type")
    if types:
        types = types if isinstance(types, list) else [types]
        if not any(_type_ok(value, t) for t in types):
            return [f"{path}: expected {types}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} not in {schema['enum']}")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing '{key}'")
        for key, v in value.items():
            if key in props:
                errors.extend(validate(v, props[key], f"{path}.{key}"))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: unexpected '{key}'")
    if isinstance(value, list) and "items" in schema:
        for i, v in enumerate(value):
            errors.extend(validate(v, schema["items"], f"{path}[{i}]"))
    return errors
