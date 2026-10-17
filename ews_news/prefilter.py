"""Cheap keyword pre-filter, configured per language in
config/topics/<topic>/languages/*.yaml.

Matching is substring matching on NFKC-normalised, lower-cased text, so it
works for Japanese (no spaces between words). For languages with
`word_boundaries: true`, each term must start and end at a word boundary.
Terms made only of Latin letters/digits (e.g. "AI", "CAP") always need
boundaries, so "ai" never matches inside "mail" even in Japanese text.

Rules (under `prefilter:` in the language file):
  strong_terms      any one of these is enough
  exclude_terms     any one of these drops the item
  require_groups    names of term lists that must ALL match (strict mode);
                    default [warning_terms, hazard_terms]
  loose_groups      term lists of which ANY must match in loose mode;
                    default: the require_groups
"""

import re
from functools import lru_cache

from .textutil import normalize

_ASCII_WORD = re.compile(r"^[a-z0-9][a-z0-9 .\-]*$")


@lru_cache(maxsize=None)
def _pattern(term: str, word_boundaries: bool, fold_accents: bool):
    norm = normalize(term, fold_accents)
    t = re.escape(norm)
    if word_boundaries:
        t = rf"(?<!\w){t}(?!\w)"
    elif _ASCII_WORD.match(norm):
        t = rf"(?<![a-z0-9]){t}(?![a-z0-9])"
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

    require = rules.get("require_groups", ["warning_terms", "hazard_terms"])
    loose = rules.get("loose_groups", require)
    strong = _hits(text, rules.get("strong_terms"), lang)
    found = {g: _hits(text, rules.get(g), lang) for g in set(require) | set(loose)}
    s = 2.0 * len(strong) + sum(len(v) for v in found.values())

    if mode == "relaxed":
        return True, s, "relaxed"
    if mode == "loose":
        # The source already matched our keywords somewhere in the full text
        # (e.g. GDELT); require at least one term in the title/snippet too.
        return bool(strong or any(found[g] for g in loose)), s, "loose"
    if strong:
        return True, s, f"strong:{strong[0]}"
    if all(found[g] for g in require):
        return True, s, "+".join(found[g][0] for g in require)
    return False, s, "no match"
