"""Topic modules: what is specific to one news page.

Each module (ews.py, ai.py) defines:

    SYSTEM_PROMPT          instructions for Claude (classification and extraction)
    SCHEMA                 JSON schema of Claude's answer (structured output)
    MATCH_WHAT             how the story matcher describes "the same story"
    FACETS                 [(manifest facet name, story field)] for the page's filters
    postprocess(data, item)              topic-specific clean-up of Claude's answer
    category(c)                          main category of an article (stories.hazard column)
    event_key(c, country_names)          clustering key, or None if too vague
    candidate_ok(c, story)               can this article belong to this existing story?
    aggregate(members)                   topic-specific story fields from its articles

Everything else (collection, pre-filter, budget, clustering, publishing) is
shared and lives in the ews_news package.
"""
