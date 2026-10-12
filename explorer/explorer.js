"use strict";
/*
 * UNIMATRIx Explorer · the single-page app. Two screens, hash-routed:
 *   #/?recipe=&rev=&show=   the arena: one plain leaderboard per recipe and revision
 *   #/run/<id>?seed=&week=  the run explorer: everything the steward did, week by week (explorer/replay.js)
 * No server, no database, no API: data come only from xCDN files (explorer/data.js).
 */
(function () {
  const D = window.ExplorerData;
  const U = window.ExplorerUI;
  const { h, icon, $ } = U;
  const store = D.store;
  const REFS = D.REFERENCE_KEYS;

  // ================================================================== derived model
  let derived = { version: -1 };
  const byCreatedDesc = (a, b) => String(b.created || "").localeCompare(String(a.created || ""));
  const scoreOf = (e) => (e.fullyCollapsed ? 0 : e.total);

  /** Recipes with their revisions (newest first), memoised per store version. */
  function model() {
    if (derived.version === store.version) return derived;
    const recipes = new Map();
    for (const e of store.entries) {
      let r = recipes.get(e.recipe);
      if (!r) { r = { id: e.recipe, name: e.recipeName, entries: [] }; recipes.set(e.recipe, r); }
      r.entries.push(e);
    }
    for (const r of recipes.values()) {
      r.entries.sort(byCreatedDesc);
      const revs = new Map();
      for (const e of r.entries) { if (!revs.has(e.revision)) revs.set(e.revision, []); revs.get(e.revision).push(e); }
      r.revisions = [...revs.entries()].map(([rev, list]) => ({ rev, entries: list, latest: list[0].created || "" }))
        .sort((a, b) => String(b.latest).localeCompare(String(a.latest)));
      r.current = r.revisions[0].rev;
      [r.title, r.subtitle] = U.splitName(r.name);
    }
    const list = [...recipes.values()].sort((a, b) => a.name.localeCompare(b.name));
    derived = { version: store.version, recipes: list, byId: recipes };
    return derived;
  }
  /** The expert reference run of the same recipe and revision (for ×expert ticks), if loaded. */
  function expertFor(entry) {
    const r = model().byId.get(entry.recipe);
    if (!r) return null;
    return r.entries.find((e) => e.isRef && e.refKey === "expert" && e.revision === entry.revision) || null;
  }
  /** Leaderboard rows: models ranked by score; reference stewards interleaved as unranked milestones. */
  function boardRows(recipe, rev, latestOnly) {
    const pool = recipe.entries.filter((e) => e.revision === rev);
    const keep = [];
    const seen = new Set();
    for (const e of pool) {
      const key = e.isRef ? `ref:${e.refKey}` : `model:${e.model}\u0000${e.snapshot}`;
      if (latestOnly && seen.has(key)) continue;
      seen.add(key);
      keep.push(e);
    }
    keep.sort((a, b) => scoreOf(b) - scoreOf(a) || Number(a.isRef) - Number(b.isRef) || (b.relative || 0) - (a.relative || 0) || byCreatedDesc(a, b));
    let rank = 0;
    const latest = new Set();
    const seenModel = new Set();
    for (const e of pool) { const key = `${e.model}\u0000${e.snapshot}`; if (!seenModel.has(key)) { seenModel.add(key); latest.add(e.id); } }
    return keep.map((e) => ({ entry: e, rank: e.isRef ? null : ++rank, older: !latest.has(e.id) }));
  }
  const runHref = (id, extra) => `#/run/${encodeURIComponent(id)}${extra || ""}`;
  const boardHref = (recipe, rev, show) => {
    const q = new URLSearchParams();
    if (recipe) q.set("recipe", recipe);
    if (rev) q.set("rev", rev);
    if (show && show !== "latest") q.set("show", show);
    const s = q.toString();
    return `#/${s ? "?" + s : ""}`;
  };

  // ================================================================== shell: source pill, data panel, files
  function renderChrome() {
    const pill = $("source-pill");
    const src = store.source;
    pill.dataset.kind = store.status === "loading" ? "loading" : src.kind;
    $("source-text").textContent = store.status === "loading" ? "Loading…" : store.status === "empty" ? "No data" : src.kind === "data" ? `data/ · ${store.entries.length}` : `Files · ${store.entries.length}`;
    pill.title = store.status === "ready" ? `${src.label}: ${store.entries.length} run${store.entries.length === 1 ? "" : "s"}. Open the data panel.` : "Data panel";
  }

  function openDataPanel() {
    const dialog = $("data-dialog");
    const body = $("data-body");
    const src = store.source;
    const parts = [];
    if (store.status === "ready") {
      const n = store.entries.length;
      parts.push(h("div", { class: "data-now" }, icon(src.kind === "files" ? "folder" : "server"),
        h("div", null, h("b", null, src.kind === "data" ? "Served data: data/index.xcdn" : "Files opened in this tab"),
          h("span", null, `${n} run${n === 1 ? "" : "s"} · ${model().recipes.length} recipe${model().recipes.length === 1 ? "" : "s"}${store.header && store.header.generated ? ` · index written ${U.dateTime(store.header.generated)}` : ""}`))));
    } else {
      parts.push(h("p", { class: "muted" }, location.protocol === "file:"
        ? "This page was opened straight from disk, where browsers do not let a page read other files: open the data folder (or single .xcdn files) instead."
        : "No data/index.xcdn was found next to this page."));
    }
    parts.push(h("div", { class: "load-grid" },
      U.actionButton("file", "Open .xcdn files", "primary big", () => { dialog.close(); $("file-input").click(); }),
      U.actionButton("folder", "Open a folder", "big", () => { dialog.close(); $("folder-input").click(); })));
    parts.push(h("p", { class: "hint" }, "Pick data/ (index.xcdn and runs/), or one or more run files: a run file alone is enough to explore it. You can also drop files or folders anywhere on the page. Nothing leaves your browser."));
    if (/^https?:$/.test(location.protocol) && store.source.kind === "files") {
      parts.push(U.actionButton("refresh", "Back to the served data/", "", () => { dialog.close(); D.boot(); }));
    }
    if (store.errors.length) {
      parts.push(h("div", { class: "alert warn" }, h("b", null, `${store.errors.length} problem${store.errors.length === 1 ? "" : "s"} while loading`),
        h("ul", { class: "err-list" }, store.errors.slice(0, 12).map((m) => h("li", null, m)))));
    }
    U.fill(body, ...parts);
    if (typeof dialog.showModal === "function") dialog.showModal(); else dialog.setAttribute("open", "");
  }

  async function ingest(items) {
    if (!items.length) return;
    const t = U.toast(`Reading ${items.length} file${items.length === 1 ? "" : "s"}…`, "info", { ms: 60000 });
    let result;
    try { result = await D.loadFiles(items); } catch (error) { result = { ok: false, errors: [error.message || String(error)] }; }
    if (t) t.remove();
    if (!result.ok) {
      U.toast(result.errors[0] || "Nothing could be loaded.", "bad", { detail: result.errors.length > 1 ? `${result.errors.length - 1} more problem(s).` : null, ms: 12000 });
      return;
    }
    const extra = result.errors.length ? ` · ${result.errors.length} file${result.errors.length === 1 ? "" : "s"} skipped` : "";
    U.toast(`Opened ${result.loaded} run${result.loaded === 1 ? "" : "s"}${extra}.`, result.errors.length ? "gold" : "good",
      result.errors.length ? { detail: result.errors[0], action: { label: "Details", run: openDataPanel } } : {});
  }
  const filesFromInput = (input) => [...(input.files || [])].map((file) => ({ file, path: file.webkitRelativePath || file.name }));
  async function filesFromDrop(dt) {
    const out = [];
    const items = dt.items ? [...dt.items] : [];
    const entries = items.map((it) => (it.kind === "file" && typeof it.webkitGetAsEntry === "function" ? it.webkitGetAsEntry() : null)).filter(Boolean);
    if (!entries.length) return [...(dt.files || [])].map((file) => ({ file, path: file.name }));
    const walk = async (entry, prefix) => {
      if (entry.isFile) {
        const file = await new Promise((resolve, reject) => entry.file(resolve, reject)).catch(() => null);
        if (file) out.push({ file, path: prefix + file.name });
      } else if (entry.isDirectory) {
        const reader = entry.createReader();
        for (;;) {
          const batch = await new Promise((resolve) => reader.readEntries(resolve, () => resolve([])));
          if (!batch.length) break;
          for (const child of batch) await walk(child, `${prefix}${entry.name}/`);
        }
      }
    };
    for (const entry of entries) await walk(entry, "");
    return out;
  }
  function wireFiles() {
    $("load-btn").addEventListener("click", openDataPanel);
    $("source-pill").addEventListener("click", openDataPanel);
    $("data-close").addEventListener("click", () => $("data-dialog").close());
    for (const id of ["file-input", "folder-input"]) {
      const input = $(id);
      input.addEventListener("change", () => { const items = filesFromInput(input); input.value = ""; ingest(items); });
    }
    const overlay = $("drop-overlay");
    let depth = 0;
    const hasFiles = (event) => event.dataTransfer && [...(event.dataTransfer.types || [])].includes("Files");
    window.addEventListener("dragenter", (event) => { if (!hasFiles(event)) return; event.preventDefault(); depth += 1; overlay.hidden = false; });
    window.addEventListener("dragover", (event) => { if (!hasFiles(event)) return; event.preventDefault(); event.dataTransfer.dropEffect = "copy"; });
    window.addEventListener("dragleave", (event) => { if (!hasFiles(event)) return; depth = Math.max(0, depth - 1); if (!depth) overlay.hidden = true; });
    window.addEventListener("drop", async (event) => {
      if (!hasFiles(event)) return;
      event.preventDefault();
      depth = 0;
      overlay.hidden = true;
      ingest(await filesFromDrop(event.dataTransfer));
    });
  }

  // ================================================================== router
  let current = null;
  let lastKey = "";
  function parseHash() {
    const raw = location.hash.replace(/^#\/?/, "");
    const at = raw.indexOf("?");
    const path = at >= 0 ? raw.slice(0, at) : raw;
    const params = new URLSearchParams(at >= 0 ? raw.slice(at + 1) : "");
    const segs = path.split("/").filter(Boolean).map((s) => { try { return decodeURIComponent(s); } catch (e) { return s; } });
    return { segs, params, path };
  }
  function route(force, userNav) {
    if (location.hash && !location.hash.startsWith("#/") && location.hash !== "#") return;
    const key = location.hash || "#/";
    if (!force && key === lastKey && current) return;
    lastKey = key;
    const { segs, params, path } = parseHash();
    const samePath = current && current.path === path;
    if (current && current.destroy) { try { current.destroy(); } catch (e) { console.error(e); } }
    current = null;
    const name = segs[0] || "board";
    if (name === "run") $("nav-board").removeAttribute("aria-current"); else $("nav-board").setAttribute("aria-current", "page");
    let view;
    if (store.status === "idle" || store.status === "loading") view = loadingView();
    else if (store.status === "empty") view = emptyView();
    else if (name === "run") view = window.ExplorerReplay.render({ id: segs[1], params, app: App });
    else view = boardView(params);
    view.path = path;
    current = view;
    U.fill($("view"), view.el);
    document.title = `${view.title ? view.title + " · " : ""}UNIMATRIx Explorer`;
    if (!samePath) {
      window.scrollTo({ top: 0, behavior: "instant" });
      const heading = $("view").querySelector("h1");
      if (heading && userNav) { heading.setAttribute("tabindex", "-1"); heading.focus({ preventScroll: true }); }
    }
  }
  /** Rewrites the address without re-rendering (filters, seed, week). */
  function replaceHash(hash) {
    if (location.hash === hash) return;
    history.replaceState(null, "", hash);
    lastKey = location.hash;
  }
  const go = (hash) => { if (location.hash === hash) route(true, true); else location.hash = hash; };

  // ================================================================== views
  function sectionHead(eyebrow, title, lead, side, level) {
    return h("div", { class: "section-head" + (level === 2 ? " compact" : "") },
      h("div", null, eyebrow ? h("p", { class: "eyebrow" }, eyebrow) : null, h(level === 2 ? "h2" : "h1", null, title), lead ? h("p", { class: "lead" }, lead) : null),
      side || null);
  }
  function backLink(href, label) {
    return h("a", { class: "back-link", href }, icon("back", "sm"), h("span", null, label));
  }
  function notFound(title, text) {
    return h("section", { class: "view" }, sectionHead(null, title), U.emptyNote("alert", title, text, U.linkButton("board", "Leaderboard", "#/", "primary"), U.actionButton("upload", "Open files", "", openDataPanel)));
  }
  function loadingView() {
    return { el: h("section", { class: "view", "aria-busy": "true" },
      h("div", { class: "skeleton", style: { height: "84px", marginBottom: "18px", maxWidth: "640px" }, "aria-hidden": "true" }),
      h("div", { class: "skeleton", style: { height: "420px" }, "aria-hidden": "true" }),
      h("p", { class: "sr-only", role: "status" }, "Loading the Explorer data…")), title: "Loading" };
  }
  function emptyView() {
    const fromDisk = location.protocol === "file:";
    const errs = store.errors.length ? h("div", { class: "alert warn" }, h("b", null, "The data could not be read"), h("ul", { class: "err-list" }, store.errors.map((m) => h("li", null, m)))) : null;
    const el = h("section", { class: "view empty-view" },
      sectionHead("UNIMATRIx · Explorer", "No runs to explore yet",
        fromDisk ? "You opened the Explorer straight from disk. Browsers do not let a page read files on its own: open the data folder or a few .xcdn files below, or serve the folder."
          : store.source.reason === "no-entries" ? "data/index.xcdn exists but lists no runs yet."
            : "No data/index.xcdn was found next to this page. Two ways to fill the Explorer:"),
      errs,
      h("div", { class: "how-grid" },
        h("section", { class: "card how" },
          h("h2", null, h("span", { class: "how-n" }, "1"), "Export runs to data/"),
          h("p", null, "The scripted reference stewards of the bundled recipe:"),
          h("pre", { class: "cmd" }, "uv run unimatrix demo --explorer-dir explorer"),
          h("p", null, "Model runs are exported to explorer/data/ automatically when they finish (unimatrix serve or unimatrix run); older ones with unimatrix export. Then serve the folder:"),
          h("pre", { class: "cmd" }, "python3 -m http.server --directory explorer 8090"),
          /^https?:$/.test(location.protocol) ? U.actionButton("refresh", "Look for data/ again", "", () => D.boot()) : null),
        h("section", { class: "card how" },
          h("h2", null, h("span", { class: "how-n" }, "2"), "Open .xcdn files"),
          h("p", null, "Open data/ (index.xcdn and runs/) or single run files exported by UNIMATRIx. They are read in this tab only."),
          h("div", { class: "load-grid" },
            U.actionButton("file", "Open .xcdn files", "primary big", () => $("file-input").click()),
            U.actionButton("folder", "Open a folder", "big", () => $("folder-input").click())),
          h("p", { class: "hint" }, "Or drop files and folders anywhere on this page."))));
    return { el, title: "No data" };
  }

  function boardView(params) {
    const m = model();
    if (!m.recipes.length) return emptyView();
    const recipe = m.byId.get(params.get("recipe")) || m.byId.get("standard-v2") || m.recipes[0];
    const rev = recipe.revisions.some((r) => r.rev === params.get("rev")) ? params.get("rev") : recipe.current;
    const show = params.get("show") === "all" ? "all" : "latest";
    const header = store.header;
    const rows = boardRows(recipe, rev, show === "latest");
    const ex = recipe.entries.find((e) => e.isRef && e.refKey === "expert" && e.revision === rev);
    const scales = U.domainScales(rows.map((r) => r.entry.domains));
    const models = new Set(recipe.entries.filter((e) => !e.isRef && e.revision === rev).map((e) => e.model)).size;
    const refresh = (r, v, s) => { replaceHash(boardHref(r, v, s)); route(true); };

    const controls = h("div", { class: "board-controls" });
    if (m.recipes.length > 1) {
      controls.append(h("fieldset", { class: "ctl" }, h("legend", { class: "label" }, "Recipe"),
        U.segmented("recipe", m.recipes.map((r) => ({ value: r.id, label: r.title, count: r.entries.length })), recipe.id, (v) => refresh(v, null, show), "Recipe")));
    }
    if (recipe.revisions.length > 1) {
      controls.append(h("fieldset", { class: "ctl" }, h("legend", { class: "label" }, "Revision"),
        U.segmented("rev", recipe.revisions.map((r, i) => ({ value: r.rev, label: `${r.rev || "—"}${i === 0 ? " · latest" : ""}`, count: r.entries.length })), rev, (v) => refresh(recipe.id, v, show), "Recipe revision")));
    }
    controls.append(h("fieldset", { class: "ctl" }, h("legend", { class: "label" }, "Runs"),
      U.segmented("show", [{ value: "latest", label: "Latest per steward" }, { value: "all", label: "All runs" }], show, (v) => refresh(recipe.id, rev, v), "Runs shown")));

    const table = h("table", { class: "board-table" },
      h("caption", { class: "sr-only" }, `Leaderboard of ${recipe.name}, revision ${rev || "unknown"}`),
      h("thead", null, h("tr", null,
        h("th", { scope: "col", class: "c-rank" }, "#"), h("th", { scope: "col", class: "c-who" }, "Steward"), h("th", { scope: "col", class: "c-score" }, "Score"),
        h("th", { scope: "col", class: "c-rel", title: "Score relative to the expert reference on the same seeds" }, "× expert"),
        h("th", { scope: "col", class: "c-seeds" }, "Seeds"), h("th", { scope: "col", class: "c-wb", title: "Wellbeing-years above a neutral point of 2" }, "WELLBY"),
        h("th", { scope: "col", class: "c-dom" }, "Domains"), h("th", { scope: "col", class: "c-date" }, "Played"), h("th", { scope: "col", class: "c-go" }, h("span", { class: "sr-only" }, "Open")))));
    const tbody = h("tbody");
    rows.forEach((row, i) => tbody.append(boardRow(row, ex, scales, header, i)));
    table.append(tbody);
    const el = h("section", { class: "view board-view" },
      sectionHead("Arena", "Leaderboard", "Every exported run of a recipe, ranked by score: the mean over seeds, where a collapsed seed scores zero. Reference stewards are scripted milestones and are not ranked. Open a run to see everything its steward did, week by week."),
      controls,
      h("div", { class: "board-meta" },
        h("span", { class: "bm-title" }, h("b", null, recipe.title), recipe.subtitle ? h("span", null, recipe.subtitle) : null),
        U.badge(`rev ${rev || "—"}`, "", "cycle"),
        U.badge(`${models} model${models === 1 ? "" : "s"}`, "info", "person"),
        U.badge(`${rows.length} run${rows.length === 1 ? "" : "s"} shown`, "", "runs"),
        h("span", { class: "legend-ref" }, U.refTag(), "scripted milestone, not ranked")),
      rows.length ? h("div", { class: "card board-card" }, table) : U.emptyNote("board", "No runs on this revision", "Pick another revision or open more files."));
    return { el, title: "Leaderboard" };
  }
  function boardRow(row, expert, scales, header, i) {
    const e = row.entry;
    const href = runHref(e.id);
    const collapsedTxt = e.collapsed ? `${e.collapsed}/${e.seeds} collapsed` : null;
    const tr = h("tr", { class: "brow" + (e.isRef ? " ref" : "") + (e.fullyCollapsed ? " ruined" : "") + (row.older ? " older" : "") + (row.rank === 1 ? " top" : ""), style: { "--i": Math.min(i, 14) } },
      h("td", { class: "c-rank" }, e.isRef ? h("span", { class: "rank ref", title: "Reference steward: not ranked" }, "–", h("span", { class: "sr-only" }, "not ranked")) : h("span", { class: "rank" }, String(row.rank))),
      h("td", { class: "c-who" },
        h("div", { class: "who" }, U.avatar(e.model, e.isRef, "xs"),
          h("div", { class: "who-text" },
            h("a", { href, class: "who-link" }, e.model),
            h("small", null, [e.isRef ? null : e.snapshot || null, row.older ? "older run" : null].filter(Boolean).join(" · "), h("span", { class: "only-phone" }, `${!e.isRef && e.snapshot ? " · " : ""}${U.dateShort(e.created)}`)),
            e.isRef ? U.refTag() : null))),
      h("td", { class: "c-score" },
        e.fullyCollapsed ? h("span", { class: "score zero" }, "0") : h("span", { class: "score" }, U.pts(e.total)),
        h("small", { class: "only-phone" }, e.fullyCollapsed ? "collapsed" : `${U.times(e.relative)} expert`),
        e.fullyCollapsed ? U.ruinsBadge(e.collapse && U.isNum(e.collapse.week) ? `week ${e.collapse.week}` : "collapsed") : null),
      h("td", { class: "c-rel" }, U.times(e.relative)),
      h("td", { class: "c-seeds" }, String(e.seeds), collapsedTxt && !e.fullyCollapsed ? h("small", { class: "bad-txt" }, collapsedTxt) : null),
      h("td", { class: "c-wb" }, U.isNum(e.wellby) ? U.fixed(e.wellby, 1) : "—"),
      h("td", { class: "c-dom" }, U.equalizer(e.domains, expert ? expert.domains : null, scales, header)),
      h("td", { class: "c-date", title: U.dateTime(e.created) }, U.dateShort(e.created)),
      h("td", { class: "c-go" }, icon("chevR")));
    tr.addEventListener("click", (event) => {
      if (event.target.closest("a, button")) return;
      if (window.getSelection && String(window.getSelection())) return;
      go(href);
    });
    return tr;
  }

  // ================================================================== boot
  const App = { store, D, U, model, expertFor, runHref, boardHref, go, replaceHash, openDataPanel, backLink, sectionHead, notFound };
  window.ExplorerApp = App;

  function boot() {
    wireFiles();
    window.addEventListener("hashchange", () => route(false, true));
    let lastStatus = "";
    D.onChange(() => {
      renderChrome();
      if (store.status === "loading" && lastStatus === "loading") return;
      lastStatus = store.status;
      route(true);
    });
    renderChrome();
    route(true);
    D.boot();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
})();
