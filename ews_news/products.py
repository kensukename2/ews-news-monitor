"""AI products and services: weekly assessment of implementation status.

Built from the AI News archive (topic "ai"):

  1. analyse_articles()  For every relevant AI article not analysed before,
                         Claude lists the AI products/services it mentions,
                         who developed them, and which institutions use them
                         at which stage. Each article is analysed only once.
  2. aggregate()         Merges the mentions per product (names normalised,
                         plus config/topics/ai/product_aliases.yaml) and
                         computes the status (rule below). No API calls.
                         Unnamed products that appear only in news stories
                         about one named product of a compatible developer
                         are merged into it.
     dedupe()            One short Claude call (only when the product list
                         changed) finds remaining duplicates, e.g. an
                         "(unnamed)" description of a named product.
  3. assess()            Claude writes a 2-3 sentence assessment per product,
                         only for products with new evidence.
  4. write_json()        site/ai_news/products/data/products.json for the page
                         (also rewritten by every normal publish, without API calls).

Status (most advanced stage wins; "eligible" = NMHS or WMO centre such as
ECMWF, an RSMC, a Regional Climate Centre or EUMETSAT):
  1 operational   used in routine operations at an eligible institution
  2 testing       tested / pre-operational at an eligible institution
  4 released      released or used by others, not yet tested at an eligible institution
  3 research      research / prototype with real-world potential
Run weekly:  python -m ews_news --topic ai products
"""

import hashlib
import json
import logging
import re
from collections import Counter
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import yaml

from . import db
from .budget import BudgetExceeded, usage_dict
from .config import ROOT
from .schema import validate
from .textutil import normalize, utcnow

log = logging.getLogger("ews_news")

ELIGIBLE = {"NMHS", "WMO_centre"}
STAGES = ["operational", "testing", "research", "planned"]
STATUS_LABELS = {
    1: "Fully operational at NMHS / WMO centre",
    2: "Testing / pre-operational",
    3: "Research phase with real-world potential",
    4: "Released, not yet tested or implemented at NMHS",
}
INSTITUTION_TYPES = ["NMHS", "WMO_centre", "WMO", "national_ews_agency", "other_national_agency",
                     "international", "research", "private_sector", "NGO", "other"]
PRODUCT_TYPES = ["weather_model", "nowcasting", "subseasonal_climate", "post_processing",
                 "observation_data", "hydrology_flood", "warning_impact", "decision_support",
                 "llm_assistant", "other"]
AREAS = ["observations", "data_processing", "forecasting", "guidance", "hydrology",
         "warnings", "communication", "climate_services", "operations", "other"]

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["products"],
    "properties": {"products": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["name", "aliases", "is_named", "developer", "developer_type",
                     "product_type", "areas", "maturity", "deployments"],
        "properties": {
            "name": {"type": "string"},
            "aliases": {"type": "array", "items": {"type": "string"}},
            "is_named": {"type": "boolean"},
            "developer": {"type": ["string", "null"]},
            "developer_type": {"type": "string", "enum": INSTITUTION_TYPES},
            "product_type": {"type": "string", "enum": PRODUCT_TYPES},
            "areas": {"type": "array", "items": {"type": "string", "enum": AREAS}},
            "maturity": {"type": "string", "enum": ["released", "research"]},
            "deployments": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "required": ["institution", "short_name", "type", "country", "stage"],
                "properties": {
                    "institution": {"type": "string"},
                    "short_name": {"type": ["string", "null"]},
                    "type": {"type": "string", "enum": INSTITUTION_TYPES},
                    "country": {"type": ["string", "null"]},
                    "stage": {"type": "string", "enum": STAGES},
                }}},
        }}}},
}

