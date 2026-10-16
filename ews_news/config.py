"""Load the YAML configuration files in config/."""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


def _load(path: Path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@dataclass
class Config:
    settings: dict
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


def load_config(config_dir: Path = CONFIG_DIR) -> Config:
    settings = _load(config_dir / "settings.yaml")

    # Every file in config/languages/ is a language; adding fr.yaml adds French.
    languages = {}
    for p in sorted((config_dir / "languages").glob("*.yaml")):
        lang = _load(p)
        languages[lang["code"]] = lang

    sources = []
    for group, entries in (_load(config_dir / "sources.yaml") or {}).items():
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
    return Config(settings, languages, sources, countries)
