"use strict";
/*
 * Society run report: live progress while the steward governs, then the score, the
 * sociological reading, the vital signs of the society, its people, the story path,
 * an optional animated replay (window.SocietyVillage) and a decision explorer.
 * All dynamic text is inserted with textContent.
 */
(function () {
  const V = window.SocietyViz;
  const API = "/api/polis";
  const POLL_MS = 3000;
  const DOMAINS = ["D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8"];
  const DOMAIN_NAMES = { D1: "Information", D2: "Economy", D3: "Public works", D4: "Commons", D5: "Relationships", D6: "Institutions", D7: "Transmission", D8: "Adaptation" };
  const REFERENCES = ["passive", "naive", "expert", "oracle"];
  const REFERENCE_INFO = {
    passive: ["Passive", "Takes no action at all; a well-designed society should collapse under it."],
    naive: ["Naive", "Answers explicit requests and reacts to loud alarms, like a weak model."],
    expert: ["Expert", "Reads only the briefing a candidate gets and follows sound heuristics."],
    oracle: ["Oracle", "The expert with access to the hidden state: optimistic, never a cap."],
  };
  const STATUS_LABELS = { running: "Running", paused: "Paused", interrupted: "Interrupted", completed: "Completed", failed: "Failed", pending: "Pending", loading: "Loading" };
  const SKILL_ORDER = ["farming", "fishing", "healing", "building", "crafting", "trading", "lore"];
  const LEVELS = { good: ["Good", "✓"], watch: ["Watch", "!"], bad: ["Concern", "✕"] };
  const MOODS = { cheerful: "good", content: "good", fine: "neutral", tired: "watch", quiet: "watch", withdrawn: "watch", worried: "watch", anxious: "watch", grieving: "watch", hopeless: "bad", irritable: "bad", angry: "bad" };
  const fmt = V ? V.format : null;

  const state = {
    id: null,
    run: null,
    report: null,
    reportText: null,
    recipe: null,
    recipeHash: null,
    recipeProblem: null,
    seed: null,
    view: null,
    ctx: null,
    timer: null,
    polling: false,
    liveFrames: [],
    liveCast: [],
    liveProblem: null,
    liveChronicle: [],
    liveCharts: {},
    latestWeek: null,
    charts: [],
    decisions: new Map(),
    decisionWeek: null,
    decisionMax: 1,
    decisionAbort: null,
    decisionTimer: null,
    panels: {},
    syncTimer: null,
    sort: { key: "fate", dir: 1 },
    navObserver: null,
    revealObserver: null,
    storyZoom: 1,
    sheets: {},
  };
  const phoneQuery = window.matchMedia ? window.matchMedia("(max-width: 760px)") : { matches: false, addListener() {} };
  const onMedia = (q, fn) => (q.addEventListener ? q.addEventListener("change", fn) : q.addListener(fn));

  // ------------------------------------------------------------------ DOM helpers
  const $ = id => document.getElementById(id);

  function h(tag, attrs, ...children) {
    const node = document.createElement(tag);
    if (attrs) {
      for (const [key, value] of Object.entries(attrs)) {
        if (value === undefined || value === null || value === false) continue;
        if (key === "class") node.className = value;
        else if (key === "text") node.textContent = String(value);
        else if (key === "style") Object.assign(node.style, value);
        else if (key === "vars") Object.entries(value).forEach(([name, v]) => node.style.setProperty(name, v));
        else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
        else if (value === true) node.setAttribute(key, "");
        else node.setAttribute(key, String(value));
      }
    }
    return add(node, children);
  }

  function add(node, children) {
    (Array.isArray(children) ? children : [children]).flat(Infinity).forEach(child => {
      if (child === null || child === undefined || child === false) return;
      node.appendChild(child instanceof Node ? child : document.createTextNode(String(child)));
    });
    return node;
  }

  function clear(node) {
    node.textContent = "";
    return node;
  }

  const isNum = v => typeof v === "number" && Number.isFinite(v);
  const pts = v => fmt.number(v, 0);
  const dec = (v, d = 2) => fmt.decimal(v, d);
  const pct = (v, d = 0) => fmt.percent(v, d);
  const humanize = id => {
    const text = String(id || "").replace(/[_-]+/g, " ").trim();
    return text ? text.charAt(0).toUpperCase() + text.slice(1) : "";
  };
  const plural = (n, word, many) => `${fmt.number(n, 0)} ${n === 1 ? word : (many || word + "s")}`;

  function dateText(value) {
    if (!value) return "";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  }

  function announce(text) {
    $("notice").textContent = text;
  }

  function showError(text) {
    const node = $("error");
    node.textContent = text || "";
    node.hidden = !text;
  }

  // ------------------------------------------------------------------ phone sheets (the steward log next to a village)
  /** On phones the steward log becomes a half-height sheet over the page; elsewhere nothing changes. */
  function makeLogSheet(card, button, title) {
    const titleNode = h("span", { class: "sheet-title", text: title });
    const close = h("button", { type: "button", class: "sheet-close", "aria-label": "Close the steward log", title: "Close" }, "✕");
    const bar = h("div", { class: "sheet-bar" }, h("span", { class: "sheet-grabber", "aria-hidden": "true" }), titleNode, close);
    card.prepend(bar);
    // Out of the layout once closed, so the off-screen sheet never lengthens the page.
    card.classList.add("is-gone");
    let goneTimer = 0;
    const sheet = {
      get isOpen() { return card.classList.contains("is-open"); },
      open() {
        if (!phoneQuery.matches) return;
        clearTimeout(goneTimer);
        card.classList.remove("is-gone");
        void card.offsetWidth;
        card.classList.add("is-open");
        button.setAttribute("aria-expanded", "true");
        const panel = Object.values(state.panels).find(p => p && p.log && card.contains(p.log.node));
        requestAnimationFrame(() => { if (panel) panel.log.rescroll(); });
      },
      close() {
        card.classList.remove("is-open");
        card.style.transform = "";
        button.setAttribute("aria-expanded", "false");
        clearTimeout(goneTimer);
        goneTimer = setTimeout(() => { if (!sheet.isOpen) card.classList.add("is-gone"); }, 380);
      },
      toggle() { if (sheet.isOpen) sheet.close(); else sheet.open(); },
      setTitle(text) { titleNode.textContent = text; },
    };
    close.addEventListener("click", () => sheet.close());
    button.addEventListener("click", () => sheet.toggle());
    onMedia(phoneQuery, () => { if (!phoneQuery.matches) sheet.close(); });
    let drag = null;
    bar.addEventListener("pointerdown", event => {
      if (!phoneQuery.matches || event.target.closest("button")) return;
      drag = { id: event.pointerId, y: event.clientY, d: 0, t: performance.now() };
      bar.setPointerCapture(event.pointerId);
      card.classList.add("is-dragging");
    });
    bar.addEventListener("pointermove", event => {
      if (!drag || event.pointerId !== drag.id) return;
      drag.d = Math.max(0, event.clientY - drag.y);
      card.style.transform = `translateY(${drag.d}px)`;
    });
    const end = event => {
      if (!drag || event.pointerId !== drag.id) return;
      card.classList.remove("is-dragging");
      card.style.transform = "";
      if (drag.d > 90 || (drag.d > 10 && drag.d / Math.max(1, performance.now() - drag.t) > 0.55)) sheet.close();
      drag = null;
    };
    bar.addEventListener("pointerup", end);
    bar.addEventListener("pointercancel", end);
    return sheet;
  }

  // ------------------------------------------------------------------ fetching
  async function failure(response) {
    let detail = response.statusText || `HTTP ${response.status}`;
    try {
      const body = await response.json();
      if (body && body.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch (_) {
      // keep the status text
    }
    const error = new Error(detail);
    error.status = response.status;
    return error;
  }

  async function request(url, options) {
    const response = await fetch(url, Object.assign({ headers: { Accept: "application/json" } }, options || {}));
    if (!response.ok) throw await failure(response);
    return response;
  }

  async function getJSON(url, options) {
    return (await request(url, options)).json();
  }

  // ------------------------------------------------------------------ run state
  function seedKeys() {
    return Object.keys((state.run && state.run.seeds) || {}).sort((a, b) => Number(a) - Number(b));
  }

  function seedEntry(key) {
    return (state.run && state.run.seeds && state.run.seeds[key || state.seed]) || null;
  }

  function seedReport(key) {
    return (state.report && state.report.seeds && state.report.seeds[key || state.seed]) || null;
  }

  function isMultiSeed() {
    return seedKeys().length > 1;
  }

  function runIdFromPath() {
    const parts = location.pathname.split("/").filter(Boolean);
    const at = parts.indexOf("runs");
    const id = at >= 0 ? parts[at + 1] : parts[parts.length - 1];
    return id && /^[a-f0-9]{24}$/.test(id) ? id : null;
  }

  function pickSeed() {
    const keys = seedKeys();
    const wanted = new URLSearchParams(location.search).get("seed");
    return keys.includes(wanted) ? wanted : keys[0] || null;
  }

  // ------------------------------------------------------------------ header
  function renderHeader() {
    const run = state.run;
    if (!run) return;
    const status = run.status || "loading";
    $("recipe-name").textContent = run.recipe_name || run.recipe_id || "Society run";
    const title = clear($("run-title"));
    add(title, [run.model || run.model_id || "Unknown model", run.snapshot ? h("span", { class: "snapshot", text: run.snapshot }) : null]);
    document.title = `${run.model || "Society run"} · ${run.recipe_name || run.recipe_id || "Polis"} · UNIMATRIx`;
    const usage = run.usage || {};
    const meta = [
      `Run ${run.id.slice(0, 8)}`,
      run.created_at ? `started ${dateText(run.created_at)}` : null,
      plural(seedKeys().length, "seed"),
      plural(run.weeks || 0, "week"),
      usage.calls ? plural(usage.calls, "model call") : null,
      usage.input_tokens ? `${fmt.compact(usage.input_tokens)} in / ${fmt.compact(usage.generated_tokens || 0)} out tokens` : null,
      usage.seconds ? `${fmt.number(usage.seconds, 0)} s of inference` : null,
    ].filter(Boolean);
    $("run-meta").textContent = meta.join(" · ");
    const pill = $("status-pill");
    pill.dataset.status = status;
    pill.querySelector("span").textContent = STATUS_LABELS[status] || humanize(status);
    $("pause-btn").hidden = status !== "running";
    $("resume-btn").hidden = !["paused", "interrupted", "failed"].includes(status);
    const del = $("delete-btn");
    del.disabled = status === "running";
    del.title = status === "running" ? "Pause the run before deleting it" : "Delete this run permanently";
    const csv = $("csv-link");
    csv.hidden = !seedReport();
    csv.href = `${API}/runs/${state.id}/timeline.csv?seed=${encodeURIComponent(state.seed || "")}`;
    csv.setAttribute("download", `society-${state.id}-seed-${state.seed}.csv`);
    if (run.error) showError(`The run stopped with an error: ${run.error}`);
    // Phones lay the actions out two by two: an odd one out takes the whole row.
    const shown = [...document.querySelectorAll(".run-actions > button, .run-actions > .button")].filter(node => !node.hidden);
    shown.forEach((node, i) => node.classList.toggle("is-alone", i === shown.length - 1 && shown.length % 2 === 1));
    renderSeedTabs();
    $("footer-meta").textContent = [
      run.engine ? `Engine ${run.engine}` : null,
      run.recipe_id ? `recipe ${run.recipe_id}` : null,
      run.recipe_hash ? `revision ${run.recipe_hash.slice(0, 12)}` : null,
      run.updated_at ? `updated ${dateText(run.updated_at)}` : null,
    ].filter(Boolean).join(" · ");
  }

  function seedSummary(entry) {
    if (!entry) return "";
    if (entry.status === "completed") {
      return entry.collapsed ? `collapsed wk ${entry.collapsed.week}` : `${pts(entry.total)} pts`;
    }
    return `${STATUS_LABELS[entry.status] || entry.status} · wk ${entry.week || 0}/${state.run.weeks}`;
  }

  function renderSeedTabs() {
    const node = $("seed-tabs");
    const keys = seedKeys();
    node.hidden = keys.length < 2;
    if (node.hidden) return;
    const existing = [...node.querySelectorAll(".seed-tab")].map(tab => tab.dataset.seed);
    if (existing.join(",") !== keys.join(",")) {
      clear(node);
      keys.forEach(key => node.appendChild(h("button", { type: "button", role: "tab", class: "seed-tab", "data-seed": key, onclick: () => selectSeed(key) },
        h("span", { class: "seed-tab-name", text: `Seed ${key}` }), h("span", { class: "seed-tab-meta" }))));
    }
    node.querySelectorAll(".seed-tab").forEach(tab => {
      const key = tab.dataset.seed;
      const entry = seedEntry(key);
      const selected = key === state.seed;
      tab.classList.toggle("is-collapse", Boolean(entry && entry.collapsed));
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
      tab.querySelector(".seed-tab-meta").textContent = seedSummary(entry);
    });
  }

  function onSeedTabKey(event) {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    const keys = seedKeys();
    let index = keys.indexOf(state.seed);
    if (event.key === "ArrowLeft") index = (index - 1 + keys.length) % keys.length;
    if (event.key === "ArrowRight") index = (index + 1) % keys.length;
    if (event.key === "Home") index = 0;
    if (event.key === "End") index = keys.length - 1;
    event.preventDefault();
    selectSeed(keys[index]);
    const tab = $("seed-tabs").querySelector(`[data-seed="${CSS.escape(keys[index])}"]`);
    if (tab) tab.focus();
  }

  function selectSeed(key) {
    if (key === state.seed) return;
    state.seed = key;
    const url = new URL(location.href);
    url.searchParams.set("seed", key);
    history.replaceState(null, "", url);
    state.liveFrames = [];
    state.liveCast = [];
    state.liveProblem = null;
    state.liveChronicle = [];
    state.latestWeek = null;
    state.decisionWeek = null;
    renderHeader();
    renderSeedView();
    if (state.view === "live") refreshLive();
  }

  // ------------------------------------------------------------------ loading
  async function loadRecipe() {
    const run = state.run;
    if (!run || !run.recipe_id) return;
    try {
      let data;
      try {
        // The exact revision this run used, frozen when it started.
        data = await getJSON(`${API}/runs/${state.id}/recipe`);
      } catch (snapshotError) {
        data = await getJSON(`${API}/recipes/${encodeURIComponent(run.recipe_id)}`);
      }
      state.recipe = data.recipe || null;
      state.recipeHash = data.hash || null;
      if (run.recipe_hash && data.hash && run.recipe_hash !== data.hash) {
        state.recipeProblem = "The recipe has changed since this run (its revision differs). Thresholds, fact statements and the story layout come from the current revision.";
      }
    } catch (error) {
      state.recipe = null;
      state.recipeProblem = error.status === 404 || error.status === 422
        ? "The recipe of this run is no longer available. The story is laid out from the run's own record and the collapse thresholds are not drawn."
        : `The recipe could not be loaded (${error.message}).`;
    }
  }

  async function loadReport() {
    const response = await request(`${API}/runs/${state.id}/report`);
    const text = await response.text();
    state.report = JSON.parse(text);
    state.reportText = text;
    if (state.report.run) state.run = Object.assign({}, state.run, state.report.run);
    return state.report;
  }

  function needsReport() {
    const run = state.run;
    return run.status === "completed" || seedKeys().some(key => seedEntry(key).status === "completed");
  }

  // ------------------------------------------------------------------ view switching
  function teardown() {
    state.charts.forEach(chart => chart && chart.destroy && chart.destroy());
    state.charts = [];
    Object.values(state.liveCharts).forEach(chart => chart && chart.destroy && chart.destroy());
    state.liveCharts = {};
    Object.values(state.panels).forEach(panel => panel && panel.destroy());
    state.panels = {};
    clearTimeout(state.syncTimer);
  }

  function renderSeedView() {
    teardown();
    const report = seedReport();
    if (report) {
      state.view = "report";
      $("live").hidden = true;
      $("report").hidden = false;
      showReport(report);
    } else {
      state.view = "live";
      state.ctx = null;
      $("report").hidden = true;
      $("live").hidden = false;
      showLive();
    }
    setupExplorer();
    renderSectionNav();
    observeReveals();
  }

  // ------------------------------------------------------------------ section navigation
  function renderSectionNav() {
    const node = clear($("section-nav-links"));
    const links = state.view === "report"
      ? [["score", "Score"], ["domains", "Domains"], ["reading", "Reading"], ["vitals", "Vital signs"], ["society", "People"], ["story", "Story"], ["replay", "Replay"], ["decisions", "Decisions"]]
      : [["live", "Live"], ["decisions", "Decisions"]];
    const visible = links.filter(([id]) => {
      const section = $(id);
      return section && !section.hidden && !section.closest("[hidden]");
    });
    visible.forEach(([id, label]) => node.appendChild(h("a", { href: `#${id}`, "data-target": id, text: label })));
    if (state.navObserver) state.navObserver.disconnect();
    if (!("IntersectionObserver" in window)) return;
    state.navObserver = new IntersectionObserver(entries => {
      entries.forEach(entry => {
        if (!entry.isIntersecting) return;
        node.querySelectorAll("a").forEach(a => {
          if (a.dataset.target === entry.target.id) {
            if (!a.hasAttribute("aria-current")) revealChip(node, a);
            a.setAttribute("aria-current", "true");
          } else a.removeAttribute("aria-current");
        });
      });
    }, { rootMargin: "-30% 0px -60% 0px" });
    visible.forEach(([id]) => state.navObserver.observe($(id)));
  }

  /** Keep the current section chip in view inside the horizontally scrolling nav. */
  function revealChip(row, chip) {
    if (row.scrollWidth <= row.clientWidth + 2) return;
    const box = row.getBoundingClientRect();
    const item = chip.getBoundingClientRect();
    const delta = item.left + item.width / 2 - (box.left + box.width / 2);
    if (Math.abs(delta) > 6) row.scrollBy({ left: delta, behavior: V.prefersReducedMotion() ? "auto" : "smooth" });
  }

  function bindSectionNav() {
    const nav = $("section-nav");
    const row = $("section-nav-links");
    const fades = () => {
      const max = row.scrollWidth - row.clientWidth;
      row.classList.toggle("fade-l", max > 2 && row.scrollLeft > 2);
      row.classList.toggle("fade-r", max > 2 && row.scrollLeft < max - 2);
    };
    row.addEventListener("scroll", fades, { passive: true });
    const height = () => { document.documentElement.style.setProperty("--nav-h", `${Math.ceil(nav.getBoundingClientRect().height)}px`); fades(); };
    if (typeof ResizeObserver === "function") { new ResizeObserver(height).observe(nav); new ResizeObserver(fades).observe(row); }
    window.addEventListener("resize", height);
    height();
  }

  function observeReveals() {
    const nodes = document.querySelectorAll(".reveal:not(.is-in)");
    if (!("IntersectionObserver" in window) || V.prefersReducedMotion()) {
      nodes.forEach(node => node.classList.add("is-in"));
      return;
    }
    if (!state.revealObserver) {
      state.revealObserver = new IntersectionObserver(entries => entries.forEach(entry => {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-in");
          state.revealObserver.unobserve(entry.target);
        }
      }), { rootMargin: "0px 0px -6% 0px" });
    }
    nodes.forEach(node => state.revealObserver.observe(node));
  }

  // ------------------------------------------------------------------ report context
  function recipeNodes() {
    const nodes = new Map();
    const story = state.recipe && state.recipe.story;
    ((story && story.nodes) || []).forEach(node => nodes.set(node.id, node));
    return nodes;
  }

  function nodeTitle(ctx, id) {
    const node = ctx.nodes.get(id);
    return (node && node.title) || humanize(id);
  }

  function isMarkerNode(node) {
    if (!node) return false;
    if (node.kind === "crisis") return true;
    return (node.effects || []).some(effect => ["arrive", "convention_shift", "epidemic"].includes(effect.type));
  }

  function buildContext(report) {
    const timeline = report.timeline || [];
    const frames = report.frames || [];
    const ctx = {
      report,
      timeline,
      frames,
      weeks: timeline.map(row => row.week),
      nodes: recipeNodes(),
      factions: new Map(((report.factions && report.factions.length ? report.factions : (state.recipe && state.recipe.factions)) || []).map(f => [f.id, f])),
      residents: new Map((report.residents || []).map(r => [r.id, r])),
      season: new Map(frames.map(frame => [frame.week, frame.season])),
      score: report.score || {},
      group: `seed-${state.seed}`,
      horizon: report.weeks || (state.run && state.run.weeks) || 52,
    };
    const story = report.story || {};
    const markers = [];
    const notes = new Map();
    const note = (week, text) => {
      if (!isNum(week)) return;
      if (!notes.has(week)) notes.set(week, []);
      notes.get(week).push(text);
    };
    Object.entries(story.nodes || {}).forEach(([id, status]) => {
      const node = ctx.nodes.get(id);
      const root = !status.via;
      if ((node ? isMarkerNode(node) : root) && isNum(status.activated)) {
        markers.push({ x: status.activated, label: nodeTitle(ctx, id), kind: node && node.kind === "crisis" ? "crisis" : "event" });
      }
      if (root) note(status.activated, `Story · ${nodeTitle(ctx, id)}`);
    });
    (story.path || []).forEach(step => note(step.week, `${nodeTitle(ctx, step.source)} → ${nodeTitle(ctx, step.target)}${step.label ? ` (${step.label})` : ""}`));
    (story.outcomes || []).forEach(outcome => note(outcome.week, `${outcome.type === "collapse" ? "Collapse" : "Turning point"} · ${outcome.label}`));
    if (ctx.score.collapsed) {
      markers.push({ x: ctx.score.collapsed.week, label: "Collapse", kind: "collapse" });
      note(ctx.score.collapsed.week, `Collapse · ${ctx.score.collapsed.label}`);
    }
    ctx.markers = markers;
    ctx.quietMarkers = markers.map(m => ({ x: m.x, kind: m.kind }));
    ctx.notes = week => (notes.get(week) || []).slice(0, 5);
    ctx.xTitle = week => (ctx.season.get(week) ? `Week ${week} · ${ctx.season.get(week)}` : `Week ${week}`);
    ctx.references = referencesForSeed();
    return ctx;
  }

  function referencesForSeed() {
    const refs = (state.report && state.report.references) || {};
    return REFERENCES.filter(key => refs[key]).map(key => {
      const ref = refs[key];
      const seeds = ref.seeds || {};
      const total = isNum(seeds[state.seed]) ? seeds[state.seed] : ref.total;
      return { key, label: REFERENCE_INFO[key][0], note: REFERENCE_INFO[key][1], total, collapsed: ref.collapsed || 0, seeds: Object.keys(seeds).length, domains: ref.domains || {} };
    });
  }

  function collapseRules() {
    return (state.recipe && state.recipe.collapse) || {};
  }

  function series(ctx, key) {
    return ctx.timeline.map(row => (isNum(row[key]) ? row[key] : null));
  }

  function track(chart) {
    state.charts.push(chart);
    return chart;
  }

  // ------------------------------------------------------------------ report
  function showReport(report) {
    const ctx = buildContext(report);
    state.ctx = ctx;
    renderHero(ctx);
    renderDomains(ctx);
    renderReading(ctx);
    renderCharts(ctx);
    renderSociety(ctx);
    renderStory(ctx);
    renderReplay(ctx);
  }

  // ---- hero
  function countUp(node, target) {
    if (!isNum(target)) {
      node.textContent = "–";
      return;
    }
    if (V.prefersReducedMotion() || target === 0) {
      node.textContent = pts(target);
      return;
    }
    const start = performance.now();
    const duration = 1400;
    const step = now => {
      if (!node.isConnected) return;
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 4);
      node.textContent = pts(target * eased);
      if (t < 1) requestAnimationFrame(step);
    };
    node.textContent = "0";
    requestAnimationFrame(step);
  }

  function renderHero(ctx) {
    const score = ctx.score;
    const collapsed = score.collapsed;
    const section = $("score");
    section.classList.toggle("is-collapse", Boolean(collapsed));
    $("score-eyebrow").textContent = isMultiSeed() ? `Total score · seed ${state.seed}` : "Total score";
    const figure = clear($("hero-figure"));
    const value = h("span", { class: "hero-value", "aria-hidden": "true" });
    const spoken = h("span", { class: "sr-only", text: `${pts(score.total)} points` });
    if (collapsed) {
      add(figure, [
        h("p", { class: "collapse-word", text: "Collapse" }),
        h("p", { class: "collapse-detail" }, h("span", { class: "collapse-week", text: `Week ${collapsed.week}` }), h("span", { text: collapsed.label || humanize(collapsed.cause) })),
        h("div", { class: "hero-number is-zero" }, value, spoken, h("span", { class: "hero-unit", text: "points" })),
        h("p", { class: "forfeit" }, "Forfeited ", h("s", { text: pts(score.forfeited) }), " points earned before the collapse."),
      ]);
      value.textContent = "0";
    } else {
      add(figure, h("div", { class: "hero-number" }, value, spoken, h("span", { class: "hero-unit", text: "points" })));
      countUp(value, score.total);
    }
    const expert = ctx.references.find(ref => ref.key === "expert");
    const ratio = expert && expert.total > 0 ? score.total / expert.total : null;
    const sub = clear($("hero-sub"));
    if (ratio !== null) add(sub, [h("strong", { text: `${dec(ratio, 2)}×` }), " the expert reference on this seed"]);
    add(sub, [ratio !== null ? " · " : "", `${score.survived_weeks ?? "–"} of ${ctx.horizon} weeks survived`]);
    const aggregate = $("hero-aggregate");
    const runScore = state.report && state.report.score;
    aggregate.hidden = !(isMultiSeed() && runScore);
    if (!aggregate.hidden) {
      aggregate.textContent = `Run mean over ${plural(runScore.seeds, "seed")}: ${pts(runScore.total)} points`
        + (isNum(runScore.relative_to_expert) ? ` · ${dec(runScore.relative_to_expert, 2)}× expert` : "")
        + ` · ${plural(runScore.collapsed || 0, "collapse")}`;
    }
    renderHeroFacts(ctx);
    renderLadder(ctx);
  }

  function factTile(label, value, detail) {
    return h("div", { class: "fact" }, h("span", { class: "fact-label", text: label }), h("strong", { class: "fact-value", text: value }), detail ? h("span", { class: "fact-detail", text: detail }) : null);
  }

  function renderHeroFacts(ctx) {
    const node = clear($("hero-facts"));
    const score = ctx.score;
    const first = ctx.timeline[0] || {};
    const last = ctx.timeline[ctx.timeline.length - 1] || {};
    const events = score.events || {};
    const arrow = (a, b, f) => `${f(a)} → ${f(b)}`;
    add(node, [
      factTile("WELLBYs", dec(score.wellby, 1), isNum(score.wellby_equity) ? `${dec(score.wellby_equity, 1)} equity-adjusted` : "life-satisfaction years above 2"),
      factTile("Population", arrow(first.population, last.population, v => fmt.number(v, 0)), `${plural(events.deaths || 0, "death")} · ${plural(events.departures || 0, "departure")}`),
      factTile("Mean wellbeing", arrow(first.wellbeing, last.wellbeing, v => dec(v, 1)), "life satisfaction, 0–10"),
      factTile("Trust in the steward", arrow(first.trust, last.trust, v => dec(v, 2)), `legitimacy ${dec(last.legitimacy, 2)}`),
      factTile("The lake", arrow(first.commons, last.commons, v => pct(v)), `${fmt.number(events.harvest, 0)} units harvested`),
      factTile("Inequality (Gini)", arrow(first.gini, last.gini, v => dec(v, 2)), "household wealth"),
    ]);
  }

  function renderLadder(ctx) {
    const score = ctx.score;
    const marks = ctx.references.map(ref => ({
      label: ref.label,
      value: ref.total,
      color: V.palette.references[ref.key],
      kind: "ref",
      note: `${ref.note}${ref.collapsed ? ` Collapsed on ${ref.collapsed} of ${ref.seeds} seed(s).` : ""}`,
    }));
    if (score.collapsed && score.earned > 0) marks.push({ label: `forfeited ${pts(score.earned)}`, value: score.earned, kind: "ghost", note: "Points earned before the collapse; all of them were forfeited." });
    marks.push({ label: "This run", value: score.total, valueText: pts(score.total), kind: "model", collapsed: Boolean(score.collapsed), note: score.collapsed ? `Collapsed in week ${score.collapsed.week}.` : "" });
    const container = $("ladder");
    if (!marks.length) {
      clear(container);
      return;
    }
    track(V.ladder(container, { marks, label: `This run scored ${pts(score.total)} points; references: ${ctx.references.map(r => `${r.label} ${pts(r.total)}`).join(", ")}` }));
  }

  // ---- domains
  function domainMeta(ctx, d) {
    const meta = (ctx.report.domains && ctx.report.domains[d]) || {};
    const scored = (ctx.score.domains && ctx.score.domains[d]) || {};
    return { name: scored.name || meta.name || DOMAIN_NAMES[d], unit: scored.unit || meta.unit || "", help: meta.help || "", units: scored.units, rate: scored.rate, points: scored.points || 0 };
  }

  function renderDomains(ctx) {
    const score = ctx.score;
    const collapsed = Boolean(score.collapsed);
    const total = DOMAINS.reduce((sum, d) => sum + domainMeta(ctx, d).points, 0);
    const segments = DOMAINS.map((d, i) => {
      const meta = domainMeta(ctx, d);
      return { key: d, short: d, label: `${d} · ${meta.name}`, value: meta.points, color: V.palette.categorical[i] };
    });
    V.compositionBar($("composition"), { segments, muted: collapsed, unit: "points", label: "Points by domain", format: v => pts(v) });
    $("composition-title").textContent = collapsed ? "What was earned before the collapse" : "Contribution to the total";
    $("composition-note").textContent = collapsed ? `${pts(score.earned)} points earned, all forfeited` : `${pts(total)} points in total`;
    const legend = clear($("composition-legend"));
    DOMAINS.forEach((d, i) => {
      const key = h("i", { class: "viz-key box" });
      key.style.background = V.palette.categorical[i];
      legend.appendChild(h("span", { class: "viz-legend-item" }, key, h("span", { text: `${d} ${domainMeta(ctx, d).name}` })));
    });
    const grid = clear($("domain-cards"));
    DOMAINS.forEach((d, i) => grid.appendChild(domainCard(ctx, d, i, total)));
    grid.appendChild(h("p", { class: "caption grid-caption", text: "Reference bars are the scripted stewards' mean domain points over the recipe's seeds. The sparkline is this run's cumulative points in the domain." }));
  }

  function domainCard(ctx, d, index, total) {
    const meta = domainMeta(ctx, d);
    const color = V.palette.categorical[index];
    const card = h("article", { class: "card domain-card reveal", vars: { "--domain": color } });
    add(card, h("header", { class: "domain-head" },
      h("span", { class: "domain-code", text: d, style: { color: V.inkFor(color) } }),
      h("h3", { text: meta.name })));
    add(card, h("p", { class: "domain-points" },
      h("strong", { text: pts(meta.points) }), h("span", { text: " pts" }),
      h("span", { class: "domain-share", text: total > 0 ? `${pct(meta.points / total, 1)} of the total` : "" })));
    add(card, h("p", { class: "domain-units" },
      h("span", { text: `${isNum(meta.units) ? fmt.number(meta.units, Math.abs(meta.units) < 100 ? 1 : 0) : "–"} ${meta.unit}` }),
      isNum(meta.rate) ? h("span", { class: "muted", text: ` × ${meta.rate} pts per unit` }) : null));
    if (meta.help) add(card, h("p", { class: "domain-help", text: meta.help }));
    const rows = [{ label: ctx.score.collapsed ? "Forfeited" : "This run", value: meta.points, own: true }]
      .concat(ctx.references.map(ref => ({ label: ref.label, value: ref.domains[d], key: ref.key })));
    const max = Math.max(1, ...rows.map(r => (isNum(r.value) ? r.value : 0)));
    const list = h("ul", { class: "compare", "aria-label": `${meta.name}: this run against the references` });
    rows.forEach(row => {
      const fill = h("i", { class: "compare-fill" });
      fill.style.width = `${Math.max(0, (isNum(row.value) ? row.value : 0) / max) * 100}%`;
      if (!row.own) fill.style.background = V.palette.references[row.key] || "#6f6f6f";
      list.appendChild(h("li", { class: row.own ? "is-own" : "" },
        h("span", { class: "compare-label", text: row.label }),
        h("span", { class: "compare-track" }, fill),
        h("span", { class: "compare-value", text: isNum(row.value) ? pts(row.value) : "–" })));
    });
    add(card, list);
    const spark = h("div", { class: "domain-spark" });
    V.sparkline(spark, { values: ctx.timeline.map(row => (row.points ? row.points[d] : null)), color, width: 220, height: 34 });
    add(card, spark);
    return card;
  }

  // ---- reading
  function renderReading(ctx) {
    const report = ctx.report;
    $("narrative").textContent = report.narrative || "";
    const findings = report.diagnosis || [];
    const counts = { good: 0, watch: 0, bad: 0 };
    findings.forEach(f => { counts[f.level] = (counts[f.level] || 0) + 1; });
    const summary = clear($("diagnosis-summary"));
    Object.keys(LEVELS).forEach(level => {
      summary.appendChild(h("span", { class: `level-chip is-${level}` }, h("i", { "aria-hidden": "true", text: LEVELS[level][1] }), `${counts[level] || 0} ${LEVELS[level][0].toLowerCase()}`));
    });
    const grid = clear($("diagnosis"));
    findings.forEach(f => {
      const level = LEVELS[f.level] ? f.level : "watch";
      grid.appendChild(h("article", { class: `finding is-${level} reveal` },
        h("header", { class: "finding-head" },
          h("span", { class: "finding-level" }, h("i", { "aria-hidden": "true", text: LEVELS[level][1] }), LEVELS[level][0]),
          h("h3", { text: f.title })),
        h("p", { class: "finding-text", text: f.text }),
        f.basis ? h("p", { class: "finding-basis" }, h("span", { text: "Basis · " }), f.basis) : null));
    });
    renderLedger(ctx);
    renderForecasts(ctx);
    renderToolkit(ctx);
  }

  function renderLedger(ctx) {
    const events = ctx.score.events || {};
    const rows = [
      ["Promises kept", events.promises_kept],
      ["Promises broken", events.promises_broken],
      ["Disputes settled", events.conflicts_resolved],
      ["Violent escalations", events.violence],
      ["Concerns relieved by the steward", events.concerns_relieved],
      ["Apprentice levels gained", events.apprentice_levels],
      ["Deaths", events.deaths],
      ["Departures", events.departures],
      ["Units harvested from the lake", events.harvest],
    ];
    const dl = clear($("ledger"));
    rows.forEach(([label, value]) => dl.appendChild(h("div", { class: "ledger-row" }, h("dt", { text: label }), h("dd", { text: isNum(value) ? fmt.number(value, Number.isInteger(value) ? 0 : 0) : "–" }))));
  }

  function factOf(id) {
    const facts = (state.recipe && state.recipe.facts) || [];
    const key = String(id || "").replace(/^p-/, "");
    return facts.find(f => f.id === key) || facts.find(f => f.id === id) || null;
  }

  function renderForecasts(ctx) {
    const node = clear($("forecasts"));
    const forecasts = ctx.score.forecasts || [];
    if (!forecasts.length) {
      node.appendChild(h("p", { class: "muted", text: "No forecast was settled during this run." }));
      return;
    }
    const table = h("table", { class: "mini-table" });
    add(table, h("thead", {}, h("tr", {}, ["Question", "P(true)", "Truth", "Points"].map(t => h("th", { scope: "col", text: t })))));
    const body = h("tbody");
    forecasts.forEach(f => {
      const fact = factOf(f.probe);
      body.appendChild(h("tr", {},
        h("th", { scope: "row", text: fact ? fact.statement : humanize(f.probe) }),
        h("td", { text: isNum(f.answer) ? pct(f.answer) : "no answer" }),
        h("td", { text: f.truth ? "true" : "false" }),
        h("td", { class: isNum(f.points) && f.points < 0 ? "is-negative" : "", text: isNum(f.points) ? `${f.points >= 0 ? "+" : ""}${dec(f.points, 1)}` : "–" })));
    });
    add(table, body);
    node.appendChild(table);
    node.appendChild(h("p", { class: "caption", text: "Logarithmic scoring: points per bit of information over a coin toss." }));
  }

  function renderToolkit(ctx) {
    const node = clear($("toolkit"));
    const actions = Object.entries(ctx.report.actions || {}).sort((a, b) => b[1] - a[1]);
    if (!actions.length) {
      node.appendChild(h("p", { class: "muted", text: "The steward took no action." }));
      return;
    }
    const max = actions[0][1] || 1;
    const total = actions.reduce((sum, [, n]) => sum + n, 0);
    const list = h("ul", { class: "toolkit" });
    actions.forEach(([verb, n]) => {
      const fill = h("i", { class: "compare-fill" });
      fill.style.width = `${(n / max) * 100}%`;
      list.appendChild(h("li", {}, h("span", { class: "compare-label", text: verb }), h("span", { class: "compare-track" }, fill), h("span", { class: "compare-value", text: fmt.number(n, 0) })));
    });
    node.appendChild(list);
    node.appendChild(h("p", { class: "caption", text: `${plural(total, "applied action")} over the run.` }));
  }

  // ---- charts
  function chartCard(parent, options) {
    const card = h("figure", { class: "card chart-card reveal" + (options.wide ? " is-wide" : "") });
    const head = h("figcaption", { class: "chart-head" },
      h("div", {}, h("h3", { text: options.title }), options.description ? h("p", { class: "chart-desc", text: options.description }) : null),
      options.unit ? h("span", { class: "chart-unit", text: options.unit }) : null);
    const body = h("div", { class: "chart-body" + (options.panels ? " is-panels" : "") });
    add(card, [head, body]);
    parent.appendChild(card);
    return body;
  }

  function panel(parent, title, unit) {
    const wrap = h("div", { class: "chart-panel" }, h("div", { class: "panel-head" }, h("h4", { text: title }), unit ? h("span", { class: "chart-unit", text: unit }) : null));
    const body = h("div", { class: "panel-body" });
    wrap.appendChild(body);
    parent.appendChild(wrap);
    return body;
  }

  function baseSpec(ctx, extra) {
    const last = ctx.weeks.length ? ctx.weeks[ctx.weeks.length - 1] : 0;
    const domain = ctx.score.collapsed && ctx.weeks.length && ctx.horizon > last
      ? { xDomain: [ctx.weeks[0], ctx.horizon], bands: [{ from: last, to: ctx.horizon, kind: "collapse", label: "after the collapse" }] }
      : {};
    return Object.assign({ x: ctx.weeks, group: ctx.group, xTitle: ctx.xTitle, notes: ctx.notes, xLabel: "Week", markers: ctx.markers }, domain, extra);
  }

  const L = V ? V.palette.lines : [];

  function renderCharts(ctx) {
    const grid = clear($("charts"));
    const rules = collapseRules();
    const initial = (ctx.timeline[0] && ctx.timeline[0].population) || (state.recipe && state.recipe.residents && state.recipe.residents.length) || 0;
    const share = { format: v => pct(v), min: 0, max: 1 };

    // People
    const people = chartCard(grid, { title: "People", description: "Residents present, and how many are distressed (wellbeing below 4) or isolated (loneliness above 0.6); next to them, mean life satisfaction.", panels: true, wide: true });
    people.classList.add("is-pair");
    track(V.timeChart(panel(people, "Residents", "people"), baseSpec(ctx, {
      label: "Residents present, distressed and isolated by week",
      height: 210,
      y: { format: v => fmt.number(v, 0) },
      series: [
        { label: "Present", values: series(ctx, "population"), color: L[0], step: true, area: true },
        { label: "Distressed", values: series(ctx, "distressed"), color: L[1], step: true },
        { label: "Isolated", values: series(ctx, "isolated"), color: L[2], step: true },
      ],
      thresholds: isNum(rules.population) && initial ? [{ y: rules.population * initial, label: `Depopulation below ${fmt.number(rules.population * initial, 1)}` }] : [],
    })));
    track(V.timeChart(panel(people, "Mean life satisfaction", "0–10"), baseSpec(ctx, {
      label: "Mean life satisfaction by week",
      height: 210,
      y: { min: 0, max: 10, format: v => dec(v, 1) },
      series: [{ label: "Wellbeing", values: series(ctx, "wellbeing"), color: L[0], area: true }],
    })));

    // Trust and unrest
    const thresholds = [];
    if (isNum(rules.revolt_share)) thresholds.push({ y: rules.revolt_share, label: `Revolt at ${pct(rules.revolt_share)} protesting for ${rules.revolt_weeks || 1} wk` });
    if (isNum(rules.legitimacy)) thresholds.push({ y: rules.legitimacy, label: `No confidence below ${dec(rules.legitimacy, 2)} for ${rules.legitimacy_weeks || 1} wk`, color: V.palette.status.watch });
    track(V.timeChart(chartCard(grid, { title: "Trust, legitimacy and unrest", description: "Mean trust in the steward, perceived legitimacy and grievance (0–1), and the share of residents protesting.", unit: "0–1" }), baseSpec(ctx, {
      label: "Trust, legitimacy, grievance and protest share by week",
      height: 260,
      y: { min: 0, max: 1, format: v => dec(v, 2) },
      series: [
        { label: "Trust", values: series(ctx, "trust"), color: L[0], format: v => dec(v, 2) },
        { label: "Legitimacy", values: series(ctx, "legitimacy"), color: L[1], format: v => dec(v, 2) },
        { label: "Grievance", values: series(ctx, "grievance"), color: L[2], format: v => dec(v, 2) },
        { label: "Protesting", values: series(ctx, "protest"), color: L[3], format: v => pct(v) },
      ],
      thresholds,
    })));

    // Cohesion and hardship
    const hard = [];
    if (isNum(rules.fragmentation)) hard.push({ y: rules.fragmentation, label: `Fragmentation below ${pct(rules.fragmentation)}`, color: V.palette.status.watch });
    if (isNum(rules.famine_share)) hard.push({ y: rules.famine_share, label: `Famine at ${pct(rules.famine_share)} hungry for ${rules.famine_weeks || 1} wk` });
    track(V.timeChart(chartCard(grid, { title: "Cohesion and hardship", description: "Share of residents in the largest connected group, and the shares going hungry or sick.", unit: "share of residents" }), baseSpec(ctx, {
      label: "Cohesion, hunger and sickness shares by week",
      height: 260,
      y: share,
      series: [
        { label: "Largest group", values: series(ctx, "cohesion"), color: L[0] },
        { label: "Hungry", values: series(ctx, "starving"), color: L[2] },
        { label: "Sick", values: series(ctx, "sick"), color: L[1] },
      ],
      thresholds: hard,
    })));

    // Economy
    const economy = chartCard(grid, { title: "Economy", description: "Community net worth (treasury, household coins, food, medicine and tools at reference prices), the public stores and the inequality of household wealth.", wide: true, panels: true });
    economy.classList.add("is-quad");
    const econ = [
      ["Community net worth", "coins", "networth", { format: v => fmt.compact(v) }],
      ["Treasury", "coins", "treasury", { format: v => fmt.compact(v) }],
      ["Granary", "food units", "granary", { format: v => fmt.compact(v) }],
      ["Inequality (Gini)", "0–1", "gini", { min: 0, max: 1, format: v => dec(v, 2) }],
    ];
    econ.forEach(([title, unit, key, y]) => track(V.timeChart(panel(economy, title, unit), baseSpec(ctx, {
      label: `${title} by week`,
      height: 160,
      markers: ctx.quietMarkers,
      y,
      series: [{ label: title, values: series(ctx, key), color: L[0], area: key !== "gini", format: y.format }],
    }))));

    // The lake
    const commons = (state.recipe && state.recipe.world && state.recipe.world.commons) || {};
    const lake = chartCard(grid, { title: "The lake", description: "The fish stock as a share of the lake's capacity, and the weekly harvest. The maximum sustainable yield sits at half of capacity.", panels: true });
    const lakeLines = [{ y: 0.5, label: "Maximum sustainable yield · 50%", color: "#8a8a8a" }];
    if (isNum(commons.critical)) lakeLines.push({ y: commons.critical, label: `Critical stock · ${pct(commons.critical)}` });
    track(V.timeChart(panel(lake, "Stock", "share of capacity"), baseSpec(ctx, {
      label: "Lake stock as a share of capacity by week",
      height: 200,
      y: share,
      series: [{ label: "Stock", values: series(ctx, "commons"), color: L[1], area: true, format: v => pct(v, 1) }],
      thresholds: lakeLines,
    })));
    track(V.timeChart(panel(lake, "Weekly harvest", "units"), baseSpec(ctx, {
      label: "Weekly harvest from the lake",
      height: 130,
      markers: ctx.quietMarkers,
      y: { format: v => fmt.compact(v) },
      series: [{ label: "Harvest", values: series(ctx, "harvest"), color: L[1], type: "bar", format: v => dec(v, 1) }],
    })));

    renderAdaptation(ctx, grid);
    renderInformation(ctx, grid);
    renderKnowledge(ctx, grid);

    // Points
    track(V.timeChart(chartCard(grid, { title: "Points accumulated", description: ctx.score.collapsed ? "Cumulative points by domain until the collapse, when all of them were forfeited." : "Cumulative points by domain. Their sum at the last week is the score.", unit: "points", wide: true }), baseSpec(ctx, {
      label: "Cumulative points by domain, stacked",
      height: 300,
      stack: true,
      y: { format: v => fmt.compact(v) },
      totalLabel: "Total",
      series: DOMAINS.map((d, i) => ({ label: `${d} ${DOMAIN_NAMES[d]}`, values: ctx.timeline.map(row => (row.points ? row.points[d] : null)), color: V.palette.categorical[i], format: v => pts(v) })),
    })));
  }

  function renderInformation(ctx, grid) {
    const ids = [];
    ctx.timeline.forEach(row => Object.keys(row.beliefs || {}).forEach(id => { if (!ids.includes(id)) ids.push(id); }));
    if (!ids.length) return;
    const body = chartCard(grid, { title: "Information", description: "For every live rumour, the share of residents confidently right about it (believing a true story, or rejecting a false one) and their mean belief that it is true.", wide: true, panels: true });
    body.classList.add("is-multiples");
    const key = h("div", { class: "viz-legend shared-legend" });
    [["Confidently right", L[0]], ["Mean belief it is true", L[1]]].forEach(([label, color]) => {
      const swatch = h("i", { class: "viz-key line" });
      swatch.style.background = color;
      key.appendChild(h("span", { class: "viz-legend-item" }, swatch, h("span", { text: label })));
    });
    body.parentNode.insertBefore(key, body);
    ids.forEach(id => {
      const fact = factOf(id);
      const wrap = h("div", { class: "chart-panel" });
      const head = h("div", { class: "panel-head" }, h("h4", { text: fact ? fact.statement : humanize(id) }));
      if (fact) head.appendChild(h("span", { class: `truth is-${fact.truth ? "true" : "false"}`, text: fact.truth ? "True" : "False" }));
      const target = h("div", { class: "panel-body" });
      add(wrap, [head, target]);
      body.appendChild(wrap);
      const markers = [];
      if (fact && isNum(fact.rumor_week)) markers.push({ x: fact.rumor_week, label: "rumour", kind: "event" });
      if (fact && isNum(fact.probe_week)) markers.push({ x: fact.probe_week, label: "forecast asked", kind: "event" });
      if (fact && isNum(fact.resolve_week)) markers.push({ x: fact.resolve_week, label: "truth revealed", kind: "flourish" });
      track(V.timeChart(target, baseSpec(ctx, {
        label: `Belief in "${fact ? fact.statement : id}" by week`,
        height: 150,
        legend: false,
        markers,
        y: { min: 0, max: 1, format: v => pct(v) },
        series: [
          { label: "Confidently right", values: ctx.timeline.map(row => (row.beliefs && isNum(row.beliefs[id]) ? row.beliefs[id] : null)), color: L[0], area: true },
          { label: "Mean belief it is true", values: ctx.timeline.map(row => (row.belief_mean && isNum(row.belief_mean[id]) ? row.belief_mean[id] : null)), color: L[1] },
        ],
      })));
    });
  }

  function renderKnowledge(ctx, grid) {
    const first = ctx.timeline[0] && ctx.timeline[0].skills;
    if (!first) return;
    const skills = SKILL_ORDER.filter(s => s in first).concat(Object.keys(first).filter(s => !SKILL_ORDER.includes(s)));
    const body = chartCard(grid, { title: "Knowledge", description: "The best level at which each skill is held by a living resident. Level 2 is mastery; a skill that drops to 0 is lost to the village.", wide: true });
    track(V.heatStrip(body, {
      label: "Best skill level held in the village by week",
      x: ctx.weeks,
      group: ctx.group,
      xTitle: ctx.xTitle,
      notes: ctx.notes,
      max: 3,
      rows: skills.map(skill => ({ label: skill, values: ctx.timeline.map(row => (row.skills ? row.skills[skill] : null)) })),
      format: v => (isNum(v) && v > 0 ? `level ${v}` : "lost"),
      levelLabel: level => (level === 0 ? "Lost" : level === 2 ? "Level 2 · mastery" : `Level ${level}`),
    }));
  }

  function renderAdaptation(ctx, grid) {
    const conventions = Object.keys((ctx.timeline[0] && ctx.timeline[0].conventions) || {});
    const newcomerWeeks = ctx.frames.map(frame => (frame.residents || []).filter(r => r.status === "present" && ctx.residents.get(r.id) && ctx.residents.get(r.id).newcomer).length);
    const hasNewcomers = newcomerWeeks.some(n => n > 0);
    if (!conventions.length && !hasNewcomers) return;
    const body = chartCard(grid, { title: "Adaptation", description: "Share of residents using the convention the world now requires, and the share of newcomers integrated (two close local ties and wellbeing of 5 or more).", unit: "share" });
    const list = conventions.map((c, i) => ({ label: `${humanize(c)} convention`, values: ctx.timeline.map(row => (row.conventions && isNum(row.conventions[c]) ? row.conventions[c] : null)), color: L[i % 2 === 0 ? 0 : 2] }));
    if (hasNewcomers) list.push({ label: "Newcomers integrated", values: ctx.timeline.map((row, i) => (newcomerWeeks[i] > 0 && isNum(row.integration) ? row.integration : null)), color: L[1] });
    track(V.timeChart(body, baseSpec(ctx, {
      label: "Convention adoption and newcomer integration by week",
      height: 300,
      y: { min: 0, max: 1, format: v => pct(v) },
      series: list.slice(0, 4),
    })));
  }

  // ---- society
  function factionColor(ctx, id) {
    const faction = ctx.factions.get(id);
    return faction ? V.safeColor(faction.color, "#8a8a8a") : "#8a8a8a";
  }

  function renderSociety(ctx) {
    const network = ctx.report.network || {};
    const start = network.start || { nodes: [], links: [] };
    const end = network.end || { nodes: [], links: [] };
    const nodes = new Map();
    end.nodes.concat(start.nodes).forEach(n => { if (!nodes.has(n.id)) nodes.set(n.id, n); });
    const links = new Map();
    const merge = (list, weight) => list.forEach(l => {
      const key = [l.source, l.target].sort().join("|");
      const value = (l.strength || 0) * weight;
      if (!links.has(key) || links.get(key).strength < value) links.set(key, { source: l.source, target: l.target, strength: value });
    });
    merge(end.links, 1);
    merge(start.links, 0.8);
    const positions = V.forceLayout([...nodes.values()], [...links.values()], { groupOf: n => n.faction || "" });
    const describe = n => {
      const faction = ctx.factions.get(n.faction);
      const status = n.status === "present" ? "" : ` · ${n.status}`;
      return `${humanize(n.role)} · ${faction ? faction.name : humanize(n.faction)}${n.newcomer ? " · newcomer" : ""}${status}`;
    };
    const firstWeek = ctx.timeline[0] || {};
    const lastWeek = ctx.timeline[ctx.timeline.length - 1] || {};
    const stats = (net, row) => {
      const present = net.nodes.filter(n => n.status === "present").length;
      const close = net.links.filter(l => l.strength >= 0.3).length;
      const mean = net.links.length ? net.links.reduce((s, l) => s + l.strength, 0) / net.links.length : 0;
      return `${plural(present, "resident")} · ${plural(close, "close tie")} · mean tie ${dec(mean, 2)} · largest group ${pct(row.cohesion)}`;
    };
    $("net-start-title").textContent = `Week ${firstWeek.week ?? 0}`;
    $("net-end-title").textContent = ctx.score.collapsed ? `Week ${lastWeek.week} · collapse` : `Week ${lastWeek.week} · end`;
    $("net-start-stats").textContent = stats(start, firstWeek);
    $("net-end-stats").textContent = stats(end, lastWeek);
    const common = { positions, colorOf: n => factionColor(ctx, n.faction), describe };
    track(V.network($("net-start"), Object.assign({ nodes: start.nodes, links: start.links, label: `Social network at week ${firstWeek.week ?? 0}: ${stats(start, firstWeek)}` }, common)));
    track(V.network($("net-end"), Object.assign({ nodes: end.nodes, links: end.links, label: `Social network at week ${lastWeek.week}: ${stats(end, lastWeek)}` }, common)));
    const legend = clear($("network-legend"));
    ctx.factions.forEach(f => {
      const key = h("i", { class: "viz-key dot" });
      key.style.background = factionColor(ctx, f.id);
      legend.appendChild(h("span", { class: "viz-legend-item" }, key, h("span", { text: f.name })));
    });
    legend.appendChild(h("span", { class: "viz-legend-item" }, h("i", { class: "viz-key dot is-gone" }), h("span", { text: "Dead or departed" })));
    legend.appendChild(h("span", { class: "viz-legend-item" }, h("i", { class: "viz-key dot is-ring" }), h("span", { text: "Newcomer" })));
    legend.appendChild(h("span", { class: "viz-legend-item muted", text: "Node size: wellbeing · line weight: tie strength" }));
    renderResidents(ctx);
  }

  const FATE_ORDER = { present: 0, departed: 1, dead: 2 };
  const RESIDENT_COLUMNS = [
    { key: "name", label: "Resident", sort: r => r.name || r.id },
    { key: "fate", label: "Fate", sort: r => (FATE_ORDER[r.status] ?? 3) * 1000 + (r.ended || 0) },
    { key: "wellbeing", label: "Wellbeing", numeric: true, sort: r => r.wellbeing },
    { key: "trust", label: "Trust", numeric: true, sort: r => r.trust },
    { key: "grievance", label: "Grievance", numeric: true, sort: r => r.grievance },
    { key: "loneliness", label: "Loneliness", numeric: true, sort: r => r.loneliness },
    { key: "health", label: "Health", numeric: true, sort: r => r.health },
    { key: "coins", label: "Coins", numeric: true, sort: r => r.coins },
    { key: "ties", label: "Close ties", numeric: true, sort: r => r.ties },
    { key: "skills", label: "Skills", sort: r => Object.values(r.skills || {}).reduce((s, v) => s + v, 0) },
    { key: "concern", label: "Remaining concern", sort: r => (r.concern ? r.concern.intensity : -1) },
  ];

  function meter(value, max, invert) {
    const fill = h("i");
    const share = isNum(value) ? Math.max(0, Math.min(1, value / max)) : 0;
    fill.style.width = `${share * 100}%`;
    return h("span", { class: "meter" + (invert ? " is-invert" : ""), "aria-hidden": "true" }, fill);
  }

  function residentCell(ctx, r, key) {
    switch (key) {
      case "name": {
        const faction = ctx.factions.get(r.faction);
        const dot = h("i", { class: "faction-dot", "aria-hidden": "true" });
        dot.style.background = factionColor(ctx, r.faction);
        return h("th", { scope: "row", class: "resident-cell" },
          h("span", { class: "resident-name" }, dot, h("strong", { text: r.name || r.id }), r.newcomer ? h("span", { class: "tag", text: "newcomer" }) : null),
          h("span", { class: "resident-sub", text: `${r.age ?? "–"} · ${humanize(r.role)} · ${faction ? faction.name : humanize(r.faction)}` }),
          r.bio ? h("span", { class: "resident-bio", title: r.bio, text: r.bio }) : null);
      }
      case "fate": {
        const label = r.status === "dead" ? `Died · wk ${r.ended ?? "?"}` : r.status === "departed" ? `Left · wk ${r.ended ?? "?"}` : r.joined > 1 ? `Present · arrived wk ${r.joined}` : "Present";
        return h("td", {}, h("span", { class: `fate is-${r.status}`, text: label }));
      }
      case "wellbeing": return h("td", { class: "num" }, h("span", { text: dec(r.wellbeing, 1) }), meter(r.wellbeing, 10));
      case "health": return h("td", { class: "num", text: fmt.number(r.health, 0) });
      case "coins": return h("td", { class: "num", text: fmt.number(r.coins, 1) });
      case "ties": return h("td", { class: "num", text: r.status === "present" ? fmt.number(r.ties, 0) : "–" });
      case "skills": {
        const entries = Object.entries(r.skills || {}).filter(([, v]) => v > 0);
        return h("td", {}, entries.length ? h("span", { class: "chips" }, entries.map(([s, v]) => h("span", { class: "chip", text: `${s} ${v}` }))) : h("span", { class: "muted", text: "–" }));
      }
      case "concern": {
        const c = r.concern;
        if (!c) return h("td", {}, h("span", { class: "muted", text: "none" }));
        return h("td", { class: "concern" }, h("strong", { text: `${humanize(c.kind)} ${dec(c.intensity, 2)}` }), c.cause ? h("span", { class: "resident-sub", text: c.cause }) : null);
      }
      default: {
        const value = r[key];
        return h("td", { class: "num" }, h("span", { text: dec(value, 2) }), meter(value, 1, key !== "trust"));
      }
    }
  }

  function renderResidents(ctx) {
    const table = clear($("residents"));
    const column = RESIDENT_COLUMNS.find(c => c.key === state.sort.key) || RESIDENT_COLUMNS[1];
    const rows = (ctx.report.residents || []).slice().sort((a, b) => {
      const x = column.sort(a), y = column.sort(b);
      const order = typeof x === "string" ? String(x).localeCompare(String(y)) : (x ?? -Infinity) - (y ?? -Infinity);
      return order * state.sort.dir || String(a.name).localeCompare(String(b.name));
    });
    add(table, h("caption", { class: "sr-only", text: `Residents, sorted by ${column.label.toLowerCase()} ${state.sort.dir > 0 ? "ascending" : "descending"}` }));
    const head = h("tr");
    RESIDENT_COLUMNS.forEach(c => {
      const active = c.key === state.sort.key;
      const th = h("th", { scope: "col", class: c.numeric ? "num" : "", "aria-sort": active ? (state.sort.dir > 0 ? "ascending" : "descending") : "none" },
        h("button", { type: "button", class: "sort", onclick: () => {
          state.sort = { key: c.key, dir: active ? -state.sort.dir : (c.numeric ? -1 : 1) };
          renderResidents(ctx);
          const again = $("residents").querySelector(`th button[data-key="${c.key}"]`);
          if (again) again.focus();
        }, "data-key": c.key }, c.label, h("i", { "aria-hidden": "true", class: "sort-mark", text: active ? (state.sort.dir > 0 ? "▲" : "▼") : "" })));
      head.appendChild(th);
    });
    add(table, h("thead", {}, head));
    const body = h("tbody");
    rows.forEach(r => body.appendChild(h("tr", { class: r.status !== "present" ? "is-gone" : "" }, RESIDENT_COLUMNS.map(c => residentCell(ctx, r, c.key)))));
    add(table, body);
  }

  // ---- story graph
  function storyModel(ctx) {
    const story = ctx.report.story || {};
    const visited = story.nodes || {};
    const outcomes = new Map((story.outcomes || []).map(o => [o.node, o]));
    let nodes = [];
    const recipeStory = state.recipe && state.recipe.story && state.recipe.story.nodes;
    if (recipeStory && recipeStory.length) {
      nodes = recipeStory.map(n => ({ id: n.id, title: n.title || humanize(n.id), kind: n.kind || "event", position: n.position, edges: (n.edges || []).map(e => ({ to: e.to, label: e.label || "" })), outcome: n.outcome || null }));
    }
    const known = new Set(nodes.map(n => n.id));
    const extra = new Set();
    Object.keys(visited).forEach(id => { if (!known.has(id)) extra.add(id); });
    (story.path || []).forEach(step => [step.source, step.target].forEach(id => { if (!known.has(id)) extra.add(id); }));
    const pathEdges = new Map();
    (story.path || []).forEach((step, i) => pathEdges.set(`${step.source}>${step.target}`, Object.assign({ order: i + 1 }, step)));
    extra.forEach(id => {
      const outcome = outcomes.get(id);
      nodes.push({ id, title: humanize(id), kind: outcome ? "outcome" : "event", position: null, edges: (story.path || []).filter(s => s.source === id).map(s => ({ to: s.target, label: s.label })), outcome: outcome ? { type: outcome.type, label: outcome.label, reward: outcome.reward } : null });
    });
    // Automatic layout for nodes without a position: one row per thread, depth along the path.
    const missing = nodes.filter(n => !n.position || !isNum(n.position.x) || !isNum(n.position.y));
    if (missing.length) {
      const byId = new Map(missing.map(n => [n.id, n]));
      const incoming = new Set(missing.flatMap(n => n.edges.map(e => e.to)));
      const maxY = Math.max(-70, ...nodes.filter(n => n.position && isNum(n.position.y)).map(n => n.position.y));
      const week = id => (visited[id] && isNum(visited[id].activated) ? visited[id].activated : 999);
      const placed = new Set();
      let row = 0;
      const place = (id, depth) => {
        const node = byId.get(id);
        if (!node || placed.has(id)) return;
        placed.add(id);
        node.position = { x: 40 + depth * 230, y: maxY + 90 + row * 72 };
        const children = node.edges.map(e => e.to).filter(to => byId.has(to) && !placed.has(to));
        if (!children.length) row += 1;
        children.forEach(child => place(child, depth + 1));
      };
      missing.filter(n => !incoming.has(n.id)).sort((a, b) => week(a.id) - week(b.id)).forEach(n => place(n.id, 0));
      missing.filter(n => !placed.has(n.id)).forEach(n => place(n.id, 0));
    }
    return { nodes, visited, outcomes, pathEdges, path: story.path || [] };
  }

  function wrapTitle(text, max) {
    const words = String(text).split(/\s+/);
    const lines = [""];
    words.forEach(word => {
      const current = lines[lines.length - 1];
      if ((current + " " + word).trim().length <= max) lines[lines.length - 1] = (current + " " + word).trim();
      else lines.push(word);
    });
    if (lines.length > 2) {
      lines.length = 2;
      lines[1] = lines[1].slice(0, max - 1) + "…";
    }
    return lines;
  }

  function rewardText(reward) {
    return Object.entries(reward || {}).map(([d, v]) => `+${fmt.number(v, 0)} ${d}`).join(" ");
  }

  function renderStory(ctx) {
    const note = $("story-note");
    note.textContent = state.recipeProblem || "";
    note.hidden = !state.recipeProblem;
    const model = storyModel(ctx);
    const container = clear($("story-graph"));
    if (!model.nodes.length) {
      container.appendChild(h("p", { class: "muted pad", text: "This recipe has no story graph." }));
      clear($("turning-points"));
      return;
    }
    const NW = 182, NH = 52, PAD = 24;
    const xs = model.nodes.map(n => n.position.x), ys = model.nodes.map(n => n.position.y);
    const minX = Math.min(...xs) - PAD, minY = Math.min(...ys) - PAD - 8;
    const spanX = Math.max(...xs) + NW + PAD - minX, spanY = Math.max(...ys) + NH + PAD - minY;
    const available = Math.max(280, container.clientWidth || 900);
    const scale = Math.max(0.78, Math.min(1.1, (available - 16) / spanX));
    const svg = V.svg("svg", { viewBox: `${minX} ${minY} ${spanX} ${spanY}`, width: Math.round(spanX * scale * state.storyZoom), height: Math.round(spanY * scale * state.storyZoom), class: "story-svg", role: "img", "aria-label": `Story graph: ${model.path.length} transitions taken, ${Object.keys(model.visited).length} situations reached` });
    state.storyFrame = { svg, spanX, spanY, scale, fit: Math.min(1, (available - 16) / spanX) / scale };
    const defs = V.svg("defs", null, svg);
    const markerId = `arrow-${state.seed}`;
    [["", "story-arrow"], ["-taken", "story-arrow is-taken"]].forEach(([suffix, cls]) => {
      const marker = V.svg("marker", { id: markerId + suffix, viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse" }, defs);
      V.svg("path", { d: "M0,1L10,5L0,9Z", class: cls }, marker);
    });
    const byId = new Map(model.nodes.map(n => [n.id, n]));
    const edgeLayer = V.svg("g", { class: "story-edges" }, svg);
    const badgeLayer = V.svg("g", { class: "story-badges" }, svg);
    const animate = !V.prefersReducedMotion();
    model.nodes.forEach(node => node.edges.forEach(edge => {
      const target = byId.get(edge.to);
      if (!target) return;
      const taken = model.pathEdges.get(`${node.id}>${edge.to}`);
      const x1 = node.position.x + NW, y1 = node.position.y + NH / 2;
      let x2 = target.position.x, y2 = target.position.y + NH / 2;
      let d;
      if (x2 >= x1 - 4) {
        const bend = Math.max(30, (x2 - x1) / 2);
        d = `M${x1},${y1}C${x1 + bend},${y1} ${x2 - bend},${y2} ${x2},${y2}`;
      } else {
        const sx = node.position.x + NW / 2, sy = node.position.y + (y2 > y1 ? NH : 0);
        x2 = target.position.x + NW / 2;
        y2 = target.position.y + (y2 > y1 ? 0 : NH);
        d = `M${sx},${sy}C${sx},${(sy + y2) / 2} ${x2},${(sy + y2) / 2} ${x2},${y2}`;
      }
      const path = V.svg("path", { d, class: "story-edge" + (taken ? " is-taken" : ""), "marker-end": `url(#${markerId}${taken ? "-taken" : ""})` }, edgeLayer);
      V.svgText({}, `${node.title} → ${target.title}${edge.label ? ` (${edge.label})` : ""}${taken ? `, week ${taken.week}` : ""}`, V.svg("title", null, path));
      if (taken) {
        if (animate) {
          path.setAttribute("pathLength", "1");
          path.classList.add("viz-draw");
          path.style.animationDelay = `${0.25 + taken.order * 0.12}s`;
        }
        const mx = (node.position.x + NW + target.position.x) / 2;
        const my = (y1 + target.position.y + NH / 2) / 2;
        const badge = V.svg("g", { class: "story-badge", transform: `translate(${mx},${my})` }, badgeLayer);
        V.svg("circle", { r: 9 }, badge);
        V.svgText({ y: 3.5, "text-anchor": "middle" }, String(taken.order), badge);
      }
    }));
    const nodeLayer = V.svg("g", { class: "story-nodes" }, svg);
    model.nodes.forEach(node => {
      const status = model.visited[node.id];
      const outcome = model.outcomes.get(node.id) || null;
      const type = (outcome && outcome.type) || (node.outcome && node.outcome.type) || null;
      const classes = ["story-node", `is-${node.kind}`, status ? "is-visited" : "is-dormant"];
      if (type) classes.push(`is-${type}`);
      if (outcome) classes.push("is-reached");
      const g = V.svg("g", { class: classes.join(" "), transform: `translate(${node.position.x},${node.position.y})` }, nodeLayer);
      V.svg("rect", { width: NW, height: NH, rx: 9 }, g);
      const lines = wrapTitle(node.title, 24);
      lines.forEach((line, i) => V.svgText({ x: 12, y: lines.length > 1 ? 18 + i * 14 : 22, class: "story-title" }, line, g));
      let detail = "";
      if (status) detail = `wk ${status.activated}${status.status === "active" ? " · still active" : ""}`;
      if (type === "flourish") detail = (detail ? detail + " · " : "") + (rewardText((outcome || node.outcome).reward) || "flourish");
      else if (type === "collapse") detail = (detail ? detail + " · " : "") + "collapse";
      else if (!status) detail = node.kind;
      if (lines.length < 2 || status || type) V.svgText({ x: 12, y: lines.length > 1 ? NH - 3 : 38, class: "story-detail" }, detail, g);
      const tooltip = `${node.title} (${node.kind})${status ? `, reached in week ${status.activated}` : ", not reached"}${outcome ? ` — ${outcome.label}` : node.outcome ? ` — ${node.outcome.label}` : ""}`;
      V.svgText({}, tooltip, V.svg("title", null, g));
      g.dataset.title = node.title;
      g.dataset.detail = `${humanize(node.kind)}${status ? ` · reached in week ${status.activated}${status.status === "active" ? ", still active" : ""}` : " · not reached"}${outcome ? ` · ${outcome.label}` : node.outcome && node.outcome.label ? ` · ${node.outcome.label}` : ""}`;
    });
    container.appendChild(svg);
    renderTurningPoints(ctx, model);
  }

  /** Zoom the story graph by resizing its SVG; the scroll position keeps the same centre. */
  function zoomStory(factor, fit) {
    const frame = state.storyFrame;
    if (!frame || !frame.svg.isConnected) return;
    const box = $("story-graph");
    const cx = (box.scrollLeft + box.clientWidth / 2) / Math.max(1, box.scrollWidth);
    const cy = (box.scrollTop + box.clientHeight / 2) / Math.max(1, box.scrollHeight);
    state.storyZoom = fit ? frame.fit : Math.max(0.3, Math.min(2.5, state.storyZoom * factor));
    frame.svg.setAttribute("width", String(Math.round(frame.spanX * frame.scale * state.storyZoom)));
    frame.svg.setAttribute("height", String(Math.round(frame.spanY * frame.scale * state.storyZoom)));
    box.scrollLeft = cx * box.scrollWidth - box.clientWidth / 2;
    box.scrollTop = cy * box.scrollHeight - box.clientHeight / 2;
    hideStoryPop();
  }

  function hideStoryPop() {
    const pop = document.querySelector(".story-pop");
    if (pop) pop.remove();
  }

  /** Tap (or click) a story node to read what happened there: the <title> tooltip needs a mouse. */
  function bindStoryTools() {
    $("story-zoom-in").addEventListener("click", () => zoomStory(1.25));
    $("story-zoom-out").addEventListener("click", () => zoomStory(1 / 1.25));
    $("story-zoom-fit").addEventListener("click", () => zoomStory(1, true));
    const box = $("story-graph");
    box.addEventListener("click", event => {
      const node = event.target.closest && event.target.closest(".story-node");
      hideStoryPop();
      if (!node || !node.dataset.title) return;
      const card = box.parentElement;
      const pop = h("div", { class: "story-pop", role: "status" }, h("strong", { text: node.dataset.title }), h("span", { text: node.dataset.detail || "" }));
      card.appendChild(pop);
      const cardBox = card.getBoundingClientRect();
      const nodeBox = node.getBoundingClientRect();
      const left = Math.max(8, Math.min(nodeBox.left - cardBox.left, cardBox.width - pop.offsetWidth - 8));
      const below = nodeBox.bottom - cardBox.top + 8;
      const top = below + pop.offsetHeight > cardBox.height - 4 ? Math.max(8, nodeBox.top - cardBox.top - pop.offsetHeight - 8) : below;
      pop.style.left = `${Math.round(left)}px`;
      pop.style.top = `${Math.round(top)}px`;
    });
    box.addEventListener("scroll", hideStoryPop, { passive: true });
    document.addEventListener("pointerdown", event => { if (!box.contains(event.target)) hideStoryPop(); });
  }

  function renderTurningPoints(ctx, model) {
    const list = clear($("turning-points"));
    const items = [];
    Object.entries(model.visited).forEach(([id, status]) => {
      if (!status.via && isNum(status.activated)) {
        const node = model.nodes.find(n => n.id === id);
        items.push({ week: status.activated, order: 0, title: node ? node.title : humanize(id), detail: node && node.kind === "crisis" ? "A crisis begins" : "", kind: node ? node.kind : "event" });
      }
    });
    model.path.forEach((step, i) => {
      const source = model.nodes.find(n => n.id === step.source);
      const target = model.nodes.find(n => n.id === step.target);
      const outcome = model.outcomes.get(step.target);
      items.push({
        week: step.week,
        order: 1 + i,
        badge: i + 1,
        title: `${source ? source.title : humanize(step.source)} → ${target ? target.title : humanize(step.target)}`,
        detail: [step.label, outcome ? `${outcome.label}${outcome.type === "flourish" && rewardText(outcome.reward) ? ` (${rewardText(outcome.reward)})` : ""}` : ""].filter(Boolean).join(" · "),
        kind: outcome ? outcome.type : (target ? target.kind : "event"),
      });
    });
    model.outcomes.forEach(outcome => {
      if (!model.path.some(step => step.target === outcome.node)) {
        items.push({ week: outcome.week, order: 99, title: outcome.label, detail: outcome.type === "flourish" ? rewardText(outcome.reward) : "collapse", kind: outcome.type });
      }
    });
    items.sort((a, b) => a.week - b.week || a.order - b.order);
    if (!items.length) {
      list.appendChild(h("li", { class: "muted", text: "No authored situation was reached." }));
      return;
    }
    items.forEach(item => list.appendChild(h("li", { class: `turn is-${item.kind}` },
      h("span", { class: "turn-week", text: `wk ${item.week}` }),
      h("div", { class: "turn-body" },
        h("strong", {}, item.badge ? h("span", { class: "turn-badge", text: String(item.badge) }) : null, item.title),
        item.detail ? h("span", { class: "turn-detail", text: item.detail }) : null))));
  }

  // ---- steward log (what the AI did, week by week)
  const VERB_ICONS = {
    talk: "M3 3.5h10v6.5H8.5L5.5 13v-3H3z",
    announce: "M2.5 6.5v3h2l5 3v-9l-5 3z M11.5 5.5c1.3 1.3 1.3 3.7 0 5",
    give: "M2.5 7h11v6.5h-11z M2 4.5h12V7H2z M8 4.5v9 M8 4.5C7 2.5 4.5 2.5 5 4.5 M8 4.5c1-2 3.5-2 3 0",
    trade: "M2.5 5.5h10l-2.5-2.5 M13.5 10.5h-10l2.5 2.5",
    inspect: "M7 2.5a4.5 4.5 0 1 0 0 9a4.5 4.5 0 1 0 0-9z M10.3 10.3l3.2 3.2",
    assign: "M2 8h6.5 M6.5 5.5 9 8l-2.5 2.5 M12 3.5a1.8 1.8 0 1 0 0.01 0 M9.8 13c0-2 1-3 2.2-3s2.2 1 2.2 3",
    build: "M2.5 13.5l6.5-6.5 M7.5 4.5l4 4 M6.5 5.5l3-3 4 4-3 3",
    propose: "M4 2h6l2.5 2.5V14H4z M6 6.5h4.5 M6 9h4.5 M6 11.5h3",
    decree: "M4 2h6l2.5 2.5V14H4z M6 6.5h4.5 M6 9h4.5 M10 11.5a1.2 1.2 0 1 0 0.01 0",
    mediate: "M8 2v12 M4.5 14h7 M3 4.5h10 M3 4.5 1.3 8.5h3.4z M13 4.5l-1.7 4h3.4z",
    introduce: "M6 8h4 M5 5a3 3 0 1 0 0 6 M11 5a3 3 0 1 1 0 6",
    promise: "M2.5 8.5l3.5 3.5 7.5-8",
    gather: "M5 4a1.8 1.8 0 1 0 0.01 0 M11 4a1.8 1.8 0 1 0 0.01 0 M1.8 12.5c0-2.2 1.4-3.5 3.2-3.5s3.2 1.3 3.2 3.5 M7.8 12.5c0-2.2 1.4-3.5 3.2-3.5s3.2 1.3 3.2 3.5",
    convention: "M12.5 6A4.8 4.8 0 0 0 4 4.6 M3.5 10a4.8 4.8 0 0 0 8.5 1.4 M3.6 2v3h3 M12.4 14v-3h-3",
  };

  function verbIcon(verb) {
    const icon = V.svg("svg", { viewBox: "0 0 16 16", width: 16, height: 16, class: "verb-icon", "aria-hidden": "true" });
    V.svg("path", { d: VERB_ICONS[verb] || "M8 6.5a1.5 1.5 0 1 0 0.01 0" }, icon);
    return icon;
  }

  function namesOf(ids, names) {
    return (ids || []).map(id => (names.get(id) || nameFor(id) || id)).join(", ");
  }

  function inspectTarget(target) {
    const [kind, key] = String(target || "").split(":");
    if (kind === "fact" || kind === "rumour") {
      const fact = factOf(key);
      return fact ? `the rumour “${fact.statement}”` : `the rumour ${humanize(key).toLowerCase()}`;
    }
    if (kind === "lake" || kind === "commons") return "the lake";
    if (kind === "goods") return `the ${key || "goods"} on sale`;
    if (kind === "resident") return nameFor(key) || key;
    return humanize(String(target || "something").replace(":", " ")).toLowerCase();
  }

  function actionTitle(a, names) {
    const p = a.params || {};
    const who = namesOf(a.targets, names);
    const v = a.verb || "";
    const text = {
      talk: () => `Talks with ${who || "someone"}${p.approach ? ` · ${p.approach}` : ""}${p.topic && p.topic !== "general" ? ` · about ${humanize(p.topic).toLowerCase()}` : ""}`,
      give: () => `Gives ${p.amount ?? ""} ${p.item || ""} to ${who || "someone"}`.replace(/\s+/g, " "),
      trade: () => `${p.side === "sell" ? "Sells" : "Buys"} ${p.qty ?? ""} ${p.item || "goods"}${p.with ? ` · ${p.with === "market" ? "local market" : humanize(p.with)}` : ""}${isNum(p.price) ? ` at ${p.price}` : ""}`,
      inspect: () => `Inspects ${who || inspectTarget(p.target)}`,
      assign: () => `Assigns ${who || "someone"} to ${humanize(String(p.task || "work").split(":")[0]).toLowerCase()}`,
      build: () => `Starts building ${humanize(p.project || "a project").toLowerCase()}`,
      propose: () => `Proposes ${p.policy || "a policy"} = ${valueText(p.value)}`,
      decree: () => `Decrees ${p.policy || "a policy"} = ${valueText(p.value)}`,
      mediate: () => `Mediates between ${who || "two residents"}${p.resolution ? ` · ${humanize(p.resolution).toLowerCase()}` : ""}`,
      introduce: () => `Introduces ${who || "residents"}`,
      promise: () => `Promises ${who || "someone"}${p.topic ? ` · ${humanize(p.topic).toLowerCase()}` : ""}`,
      gather: () => `Calls a ${humanize(p.kind || "gathering").toLowerCase()}`,
      announce: () => {
        const fact = factOf(p.topic);
        if (fact) return `Announces that “${fact.statement}” is ${p.stance || "unclear"}`;
        return `Announces${p.topic ? ` the ${humanize(p.topic).toLowerCase()}` : ""}${p.stance ? `: ${p.stance}` : ""}`;
      },
      convention: () => `Adopts the ${p.domain || ""} convention “${p.variant || "?"}”`,
    }[v];
    if (text && (a.params || a.targets)) return text();
    return a.label || a.action || humanize(v) || "Action";
  }

  function stewardLog(logNode, notebookNode, castNames) {
    const groups = new Map();
    let frames = [];
    let current = -1;
    clear(logNode);
    clear(notebookNode);
    const list = h("ol", { class: "log-list" });
    logNode.appendChild(list);
    const empty = h("p", { class: "muted log-empty", text: "The steward has not acted yet." });
    logNode.appendChild(empty);

    function group(frame, index) {
      const names = castNames();
      const actions = Array.isArray(frame.actions) ? frame.actions : [];
      const steward = frame.steward || {};
      const item = h("li", { class: "log-week", "data-index": String(index) },
        h("header", { class: "log-head" }, h("span", { class: "turn-week", text: `wk ${frame.week}` }), frame.season ? h("span", { class: "muted", text: frame.season }) : null,
          frame.collapsed ? h("span", { class: "badge is-bad", text: "collapse" }) : null));
      if (steward.error) item.appendChild(h("p", { class: "parse-error", text: `The answer could not be read: ${steward.error}` }));
      const rows = h("ul", { class: "log-actions" });
      actions.forEach((a, i) => {
        const rejected = a.status && a.status !== "done";
        rows.appendChild(h("li", { class: "log-action" + (rejected ? " is-rejected" : ""), "data-action": String(isNum(a.index) ? a.index : i) },
          h("span", { class: "log-icon" }, verbIcon(a.verb)),
          h("div", { class: "log-body" },
            h("p", { class: "log-title" }, h("strong", { text: actionTitle(a, names) }), rejected ? h("span", { class: "badge is-bad", text: a.status }) : null),
            a.say ? h("q", { class: "log-say", text: a.say }) : null,
            a.text ? h("p", { class: "log-outcome", text: a.text }) : null)));
      });
      if (!actions.length && !steward.error) rows.appendChild(h("li", { class: "muted log-none", text: frame.collapsed ? "No action: the society collapsed." : "No action this week." }));
      item.appendChild(rows);
      return item;
    }

    function notebook(index) {
      clear(notebookNode);
      let at = Math.min(index, frames.length - 1);
      while (at > 0 && !(frames[at] && frames[at].steward)) at -= 1;
      if (at < 0) at = 0;
      const frame = frames[at];
      const steward = frame && frame.steward;
      notebookNode.appendChild(h("p", { class: "notebook-title" }, "Steward's notebook", frame && frame.week ? h("span", { class: "muted", text: ` · week ${frame.week}` }) : null));
      if (steward && steward.note) notebookNode.appendChild(h("p", { class: "notebook-note", text: steward.note }));
      else notebookNode.appendChild(h("p", { class: "notebook-note is-empty", text: steward ? "The steward left no note this week." : "Nothing written yet." }));
      const forecasts = steward && steward.forecasts && typeof steward.forecasts === "object" ? Object.entries(steward.forecasts) : [];
      if (forecasts.length) {
        notebookNode.appendChild(h("p", { class: "notebook-forecasts" }, h("strong", { text: "Forecasts · " }),
          forecasts.map(([k, v]) => { const fact = factOf(k); return `${fact ? `“${fact.statement}”` : humanize(k)} ${isNum(v) ? pct(v) : valueText(v)}`; }).join(" · ")));
      }
    }

    notebook(-1);

    const api = {
      add(more) {
        more.forEach(frame => {
          const index = frames.length;
          frames.push(frame);
          if (index === 0 && !frame.actions && !frame.steward) return;
          if (frame.week === 0) return;
          const node = group(frame, index);
          groups.set(index, node);
          list.appendChild(node);
        });
        empty.hidden = groups.size > 0;
        if (current < 0) notebook(frames.length - 1);
      },
      reset(all) {
        frames = [];
        groups.clear();
        clear(list);
        current = -1;
        api.add(all || []);
      },
      current(index, scroll) {
        if (!frames.length) return;
        const i = Math.max(0, Math.min(index, frames.length - 1));
        if (current === i) return;
        current = i;
        list.querySelectorAll(".log-week.is-current").forEach(node => node.classList.remove("is-current"));
        list.querySelectorAll(".log-action.is-speaking").forEach(node => node.classList.remove("is-speaking"));
        let node = groups.get(i);
        if (!node) {
          for (let k = i; k >= 0 && !node; k -= 1) node = groups.get(k);
        }
        if (node) {
          node.classList.add("is-current");
          if (scroll !== false) {
            const top = node.offsetTop - list.offsetTop - 6;
            logNode.scrollTo({ top: Math.max(0, top), behavior: V.prefersReducedMotion() ? "auto" : "smooth" });
          }
        }
        notebook(i);
      },
      rescroll() {
        const node = list.querySelector(".log-week.is-current");
        if (node) logNode.scrollTop = Math.max(0, node.offsetTop - list.offsetTop - 6);
      },
      highlight(frameIndex, actionIndex) {
        const node = groups.get(frameIndex);
        if (!node) return;
        list.querySelectorAll(".log-action.is-speaking").forEach(n => n.classList.remove("is-speaking"));
        const row = node.querySelector(`.log-action[data-action="${actionIndex}"]`);
        if (row) row.classList.add("is-speaking");
      },
      get length() { return frames.length; },
      node: logNode,
    };
    return api;
  }

  // ---- playback panel: optional SocietyVillage + controls + steward log
  function frameIndexFrom(arg, frames) {
    if (isNum(arg)) return arg;
    if (arg && isNum(arg.index)) return arg.index;
    if (arg && isNum(arg.frameIndex)) return arg.frameIndex;
    const week = arg && (isNum(arg.week) ? arg.week : arg.frame && isNum(arg.frame.week) ? arg.frame.week : null);
    if (isNum(week)) {
      const i = frames.findIndex(f => f.week === week);
      return i >= 0 ? i : null;
    }
    return null;
  }

  function createPlayback(prefix, options) {
    const o = options || {};
    const lib = window.SocietyVillage;
    const panel = {
      prefix,
      frames: [],
      cast: o.cast || [],
      controller: null,
      playing: false,
      live: Boolean(o.live),
      onFrame: o.onFrame || null,
    };
    const castNames = () => new Map((panel.cast || []).map(r => [r.id, r.name]));
    panel.log = stewardLog($(`${prefix}log`), $(`${prefix}notebook`), castNames);
    const stage = clear($(`${prefix}village`));
    const call = (method, ...args) => {
      const c = panel.controller;
      if (!c || typeof c[method] !== "function") return undefined;
      try {
        return c[method](...args);
      } catch (error) {
        console.warn(`SocietyVillage.${method} failed`, error);
        return undefined;
      }
    };
    panel.call = call;
    if (lib && typeof lib.mount === "function") {
      try {
        panel.controller = lib.mount(stage, { autoplay: false, loop: false, notebook: false, expand: true });
      } catch (error) {
        console.warn("SocietyVillage could not be mounted", error);
        panel.controller = null;
        clear(stage);
      }
    }
    panel.hasVillage = Boolean(panel.controller);

    panel.setPlaying = playing => {
      panel.playing = playing;
      const button = $(`${prefix}play`);
      button.textContent = playing ? "Pause" : "Play";
      button.setAttribute("aria-pressed", String(playing));
    };

    panel.readout = index => {
      const frame = panel.frames[index];
      if (!frame) return;
      const scrub = $(`${prefix}scrub`);
      scrub.max = String(Math.max(0, panel.frames.length - 1));
      scrub.value = String(index);
      const present = (frame.residents || []).filter(r => r.status === "present").length;
      const text = `Week ${frame.week}${frame.season ? ` · ${frame.season}` : ""}${present ? ` · ${plural(present, "resident")}` : ""}`;
      $(`${prefix}readout`).textContent = text;
      scrub.setAttribute("aria-valuetext", text);
    };

    panel.show = (index, origin) => {
      if (!panel.frames[index]) return;
      panel.index = index;
      panel.readout(index);
      const sheet = state.sheets[prefix];
      if (sheet) {
        const frame = panel.frames[index];
        const acts = Array.isArray(frame.actions) ? frame.actions.length : 0;
        sheet.setTitle(`Steward log · week ${frame.week}`);
        const button = $(`${prefix}log-btn`);
        button.textContent = acts ? `Log · ${acts}` : "Log";
        button.setAttribute("aria-label", `Steward log, week ${frame.week}${acts ? `, ${plural(acts, "action")}` : ""}`);
      }
      panel.log.current(index, origin !== "init");
      if (panel.onFrame && origin !== "init") panel.onFrame(panel.frames[index], index, origin);
    };

    if (panel.controller && typeof panel.controller.on === "function") {
      call("on", "frame", arg => {
        if (panel.silent) return;
        const index = frameIndexFrom(arg, panel.frames);
        if (index === null || !panel.frames[index]) return;
        panel.show(index, "village");
      });
      call("on", "action", arg => {
        if (!arg) return;
        const frameIndex = frameIndexFrom(arg, panel.frames);
        const action = arg.action || {};
        if (frameIndex !== null && isNum(action.index)) panel.log.highlight(frameIndex, action.index);
      });
      call("on", "play", () => panel.setPlaying(true));
      call("on", "pause", () => panel.setPlaying(false));
      call("on", "end", () => panel.setPlaying(false));
    }

    const load = () => call("load", { frames: panel.frames, residents: panel.cast, factions: o.factions || [], recipe: state.recipe });

    panel.add = (more, cast) => {
      if (cast && cast.length) panel.cast = cast;
      if (!more || !more.length) return;
      const first = panel.frames.length === 0;
      const previous = panel.index || 0;
      panel.frames = panel.frames.concat(more);
      panel.log.add(more);
      const last = panel.frames.length - 1;
      const follow = panel.live && $(`${prefix}follow`).checked;
      let target = panel.live && follow ? last : first ? 0 : previous;
      if (panel.hasVillage) {
        panel.silent = true;
        if (first) {
          load();
          if (panel.live && follow) {
            call("setFollowLive", true);
            call("seek", last);
          }
        } else if (typeof panel.controller.append === "function") {
          call("append", more);
          if (follow && typeof panel.controller.setFollowLive !== "function") call("seek", last);
          if (follow && typeof panel.controller.setFollowLive === "function") target = null;
        } else {
          load();
          call("seek", target);
        }
        panel.silent = false;
      }
      if (target !== null) panel.show(target, first ? "init" : "data");
      else panel.readout(panel.index || 0);
    };

    panel.seek = index => {
      if (panel.hasVillage) call("seek", index);
      panel.show(index, "seek");
    };

    panel.destroy = () => {
      call("destroy");
      panel.controller = null;
    };

    $(`${prefix}stage-card`) && ($(`${prefix}stage-card`).hidden = !panel.hasVillage);
    $(`${prefix}play`).disabled = !panel.hasVillage;
    $(`${prefix}speed`).disabled = !panel.hasVillage;
    call("setSpeed", Number($(`${prefix}speed`).value) || 1);
    panel.setPlaying(false);
    if (o.frames && o.frames.length) panel.add(o.frames, o.cast);
    return panel;
  }

  function bindPlaybackControls(prefix) {
    const panel = () => state.panels[prefix];
    $(`${prefix}play`).addEventListener("click", () => {
      const p = panel();
      if (!p || !p.hasVillage) return;
      if (!p.playing && (p.index || 0) >= p.frames.length - 1) p.seek(0);
      p.call(p.playing ? "pause" : "play");
      const playing = typeof p.controller.isPlaying === "function" ? Boolean(p.controller.isPlaying()) : !p.playing;
      p.setPlaying(playing);
      if (playing && p.live) {
        $(`${prefix}follow`).checked = false;
        p.call("setFollowLive", false);
      }
    });
    $(`${prefix}speed`).addEventListener("change", event => {
      const p = panel();
      if (p) p.call("setSpeed", Number(event.target.value) || 1);
    });
    $(`${prefix}scrub`).addEventListener("input", event => {
      const p = panel();
      if (!p) return;
      if (p.live) {
        const atEnd = Number(event.target.value) >= p.frames.length - 1;
        $(`${prefix}follow`).checked = atEnd;
        p.call("setFollowLive", atEnd);
      }
      p.seek(Number(event.target.value));
    });
    const follow = $(`${prefix}follow`);
    follow.addEventListener("change", () => {
      const p = panel();
      if (!p || !p.live) return;
      p.call("setFollowLive", follow.checked);
      if (follow.checked && p.frames.length) p.seek(p.frames.length - 1);
    });
  }

  function renderReplay(ctx) {
    const section = $("replay");
    section.hidden = !ctx.frames.length;
    if (section.hidden) return;
    const panel = createPlayback("replay-", {
      frames: ctx.frames,
      cast: ctx.report.residents || [],
      factions: [...ctx.factions.values()],
      onFrame: (frame, index, origin) => {
        if (origin === "decision" || !$("replay-follow").checked || frame.week < 1) return;
        clearTimeout(state.syncTimer);
        state.syncTimer = setTimeout(() => showDecision(Math.min(frame.week, state.decisionMax), "replay"), panel.playing ? 450 : 80);
      },
    });
    state.panels["replay-"] = panel;
    $("replay-title").textContent = panel.hasVillage ? "Watch the year unfold" : "What the steward did, week by week";
    section.querySelector(".stage-layout").classList.toggle("no-village", !panel.hasVillage);
  }

  function seekReplayToWeek(week) {
    const panel = state.panels["replay-"];
    if (!panel || !panel.frames.length) return;
    const index = panel.frames.findIndex((f, i) => i > 0 && f.week === week);
    if (index < 0 || panel.index === index) return;
    if (panel.hasVillage) panel.call("seek", index);
    panel.show(index, "decision");
  }

  // ------------------------------------------------------------------ live mode
  const LIVE_METRICS = [
    { key: "population", label: "Population", read: f => f.metrics && f.metrics.population, format: v => fmt.number(v, 0), step: true },
    { key: "wellbeing", label: "Mean wellbeing", read: f => f.metrics && f.metrics.wellbeing, format: v => dec(v, 2), y: { min: 0, max: 10 } },
    { key: "trust", label: "Trust in the steward", read: f => f.metrics && f.metrics.trust, format: v => dec(v, 2), y: { min: 0, max: 1 } },
    { key: "protest", label: "Protesting", read: f => f.metrics && f.metrics.protest, format: v => pct(v), y: { min: 0, max: 1 }, threshold: () => collapseRules().revolt_share },
    { key: "commons", label: "Lake stock", read: f => f.metrics && f.metrics.commons, format: v => pct(v), y: { min: 0, max: 1 }, threshold: () => state.recipe && state.recipe.world && state.recipe.world.commons && state.recipe.world.commons.critical },
    { key: "points", label: "Running points", read: f => (f.points ? Object.values(f.points).reduce((s, v) => s + (isNum(v) ? v : 0), 0) : null), format: v => pts(v), area: true },
  ];

  function liveLede() {
    const run = state.run;
    const entry = seedEntry() || {};
    const lede = {
      running: "The steward is governing. Indicators refresh every few seconds; the full report appears here when the run ends.",
      paused: "The run is paused. Resume it to continue from the decision log; nothing already decided is lost.",
      interrupted: "The server stopped while this run was active. Resume it: the engine replays the decision log deterministically and continues.",
      failed: "The run stopped with an error. Resume to retry from the decision log.",
      completed: "This seed has finished; its report is loading.",
    }[run.status] || "";
    $("live-lede").textContent = lede + (isMultiSeed() ? ` Showing seed ${state.seed} (${seedSummary(entry)}).` : "");
  }

  function showLive() {
    liveLede();
    const grid = clear($("live-charts"));
    LIVE_METRICS.forEach(metric => {
      const value = h("strong", { class: "mini-value", text: "–" });
      const delta = h("span", { class: "mini-delta" });
      const body = h("div", { class: "mini-chart" });
      grid.appendChild(h("article", { class: "card mini-card" }, h("header", {}, h("h3", { text: metric.label }), h("p", {}, value, delta)), body));
      state.liveCharts[metric.key] = null;
      metric.nodes = { value, delta, body };
    });
    const factions = (state.recipe && state.recipe.factions) || [];
    const panel = createPlayback("live-", { live: true, factions, cast: state.liveCast, frames: state.liveFrames });
    state.panels["live-"] = panel;
    $("live-stage").classList.toggle("no-village", !panel.hasVillage);
    $("live-log-title").textContent = panel.hasVillage ? "Steward log" : "Steward log · live";
    renderLiveData();
  }

  function liveProgress() {
    liveLede();
    const run = state.run;
    const entry = seedEntry() || {};
    const week = entry.week || 0;
    $("live-week").textContent = `Week ${week}`;
    $("live-of").textContent = ` of ${run.weeks}`;
    const progress = run.progress || { weeks: week, total: run.weeks };
    const share = progress.total ? progress.weeks / progress.total : 0;
    const bar = $("live-progress");
    bar.setAttribute("aria-valuenow", String(Math.round(share * 100)));
    bar.setAttribute("aria-valuetext", `${progress.weeks} of ${progress.total} weeks across all seeds`);
    bar.querySelector("i").style.width = `${share * 100}%`;
    $("live-updated").textContent = run.updated_at ? `updated ${new Date(run.updated_at).toLocaleTimeString()}` : "";
    const seeds = clear($("seed-progress"));
    if (isMultiSeed()) {
      seedKeys().forEach(key => {
        const s = seedEntry(key);
        const fill = h("i");
        fill.style.width = `${Math.min(100, ((s.week || 0) / run.weeks) * 100)}%`;
        seeds.appendChild(h("div", { class: "seed-row" + (key === state.seed ? " is-current" : "") }, h("span", { text: `Seed ${key}` }), h("span", { class: "seed-bar" }, fill), h("span", { class: "muted", text: seedSummary(s) })));
      });
    }
  }

  function renderLiveData() {
    if (state.view !== "live") return;
    liveProgress();
    const frames = state.liveFrames || [];
    const entry = seedEntry() || {};
    const snapshot = $("live-snapshot");
    snapshot.hidden = frames.length > 0 || !entry.metrics;
    if (!snapshot.hidden) {
      clear(snapshot);
      const m = entry.metrics;
      add(snapshot, [
        h("p", { class: "tiles-note muted", text: `${state.liveProblem || "The week-by-week history is not available yet."} The last recorded snapshot:` }),
        factTile("Population", fmt.number(m.population, 0)),
        factTile("Mean wellbeing", dec(m.wellbeing, 2)),
        factTile("Trust", dec(m.trust, 2)),
        factTile("Legitimacy", dec(m.legitimacy, 2)),
        factTile("Protesting", pct(m.protest)),
        factTile("Lake stock", pct(m.commons)),
        factTile("Treasury", fmt.number(m.treasury, 0)),
        factTile("Granary", fmt.number(m.granary, 0)),
        factTile("Gini", dec(m.gini, 2)),
        factTile("Running points", pts(entry.points)),
      ]);
    }
    $("live-charts").hidden = !frames.length;
    const stage = $("live-stage");
    const appearing = stage.hidden && frames.length > 0;
    stage.hidden = !frames.length;
    if (appearing && state.panels["live-"]) state.panels["live-"].log.rescroll();
    const weeks = frames.map(f => f.week);
    LIVE_METRICS.forEach(metric => {
      if (!metric.nodes) return;
      const values = frames.map(f => {
        const v = metric.read(f);
        return isNum(v) ? v : null;
      });
      const last = values.length ? values[values.length - 1] : null;
      metric.nodes.value.textContent = isNum(last) ? metric.format(last) : "–";
      const firstValue = values.find(isNum);
      metric.nodes.delta.textContent = isNum(last) && isNum(firstValue) && values.length > 1 ? ` from ${metric.format(firstValue)}` : "";
      if (!frames.length) return;
      const threshold = metric.threshold ? metric.threshold() : null;
      const spec = {
        x: weeks,
        xDomain: [0, state.run.weeks || 52],
        compact: true,
        height: 86,
        group: "live",
        table: false,
        legend: false,
        label: `${metric.label} by week`,
        y: Object.assign({ format: metric.format, ticks: 2 }, metric.y || {}),
        series: [{ label: metric.label, values, color: V.palette.accent, step: metric.step, area: metric.area || metric.key === "commons", format: metric.format }],
        thresholds: isNum(threshold) ? [{ y: threshold, label: "" }] : [],
        notes: (week, i) => (frames[i] && frames[i].events ? frames[i].events.slice(0, 3) : []),
      };
      if (state.liveCharts[metric.key]) state.liveCharts[metric.key].update(spec);
      else state.liveCharts[metric.key] = V.timeChart(metric.nodes.body, spec);
    });
    renderLiveEvents(frames, state.liveChronicle);
    updateExplorerMax();
    const latest = entry.week || 0;
    if (latest >= 1 && latest !== state.latestWeek) {
      state.latestWeek = latest;
      $("live-announcer").textContent = `Week ${latest} of ${state.run.weeks} completed.`;
      loadLatestDecision(latest);
    } else if (latest < 1) {
      const node = clear($("latest-decision"));
      node.appendChild(h("p", { class: "muted", text: "Waiting for the first decision…" }));
    }
  }

  function chronicleFrames(chronicle) {
    const byWeek = new Map();
    (chronicle || []).forEach(entry => {
      if (!byWeek.has(entry.week)) byWeek.set(entry.week, []);
      byWeek.get(entry.week).push(entry.text);
    });
    return [...byWeek.entries()].sort((a, b) => a[0] - b[0]).map(([week, events]) => ({ week, events }));
  }

  function renderLiveEvents(frames, fallback) {
    const list = clear($("live-events"));
    let recent = frames.filter(f => f.events && f.events.length).slice(-6).reverse();
    if (!recent.length && fallback && fallback.length) recent = chronicleFrames(fallback).reverse();
    if (!recent.length) {
      list.appendChild(h("li", { class: "is-empty muted", text: frames.length ? "Nothing notable yet." : "No events recorded yet." }));
      return;
    }
    let count = 0;
    recent.forEach(frame => {
      if (count >= 14) return;
      const items = frame.events.slice(0, 14 - count);
      count += items.length;
      list.appendChild(h("li", { class: frame.collapsed ? "is-collapse" : "" }, h("span", { class: "turn-week", text: `wk ${frame.week}` }), h("ul", {}, items.map(text => h("li", { text })))));
    });
  }

  async function refreshLive() {
    if (state.view !== "live" || !state.seed) return;
    const seed = state.seed;
    try {
      const known = state.liveFrames || [];
      const since = known.length ? known[known.length - 1].week : -1;
      const data = await getJSON(`${API}/runs/${state.id}/live?seed=${encodeURIComponent(seed)}&since=${since}`);
      if (seed !== state.seed || state.view !== "live") return;
      if (data.run) state.run = Object.assign({}, state.run, data.run);
      if (Array.isArray(data.residents) && data.residents.length) state.liveCast = data.residents;
      let fresh = [];
      if (Array.isArray(data.frames)) fresh = data.frames.filter(f => f && isNum(f.week) && f.week > since);
      else if (Array.isArray(data.timeline) && since < 0) fresh = data.timeline.map(row => ({ week: row.week, metrics: row, points: row.points, events: [] }));
      state.liveFrames = known.concat(fresh);
      state.liveProblem = null;
      const panel = state.panels["live-"];
      if (panel && (fresh.length || (data.residents && data.residents.length))) panel.add(fresh, state.liveCast);
      renderLiveData();
    } catch (error) {
      if (error.status === 422 || error.status === 409) {
        state.liveProblem = `The week-by-week history could not be rebuilt (${error.message}).`;
        renderLiveData();
      } else {
        showError(`Live indicators could not be loaded: ${error.message}`);
      }
    }
  }

  async function loadLatestDecision(week) {
    const node = $("latest-decision");
    try {
      const row = await fetchDecision(state.seed, week);
      if (state.latestWeek !== week || state.view !== "live") return;
      clear(node);
      if (!row) {
        node.appendChild(h("p", { class: "muted", text: `No decision recorded for week ${week}.` }));
        return;
      }
      state.liveChronicle = (row.observation && row.observation.chronicle) || [];
      if (!(state.liveFrames || []).length) renderLiveEvents([], state.liveChronicle);
      add(node, [
        h("p", { class: "decision-meta" }, h("strong", { text: `Week ${row.week}` }), usageLine(row.usage)),
        answerBlock(row, true),
        h("h4", { class: "sub-title", text: week > 1 ? `Results of the week ${week - 1} actions, as reported in this briefing` : "Results" }),
        resultsList((row.observation && row.observation.results) || [], week > 1 ? "No results were reported." : "This is the first week: nothing to report yet."),
        h("p", { class: "caption", text: `The outcome of the week ${week} actions appears in the next briefing.` }),
        h("p", {}, h("a", { href: "#decisions", onclick: () => showDecision(week) }, "Open the full briefing in the decision explorer")),
      ]);
    } catch (error) {
      clear(node).appendChild(h("p", { class: "muted", text: `The latest decision could not be loaded (${error.message}).` }));
    }
  }

  // ------------------------------------------------------------------ polling
  function schedulePoll(delay) {
    clearTimeout(state.timer);
    if (!state.run || state.run.status !== "running") return;
    state.timer = setTimeout(poll, delay ?? POLL_MS);
  }

  async function poll() {
    clearTimeout(state.timer);
    if (state.polling) return;
    if (document.hidden) return;
    state.polling = true;
    try {
      const run = await getJSON(`${API}/runs/${state.id}`);
      state.run = run;
      const missing = seedKeys().filter(key => seedEntry(key).status === "completed" && !seedReport(key));
      if (run.status === "completed" || missing.length) {
        await loadReport();
        renderHeader();
        if (seedReport() && state.view !== "report") {
          renderSeedView();
          announce(run.status === "completed" ? "The run has finished: the full report is ready." : `Seed ${state.seed} has finished: its report is ready.`);
        } else if (state.view === "live") {
          await refreshLive();
        }
      } else {
        renderHeader();
        if (state.view === "live") await refreshLive();
      }
      showError(run.error ? `The run stopped with an error: ${run.error}` : "");
    } catch (error) {
      showError(`Live update failed (${error.message}). Retrying…`);
    } finally {
      state.polling = false;
    }
    schedulePoll();
  }

  // ------------------------------------------------------------------ run controls
  async function control(action) {
    const buttons = ["pause-btn", "resume-btn", "delete-btn"].map($);
    buttons.forEach(b => { b.disabled = true; });
    showError("");
    try {
      if (action === "delete") {
        const ok = window.confirm("Delete this society run permanently? Its decision log and report will be removed. This cannot be undone.");
        if (!ok) return;
        await request(`${API}/runs/${state.id}`, { method: "DELETE" });
        announce("The run was deleted.");
        location.assign("/");
        return;
      }
      const run = await getJSON(`${API}/runs/${state.id}/${action}`, { method: "POST" });
      state.run = Object.assign({}, state.run, run);
      renderHeader();
      announce(action === "pause" ? "The run is paused." : "The run has resumed.");
      if (state.view === "live") await refreshLive();
      if (state.run.status === "running") schedulePoll(800);
    } catch (error) {
      showError(`Could not ${action} the run: ${error.message}`);
    } finally {
      buttons.forEach(b => { b.disabled = false; });
      renderHeader();
    }
  }

  async function exportReport() {
    const button = $("export-btn");
    button.disabled = true;
    try {
      let text = state.reportText;
      if (!text || (state.run && state.run.status !== "completed")) text = await (await request(`${API}/runs/${state.id}/report`)).text();
      const blob = new Blob([text], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = h("a", { href: url, download: `society-${state.id}.json` });
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 4000);
    } catch (error) {
      showError(`The report could not be downloaded: ${error.message}`);
    } finally {
      button.disabled = false;
    }
  }

  // ------------------------------------------------------------------ decisions
  async function fetchDecision(seed, week, signal) {
    const key = `${seed}:${week}`;
    if (state.decisions.has(key)) return state.decisions.get(key);
    const rows = await getJSON(`${API}/runs/${state.id}/decisions?seed=${encodeURIComponent(seed)}&week=${week}`, signal ? { signal } : undefined);
    const row = Array.isArray(rows) && rows.length ? rows[0] : null;
    if (row) state.decisions.set(key, row);
    return row;
  }

  function setupExplorer() {
    const section = $("decisions");
    const entry = seedEntry() || {};
    let max = 0;
    if (state.view === "report" && state.ctx) {
      max = Math.max(0, ...state.ctx.frames.map(f => f.week));
      if (state.ctx.score.collapsed) max = Math.max(1, state.ctx.score.collapsed.week);
    } else {
      max = entry.week || 0;
    }
    section.hidden = max < 1;
    if (section.hidden) return;
    state.decisionMax = max;
    const slider = $("decision-week");
    slider.max = String(max);
    const week = state.decisionWeek && state.decisionWeek <= max ? state.decisionWeek : state.view === "live" ? max : 1;
    showDecision(week, "init");
  }

  function updateExplorerMax() {
    if (state.view !== "live") return;
    const entry = seedEntry() || {};
    const max = entry.week || 0;
    if (max < 1) return;
    const section = $("decisions");
    const wasHidden = section.hidden;
    section.hidden = false;
    const following = state.decisionWeek === state.decisionMax;
    state.decisionMax = max;
    $("decision-week").max = String(max);
    if (wasHidden || !state.decisionWeek) {
      showDecision(max, "init");
      renderSectionNav();
    } else if (following && state.decisionWeek !== max) showDecision(max, "live");
  }

  function showDecision(week, origin) {
    const max = state.decisionMax || 1;
    const w = Math.max(1, Math.min(max, Math.round(week)));
    const changed = w !== state.decisionWeek;
    state.decisionWeek = w;
    const slider = $("decision-week");
    slider.value = String(w);
    const label = `Week ${w} of ${max}`;
    $("decision-week-label").textContent = label;
    slider.setAttribute("aria-valuetext", label);
    $("decision-prev").disabled = w <= 1;
    $("decision-next").disabled = w >= max;
    if (origin !== "replay" && origin !== "init") seekReplayToWeek(w);
    if (!changed && origin !== "init" && $("decision-view").childElementCount) return;
    clearTimeout(state.decisionTimer);
    state.decisionTimer = setTimeout(() => loadDecision(w), origin === "slider" ? 140 : 0);
  }

  async function loadDecision(week) {
    const view = $("decision-view");
    const seed = state.seed;
    if (state.decisionAbort) state.decisionAbort.abort();
    const abort = new AbortController();
    state.decisionAbort = abort;
    view.setAttribute("aria-busy", "true");
    view.classList.add("is-loading");
    try {
      const row = await fetchDecision(seed, week, abort.signal);
      let outcome = null;
      let outcomeSource = "";
      const frame = state.view === "report" && state.ctx ? state.ctx.frames.find((f, i) => i > 0 && f.week === week) : null;
      if (frame && Array.isArray(frame.actions)) {
        outcome = frame.actions;
        outcomeSource = "frame";
      } else if (week < (state.decisionMax || 0) || state.view === "report") {
        const next = await fetchDecision(seed, week + 1, abort.signal).catch(() => null);
        if (next && next.observation) {
          outcome = next.observation.results || [];
          outcomeSource = "briefing";
        }
      }
      if (seed !== state.seed || week !== state.decisionWeek) return;
      renderDecision(view, row, week, { outcome, outcomeSource, frame });
    } catch (error) {
      if (error.name === "AbortError") return;
      clear(view).appendChild(h("p", { class: "card muted", text: `The decision for week ${week} could not be loaded (${error.message}).` }));
    } finally {
      if (state.decisionAbort === abort) {
        view.setAttribute("aria-busy", "false");
        view.classList.remove("is-loading");
      }
    }
  }

  function usageLine(usage) {
    const u = usage || {};
    const parts = [];
    if (isNum(u.input_tokens)) parts.push(`${fmt.number(u.input_tokens, 0)} input tokens`);
    if (isNum(u.generated_tokens)) parts.push(`${fmt.number(u.generated_tokens, 0)} generated`);
    if (isNum(u.reasoning_tokens)) parts.push(`${fmt.number(u.reasoning_tokens, 0)} reasoning`);
    if (isNum(u.latency)) parts.push(`${dec(u.latency, u.latency < 10 ? 2 : 1)} s`);
    if (isNum(u.attempt) && u.attempt > 0) parts.push(`retry ${u.attempt}`);
    return h("span", { class: "muted", text: parts.length ? ` · ${parts.join(" · ")}` : "" });
  }

  function parseAnswer(raw) {
    if (raw && typeof raw === "object") return { value: raw, ok: true };
    if (typeof raw !== "string") return { ok: false };
    try {
      return { value: JSON.parse(raw), ok: true };
    } catch (_) {
      const start = raw.indexOf("{");
      const end = raw.lastIndexOf("}");
      if (start >= 0 && end > start) {
        try {
          return { value: JSON.parse(raw.slice(start, end + 1)), ok: true, extracted: true };
        } catch (__) {
          // not JSON
        }
      }
    }
    return { ok: false };
  }

  function jsonTree(value, indent, parent) {
    const pad = "  ".repeat(indent);
    const span = (cls, text) => parent.appendChild(h("span", { class: cls, text }));
    if (value === null) span("j-null", "null");
    else if (typeof value === "string") span("j-str", JSON.stringify(value));
    else if (typeof value === "number") span("j-num", String(value));
    else if (typeof value === "boolean") span("j-bool", String(value));
    else if (Array.isArray(value)) {
      if (!value.length) span("j-punct", "[]");
      else {
        span("j-punct", "[");
        value.forEach((item, i) => {
          parent.appendChild(document.createTextNode(`\n${pad}  `));
          jsonTree(item, indent + 1, parent);
          if (i < value.length - 1) span("j-punct", ",");
        });
        parent.appendChild(document.createTextNode(`\n${pad}`));
        span("j-punct", "]");
      }
    } else if (typeof value === "object") {
      const keys = Object.keys(value);
      if (!keys.length) span("j-punct", "{}");
      else {
        span("j-punct", "{");
        keys.forEach((key, i) => {
          parent.appendChild(document.createTextNode(`\n${pad}  `));
          span("j-key", JSON.stringify(key));
          span("j-punct", ": ");
          jsonTree(value[key], indent + 1, parent);
          if (i < keys.length - 1) span("j-punct", ",");
        });
        parent.appendChild(document.createTextNode(`\n${pad}`));
        span("j-punct", "}");
      }
    } else span("j-str", String(value));
    return parent;
  }

  function nameFor(id) {
    if (!id) return "";
    const fromCtx = state.ctx && state.ctx.residents.get(id);
    if (fromCtx) return fromCtx.name;
    const fromRecipe = state.recipe && (state.recipe.residents || []).find(r => r.id === id);
    return fromRecipe ? fromRecipe.name : "";
  }

  function withName(id, names) {
    const name = (names && names.get(id)) || nameFor(id);
    return name ? `${name} (${id})` : String(id);
  }

  function describeAction(action, names) {
    if (!action || typeof action !== "object") return String(action);
    const verb = action.do || action.verb || action.action || "action";
    const parts = Object.entries(action).filter(([k]) => !["do", "verb", "action", "say"].includes(k)).map(([k, v]) => {
      let text = Array.isArray(v) ? v.map(item => (typeof item === "string" && /^r\d+$/.test(item) ? withName(item, names) : JSON.stringify(item))).join(", ")
        : typeof v === "string" && /^r\d+$/.test(v) ? withName(v, names)
          : typeof v === "object" ? JSON.stringify(v) : String(v);
      if (text.length > 160) text = text.slice(0, 159) + "…";
      return `${k} ${text}`;
    });
    return { verb, rest: parts.join(" · ") };
  }

  function answerBlock(row, compact, names) {
    const wrap = h("div", { class: "answer" });
    const parsed = parseAnswer(row.raw);
    if (row.parse_error) wrap.appendChild(h("p", { class: "parse-error" }, h("strong", { text: "Parse error · " }), String(row.parse_error), " No actions were taken."));
    if (parsed.ok && parsed.value && typeof parsed.value === "object") {
      const actions = Array.isArray(parsed.value.actions) ? parsed.value.actions : [];
      if (actions.length) {
        const list = h("ol", { class: "action-list" });
        actions.forEach(action => {
          const d = describeAction(action, names);
          list.appendChild(typeof d === "string" ? h("li", { text: d }) : h("li", {}, h("strong", { class: "verb", text: d.verb }), d.rest ? h("span", { text: ` ${d.rest}` }) : null,
            action && typeof action.say === "string" && action.say ? h("q", { class: "log-say", text: action.say }) : null));
        });
        wrap.appendChild(list);
      } else if (!row.parse_error) wrap.appendChild(h("p", { class: "muted", text: "No actions this week." }));
      const forecasts = parsed.value.forecasts && typeof parsed.value.forecasts === "object" ? Object.entries(parsed.value.forecasts) : [];
      if (forecasts.length) wrap.appendChild(h("p", { class: "answer-extra" }, h("strong", { text: "Forecasts · " }), forecasts.map(([k, v]) => { const fact = factOf(k); return `${fact ? `“${fact.statement}”` : humanize(k)} ${isNum(v) ? pct(v) : String(v)}`; }).join(" · ")));
      if (parsed.value.note) wrap.appendChild(h("p", { class: "answer-note" }, h("strong", { text: "Note to self · " }), String(parsed.value.note)));
    }
    const details = h("details", { class: "raw", open: compact ? false : true });
    details.appendChild(h("summary", { text: parsed.ok ? (parsed.extracted ? "Raw answer (JSON extracted from text)" : "Raw answer · JSON") : "Raw answer · not JSON" }));
    const pre = h("pre", { class: "code" + (compact ? " is-compact" : "") });
    if (parsed.ok) jsonTree(parsed.value, 0, pre);
    else pre.textContent = typeof row.raw === "string" ? row.raw : JSON.stringify(row.raw);
    details.appendChild(pre);
    if (parsed.ok && parsed.extracted) {
      const full = h("details", { class: "raw" }, h("summary", { text: "Full text as sent" }), h("pre", { class: "code is-compact", text: row.raw }));
      details.appendChild(full);
    }
    wrap.appendChild(details);
    return wrap;
  }

  function statusBadge(status) {
    const s = String(status || "").toLowerCase();
    const level = ["done", "ok", "applied", "accepted", "passed"].includes(s) ? "good" : ["rejected", "failed", "invalid", "error", "refused"].includes(s) ? "bad" : "watch";
    return h("span", { class: `badge is-${level}`, text: s || "?" });
  }

  function resultsList(results, empty) {
    if (!results || !results.length) return h("p", { class: "muted", text: empty || "No results." });
    const list = h("ul", { class: "results" });
    results.forEach(item => list.appendChild(h("li", {},
      statusBadge(item.status),
      h("div", {}, h("code", { text: item.action || item.verb || "" }), h("p", { text: item.text || "" })))));
    return list;
  }

  function renderDecision(view, row, week, extra) {
    clear(view);
    if (!row) {
      const collapsed = state.ctx && state.ctx.score.collapsed && state.ctx.score.collapsed.week === week;
      view.appendChild(h("p", { class: "card muted", text: collapsed ? `No decision was recorded for week ${week}: the society collapsed before the steward could act.` : `No decision was recorded for week ${week}.` }));
      return;
    }
    const obs = row.observation || {};
    const names = new Map((obs.residents || []).map(r => [r.id, r.name]));
    const side = h("aside", { class: "explorer-side" });
    add(side, h("section", { class: "card brief-card answer-card" },
      h("h3", {}, "Answer", usageLine(row.usage)),
      answerBlock(row, false, names)));
    const outcome = h("section", { class: "card brief-card" }, h("h3", { text: "What the actions caused" }));
    if (extra.outcome) {
      outcome.appendChild(resultsList(extra.outcome, "No action was applied."));
      outcome.appendChild(h("p", { class: "caption", text: extra.outcomeSource === "frame" ? "As applied by the engine that week." : `As reported in the week ${week + 1} briefing.` }));
    } else {
      outcome.appendChild(h("p", { class: "muted", text: "The outcome appears in the next briefing, which the steward has not received yet." }));
    }
    if (extra.frame && extra.frame.events && extra.frame.events.length) {
      outcome.appendChild(h("h4", { class: "sub-title", text: `The chronicle of week ${week}` }));
      outcome.appendChild(h("ul", { class: "chronicle" }, extra.frame.events.map(text => h("li", { text }))));
    }
    side.appendChild(outcome);
    const usage = row.usage || {};
    const usageKeys = Object.keys(usage);
    if (usageKeys.length) {
      side.appendChild(h("section", { class: "card brief-card" }, h("h3", { text: "Usage" }),
        h("dl", { class: "kv" }, usageKeys.map(key => h("div", {}, h("dt", { text: humanize(key) }), h("dd", { text: typeof usage[key] === "number" ? fmt.number(usage[key], Number.isInteger(usage[key]) ? 0 : 3) : String(usage[key]) }))))));
    }
    const main = h("div", { class: "explorer-main" }, renderBriefing(obs, names));
    view.appendChild(h("div", { class: "explorer-grid" }, main, side));
  }

  // ---- briefing
  const BRIEFING_KEYS = new Set(["week", "weeks", "season", "title", "place", "actions_per_week", "stores", "population", "lake", "policies", "council", "conventions", "residents", "projects", "merchants", "market", "conflicts", "promises", "rumours", "probes", "inbox", "open_requests", "results", "chronicle", "manuals", "note"]);

  function briefCard(title, ...children) {
    return h("section", { class: "card brief-card" }, h("h3", { text: title }), children);
  }

  function kvList(entries) {
    return h("dl", { class: "kv" }, entries.filter(([, v]) => v !== undefined && v !== null && v !== "").map(([k, v]) => h("div", {}, h("dt", { text: k }), h("dd", {}, v))));
  }

  function valueText(v) {
    if (v === null || v === undefined) return "none";
    if (typeof v === "boolean") return v ? "yes" : "no";
    if (typeof v === "object") return JSON.stringify(v);
    return String(v);
  }

  function renderBriefing(obs, names) {
    const wrap = h("div", { class: "briefing" });
    const subtitle = [obs.season, obs.place, obs.title, isNum(obs.actions_per_week) ? `up to ${obs.actions_per_week} actions` : null].filter(Boolean).join(" · ");
    wrap.appendChild(h("header", { class: "briefing-head" }, h("h3", { text: `The briefing for week ${obs.week ?? "?"}${obs.weeks ? ` of ${obs.weeks}` : ""}` }), h("p", { class: "muted", text: subtitle })));
    const s = obs.stores || {};
    wrap.appendChild(h("div", { class: "tiles brief-tiles" },
      factTile("Treasury", `${fmt.number(s.treasury, 1)}`, "coins"),
      factTile("Granary", `${fmt.number(s.granary, 1)}`, isNum(s.granary_weeks) ? `food · ~${dec(s.granary_weeks, 1)} weeks for everyone` : "food"),
      factTile("Medicine", fmt.number(s.medicine, 0), "doses"),
      factTile("Tools", fmt.number(s.tools, 1), "sets"),
      obs.population ? factTile("Population", `${obs.population.present} / ${obs.population.initial}`, "present / at start") : null));
    const grid = h("div", { class: "brief-grid" });
    const wide = h("div", { class: "brief-wide" });
    wrap.appendChild(grid);
    wrap.appendChild(wide);

    if (obs.inbox && obs.inbox.length) {
      grid.appendChild(briefCard(`Inbox · ${obs.inbox.length}`, h("ul", { class: "inbox" }, obs.inbox.map(m => h("li", {}, h("strong", { text: m.name || withName(m.sender, names) }), h("q", { text: m.text }))))));
    }
    if (obs.results) grid.appendChild(briefCard("Results of last week's actions", resultsList(obs.results, obs.week > 1 ? "Nothing to report." : "First week: nothing to report yet.")));

    const pop = obs.population || {};
    const lake = obs.lake || {};
    grid.appendChild(briefCard("Village and lake", kvList([
      ["Gone", pop.gone && pop.gone.length ? pop.gone.map(g => `${g.name} (${g.fate}, wk ${g.week})`).join(", ") : "nobody"],
      [`Catches on ${lake.name || "the lake"}`, lake.catches],
      ["Landed last week", isNum(lake.landed_last_week) ? `about ${lake.landed_last_week} units` : null],
      ["Survey", lake.survey ? `week ${lake.survey.week}: ${lake.survey.stock_percent}% of capacity` : "never surveyed"],
      ["Lake state", lake.collapsed ? "DEAD" : null],
    ])));

    const council = obs.council || {};
    const policyChips = h("span", { class: "chips" }, Object.entries(obs.policies || {}).map(([k, v]) => h("span", { class: "chip", text: `${k} = ${valueText(v)}` })));
    const conventionChips = h("span", { class: "chips" }, Object.entries(obs.conventions || {}).map(([k, v]) => h("span", { class: "chip", text: `${k}: ${valueText(v)}` })));
    const pending = (council.pending || []).map(p => `${p.policy} = ${valueText(p.value)}${p.by ? ` (petition by ${withName(p.by, names)})` : ""}`);
    const votes = (council.recent_votes || []).map(v => `wk ${v.week}: ${v.policy} = ${valueText(v.value)} ${v.result} (${v.yes}–${v.no})`);
    grid.appendChild(briefCard("Policies and council", kvList([
      ["Policies", policyChips],
      ["Conventions", conventionChips],
      ["Pending", pending.length ? pending.join("; ") : "none"],
      ["Recent votes", votes.length ? votes.join("; ") : "none"],
    ])));

    if (obs.residents && obs.residents.length) {
      const table = h("table", { class: "data-table brief-table" });
      add(table, h("thead", {}, h("tr", {}, ["Resident", "Role", "Mood", "Health", "Seen", "Task", "Skills", "Last talk", "Notes"].map(t => h("th", { scope: "col", text: t })))));
      const body = h("tbody");
      obs.residents.forEach(r => {
        const mood = MOODS[r.mood] || "neutral";
        body.appendChild(h("tr", {},
          h("th", { scope: "row" }, h("strong", { text: r.name }), h("span", { class: "resident-sub", text: `${r.id} · ${r.age ?? ""}` })),
          h("td", { text: `${humanize(r.role)}${r.faction ? ` · ${r.faction}` : ""}` }),
          h("td", {}, h("span", { class: `mood is-${mood}`, text: r.mood })),
          h("td", {}, h("span", { class: `mood is-${r.health === "healthy" ? "good" : r.health === "bedridden" || r.health === "frail" ? "bad" : "watch"}`, text: r.health })),
          h("td", { text: r.seen }),
          h("td", { text: r.task }),
          h("td", { text: Object.entries(r.skills || {}).map(([k, v]) => `${k} ${v}`).join(", ") || "–" }),
          h("td", { text: r.last_talk === "never" ? "never" : isNum(r.last_talk) ? `${r.last_talk} wk ago` : valueText(r.last_talk) }),
          h("td", { class: "notes", text: (r.flags || []).join("; ") })));
      });
      add(table, body);
      wide.appendChild(h("section", { class: "card brief-card is-wide" }, h("h3", { text: `Residents · ${obs.residents.length}` }), h("div", { class: "table-scroll" }, table), h("p", { class: "caption", text: "Observable cues only: pride and trust can mask distress." })));
    }

    if (obs.rumours && obs.rumours.length) {
      grid.appendChild(briefCard("Rumours", h("ul", { class: "plain" }, obs.rumours.map(f => h("li", {},
        h("strong", { text: `“${f.statement}”` }),
        h("span", { class: "resident-sub", text: `heard by ${f.heard_by} · your inquiries: ${(f.your_inquiries || []).join("/") || "none"} · your statements: ${(f.your_statements || []).join("/") || "none"}` }))))));
    }
    if (obs.probes && obs.probes.length) {
      grid.appendChild(briefCard("Forecasts requested", h("ul", { class: "plain" }, obs.probes.map(p => h("li", {},
        h("strong", { text: p.question }),
        h("span", { class: "resident-sub", text: `answer by week ${p.answer_by_week}${p.your_answer !== null && p.your_answer !== undefined ? ` · your answer ${pct(p.your_answer)}` : ""}` }))))));
    }
    if ((obs.conflicts && obs.conflicts.length) || (obs.promises && obs.promises.length) || (obs.open_requests && obs.open_requests.length)) {
      const items = [];
      (obs.conflicts || []).forEach(c => items.push(h("li", {}, h("strong", { text: `Dispute · ${(c.between || []).map(id => withName(id, names)).join(" vs ")}` }), h("span", { class: "resident-sub", text: `about ${c.about}, ${c.severity}, since week ${c.since_week}; heard: ${(c.heard || []).map(id => withName(id, names)).join(", ") || "nobody"}` }))));
      (obs.promises || []).forEach(p => items.push(h("li", {}, h("strong", { text: `Promise to ${withName(p.to, names)}` }), h("span", { class: "resident-sub", text: `${p.topic}, due by week ${p.by}` }))));
      (obs.open_requests || []).forEach(q => items.push(h("li", {}, h("strong", { text: `Request from ${withName(q.sender, names)}` }), h("span", { class: "resident-sub", text: `${q.kind}, week ${q.week}` }))));
      grid.appendChild(briefCard("Disputes, promises and requests", h("ul", { class: "plain" }, items)));
    }
    if (obs.projects && obs.projects.length) {
      const table = h("table", { class: "data-table brief-table" });
      add(table, h("thead", {}, h("tr", {}, ["Project", "Status", "Progress", "Cost", "Workers"].map(t => h("th", { scope: "col", text: t })))));
      add(table, h("tbody", {}, obs.projects.map(p => h("tr", {},
        h("th", { scope: "row" }, h("strong", { text: p.name || p.id }), p.benefit ? h("span", { class: "resident-sub", text: p.benefit }) : null),
        h("td", {}, h("span", { class: `badge is-${p.status === "done" ? "good" : p.status === "in_progress" ? "watch" : "neutral"}`, text: humanize(p.status) })),
        h("td", { text: Object.entries(p.needs || {}).map(([k, v]) => `${k} ${v}`).join(", ") + (p.together ? " · same week" : "") + (p.requires && p.requires.length ? ` · requires ${p.requires.join(", ")}` : "") }),
        h("td", { class: "num", text: isNum(p.cost) ? `${p.cost}c` : "" }),
        h("td", { text: (p.workers || []).map(id => names.get(id) || id).join(", ") || "–" })))));
      wide.appendChild(h("section", { class: "card brief-card is-wide" }, h("h3", { text: "Projects" }), h("div", { class: "table-scroll" }, table)));
    }
    if ((obs.merchants && obs.merchants.length) || obs.market) {
      const items = (obs.merchants || []).map(m => h("li", {}, h("strong", { text: `${m.name} · until week ${m.leaves_after_week}` }),
        h("span", { class: "resident-sub", text: `sells ${Object.entries(m.sells || {}).map(([k, v]) => `${k} ${v.price} (stock ${v.stock})`).join(", ") || "nothing"} · buys ${Object.entries(m.buys || {}).map(([k, v]) => `${k} ${v}`).join(", ") || "nothing"}${Object.keys(m.counteroffers || {}).length ? ` · counteroffers ${Object.entries(m.counteroffers).map(([k, v]) => `${k} ${v}`).join(", ")}` : ""}` })));
      const market = obs.market ? h("p", { class: "resident-sub", text: `Local market: ${fmt.number(obs.market.food_offered_last_week, 1)} food offered last week · you buy at ${obs.market.buy_price} · you sell at ${obs.market.sell_price}` }) : null;
      grid.appendChild(briefCard("Trade", items.length ? h("ul", { class: "plain" }, items) : h("p", { class: "muted", text: "No merchant in town." }), market));
    }
    if (obs.chronicle && obs.chronicle.length) {
      grid.appendChild(briefCard("Chronicle", h("ul", { class: "chronicle" }, obs.chronicle.map(c => h("li", {}, h("span", { class: "turn-week", text: `wk ${c.week}` }), ` ${c.text}`)))));
    }
    const manuals = Object.entries(obs.manuals || {});
    grid.appendChild(briefCard("Notes and manuals", kvList([
      ["Note from last week", obs.note ? h("q", { text: obs.note }) : "(empty)"],
      ["Manuals", manuals.length ? manuals.map(([k, v]) => `${k} level ${v}`).join(", ") : "none"],
    ])));
    const other = Object.keys(obs).filter(key => !BRIEFING_KEYS.has(key));
    if (other.length) {
      const pre = h("pre", { class: "code is-compact" });
      const rest = {};
      other.forEach(key => { rest[key] = obs[key]; });
      jsonTree(rest, 0, pre);
      wide.appendChild(h("section", { class: "card brief-card is-wide" }, h("details", { class: "raw" }, h("summary", { text: `Other briefing fields · ${other.join(", ")}` }), pre)));
    }
    return wrap;
  }

  // ------------------------------------------------------------------ bindings and start
  function bindControls() {
    $("pause-btn").addEventListener("click", () => control("pause"));
    $("resume-btn").addEventListener("click", () => control("resume"));
    $("delete-btn").addEventListener("click", () => control("delete"));
    $("export-btn").addEventListener("click", exportReport);
    $("seed-tabs").addEventListener("keydown", onSeedTabKey);
    const slider = $("decision-week");
    slider.addEventListener("input", () => showDecision(Number(slider.value), "slider"));
    $("decision-prev").addEventListener("click", () => showDecision((state.decisionWeek || 1) - 1, "button"));
    $("decision-next").addEventListener("click", () => showDecision((state.decisionWeek || 1) + 1, "button"));
    bindPlaybackControls("replay-");
    bindPlaybackControls("live-");
    state.sheets["replay-"] = makeLogSheet($("replay-log-card"), $("replay-log-btn"), "Steward log");
    state.sheets["live-"] = makeLogSheet($("live-log-card"), $("live-log-btn"), "Steward log");
    bindSectionNav();
    bindStoryTools();
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden && state.run && state.run.status === "running") poll();
    });
  }

  async function start() {
    if (!V) {
      showError("The chart library failed to load. Reload the page.");
      return;
    }
    state.id = runIdFromPath();
    if (!state.id) {
      showError("This address does not name a society run.");
      $("run-title").textContent = "Unknown run";
      $("export-btn").hidden = true;
      $("delete-btn").hidden = true;
      return;
    }
    bindControls();
    try {
      state.run = await getJSON(`${API}/runs/${state.id}`);
    } catch (error) {
      $("run-title").textContent = error.status === 404 ? "Run not found" : "The run could not be loaded";
      showError(error.status === 404 ? "This society run does not exist. It may have been deleted." : `The run could not be loaded: ${error.message}`);
      $("status-pill").dataset.status = "failed";
      $("status-pill").querySelector("span").textContent = "Unavailable";
      $("export-btn").hidden = true;
      $("delete-btn").hidden = true;
      return;
    }
    state.seed = pickSeed();
    renderHeader();
    const recipe = loadRecipe();
    try {
      if (needsReport()) {
        announce("Loading the report…");
        await Promise.all([loadReport(), recipe]);
        announce("");
      } else {
        await recipe;
      }
    } catch (error) {
      showError(`The report could not be loaded: ${error.message}`);
    }
    renderHeader();
    renderSeedView();
    if (state.view === "live") await refreshLive();
    if (location.hash) {
      const target = document.getElementById(location.hash.slice(1));
      if (target && !target.closest("[hidden]")) target.scrollIntoView();
    }
    schedulePoll();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