ARTICLE_PROMPT = """You assess the implementation status of AI products and services used for \
weather, water, climate and early warning work (by National Meteorological and Hydrological \
Services (NMHSs), WMO centres such as ECMWF, RSMCs, Regional Climate Centres and EUMETSAT, \
and others).

For the news article given by the user, list each distinct AI product or service it \
reports on (a model, system, tool or service), with:
- name: the product's name as commonly known (e.g. "AIFS", "GraphCast", "WeatherNext", \
"Aurora", "MAZU"). If it has no name, a short descriptive English name ending in \
"(unnamed)", e.g. "CMA AI medium-range forecasting system (unnamed)". Treat versions and \
variants of one product line as the same product (e.g. "AIFS Single" and "AIFS ENS" are \
"AIFS"; "WeatherNext 2" is "WeatherNext") and put the variant in aliases. If the product \
is in the KNOWN PRODUCTS list, use that exact name.
- aliases: other names/acronyms used in the article (may be empty).
- is_named: false for "(unnamed)" products.
- developer, developer_type: who built it.
- product_type: weather_model (global/regional AI weather model), nowcasting, \
subseasonal_climate, post_processing (bias correction, downscaling, ensembles), \
observation_data (observations, data processing, QC), hydrology_flood, warning_impact \
(impact-based forecasting, warning generation), decision_support, llm_assistant \
(chatbots, LLM tools), other.
- areas: areas of operation (observations, data_processing, forecasting, guidance, \
hydrology, warnings, communication, climate_services, operations, other).
- maturity: "released" if it has been launched, made available to users or customers, \
open-sourced, or is used in operations by anyone; otherwise "research" (paper, \
prototype, experiment).
- deployments: institutions that use or test it (including the developer if it runs it \
itself), each with stage: operational (in routine operational service), testing (trial, \
pilot, pre-operational, evaluation, parallel run), research (used only in research), \
planned (announced plan or agreement, not yet in use). Institution types: NMHS = national \
meteorological/hydrological service; WMO_centre = ECMWF, RSMCs, Regional Climate \
Centres, World Meteorological Centres, EUMETSAT and similar operational centres; WMO = \
WMO Secretariat; national_ews_agency = civil protection / disaster management; \
other_national_agency; international; research; private_sector; NGO; other. \
country: ISO 3166-1 alpha-2 or null.

Only report what the article states; do not add knowledge from elsewhere. If the article \
is not about a specific AI product or service (e.g. a general conference or policy \
discussion), return an empty list."""

ASSESS_PROMPT = """Write a short assessment (2-3 sentences, at most 60 words, English) of the \
implementation status of the AI product below, for WMO staff following AI in National \
Meteorological and Hydrological Services (NMHSs). Say who uses or tests it and since when \
(from the evidence dates), and what is still missing for operational use at NMHSs, if \
anything. Use only the evidence given. Its status is: {status_label}. Do not contradict \
it and do not explain how the status was determined.
Also give status_basis: at most 12 words, e.g. "Operational at ECMWF since Oct 2026" or \
"Tested by KMSA and ECMWF; no operational use yet".

PRODUCT:
{product}

EVIDENCE (newest first):
{evidence}"""

DEDUPE_PROMPT = """Below is a list of AI products/services found in news articles about \
weather, water, climate and early warning. Different articles often describe the same \
product differently: by acronym or full name, with a version number, or as an \
"(unnamed)" description. Group the entries that refer to the same product. Entries with \
the same (or no stated) developer that describe the same tool for the same purpose, as \
shown by "about" and "titles", are the same product, even if one is unnamed. Entries \
that share a news story number in "stories" come from articles about the same news and \
are very likely the same product. Do not group different products of the same \
developer (e.g. a dataset and a separate forecasting model). For each group give the canonical name \
(prefer the named entry, without "(unnamed)") and the member names exactly as listed. \
Return an empty list if there are no duplicates.

PRODUCTS:
{products}"""

DEDUPE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["groups"],
    "properties": {"groups": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["canonical", "members"],
        "properties": {"canonical": {"type": "string"},
                       "members": {"type": "array", "items": {"type": "string"}}}}}},
}

ASSESS_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["assessment", "status_basis"],
    "properties": {"assessment": {"type": "string"},
                   "status_basis": {"type": "string"}},   # one short phrase
}


# ------------------------------------------------------------------- storage

TABLES = """
CREATE TABLE IF NOT EXISTS product_mentions (
    item_id     INTEGER PRIMARY KEY REFERENCES items(id),  -- one analysis per article
    analysed_at TEXT NOT NULL,
    data_json   TEXT NOT NULL                              -- {"products": [...]}
);
CREATE TABLE IF NOT EXISTS product_merges (
    variant_key TEXT PRIMARY KEY,                         -- name found by the weekly dedupe step
    canonical   TEXT NOT NULL,
    decided_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
    key           TEXT PRIMARY KEY,                       -- normalised product name
    updated_at    TEXT NOT NULL,
    evidence_hash TEXT NOT NULL,                          -- assessment is redone when this changes
    data_json     TEXT NOT NULL
);
"""


