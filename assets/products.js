// AI products page: reads data/products.json (format: docs/data-schema.md).
(function () {
  "use strict";

  const STATUS_CLASS = { 1: "is-success", 2: "is-info", 3: "is-light", 4: "is-warning" };
  const STATUS_SHORT = { 1: "Operational", 2: "Testing / pre-operational", 3: "Research", 4: "Released, not at NMHS" };
  const STATUS_ORDER = [1, 2, 4, 3];
  const STAGE_LABEL = { operational: "operational", testing: "testing", research: "research", planned: "planned" };
  const AREAS = {
    observations: "Observations & data collection", data_processing: "Data processing & QC",
    forecasting: "Forecasting & nowcasting", guidance: "Guidance & post-processing",
    hydrology: "Hydrology & floods", warnings: "Warnings & impact-based forecasting",
    communication: "Communication & dissemination", climate_services: "Climate services",
    operations: "Operations & IT", other: "Other",
  };
  const TYPES = {
    weather_model: "AI weather model", nowcasting: "Nowcasting", subseasonal_climate: "Subseasonal / climate",
    post_processing: "Post-processing", observation_data: "Observations / data", hydrology_flood: "Hydrology / flood",
    warning_impact: "Warnings / impact", decision_support: "Decision support", llm_assistant: "LLM / assistant", other: "Other",
  };
  const DEV_TYPES = {
    NMHS: "NMHS", WMO_centre: "WMO / regional centre", WMO: "WMO", national_ews_agency: "National EWS agency",
    other_national_agency: "Other national agency", international: "International", research: "Research",
    private_sector: "Private sector", NGO: "NGO", other: "Other",
  };

  const $ = (id) => document.getElementById(id);
  const state = { data: null, status: "" };
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : "#");
  const fmtDate = (d) => (d ? new Date(d + "T12:00:00Z").toLocaleDateString("en-GB",
    { year: "numeric", month: "short", day: "numeric" }) : "");

  function fillSelect(id, values, labels, all) {
    $(id).innerHTML = '<option value="">' + esc(all) + "</option>" + values.map((v) =>
      '<option value="' + esc(v) + '">' + esc(labels[v] || v) + "</option>").join("");
  }

  function renderSummary() {
    const d = state.data;
    $("summary").innerHTML = STATUS_ORDER.map((s) =>
      '<div class="column is-6-mobile is-3-tablet"><a class="box status-box' + (String(state.status) === String(s) ? " is-selected" : "") +
      '" data-status="' + s + '"><span class="tag ' + STATUS_CLASS[s] + '">' + s + "</span> " +
      '<span class="status-count">' + (d.counts[s] || 0) + "</span><br><span class=\"is-size-7\">" +
      esc(d.status_labels[s]) + "</span></a></div>").join("");
  }

  function matches(p, f) {
    if (state.status && String(p.status) !== String(state.status)) return false;
    if (!f.unnamed && !p.is_named) return false;
    if (f.area && !p.areas.includes(f.area)) return false;
    if (f.dev && p.developer_type !== f.dev) return false;
    if (f.type && p.product_type !== f.type) return false;
    if (f.q) {
      const text = [p.name, p.developer, p.aliases.join(" "), p.assessment,
        p.deployments.map((d) => d.institution + " " + (d.short_name || "")).join(" ")].join(" ").toLowerCase();
      if (!f.q.split(/\s+/).every((w) => text.includes(w))) return false;
    }
    return true;
  }

  function row(p, i) {
    const deps = p.deployments.map((d) =>
      '<span class="dep' + (d.eligible ? " is-eligible" : "") + '" title="' + esc(DEV_TYPES[d.type] || d.type) + '">' +
      esc(d.short_name || d.institution) + ': <span class="stage-' + esc(d.stage) + '">' + esc(STAGE_LABEL[d.stage] || d.stage) +
      "</span></span>").join(" ") || '<span class="has-text-grey">—</span>';
    const evidence = p.evidence.map((x) =>
      '<li><span class="lang-badge">' + esc(x.language) + '</span><a href="' + esc(safeUrl(x.url)) +
      '" target="_blank" rel="noopener noreferrer">' + esc(x.title) + "</a> " +
      '<span class="has-text-grey">— ' + esc(x.source_name) + ", " + esc(fmtDate(x.date)) + "</span>" +
      (x.language !== "en" && x.title_en ? '<br><span class="has-text-grey is-size-7">' + esc(x.title_en) + "</span>" : "") +
      "</li>").join("");
    return '<tr class="product-row" data-i="' + i + '">' +
      '<td><button class="button is-small is-white toggle" aria-expanded="false" aria-label="Show details">▸</button></td>' +
      '<td><strong>' + esc(p.name) + "</strong>" + (p.is_named ? "" : ' <span class="tag is-light is-small">unnamed</span>') +
      (p.aliases.length ? '<br><span class="is-size-7 has-text-grey">also: ' + esc(p.aliases.slice(0, 4).join(", ")) + "</span>" : "") +
      '<br><span class="is-size-7">' + esc(p.developer || "—") + ' <span class="has-text-grey">(' + esc(DEV_TYPES[p.developer_type] || p.developer_type) + ")</span></span></td>" +
      '<td><span class="tag ' + STATUS_CLASS[p.status] + '" title="' + esc(p.status_label) + '">' + p.status + " · " + esc(STATUS_SHORT[p.status]) + "</span></td>" +
      '<td class="is-size-7">' + deps + "</td>" +
      '<td class="is-size-7">' + esc(TYPES[p.product_type] || p.product_type) + "<br>" +
      esc(p.areas.map((a) => (AREAS[a] || a).split(" &")[0]).join(", ")) + "</td>" +
      '<td class="is-size-7">' + p.n_articles + "<br>" + esc(fmtDate(p.last_mentioned)) + "</td></tr>" +
      '<tr class="detail-row" data-i="' + i + '" hidden><td></td><td colspan="5">' +
      (p.assessment ? '<p class="mb-2">' + esc(p.assessment) + "</p>" : '<p class="has-text-grey mb-2">Assessment pending (next weekly update).</p>') +
      (p.status_basis ? '<p class="is-size-7 mb-2"><strong>Basis:</strong> ' + esc(p.status_basis) + "</p>" : "") +
      '<p class="is-size-7 mb-1"><strong>Evidence (' + p.evidence.length + '):</strong> first mentioned ' + esc(fmtDate(p.first_mentioned)) + "</p>" +
      '<ul class="sources is-size-7">' + evidence + "</ul></td></tr>";
  }

  function render() {
    renderSummary();
    const f = { q: $("f-search").value.trim().toLowerCase(), area: $("f-area").value, dev: $("f-dev").value,
                type: $("f-type").value, unnamed: $("f-unnamed").checked };
    const list = state.data.products.filter((p) => matches(p, f));
    $("product-rows").innerHTML = list.map(row).join("") ||
      '<tr><td colspan="6" class="has-text-grey">No products match these filters.</td></tr>';
    $("result-count").textContent = list.length + (list.length === 1 ? " product" : " products") +
      (state.status ? " · status " + state.status + " (" + STATUS_SHORT[state.status] + ")" : "");
  }

  async function init() {
    let d;
    try {
      const r = await fetch("data/products.json", { cache: "no-cache" });
      if (!r.ok) throw new Error("HTTP " + r.status);
      d = await r.json();
    } catch (e) {
      $("loading").hidden = true;
      $("last-updated").textContent = "unavailable";
      $("status-banner").innerHTML = '<div class="notification is-warning">The product list is not available yet (' +
        esc(e.message) + "). It is built every Monday from the AI News archive.</div>";
      return;
    }
    state.data = d;
    $("last-updated").textContent = d.last_assessed_local ? new Date(d.last_assessed_at).toLocaleString("en-GB",
      { timeZone: "Europe/Zurich", year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "not yet";
    $("articles-analysed").textContent = d.articles_analysed;
    const uniq = (f) => [...new Set(d.products.map(f).flat())].filter(Boolean).sort();
    fillSelect("f-area", uniq((p) => p.areas), AREAS, "All areas");
    fillSelect("f-dev", uniq((p) => p.developer_type), DEV_TYPES, "All developers");
    fillSelect("f-type", uniq((p) => p.product_type), TYPES, "All product types");
    $("filters").addEventListener("input", render);
    $("filters").addEventListener("submit", (e) => e.preventDefault());
    $("filters").addEventListener("reset", () => { state.status = ""; setTimeout(render, 0); });
    $("summary").addEventListener("click", (e) => {
      const a = e.target.closest("[data-status]");
      if (!a) return;
      e.preventDefault();
      state.status = String(state.status) === a.dataset.status ? "" : a.dataset.status;
      render();
    });
    $("product-rows").addEventListener("click", (e) => {
      const tr = e.target.closest("tr.product-row");
      if (!tr || e.target.closest("a")) return;
      const detail = $("product-rows").querySelector('tr.detail-row[data-i="' + tr.dataset.i + '"]');
      const btn = tr.querySelector(".toggle");
      detail.hidden = !detail.hidden;
      btn.textContent = detail.hidden ? "▸" : "▾";
      btn.setAttribute("aria-expanded", String(!detail.hidden));
    });
    $("loading").hidden = true;
    render();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
