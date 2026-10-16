"""Cheap keyword pre-filter, configured per language in config/languages/*.yaml.

Matching is substring matching on NFKC-normalised, lower-cased text, so it
works for Japanese (no spaces between words). For languages with
`word_boundaries: true`, each term must start and end at a word boundary.
"""

import re
from functools import lru_cache

from .textutil import normalize


@lru_cache(maxsize=None)
def _pattern(term: str, word_boundaries: bool, fold_accents: bool):
    t = re.escape(normalize(term, fold_accents))
    if word_boundaries:
        t = rf"(?<!\w){t}(?!\w)"
    return re.compile(t)


def hits(text: str, terms, lang: dict) -> list:
    """Return the terms found in text (text is normalised here)."""
    text = normalize(text, bool(lang.get("fold_accents")))
    return _hits(text, terms, lang)


def _hits(text: str, terms, lang: dict) -> list:
    wb = bool(lang.get("word_boundaries"))
    fa = bool(lang.get("fold_accents"))
    return [t for t in terms or [] if _pattern(t, wb, fa).search(text)]


def score(title: str, snippet: str, lang: dict, mode: str = "strict"):
    """Return (keep: bool, score: float, reason: str)."""
    rules = lang.get("prefilter", {})
    text = normalize(f"{title} {snippet}", bool(lang.get("fold_accents")))

    excluded = _hits(text, rules.get("exclude_terms"), lang)
    if excluded:
        return False, 0.0, f"exclude:{excluded[0]}"

    strong = _hits(text, rules.get("strong_terms"), lang)
    warning = _hits(text, rules.get("warning_terms"), lang)
    hazard = _hits(text, rules.get("hazard_terms"), lang)
    s = 2.0 * len(strong) + len(warning) + len(hazard)

    if mode == "relaxed":
        return True, s, "relaxed"
    if mode == "loose":
        # The source already matched our keywords somewhere in the full text
        # (e.g. GDELT); require at least one term in the title/snippet too.
        return bool(strong or warning or hazard), s, "loose"
    if strong:
        return True, s, f"strong:{strong[0]}"
    if warning and hazard:
        return True, s, f"warning+hazard:{warning[0]}+{hazard[0]}"
    return False, s, "no match"