def ensure_tables(conn):
    conn.executescript(TABLES)


def _key(name: str) -> str:
    n = normalize(re.sub(r"\(unnamed\)", "", name or ""), fold_accents=True)
    return " ".join(re.sub(r"[^\w ]", " ", n).split())


def load_aliases(config, conn=None) -> dict:
    """{variant key: canonical name}, from the weekly dedupe step (database) and from
    config/topics/ai/product_aliases.yaml (which wins, so you can correct merges)."""
    out = {}
    if conn is not None:
        for r in conn.execute("SELECT variant_key, canonical FROM product_merges"):
            out[r[0]] = r[1]
    path = ROOT / "config" / "topics" / config.topic / "product_aliases.yaml"
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for canonical, variants in data.items():
            for v in [canonical] + list(variants or []):
                out[_key(v)] = canonical
    return out


# --------------------------------------------------------- 1. article analysis

def _known_products(conn, limit=150) -> list:
    rows = conn.execute("SELECT data_json FROM products ORDER BY updated_at DESC LIMIT ?", (limit,))
    return [f"{p['name']} ({p.get('developer') or '?'})" for p in (db.loads(r[0]) for r in rows)]


def articles_to_analyse(conn) -> list:
    return conn.execute(
        "SELECT i.id, i.title, i.url, i.source_name, i.published_at, c.data_json FROM items i "
        "JOIN classifications c ON c.item_id = i.id "
        "LEFT JOIN product_mentions m ON m.item_id = i.id "
        "WHERE c.relevant = 1 AND c.model != 'copied' AND m.item_id IS NULL "
        "ORDER BY i.published_at").fetchall()


def analyse_articles(conn, config, budget, client, limit=200) -> dict:
    model = config.settings["classify"]["model"]
    out = {"analysed": 0, "errors": 0, "budget_stopped": False}
    for row in articles_to_analyse(conn)[:limit]:
        c = db.loads(row["data_json"])
        article = {
            "title_en": c.get("title_en"), "original_title": row["title"],
            "date": (row["published_at"] or "")[:10], "source": row["source_name"],
            "summary": c.get("summary_en"), "ai_system_named": c.get("system_name"),
            "institutions": c.get("institutions"), "news_type": c.get("news_type"),
            "areas": c.get("areas"),
        }
        known = _known_products(conn)
        try:
            budget.check(budget.cost(model, {"input_tokens": 4000, "output_tokens": 800}))
            r = client.messages.create(
                model=model, max_tokens=1500, thinking={"type": "disabled"},
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
                system=[{"type": "text", "text": ARTICLE_PROMPT, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content":
                           "KNOWN PRODUCTS:\n" + ("\n".join(known) or "(none yet)") +
                           "\n\nARTICLE:\n" + json.dumps(article, ensure_ascii=False)}])
            budget.record("products", model, usage_dict(r.usage))
            data = json.loads(next(b.text for b in r.content if b.type == "text"))
            errors = validate(data, SCHEMA)
            if errors:
                raise ValueError(errors[:2])
        except BudgetExceeded as e:
            log.warning("products: %s", e)
            out["budget_stopped"] = True
            break
        except Exception as e:
            log.warning("products: article %s failed: %s", row["id"], e)
            out["errors"] += 1
            continue
        conn.execute("INSERT OR REPLACE INTO product_mentions (item_id, analysed_at, data_json) VALUES (?,?,?)",
                     (row["id"], utcnow(), db.dumps(data)))
        conn.commit()
        out["analysed"] += 1
        # New products become "known" right away, so later articles reuse their names.
        aggregate(conn, config)
    return out


# ------------------------------------------------------------- 2. aggregation

def status_of(product: dict) -> int:
    deps = product["deployments"]
    if any(d["eligible"] and d["stage"] == "operational" for d in deps):
        return 1
    if any(d["eligible"] and d["stage"] == "testing" for d in deps):
        return 2
    if product["released"] or any(d["stage"] in ("operational", "testing") for d in deps):
        return 4
    return 3


_STAGE_RANK = {"operational": 3, "testing": 2, "planned": 1, "research": 0}
_PLACEHOLDER = re.compile(r"not specified|unspecified|not stated|unknown|unnamed|^n/?a$|^none$|various|"
                          r"^researchers\b|^research team\b", re.I)


