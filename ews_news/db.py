"""SQLite storage.

The schema uses only TEXT / INTEGER / REAL columns, ISO-8601 UTC timestamps
stored as text and JSON stored as text, so it can be moved to PostgreSQL later
(e.g. with pgloader, or by loading the text dump in state/ews.sql).
"""

import json
import sqlite3
from pathlib import Path
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id              INTEGER PRIMARY KEY,
    url             TEXT NOT NULL,
    url_canonical   TEXT NOT NULL UNIQUE,   -- never collect/classify a URL twice
    title           TEXT NOT NULL,          -- original-language title
    title_key       TEXT NOT NULL,          -- normalised, for duplicate headlines
    source_id       TEXT NOT NULL,          -- id from config/sources.yaml
    source_name     TEXT,                   -- outlet/agency name as published
    language        TEXT NOT NULL,          -- en | ja | es ...
    published_at    TEXT,                   -- ISO-8601 UTC
    collected_at    TEXT NOT NULL,
    snippet         TEXT,                   -- short description only, never full text
    prefilter_score REAL,
    status          TEXT NOT NULL,          -- filtered_out | pending | classified | duplicate | error
    duplicate_of    INTEGER REFERENCES items(id),
    story_id        INTEGER REFERENCES stories(id)
);
CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
CREATE INDEX IF NOT EXISTS idx_items_title_key ON items(title_key);

CREATE TABLE IF NOT EXISTS classifications (
    item_id         INTEGER PRIMARY KEY REFERENCES items(id),
    model           TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    relevant        INTEGER NOT NULL,       -- 0/1
    confidence      REAL NOT NULL,
    review          INTEGER NOT NULL,       -- 1 = low confidence, needs human review
    data_json       TEXT NOT NULL           -- full structured output (see docs/data-schema.md)
);

CREATE TABLE IF NOT EXISTS stories (
    id              INTEGER PRIMARY KEY,
    event_key       TEXT NOT NULL,          -- clustering key, e.g. "tropical_cyclone|typhoon ragasa"
    hazard          TEXT NOT NULL,
    first_seen      TEXT NOT NULL,          -- when the first article was collected
    last_updated    TEXT NOT NULL,
    event_date      TEXT,                   -- earliest article publication date
    data_json       TEXT NOT NULL           -- aggregated story (see docs/data-schema.md)
);
CREATE INDEX IF NOT EXISTS idx_stories_key ON stories(event_key);

CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    status          TEXT NOT NULL,          -- running | success | partial | failed
    stats_json      TEXT
);

CREATE TABLE IF NOT EXISTS source_stats (
    run_id          INTEGER NOT NULL REFERENCES runs(id),
    source_id       TEXT NOT NULL,
    language        TEXT NOT NULL,
    fetched         INTEGER NOT NULL DEFAULT 0,
    new_items       INTEGER NOT NULL DEFAULT 0,
    kept            INTEGER NOT NULL DEFAULT 0,  -- passed the pre-filter
    error           TEXT,
    PRIMARY KEY (run_id, source_id)
);

CREATE TABLE IF NOT EXISTS api_usage (
    id              INTEGER PRIMARY KEY,
    ts              TEXT NOT NULL,
    run_id          INTEGER,
    purpose         TEXT NOT NULL,          -- classify | web_search
    model           TEXT NOT NULL,
    input_tokens    INTEGER NOT NULL DEFAULT 0,
    output_tokens   INTEGER NOT NULL DEFAULT 0,
    cache_read      INTEGER NOT NULL DEFAULT 0,
    cache_write     INTEGER NOT NULL DEFAULT 0,
    web_searches    INTEGER NOT NULL DEFAULT 0,
    cost_usd        REAL NOT NULL
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def dump(conn: sqlite3.Connection, path: Path) -> None:
    """Write a text SQL dump (diffs well in git, loads into PostgreSQL with minor edits)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for line in conn.iterdump():
            f.write(line + "\n")


def restore(db_path: Path, dump_path: Path) -> bool:
    """Rebuild the SQLite file from a text dump if the file is missing."""
    if db_path.exists() or not dump_path.exists():
        return False
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    with open(dump_path, encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.close()
    return True


def loads(text: Optional[str]):
    return json.loads(text) if text else None


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)
