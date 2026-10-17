"""Ask Claude whether an article is about one of a few candidate stories.

Used by cluster.py only when an article has no event-name match but there
are candidate stories (same hazard group, shared country, close in time).
Works across languages because it compares meaning, not words.
"""

import json

from .budget import usage_dict

MATCH_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["story_id"],
    "properties": {"story_id": {"type": ["integer", "null"]}},
}

PROMPT = """Decide whether the ARTICLE reports on the same story as one of the CANDIDATE \
stories, i.e. {what}. Follow-up reports, reactions and investigations count as the same \
story. A different event or development of the same type does not.

ARTICLE:
{article}

CANDIDATES:
{candidates}

Answer with the candidate's story_id, or null if none is the same story."""


def _who(d: dict) -> str:
    """Organisations and named systems/events, which identify a story best."""
    orgs = d.get("institutions") or d.get("agencies") or []
    names = [o.get("short_name") or o["name"] for o in orgs]
    names += d.get("system_names") or ([d["system_name"]] if d.get("system_name") else [])
    if d.get("event_name"):
        names.append(d["event_name"])
    return ", ".join(dict.fromkeys(names))


class StoryMatcher:
    def __init__(self, config, budget, client):
        self.model = config.settings["classify"]["model"]
        self.what = config.module.MATCH_WHAT
        self.budget = budget
        self.client = client

    def match(self, article: dict, candidates: list):
        """article/candidates: dicts with title, summary_en (+ story_id for candidates)."""
        self.budget.check(self.budget.cost(self.model, {"input_tokens": 1500, "output_tokens": 50}))
        cand_text = "\n".join(json.dumps({
            "story_id": c["story_id"], "title": c["title"], "date": c.get("event_date"),
            "who": _who(c), "summary": (c.get("summary_en") or "")[:300]},
            ensure_ascii=False) for c in candidates)
        art_text = json.dumps({"title": article["title_en"], "date": article.get("event_date"),
                               "who": _who(article), "summary": article.get("summary_en", "")[:400]},
                              ensure_ascii=False)
        response = self.client.messages.create(
            model=self.model,
            max_tokens=100,
            thinking={"type": "disabled"},
            output_config={"effort": "low",
                           "format": {"type": "json_schema", "schema": MATCH_SCHEMA}},
            messages=[{"role": "user", "content": PROMPT.format(
                what=self.what, article=art_text, candidates=cand_text)}],
        )
        self.budget.record("cluster", self.model, usage_dict(response.usage))
        text = next((b.text for b in response.content if b.type == "text"), "{}")
        story_id = json.loads(text).get("story_id")
        return story_id if story_id in {c["story_id"] for c in candidates} else None