def _dev_words(dev) -> set:
    stop = {"of", "and", "the", "for", "inc", "ltd", "university", "institute", "national"}
    return {w for w in _key(dev or "").split() if len(w) > 2 and w not in stop}


def _unnamed_into_named(products: dict) -> dict:
    """{unnamed key: named canonical name} for unnamed products whose articles all
    belong to news stories that mention exactly one named product with a compatible
    developer (same words in the name, or no developer stated)."""
    out = {}
    stories = {k: {x["story_id"] for x in e["evidence"] if x["story_id"]} for k, e in products.items()}
    for k, e in products.items():
        if e["named"] or not stories[k]:
            continue
        dev = _dev_words(e["developers"].most_common(1)[0][0] if e["developers"] else None)
        candidates = []
        for k2, e2 in products.items():
            if k2 == k or not e2["named"] or not stories[k] <= stories[k2]:
                continue
            dev2 = set().union(*(_dev_words(d) for d in e2["developers"])) if e2["developers"] else set()
            if not dev or not dev2 or dev & dev2:
                candidates.append(e2["canonical"] or e2["names"].most_common(1)[0][0])
        if len(candidates) == 1:
            out[k] = candidates[0]
    return out


def aggregate(conn, config, _story_merges=None) -> int:
    """Rebuild the products table from all mentions (no API calls)."""
    aliases = load_aliases(config, conn)
    aliases.update(_story_merges or {})
    rows = conn.execute(
        "SELECT m.item_id, m.data_json AS mentions, i.title, i.url, i.source_name, i.language, "
        "i.published_at, i.collected_at, i.story_id, c.data_json AS cls FROM product_mentions m "
        "JOIN items i ON i.id = m.item_id JOIN classifications c ON c.item_id = i.id").fetchall()
    # Story ids of duplicate articles: their sources count as evidence too.
    products = {}
    alias_to_key = {}
    for r in rows:
        cls = db.loads(r["cls"])
        for p in db.loads(r["mentions"])["products"]:
            canonical = aliases.get(_key(p["name"]))
            key = _key(canonical or p["name"])
            for a in p.get("aliases") or []:
                if _key(a) in alias_to_key and not canonical:
                    key = alias_to_key[_key(a)]
                    break
            if not key:
                continue
            for a in [p["name"]] + list(p.get("aliases") or []):
                alias_to_key.setdefault(_key(a), key)
            e = products.setdefault(key, {
                "key": key, "names": Counter(), "aliases": set(), "named": False,
                "developers": Counter(), "developer_types": Counter(), "types": Counter(),
                "areas": Counter(), "released": False, "deployments": {}, "evidence": [],
                "canonical": canonical})
            e["names"][p["name"]] += 1
            e["aliases"].update(a for a in p.get("aliases") or [] if a)
            e["named"] |= bool(p["is_named"])
            if p.get("developer"):
                e["developers"][p["developer"]] += 1
            e["developer_types"][p["developer_type"]] += 1
            e["types"][p["product_type"]] += 1
            e["areas"].update(p.get("areas") or [])
            e["released"] |= p["maturity"] == "released"
            date = (r["published_at"] or r["collected_at"])[:10]
            for d in p.get("deployments") or []:
                if _PLACEHOLDER.search(d["institution"] or "") or not (d["institution"] or "").strip():
                    continue
                dk = _key(d.get("short_name") or d["institution"])
                old = e["deployments"].get(dk)
                if not old or _STAGE_RANK[d["stage"]] > _STAGE_RANK[old["stage"]] or \
                        (d["stage"] == old["stage"] and date > old["last_date"]):
                    e["deployments"][dk] = {
                        "institution": d["institution"], "short_name": d.get("short_name"),
                        "type": d["type"], "country": (d.get("country") or "").upper() or None,
                        "stage": d["stage"], "eligible": d["type"] in ELIGIBLE,
                        "last_date": max(date, old["last_date"]) if old else date}
            e["evidence"].append({
                "item_id": r["item_id"], "story_id": r["story_id"], "date": date,
                "title": r["title"], "title_en": cls.get("title_en"), "url": r["url"],
                "source_name": r["source_name"], "language": r["language"],
                "summary_en": cls.get("summary_en")})

    if _story_merges is None:
        merges = _unnamed_into_named(products)
        if merges:
            return aggregate(conn, config, merges)

    now = utcnow()
    old = {r["key"]: db.loads(r["data_json"]) for r in conn.execute("SELECT key, data_json FROM products")}
    conn.execute("DELETE FROM products")
    for key, e in products.items():
        name = e["canonical"] or e["names"].most_common(1)[0][0]
        deployments = sorted(e["deployments"].values(),
                             key=lambda d: (-_STAGE_RANK[d["stage"]], not d["eligible"], d["institution"]))
        evidence = sorted(e["evidence"], key=lambda x: x["date"], reverse=True)
        data = {
            "key": key, "name": name,
            "aliases": sorted(a for a in e["aliases"] | set(e["names"]) if a != name),
            "is_named": e["named"],
            "developer": e["developers"].most_common(1)[0][0] if e["developers"] else None,
            "developer_type": e["developer_types"].most_common(1)[0][0],
            "product_type": e["types"].most_common(1)[0][0],
            "areas": [a for a in AREAS if a in e["areas"]],
            "released": e["released"],
            "deployments": deployments,
            "first_mentioned": evidence[-1]["date"], "last_mentioned": evidence[0]["date"],
            "n_articles": len(evidence),
            "evidence": evidence,
        }
        data["status"] = status_of(data)
        data["status_label"] = STATUS_LABELS[data["status"]]
        ehash = hashlib.sha1(db.dumps([x["item_id"] for x in evidence]).encode()).hexdigest()
        prev = old.get(key, {})
        if prev.get("evidence_hash") == ehash:          # keep the existing assessment
            data["assessment"] = prev.get("assessment")
            data["status_basis"] = prev.get("status_basis")
            data["assessed_at"] = prev.get("assessed_at")
        data["evidence_hash"] = ehash
        conn.execute("INSERT INTO products (key, updated_at, evidence_hash, data_json) VALUES (?,?,?,?)",
                     (key, now, ehash, db.dumps(data)))
    conn.commit()
    return len(products)


