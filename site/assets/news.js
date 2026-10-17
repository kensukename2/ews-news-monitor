// Shared news page script (EWS News, AI News).
// Reads data/manifest.json and data/stories-YYYY-MM.json (format: docs/data-schema.md)
// and renders them using the page's configuration in window.NEWS_PAGE:
//
//   tabs:    {field, all, labels: {value: "Tab name"}, titles: {value: "tooltip"}}
//   filters: [{id, label, field, all, labels?}]   select menus built from manifest facets
//            (facet = manifest facet name; field = story field, list or single value)
//   card:    {heading(s), tags(s) -> [[cls, text, title?]], rows(s) -> [[label, html]]}
(function () {
  "use strict";

  const PAGE = window.NEWS_PAGE;
  const DATA = "data/";
  const PAGE_SIZE = 20;
  const LANGS = { en: "English", ja: "日本語", es: "Español" };

  const $ = (id) => document.getElementById(id);
  const state = { manifest: null, stories: [], tab: "all", shown: PAGE_SIZE };

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function safeUrl(u) { return /^https?:\/\//i.test(u || "") ? u : "#"; }
  function label(s) { return String(s).replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase()); }
  function fmtLocal(iso) {
    if (!iso) return "–";
    return new Date(iso).toLocaleString("en-GB", { timeZone: "Europe/Zurich", year: "numeric",
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }
  function fmtDate(iso) {
    return iso ? new Date(iso.length === 10 ? iso + "T12:00:00Z" : iso)
      .toLocaleDateString("en-GB", { year: "numeric", month: "short", day: "numeric" }) : "";
  }
  function asList(v) { return Array.isArray(v) ? v : (v == null ? [] : [v]); }
  function nameOf(map, v) { return (map && map[v]) || label(v); }

  // Helpers available to the page configuration.
  window.NEWS = { esc, label, nameOf, fmtDate };

  async function getJSON(name) {
    const r = await fetch(DATA + name, { cache: "no-cache" });
    if (!r.ok) throw new Error(name + ": HTTP " + r.status);
    return r.json();
  }

  // ------------------------------------------------------------------ status
  function showStatus(m) {
    $("last-updated").textContent = m.last_success_at ? fmtLocal(m.last_success_at) : "never";
    const msgs = [];
    const run = m.last_run;
    if (run && run.status === "failed") {
      msgs.push(["is-danger", "The latest daily update (" + fmtLocal(run.started_at) +
        ") failed. The stories below are from the last successful update."]);
    } else if (run && run.status === "partial") {
      const failed = ((run.stats && run.stats.sources) || []).filter((s) => s.error).map((s) => s.source_id);
      msgs.push(["is-warning is-light", "The latest update completed, but some sources could not be read: " +
        failed.join(", ") + "."]);
    }
    const ageH = (Date.now() - new Date(m.generated_at).getTime()) / 3.6e6;
    if (ageH > (m.stale_after_hours || 30)) {
      msgs.push(["is-warning", "This page has not been updated for " + Math.round(ageH) +
        " hours; the daily update may have failed."]);
    }
    $("status-banner").innerHTML = msgs.map(([cls, t]) =>
      '<div class="notification ' + cls + ' py-3 px-4 is-size-7">' + esc(t) + "</div>").join("");
  }

  function showFatal(err) {
    $("loading").hidden = true;
    $("last-updated").textContent = "unavailable";
    $("status-banner").innerHTML = '<div class="notification is-danger">The news data could not be loaded (' +
      esc(err.message) + "). Please try again later.</div>";
  }

  // ----------------------------------------------------------------- filters
  function buildFilters(facets) {
    const col = (cls, inner) => '<div class="column ' + cls + '">' + inner + "</div>";
    const select = (f) => col("is-6-mobile is-4-tablet is-2-desktop",
      '<label class="label is-small" for="f-' + f.id + '">' + esc(f.label) + "</label>" +
      '<div class="select is-small is-fullwidth"><select id="f-' + f.id + '"></select></div>');
    $("filters").innerHTML = '<div class="columns is-multiline is-variable is-2">' +
      col("is-12-mobile is-6-tablet is-4-desktop",
        '<label class="label is-small" for="f-search">Search</label>' +
        '<input class="input is-small" type="search" id="f-search" placeholder="' + esc(PAGE.searchHint || "Search") + '">') +
      col("is-6-mobile is-3-tablet is-2-desktop",
        '<label class="label is-small" for="f-from">From</label><input class="input is-small" type="date" id="f-from">') +
      col("is-6-mobile is-3-tablet is-2-desktop",
        '<label class="label is-small" for="f-to">To</label><input class="input is-small" type="date" id="f-to">') +
      PAGE.filters.map(select).join("") +
      col("is-6-mobile is-4-tablet is-2-desktop",
        '<label class="label is-small" for="f-country">Country</label>' +
        '<div class="select is-small is-fullwidth"><select id="f-country"></select></div>') +
      col("is-6-mobile is-4-tablet is-2-desktop",
        '<label class="label is-small" for="f-lang">Language</label>' +
        '<div class="select is-small is-fullwidth"><select id="f-lang"></select></div>') +
      col("is-12-mobile is-12-tablet is-2-desktop filter-actions",
        '<label class="checkbox is-size-7"><input type="checkbox" id="f-review" checked> Include items flagged for review</label>' +
        '<button type="reset" class="button is-small is-light">Reset filters</button>') +
      "</div>";

    const fill = (id, values, toLabel, all) => {
      $(id).innerHTML = '<option value="">' + esc(all) + "</option>" + values.map((v) =>
        '<option value="' + esc(v) + '">' + esc(toLabel(v)) + "</option>").join("");
    };
    PAGE.filters.forEach((f) => fill("f-" + f.id, (facets[f.facet] || []).map(String),
      (v) => nameOf(f.labels, v), f.all));
    $("f-country").innerHTML = '<option value="">All countries</option>' + (facets.countries || [])
      .map((c) => '<option value="' + esc(c.code) + '">' + esc(c.name) + "</option>").join("");
    fill("f-lang", facets.languages || [], (v) => LANGS[v] || v, "All languages");

    const form = $("filters");
    form.addEventListener("input", () => { state.shown = PAGE_SIZE; render(); });
    form.addEventListener("submit", (e) => e.preventDefault());
    form.addEventListener("reset", () => setTimeout(() => { state.shown = PAGE_SIZE; render(); }, 0));
  }

  function currentFilters() {
    const f = {
      q: $("f-search").value.trim().toLowerCase(), from: $("f-from").value, to: $("f-to").value,
      country: $("f-country").value, lang: $("f-lang").value, review: $("f-review").checked, sel: {},
    };
    PAGE.filters.forEach((x) => { f.sel[x.field] = $("f-" + x.id).value; });
    return f;
  }

  function searchText(s) {
    if (!s._text) {
      s._text = [s.event_name, s.title, s.summary_en, s.event_location, s.value_added,
        Object.values(s.summaries_original || {}).join(" "),
        s.countries.map((c) => c.name).join(" "),
        (s.agencies || s.institutions || []).map((a) => a.name + " " + (a.short_name || "")).join(" "),
        (s.system_names || []).join(" "),
        s.sources.map((x) => x.title + " " + x.source_name).join(" ")].join(" ").toLowerCase();
    }
    return s._text;
  }

  function matches(s, f, ignoreTab) {
    if (!ignoreTab && state.tab !== "all" && !asList(s[PAGE.tabs.field]).includes(state.tab)) return false;
    if (!f.review && s.review) return false;
    const d = s.event_date || s.first_seen.slice(0, 10);
    if (f.from && d < f.from) return false;
    if (f.to && d > f.to) return false;
    for (const [field, value] of Object.entries(f.sel)) {
      if (value && !asList(s[field]).map(String).includes(value)) return false;
    }
    if (f.country && !s.countries.some((c) => c.code === f.country)) return false;
    if (f.lang && !s.languages.includes(f.lang)) return false;
    if (f.q && !f.q.split(/\s+/).every((w) => searchText(s).includes(w))) return false;
    return true;
  }

  // ------------------------------------------------------------------ render
  function renderTabs(f) {
    const base = state.stories.filter((s) => matches(s, f, true));
    const t = PAGE.tabs;
    const count = (v) => (v === "all" ? base.length : base.filter((s) => asList(s[t.field]).includes(v)).length);
    const tabs = [["all", t.all]].concat(Object.entries(t.labels));
    $("tabs").querySelector("ul").innerHTML = tabs.map(([v, name]) =>
      '<li class="' + (state.tab === v ? "is-active" : "") + '"><a href="#' + esc(v) + '" data-tab="' + esc(v) +
      '" title="' + esc((t.titles && t.titles[v]) || name) + '">' + esc(name) + '<span class="count">' + count(v) +
      "</span></a></li>").join("");
  }

  function storyCard(s) {
    const tags = PAGE.card.tags(s).map(([cls, text, title]) =>
      '<span class="tag ' + esc(cls) + '"' + (title ? ' title="' + esc(title) + '"' : "") + ">" + esc(text) + "</span>").join("") +
      (s.review ? '<span class="tag review" title="Low classification confidence">Needs review</span>' : "");
    const countries = s.countries.map((c) => c.name).join(", ");
    const originals = Object.entries(s.summaries_original || {}).map(([lang, text]) =>
      '<div class="original-summary" lang="' + esc(lang) + '"><span class="lang-badge">' + esc(lang) + "</span>" + esc(text) + "</div>").join("");
    const rows = PAGE.card.rows(s);
    const sources = s.sources.map((x) =>
      '<li><span class="lang-badge">' + esc(x.language) + '</span><a href="' + esc(safeUrl(x.url)) +
      '" target="_blank" rel="noopener noreferrer">' + esc(x.title) + "</a> " +
      '<span class="has-text-grey">— ' + esc(x.source_name) + (x.published_at ? ", " + esc(fmtDate(x.published_at)) : "") + "</span>" +
      (x.language !== "en" && x.title_en ? '<br><span class="has-text-grey is-size-7">' + esc(x.title_en) + "</span>" : "") +
      "</li>").join("");

    return '<article class="box story' + (s.is_new ? " is-new" : "") + '">' +
      '<div class="story-head"><h3 class="story-title">' + esc(PAGE.card.heading(s)) + "</h3>" +
      (s.is_new ? '<span class="tag is-link">New today</span>' : "") + "</div>" +
      '<p class="story-meta">' + esc(fmtDate(s.event_date)) + (countries ? " · " + esc(countries) : "") +
      (s.event_location ? " · " + esc(s.event_location) : "") + " · " + s.sources.length + " source" + (s.sources.length > 1 ? "s" : "") + "</p>" +
      '<div class="tags">' + tags + "</div>" +
      '<p class="story-summary">' + esc(s.summary_en) + "</p>" +
      (originals ? "<details><summary>Original-language summary</summary>" + originals + "</details>" : "") +
      "<details><summary>Details and sources (" + s.sources.length + ")</summary>" +
      (rows.length ? "<dl>" + rows.map(([k, v]) => "<dt>" + esc(k) + "</dt><dd>" + v + "</dd>").join("") + "</dl>" : "") +
      '<ul class="sources">' + sources + "</ul></details>" +
      "</article>";
  }

  function render() {
    const f = currentFilters();
    renderTabs(f);
    const list = state.stories.filter((s) => matches(s, f));
    const today = list.filter((s) => s.is_new);
    const earlier = list.filter((s) => !s.is_new);
    $("today-section").hidden = today.length === 0;
    $("today-list").innerHTML = today.map(storyCard).join("");
    $("earlier-heading").textContent = today.length ? "Earlier stories" : "Stories";
    $("earlier-list").innerHTML = earlier.slice(0, state.shown).map(storyCard).join("") ||
      (today.length ? "" : '<p class="has-text-grey">No stories match these filters.</p>');
    $("load-more").hidden = earlier.length <= state.shown;
    $("result-count").textContent = list.length + (list.length === 1 ? " story" : " stories") +
      (today.length ? " · " + today.length + " new today" : "");
  }

  // -------------------------------------------------------------------- init
  async function init() {
    let m;
    try {
      m = await getJSON("manifest.json");
    } catch (e) {
      showFatal(e);
      return;
    }
    state.manifest = m;
    showStatus(m);
    buildFilters(m.facets || {});
    const hash = decodeURIComponent(location.hash.replace("#", ""));
    if (PAGE.tabs.labels[hash]) state.tab = hash;

    $("tabs").addEventListener("click", (e) => {
      const a = e.target.closest("a[data-tab]");
      if (!a) return;
      e.preventDefault();
      state.tab = a.dataset.tab;
      state.shown = PAGE_SIZE;
      history.replaceState(null, "", state.tab === "all" ? location.pathname : "#" + state.tab);
      render();
    });
    $("load-more").addEventListener("click", () => { state.shown += PAGE_SIZE; render(); });

    // Newest month first so the page is usable quickly; older months follow.
    const months = m.months || [];
    try {
      for (let i = 0; i < months.length; i++) {
        const data = await getJSON(months[i].file);
        state.stories = state.stories.concat(data.stories || []);
        state.stories.sort((a, b) => (a.last_updated < b.last_updated ? 1 : -1));
        if (i === 0) $("loading").hidden = true;
        render();
      }
    } catch (e) {
      $("status-banner").insertAdjacentHTML("beforeend",
        '<div class="notification is-warning py-3 px-4 is-size-7">Some older stories could not be loaded (' + esc(e.message) + ").</div>");
    }
    $("loading").hidden = true;
    if (!months.length) {
      render();
      $("earlier-list").innerHTML = '<p class="has-text-grey">No stories have been collected yet.</p>';
    }
  }

  document.addEventListener("DOMContentLoaded", init);
})();
