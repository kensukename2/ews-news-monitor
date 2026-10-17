# EWS News Monitor

Collects news every day and publishes two pages on **https://knaito.xyz**:

* **[EWS News](https://knaito.xyz/ews_news)**: how **early warning services**
  were provided during hazardous events (how hazards were monitored and
  forecast, how warnings were issued and communicated, what people did, and
  what happened as a result), grouped by WMO Regional Association (RA I–VI).
* **[AI News](https://knaito.xyz/ai_news)**: how **AI supports** National
  Meteorological and Hydrological Services, WMO centres, early warning systems
  and partner institutions (observations, data processing, forecasting,
  guidance, hydrology, warnings, communication, climate services and
  operations), including new AI model releases. Grouped by area of operation.

Sources are in English, Japanese and Spanish (more languages can be added
through configuration). Articles are classified and summarised with the
Claude API (Claude Haiku); only titles, short summaries and links are
stored, never full article text.

Each page is a **topic** (`config/topics/ews/`, `config/topics/ai/`) with its
own sources, keywords, Claude prompt, database and budget; collection,
filtering, clustering and publishing are shared.

## How it works

```
collect ──► pre-filter ──► classify (Claude) ──► cluster into stories ──► publish JSON ──► web page
(RSS, APIs,   (keywords      (structured JSON:      (same event across      (site/ews_news/
 list pages,   per language)  hazard, countries,     languages, e.g.         data/*.json)
 web search)                  EW4All pillars,        "Typhoon X" in en/ja/es)
                              outcome, summaries)
```

1. **Collect** (`ews_news/collectors/`): RSS feeds, the GDELT DOC API, Claude
   web search, JMA's press list, and news list pages of agencies that have no
   feed. robots.txt is respected and each site is contacted at most once
   every 1.5 s.
2. **Pre-filter** (`ews_news/prefilter.py`): cheap keyword rules per topic and
   language (`config/topics/<topic>/languages/*.yaml`). Japanese works without
   spaces between words.
3. **Classify** (`ews_news/classify.py`): Claude Haiku returns structured
   JSON; prompt and schema per topic in `ews_news/topics/ews.py` and
   `ews_news/topics/ai.py`. A URL is never classified twice;
   the same headline from several outlets is classified once. Items with
   confidence below 0.6 are flagged **Needs review** instead of being dropped.
4. **Cluster** (`ews_news/cluster.py`): articles about the same event (or the
   same AI development) become one story, also across languages (by event or
   AI system name, else by asking Claude which candidate story matches).
5. **Store** in SQLite (`state/ews.sqlite`, `state/ai.sqlite`), designed to
   move to PostgreSQL.
6. **Publish** static JSON for the page (`docs/data-schema.md`).

## Costs

All Claude calls are logged with their cost in each topic's `api_usage`
table and capped by `budget.monthly_usd` in `config/topics/<topic>/topic.yaml`
(**$5 per topic per month**, $10 in total). Each run may spend what is left
this month divided by the days left; work that does not fit stays pending for
the next day. Typical daily cost per topic: 3 web searches (~$0.011 each) +
classifications (~$0.0003 each) + story matching (~$0.0001 each), about
**$1.5–2 per topic per month**. Check spending with `python -m ews_news report`.

## Setup (local)

Requires Python 3.10 or newer.

```bash
git clone https://github.com/kensukename2/ews-news-monitor.git
cd ews-news-monitor
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # then put your Anthropic API key in .env
```

## Running it manually

```bash
.venv/bin/python -m ews_news run                  # full pipeline, all topics
.venv/bin/python -m ews_news --topic ai run       # only AI News
.venv/bin/python -m ews_news run --no-classify    # collect only, no API cost
.venv/bin/python -m ews_news --topic ews run --source nhk_news --source jma_press
.venv/bin/python -m ews_news check-sources --all  # test every feed / list page
.venv/bin/python -m ews_news report               # last run and this month's spending
.venv/bin/python -m ews_news recluster            # rebuild stories after changing cluster settings
.venv/bin/python -m pytest                        # tests
```

To see the page locally:

```bash
.venv/bin/python -m http.server 8000 --directory site
# open http://localhost:8000/ews_news/ or http://localhost:8000/ai_news/
```

## Configuration

| File | What it controls |
|---|---|
| `config/settings.yaml` | Settings shared by all topics: model, prices, confidence threshold, clustering windows. |
| `config/countries.yaml` | Country code → name and WMO Regional Association. |
| `config/topics/<topic>/topic.yaml` | Topic name, budget, file paths; can override any shared setting. |
| `config/topics/<topic>/sources.yaml` | All sources of that topic, grouped by language. Each has a `type` (`rss`, `gdelt`, `claude_search`, `html_links`, `jma_press`), a pre-filter mode and `enabled`. Disabled entries record why a listed source could not be used. |
| `config/topics/<topic>/languages/en.yaml`, `ja.yaml`, `es.yaml` | Keyword pre-filter, GDELT queries and web-search queries for that language. EWS uses `warning_terms` + `hazard_terms`; AI uses `ai_terms` + `domain_terms` (set by `require_groups`). |
| `ews_news/topics/ews.py`, `ai.py` | Claude prompt, output schema and story fields of each topic. |
| `site/ews_news/index.html`, `site/ai_news/index.html` | Page text, tabs, filters and card layout (`window.NEWS_PAGE`). |

**Adding a source:** prefer an RSS feed (`type: rss`). If a site has no
feed, use `type: html_links` with the news list page as `url` and a
`link_pattern` (regular expression matching the article links' paths). Run
`python -m ews_news check-sources` to test it.

**Adding a language** (e.g. French): copy
`config/topics/<topic>/languages/es.yaml` to `fr.yaml`, set `code: fr`,
translate the keyword lists and queries, then add sources with
`language: fr` to that topic's `sources.yaml`. No code changes are
needed. (Web-search queries can use `{month_en}`, `{month_ja}`, `{month_es}`,
`{today}`; add a month name for a new language in
`ews_news/collectors/websearch.py` if you need one.)

## Deployment

The site is served by **GitHub Pages** from the `gh-pages` branch of this
repository, with the custom domain `knaito.xyz`. The daily pipeline runs on
the **Hetzner server** (the same machine as CAP Composer at
`alerts.knaito.xyz`, which it does not touch).

```
Hetzner server (05:00 Europe/Zurich)                     GitHub
  systemd timer ews-news.timer
    └─ scripts/server_run.sh  (user ewsnews, /opt/ews-news-monitor)
         1. git pull main            (code)          ◄──── main
         2. git pull state/          (saved state)   ◄──── data
         3. python -m ews_news run   (both topics)
         4. push state/              ────────────────────► data      (ews.sql, ai.sql, logs/, published/)
         5. scripts/publish_pages.sh ────────────────────► gh-pages  ──► knaito.xyz
```

* **Daily run:** `deploy/ews-news.timer` starts `deploy/ews-news.service`
  at 05:00 Swiss time (summer and winter time handled by systemd); if the
  server was off, it runs at the next boot.
* **Code changes:** push to `main`. The server picks them up at its next run;
  the GitHub workflow `.github/workflows/daily.yml` ("Republish site")
  rebuilds the pages from the saved state right away, so page changes are
  visible within a minute. The workflow never collects or classifies news.
* **If a run fails,** the page shows a notice (stale or failed update).

### Useful commands on the server

```bash
ssh knaitocap
systemctl list-timers ews-news.timer          # next scheduled run
sudo systemctl start ews-news.service         # run now (takes ~15-25 min)
journalctl -u ews-news.service -n 100         # log of the latest runs
sudo -u ewsnews -H /opt/ews-news-monitor/.venv/bin/python -m ews_news report   # spending
```

### Server setup (already done; for reference or a new server)

```bash
apt-get install -y python3-venv
useradd --system --home-dir /var/lib/ewsnews --create-home --shell /usr/sbin/nologin ewsnews
# deploy key: ssh-keygen as ewsnews, add the public key to the repository's
# Settings -> Deploy keys with write access, Host alias "github-ewsnews" in
# /var/lib/ewsnews/.ssh/config
sudo -u ewsnews -H git clone git@github-ewsnews:kensukename2/ews-news-monitor.git /opt/ews-news-monitor
cd /opt/ews-news-monitor
sudo -u ewsnews -H git worktree add state -B data origin/data
sudo -u ewsnews -H python3 -m venv .venv && sudo -u ewsnews -H .venv/bin/pip install -r requirements.txt
# .env with ANTHROPIC_API_KEY, owner ewsnews, mode 600
install -m 644 deploy/ews-news.service deploy/ews-news.timer /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now ews-news.timer
```

DNS at Porkbun for `knaito.xyz`: four `A` records to `185.199.108.153`,
`185.199.109.153`, `185.199.110.153`, `185.199.111.153`, `www` `CNAME`
`kensukename2.github.io`, and the `_github-pages-challenge-kensukename2`
TXT record (domain verification). The `alerts` record points to the server.

## Data and privacy

Only metadata (title, source, date, URL), a short snippet/description and
generated summaries are stored. Secrets are read from `.env` (on your computer and on the
server, readable only by the `ewsnews` user); `.env` is never committed.

## License

MIT. See `LICENSE`.