# ------------------------------------------------------------ 2b. duplicates

def dedupe(conn, config, budget, client) -> int:
    """Ask Claude which products are the same; remember the merges. Runs only when the
    product list changed since the last check. Returns the number of new merges."""
    rows = [db.loads(r[0]) for r in conn.execute("SELECT data_json FROM products ORDER BY key")]
    if len(rows) < 2:
        return 0
    keys_hash = hashlib.sha1(",".join(p["key"] for p in rows).encode()).hexdigest()
    conn.execute("CREATE TABLE IF NOT EXISTS product_state (name TEXT PRIMARY KEY, value TEXT)")
    last = conn.execute("SELECT value FROM product_state WHERE name = 'dedupe_hash'").fetchone()
    if last and last[0] == keys_hash:
        return 0
    model = config.settings["classify"]["model"]
    listing = "\n".join(json.dumps({"name": p["name"], "developer": p.get("developer"),
                                    "type": p["product_type"],
                                    "about": (p["evidence"][0].get("summary_en") or "")[:200],
                                    "titles": [x["title_en"] or x["title"] for x in p["evidence"][:3]],
                                    "stories": sorted({x["story_id"] for x in p["evidence"] if x["story_id"]})},
                                   ensure_ascii=False) for p in rows)
    budget.check(budget.cost(model, {"input_tokens": 200 + 80 * len(rows), "output_tokens": 600}))
    r = client.messages.create(
        model=model, max_tokens=2000, thinking={"type": "disabled"},
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": DEDUPE_SCHEMA}},
        messages=[{"role": "user", "content": DEDUPE_PROMPT.format(products=listing)}])
    budget.record("products", model, usage_dict(r.usage))
    groups = json.loads(next(b.text for b in r.content if b.type == "text"))["groups"]
    names = {p["name"] for p in rows}
    merges = 0
    for g in groups:
        members = [m for m in g["members"] if m in names]
        if len(members) < 2:
            continue
        for m in members:
            if _key(m) != _key(g["canonical"]):
                conn.execute("INSERT OR REPLACE INTO product_merges (variant_key, canonical, decided_at)"
                             " VALUES (?,?,?)", (_key(m), g["canonical"], utcnow()))
                merges += 1
    conn.execute("INSERT OR REPLACE INTO product_state (name, value) VALUES ('dedupe_hash', ?)", (keys_hash,))
    conn.commit()
    return merges


