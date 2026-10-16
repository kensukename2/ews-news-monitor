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

PROMPT = """Decide whether the ARTICLE reports on the same hazard event as one of the \
CANDIDATE stories (same event, e.g. the same storm, the same earthquake and its warning, \
the same flood episode in the same area). Follow-up reports, reactions and investigations \
about that event count as the same event. A different event of the same type does not.

ARTICLE:
{article}

CANDIDATES:
{candidates}

Answer with the candidate's story_id, or null if none is the same event."""


class StoryMatcher:
    def __init__(self, config, budget, client):
        self.model = config.settings["classify"]["model"]
        self.budget = budget
        self.client = client

    def match(self, article: dict, candidates: list):
        """article/candidates: dicts with title, summary_en (+ story_id for candidates)."""
        self.budget.check(self.budget.cost(self.model, {"input_tokens": 1500, "output_tokens": 50}))
        cand_text = "\n".join(json.dumps({
            "story_id": c["story_id"], "title": c["title"], "date": c.get("event_date"),
            "summary": (c.get("summary_en") or "")[:300]}, ensure_ascii=False) for c in candidates)
        art_text = json.dumps({"title": article["title_en"], "date": article.get("event_date"),
                               "summary": article.get("summary_en", "")[:400]}, ensure_ascii=False)
        response = self.client.messages.create(
            model=self.model,
            max_tokens=100,
            thinking={"type": "disabled"},
            output_config={"effort": "low",
                           "format": {"type": "json_schema", "schema": MATCH_SCHEMA}},
            messages=[{"role": "user", "content": PROMPT.format(article=art_text,
                                                                 candidates=cand_text)}],
        )
        self.budget.record("cluster", self.model, usage_dict(response.usage))
        text = next((b.text for b in response.content if b.type == "text"), "{}")
        story_id = json.loads(text).get("story_id")
        return story_id if story_id in {c["story_id"] for c in candidates} else None
