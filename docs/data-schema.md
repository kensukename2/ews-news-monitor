# Data schema

The web page (`site/ews_news/`) only reads static JSON files from
`site/ews_news/data/`. You can redesign the page freely as long as it reads
these files. All text is UTF-8; all timestamps are ISO-8601 in UTC
(`2026-10-16T03:05:00Z`) unless the field name ends in `_local`.

## `manifest.json`

Read first. Small; tells the page what exists and whether the last update worked.

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | int | Currently `1`. Increased if fields change incompatibly. |
| `generated_at` | string | When these files were written (UTC). |
| `generated_at_local` | string | Same, in Europe/Zurich, e.g. `2026-10-16T05:12+02:00`. |
| `timezone` | string | `Europe/Zurich`. |
| `today_local` | string | Date (Zurich) used to mark stories as new, `YYYY-MM-DD`. |
| `stale_after_hours` | int | Show a warning if `generated_at` is older than this. |
| `last_run` | object or null | The latest pipeline run (see below). |
| `last_success_at` | string or null | End time of the latest run with status `success` or `partial`. Show as "Last updated". |
| `total_stories` | int | Number of stories in all monthly files. |
| `new_today` | int | Stories first seen today (Zurich date). |
| `months` | list | `{"month": "2026-10", "file": "stories-2026-10.json", "count": 33}`, newest first. |
| `facets` | object | Values present in the data, for filter menus: `hazards`, `wmo_regions`, `agency_types`, `pillars`, `outcomes`, `languages` (lists of strings/ints) and `countries` (list of `{code, name}`). |

`last_run`:

| Field | Meaning |
|---|---|
| `status` | `success`, `partial` (some sources failed), `failed`, or `running`. |
| `started_at`, `finished_at` | UTC timestamps. |
| `stats.sources` | One entry per source: `source_id`, `language`, `fetched`, `new_items`, `kept` (passed the pre-filter), `error` (null or message). |
| `stats.by_language` | Totals per language: `fetched`, `new_items`, `kept`, `errors`. |
| `stats.classify` | `classified`, `relevant`, `review`, `errors`, `copied` (duplicate headlines), `left_pending`, `budget_stopped`, `cost_usd`. |
| `stats.budget` | `spent_month_usd`, `allowance_run_usd`, `monthly_usd`. |
| `stats.fatal_error` | Message if the whole run crashed. |

## `stories-YYYY-MM.json`

```json
{"schema_version": 1, "month": "2026-10", "stories": [ ... ]}
```

Stories are grouped by the month they were **first seen** and sorted by
`last_updated`, newest first. Each story:

| Field | Type | Meaning |
|---|---|---|
| `id` | int | Story id (stable unless you run `recluster`). |
| `event_name` | string or null | Standard English name, e.g. `Hurricane Melissa`. |
| `title` | string | English headline of the most confident article. |
| `hazard` | string | Main hazard (see list below). |
| `hazards` | list | All hazards mentioned by its articles. |
| `event_location` | string or null | e.g. `Piura, Peru`. |
| `event_date` | string | `YYYY-MM-DD`, earliest article date. Used by the date filter. |
| `countries` | list | `{"code": "PE", "name": "Peru"}` (ISO 3166-1 alpha-2). |
| `wmo_regions` | list | `I`…`VI` (WMO Regional Associations). |
| `agencies` | list | `{"name", "type", "country"}`; `type` is one of `NMHS`, `RSMC`, `other_national_agency`, `international`, `NGO`, `media`, `other`. |
| `agency_types` | list | Distinct `type` values above. |
| `pillars` | list of int | Early Warnings for All pillars: 1 risk knowledge; 2 detection, monitoring, analysis and forecasting; 3 warning dissemination and communication; 4 preparedness and response. |
| `channels` | list | Dissemination channels: `sms`, `cell_broadcast`, `cap`, `radio`, `tv`, `sirens`, `community_networks`, `social_media`, `mobile_app`, `website`, `loudspeakers`, `door_to_door`, `other`. |
| `lead_time` | string or null | Warning lead time as stated. |
| `actions` | list of strings | Preparedness/response actions taken. |
| `outcome` | string | `positive`, `mixed`, `negative`, `not_reported` (combined over articles). |
| `outcome_justification` | string | One sentence. |
| `impacts` | object | Only stated values: `deaths`, `missing`, `injured`, `evacuated`, `people_reached`, `affected` (numbers), `economic_losses` (text). |
| `summary_en` | string | 2–3 sentence English summary. |
| `summaries_original` | object | Short summaries in the original language, keyed by language code, e.g. `{"ja": "…", "es": "…"}`. |
| `languages` | list | Languages of its articles. |
| `confidence` | number | Highest classification confidence (0–1). |
| `review` | bool | `true` if all its articles had low confidence (needs human review). |
| `sources` | list | Every article: `url`, `title` (original language), `title_en`, `source_name`, `language`, `published_at`. |
| `first_seen`, `last_updated` | string | When its first / latest article was collected (UTC). |
| `first_seen_local` | string | `first_seen` in Zurich time. |
| `is_new` | bool | First seen today (Zurich date). |

Hazard values: `tropical_cyclone`, `flood`, `flash_flood`, `storm_surge`,
`heavy_rain`, `heatwave`, `drought`, `severe_storm`, `tornado`, `landslide`,
`tsunami`, `earthquake`, `volcanic`, `wildfire`, `cold_wave`, `snow_ice`,
`sand_dust_storm`, `glacial_lake_outburst`, `multi_hazard`, `other`.

## Database (SQLite, `state/ews.sqlite`)

Defined in `ews_news/db.py`. Tables: `items` (every collected article with
its status), `classifications` (Claude's structured output per item, JSON in
`data_json`), `stories`, `runs`, `source_stats` (per run and source) and
`api_usage` (every Claude call with tokens and cost). The daily job saves a
text dump `ews.sql` on the `data` branch; it can be loaded into PostgreSQL
with minor edits.