# --------------------------------------------------------------- 3. assessment

def assess(conn, config, budget, client) -> dict:
    """Write assessments for products without one (new or with new evidence)."""
    model = config.settings["classify"]["model"]
    out = {"assessed": 0, "errors": 0, "budget_stopped": False}
    for r in conn.execute("SELECT key, data_json FROM products").fetchall():
        p = db.loads(r["data_json"])
        if p.get("assessment"):
            continue
        product = {k: p[k] for k in ("name", "aliases", "developer", "developer_type",
                                     "product_type", "areas", "released")}
        product["deployments"] = [{k: d[k] for k in ("institution", "type", "country", "stage", "last_date")}
                                  for d in p["deployments"]]
        evidence = "\n".join(json.dumps({"date": x["date"], "title": x["title_en"] or x["title"],
                                         "source": x["source_name"], "summary": x["summary_en"]},
                                        ensure_ascii=False) for x in p["evidence"][:10])
        try:
            budget.check(budget.cost(model, {"input_tokens": 3000, "output_tokens": 300}))
            resp = client.messages.create(
                model=model, max_tokens=500, thinking={"type": "disabled"},
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": ASSESS_SCHEMA}},
                messages=[{"role": "user", "content": ASSESS_PROMPT.format(
                    status_label=p["status_label"], product=json.dumps(product, ensure_ascii=False),
                    evidence=evidence)}])
            budget.record("products", model, usage_dict(resp.usage))
            a = json.loads(next(b.text for b in resp.content if b.type == "text"))
        except BudgetExceeded as e:
            log.warning("products: %s", e)
            out["budget_stopped"] = True
            break
        except Exception as e:
            log.warning("products: assessment of %s failed: %s", p["name"], e)
            out["errors"] += 1
            continue
        p.update(assessment=a["assessment"], status_basis=a["status_basis"], assessed_at=utcnow())
        conn.execute("UPDATE products SET data_json = ? WHERE key = ?", (db.dumps(p), r["key"]))
        conn.commit()
        out["assessed"] += 1
    return out


# ------------------------------------------------------------------ 4. output

def write_json(conn, config) -> dict:
    """site/ai_news/products/data/products.json (see docs/data-schema.md)."""
    ensure_tables(conn)
    tz = config.settings["timezone"]
    products = [db.loads(r[0]) for r in conn.execute("SELECT data_json FROM products")]
    for p in products:
        p.pop("evidence_hash", None)
        for x in p["evidence"]:
            x.pop("item_id", None)
    products.sort(key=lambda p: (p["status"] if p["status"] != 4 else 2.5, -p["n_articles"],
                                 p["name"].lower()))
    last = conn.execute("SELECT MAX(analysed_at) FROM product_mentions").fetchone()[0]
    now = datetime.now(timezone.utc)
    out = {
        "schema_version": 1,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "last_assessed_at": last,
        "last_assessed_local": (datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                                .astimezone(ZoneInfo(tz)).isoformat(timespec="minutes") if last else None),
        "articles_analysed": conn.execute("SELECT COUNT(*) FROM product_mentions").fetchone()[0],
        "status_labels": STATUS_LABELS,
        "counts": {s: sum(1 for p in products if p["status"] == s) for s in STATUS_LABELS},
        "products": products,
    }
    path = config.path("site_data").parent / "products" / "data" / "products.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)
    return out


def run_weekly(conn, config, budget, client, limit=200) -> dict:
    ensure_tables(conn)
    stats = {"analysis": analyse_articles(conn, config, budget, client, limit) if client else
             {"analysed": 0, "skipped": "ANTHROPIC_API_KEY not set"}}
    stats["products"] = aggregate(conn, config)
    if client:
        try:
            stats["merged"] = dedupe(conn, config, budget, client)
        except Exception as e:  # budget or API problem: try again next week
            log.warning("products: duplicate check failed: %s", e)
            stats["merged"] = 0
        if stats["merged"]:
            stats["products"] = aggregate(conn, config)
    stats["assessment"] = assess(conn, config, budget, client) if client else {}
    out = write_json(conn, config)
    stats["counts"] = out["counts"]
    stats["cost_usd"] = round(budget.spent_run, 4)
    return stats
