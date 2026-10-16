// EWS News page. Reads data/manifest.json and data/stories-YYYY-MM.json
// (format documented in docs/data-schema.md) and renders them with filters.
(function () {
  "use strict";

  const DATA = "data/";
  const PAGE_SIZE = 20;
  const REGIONS = {
    I: "RA I Africa", II: "RA II Asia", III: "RA III South America",
    IV: "RA IV North & Central America, Caribbean", V: "RA V South-West Pacific", VI: "RA VI Europe",
  };
  const REGION_SHORT = { I: "Africa", II: "Asia", III: "South America", IV: "N. & C. America", V: "SW Pacific", VI: "Europe" };
  const PILLARS = {
    1: "1 Risk knowledge", 2: "2 Detection, monitoring & forecasting",
    3: "3 Dissemination & communication", 4: "4 Preparedness & response",
  };
  const OUTCOMES = { positive: "Positive", mixed: "Mixed", negative: "Negative", not_reported: "Not reported" };
  const LANGS = { en: "English", ja: "日本語", es: "Español" };
  const AGENCY = {
    NMHS: "NMHS", RSMC: "RSMC", other_national_agency: "Other national agency",
    international: "International", NGO: "NGO", media: "Media", other: "Other",
  };

  const $ = (id) => document.getElementById(id);
  const state = { manifest: null, stories: [], region: "all", shown: PAGE_SIZE };

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function safeUrl(u) { return /^https?:\/\//i.test(u || "") ? u : "#"; }
  function label(s) { return String(s).replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase()); }
  function fmtLocal(iso) {
    if (!iso) return "–";
    const d = new Date(iso);
    return d.toLocaleString("en-GB", { timeZone: "Europe/Zurich", year: "numeric", month: "short",
      day: "numeric", hour: "2-digit", minute: "2-digit" });
  }
  function fmtDate(iso) {
    return iso ? new Date(iso.length === 10 ? iso + "T12:00:00Z" : iso)
      .toLocaleDateString("en-GB", { year: "numeric", month: "short", day: "numeric" }) : "";
  }

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
  function fillSelect(id, values, toLabel, allLabel) {
    $(id).innerHTML = '<option value="">' + esc(allLabel) + "</option>" +
      values.map((v) => '<option value="' + esc(v) + '">' + esc(toLabel(v)) + "</option>").join("");
  }

  function setupFilters(f) {
    fillSelect("f-hazard", f.hazards || [], label, "All hazards");
    $("f-country").innerHTML = '<option value="">All countries</option>' + (f.countries || [])
      .map((c) => '<option value="' + esc(c.code) + '">' + esc(c.name) + "</option>").join("");
    fillSelect("f-agency", f.agency_types || [], (v) => AGENCY[v] || v, "All agency types");
    fillSelect("f-pillar", (f.pillars || []).map(String), (v) => PILLARS[v] || v, "All pillars");
    fillSelect("f-outcome", f.outcomes || [], (v) => OUTCOMES[v] || v, "All outcomes");
    fillSelect("f-lang", f.languages || [], (v) => LANGS[v] || v, "All languages");
    const form = $("filters");
    form.addEventListener("input", () => { state.shown = PAGE_SIZE; render(); });
    form.addEventListener("submit", (e) => e.preventDefault());
    form.addEventListener("reset", () => setTimeout(() => { state.shown = PAGE_SIZE; render(); }, 0));
  }

  function currentFilters() {
    return {
      q: $("f-search").value.trim().toLowerCase(),
      from: $("f-from").value, to: $("f-to").value,
      hazard: $("f-hazard").value, country: $("f-country").value, agency: $("f-agency").value,
      pillar: $("f-pillar").value, outcome: $("f-outcome").value, lang: $("f-lang").value,
      review: $("f-review").checked,
    };
  }

  function searchText(s) {
    if (!s._text) {
      s._text = [s.event_name, s.title, s.summary_en, s.event_location,
        Object.values(s.summaries_original || {}).join(" "),
        s.countries.map((c) => c.name).join(" "), s.agencies.map((a) => a.name).join(" "),
        s.sources.map((x) => x.title + " " + x.source_name).join(" ")].join(" ").toLowerCase();
    }
    return s._text;
  }

  function matches(s, f, ignoreRegion) {
    if (!ignoreRegion && state.region !== "all" && !s.wmo_regions.includes(state.region)) return false;
    if (!f.review && s.review) return false;
    const d = s.event_date || s.first_seen.slice(0, 10);
    if (f.from && d < f.from) return false;
    if (f.to && d > f.to) return false;
    if (f.hazard && !s.hazards.includes(f.hazard)) return false;
    if (f.country && !s.countries.some((c) => c.code === f.country)) return false;
    if (f.agency && !s.agency_types.includes(f.agency)) return false;
    if (f.pillar && !s.pillars.includes(Number(f.pillar))) return false;
    if (f.outcome && s.outcome !== f.outcome) return false;
    if (f.lang && !s.languages.includes(f.lang)) return false;
    if (f.q && !f.q.split(/\s+/).every((w) => searchText(s).includes(w))) return false;
    return true;
  }

  // ------------------------------------------------------------------ render
  function renderTabs(f) {
    const base = state.stories.filter((s) => matches(s, f, true));
    const count = (r) => (r === "all" ? base.length : base.filter((s) => s.wmo_regions.includes(r)).length);
    const tabs = [["all", "All regions"]].concat(Object.keys(REGIONS).map((r) => [r, "RA " + r + " " + REGION_SHORT[r]]));
    $("region-tabs").querySelector("ul").innerHTML = tabs.map(([r, name]) =>
      '<li class="' + (state.region === r ? "is-active" : "") + '"><a href="#' + r + '" data-region="' + r +
      '" title="' + esc(REGIONS[r] || name) + '">' + esc(name) + '<span class="count">' + count(r) + "</span></a></li>").join("");
  }

  function storyCard(s) {
    const outcome = '<span class="tag outcome-' + esc(s.outcome) + '">Outcome: ' + esc(OUTCOMES[s.outcome] || s.outcome) + "</span>";
    const review = s.review ? '<span class="tag review" title="Low classification confidence">Needs review</span>' : "";
    const hazards = s.hazards.map((h) => '<span class="tag is-info is-light">' + esc(label(h)) + "</span>").join("");
    const regions = s.wmo_regions.map((r) => '<span class="tag is-light">RA ' + esc(r) + "</span>").join("");
    const countries = s.countries.map((c) => c.name).join(", ");
    const heading = s.event_name ? s.event_name + ": " + s.title : s.title;

    const originals = Object.entries(s.summaries_original || {}).map(([lang, text]) =>
      '<div class="original-summary" lang="' + esc(lang) + '"><span class="lang-badge">' + esc(lang) + "</span>" + esc(text) + "</div>").join("");

    const rows = [];
    if (s.agencies.length) rows.push(["Agencies", s.agencies.map((a) => esc(a.name) + ' <span class="has-text-grey">(' + esc(AGENCY[a.type] || a.type) + ")</span>").join("; ")]);
    if (s.pillars.length) rows.push(["EW4All pillars", esc(s.pillars.map((p) => PILLARS[p]).join("; "))]);
    if (s.channels.length) rows.push(["Channels", esc(s.channels.map(label).join(", "))]);
    if (s.lead_time) rows.push(["Lead time", esc(s.lead_time)]);
    if (s.actions.length) rows.push(["Actions", esc(s.actions.join("; "))]);
    const imp = Object.entries(s.impacts || {}).map(([k, v]) => label(k) + ": " + (typeof v === "number" ? v.toLocaleString("en") : v));
    if (imp.length) rows.push(["Impacts", esc(imp.join("; "))]);
    if (s.outcome_justification) rows.push(["Outcome", esc(s.outcome_justification)]);

    const sources = s.sources.map((x) =>
      '<li><span class="lang-badge">' + esc(x.language) + '</span><a href="' + esc(safeUrl(x.url)) +
      '" target="_blank" rel="noopener noreferrer">' + esc(x.title) + "</a> " +
      '<span class="has-text-grey">— ' + esc(x.source_name) + (x.published_at ? ", " + esc(fmtDate(x.published_at)) : "") + "</span>" +
      (x.language !== "en" && x.title_en ? '<br><span class="has-text-grey is-size-7">' + esc(x.title_en) + "</span>" : "") +
      "</li>").join("");

    return '<article class="box story' + (s.is_new ? " is-new" : "") + '">' +
      '<div class="story-head"><h3 class="story-title">' + esc(heading) + "</h3>" +
      (s.is_new ? '<span class="tag is-link">New today</span>' : "") + "</div>" +
      '<p class="story-meta">' + esc(fmtDate(s.event_date)) + (countries ? " · " + esc(countries) : "") +
      (s.event_location ? " · " + esc(s.event_location) : "") + " · " + s.sources.length + " source" + (s.sources.length > 1 ? "s" : "") + "</p>" +
      '<div class="tags">' + hazards + regions + outcome + review + "</div>" +
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
    $("result-count").textContent = list.length + " stories" + (today.length ? " · " + today.length + " new today" : "");
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
    setupFilters(m.facets || {});
    const hash = location.hash.replace("#", "");
    if (REGIONS[hash]) state.region = hash;

    $("region-tabs").addEventListener("click", (e) => {
      const a = e.target.closest("a[data-region]");
      if (!a) return;
      e.preventDefault();
      state.region = a.dataset.region;
      state.shown = PAGE_SIZE;
      history.replaceState(null, "", state.region === "all" ? location.pathname : "#" + state.region);
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
      $("earlier-list").innerHTML = '<p class="has-text-grey">No stories have been collected yet.</p>';
    }
  }

  document.addEventListener("DOMContentLoaded", init);
})();
