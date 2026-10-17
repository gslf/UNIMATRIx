"use strict";
/*
 * UNIMATRIx Explorer · the run explorer: everything the steward did, week by week.
 *
 *   stage      the animated village (vendor/village.js): the steward walks to where it acts and speaks
 *   transport  play/pause, previous/next week, scrubber with turning points, speed, full screen
 *   inspector  Steward (actions and their outcome, notebook, forecasts, chronicle) · Mind (the briefing the
 *              model read, its reasoning, the raw answer, tokens, latency, errors) · People (each resident over
 *              time and every interaction with the steward) · Story (the story graph and the path taken)
 *   below      quest log of every action of the seed, vital signs, final scoreboard and diagnosis
 *
 * Keyboard: Space/K play-pause · ←/→ (J/L) week · Home/End · [ ] speed · F full screen · 1–4 inspector tabs.
 */
(function () {
  const D = window.ExplorerData;
  const U = window.ExplorerUI;
  const { h, icon } = U;
  const SPEEDS = [0.5, 1, 2, 4, 8];
  const TABS = [
    { id: "steward", label: "Steward", icon: "scroll" },
    { id: "mind", label: "Mind", icon: "brain" },
    { id: "people", label: "People", icon: "users" },
    { id: "story", label: "Story", icon: "graph" },
  ];
  const KIND = {
    event: { icon: "flag", label: "Event", cls: "event" },
    crisis: { icon: "alert", label: "Crisis", cls: "crisis" },
    branch: { icon: "route", label: "Turning point", cls: "branch" },
    flourish: { icon: "sparkle", label: "Outcome", cls: "flourish" },
    collapse: { icon: "ruins", label: "Collapse", cls: "collapse" },
  };
  const EVENT_LABELS = {
    promises_kept: ["Promises kept", "check"], promises_broken: ["Promises broken", "alert"], conflicts_resolved: ["Disputes settled", "shield"],
    violence: ["Violent escalations", "alert"], concerns_relieved: ["Concerns relieved", "heart"], apprentice_levels: ["Apprentice levels", "book"],
    deaths: ["Deaths", "grave"], departures: ["Departures", "back"], harvest: ["Fish harvested", "lake"],
  };
  const V = () => window.SocietyVillage;
  let prefs = { tab: "steward", mind: "answer", spoilers: false };

  function render({ id, params, app }) {
    const store = D.store;
    const entry = store.byId.get(id);
    if (!entry) return { el: app.notFound("Run not found", `No loaded run has the id “${id || ""}”. Open its file to explore it.`), title: "Run not found" };
    const header = store.header;
    const expert = app.expertFor(entry);
    const recipeTitle = U.splitName(entry.recipeName)[0];

    // ============================================================ static layout
    const headScore = h("strong", null, U.pts(entry.total));
    const seedBox = h("div", { class: "seed-box", hidden: true });
    const jumpCollapse = h("button", { type: "button", class: "small danger-ghost", hidden: true }, icon("skull", "sm"), h("span", null, "Jump to the collapse"));
    const headEl = h("div", { class: "run-head" },
      h("div", { class: "rh-main" }, U.avatar(entry.model, entry.isRef, "lg"),
        h("div", { class: "rh-text" },
          h("p", { class: "eyebrow" }, `Run explorer · ${recipeTitle}`),
          h("h1", null, entry.model, entry.isRef ? U.refTag() : null),
          h("p", { class: "rh-sub" }, [entry.isRef ? "scripted reference steward" : entry.snapshot || null, U.dateTime(entry.created) || null,
            entry.revision ? `rev ${entry.revision}` : null, `${entry.seeds} seed${entry.seeds === 1 ? "" : "s"}`].filter(Boolean).join(" · ")))),
      h("div", { class: "rh-side" },
        h("div", { class: "rh-score" }, headScore, h("span", null, entry.fullyCollapsed ? "every seed collapsed" : `${U.times(entry.relative)} expert${entry.seeds > 1 ? ` · mean of ${entry.seeds} seeds` : ""}`)),
        entry.collapsed ? U.ruinsBadge(entry.fullyCollapsed ? (entry.collapse && U.isNum(entry.collapse.week) ? `week ${entry.collapse.week}` : "collapsed") : `${entry.collapsed}/${entry.seeds} seeds`) : U.badge("survived", "good", "shield")),
      h("div", { class: "rh-bar" }, seedBox, jumpCollapse));

    // stage
    const villageBox = h("div", { class: "village-box" });
    const hudPop = h("b", null, "–"), hudWb = h("b", null, "–"), hudLake = h("b", null, "–"), hudPts = h("b", null, "0");
    const hud = h("div", { class: "hud", hidden: true },
      h("span", { class: "hud-chip", title: "Residents present" }, icon("users"), hudPop),
      h("span", { class: "hud-chip", title: "Mean wellbeing (0–10)" }, icon("smile"), hudWb),
      h("span", { class: "hud-chip", title: "Lake stock" }, icon("lake"), hudLake),
      h("span", { class: "hud-chip hud-points", title: "Points earned so far" }, icon("star"), hudPts, h("span", null, "pts")));
    const progressBar = h("i");
    const progressText = h("span", null, "Fetching the run…");
    const loading = h("div", { class: "stage-loading", role: "status" }, h("span", { class: "spinner", "aria-hidden": "true" }), progressText,
      h("div", { class: "progress", "aria-hidden": "true" }, progressBar));
    const endscreen = h("div", { class: "endscreen", hidden: true, role: "dialog", "aria-label": "End of the seed" });
    const stage = h("div", { class: "stage" }, villageBox, hud, loading, endscreen);

    const btnPrev = h("button", { type: "button", class: "icon-btn tb", "aria-label": "Previous week", title: "Previous week (←)" }, icon("prev"));
    const playIcon = h("span", { class: "play-ic", "aria-hidden": "true" }, icon("play", "fill"));
    const btnPlay = h("button", { type: "button", class: "play-btn", "aria-label": "Play", title: "Play / pause (Space)" }, playIcon);
    const btnNext = h("button", { type: "button", class: "icon-btn tb", "aria-label": "Next week", title: "Next week (→)" }, icon("next"));
    const weekLabel = h("span", { class: "pb-week" }, h("b", null, "Week –"));
    const scrub = h("input", { type: "range", min: 0, max: 0, step: 1, value: 0, "aria-label": "Week", class: "scrub" });
    const ticks = h("div", { class: "pb-ticks", "aria-hidden": "true" });
    const speedSel = h("select", { class: "speed", "aria-label": "Playback speed", title: "Speed ([ and ])" }, SPEEDS.map((s) => h("option", { value: String(s) }, `${s}×`)));
    speedSel.value = "1";
    const btnFull = h("button", { type: "button", class: "icon-btn tb", "aria-label": "Full screen map", "aria-pressed": "false", title: "Full screen (F)" }, icon("expand"));
    const playbar = h("div", { class: "playbar" },
      h("div", { class: "pb-buttons" }, btnPrev, btnPlay, btnNext, weekLabel),
      h("div", { class: "pb-scrub" }, scrub, ticks),
      h("div", { class: "pb-tools" }, speedSel, btnFull));
    const stageCard = h("section", { class: "card stage-card", "aria-label": "Village replay" }, stage, playbar);

    // score HUD
    const tkTotal = h("strong", { class: "tk-total" }, "0");
    const tkSub = h("span", { class: "tk-sub" }, "points so far");
    const tkDelta = h("div", { class: "tk-delta" });
    const tkDomains = h("div", { class: "tk-domains" });
    const scoreCard = h("section", { class: "card score-card", "aria-label": "Score so far" }, h("div", { class: "tk-head" }, icon("star"), tkTotal, tkSub, tkDelta), tkDomains);

    // inspector
    const tabButtons = new Map();
    const tabPanels = new Map();
    const tablist = h("div", { class: "tabs", role: "tablist", "aria-label": "Week inspector" });
    for (const t of TABS) {
      const count = h("em", { class: "tab-count" });
      const b = h("button", { type: "button", role: "tab", id: `tab-${t.id}`, "aria-controls": `panel-${t.id}`, "aria-selected": "false", tabindex: "-1", class: "tab" }, icon(t.icon, "sm"), h("span", null, t.label), count);
      b._count = count;
      b.addEventListener("click", () => selectTab(t.id, true));
      tabButtons.set(t.id, b);
      tablist.append(b);
      tabPanels.set(t.id, h("div", { class: "tab-panel", role: "tabpanel", id: `panel-${t.id}`, "aria-labelledby": `tab-${t.id}`, tabindex: "0", hidden: true }));
    }
    tablist.addEventListener("keydown", (event) => {
      const ids = TABS.map((t) => t.id);
      const at = ids.indexOf(prefs.tab);
      let next = null;
      if (event.key === "ArrowRight") next = ids[(at + 1) % ids.length];
      else if (event.key === "ArrowLeft") next = ids[(at - 1 + ids.length) % ids.length];
      else if (event.key === "Home") next = ids[0];
      else if (event.key === "End") next = ids[ids.length - 1];
      if (next) { event.preventDefault(); event.stopPropagation(); selectTab(next, true); tabButtons.get(next).focus(); }
    });
    const inspector = h("section", { class: "card inspector", "aria-label": "Week inspector" },
      h("div", { class: "insp-head" }, tablist), ...tabPanels.values());

    // below
    const questCard = h("section", { class: "card questlog", "aria-labelledby": "ql-title" });
    const vitalsCard = h("section", { class: "card vitals-card", "aria-labelledby": "vt-title" });
    const results = h("section", { class: "card results", id: "results", "aria-labelledby": "results-title" });
    const kbd = h("p", { class: "kbd-hint" }, icon("keyboard", "sm"), "Space play/pause · ←/→ week · Home/End · [ ] speed · F full screen · 1–4 inspector tabs");

    const grid = h("div", { class: "run-grid" },
      h("div", { class: "col-stage" }, stageCard, scoreCard),
      h("div", { class: "col-inspect" }, inspector));
    const below = [questCard, vitalsCard, results, kbd];
    const el = h("section", { class: "view run-view" }, app.backLink(app.boardHref(entry.recipe, entry.revision), "Leaderboard"), headEl, grid, ...below);
    for (const c of [scoreCard, inspector, ...below]) c.classList.add("dim-until-ready");

    // ============================================================ state
    let destroyed = false;
    let village = null;
    let run = null;
    let seed = null;
    let frames = [];
    let index = 0;
    let charts = [];
    let personCharts = [];
    let finalMax = 1;
    let selectedPerson = null;
    let openPerson = null;
    let endShownFor = null;
    let ready = false; // the address is rewritten only once the deep link (seed, week) has been applied
    let perSeed = null; // derived per seed: actions, interactions, fates, quest items
    const offs = [];

    // ============================================================ load
    D.getRun(entry.id, (frac) => {
      if (destroyed) return;
      if (U.isNum(frac)) { progressBar.style.width = `${Math.round(frac * 100)}%`; progressText.textContent = `Fetching the run · ${Math.round(frac * 100)}%`; }
      else { loading.classList.add("indeterminate"); progressText.textContent = "Fetching the run…"; }
    }).then((r) => { if (!destroyed) setup(r); }).catch((error) => { if (!destroyed) fail(error); });

    function fail(error) {
      loading.classList.add("failed");
      U.fill(loading, U.emptyNote("alert", "This run cannot be explored", error.message || String(error),
        U.actionButton("upload", "Open files", "primary small", () => app.openDataPanel())));
      for (const c of [scoreCard, inspector, ...below]) c.hidden = true;
      playbar.classList.add("disabled");
      for (const b of [btnPrev, btnPlay, btnNext, scrub, speedSel, btnFull]) b.disabled = true;
    }

    function setup(r) {
      run = r;
      if (!V()) { fail(new Error("The village renderer (vendor/village.js) did not load.")); return; }
      for (const c of [scoreCard, inspector, ...below]) c.classList.remove("dim-until-ready");
      village = V().mount(villageBox, { speed: 1, weekMs: 1500, captions: true, captionsAt: "bottom", steward: true, thoughts: true, notebook: false, expand: true, interactive: true, labels: false });
      offs.push(village.on("frame", onFrame), village.on("action", onAction), village.on("play", syncPlay), village.on("pause", syncPlay),
        village.on("end", onEnd), village.on("select", (rid) => { selectedPerson = rid; markSelected(); }),
        village.on("expand", (on) => btnFull.setAttribute("aria-pressed", on ? "true" : "false")));
      const seeds = run.seeds.map((s) => s.seed).filter((s) => (run.frames.get(s) || []).length);
      if (seeds.length > 1) {
        seedBox.hidden = false;
        U.fill(seedBox, h("span", { class: "label" }, "Seed"), U.segmented("seed", seeds.map((s) => {
          const info = run.seeds.find((x) => x.seed === s);
          return { value: String(s), label: `${s}`, icon: info && info.collapsed ? "ruins" : null, cls: info && info.collapsed ? "seg-bad" : null,
            title: info ? (info.collapsed ? `Seed ${s}: collapsed in week ${info.collapsed.week}` : `Seed ${s}: ${U.pts(info.total)} points`) : "" };
        }), String(seeds[0]), (v) => switchSeed(Number(v), true), "Seed"));
      }
      loading.classList.add("done");
      hud.hidden = false;
      const want = Number(params.get("seed"));
      switchSeed(seeds.includes(want) ? want : seeds[0], false);
      const wantWeek = params.has("week") ? Number(params.get("week")) : NaN;
      if (Number.isFinite(wantWeek)) { const i = frames.findIndex((f) => f.week === wantWeek); if (i > 0) village.seek(i); }
      ready = true;
      writeHash();
      selectTab(prefs.tab, false);
    }

    function switchSeed(s, userAction) {
      if (!run) return;
      seed = s;
      frames = run.frames.get(s) || [];
      index = 0;
      endShownFor = null;
      endscreen.hidden = true;
      openPerson = null;
      const radio = seedBox.querySelector(`input[value="${s}"]`);
      if (radio) radio.checked = true;
      perSeed = derive();
      finalMax = Math.max(1, ...U.DOMAINS.map((d) => Math.max(...frames.map((f) => f.points[d] || 0))));
      scrub.max = String(Math.max(0, frames.length - 1));
      const si = seedInfo();
      jumpCollapse.hidden = !(si && si.collapsed);
      buildTicks();
      buildStory();
      buildPeople();
      buildQuestLog();
      buildVitals();
      buildResults();
      village.load({ frames, residents: run.residents, factions: run.factions,
        recipe: { world: { name: run.recipe.world, start_season: run.recipe.startSeason, commons: { name: run.recipe.commons.name, capacity: run.recipe.commons.capacity } } } });
      village.setSpeed(Number(speedSel.value) || 1);
      if (userAction) writeHash();
      syncPlay();
    }
    const seedInfo = () => (run ? run.seeds.find((x) => x.seed === seed) || run.seeds[0] : null);
    const resident = (rid) => (run && run.residents.find((x) => x.id === rid)) || null;
    const nameOf = (rid) => { const r = resident(rid); return r ? r.name : rid; };
    const factionOf = (fid) => (run && run.factions.find((f) => f.id === fid)) || { name: fid || "no faction", color: "#c8c8c8" };
    const totalOf = (f) => (f ? Object.values(f.points).reduce((a, b) => a + (U.isNum(b) ? b : 0), 0) : 0);
    const seekTo = (i) => { if (village && i >= 0) { village.pause(); village.seek(i); } };
    const seekWeek = (week) => seekTo(frames.findIndex((f) => f.week === week));

    function writeHash() {
      const f = frames[index];
      const q = new URLSearchParams();
      if (run && run.seeds.length > 1) q.set("seed", String(seed));
      if (f && index > 0) q.set("week", String(f.week));
      app.replaceHash(app.runHref(entry.id, q.toString() ? `?${q}` : ""));
    }

    /** Per-seed indexes: every action with its week, interactions per resident, fates. */
    function derive() {
      const actions = [];
      const byPerson = new Map();
      frames.forEach((f, i) => f.actions.forEach((a, k) => {
        const item = { a, k, i, week: f.week, season: f.season };
        actions.push(item);
        for (const t of a.targets) { if (!byPerson.has(t)) byPerson.set(t, []); byPerson.get(t).push(item); }
      }));
      const fates = new Map();
      for (const f of frames) for (const r of f.residents) {
        const prev = fates.get(r.id);
        if (!prev) fates.set(r.id, { first: f.week, status: r.status, week: r.status === "present" ? null : f.week, wb: r.wb });
        else {
          if (r.status !== "present" && prev.status === "present") prev.week = f.week;
          prev.status = r.status;
          prev.wb = r.wb;
        }
      }
      const verbs = [...new Set(actions.map((x) => x.a.verb))].sort();
      return { actions, byPerson, fates, verbs };
    }

    // ============================================================ per-frame updates
    function onFrame(i) {
      index = i;
      const f = frames[i];
      if (!f) return;
      const prev = frames[i - 1];
      const m = f.metrics;
      scrub.value = String(i);
      scrub.setAttribute("aria-valuetext", `Week ${f.week}${f.season ? ", " + f.season : ""}`);
      U.fill(weekLabel, h("b", null, `Week ${f.week}`), h("span", null, `${f.season ? U.capitalize(f.season) : ""} · ${i + 1}/${frames.length}`));
      btnPrev.disabled = i <= 0;
      btnNext.disabled = i >= frames.length - 1;
      hudPop.textContent = U.isNum(m.population) ? String(m.population) : "–";
      hudWb.textContent = U.isNum(m.wellbeing) ? m.wellbeing.toFixed(1) : "–";
      hudLake.textContent = U.isNum(m.commons) ? U.pct(m.commons) : U.isNum(f.commons.stock) ? U.pct(f.commons.stock / (run.recipe.commons.capacity || 1000)) : "–";
      const total = totalOf(f);
      U.countTo(hudPts, total, 600);
      updateScore(f, prev, total);
      stageCard.classList.toggle("collapsing", Boolean(f.collapsed));
      const nAct = f.actions.length, nBad = f.actions.filter((a) => !a.ok).length;
      tabButtons.get("steward")._count.textContent = nAct ? String(nAct) : "";
      tabButtons.get("steward").classList.toggle("warn", nBad > 0);
      tabButtons.get("mind").classList.toggle("warn", Boolean(f.decision && f.decision.error));
      tabButtons.get("mind")._count.textContent = f.decision && f.decision.error ? "!" : "";
      tabButtons.get("people")._count.textContent = U.isNum(m.population) ? String(m.population) : "";
      tabButtons.get("story")._count.textContent = f.story.length ? `+${f.story.length}` : "";
      renderTab();
      updateQuestLog(f);
      for (const c of charts) c.setIndex(i);
      for (const c of personCharts) c.setIndex(i);
      if (i < frames.length - 1 && !endscreen.hidden) endscreen.hidden = true;
      if (ready && village && !village.isPlaying()) writeHash();
    }
    function updateScore(f, prev, total) {
      scoreCard.classList.toggle("collapsed", Boolean(f.collapsed));
      if (f.collapsed) {
        U.countTo(tkTotal, total, 700);
        tkSub.textContent = "final points · retained at collapse";
        tkDelta.className = "tk-delta bad";
        U.fill(tkDelta, U.ruinsBadge("collapse"));
      } else {
        U.countTo(tkTotal, total, 700);
        tkSub.textContent = f === frames[frames.length - 1] ? "final points" : "points so far";
        const delta = prev ? total - totalOf(prev) : 0;
        tkDelta.className = "tk-delta" + (delta > 0 ? " up" : delta < 0 ? " down" : "");
        U.fill(tkDelta, h("span", null, delta > 0 ? `▲ +${U.pts(delta)}` : delta < 0 ? `▼ ${U.pts(delta)}` : "±0"), h("small", null, " this week"));
      }
      U.fill(tkDomains, ...U.DOMAINS.map((d) => {
        const v = f.points[d] || 0;
        const dv = prev ? v - (prev.points[d] || 0) : 0;
        return h("div", { class: "tk-row", style: { "--c": U.DOMAIN_COLORS[d] }, title: `${U.domainName(d, header)}: ${U.pts(v)} points` },
          h("span", { class: "tk-ic" }, icon(U.DOMAIN_INFO[d].icon)),
          h("span", { class: "tk-name" }, U.domainName(d, header)),
          h("span", { class: "tk-bar" }, h("i", { style: { width: `${Math.min(100, (v / finalMax) * 100)}%` } })),
          h("span", { class: "tk-val" }, U.pts(v), dv > 0.5 ? h("small", null, `+${U.pts(dv)}`) : null));
      }));
    }
    function syncPlay() {
      const playing = village ? village.isPlaying() : false;
      U.fill(playIcon, icon(playing ? "pause" : "play", "fill"));
      btnPlay.setAttribute("aria-label", playing ? "Pause" : "Play");
      btnPlay.classList.toggle("playing", playing);
      stageCard.classList.toggle("is-playing", playing);
      if (!playing && village && ready) writeHash();
    }
    function onAction(info) {
      if (!info || prefs.tab !== "steward") return;
      const panel = tabPanels.get("steward");
      for (const item of panel.querySelectorAll(".act.acting")) item.classList.remove("acting");
      if (info.index < 0 || info.frameIndex !== index) return;
      const item = panel.querySelector(`.act[data-k="${info.index}"]`);
      if (!item) return;
      item.classList.add("acting");
      const box = panel;
      const top = item.offsetTop - box.offsetTop;
      if (box.scrollHeight > box.clientHeight + 4 && (top < box.scrollTop || top + item.offsetHeight > box.scrollTop + box.clientHeight)) box.scrollTop = Math.max(0, top - 12);
    }
    function onEnd() {
      const s = seedInfo();
      if (!s || endShownFor === seed) return;
      endShownFor = seed;
      showEndscreen(s);
    }

    // ============================================================ inspector tabs
    function selectTab(idTab, user) {
      prefs.tab = idTab;
      for (const [k, b] of tabButtons) {
        const on = k === idTab;
        b.setAttribute("aria-selected", on ? "true" : "false");
        b.tabIndex = on ? 0 : -1;
        tabPanels.get(k).hidden = !on;
      }
      if (run) renderTab(true);
      if (user && idTab === "people" && selectedPerson && !openPerson) openPersonDetail(selectedPerson);
    }
    function renderTab(force) {
      const f = frames[index];
      if (!f) return;
      if (prefs.tab === "steward") renderSteward(f);
      else if (prefs.tab === "mind") renderMind(f);
      else if (prefs.tab === "people") updatePeople(f, force);
      else if (prefs.tab === "story") updateStory(f);
    }

    // ------------------------------------------------------------ Steward
    function splitReply(a) {
      const text = String(a.text || "").trim();
      const m = /^([^:"“]{2,48}):\s+([\s\S]*)$/.exec(text);
      if (m && (a.verb === "talk" || a.verb === "promise")) return { speaker: m[1], text: m[2] };
      return { speaker: null, text };
    }
    function actionView(a, k, compact) {
      const meta = V() ? V().describeAction(a, nameOf) : { icon: "❔", label: a.label || a.verb };
      const reply = splitReply(a);
      const who = [a.targets.length ? `→ ${a.targets.map(nameOf).join(" & ")}` : null, a.place ? `at the ${a.place}` : null].filter(Boolean).join(" · ");
      return h("div", { class: "act" + (a.ok ? "" : " rejected") + (compact ? " compact" : ""), role: "listitem", dataset: { k: String(k) } },
        h("span", { class: "act-icon", "aria-hidden": "true" }, meta.icon),
        h("div", { class: "act-body" },
          h("div", { class: "act-head" }, h("b", null, meta.label), h("span", { class: "status " + (a.ok ? "ok" : "bad") }, a.ok ? "applied" : a.status)),
          who ? h("div", { class: "act-who" }, who) : null,
          a.say ? h("div", { class: "act-say" }, `“${a.say}”`) : null,
          reply.text ? h("div", { class: "act-out" + (a.ok ? "" : " why") }, a.ok ? null : h("b", null, "Why: "), reply.speaker ? h("b", null, `${reply.speaker}: `) : null, reply.text) : null,
          a.label && !compact ? h("code", { class: "act-code", title: "The action as the engine recorded it" }, a.label) : null));
    }
    function notebookView(f) {
      const st = f.steward || {};
      const forecasts = Object.entries(st.forecasts || {});
      if (!st.note && !st.error && !forecasts.length) return null;
      return h("div", { class: "notebook-box" },
        h("div", { class: "nb-head" }, icon("notebook", "sm"), "The steward's notebook"),
        st.note ? h("p", { class: "nb-note" }, st.note) : null,
        forecasts.length ? h("div", { class: "nb-fc" }, h("span", { class: "label" }, "Forecasts"), forecasts.map(([k, v]) => {
          const p = Number(v);
          return h("span", { class: "fc" }, h("b", null, U.humanize(String(k).replace(/^p[-_]/i, ""))), h("span", { class: "fc-bar" }, h("i", { style: { width: `${U.isNum(p) ? Math.round(Math.max(0, Math.min(1, p)) * 100) : 0}%` } })), U.isNum(p) ? `${Math.round(p * 100)}%` : String(v));
        })) : null,
        st.error ? h("p", { class: "nb-err" }, icon("alert", "sm"), st.error) : null);
    }
    function renderSteward(f) {
      const panel = tabPanels.get("steward");
      const nBad = f.actions.filter((a) => !a.ok).length;
      const parts = [
        h("div", { class: "insp-title" }, h("h2", null, `What the steward did in week ${f.week}`),
          h("span", { class: "chips" }, U.badge(`${f.actions.length} action${f.actions.length === 1 ? "" : "s"}`, "", "bolt"), nBad ? U.badge(`${nBad} rejected`, "bad", "x") : null)),
        notebookView(f),
      ];
      if (!f.actions.length) {
        parts.push(h("p", { class: "muted small pad" }, f.week === 0 ? "Week 0 is the starting state: the steward acts from week 1." : "The steward took no action this week."));
      } else {
        parts.push(h("div", { class: "acts", role: "list", "aria-label": `Actions of week ${f.week}` }, f.actions.map((a, k) => actionView(a, k))));
      }
      if (f.events.length) {
        parts.push(h("div", { class: "chronicle" }, h("h3", null, icon("book", "sm"), "Village chronicle"),
          h("ul", null, f.events.map((e) => h("li", { class: /COLLAPSE|riot|died|dies|dead|famine|revolt|collapse/i.test(e) ? "bad" : /settled|recovers|welcomed|reached level|completed|integrated/i.test(e) ? "good" : "" }, e)))));
      }
      U.fill(panel, ...parts.filter(Boolean));
    }

    // ------------------------------------------------------------ Mind (the raw decision)
    function prettyAnswer(text) {
      try {
        const parsed = JSON.parse(text);
        return { parsed, pretty: JSON.stringify(parsed, null, 2) };
      } catch (e) { return { parsed: null, pretty: text }; }
    }
    function askedView(parsed) {
      const list = Array.isArray(parsed && parsed.actions) ? parsed.actions : [];
      if (!list.length) return h("p", { class: "muted small" }, "The answer asked for no action.");
      return h("ol", { class: "asked" }, list.map((x) => {
        const o = x && typeof x === "object" ? x : {};
        const verb = String(o.do || o.verb || o.action || "?");
        const rest = Object.entries(o).filter(([k]) => !["do", "verb", "action", "say"].includes(k))
          .map(([k, v]) => `${k} ${k === "to" || k === "who" || k === "with" ? (Array.isArray(v) ? v.map(nameOf).join(" & ") : nameOf(String(v))) : typeof v === "object" ? JSON.stringify(v) : String(v)}`);
        return h("li", null, h("b", null, verb), rest.length ? h("span", null, ` · ${rest.join(" · ")}`) : null, o.say ? h("span", { class: "asked-say" }, ` “${o.say}”`) : null);
      }));
    }
    function renderMind(f) {
      const panel = tabPanels.get("mind");
      if (run.isRef || !run.hasDecisions) {
        U.fill(panel, h("div", { class: "insp-title" }, h("h2", null, "Inside the steward's mind")),
          U.emptyNote("robot", run.isRef ? "A scripted reference steward" : "No decision log in this file",
            run.isRef ? "Reference stewards follow fixed rules, so there is no briefing, reasoning or answer to inspect. Everything they did is in the Steward tab and the quest log." : "This run file carries no #decision values (it was exported before decisions were recorded)."));
        return;
      }
      const d = f.decision;
      if (!d) {
        U.fill(panel, h("div", { class: "insp-title" }, h("h2", null, `Inside the steward's mind · week ${f.week}`)),
          U.emptyNote("clock", f.week === 0 ? "Week 0 is the starting state" : "No decision recorded this week", f.week === 0 ? "The model reads its first briefing and answers in week 1." : "The decision log has no row for this week."));
        return;
      }
      const stat = (label, value, title) => h("span", { class: "mstat", title: title || null }, h("b", null, value), label);
      const stats = h("div", { class: "mind-stats" },
        stat("input tokens", U.isNum(d.inputTokens) ? U.pts(d.inputTokens) : "—"),
        stat("generated", U.isNum(d.generatedTokens) ? U.pts(d.generatedTokens) : "—"),
        U.isNum(d.reasoningTokens) ? stat("reasoning", U.pts(d.reasoningTokens)) : null,
        stat("latency", d.latencyText ? `${d.latencyText} s` : U.isNum(d.latency) ? `${d.latency.toFixed(3)} s` : "—"),
        d.finishReason ? stat("finish", d.finishReason, "Why generation stopped") : null,
        d.retries ? stat("attempts", String(d.retries), "Requests needed for this week") : null);
      const answer = d.answer ? prettyAnswer(d.answer) : null;
      const panes = {
        briefing: { label: "Read", icon: "inbox", title: "The briefing the model read this week",
          body: () => (d.briefing ? h("pre", { class: "mind-text" }, d.briefing) : U.emptyNote("inbox", "No briefing recorded", "The decision log kept no observation for this week.")) },
        reasoning: { label: "Thought", icon: "brain", title: "The model's reasoning, when the provider exposes it",
          body: () => (d.reasoning ? h("pre", { class: "mind-text thought" }, d.reasoning) : U.emptyNote("brain", "No reasoning exposed", `The provider did not return a chain of thought for this week${U.isNum(d.reasoningTokens) && d.reasoningTokens > 0 ? `, although it reports ${U.pts(d.reasoningTokens)} reasoning tokens` : ""}.`)) },
        answer: { label: "Answered", icon: "code", title: "The raw answer the model gave",
          body: () => (answer ? h("div", { class: "answer" },
            answer.parsed ? h("div", { class: "asked-box" }, h("h3", null, icon("bolt", "sm"), "It asked for"), askedView(answer.parsed),
              answer.parsed.note ? h("p", { class: "asked-note" }, h("b", null, "Note to self: "), String(answer.parsed.note)) : null) : null,
            h("h3", null, icon("code", "sm"), answer.parsed ? "Raw JSON" : "Raw answer (not valid JSON)"),
            h("pre", { class: "mind-text code" }, answer.pretty)) : U.emptyNote("code", "No answer recorded", "The model returned nothing this week.")) },
      };
      const pipe = h("div", { class: "pipeline", role: "radiogroup", "aria-label": "Decision step" });
      ["briefing", "reasoning", "answer"].forEach((key, i) => {
        const p = panes[key];
        const empty = key === "briefing" ? !d.briefing : key === "reasoning" ? !d.reasoning : !d.answer;
        const b = h("button", { type: "button", role: "radio", class: "pipe" + (empty ? " empty" : ""), "aria-checked": prefs.mind === key ? "true" : "false", title: p.title },
          h("span", { class: "pipe-n" }, String(i + 1)), icon(p.icon, "sm"), h("span", null, p.label));
        b.addEventListener("click", () => { prefs.mind = key; renderMind(frames[index]); });
        pipe.append(b);
        if (i < 2) pipe.append(h("span", { class: "pipe-arrow", "aria-hidden": "true" }, "→"));
      });
      U.fill(panel, 
        h("div", { class: "insp-title" }, h("h2", null, `Inside the steward's mind · week ${f.week}`)),
        stats,
        d.error ? h("div", { class: "alert" }, h("b", null, "The answer could not be fully used"), d.error) : null,
        pipe,
        h("div", { class: "mind-pane" }, panes[prefs.mind].body()));
    }

    // ------------------------------------------------------------ People
    let peopleRows = new Map();
    let peopleList = null;
    let peopleSum = null;
    function buildPeople() {
      peopleRows = new Map();
      peopleSum = h("div", { class: "people-sum" });
      peopleList = h("ul", { class: "people-list" });
      for (const r of run.residents) {
        const fac = factionOf(r.faction);
        const fate = perSeed.fates.get(r.id);
        const fateChip = !fate ? U.badge("never arrives", "") : fate.status === "dead" ? U.badge(`dies · w${fate.week}`, "bad", "grave")
          : fate.status === "departed" ? U.badge(`leaves · w${fate.week}`, "watch", "back") : U.badge(`stays · ${U.fixed(fate.wb, 1)}`, "good", "shield");
        const dot = h("span", { class: "p-dot", style: { "--fc": fac.color } });
        const now = h("span", { class: "p-now" });
        const flags = h("span", { class: "p-flags" });
        const talks = (perSeed.byPerson.get(r.id) || []).length;
        const button = h("button", { type: "button", class: "p-btn", "aria-label": `${r.name}, ${r.role}, ${fac.name}. Open the details.` },
          dot, h("span", { class: "p-main" }, h("b", null, r.name, r.newcomer ? h("em", { class: "p-new" }, "newcomer") : null),
            h("small", null, `${r.role} · ${fac.name}${talks ? ` · ${talks} interaction${talks === 1 ? "" : "s"}` : ""}`)),
          now, flags, h("span", { class: "p-fate", title: "Fate at the end of this seed" }, fateChip));
        button.addEventListener("click", () => openPersonDetail(r.id));
        const li = h("li", { class: "person" }, button);
        peopleList.append(li);
        peopleRows.set(r.id, { li, dot, now, flags });
      }
    }
    function markSelected() {
      for (const [pid, row] of peopleRows) row.li.classList.toggle("selected", pid === selectedPerson);
    }
    function updatePeople(f, force) {
      const panel = tabPanels.get("people");
      if (openPerson) { updatePersonDetail(f); if (force && !panel.contains(openPerson.el)) U.fill(panel, openPerson.el); return; }
      if (!panel.contains(peopleList)) U.fill(panel, h("div", { class: "insp-title" }, h("h2", null, "The people of the village"), h("span", { class: "muted small" }, "Open someone to follow them over time")), peopleSum, peopleList);
      const byId = new Map(f.residents.map((r) => [r.id, r]));
      let present = 0, dead = 0, left = 0, protest = 0, sick = 0, newcomers = 0;
      for (const r of run.residents) {
        const row = peopleRows.get(r.id);
        if (!row) continue;
        const d = byId.get(r.id);
        row.li.classList.toggle("absent", !d || d.status !== "present");
        row.li.dataset.status = d ? d.status : "away";
        if (!d) { row.now.textContent = r.newcomer ? `arrives week ${U.isNum(r.joined) ? r.joined : "?"}` : "not here"; row.flags.replaceChildren(); row.dot.style.setProperty("--wb", "#555"); continue; }
        if (d.status === "dead") dead += 1; else if (d.status === "departed") left += 1; else present += 1;
        if (d.status === "present" && r.newcomer) newcomers += 1;
        if (d.protest) protest += 1;
        if (d.sick) sick += 1;
        row.dot.style.setProperty("--wb", U.wbColor(d.wb));
        row.now.textContent = d.status === "dead" ? "dead" : d.status === "departed" ? "left the village" : `${(V() && V().ACTIVITY_LABEL[d.activity]) || d.activity} · wellbeing ${U.fixed(d.wb, 1)}`;
        U.fill(row.flags, ...flagsOf(d));
      }
      const chip = (n, label, cls) => h("span", { class: "ps " + (cls || "") }, h("b", null, String(n)), ` ${label}`);
      U.fill(peopleSum, chip(present, "present"), dead ? chip(dead, "dead", "bad") : null, left ? chip(left, "left", "watch") : null,
        newcomers ? chip(newcomers, "newcomers", "info") : null, protest ? chip(protest, "protesting", "bad") : null, sick ? chip(sick, "sick", "watch") : null);
      markSelected();
    }
    function flagsOf(d) {
      const fl = [];
      if (d.protest) fl.push(h("span", { class: "flag bad", title: "Protesting" }, icon("megaphone"), "protest"));
      if (d.sick) fl.push(h("span", { class: "flag watch", title: "Sick" }, icon("plus"), "sick"));
      if (d.hungry) fl.push(h("span", { class: "flag watch", title: "Hungry" }, icon("granary"), "hungry"));
      if (d.concern) fl.push(h("span", { class: "flag concern", title: `Hidden concern${U.isNum(d.concern.intensity) ? `, intensity ${d.concern.intensity.toFixed(2)}` : ""}` }, d.concern.kind));
      return fl;
    }
    function openPersonDetail(rid) {
      const r = resident(rid);
      if (!r) return;
      selectedPerson = rid;
      if (village) village.select(rid);
      const fac = factionOf(r.faction);
      const series = (get) => frames.map((f) => { const x = f.residents.find((p) => p.id === rid); return x && x.status === "present" ? get(x) : null; });
      const weekOf = (i) => (frames[i] ? frames[i].week : i);
      personCharts = [
        U.lineChart([{ values: series((x) => x.wb), color: "#7cc47f" }], { label: "Wellbeing", icon: "smile", min: 0, max: 10, format: (v) => `${U.fixed(v, 1)}/10`, weekOf, onSeek: seekTo }),
        U.lineChart([{ values: series((x) => x.tr), color: "#d55181" }], { label: "Trust in the steward", icon: "heart", min: 0, max: 1, format: (v) => U.fixed(v, 2), weekOf, onSeek: seekTo }),
        U.lineChart([{ values: series((x) => x.st), color: "#ff9f43", label: "stress" }, { values: series((x) => x.lo), color: "#4aa3df", label: "loneliness" }, { values: series((x) => x.gr), color: "#ef6a5b", label: "grievance" }],
          { label: "Stress · loneliness · grievance", icon: "pulse", min: 0, max: 1, format: (v) => U.fixed(v, 2), weekOf, onSeek: seekTo }),
        U.lineChart([{ values: series((x) => x.hp), color: "#8fe3d8" }], { label: "Health", icon: "plus", min: 0, max: 100, format: (v) => U.pts(v), weekOf, onSeek: seekTo }),
        U.lineChart([{ values: series((x) => x.coins), color: "#e0b030" }], { label: "Coins", icon: "coins", min: 0, format: (v) => U.fixed(v, 1), weekOf, onSeek: seekTo }),
      ];
      // concerns over the weeks, as a strip of coloured segments
      const strip = h("div", { class: "concern-strip", role: "img", "aria-label": "Hidden concerns over the weeks" });
      const kinds = new Map();
      frames.forEach((f, i) => {
        const x = f.residents.find((p) => p.id === rid);
        const kind = x && x.status === "present" && x.concern ? x.concern.kind : null;
        if (kind && !kinds.has(kind)) kinds.set(kind, U.hueColor(kind));
        strip.append(h("i", { style: { background: kind ? kinds.get(kind) : "transparent", opacity: x && x.concern && U.isNum(x.concern.intensity) ? 0.35 + 0.65 * x.concern.intensity : 1 }, title: `Week ${f.week}: ${kind || "no hidden concern"}`, onclick: () => seekTo(i) }));
      });
      const talks = perSeed.byPerson.get(rid) || [];
      const nowBox = h("div", { class: "pd-now" });
      const fate = perSeed.fates.get(rid);
      const det = h("div", { class: "person-detail" },
        h("button", { type: "button", class: "small back-btn", onclick: () => { openPerson = null; personCharts = []; selectedPerson = null; if (village) village.select(null); updatePeople(frames[index], true); } }, icon("back", "sm"), h("span", null, "All people")),
        h("div", { class: "pd-head" }, h("span", { class: "pd-dot", style: { "--fc": fac.color } }),
          h("div", null, h("h2", null, r.name), h("p", { class: "muted small" }, [U.isNum(r.age) ? `${r.age}` : null, r.role, fac.name, r.archetype ? U.humanize(r.archetype) : null, r.newcomer ? `newcomer (week ${r.joined ?? "?"})` : null].filter(Boolean).join(" · ")))),
        r.bio ? h("p", { class: "pd-bio" }, r.bio) : null,
        Object.keys(r.skills).length ? h("div", { class: "chips" }, Object.entries(r.skills).map(([k, v]) => U.badge(`${k} ${v}`, "", "book"))) : null,
        nowBox,
        h("div", { class: "pd-fate" }, h("span", { class: "label" }, "Fate on this seed"), !fate ? "never arrives" : fate.status === "dead" ? `dies in week ${fate.week}` : fate.status === "departed" ? `leaves in week ${fate.week}` : `stays to the end, wellbeing ${U.fixed(fate.wb, 1)}`),
        h("div", { class: "pd-charts" }, personCharts),
        h("div", { class: "pd-concerns" }, h("span", { class: "label" }, "Hidden concerns, week by week"), strip,
          kinds.size ? h("div", { class: "lc-legend" }, [...kinds].map(([k, c]) => h("span", null, h("i", { style: { background: c } }), k))) : h("span", { class: "muted small" }, "No hidden concern recorded.")),
        h("h3", null, icon("chat", "sm"), `The steward and ${r.name.split(" ")[0]} · ${talks.length} interaction${talks.length === 1 ? "" : "s"}`),
        talks.length ? h("div", { class: "pd-talks" }, talks.map((t) => h("button", { type: "button", class: "talk", onclick: () => seekTo(t.i) },
          h("span", { class: "q-week" }, `W${t.week}`), actionView(t.a, t.k, true)))) : h("p", { class: "muted small" }, "The steward never acted on this person on this seed."));
      openPerson = { id: rid, el: det, nowBox };
      const panel = tabPanels.get("people");
      U.fill(panel, det);
      panel.scrollTop = 0;
      updatePersonDetail(frames[index]);
      for (const c of personCharts) c.setIndex(index);
      if (prefs.tab !== "people") selectTab("people", false);
    }
    function updatePersonDetail(f) {
      if (!openPerson) return;
      const d = f.residents.find((p) => p.id === openPerson.id);
      if (!d) { U.fill(openPerson.nowBox, h("p", { class: "muted small" }, `Not in the village in week ${f.week}.`)); return; }
      const cell = (label, value) => h("div", { class: "pd-cell" }, h("b", null, value), h("span", null, label));
      U.fill(openPerson.nowBox, h("span", { class: "label" }, `Week ${f.week}`),
        h("div", { class: "pd-grid" },
          cell("status", d.status === "present" ? ((V() && V().ACTIVITY_LABEL[d.activity]) || d.activity) : d.status),
          cell("wellbeing", U.fixed(d.wb, 1)), cell("trust", U.fixed(d.tr, 2)), cell("health", U.pts(d.hp)),
          cell("coins", U.fixed(d.coins, 1)), cell("food", U.fixed(d.food, 1)), cell("task", d.task || "—"), cell("legitimacy", U.fixed(d.lg, 2))),
        h("div", { class: "p-flags" }, flagsOf(d)));
      for (const c of personCharts) c.setIndex(index);
    }

    // ------------------------------------------------------------ Story (graph + turning points)
    let storyView = null;
    function questItems() {
      const s = seedInfo();
      const nodes = new Map(run.story.nodes.map((n) => [n.id, n]));
      const items = [];
      const seen = new Set();
      const stepTo = new Map();
      for (const p of (s && s.path) || []) stepTo.set(`${p.target}@${p.week}`, p);
      for (const f of frames) for (const nid of f.story) {
        const n = nodes.get(nid) || { id: nid, title: U.humanize(nid), kind: "event" };
        const step = stepTo.get(`${nid}@${f.week}`);
        const outcome = ((s && s.outcomes) || []).find((o) => o.node === nid && o.week === f.week);
        const kind = outcome ? (outcome.type === "collapse" ? "collapse" : "flourish") : n.kind === "outcome" ? (n.outcome === "collapse" ? "collapse" : "flourish") : n.kind === "crisis" ? "crisis" : "event";
        const from = step ? nodes.get(step.source) : null;
        items.push({ week: f.week, node: nid, kind, title: n.title, text: outcome ? outcome.label : step && step.label ? `${from ? from.title + " → " : ""}${step.label}` : n.label || "", reward: outcome ? outcome.reward : null });
        seen.add(`${nid}@${f.week}`);
      }
      for (const p of (s && s.path) || []) {
        if (seen.has(`${p.target}@${p.week}`)) continue;
        const a = nodes.get(p.source), b = nodes.get(p.target);
        items.push({ week: p.week, node: p.target, kind: b && b.kind === "crisis" ? "crisis" : "branch", title: b ? b.title : U.humanize(p.target), text: `${a ? a.title : U.humanize(p.source)} → ${p.label || "next"}` });
        seen.add(`${p.target}@${p.week}`);
      }
      for (const o of (s && s.outcomes) || []) {
        if (seen.has(`${o.node}@${o.week}`)) continue;
        const n = nodes.get(o.node);
        items.push({ week: o.week, node: o.node, kind: o.type === "collapse" ? "collapse" : "flourish", title: n ? n.title : U.humanize(o.node), text: o.label, reward: o.reward });
      }
      if (s && s.collapsed) items.push({ week: s.collapsed.week, kind: "collapse", title: "The society collapses", text: s.collapsed.label, final: true });
      items.sort((x, y) => (x.week || 0) - (y.week || 0) || Number(Boolean(x.final)) - Number(Boolean(y.final)));
      return items;
    }
    function buildTicks() {
      const items = questItems();
      U.fill(ticks, ...items.map((it) => {
        const i = frames.findIndex((f) => f.week === it.week);
        return i < 0 ? null : h("span", { class: `tick ${KIND[it.kind].cls}`, style: { left: `${frames.length > 1 ? (i / (frames.length - 1)) * 100 : 0}%` } });
      }));
    }
    function buildStory() {
      const s = seedInfo();
      const nodes = run.story.nodes.filter((n) => U.isNum(n.x) && U.isNum(n.y));
      const NW = 158, NH = 34, PAD = 16;
      const graphBox = h("div", { class: "story-graph" });
      const svg = U.svgEl("svg", { class: "story-svg", role: "img", "aria-label": "Story graph: events, crises and outcomes, with the path this seed took" });
      if (nodes.length) {
        const minX = Math.min(...nodes.map((n) => n.x)) - PAD, minY = Math.min(...nodes.map((n) => n.y)) - PAD;
        const maxX = Math.max(...nodes.map((n) => n.x)) + NW + PAD, maxY = Math.max(...nodes.map((n) => n.y)) + NH + PAD;
        const W = maxX - minX, H = maxY - minY;
        svg.setAttribute("viewBox", `${minX} ${minY} ${W} ${H}`);
        svg.style.width = `${W}px`;
        svg.style.height = `${H}px`;
        svg.dataset.w = String(W);
        const defs = U.svgEl("defs", {}, svg);
        const mk = (idm, color) => { const m = U.svgEl("marker", { id: idm, viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse" }, defs); U.svgEl("path", { d: "M0 0 10 5 0 10z", fill: color }, m); };
        mk("arr-dim", "#4a4a4a"); mk("arr-on", "#e2c21c");
        const pos = new Map(nodes.map((n) => [n.id, n]));
        const edgeEls = new Map();
        for (const [a, b, label] of run.story.edges) {
          const na = pos.get(a), nb = pos.get(b);
          if (!na || !nb) continue;
          const x1 = na.x + NW, y1 = na.y + NH / 2, x2 = nb.x, y2 = nb.y + NH / 2;
          const back = x2 < x1;
          const d = back ? `M${na.x + NW / 2} ${na.y + NH} C${na.x + NW / 2} ${na.y + NH + 40} ${nb.x + NW / 2} ${nb.y - 40} ${nb.x + NW / 2} ${nb.y}`
            : `M${x1} ${y1} C${x1 + 50} ${y1} ${x2 - 50} ${y2} ${x2 - 2} ${y2}`;
          const g = U.svgEl("g", { class: "s-edge" }, svg);
          U.svgEl("path", { d, "marker-end": "url(#arr-dim)" }, g);
          if (label) { const t = U.svgEl("text", { x: back ? (na.x + nb.x) / 2 + NW / 2 + 6 : (x1 + x2) / 2, y: (y1 + y2) / 2 - 5, "text-anchor": "middle" }, g); t.textContent = label; }
          edgeEls.set(`${a}>${b}`, g);
        }
        const nodeEls = new Map();
        for (const n of nodes) {
          const kind = n.kind === "outcome" ? (n.outcome === "collapse" ? "collapse" : "flourish") : n.kind === "crisis" ? "crisis" : "event";
          const g = U.svgEl("g", { class: `s-node k-${kind}`, transform: `translate(${n.x} ${n.y})` }, svg);
          U.svgEl("rect", { width: NW, height: NH, rx: 9 }, g);
          const t = U.svgEl("text", { x: 10, y: NH / 2 + 4 }, g);
          t.textContent = n.title.length > 19 ? n.title.slice(0, 18) + "…" : n.title;
          const wk = U.svgEl("text", { x: NW - 8, y: NH / 2 + 4, "text-anchor": "end", class: "s-week" }, g);
          const tip = U.svgEl("title", {}, g);
          tip.textContent = `${n.title}${n.label ? ` — ${n.label}` : ""}`;
          nodeEls.set(n.id, { g, wk, n });
        }
        // when each node was reached and each edge taken on this seed
        const reached = new Map();
        frames.forEach((f) => f.story.forEach((nid) => { if (!reached.has(nid)) reached.set(nid, f.week); }));
        for (const p of (s && s.path) || []) { if (!reached.has(p.source)) reached.set(p.source, p.week); if (!reached.has(p.target)) reached.set(p.target, p.week); }
        const taken = ((s && s.path) || []).map((p) => ({ key: `${p.source}>${p.target}`, week: p.week }));
        graphBox.append(svg);
        storyView = { svg, nodeEls, edgeEls, reached, taken, graphBox, W, lastFocus: null };
      } else {
        storyView = null;
        graphBox.append(U.emptyNote("graph", "No story graph", "This recipe has no positioned story nodes."));
      }
      const items = questItems();
      const spoilers = h("input", { type: "checkbox" });
      spoilers.checked = prefs.spoilers;
      spoilers.addEventListener("change", () => { prefs.spoilers = spoilers.checked; updateStory(frames[index]); });
      const listEl = h("ol", { class: "quest-list" });
      const qitems = items.map((it) => {
        const k = KIND[it.kind];
        const rewards = it.reward && Object.keys(it.reward).length ? h("span", { class: "q-rewards" }, Object.entries(it.reward).map(([dm, v]) => h("span", { class: "q-reward", style: { "--c": U.DOMAIN_COLORS[dm] || "#e2c21c" } }, `+${U.pts(v)} ${dm}`))) : null;
        const btn = h("button", { type: "button", class: "q-btn" }, h("span", { class: "q-week" }, `W${it.week ?? "?"}`), h("span", { class: `q-icon ${k.cls}` }, icon(k.icon)),
          h("span", { class: "q-body" }, h("span", { class: "q-kind" }, k.label), h("b", null, it.title), it.text ? h("span", { class: "q-text" }, it.text) : null, rewards),
          h("span", { class: "q-lock", "aria-hidden": "true" }, icon("eye")));
        btn.addEventListener("click", () => seekWeek(it.week));
        const li = h("li", { class: `quest ${k.cls}` }, btn);
        listEl.append(li);
        return { it, li, btn };
      });
      if (!qitems.length) listEl.append(h("li", { class: "muted small" }, "No turning point recorded on this seed."));
      const fitBtn = h("button", { type: "button", class: "small", "aria-pressed": "false" }, icon("expand", "sm"), h("span", null, "Zoom in"));
      let fit = true;
      fitBtn.addEventListener("click", () => { fit = !fit; fitBtn.querySelector("span").textContent = fit ? "Zoom in" : "Fit"; fitBtn.setAttribute("aria-pressed", fit ? "false" : "true"); sizeGraph(); });
      const sizeGraph = () => {
        if (!storyView) return;
        const avail = graphBox.clientWidth || 600;
        const k = fit ? Math.max(0.72, Math.min(1, avail / storyView.W)) : 1.25;
        storyView.svg.style.zoom = "";
        storyView.svg.style.transform = "";
        const vb = storyView.svg.viewBox.baseVal;
        storyView.svg.style.width = `${vb.width * k}px`;
        storyView.svg.style.height = `${vb.height * k}px`;
      };
      if (storyView) {
        storyView.size = sizeGraph;
        if (typeof ResizeObserver === "function") {
          const ro = new ResizeObserver(() => { if (!graphBox.isConnected) { ro.disconnect(); return; } sizeGraph(); });
          ro.observe(graphBox);
        }
      }
      const legend = h("div", { class: "lc-legend story-legend" }, ["event", "crisis", "flourish", "collapse"].map((k) => h("span", null, h("i", { class: `k-${k}` }), KIND[k].label === "Outcome" ? "Flourishing outcome" : KIND[k].label)),
        h("span", null, h("i", { class: "k-path" }), "Path taken"));
      storyPanel = h("div", { class: "story" },
        h("div", { class: "insp-title" }, h("h2", null, "The story so far"), fitBtn),
        legend, graphBox,
        h("div", { class: "card-head tight" }, h("h3", null, icon("route", "sm"), "Turning points"), h("label", { class: "switch small" }, spoilers, h("span", null, "Show what comes next"))),
        listEl);
      storyQuest = qitems;
    }
    let storyPanel = null;
    let storyQuest = [];
    function updateStory(f) {
      const panel = tabPanels.get("story");
      if (storyPanel && !panel.contains(storyPanel)) { U.fill(panel, storyPanel); if (storyView && storyView.size) requestAnimationFrame(storyView.size); }
      if (storyView) {
        for (const { g, wk, n } of storyView.nodeEls.values()) {
          const w = storyView.reached.get(n.id);
          const on = U.isNum(w) && w <= f.week;
          g.classList.toggle("on", on);
          g.classList.toggle("now", on && w === f.week && f.week > 0);
          g.classList.toggle("future", prefs.spoilers && U.isNum(w) && w > f.week);
          wk.textContent = on ? `w${w}` : "";
        }
        for (const g of storyView.edgeEls.values()) { g.classList.remove("on"); g.firstChild.setAttribute("marker-end", "url(#arr-dim)"); }
        for (const t of storyView.taken) {
          const g = storyView.edgeEls.get(t.key);
          if (g && U.isNum(t.week) && t.week <= f.week) { g.classList.add("on"); g.firstChild.setAttribute("marker-end", "url(#arr-on)"); }
        }
        const nowNode = f.story.map((nid) => storyView.nodeEls.get(nid)).find(Boolean);
        if (nowNode && storyView.lastFocus !== nowNode.n.id && prefs.tab === "story") {
          storyView.lastFocus = nowNode.n.id;
          const box = storyView.graphBox;
          const k = (parseFloat(storyView.svg.style.width) || storyView.W) / storyView.W;
          const vb = storyView.svg.viewBox.baseVal;
          const top = (nowNode.n.y - vb.y) * k, left = (nowNode.n.x - vb.x) * k;
          if (top < box.scrollTop || top > box.scrollTop + box.clientHeight - 40 || left < box.scrollLeft || left > box.scrollLeft + box.clientWidth - 80) {
            box.scrollTo({ top: Math.max(0, top - box.clientHeight / 3), left: Math.max(0, left + (158 * k) / 2 - box.clientWidth / 2), behavior: U.reduced() ? "auto" : "smooth" });
          }
        }
      }
      for (const q of storyQuest) {
        const reached = (q.it.week || 0) <= f.week;
        q.li.classList.toggle("reached", reached);
        q.li.classList.toggle("now", q.it.week === f.week && f.week > 0);
        q.li.classList.toggle("locked", !reached && !prefs.spoilers);
        q.btn.setAttribute("aria-label", !reached && !prefs.spoilers ? `Week ${q.it.week}: a turning point not reached yet. Jump there.` : `Week ${q.it.week}: ${KIND[q.it.kind].label}, ${q.it.title}. ${q.it.text || ""}`);
      }
    }

    // ============================================================ quest log (every action of the seed)
    let questState = { status: "all", verb: "", q: "" };
    let questGroups = [];
    function buildQuestLog() {
      const acts = perSeed.actions;
      const ok = acts.filter((x) => x.a.ok).length;
      const said = acts.filter((x) => x.a.say).length;
      const people = new Set(acts.flatMap((x) => x.a.targets)).size;
      const search = h("input", { type: "search", placeholder: "Search actions, names, words…", "aria-label": "Search the quest log", value: questState.q });
      const verbSel = h("select", { "aria-label": "Action type" }, h("option", { value: "" }, "Every kind of action"), perSeed.verbs.map((v) => h("option", { value: v }, `${(V() && V().VERB_ICONS[v]) || "•"} ${v}`)));
      verbSel.value = questState.verb;
      const statusSeg = U.segmented("ql-status", [{ value: "all", label: "All", count: acts.length }, { value: "ok", label: "Applied", count: ok }, { value: "bad", label: "Rejected", count: acts.length - ok }],
        questState.status, (v) => { questState.status = v; drawQuest(); }, "Status");
      const listBox = h("div", { class: "ql-list" });
      search.addEventListener("input", () => { questState.q = search.value; drawQuest(); });
      verbSel.addEventListener("change", () => { questState.verb = verbSel.value; drawQuest(); });
      U.fill(questCard, 
        h("div", { class: "card-head" }, h("h2", { id: "ql-title" }, icon("scroll"), "Quest log"),
          h("span", { class: "chips" }, U.badge(`${acts.length} actions`, "", "bolt"), U.badge(`${ok} applied`, "good", "check"),
            acts.length - ok ? U.badge(`${acts.length - ok} rejected`, "bad", "x") : null, said ? U.badge(`${said} spoken lines`, "gold", "chat") : null, U.badge(`${people} people addressed`, "info", "users"))),
        h("p", { class: "muted small" }, "Every action the steward took on this seed, week by week: what it asked for, to whom, what it said, and what happened. Click a week to jump there."),
        h("div", { class: "ql-filters" }, search, verbSel, statusSeg),
        listBox);
      questCard._list = listBox;
      drawQuest();
    }
    function drawQuest() {
      const listBox = questCard._list;
      const q = questState.q.trim().toLowerCase();
      const match = (x) => {
        if (questState.status === "ok" && !x.a.ok) return false;
        if (questState.status === "bad" && x.a.ok) return false;
        if (questState.verb && x.a.verb !== questState.verb) return false;
        if (!q) return true;
        const hay = `${x.a.label} ${x.a.text} ${x.a.say || ""} ${x.a.targets.map(nameOf).join(" ")} ${x.a.place || ""} ${x.a.verb}`.toLowerCase();
        return hay.includes(q);
      };
      const byWeek = new Map();
      for (const x of perSeed.actions) if (match(x)) { if (!byWeek.has(x.i)) byWeek.set(x.i, []); byWeek.get(x.i).push(x); }
      questGroups = [];
      const frag = [];
      for (const [i, list] of byWeek) {
        const f = frames[i];
        const bad = list.filter((x) => !x.a.ok).length;
        const head = h("button", { type: "button", class: "ql-week" }, h("b", null, `Week ${f.week}`), h("span", null, f.season || ""),
          h("span", { class: "ql-count" }, `${list.length} action${list.length === 1 ? "" : "s"}${bad ? ` · ${bad} rejected` : ""}`), f.collapsed ? U.ruinsBadge("collapse") : null, icon("chevR", "sm"));
        head.addEventListener("click", () => seekTo(i));
        const group = h("div", { class: "ql-group" + (f.collapsed ? " collapse" : ""), dataset: { i: String(i) } }, head, h("div", { class: "acts", role: "list" }, list.map((x) => actionView(x.a, x.k, false))));
        questGroups.push({ i, group });
        frag.push(group);
      }
      U.fill(listBox, ...(frag.length ? frag : [U.emptyNote("search", "Nothing matches", "Try another word or filter.")]));
      if (frames[index]) updateQuestLog(frames[index]);
    }
    function updateQuestLog() {
      for (const g of questGroups) {
        g.group.classList.toggle("now", g.i === index);
        g.group.classList.toggle("future", g.i > index);
      }
    }

    // ============================================================ vitals
    function buildVitals() {
      const cap = run.recipe.commons.capacity || 1000;
      const crit = run.recipe.commons.critical;
      const get = (fn) => frames.map((f) => { const v = fn(f); return U.isNum(v) ? v : null; });
      const marks = [];
      const s = seedInfo();
      if (s && s.collapsed) marks.push({ i: frames.length - 1, color: "var(--collapse)", label: s.collapsed.label });
      const weekOf = (i) => (frames[i] ? frames[i].week : i);
      const opts = (o) => Object.assign({ markers: marks, weekOf, onSeek: seekTo, height: 52, aria: `${o.label} over the weeks` }, o);
      const defs = [
        [[{ values: get((f) => f.metrics.population), color: "#4aa3df" }], opts({ label: "Population", icon: "users", format: (v) => U.pts(v) })],
        [[{ values: get((f) => f.metrics.wellbeing), color: "#7cc47f" }], opts({ label: "Wellbeing", icon: "smile", min: 0, max: 10, format: (v) => `${U.fixed(v, 1)}/10` })],
        [[{ values: get((f) => f.metrics.trust), color: "#d55181" }], opts({ label: "Trust in the steward", icon: "heart", min: 0, max: 1, format: (v) => U.fixed(v, 2) })],
        [[{ values: get((f) => f.metrics.protest), color: "#ef6a5b" }], opts({ label: "Protest", icon: "megaphone", min: 0, minSpan: 0.2, format: (v) => U.pct(v) })],
        [[{ values: get((f) => (U.isNum(f.metrics.commons) ? f.metrics.commons : U.isNum(f.commons.stock) ? f.commons.stock / cap : null)), color: "#3987e5" }],
          opts({ label: `Commons · ${run.recipe.commons.name}`, icon: "lake", min: 0, max: 1, format: (v) => U.pct(v), refLines: U.isNum(crit) ? [{ value: crit, color: "#ef5350" }] : [] })],
        [[{ values: get((f) => f.metrics.gini), color: "#ff9f43" }], opts({ label: "Wealth Gini", icon: "scale", min: 0, max: 1, format: (v) => U.fixed(v, 2) })],
        [[{ values: get((f) => f.stores.granary), color: "#e0b030", label: "granary" }, { values: get((f) => f.stores.treasury), color: "#b388ff", label: "treasury" }],
          opts({ label: "Granary · treasury", icon: "granary", min: 0, format: (v) => U.pts(v) })],
        [[{ values: get((f) => totalOf(f)), color: "#e2c21c" }], opts({ label: "Points", icon: "star", min: 0, format: (v) => U.pts(v) })],
        [U.DOMAINS.map((d) => ({ values: get((f) => f.points[d]), color: U.DOMAIN_COLORS[d], label: U.domainName(d, header), width: 1.3 })),
          opts({ label: "Points per domain", icon: "columns", min: 0, format: (v) => U.pts(v), cls: "wide" })],
      ];
      if (run.hasDecisions) {
        const tokenSeries = [{ values: get((f) => f.decision && f.decision.inputTokens), color: "#8cc8f0", label: "input" }, { values: get((f) => f.decision && f.decision.generatedTokens), color: "#e2c21c", label: "generated" },
          { values: get((f) => f.decision && f.decision.reasoningTokens), color: "#b388ff", label: "reasoning" }].filter((x) => x.values.some(U.isNum));
        if (tokenSeries.length) defs.push([tokenSeries, opts({ label: "Tokens per week", icon: "chip", min: 0, format: (v) => U.pts(v) })]);
        defs.push([[{ values: get((f) => f.decision && f.decision.latency), color: "#8fe3d8" }], opts({ label: "Answer latency", icon: "clock", min: 0, format: (v) => `${U.fixed(v, 2)} s` })]);
      }
      charts = defs.map(([series, o]) => U.lineChart(series, o));
      U.fill(vitalsCard, h("div", { class: "card-head" }, h("h2", { id: "vt-title" }, icon("pulse"), "Vital signs"), h("span", { class: "muted small" }, "Hover to read a week, click to jump")),
        h("div", { class: "vitals-grid" }, charts));
    }

    // ============================================================ end screen & results
    function showEndscreen(s) {
      const collapsed = Boolean(s.collapsed);
      const total = h("strong", { class: "es-total" }, "0");
      endscreen.className = "endscreen" + (collapsed ? " lost" : " won");
      U.fill(endscreen, 
        h("button", { type: "button", class: "es-close icon-btn", "aria-label": "Close" }, icon("x")),
        h("p", { class: "es-kicker" }, collapsed ? `Week ${s.collapsed.week}` : `${run.recipe.weeks || frames[frames.length - 1].week} weeks survived`),
        h("h2", { class: "es-title" }, collapsed ? "Collapse" : "Year complete"),
        collapsed ? h("p", { class: "es-cause" }, s.collapsed.label) : null,
        h("div", { class: "es-score" }, total, h("span", null, collapsed ? "points retained at collapse" : `points${run.seeds.length > 1 ? ` · seed ${s.seed}` : ""}`)),
        h("div", { class: "es-actions" },
          U.actionButton("refresh", "Watch again", "small", () => { endscreen.hidden = true; village.seek(0); village.play(); }),
          U.actionButton("board", "Final scoreboard", "primary small", () => { endscreen.hidden = true; results.scrollIntoView({ behavior: U.reduced() ? "auto" : "smooth", block: "start" }); })));
      endscreen.querySelector(".es-close").addEventListener("click", () => { endscreen.hidden = true; });
      endscreen.hidden = false;
      U.countTo(total, s.total, 1200);
    }
    function buildResults() {
      const s = seedInfo();
      if (!s) { results.replaceChildren(); return; }
      const last = frames[frames.length - 1] || { points: {}, metrics: {} };
      const collapsed = Boolean(s.collapsed);
      const expertDomains = expert && expert.id !== entry.id ? expert.domains : null;
      const seedDomains = {};
      for (const d of U.DOMAINS) seedDomains[d] = last.points[d] || 0;
      const scales = U.domainScales([seedDomains, expertDomains]);
      const headline = h("div", { class: "sb-headline" },
        h("div", { class: "sb-main" + (collapsed ? " lost" : "") }, h("span", { class: "label" }, run.seeds.length > 1 ? `Seed ${s.seed}` : "Final score"),
          h("strong", { class: "sb-total" + (collapsed ? " collapse" : "") }, U.pts(s.total)),
          collapsed ? h("p", { class: "sb-cause" }, U.ruinsBadge(`week ${s.collapsed.week}`), h("span", null, `${s.collapsed.label}${s.collapsed.cause ? ` (${s.collapsed.cause})` : ""}`))
            : h("p", { class: "sb-cause good" }, icon("shield", "sm"), h("span", null, `Survived all ${U.isNum(s.survivedWeeks) ? s.survivedWeeks : run.recipe.weeks} weeks`)),
          collapsed ? h("p", { class: "sb-retained" }, "Points accumulated up to the collapse remain the final score.") : null),
        h("div", { class: "sb-run" },
          h("span", { class: "label" }, run.seeds.length > 1 ? `Run score · mean of ${run.seeds.length} seeds` : "Run score"),
          h("b", null, U.pts(entry.total)),
          h("span", null, entry.fullyCollapsed ? "every seed collapsed" : `${U.times(entry.relative)} the expert reference`),
          U.isNum(s.wellby) ? h("span", { class: "muted small" }, `${U.fixed(s.wellby, 1)} WELLBY on this seed`) : null,
          run.seeds.length > 1 ? h("div", { class: "sb-seeds" }, run.seeds.map((x) => h("button", { type: "button", class: "sb-seed" + (x.seed === seed ? " on" : "") + (x.collapsed ? " dead" : ""), onclick: () => switchSeed(x.seed, true) },
            h("span", null, `Seed ${x.seed}`), h("b", null, U.pts(x.total)), h("small", null, x.collapsed ? `✕ week ${x.collapsed.week}` : `${x.survivedWeeks ?? "?"} weeks`)))) : null));
      const diag = s.diagnosis.length ? h("div", { class: "diag-grid" }, s.diagnosis.map((f) => h("div", { class: `diag ${f.level}` },
        h("h4", null, h("span", { class: "lvl" }, f.level === "good" ? "✓" : f.level === "bad" ? "✕" : "!"), f.title), h("p", null, f.text), f.basis ? h("p", { class: "basis" }, f.basis) : null))) : null;
      const outcomes = s.outcomes.length ? h("ul", { class: "sb-outcomes" }, s.outcomes.map((o) => h("li", { class: o.type === "collapse" ? "bad" : "good" },
        h("span", { class: "q-week" }, `W${o.week}`), h("b", null, o.label), h("span", { class: "q-rewards" }, Object.entries(o.reward).map(([dm, v]) => h("span", { class: "q-reward", style: { "--c": U.DOMAIN_COLORS[dm] || "#e2c21c" } }, `+${U.pts(v)} ${dm}`)))))) : h("p", { class: "muted small" }, "No flourishing outcome was reached on this seed.");
      const events = h("div", { class: "ev-chips" }, Object.entries(s.events).map(([k, v]) => {
        const [label, ic] = EVENT_LABELS[k] || [U.humanize(k), "info"];
        return h("span", { class: "ev-chip" }, icon(ic, "sm"), h("b", null, k === "harvest" ? U.pts(v) : String(v)), label);
      }));
      const verbs = Object.entries(s.actions).sort((x, y) => y[1] - x[1]);
      const usage = run.usage || {};
      U.fill(results, 
        h("div", { class: "card-head" }, h("h2", { id: "results-title" }, icon("board"), "Final scoreboard")),
        headline,
        h("div", { class: "sb-grid" },
          h("div", null, h("h3", null, icon("columns"), "Points per domain", expertDomains ? h("span", { class: "h3-note" }, "white tick: expert reference") : null), U.skillBars(seedDomains, expertDomains, scales, header)),
          h("div", null, h("h3", null, icon("book"), "Narrative"), h("p", { class: "sb-narrative" }, s.narrative || "—"),
            h("h3", { class: "mt" }, icon("sparkle"), "Outcomes reached"), outcomes)),
        h("h3", { class: "mt" }, icon("pulse"), "What happened"), events,
        verbs.length ? h("h3", { class: "mt" }, icon("bolt"), "What the steward did") : null,
        verbs.length ? h("div", { class: "ev-chips" }, verbs.map(([verb, n]) => h("span", { class: "ev-chip" }, h("span", { "aria-hidden": "true" }, (V() && V().VERB_ICONS[verb]) || "❔"), h("b", null, String(n)), verb))) : null,
        diag ? h("h3", { class: "mt" }, icon("report"), "Diagnosis") : null, diag,
        h("p", { class: "sb-usage" }, [`Run ${run.id}`, run.status, usage.calls ? `${U.pts(usage.calls)} model calls` : null, usage.input_tokens ? `${U.compact(usage.input_tokens)} input tokens` : null,
          usage.generated_tokens ? `${U.compact(usage.generated_tokens)} generated tokens` : null, run.duration ? U.durationText(run.duration) : null].filter(Boolean).join(" · ")));
    }

    // ============================================================ controls & keyboard
    btnPlay.addEventListener("click", () => { if (village) village.toggle(); });
    btnPrev.addEventListener("click", () => seekTo(index - 1));
    btnNext.addEventListener("click", () => seekTo(index + 1));
    scrub.addEventListener("input", () => seekTo(Number(scrub.value)));
    speedSel.addEventListener("change", () => { if (village) village.setSpeed(Number(speedSel.value) || 1); });
    btnFull.addEventListener("click", () => { if (village) village.setExpanded(!village.isExpanded()); });
    jumpCollapse.addEventListener("click", () => { seekTo(frames.length - 1); stage.scrollIntoView({ behavior: U.reduced() ? "auto" : "smooth", block: "center" }); });
    function bumpSpeed(dir) {
      const i = Math.max(0, Math.min(SPEEDS.length - 1, SPEEDS.indexOf(Number(speedSel.value)) + dir));
      speedSel.value = String(SPEEDS[i]);
      if (village) village.setSpeed(SPEEDS[i]);
      U.toast(`Speed ${SPEEDS[i]}×`, "info", { ms: 1200 });
    }
    function onKey(event) {
      if (!village || event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) return;
      const t = event.target;
      const tag = t && t.tagName;
      const typing = tag === "TEXTAREA" || tag === "SELECT" || (tag === "INPUT" && !["range", "checkbox", "radio"].includes(t.type)) || (t && t.isContentEditable);
      if (typing || (t && t.closest && (t.closest("dialog[open]") || t.closest('[role="tablist"]')))) return;
      const onRange = tag === "INPUT" && t.type === "range";
      const k = event.key;
      if (k === " " || k === "k" || k === "K") {
        if ((tag === "BUTTON" || tag === "A") && k === " ") return;
        event.preventDefault(); village.toggle();
      } else if ((k === "ArrowLeft" || k === "j" || k === "J") && !onRange) { event.preventDefault(); seekTo(index - 1); }
      else if ((k === "ArrowRight" || k === "l" || k === "L") && !onRange) { event.preventDefault(); seekTo(index + 1); }
      else if (k === "Home" && !onRange) { event.preventDefault(); seekTo(0); }
      else if (k === "End" && !onRange) { event.preventDefault(); seekTo(frames.length - 1); }
      else if (k === "]" || k === "+" || k === "=") { event.preventDefault(); bumpSpeed(1); }
      else if (k === "[" || k === "-") { event.preventDefault(); bumpSpeed(-1); }
      else if (k === "f" || k === "F") { event.preventDefault(); village.setExpanded(!village.isExpanded()); }
      else if (/^[1-4]$/.test(k)) { event.preventDefault(); selectTab(TABS[Number(k) - 1].id, true); }
    }
    document.addEventListener("keydown", onKey);

    function destroy() {
      destroyed = true;
      document.removeEventListener("keydown", onKey);
      for (const off of offs) { try { off(); } catch (e) { /* listener already gone */ } }
      if (village) { try { village.destroy(); } catch (e) { console.error(e); } }
      village = null;
    }
    return { el, title: `${entry.model} · ${recipeTitle}`, destroy };
  }

  window.ExplorerReplay = Object.freeze({ render });
})();
