"""Load the YAML configuration files in config/.

config/settings.yaml          settings shared by all topics
config/countries.yaml         country -> WMO Regional Association
config/topics/<topic>/        one folder per topic (ews, ai, ...):
    topic.yaml                topic settings (override settings.yaml)
    sources.yaml              sources grouped by language
    languages/<lang>.yaml     keywords and queries per language
"""

import copy
import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


def _load(path: Path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


@dataclass
class Config:
    topic: str                   # e.g. "ews"
    settings: dict               # settings.yaml merged with topic.yaml
    languages: Dict[str, dict]   # language code -> language config
    sources: List[dict]          # flat list, each with a "group" key
    countries: Dict[str, dict]   # ISO2 -> {"name": ..., "region": ...}

    def path(self, key: str) -> Path:
        return ROOT / self.settings["paths"][key]

    def source(self, source_id: str) -> dict:
        for s in self.sources:
            if s["id"] == source_id:
                return s
        raise KeyError(source_id)

    @property
    def module(self):
        """The topic's Python module (ews_news/topics/<module>.py)."""
        return importlib.import_module(f"ews_news.topics.{self.settings['module']}")


def topic_names(config_dir: Path = CONFIG_DIR) -> List[str]:
    return sorted(p.name for p in (config_dir / "topics").iterdir()
                  if (p / "topic.yaml").exists())


def load_config(topic: str = "ews", config_dir: Path = CONFIG_DIR) -> Config:
    tdir = config_dir / "topics" / topic
    settings = _merge(_load(config_dir / "settings.yaml"), _load(tdir / "topic.yaml"))

    # Every file in languages/ is a language; adding fr.yaml adds French.
    languages = {}
    for p in sorted((tdir / "languages").glob("*.yaml")):
        lang = _load(p)
        languages[lang["code"]] = lang

    sources = []
    for group, entries in (_load(tdir / "sources.yaml") or {}).items():
        for s in entries or []:
            s = dict(s)
            s["group"] = group
            s.setdefault("enabled", True)
            s.setdefault("prefilter", "strict")
            sources.append(s)

    countries = {
        code: {"name": v[0], "region": v[1]}
        for code, v in _load(config_dir / "countries.yaml").items()
    }
    return Config(topic, settings, languages, sources, countries)
