import copy
from pathlib import Path

import pytest

from ews_news.config import load_config

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def config():
    return load_config()


@pytest.fixture
def tmp_config(config, tmp_path):
    """Config whose state/ and site data paths point into a temp directory."""
    c = copy.deepcopy(config)
    c.settings["paths"] = {
        "database": str(tmp_path / "ews.sqlite"),
        "db_dump": str(tmp_path / "ews.sql"),
        "run_logs": str(tmp_path / "logs"),
        "site_data": str(tmp_path / "site"),
    }
    return c


def sample_classification(**overrides):
    data = {
        "relevant": True,
        "confidence": 0.9,
        "language": "en",
        "title_en": "Cyclone Dana: Odisha evacuates 1 million people",
        "hazard": "tropical_cyclone",
        "event_name": "Cyclone Dana",
        "event_location": "Odisha, India",
        "event_date": "2026-10-14",
        "countries": ["in"],
        "wmo_regions": [],
        "agencies": [{"name": "India Meteorological Department", "type": "NMHS", "country": "IN"}],
        "ew4all_pillars": [3, 2, 4],
        "lead_time": "72 hours before landfall",
        "lead_time_hours": 72,
        "dissemination_channels": ["sms", "tv"],
        "actions_taken": ["evacuation of 1 million people"],
        "outcome": "positive",
        "outcome_justification": "Large-scale evacuation ahead of landfall.",
        "impacts": {"deaths": None, "missing": None, "injured": None, "evacuated": 1000000,
                    "people_reached": None, "affected": None, "economic_losses": None},
        "summary_en": "IMD issued a red alert 72 hours before landfall and Odisha evacuated 1 million people.",
        "summary_original": None,
    }
    data.update(overrides)
    return data
