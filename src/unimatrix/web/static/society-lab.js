"use strict";
/*
 * UNIMATRIx Society Lab — design a Polis society and preview it with reference stewards.
 *
 * The editing model is the authored ("raw") recipe JSON: every form mutates it in place,
 * marks the lab dirty and schedules a validation. The raw recipe is what is sent to
 * /validate, /simulate, /calibrate and saved with PUT. Recipe text is only ever written
 * into the DOM through textContent (see h()).
 */
(() => {
  // =====================================================================================
  // 1. Utilities
  // =====================================================================================
  const SVG_NS = "http://www.w3.org/2000/svg";
  const IDENT = /^[a-z0-9][a-z0-9_-]{0,63}$/;
  const reducedMotion = () => Boolean(window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches);
  const media = (query) => (window.matchMedia ? matchMedia(query) : { matches: false, addListener() {}, removeListener() {} });
  // MediaQueryList change events (older Safari only has addListener).
  const onMedia = (q, fn) => (q.addEventListener ? q.addEventListener("change", fn) : q.addListener(fn));
  const offMedia = (q, fn) => (q.removeEventListener ? q.removeEventListener("change", fn) : q.removeListener(fn));
  /** Side panels become sheets below this width (a drawer on tablets, a bottom sheet on phones). */
  const sheetQuery = media("(max-width: 1000px)");
  const phoneQuery = media("(max-width: 760px)");
  const COARSE = media("(pointer: coarse)").matches;
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  /** Create an element. Text children become text nodes: never parsed as HTML. */
  function h(tag, props, ...children) {
    const el = document.createElement(tag);
    applyProps(el, props);
    if (tag === "input" && el.type === "number" && !el.hasAttribute("inputmode")) numericKeyboard(el);
    appendChildren(el, children);
    return el;
  }
  /** Touch keyboards: digits for non-negative fields; fields that accept negatives keep the full number pad (it has the minus). */
  function numericKeyboard(input) {
    const min = input.getAttribute("min");
    if (min === null || min === "" || Number(min) < 0) return;
    input.setAttribute("inputmode", input.getAttribute("step") === "1" ? "numeric" : "decimal");
  }
  /** Create an SVG element. */
  function s(tag, attrs, ...children) {
    const el = document.createElementNS(SVG_NS, tag);
    if (attrs) for (const [key, value] of Object.entries(attrs)) {
      if (value == null || value === false) continue;
      if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2), value);
      else el.setAttribute(key, String(value));
    }
    appendChildren(el, children);
    return el;
  }
  function applyProps(el, props) {
    if (!props) return;
    for (const [key, value] of Object.entries(props)) {
      if (value == null || value === false) continue;
      if (key === "class") el.className = value;
      else if (key === "style" && typeof value === "object") Object.assign(el.style, value);
      else if (key === "dataset") Object.assign(el.dataset, value);
      else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2), value);
      else if (key === "value") el.value = value;
      else if (key === "checked" || key === "disabled" || key === "selected" || key === "open" || key === "hidden") el[key] = Boolean(value);
      else el.setAttribute(key, value === true ? "" : String(value));
    }
  }
  function appendChildren(el, children) {
    for (const child of children.flat(Infinity)) {
      if (child == null || child === false) continue;
      el.append(child instanceof Node ? child : String(child));
    }
  }
  /** Replace an element's children, skipping null/false/undefined (recipe text stays text). */
  function fill(el, ...items) { el.replaceChildren(...cleanChildren(items)); return el; }
  /** Append children, skipping null/false/undefined. */
  function put(el, ...items) { el.append(...cleanChildren(items)); return el; }
  function cleanChildren(items) {
    return items.flat(Infinity).filter((c) => c != null && c !== false).map((c) => (c instanceof Node ? c : String(c)));
  }
  function clone(value) { return value === undefined ? undefined : JSON.parse(JSON.stringify(value)); }
  function isObject(value) { return value !== null && typeof value === "object" && !Array.isArray(value); }
  function deepMerge(base, over, replace = new Set()) {
    if (!isObject(base) || !isObject(over)) return clone(over === undefined ? base : over);
    const out = clone(base);
    for (const [key, value] of Object.entries(over)) {
      out[key] = isObject(base[key]) && isObject(value) && !replace.has(key) ? deepMerge(base[key], value, replace) : clone(value);
    }
    return out;
  }
  function getIn(obj, path, fallback) {
    let cur = obj;
    for (const key of path) { if (cur == null) return fallback; cur = cur[key]; }
    return cur === undefined ? fallback : cur;
  }
  function setIn(obj, path, value) {
    let cur = obj;
    for (let i = 0; i < path.length - 1; i++) {
      const key = path[i];
      if (cur[key] == null || typeof cur[key] !== "object") cur[key] = typeof path[i + 1] === "number" ? [] : {};
      cur = cur[key];
    }
    const last = path[path.length - 1];
    if (value === undefined) { if (Array.isArray(cur)) cur.splice(last, 1); else delete cur[last]; } else cur[last] = value;
  }
  function pathString(path) {
    if (typeof path === "string") return path;
    if (!path) return "";
    return path.map((key, i) => (typeof key === "number" ? `[${key}]` : (i ? "." : "") + key)).join("");
  }
  function debounce(fn, ms) {
    let timer = 0;
    const wrapped = (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
    wrapped.cancel = () => clearTimeout(timer);
    return wrapped;
  }
  function hashString(text) {
    let hash = 2166136261;
    for (let i = 0; i < text.length; i++) { hash ^= text.charCodeAt(i); hash = Math.imul(hash, 16777619); }
    return (hash >>> 0) / 4294967296;
  }
  function clamp(v, lo, hi) { return v < lo ? lo : v > hi ? hi : v; }
  function titleCase(text) { return String(text).replace(/[_-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()); }
  function slug(text) { return String(text).toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60) || "x"; }
  function uniqueId(prefix, taken) {
    const set = taken instanceof Set ? taken : new Set(taken);
    for (let i = 1; i < 10000; i++) {
      const id = `${prefix}${String(i).padStart(2, "0")}`;
      if (!set.has(id)) return id;
    }
    return `${prefix}${Date.now()}`;
  }
  function initials(name) { return String(name || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0].toUpperCase()).join(""); }
  function truncate(text, n) { const t = String(text ?? ""); return t.length > n ? t.slice(0, n - 1) + "…" : t; }
  const fmt = {
    int: (v) => (Number.isFinite(v) ? Math.round(v).toLocaleString("en-US") : "–"),
    num: (v, d = 2) => (Number.isFinite(v) ? Number(v).toFixed(d) : "–"),
    pct: (v, d = 0) => (Number.isFinite(v) ? `${(v * 100).toFixed(d)}%` : "–"),
    compact: (v) => (!Number.isFinite(v) ? "–" : Math.abs(v) >= 10000 ? `${(v / 1000).toFixed(1)}k` : Math.round(v).toLocaleString("en-US")),
  };
  function hexToRgb(hex) {
    const m = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(String(hex || "").trim());
    if (!m) return [154, 154, 154];
    let v = m[1];
    if (v.length === 3) v = v.split("").map((c) => c + c).join("");
    return [parseInt(v.slice(0, 2), 16), parseInt(v.slice(2, 4), 16), parseInt(v.slice(4, 6), 16)];
  }
  function rgba(hex, alpha) { const [r, g, b] = hexToRgb(hex); return `rgba(${r},${g},${b},${alpha})`; }
  function safeColor(hex) { return /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(String(hex || "")) ? hex : "#9a9a9a"; }
  function healthColor(v) { return v >= 70 ? "#7cc47f" : v >= 45 ? "#e0b030" : "#ef6a5b"; }
  function storageGet(key) { try { return JSON.parse(localStorage.getItem(key) || "null"); } catch { return null; } }
  function storageSet(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* storage unavailable */ } }

  /** Observe visibility of an element (and of the document) to pause animations when hidden. */
  function watchVisibility(element, onChange) {
    let inView = true;
    const update = () => onChange(inView && !document.hidden);
    const io = typeof IntersectionObserver === "function"
      ? new IntersectionObserver((entries) => { inView = entries.some((e) => e.isIntersecting); update(); }) : null;
    if (io) io.observe(element);
    document.addEventListener("visibilitychange", update);
    return () => { if (io) io.disconnect(); document.removeEventListener("visibilitychange", update); };
  }

  // =====================================================================================
  // 2. API client
  // =====================================================================================
  class ApiError extends Error {
    constructor(message, status) { super(message); this.status = status; }
  }
  async function api(path, { method = "GET", body } = {}) {
    const init = { method, headers: {} };
    if (body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(body); }
    let response;
    try { response = await fetch(`/api/polis${path}`, init); } catch (error) { throw new ApiError(`Network error: ${error.message}`, 0); }
    let data = null;
    try { data = await response.json(); } catch { data = null; }
    if (!response.ok) {
      const detail = data && data.detail;
      const message = typeof detail === "string" ? detail
        : Array.isArray(detail) ? detail.map((d) => `${(d.loc || []).join(".")}: ${d.msg}`).join("; ")
          : `${response.status} ${response.statusText}`;
      throw new ApiError(message, response.status);
    }
    return data;
  }
  const API = {
    catalog: () => api("/catalog"),
    recipes: () => api("/recipes"),
    recipe: (id) => api(`/recipes/${encodeURIComponent(id)}`),
    validate: (recipe) => api("/recipes/validate", { method: "POST", body: { recipe } }),
    save: (recipe) => api(`/recipes/${encodeURIComponent(recipe.id)}`, { method: "PUT", body: { recipe } }),
    remove: (id) => api(`/recipes/${encodeURIComponent(id)}`, { method: "DELETE" }),
    simulate: (recipe, policy, seed) => api("/simulate", { method: "POST", body: { recipe, policy, seed, frames: true } }),
    calibrate: (recipe, extra) => api("/calibrate", { method: "POST", body: Object.assign({ recipe }, extra || {}) }),
    estimate: (id, seconds) => api(`/estimate?recipe=${encodeURIComponent(id)}&seconds=${encodeURIComponent(seconds)}`),
  };

  // =====================================================================================
  // 3. State, change bus and static vocabulary
  // =====================================================================================
  const state = {
    catalog: null,
    recipes: [],
    raw: null,
    meta: { bundled: false, saved: false, origin: null },
    clean: "",
    dirty: false,
    section: "overview",
    validation: { status: "idle", error: null, warnings: [] },
    calib: null,
    calibBusy: false,
    calibAuto: false,
    sim: null,
    simBusy: false,
    compare: null,
    rates: null,
    sel: { resident: 0, tie: null, archetype: null, fact: 0, node: null, edge: null },
    policy: "expert",
    seed: null,
  };
  const bus = {
    handlers: new Map(),
    on(topic, fn) {
      if (!this.handlers.has(topic)) this.handlers.set(topic, new Set());
      this.handlers.get(topic).add(fn);
      return () => this.handlers.get(topic).delete(fn);
    },
    emit(topic, value) { for (const fn of Array.from(this.handlers.get(topic) || [])) { try { fn(value); } catch (e) { console.error(e); } } },
  };
  /** Section-scoped subscriptions, dropped when the section re-renders. */
  let sectionSubs = [];
  function subscribe(topic, fn) { sectionSubs.push(bus.on(topic, fn)); }

  // Trait explanations (mirror of polis/catalog.py TRAITS; /catalog only exposes defaults).
  const TRAIT_HELP = {
    O: "Openness (Big Five): curiosity; speeds learning and convention adoption.",
    C: "Conscientiousness (Big Five): diligence; work willingness and rule compliance.",
    E: "Extraversion (Big Five): sociability; more interactions, gossip and need for contact.",
    A: "Agreeableness (Big Five): warmth; teaching willingness, conflict de-escalation.",
    N: "Neuroticism (Big Five): emotional reactivity; stress gain and rumour fear.",
    prosocial: "Social value orientation (Van Lange 1999): 0 competitive, 0.5 individualist, 1 prosocial.",
    honesty: "Probability of reporting own signals and needs truthfully.",
    credulity: "Weight given to rumours heard from neighbours (Vosoughi, Roy & Aral 2018).",
    threshold: "Protest threshold: share of neighbours already protesting needed to join (Granovetter 1978).",
    pride: "Stigma sensitivity: hides distress and refuses public aid until trust is high.",
    reciprocity: "Strong reciprocity: reports and punishes free riders (Fehr & Gächter 2002).",
    conformity: "Weight on neighbours' beliefs, votes and conventions (Asch 1956; Centola 2018).",
    greed: "Extraction beyond need when unmonitored (Hardin 1968; Ostrom 1990).",
    cond_coop: "Conditional cooperation: matches perceived extraction of others (Fischbacher, Gächter & Fehr 2001).",
    inequity: "Inequity aversion: grievance from earning less than others (Fehr & Schmidt 1999).",
    resilience: "Stress buffering through coping resources (Hobfoll 1989, Conservation of Resources).",
    gatekeeper: "Hoards expertise: teaches slowly until honoured with encouragement (Henrich & Gil-White 2001).",
  };
  const TRAIT_NAMES = { O: "Openness", C: "Conscientiousness", E: "Extraversion", A: "Agreeableness", N: "Neuroticism",
    prosocial: "Prosociality", honesty: "Honesty", credulity: "Credulity", threshold: "Protest threshold", pride: "Pride",
    reciprocity: "Reciprocity", conformity: "Conformity", greed: "Greed", cond_coop: "Conditional coop.", inequity: "Inequity aversion",
    resilience: "Resilience", gatekeeper: "Gatekeeping" };
  // Project benefit keys (mirror of polis/catalog.py BENEFITS).
  const BENEFIT_HELP = {
    lake_visible: "lake stock visible weekly", drought_shield: "share of drought farm losses avoided",
    water_rumour_shield: "share of water-rumour panic avoided", spoilage: "weekly granary spoilage rate",
    cure: "extra weekly cure chance", regen: "extra weekly health regeneration", fish_growth: "lake regeneration multiplier",
    sale_price: "sale price multiplier", convention_speed: "convention adoption multiplier", learning: "apprenticeship speed multiplier",
    farm: "farm output multiplier",
  };
  const VERBS = ["talk", "announce", "give", "trade", "inspect", "assign", "build", "propose", "decree", "mediate", "introduce",
    "promise", "gather", "convention"];
  const MODIFIER_KEYS = ["farm", "fish", "fish_growth", "illness", "productivity", "price:food", "price:medicine", "price:tools"];
  const STEWARDS = {
    passive: "Does nothing. A well-designed society should collapse under it.",
    random: "Spends its actions on random valid-looking moves.",
    naive: "Answers explicit requests and reacts to loud alarms, like a weak model.",
    expert: "Reads only the briefing and follows sound heuristics: triage, consultation, planning.",
    oracle: "The expert with access to the hidden state: optimistic reference, never a cap.",
  };
  const KIND_COLOR = { event: "#4aa3df", crisis: "#ff9f43", branch: "#b388ff", collapse: "#ef5350", flourish: "#66bb6a", outcome: "#9e9e9e" };
  const STYLE_COLOR = { fair: "#7cc47f", shark: "#ef6a5b", guild: "#4aa3df" };
  const SEASON_COLOR = { spring: "#7cc47f", summer: "#e0b030", autumn: "#e08a3c", winter: "#8fb6e0" };
  const COLLAPSE_SOCIOLOGY = {
    population: "Exit (Hirschman 1970): when people die or leave faster than the community can absorb, it stops functioning as a society.",
    commons: "Depensation (Allee effect): below a critical stock the fish cannot breed. Hardin's tragedy (1968) — unless governed as Ostrom (1990) describes.",
    revolt: "Threshold cascades (Granovetter 1978): a few agitators tip neighbours whose thresholds are met by those already protesting.",
    legitimacy: "Legitimacy (Weber; Tyler 2006): authority survives only while it is seen as rightful. Sustained doubt leads the council to remove the steward.",
    famine: "Entitlement failure (Sen 1981): famines follow from people losing access to food, not only from scarcity.",
    fragmentation: "Anomie (Durkheim 1893) and the strength of weak ties (Granovetter 1973): a society split into isolated groups loses collective capacity.",
  };
  const SCORING_HELP = {
    forecast_bit: "Points per bit of information over a coin toss in the steward's forecasts (D1).",
    promise_kept: "Each promise kept (D5).",
    promise_broken: "Each promise broken (D5, usually negative).",
    conflict_resolved: "Each dispute settled (D5).",
    violence: "Each dispute that turned violent (D5, usually negative).",
    concern_relieved: "Each hidden concern relieved by the steward (D5).",
    apprentice_level: "Each skill level an apprentice gains (D7).",
  };

  // =====================================================================================
  // 4. Notices and dialogs
  // =====================================================================================
  function toast(message, kind = "info", ms = 4200) {
    const host = $("#toasts");
    const el = h("div", { class: `toast ${kind}` }, h("i", { "aria-hidden": "true" }), h("div", null, message));
    put(host, el);
    const close = () => { el.classList.add("leaving"); setTimeout(() => el.remove(), 260); };
    el.addEventListener("click", close);
    setTimeout(close, ms);
    while (host.children.length > 4) host.firstElementChild.remove();
  }
  function confirmDialog({ title = "Are you sure?", text = "", ok = "Continue", danger = false } = {}) {
    const dialog = $("#dlg-confirm");
    $("#dlg-confirm-title").textContent = title;
    $("#dlg-confirm-text").textContent = text;
    const okButton = $("#dlg-confirm-ok");
    okButton.textContent = ok;
    okButton.className = danger ? "danger" : "primary";
    dialog.returnValue = "";
    dialog.showModal();
    return new Promise((resolve) => dialog.addEventListener("close", () => resolve(dialog.returnValue === "ok"), { once: true }));
  }
  function identDialog({ title, lead, id, name, ok = "Create", templates = null }) {
    const dialog = $("#dlg-ident");
    const form = $("#dlg-ident-form");
    $("#dlg-ident-title").textContent = title;
    $("#dlg-ident-lead").textContent = lead || "";
    $("#dlg-ident-ok").textContent = ok;
    const idInput = $("#dlg-ident-id");
    const nameInput = $("#dlg-ident-name");
    const error = $("#dlg-ident-error");
    const wrap = $("#dlg-ident-template-wrap");
    const select = $("#dlg-ident-template");
    idInput.value = id || "";
    nameInput.value = name || "";
    error.textContent = "";
    wrap.hidden = !templates;
    if (templates) fill(select, ...templates.map(([value, label]) => h("option", { value }, label)));
    dialog.showModal();
    idInput.focus();
    idInput.select();
    return new Promise((resolve) => {
      const taken = new Set(state.recipes.map((r) => r.id));
      const onSubmit = (event) => {
        event.preventDefault();
        const newId = idInput.value.trim();
        const newName = nameInput.value.trim();
        if (!IDENT.test(newId)) { error.textContent = "The id must be lowercase letters, digits, '-' or '_' (max 64)."; idInput.focus(); return; }
        if (taken.has(newId)) { error.textContent = `A society with id '${newId}' already exists.`; idInput.focus(); return; }
        if (!newName) { error.textContent = "A name is required."; nameInput.focus(); return; }
        finish({ id: newId, name: newName.slice(0, 80), template: templates ? select.value : null });
      };
      const onCancel = () => finish(null);
      const finish = (value) => {
        form.removeEventListener("submit", onSubmit);
        $("#dlg-ident-cancel").removeEventListener("click", onCancel);
        dialog.removeEventListener("cancel", onCancel);
        dialog.close();
        resolve(value);
      };
      form.addEventListener("submit", onSubmit);
      $("#dlg-ident-cancel").addEventListener("click", onCancel);
      dialog.addEventListener("cancel", onCancel);
    });
  }
  function promptDialog({ title, text = "", label = "Value", value = "", check }) {
    const dialog = $("#dlg-prompt");
    const form = $("#dlg-prompt-form");
    $("#dlg-prompt-title").textContent = title;
    $("#dlg-prompt-text").textContent = text;
    $("#dlg-prompt-label").textContent = label;
    const input = $("#dlg-prompt-input");
    const error = $("#dlg-prompt-error");
    input.value = value;
    error.textContent = "";
    dialog.showModal();
    input.focus();
    input.select();
    return new Promise((resolve) => {
      const onSubmit = (event) => {
        event.preventDefault();
        const result = input.value.trim();
        const problem = check ? check(result) : null;
        if (problem) { error.textContent = problem; input.focus(); return; }
        finish(result);
      };
      const onCancel = () => finish(null);
      const finish = (result) => {
        form.removeEventListener("submit", onSubmit);
        $("#dlg-prompt-cancel").removeEventListener("click", onCancel);
        dialog.removeEventListener("cancel", onCancel);
        dialog.close();
        resolve(result);
      };
      form.addEventListener("submit", onSubmit);
      $("#dlg-prompt-cancel").addEventListener("click", onCancel);
      dialog.addEventListener("cancel", onCancel);
    });
  }
  function identCheck(taken) {
    return (value) => (!IDENT.test(value) ? "Use lowercase letters, digits, '-' or '_' (max 64)."
      : taken.has(value) ? `'${value}' is already used.` : null);
  }

  // ----- sheets: on small screens the side panels slide over the page
  const openSheets = new Set();
  let sheetBackdrop = null;
  function sheetChrome() {
    if (!sheetBackdrop) {
      sheetBackdrop = h("div", { class: "sheet-backdrop", "aria-hidden": "true" });
      sheetBackdrop.addEventListener("click", () => { for (const sheet of Array.from(openSheets)) if (sheet.modal) sheet.close(); });
      put(document.body, sheetBackdrop);
    }
    const modal = Array.from(openSheets).some((sheet) => sheet.modal && sheet.active());
    sheetBackdrop.classList.toggle("is-on", modal);
    document.documentElement.classList.toggle("sheet-lock", modal);
  }
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || event.defaultPrevented || !openSheets.size || document.querySelector("dialog[open]")) return;
    const last = Array.from(openSheets).pop();
    if (last && last.active()) { event.preventDefault(); last.close(); }
  });
  /**
   * Turn a side panel into a sheet on small screens: a right-hand drawer on tablets, a bottom sheet on
   * phones (`phone: true` keeps it inline on tablets). Outside sheet mode open() and close() change nothing
   * visible, so callers can open the sheet on every user selection. Drag the bar (or tap ✕) to dismiss.
   */
  function makeSheet(panel, { title = "", phone = false, modal = true, half = false, onToggle = null } = {}) {
    const query = phone ? phoneQuery : sheetQuery;
    const titleEl = h("span", { class: "sheet-title" }, title);
    const closeButton = h("button", { type: "button", class: "sheet-close ghost", "aria-label": "Close panel", title: "Close" }, "✕");
    const bar = h("div", { class: "sheet-bar" }, h("span", { class: "sheet-grabber", "aria-hidden": "true" }), titleEl, closeButton);
    // Closed sheets are taken out of the layout (`is-gone`) once they have slid away, so that their
    // off-screen box never widens or lengthens the page.
    panel.classList.add("sheet", "is-gone");
    if (phone) panel.classList.add("sheet-phone");
    if (half) panel.classList.add("sheet-half");
    panel.prepend(bar);
    let returnFocus = null;
    let goneTimer = 0;
    const sheet = {
      modal,
      panel,
      active: () => query.matches,
      get isOpen() { return panel.classList.contains("is-open"); },
      open(text) {
        if (text != null) titleEl.textContent = text;
        if (!query.matches) return;
        clearTimeout(goneTimer);
        if (!sheet.isOpen) {
          returnFocus = document.activeElement;
          panel.classList.remove("is-gone");
          void panel.offsetWidth;
          panel.scrollTop = 0;
        }
        panel.classList.add("is-open");
        if (modal) { panel.setAttribute("role", "dialog"); panel.setAttribute("aria-modal", "true"); }
        openSheets.delete(sheet);
        openSheets.add(sheet);
        sheetChrome();
        if (modal) requestAnimationFrame(() => closeButton.focus({ preventScroll: true }));
        if (onToggle) onToggle(true);
      },
      close() {
        if (!sheet.isOpen) return;
        panel.classList.remove("is-open");
        panel.style.transform = "";
        clearTimeout(goneTimer);
        goneTimer = setTimeout(() => { if (!sheet.isOpen) panel.classList.add("is-gone"); }, 380);
        panel.removeAttribute("aria-modal");
        if (modal) panel.removeAttribute("role");
        openSheets.delete(sheet);
        sheetChrome();
        if (returnFocus && returnFocus.isConnected && query.matches && typeof returnFocus.focus === "function") returnFocus.focus({ preventScroll: true });
        returnFocus = null;
        if (onToggle) onToggle(false);
      },
      toggle(text) { if (sheet.isOpen) sheet.close(); else sheet.open(text); },
      setTitle(text) { titleEl.textContent = text; },
      destroy() { clearTimeout(goneTimer); openSheets.delete(sheet); offMedia(query, onMode); sheetChrome(); },
    };
    panel._sheet = sheet;
    const onMode = () => { if (!query.matches && sheet.isOpen) sheet.close(); sheetChrome(); };
    onMedia(query, onMode);
    closeButton.addEventListener("click", () => sheet.close());
    let drag = null;
    bar.addEventListener("pointerdown", (event) => {
      if (!query.matches || event.target.closest("button") || (event.pointerType === "mouse" && event.button !== 0)) return;
      drag = { id: event.pointerId, x: event.clientX, y: event.clientY, d: 0, t: performance.now(), axis: phoneQuery.matches ? "y" : "x" };
      bar.setPointerCapture(event.pointerId);
      panel.classList.add("is-dragging");
    });
    bar.addEventListener("pointermove", (event) => {
      if (!drag || event.pointerId !== drag.id) return;
      drag.d = Math.max(0, drag.axis === "y" ? event.clientY - drag.y : event.clientX - drag.x);
      panel.style.transform = drag.axis === "y" ? `translateY(${drag.d}px)` : `translateX(${drag.d}px)`;
    });
    const endDrag = (event) => {
      if (!drag || event.pointerId !== drag.id) return;
      const fast = drag.d / Math.max(1, performance.now() - drag.t) > 0.55;
      const far = drag.d > 110;
      panel.classList.remove("is-dragging");
      panel.style.transform = "";
      if (drag.d > 10 && (far || fast)) sheet.close();
      drag = null;
    };
    bar.addEventListener("pointerup", endDrag);
    bar.addEventListener("pointercancel", endDrag);
    return sheet;
  }

  // =====================================================================================
  // 5. Form controls bound to the raw recipe
  // =====================================================================================
  /** Mark the recipe as edited: dirty flag, validation, and notify listeners. */
  function touch(topic = "any") {
    state.dirty = true;
    refreshDirty();
    scheduleValidate();
    if (topic !== "any") bus.emit(topic);
    bus.emit("any", topic);
  }
  const refreshDirty = debounce(() => {
    state.dirty = JSON.stringify(state.raw) !== state.clean;
    renderBadges();
  }, 120);

  function field(label, control, hint, extra) {
    return h("label", { class: "field" }, h("span", { class: "label" }, h("span", null, label), extra || null), control,
      hint ? h("span", { class: "hint" }, hint) : null);
  }
  function group(label, control, hint, extra) {
    return h("div", { class: "field", role: "group", "aria-label": label },
      h("span", { class: "label" }, h("span", null, label), extra || null), control, hint ? h("span", { class: "hint" }, hint) : null);
  }
  const F = {
    text({ label, value, onChange, path, hint, max, placeholder, onCommit, mono }) {
      const input = h("input", { type: "text", value: value ?? "", maxlength: max, placeholder, "data-path": pathString(path),
        spellcheck: mono ? "false" : null, class: mono ? "mono" : null });
      if (onChange) input.addEventListener("input", () => onChange(input.value));
      if (onCommit) input.addEventListener("change", () => onCommit(input.value, input));
      return field(label, input, hint);
    },
    area({ label, value, onChange, path, hint, max, rows = 3, placeholder }) {
      const input = h("textarea", { rows, maxlength: max, placeholder, "data-path": pathString(path) });
      input.value = value ?? "";
      const counter = max ? h("span", { class: "tiny muted" }, `${input.value.length}/${max}`) : null;
      input.addEventListener("input", () => { if (counter) counter.textContent = `${input.value.length}/${max}`; onChange(input.value); });
      return field(label, input, hint, counter);
    },
    number({ label, value, onChange, path, hint, min, max, step = "any", placeholder, integer }) {
      const input = h("input", { type: "number", value: value ?? "", min, max, step: integer ? 1 : step, placeholder,
        "data-path": pathString(path) });
      input.addEventListener("input", () => {
        if (input.value === "") { onChange(undefined); return; }
        const v = Number(input.value);
        if (Number.isFinite(v)) onChange(integer ? Math.round(v) : v);
      });
      return field(label, input, hint);
    },
    range({ label, value, onChange, path, hint, min = 0, max = 1, step = 0.01, title, extra, integer, marker }) {
      const num = h("input", { type: "number", min, max, step, value: value ?? "", "aria-label": `${label} value`, "data-path": pathString(path) });
      const slider = h("input", { type: "range", min, max, step, value: value ?? min, "aria-label": label, title });
      const fill = () => slider.style.setProperty("--fill", `${((Number(slider.value) - min) / (max - min || 1)) * 100}%`);
      fill();
      slider.addEventListener("input", () => { num.value = slider.value; fill(); onChange(integer ? Math.round(Number(slider.value)) : Number(slider.value)); });
      num.addEventListener("input", () => {
        if (num.value === "") { onChange(undefined); return; }
        const v = Number(num.value);
        if (!Number.isFinite(v)) return;
        slider.value = String(v);
        fill();
        onChange(integer ? Math.round(v) : v);
      });
      const row = h("div", { class: "slider-row" }, slider, num);
      if (marker != null) row.title = `Reference value ${marker}`;
      const el = group(label, row, hint, extra);
      el.setValue = (v) => { slider.value = String(v); num.value = String(v); fill(); };
      return el;
    },
    select({ label, value, options, onChange, path, hint, title }) {
      const select = h("select", { "data-path": pathString(path), title, "aria-label": label || title });
      const values = [];
      const add = (parent, [v, text]) => { values.push(v); put(parent, h("option", { value: String(values.length - 1) }, text)); };
      let found = false;
      for (const option of options) {
        if (option && option.group) {
          const og = h("optgroup", { label: option.group });
          option.options.forEach((o) => add(og, o));
          put(select, og);
        } else add(select, option);
      }
      found = values.some((v) => v === value);
      if (!found && value !== undefined) add(select, [value, `${value === null ? "none" : value} (custom)`]);
      select.value = String(values.findIndex((v) => v === value));
      select.addEventListener("change", () => onChange(values[Number(select.value)]));
      return label ? field(label, select, hint) : select;
    },
    toggle({ label, checked, onChange, hint, path }) {
      const input = h("input", { type: "checkbox", checked, "data-path": pathString(path), role: "switch" });
      input.addEventListener("change", () => onChange(input.checked));
      const el = h("label", { class: "switch" }, input, h("span", null, label));
      return hint ? h("div", { class: "field" }, el, h("span", { class: "hint" }, hint)) : el;
    },
    segmented({ label, value, options, onChange, path, small }) {
      const box = h("div", { class: "segmented", role: "radiogroup", "aria-label": label, "data-path": pathString(path) });
      const buttons = options.map(([v, text, title]) => {
        const b = h("button", { type: "button", role: "radio", "aria-checked": String(v === value), title }, text);
        b.addEventListener("click", () => {
          buttons.forEach((x) => x.setAttribute("aria-checked", "false"));
          b.setAttribute("aria-checked", "true");
          onChange(v);
        });
        b.addEventListener("keydown", (event) => {
          const i = buttons.indexOf(b);
          if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
            event.preventDefault();
            const next = buttons[(i + (event.key === "ArrowRight" ? 1 : buttons.length - 1)) % buttons.length];
            next.focus();
            next.click();
          }
        });
        return b;
      });
      put(box, ...buttons);
      if (small) box.classList.add("small");
      return label ? group(label, box) : box;
    },
    pips({ label, value, onChange, max = 3, path }) {
      const box = h("div", { class: "pips", role: "radiogroup", "aria-label": label, "data-path": pathString(path) });
      const buttons = [];
      for (let v = 0; v <= max; v++) {
        const b = h("button", { type: "button", role: "radio", "aria-checked": String(v === value), title: `Level ${v}` }, String(v));
        b.addEventListener("click", () => { buttons.forEach((x) => x.setAttribute("aria-checked", "false")); b.setAttribute("aria-checked", "true"); onChange(v); });
        buttons.push(b);
      }
      put(box, ...buttons);
      return box;
    },
    tags({ label, values, onChange, max = 80, placeholder = "Add and press Enter", path, hint }) {
      const list = values;
      const chips = h("div", { class: "chips" });
      const input = h("input", { type: "text", maxlength: max, placeholder, "data-path": pathString(path) });
      const draw = () => {
        fill(chips, ...list.map((text, i) => h("span", { class: "chip" }, text,
          h("button", { type: "button", class: "x", "aria-label": `Remove ${text}`, onclick: () => { list.splice(i, 1); draw(); onChange(list); } }, "×"))));
      };
      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          const text = input.value.trim();
          if (text) { list.push(text); input.value = ""; draw(); onChange(list); }
        }
      });
      draw();
      return group(label, h("div", { class: "tag-input" }, chips, input), hint);
    },
    color({ label, value, onChange, path }) {
      const input = h("input", { type: "color", value: safeColor(value).length === 7 ? safeColor(value) : "#9a9a9a", "aria-label": label, "data-path": pathString(path) });
      input.addEventListener("input", () => onChange(input.value));
      return input;
    },
  };
  /** A number field bound to a path in the raw recipe, with a fallback default. */
  function bindNumber(label, path, fallback, opts = {}) {
    return F.number(Object.assign({ label, value: getIn(state.raw, path, fallback), path,
      onChange: (v) => { setIn(state.raw, path, v); touch(opts.topic); } }, opts));
  }
  function bindRange(label, path, fallback, opts = {}) {
    return F.range(Object.assign({ label, value: getIn(state.raw, path, fallback), path,
      onChange: (v) => { setIn(state.raw, path, v); touch(opts.topic); if (opts.after) opts.after(v); } }, opts));
  }
  function bindText(label, path, fallback, opts = {}) {
    return F.text(Object.assign({ label, value: getIn(state.raw, path, fallback), path,
      onChange: (v) => { setIn(state.raw, path, v); touch(opts.topic); } }, opts));
  }

  // =====================================================================================
  // 6. Effective data (raw recipe + catalog defaults)
  // =====================================================================================
  const cat = () => state.catalog;
  function ticks() { return Number.isInteger(state.raw.ticks) ? state.raw.ticks : 52; }
  function worldEff() {
    const world = deepMerge(cat().defaults.world, state.raw.world || {}, new Set(["conventions"]));
    if (state.raw.world && isObject(state.raw.world.conventions)) world.conventions = clone(state.raw.world.conventions);
    world.policies = Object.assign({}, cat().defaults.world.policies, getIn(state.raw, ["world", "policies"], {}));
    return world;
  }
  function seasonAt(week) {
    const world = worldEff();
    const start = Math.max(0, cat().seasons.indexOf(world.start_season));
    const length = world.season_length || 13;
    return cat().seasons[(start + Math.floor((week - 1) / length)) % 4];
  }
  function archetypesEff() {
    const lib = clone(cat().archetypes);
    for (const key of Object.keys(lib)) lib[key].builtin = true;
    for (const [key, spec] of Object.entries(state.raw.archetypes || {})) {
      if (!isObject(spec)) continue;
      const base = lib[key] || { label: titleCase(key), summary: "", basis: "", traits: {}, builtin: false };
      for (const f of ["label", "summary", "basis"]) if (typeof spec[f] === "string") base[f] = spec[f];
      base.traits = Object.assign({}, base.traits, isObject(spec.traits) ? spec.traits : {});
      base.modified = true;
      lib[key] = base;
    }
    return lib;
  }
  function traitDefaults() { const out = {}; for (const [k, v] of Object.entries(cat().traits)) out[k] = v.default; return out; }
  function archetypeTraits(key, lib = archetypesEff()) { return Object.assign(traitDefaults(), (lib[key] || {}).traits || {}); }
  function residentTraits(r, lib) { return Object.assign(archetypeTraits(r.archetype || "conditional_cooperator", lib), isObject(r.traits) ? r.traits : {}); }
  function roleSkill(role) { return (cat().roles[role] || {}).skill || null; }
  function residentSkills(r) {
    const skill = roleSkill(r.role || "farmer");
    const out = skill ? { [skill]: 2 } : {};
    Object.assign(out, isObject(r.skills) ? r.skills : {});
    return out;
  }
  function residentState(r) { return Object.assign({}, cat().defaults.resident_state, isObject(r.state) ? r.state : {}); }
  function residents() { if (!Array.isArray(state.raw.residents)) state.raw.residents = []; return state.raw.residents; }
  function ties() { if (!Array.isArray(state.raw.ties)) state.raw.ties = []; return state.raw.ties; }
  function factions() { if (!Array.isArray(state.raw.factions)) state.raw.factions = []; return state.raw.factions; }
  function merchants() { if (!Array.isArray(state.raw.merchants)) state.raw.merchants = []; return state.raw.merchants; }
  function facts() { if (!Array.isArray(state.raw.facts)) state.raw.facts = []; return state.raw.facts; }
  function storyNodes() {
    if (!isObject(state.raw.story)) state.raw.story = { nodes: [] };
    if (!Array.isArray(state.raw.story.nodes)) state.raw.story.nodes = [];
    return state.raw.story.nodes;
  }
  function factionColor(id) { const f = factions().find((x) => x.id === id); return safeColor(f ? f.color : null); }
  function factionName(id) { const f = factions().find((x) => x.id === id); return f ? f.name : id || "No faction"; }
  /** Newcomers defined inside story `arrive` effects. */
  function newcomers() {
    const out = [];
    storyNodes().forEach((node, nodeIndex) => (node.effects || []).forEach((effect, effectIndex) => {
      if (effect && effect.type === "arrive" && Array.isArray(effect.residents)) {
        effect.residents.forEach((spec, k) => out.push({ spec, node, nodeIndex, effectIndex, k }));
      }
    }));
    return out;
  }
  function people() {
    return residents().map((r) => ({ id: r.id, name: r.name, faction: r.faction, role: r.role, newcomer: false }))
      .concat(newcomers().map(({ spec, node }) => ({ id: spec.id, name: spec.name, faction: spec.faction, role: spec.role, newcomer: true, node: node.id })));
  }
  function personLabel(id) { const p = people().find((x) => x.id === id); return p ? `${p.name} (${id})` : id; }
  function peopleOptions() {
    const locals = residents().map((r) => [r.id, `${r.name || r.id} · ${r.id}`]);
    const arriving = newcomers().map(({ spec }) => [spec.id, `${spec.name || spec.id} · ${spec.id} (newcomer)`]);
    return arriving.length ? [{ group: "Residents", options: locals }, { group: "Newcomers", options: arriving }] : locals;
  }
  function projectsEff() {
    const raw = state.raw.projects;
    const out = {};
    const keys = new Set(Object.keys(cat().projects).concat(isObject(raw) ? Object.keys(raw) : []));
    for (const key of keys) {
      const base = clone(cat().projects[key] || { name: key, needs: {}, together: false, cost: 0, requires: [], value: 1, benefit: {}, description: "" });
      const enabled = !isObject(raw) || key in raw;
      const override = isObject(raw) && isObject(raw[key]) ? raw[key] : {};
      out[key] = Object.assign(base, clone(override), { enabled, custom: !cat().projects[key] });
    }
    return out;
  }
  function conventionDomains() {
    const set = new Set(Object.keys(worldEff().conventions || {}));
    storyNodes().forEach((n) => (n.effects || []).forEach((e) => { if (e && e.type === "convention_shift" && e.domain) set.add(e.domain); }));
    return Array.from(set);
  }
  function conflictIds() {
    const set = new Set();
    storyNodes().forEach((n) => (n.effects || []).forEach((e) => { if (e && e.type === "conflict" && e.id) set.add(e.id); }));
    return Array.from(set);
  }
  function knownShocks() {
    const set = new Set(["drought", "fever"]);
    merchants().forEach((m) => Object.keys(m.shock_prices || {}).forEach((k) => set.add(k)));
    facts().forEach((f) => (f.effects || []).forEach((e) => { if (e && e.shock) set.add(e.shock); }));
    storyNodes().forEach((n) => (n.effects || []).forEach((e) => { if (e && e.shock) set.add(e.shock); }));
    return Array.from(set);
  }
  function allIds() {
    return new Set(people().map((p) => p.id));
  }

  // =====================================================================================
  // 7. Recipe bar: open, new, duplicate, validate, save, delete, import, export
  // =====================================================================================
  async function loadRecipeList() {
    state.recipes = await API.recipes();
    renderRecipeSelect();
  }
  function renderRecipeSelect() {
    const select = $("#recipe-select");
    const groups = [];
    if (state.raw && !state.meta.saved) {
      groups.push(h("optgroup", { label: "Open in the lab" }, h("option", { value: "__current__" }, `${state.raw.name || state.raw.id} (not saved)`)));
    }
    const bundled = state.recipes.filter((r) => r.bundled);
    const own = state.recipes.filter((r) => !r.bundled);
    if (bundled.length) groups.push(h("optgroup", { label: "Bundled · read-only" }, bundled.map((r) => h("option", { value: r.id }, r.name))));
    if (own.length) groups.push(h("optgroup", { label: "Your societies" }, own.map((r) => h("option", { value: r.id }, r.name))));
    fill(select, ...groups);
    select.value = state.raw && state.meta.saved ? state.raw.id : "__current__";
  }
  async function openRecipe(id, { force = false } = {}) {
    if (!force && state.dirty) {
      const ok = await confirmDialog({ title: "Discard unsaved changes?", text: `Your edits to “${state.raw.name}” have not been saved.`, ok: "Discard changes", danger: true });
      if (!ok) { renderRecipeSelect(); return false; }
    }
    try {
      const data = await API.recipe(id);
      setRaw(data.raw, { bundled: data.bundled, saved: true });
      storageSet("society-lab:last", id);
      return true;
    } catch (error) {
      toast(`Could not open ${id}: ${error.message}`, "bad");
      renderRecipeSelect();
      return false;
    }
  }
  function setRaw(raw, meta, { clean = true } = {}) {
    if (!isObject(raw.story)) raw.story = { nodes: [] };
    if (!Array.isArray(raw.story.nodes)) raw.story.nodes = [];
    for (const key of ["ties", "factions", "merchants", "facts"]) if (raw[key] === undefined) raw[key] = [];
    state.raw = raw;
    state.meta = { bundled: Boolean(meta.bundled), saved: Boolean(meta.saved) };
    state.clean = clean ? JSON.stringify(raw) : "";
    state.dirty = !clean;
    state.validation = { status: "idle", error: null, warnings: [] };
    state.calib = null;
    state.calibAuto = false;
    state.calibError = null;
    state.sim = null;
    state.compare = null;
    state.rates = null;
    state.sel = { resident: 0, tie: null, archetype: null, fact: 0, node: null, edge: null };
    state.seed = Array.isArray(raw.seeds) && Number.isInteger(raw.seeds[0]) ? raw.seeds[0] : 1;
    hideBanner();
    renderRecipeSelect();
    renderBadges();
    renderNavCounts();
    renderSection(state.section);
    runValidate({ quiet: true });
  }
  function renderBadges() {
    const host = $("#recipe-badges");
    if (!state.raw) return;
    const items = [];
    if (state.meta.bundled) items.push(h("span", { class: "badge gold", title: "Bundled recipes are read-only. Use “Save as…” or Duplicate to keep your changes." }, "Bundled · read-only"));
    else if (!state.meta.saved) items.push(h("span", { class: "badge watch" }, "Not saved yet"));
    if (state.dirty) items.push(h("span", { class: "badge gold" }, h("i", { class: "dirty-dot", "aria-hidden": "true" }), "Unsaved changes"));
    const v = state.validation;
    if (v.status === "checking") items.push(h("span", { class: "badge" }, h("span", { class: "spinner", style: { width: "10px", height: "10px" }, "aria-hidden": "true" }), "Checking…"));
    else if (v.status === "ok") {
      items.push(v.warnings.length
        ? h("button", { type: "button", class: "badge watch", onclick: showWarnings, title: "Show authoring warnings" }, `Valid · ${v.warnings.length} warning${v.warnings.length > 1 ? "s" : ""}`)
        : h("span", { class: "badge good" }, "✓ Valid"));
    } else if (v.status === "error") items.push(h("button", { type: "button", class: "badge bad", onclick: () => jumpToError(v.error), title: v.error }, "✕ Invalid · show"));
    fill(host, ...items);
    if (badgeFades) badgeFades();
    $("#btn-delete").disabled = state.meta.bundled || !state.meta.saved;
    $("#btn-save").textContent = state.meta.bundled ? "Save as…" : "Save";
    document.title = `${state.dirty ? "● " : ""}${state.raw.name || state.raw.id} · Society Lab`;
  }
  function renderNavCounts() {
    if (!state.raw) return;
    const counts = {
      people: residents().length,
      archetypes: Object.keys(archetypesEff()).length,
      merchants: merchants().length,
      projects: Object.values(projectsEff()).filter((p) => p.enabled).length,
      facts: facts().length,
      story: storyNodes().length,
    };
    for (const em of $$("#nav-list em[data-count]")) em.textContent = counts[em.dataset.count] != null ? String(counts[em.dataset.count]) : "";
    const outcomes = storyNodes().filter((n) => n.outcome);
    $("#nav-summary").textContent = `${counts.people} residents · ${ticks()} weeks · ${outcomes.filter((n) => n.outcome.type === "collapse").length} collapse and ${outcomes.filter((n) => n.outcome.type === "flourish").length} flourishing outcomes`;
  }
  bus.on("any", debounce(renderNavCounts, 250));

  // ----- validation
  let validateSeq = 0;
  const scheduleValidate = debounce(() => runValidate({ quiet: true }), 900);
  async function runValidate({ jump = false, quiet = false } = {}) {
    if (!state.raw) return null;
    scheduleValidate.cancel();
    const seq = ++validateSeq;
    state.validation.status = "checking";
    renderBadges();
    let result = null;
    try {
      result = await API.validate(state.raw);
    } catch (error) {
      if (seq === validateSeq) { state.validation = { status: "idle", error: null, warnings: [] }; renderBadges(); }
      if (!quiet) toast(`Validation request failed: ${error.message}`, "bad");
      return null;
    }
    if (seq !== validateSeq) return result;
    if (result.ok) {
      state.validation = { status: "ok", error: null, warnings: result.warnings || [] };
      if ($("#validation-banner").dataset.kind === "error") hideBanner();
      if (!quiet) toast(result.warnings && result.warnings.length ? `Valid, with ${result.warnings.length} authoring warning(s).` : "The society is valid.", "good");
    } else {
      state.validation = { status: "error", error: result.error, warnings: [] };
      showErrorBanner(result.error);
      if (jump) jumpToError(result.error);
    }
    renderBadges();
    markNavError();
    return result;
  }
  function splitError(error) {
    const text = String(error || "");
    const i = text.indexOf(": ");
    return i > 0 && !/\s/.test(text.slice(0, i)) ? [text.slice(0, i), text.slice(i + 2)] : ["", text];
  }
  function showErrorBanner(error) {
    const banner = $("#validation-banner");
    const [path, message] = splitError(error);
    banner.dataset.kind = "error";
    banner.classList.remove("warn");
    banner.setAttribute("role", "alert");
    fill(banner, 
      h("strong", null, "Not valid"),
      h("span", { class: "grow" }, path ? h("code", null, path) : null, " ", message),
      path ? h("button", { type: "button", class: "small", onclick: () => jumpToError(error) }, "Go to field") : null,
      h("button", { type: "button", class: "small ghost", "aria-label": "Dismiss", onclick: hideBanner }, "✕"));
    banner.hidden = false;
  }
  function showWarnings() {
    const banner = $("#validation-banner");
    banner.dataset.kind = "warn";
    banner.classList.add("warn");
    banner.setAttribute("role", "status");
    fill(banner, 
      h("strong", null, "Authoring warnings"),
      h("ul", { class: "grow warn-list" }, state.validation.warnings.map((w) => h("li", null, w))),
      h("button", { type: "button", class: "small ghost", "aria-label": "Dismiss", onclick: hideBanner }, "✕"));
    banner.hidden = false;
  }
  function hideBanner() { const banner = $("#validation-banner"); banner.hidden = true; banner.dataset.kind = ""; }
  function markNavError() {
    const target = state.validation.status === "error" ? locate(splitError(state.validation.error)[0]).section : null;
    for (const a of $$("#nav-list a")) a.classList.toggle("has-error", a.dataset.section === target);
  }
  /** Which section (and selection) an error path such as `residents[3].traits.O` belongs to. */
  function locate(path) {
    let m;
    if ((m = /^residents\[(\d+)\]/.exec(path))) return { section: "people", sel: { resident: Number(m[1]), tie: null } };
    if ((m = /^ties\[(\d+)\]/.exec(path))) return { section: "people", sel: { tie: Number(m[1]) } };
    if (/^(factions|residents|ties)/.test(path)) return { section: "people" };
    if ((m = /^archetypes\.([a-z0-9_-]+)/.exec(path))) return { section: "archetypes", sel: { archetype: m[1] } };
    if (/^archetypes/.test(path)) return { section: "archetypes" };
    if (/^world\.(name|start_season|season_length)/.test(path)) return { section: "overview" };
    if (/^world/.test(path)) return { section: "world" };
    if (/^merchants/.test(path)) return { section: "merchants" };
    if (/^projects/.test(path)) return { section: "projects" };
    if ((m = /^facts\[(\d+)\]/.exec(path))) return { section: "facts", sel: { fact: Number(m[1]) } };
    if (/^facts/.test(path)) return { section: "facts" };
    if ((m = /^story\.nodes\[(\d+)\]/.exec(path))) {
      const node = storyNodes()[Number(m[1])];
      return { section: "story", sel: { node: node ? node.id : null, edge: null } };
    }
    if ((m = /^story\.([a-z0-9_-]+)\./.exec(path))) return { section: "story", sel: { node: m[1], edge: null } };
    if (/^story/.test(path)) return { section: "story" };
    if (/^(collapse|scoring)/.test(path)) return { section: "collapse" };
    return { section: "overview" };
  }
  function translatePath(path) {
    const m = /^story\.([a-z0-9_-]+)\.(edges.*)$/.exec(path);
    if (m && m[1] !== "nodes") {
      const index = storyNodes().findIndex((n) => n.id === m[1]);
      if (index >= 0) return `story.nodes[${index}].${m[2]}`;
    }
    return path;
  }
  function jumpToError(error) {
    const [path] = splitError(error);
    const target = locate(path);
    Object.assign(state.sel, target.sel || {});
    go(target.section, true);
    setTimeout(() => highlightPath(translatePath(path)), 60);
  }
  function highlightPath(path) {
    const section = $(`#sec-${state.section}`);
    if (!section || !path) return;
    const all = $$("[data-path]", section).filter((el) => el.dataset.path);
    let best = all.find((el) => el.dataset.path === path) || null;
    if (!best) {
      let bestLength = -1;
      for (const el of all) {
        const p = el.dataset.path;
        if (path.startsWith(p) && (path[p.length] === "." || path[p.length] === "[") && p.length > bestLength) { best = el; bestLength = p.length; }
      }
    }
    if (!best) best = all.find((el) => el.dataset.path.startsWith(path)) || null;
    if (!best) return;
    const sheetHost = best.closest(".sheet");
    if (sheetHost && sheetHost._sheet) sheetHost._sheet.open();
    for (let d = best.closest("details"); d; d = d.parentElement && d.parentElement.closest("details")) d.open = true;
    best.scrollIntoView({ block: "center", behavior: reducedMotion() ? "auto" : "smooth" });
    best.classList.add("invalid");
    if (typeof best.focus === "function") best.focus({ preventScroll: true });
    const clear = () => best.classList.remove("invalid");
    best.addEventListener("input", clear, { once: true });
    setTimeout(clear, 6000);
  }

  // ----- bar actions
  async function newSociety() {
    if (state.dirty && !(await confirmDialog({ title: "Discard unsaved changes?", text: "Creating a new society replaces the one open in the lab.", ok: "Discard and continue", danger: true }))) return;
    const templates = state.recipes.map((r) => [r.id, `${r.name}${r.bundled ? " (bundled)" : ""}`]).concat([["__blank__", "Blank village (10 residents, empty story)"]]);
    const taken = new Set(state.recipes.map((r) => r.id));
    const answer = await identDialog({ title: "New society", lead: "Start from an existing society as a template, or from a blank village.",
      id: uniqueId("my-society-", taken), name: "My society", ok: "Create", templates });
    if (!answer) return;
    let raw;
    if (answer.template === "__blank__") raw = blankRecipe(answer.id, answer.name);
    else {
      try { raw = (await API.recipe(answer.template)).raw; } catch (error) { toast(error.message, "bad"); return; }
      raw.tags = (raw.tags || []).filter((t) => t !== "standard");
    }
    raw.id = answer.id;
    raw.name = answer.name;
    raw.author = "";
    setRaw(raw, { bundled: false, saved: false }, { clean: false });
    toast(`Created “${answer.name}”. Save it to keep it.`, "good");
  }
  async function duplicateSociety({ thenSave = false } = {}) {
    const taken = new Set(state.recipes.map((r) => r.id));
    let id = `${state.raw.id}-copy`;
    if (taken.has(id)) id = uniqueId(`${state.raw.id}-copy-`, taken);
    const answer = await identDialog({ title: thenSave ? "Save as a new society" : "Duplicate society",
      lead: state.meta.bundled ? "Bundled societies are read-only. Your copy keeps all current edits and can be saved." : "The copy keeps all current edits.",
      id, name: `${state.raw.name} (copy)`.slice(0, 80), ok: thenSave ? "Save copy" : "Duplicate" });
    if (!answer) return false;
    const raw = clone(state.raw);
    raw.id = answer.id;
    raw.name = answer.name;
    const sim = state.sim;
    const calib = state.calib;
    setRaw(raw, { bundled: false, saved: false }, { clean: false });
    state.sim = sim;
    state.calib = calib;
    if (thenSave) return saveSociety();
    toast(`Duplicated as ${answer.id}. Save it to keep it.`, "good");
    return true;
  }
  async function saveSociety() {
    if (state.meta.bundled || state.recipes.some((r) => r.bundled && r.id === state.raw.id)) return duplicateSociety({ thenSave: true });
    const button = $("#btn-save");
    button.disabled = true;
    try {
      const result = await runValidate({ jump: true, quiet: true });
      if (!result) { toast("Could not validate before saving.", "bad"); return false; }
      if (!result.ok) { toast("Fix the validation error before saving.", "bad"); return false; }
      const existing = state.recipes.find((r) => r.id === state.raw.id);
      if (existing && !state.meta.saved) {
        const ok = await confirmDialog({ title: "Overwrite saved society?", text: `A society with id '${state.raw.id}' already exists. Saving replaces it.`, ok: "Overwrite", danger: true });
        if (!ok) return false;
      }
      await API.save(state.raw);
      state.meta.saved = true;
      state.meta.bundled = false;
      state.clean = JSON.stringify(state.raw);
      state.dirty = false;
      storageSet("society-lab:last", state.raw.id);
      await loadRecipeList();
      renderBadges();
      toast(`Saved “${state.raw.name}”.`, "good");
      return true;
    } catch (error) {
      toast(`Save failed: ${error.message}`, "bad");
      if (error.status === 422 && /:/.test(error.message)) showErrorBanner(error.message);
      return false;
    } finally {
      button.disabled = false;
    }
  }
  async function deleteSociety() {
    if (state.meta.bundled || !state.meta.saved) return;
    const ok = await confirmDialog({ title: `Delete “${state.raw.name}”?`, text: `The saved recipe '${state.raw.id}' will be removed from config/societies. Exported copies are not affected.`, ok: "Delete society", danger: true });
    if (!ok) return;
    try {
      await API.remove(state.raw.id);
      toast(`Deleted ${state.raw.id}.`, "good");
      await loadRecipeList();
      const first = state.recipes.find((r) => r.bundled) || state.recipes[0];
      state.dirty = false;
      if (first) await openRecipe(first.id, { force: true });
    } catch (error) {
      toast(`Delete failed: ${error.message}`, "bad");
    }
  }
  function exportSociety() {
    const blob = new Blob([JSON.stringify(state.raw, null, 2) + "\n"], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = h("a", { href: url, download: `${state.raw.id || "society"}.json` });
    put(document.body, a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  }
  async function importSociety(file) {
    if (!file) return;
    let raw;
    try {
      raw = JSON.parse(await file.text());
    } catch (error) {
      toast(`That file is not valid JSON: ${error.message}`, "bad");
      return;
    }
    if (!isObject(raw)) { toast("A society recipe must be a JSON object.", "bad"); return; }
    if (state.dirty && !(await confirmDialog({ title: "Discard unsaved changes?", text: "Importing replaces the society open in the lab.", ok: "Import", danger: true }))) return;
    const existing = state.recipes.find((r) => r.id === raw.id);
    setRaw(raw, { bundled: false, saved: false }, { clean: false });
    if (existing && existing.bundled) toast(`'${raw.id}' is a bundled id: saving will ask for a new one.`, "info", 6000);
    else if (existing) toast(`Imported. Saving will overwrite the existing '${raw.id}'.`, "info", 6000);
    else toast(`Imported “${raw.name || raw.id}”.`, "good");
  }
  function blankRecipe(id, name) {
    const cast = [["Ada Rossi", "farmer", "steady_worker"], ["Bruno Leto", "fisher", "free_rider"], ["Chiara Neri", "healer", "conditional_cooperator"],
      ["Dario Gallo", "builder", "steady_worker"], ["Elena Riva", "trader", "conditional_cooperator"], ["Fosco Bassi", "elder", "elder_keeper"],
      ["Giada Conti", "farmer", "conditional_cooperator"], ["Ivo Sala", "fisher", "conditional_cooperator"], ["Lia Greco", "farmer", "gossip"],
      ["Marco Ferri", "youth", "conditional_cooperator"]];
    const rid = (i) => `r${String(i + 1).padStart(2, "0")}`;
    const ring = cast.map((_, i) => [rid(i), rid((i + 1) % cast.length), 0.5]);
    return {
      format: cat().format, id, name, description: "", author: "", tags: [], ticks: 52, seeds: [1],
      steward: { title: "Steward", actions_per_week: 6, note_chars: 1500 },
      world: { name: "New village", start_season: "spring" },
      factions: [{ id: "village", name: "Villagers", color: "#b59a00" }],
      residents: cast.map(([n, role, archetype], i) => ({ id: rid(i), name: n, age: 28 + i * 5, role, faction: "village", archetype })),
      ties: ring.concat([["r01", "r07", 0.6], ["r02", "r08", 0.6], ["r03", "r06", 0.4], ["r05", "r09", 0.4], ["r04", "r10", 0.5]]),
      merchants: [{ id: "caravan", name: "A trading caravan", style: "fair", schedule: { every: 4, start: 2, stay: 2 },
        sells: { food: { price: 5, stock: 60 }, medicine: { price: 12, stock: 6 } }, buys: { food: { price: 3, max: 40 } } }],
      facts: [], story: { nodes: [] },
    };
  }

  // =====================================================================================
  // 8. Navigation and section framework
  // =====================================================================================
  const SECTIONS = ["overview", "people", "archetypes", "world", "merchants", "projects", "facts", "story", "collapse", "simulate"];
  const renderers = {};
  let cleanupSection = null;
  function go(section, force = false) {
    if (!SECTIONS.includes(section)) section = "overview";
    if (location.hash !== `#${section}`) {
      state.section = section;
      history.pushState(null, "", `#${section}`);
      renderSection(section);
    } else if (force || state.section !== section) {
      state.section = section;
      renderSection(section);
    }
  }
  function renderSection(section, { keepScroll = false } = {}) {
    if (!state.raw) return;
    const scroll = window.scrollY;
    if (cleanupSection) { try { cleanupSection(); } catch (error) { console.error(error); } cleanupSection = null; }
    sectionSubs.forEach((off) => off());
    sectionSubs = [];
    openSheets.clear();
    if (sheetBackdrop) sheetChrome();
    $("#loading").hidden = true;
    for (const el of $$(".lab-section")) el.hidden = el.dataset.section !== section;
    for (const a of $$("#nav-list a")) { if (a.dataset.section === section) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current"); }
    revealNavItem($(`#nav-list a[data-section="${section}"]`));
    const host = $(`#sec-${section}`);
    fill(host);
    try {
      cleanupSection = renderers[section](host) || null;
    } catch (error) {
      console.error(error);
      put(host, h("div", { class: "callout bad" }, `This section could not be drawn: ${error.message}`));
    }
    if (keepScroll) window.scrollTo(0, scroll);
    else if (!keepScroll && window.scrollY > 200) window.scrollTo(0, 0);
  }
  /** Re-render the current section after a structural change, keeping the scroll position. */
  function rerender() { renderSection(state.section, { keepScroll: true }); }
  function sectionHead(eyebrow, title, lead, ...actions) {
    return h("header", { class: "section-head" },
      h("div", null, h("p", { class: "eyebrow" }, eyebrow), h("h2", null, title), lead ? h("p", { class: "lead" }, lead) : null),
      actions.length ? h("div", { class: "toolbar" }, actions) : null);
  }
  function card(title, ...children) {
    return h("div", { class: "card" }, title ? h("h3", null, title) : null, children);
  }
  function foldCard(title, hint, open, ...children) {
    return h("details", { class: "fold", open }, h("summary", null, title, hint ? h("span", { class: "hint" }, hint) : null), h("div", { class: "fold-body" }, children));
  }

  // =====================================================================================
  // 9. Section: Overview (village hero, difficulty profile, identity, time, scoring)
  // =====================================================================================
  renderers.overview = (host) => {
    const raw = state.raw;
    const world = worldEff();
    const overlayTitle = h("h2", null, raw.name || raw.id);
    const overlayText = h("p", null, truncate(raw.description || "A society waiting for its story.", 240));
    const villageBox = h("div", { class: "hero-village" },
      h("div", { class: "overlay", style: { zIndex: 2 } }, h("p", { class: "eyebrow" }, `${world.name} · ${ticks()} weeks · ${residents().length} residents`), overlayTitle, overlayText));
    put(host, 
      sectionHead("Society", "Overview", "Shape a society, then preview it with scripted reference stewards. A good benchmark society collapses under a passive steward, barely survives a naive one and lets an expert flourish."),
      h("div", { class: "hero" }, villageBox, difficultyCard()),
      statsRow(),
      h("div", { class: "grid cols-2", style: { marginTop: "18px" } }, identityCard(overlayTitle, overlayText), h("div", { class: "stack" }, timeCard(), worldBasicsCard())),
      scoringExplainer());
    let village = null;
    if (window.SocietyVillage) {
      village = window.SocietyVillage.mount(villageBox, { loop: true, autoplay: true, weekMs: 2600, captions: true, captionsAt: "top", steward: false, notebook: false });
      village.load(heroData());
    }
    return () => { if (village) village.destroy(); };
  };
  function heroData() {
    const world = worldEff();
    const T = ticks();
    const nodes = storyNodes();
    const frames = [];
    const steps = 8;
    const list = residents().filter((r) => r && typeof r.id === "string");
    let previous = 0;
    for (let k = 0; k < steps; k++) {
      const week = 1 + Math.round((k * (T - 1)) / steps);
      const people = list.map((r) => {
        const st = residentState(r);
        const roll = hashString(`${r.id}:${k}`);
        let activity = "work";
        if (k % 4 === 2) activity = roll < 0.3 ? "trade" : roll < 0.5 ? "rest" : "work";
        if (k % 4 === 3) activity = r.role === "elder" ? "lore" : r.role === "youth" ? "learn" : roll < 0.35 ? "rest" : "work";
        const wb = clamp(8.2 - 4 * st.stress - 3 * st.loneliness - 2 * st.grievance + (st.trust - 0.5) * 2, 1, 10);
        return { id: r.id, status: "present", activity, wb, st: st.stress, lo: st.loneliness, gr: st.grievance, tr: st.trust, lg: 0.5,
          hp: st.health, coins: st.coins, food: st.food, protest: false, sick: false, hungry: false,
          concern: isObject(r.concern) ? { kind: r.concern.kind, intensity: r.concern.intensity ?? 0.5 } : null, task: "work" };
      });
      const events = k ? nodes.filter((n) => n.trigger && Number.isInteger(n.trigger.at) && n.trigger.at > previous && n.trigger.at <= week)
        .slice(0, 2).map((n) => `Week ${n.trigger.at} · ${n.title || n.id}`) : [];
      previous = week;
      frames.push({ week, season: seasonAt(week), residents: people, ties: ties().filter(Array.isArray).map((t) => [t[0], t[1], Number(t[2]) || 0]),
        commons: { stock: Number(world.commons.stock) }, conflicts: {}, events, metrics: {} });
    }
    return { frames, residents: list.map((r) => ({ id: r.id, name: r.name, role: r.role, faction: r.faction, newcomer: false })),
      factions: factions(), recipe: { world } };
  }
  function statsRow() {
    const nodes = storyNodes();
    const stat = (value, label) => h("div", { class: "stat" }, h("strong", null, String(value)), h("span", null, label));
    return h("div", { class: "stats" },
      stat(residents().length, "residents"),
      stat(newcomers().length, "newcomers in the story"),
      stat(factions().length, "factions"),
      stat(ties().length, "social ties"),
      stat(merchants().length, "merchants"),
      stat(facts().length, "facts & rumours"),
      stat(nodes.length, "story nodes"),
      stat(nodes.filter((n) => n.outcome && n.outcome.type === "collapse").length, "collapse outcomes"),
      stat(nodes.filter((n) => n.outcome && n.outcome.type === "flourish").length, "flourishing outcomes"));
  }
  function identityCard(overlayTitle, overlayText) {
    const raw = state.raw;
    const tags = Array.isArray(raw.tags) ? raw.tags : [];
    const idField = state.meta.saved
      ? field("Recipe id", h("input", { type: "text", value: raw.id, readonly: true, "data-path": "id", class: "mono" }), "Fixed once saved. Duplicate to use another id.")
      : F.text({ label: "Recipe id", value: raw.id, path: "id", mono: true, max: 64, hint: "Lowercase letters, digits, '-' or '_'.",
        onChange: (v) => { raw.id = v.trim(); touch("meta"); renderRecipeSelect(); } });
    return card("Identity",
      h("div", { class: "form-grid two-col" },
        F.text({ label: "Name", value: raw.name, path: "name", max: 80, onChange: (v) => { raw.name = v; overlayTitle.textContent = v; touch("meta"); renderRecipeSelect(); } }),
        idField,
        F.text({ label: "Author", value: raw.author || "", path: "author", max: 80, onChange: (v) => { raw.author = v; touch(); } }),
        F.tags({ label: "Tags", values: tags, path: "tags", onChange: () => { raw.tags = tags; touch(); } }),
        h("div", { class: "span-all" }, F.area({ label: "Description", value: raw.description || "", path: "description", max: 2000, rows: 6,
          hint: "What the steward will face, in a few sentences. Shown to researchers, not to the candidate.",
          onChange: (v) => { raw.description = v; overlayText.textContent = truncate(v, 240); touch(); } }))));
  }
  function timeCard() {
    const raw = state.raw;
    const seeds = h("input", { type: "text", value: (raw.seeds || [1]).join(", "), "data-path": "seeds", class: "mono", inputmode: "numeric" });
    seeds.addEventListener("input", () => {
      const tokens = seeds.value.split(/[\s,;]+/).filter(Boolean);
      raw.seeds = tokens.map((t) => (/^\d+$/.test(t) ? Number(t) : t));
      if (Number.isInteger(raw.seeds[0])) state.seed = raw.seeds[0];
      touch("seeds");
    });
    return card("Time & steward",
      h("div", { class: "form-grid two-col" },
        h("div", { class: "span-all" }, bindRange("Weeks (ticks)", ["ticks"], 52, { min: 12, max: 240, step: 1, integer: true, topic: "ticks",
          hint: "One steward decision per week. 52 weeks = one year." })),
        field("Seeds", seeds, "One to eight distinct integers; each seed is a separate episode."),
        bindText("Steward title", ["steward", "title"], cat().defaults.steward.title, { max: 60 }),
        bindRange("Actions per week", ["steward", "actions_per_week"], cat().defaults.steward.actions_per_week, { min: 1, max: 12, step: 1, integer: true,
          hint: "How many actions the steward may take each week." }),
        bindNumber("Note length (characters)", ["steward", "note_chars"], cat().defaults.steward.note_chars, { min: 0, max: 4000, integer: true,
          hint: "Size of the steward's private notebook carried across weeks." })));
  }
  function worldBasicsCard() {
    const world = worldEff();
    return card("World",
      h("div", { class: "form-grid two-col" },
        bindText("World name", ["world", "name"], world.name, { max: 60 }),
        bindNumber("Season length (weeks)", ["world", "season_length"], world.season_length, { min: 2, max: 60, integer: true }),
        h("div", { class: "span-all" }, F.segmented({ label: "Starting season", value: world.start_season, path: "world.start_season",
          options: cat().seasons.map((sname) => [sname, titleCase(sname)]),
          onChange: (v) => { setIn(state.raw, ["world", "start_season"], v); touch("world"); } }))));
  }
  function scoringExplainer() {
    const domains = cat().domains;
    return h("div", { class: "card", style: { marginTop: "18px" } },
      h("h3", null, "How a society is scored"),
      h("p", { class: "muted", style: { maxWidth: "980px" } },
        "Scores are unbounded points in natural units. Every domain counts something real — informed resident-weeks, coins of net worth created, service-weeks of public works, fish harvested, trust-weeks — and the recipe's exchange rate converts those units into points. Nothing caps the total: a better steward can always create more. If the society collapses the score is ",
        h("strong", null, "0"), " and everything accumulated is reported as forfeited."),
      h("div", { class: "chips", style: { marginTop: "12px" } }, Object.entries(domains).map(([key, d]) => h("span", { class: "chip", title: d.help },
        h("b", { style: { color: "var(--accent-hi)" } }, key), ` ${d.name} · ${d.unit}`))),
      h("p", { class: "small muted", style: { marginTop: "10px" } }, "Exchange rates, collapse rules and scoring constants live under ", h("a", { href: "#collapse" }, "Collapse & scoring"), "."));
  }

  // ----- difficulty profile
  async function runCalibration({ quiet = false } = {}) {
    if (state.calibBusy) return;
    state.calibBusy = true;
    state.calibError = null;
    bus.emit("calib");
    const snapshot = JSON.stringify(state.raw);
    try {
      const data = await API.calibrate(state.raw, {});
      state.calib = { snapshot, data };
    } catch (error) {
      state.calibError = error.message;
      if (!quiet) toast(`Calibration failed: ${error.message}`, "bad");
    } finally {
      state.calibBusy = false;
      bus.emit("calib");
    }
  }
  function aggregateCalibration(rows) {
    const out = {};
    for (const policy of Object.keys(STEWARDS)) {
      const list = rows.filter((r) => r.policy === policy);
      if (!list.length) continue;
      const mean = (key) => list.reduce((a, r) => a + (r[key] || 0), 0) / list.length;
      const collapses = list.filter((r) => r.collapsed);
      out[policy] = { n: list.length, total: mean("total"), earned: mean("earned"), survived: mean("survived_weeks"), collapses,
        wellby: mean("wellby") };
    }
    return out;
  }
  function difficultyCard() {
    const body = h("div", { "aria-live": "polite" });
    const runButton = h("button", { type: "button", class: "small", onclick: () => runCalibration() }, "Run calibration");
    const box = h("div", { class: "card glow" }, h("h3", null, h("span", { class: "grow" }, "Difficulty profile"), runButton), body);
    const seconds = h("input", { type: "number", min: 1, max: 600, step: 1, value: storageGet("society-lab:spd") || 15, style: { width: "84px" }, "aria-label": "Seconds per steward decision" });
    const estimate = h("span", { class: "small muted" });
    seconds.addEventListener("input", debounce(() => { storageSet("society-lab:spd", Number(seconds.value) || 15); updateEstimate(); }, 300));
    async function updateEstimate() {
      const spd = clamp(Number(seconds.value) || 15, 0.1, 3600);
      let decisions = ticks() * ((state.raw.seeds || [1]).length || 1);
      let minutes = (decisions * spd * 1.3) / 60;
      if (state.meta.saved && !state.dirty) {
        try { const r = await API.estimate(state.raw.id, spd); decisions = r.decisions; minutes = r.minutes; } catch { /* fall back to the local estimate */ }
      }
      estimate.textContent = `≈ ${minutes.toFixed(1)} min for ${decisions} decisions (weeks × seeds, +30% margin)`;
    }
    function draw() {
      runButton.disabled = state.calibBusy;
      fill(body);
      if (state.calibBusy) { put(body, h("p", { class: "muted row" }, h("span", { class: "spinner" }), "Running the five reference stewards…")); }
      else if (state.calibError) put(body, h("div", { class: "callout bad" }, state.calibError));
      else if (!state.calib) put(body, h("p", { class: "muted small" }, "Run the reference stewards (passive, random, naive, expert, oracle) on the recipe's seeds to see how hard this society is."));
      if (state.calib && !state.calibBusy) put(body, calibrationView(state.calib));
      put(body, h("div", { class: "divider" }), h("div", { class: "row small" }, h("span", { class: "muted" }, "Run time of a model at"), seconds, h("span", { class: "muted" }, "s / decision:"), estimate));
      updateEstimate();
    }
    subscribe("calib", draw);
    subscribe("ticks", debounce(updateEstimate, 300));
    subscribe("seeds", debounce(updateEstimate, 300));
    draw();
    if (!state.calib && !state.calibAuto && state.validation.status !== "error") { state.calibAuto = true; runCalibration({ quiet: true }); }
    return box;
  }
  function calibrationView(calib) {
    const agg = aggregateCalibration(calib.data.rows);
    const max = Math.max(1, ...Object.values(agg).map((a) => Math.max(a.total, a.earned)));
    const stale = calib.snapshot !== JSON.stringify(state.raw);
    const rows = Object.entries(agg).map(([policy, a]) => {
      const collapsed = a.collapses.length;
      const all = collapsed === a.n;
      const bar = h("div", { class: "bar" }, h("i", { class: all ? "ghost" : policy === "expert" || policy === "oracle" ? "good" : "", style: { width: `${((all ? a.earned : a.total) / max) * 100}%` } }));
      const weeks = a.collapses.map((r) => `w${r.collapsed.week}`).join(", ");
      const causes = Array.from(new Set(a.collapses.map((r) => r.collapsed.cause))).join(", ");
      const value = all
        ? h("div", { class: "val" }, h("span", { class: "collapse-tag" }, "COLLAPSE"), h("small", null, `${weeks} · ${causes}`))
        : h("div", { class: "val" }, fmt.int(a.total), h("small", null, collapsed ? `${collapsed}/${a.n} collapsed (${causes})` : `${a.n} seed${a.n > 1 ? "s" : ""} · ${fmt.num(a.wellby, 0)} WELLBY`));
      return h("div", { class: "difficulty-row", title: STEWARDS[policy] }, h("div", { class: "who" }, policy, h("small", null, all ? `forfeited ${fmt.int(a.earned)}` : "mean points")), bar, value);
    });
    const p = agg.passive, n = agg.naive, e = agg.expert, o = agg.oracle;
    const checks = [];
    const check = (ok, text) => checks.push(h("div", { class: `check ${ok ? "ok" : "no"}` }, h("b", { "aria-hidden": "true" }, ok ? "✓" : "✕"), h("span", null, text)));
    if (p) check(p.collapses.length === p.n, p.collapses.length === p.n ? "A passive steward loses the society." : "A passive steward survives: neglect is not punished enough.");
    if (n && e) {
      const ratio = e.total > 0 ? n.total / e.total : 0;
      const ok = n.collapses.length < n.n && ratio < 0.85;
      check(ok, n.collapses.length === n.n ? "The naive steward collapses: the society may be too harsh." : `The naive steward survives with ${fmt.pct(ratio)} of the expert's points${ratio >= 0.85 ? " — too easy to tell them apart?" : "."}`);
    }
    if (e) check(e.collapses.length === 0 && (!n || e.total > n.total), e.collapses.length ? "The expert collapses: the society may be unwinnable." : "The expert steward thrives.");
    if (o) check(o.collapses.length === 0, o.collapses.length ? "Even the oracle collapses." : "The oracle survives (an optimistic reference, never a cap).");
    return h("div", null,
      stale ? h("p", { class: "badge watch", style: { marginBottom: "8px" } }, "Recipe changed since this run — re-run to refresh") : null,
      rows, h("div", { class: "checks" }, checks),
      h("p", { class: "tiny muted", style: { marginTop: "8px" } }, `Seeds: ${calib.data.seeds.join(", ")}. Collapse scores 0; the striped bar shows the points forfeited.`));
  }

  // =====================================================================================
  // 10. Section: People & network
  // =====================================================================================
  /** Animated force-directed network of residents, coloured by faction, sized by wealth. */
  class NetworkView {
    constructor(wrap, handlers) {
      this.wrap = wrap;
      this.handlers = handlers;
      this.canvas = h("canvas", { tabindex: "0", "aria-label": "Social network of the residents. Use the roster to select residents with the keyboard." });
      this.tip = h("div", { class: "canvas-tip", style: { opacity: "0" }, "aria-hidden": "true" });
      this.hint = h("div", { class: "canvas-hint", hidden: true, role: "status" });
      put(wrap, this.canvas, this.tip, this.hint);
      this.ctx = this.canvas.getContext("2d");
      this.nodes = new Map();
      this.alpha = 1;
      this.k = 1;
      this.zoomK = 1;
      this.pan = { x: 0, y: 0 };
      this.pointers = new Map();
      this.pinch = null;
      this.press = 0;
      this.lastTap = null;
      this.lastDraw = 0;
      this.fitted = false;
      this.connect = false;
      this.pending = null;
      this.hover = null;
      this.drag = null;
      this.mouse = null;
      this.labels = true;
      this.raf = 0;
      this.visible = true;
      this.w = 1; this.h = 1; this.dpr = 1;
      this.persist = debounce(() => this.savePositions(), 600);
      this.sync();
      this.bind();
      this.ro = new ResizeObserver(() => this.resize());
      this.ro.observe(wrap);
      this.unwatch = watchVisibility(wrap, (visible) => { this.visible = visible; if (visible) this.kick(); });
      this.resize();
    }
    layoutKey() { return `society-lab:layout:${state.raw.id}`; }
    savePositions() {
      const out = {};
      for (const n of this.nodes.values()) out[n.id] = [Math.round(n.x), Math.round(n.y)];
      storageSet(this.layoutKey(), out);
    }
    sync() {
      const saved = storageGet(this.layoutKey()) || {};
      const factionIds = factions().map((f) => f.id);
      const seen = new Set();
      residents().forEach((r, index) => {
        if (!r || typeof r.id !== "string") return;
        seen.add(r.id);
        let n = this.nodes.get(r.id);
        if (!n) {
          const pos = saved[r.id];
          const fi = Math.max(0, factionIds.indexOf(r.faction));
          const angle = (fi / Math.max(1, factionIds.length)) * Math.PI * 2 + hashString(r.id) * 0.9;
          const radius = 110 + hashString(`${r.id}:r`) * 90;
          n = { id: r.id, x: pos ? pos[0] : Math.cos(angle) * radius, y: pos ? pos[1] : Math.sin(angle) * radius, vx: 0, vy: 0, fixed: false };
          this.nodes.set(r.id, n);
        }
        n.r = r;
        n.index = index;
      });
      for (const id of Array.from(this.nodes.keys())) if (!seen.has(id)) this.nodes.delete(id);
      this.reheat(Object.keys(saved).length ? 0.35 : 1);
    }
    rename(from, to) {
      const n = this.nodes.get(from);
      if (!n) return;
      this.nodes.delete(from);
      n.id = to;
      this.nodes.set(to, n);
      this.savePositions();
    }
    relayout() {
      let i = 0;
      for (const n of this.nodes.values()) { const a = (i++ / this.nodes.size) * Math.PI * 2; n.x = Math.cos(a) * 200; n.y = Math.sin(a) * 200; }
      this.reheat(1);
    }
    reheat(alpha = 0.5) { this.alpha = Math.max(this.alpha, alpha); this.kick(); }
    kick() { if (!this.raf && this.visible) this.raf = requestAnimationFrame((t) => this.frame(t)); }
    resize() {
      const rect = this.wrap.getBoundingClientRect();
      this.w = Math.max(1, rect.width);
      this.h = Math.max(1, rect.height);
      this.dpr = Math.min(window.devicePixelRatio || 1, 2.5);
      this.canvas.width = Math.round(this.w * this.dpr);
      this.canvas.height = Math.round(this.h * this.dpr);
      this.kick();
    }
    frame(now) {
      this.raf = 0;
      if (!this.visible) return;
      const busy = this.alpha > 0 || this.drag || this.pinch;
      // On phones the idle selection pulse runs at ~30 fps.
      if (COARSE && !busy && now - this.lastDraw < 32) { this.kick(); return; }
      this.lastDraw = now;
      if (this.alpha > 0) this.step();
      this.draw(now);
      const selected = this.handlers.selectedResidentId() || this.handlers.selectedTie() != null;
      if (this.alpha > 0 || (selected && !reducedMotion()) || this.drag || this.pinch || this.pending) this.kick();
    }
    step() {
      const nodes = Array.from(this.nodes.values());
      const a = this.alpha;
      for (let i = 0; i < nodes.length; i++) {
        const p = nodes[i];
        for (let j = i + 1; j < nodes.length; j++) {
          const q = nodes[j];
          let dx = q.x - p.x, dy = q.y - p.y;
          let d2 = dx * dx + dy * dy;
          if (d2 < 0.01) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; d2 = 0.5; }
          if (d2 > 360000) continue;
          const f = (2200 * a) / d2;
          p.vx -= dx * f; p.vy -= dy * f; q.vx += dx * f; q.vy += dy * f;
        }
      }
      for (const t of ties()) {
        if (!Array.isArray(t)) continue;
        const p = this.nodes.get(t[0]), q = this.nodes.get(t[1]);
        if (!p || !q) continue;
        const strength = clamp(Number(t[2]) || 0, 0, 1);
        const dx = q.x - p.x, dy = q.y - p.y;
        const d = Math.sqrt(dx * dx + dy * dy) || 1;
        const f = ((d - (150 - 85 * strength)) / d) * (0.02 + 0.07 * strength) * a;
        p.vx += dx * f; p.vy += dy * f; q.vx -= dx * f; q.vy -= dy * f;
      }
      const centroids = new Map();
      for (const n of nodes) {
        const key = n.r.faction || "";
        const c = centroids.get(key) || { x: 0, y: 0, n: 0 };
        c.x += n.x; c.y += n.y; c.n += 1;
        centroids.set(key, c);
      }
      for (const n of nodes) {
        const c = centroids.get(n.r.faction || "");
        if (c && c.n > 1) { n.vx += (c.x / c.n - n.x) * 0.012 * a; n.vy += (c.y / c.n - n.y) * 0.012 * a; }
        n.vx -= n.x * 0.006 * a; n.vy -= n.y * 0.006 * a;
        if (n.fixed) { n.vx = 0; n.vy = 0; continue; }
        n.vx *= 0.6; n.vy *= 0.6;
        n.x += clamp(n.vx, -24, 24); n.y += clamp(n.vy, -24, 24);
      }
      this.alpha *= 0.985;
      if (this.alpha < 0.004) { this.alpha = 0; this.persist(); }
    }
    /** Screen scale: the automatic fit times the viewer's zoom. */
    scale() { return this.k * this.zoomK; }
    toWorld(event) {
      const rect = this.canvas.getBoundingClientRect();
      const K = this.scale();
      return { x: (event.clientX - rect.left - this.w / 2 - this.pan.x) / K, y: (event.clientY - rect.top - this.h / 2 - this.pan.y) / K };
    }
    resetView() { this.zoomK = 1; this.pan = { x: 0, y: 0 }; this.kick(); }
    zoomAt(factor, mx, my) {
      const K0 = this.scale();
      const wx = (mx - this.w / 2 - this.pan.x) / K0, wy = (my - this.h / 2 - this.pan.y) / K0;
      this.zoomK = clamp(this.zoomK * factor, 0.35, 4);
      const K = this.scale();
      this.pan.x = mx - this.w / 2 - wx * K;
      this.pan.y = my - this.h / 2 - wy * K;
      this.kick();
    }
    setPending(id) {
      this.pending = id || null;
      const n = id ? this.nodes.get(id) : null;
      this.hint.hidden = !n && !this.connect;
      this.hint.textContent = n ? `Now pick the resident to tie with ${String(n.r.name || n.id).split(" ")[0]} · empty space cancels`
        : "Connect: pick a resident, then the one to tie them with";
      this.kick();
    }
    hideTip() { this.hover = null; this.tip.style.opacity = "0"; }
    cancelPress() { clearTimeout(this.press); this.press = 0; }
    radius(n) {
      const coins = residentState(n.r).coins;
      return 8 + 10 * Math.sqrt(clamp((coins || 0) / (this.maxCoins || 30), 0, 1));
    }
    hitNode(p, slop = 6) {
      let best = null, bestD = Infinity;
      const K = this.scale();
      for (const n of this.nodes.values()) {
        const d = Math.hypot(n.x - p.x, n.y - p.y) * K;
        if (d < this.radius(n) + slop && d < bestD) { best = n; bestD = d; }
      }
      return best;
    }
    hitTie(p, slop = 7) {
      let best = null, bestD = slop / this.scale();
      ties().forEach((t, i) => {
        if (!Array.isArray(t)) return;
        const a = this.nodes.get(t[0]), b = this.nodes.get(t[1]);
        if (!a || !b) return;
        const dx = b.x - a.x, dy = b.y - a.y;
        const len2 = dx * dx + dy * dy || 1;
        const u = clamp(((p.x - a.x) * dx + (p.y - a.y) * dy) / len2, 0, 1);
        const d = Math.hypot(a.x + u * dx - p.x, a.y + u * dy - p.y);
        if (d < bestD) { bestD = d; best = i; }
      });
      return best;
    }
    bind() {
      const c = this.canvas;
      const touchy = (e) => e.pointerType !== "mouse";
      this.onDown = (e) => {
        if (e.pointerType === "mouse" && e.button !== 0) return;
        try { c.setPointerCapture(e.pointerId); } catch { /* pointer already gone */ }
        this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
        if (this.pointers.size === 2) { this.startPinch(); return; }
        if (this.pointers.size > 2 || this.pinch) return;
        const touch = touchy(e);
        if (touch) this.hideTip();
        this.cancelPress();
        const p = this.toWorld(e);
        const n = this.hitNode(p, touch ? 16 : 6);
        if (n) {
          this.drag = { n, sx: e.clientX, sy: e.clientY, moved: false, touch };
          // Touch: a long press starts a tie from this resident (no shift key on a phone).
          if (touch && !this.connect) {
            this.press = setTimeout(() => {
              if (!this.drag || this.drag.n !== n || this.drag.moved) return;
              this.drag.pressed = true;
              this.setPending(n.id);
              if (navigator.vibrate) navigator.vibrate(12);
            }, 460);
          }
        } else {
          this.drag = { pan: true, tie: this.hitTie(p, touch ? 16 : 7), sx: e.clientX, sy: e.clientY, px: this.pan.x, py: this.pan.y, moved: false, touch };
        }
      };
      this.onMove = (e) => {
        if (this.pointers.has(e.pointerId)) this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
        if (this.pinch) { this.movePinch(); return; }
        const p = this.toWorld(e);
        this.mouse = p;
        const drag = this.drag;
        if (drag) {
          if (!drag.moved && Math.hypot(e.clientX - drag.sx, e.clientY - drag.sy) > (drag.touch ? 8 : 3)) { drag.moved = true; this.cancelPress(); }
          if (drag.n) {
            if (drag.moved && !drag.pressed) { drag.n.x = p.x; drag.n.y = p.y; drag.n.fixed = true; this.reheat(0.25); }
            if (drag.pressed) this.kick();
            return;
          }
          if (drag.moved) {
            this.pan.x = drag.px + e.clientX - drag.sx;
            this.pan.y = drag.py + e.clientY - drag.sy;
            c.style.cursor = "grabbing";
            this.kick();
            return;
          }
        }
        if (touchy(e)) { if (this.pending) this.kick(); return; }
        const n = this.hitNode(p);
        const t = n ? null : this.hitTie(p);
        c.style.cursor = n ? (this.connect ? "crosshair" : "grab") : t != null ? "pointer" : this.connect || this.pending ? "crosshair" : "grab";
        if ((n && n.id) !== (this.hover && this.hover.id)) { this.hover = n; this.updateTip(e); this.kick(); }
        else if (n) this.updateTip(e);
        if (this.pending) this.kick();
      };
      this.onUp = (e) => {
        this.pointers.delete(e.pointerId);
        if (this.pinch) { if (this.pointers.size < 2) { this.pinch = null; this.drag = null; } return; }
        this.cancelPress();
        const drag = this.drag;
        this.drag = null;
        if (!drag) return;
        const touch = drag.touch;
        if (drag.n) {
          drag.n.fixed = false;
          if (drag.pressed) {
            // Released over another resident after the long press: tie them now; otherwise wait for a tap.
            const target = this.hitNode(this.toWorld(e), 16);
            if (target && target.id !== drag.n.id) { this.setPending(null); this.handlers.connect(drag.n.id, target.id); }
            return;
          }
          if (!drag.moved) this.clickNode(drag.n, e.shiftKey);
          else this.persist();
          return;
        }
        c.style.cursor = this.connect || this.pending ? "crosshair" : "grab";
        if (drag.moved) return;
        if (this.pending) { this.setPending(null); return; }
        if (drag.tie != null) { this.handlers.selectTie(drag.tie); return; }
        // A double tap on empty space fits the view again.
        const now = performance.now();
        if (this.lastTap && now - this.lastTap.t < 340 && Math.hypot(e.clientX - this.lastTap.x, e.clientY - this.lastTap.y) < (touch ? 32 : 12)) {
          this.lastTap = null;
          this.resetView();
        } else this.lastTap = { t: now, x: e.clientX, y: e.clientY };
      };
      this.onCancel = (e) => {
        this.pointers.delete(e.pointerId);
        this.cancelPress();
        if (this.drag && this.drag.n) this.drag.n.fixed = false;
        this.drag = null;
        if (this.pointers.size < 2) this.pinch = null;
      };
      this.onLeave = (e) => { if (e.pointerType === "mouse") { this.hideTip(); this.kick(); } };
      this.onKey = (e) => {
        if (e.key === "Escape" && this.pending) { this.setPending(null); }
        else if (e.key === "+" || e.key === "=") { this.zoomAt(1.2, this.w / 2, this.h / 2); e.preventDefault(); }
        else if (e.key === "-" || e.key === "_") { this.zoomAt(1 / 1.2, this.w / 2, this.h / 2); e.preventDefault(); }
        else if (e.key === "0") { this.resetView(); e.preventDefault(); }
      };
      // Trackpad pinches arrive as ctrl+wheel; plain wheel scrolling keeps scrolling the page.
      this.onWheel = (e) => {
        if (!e.ctrlKey) return;
        e.preventDefault();
        const r = c.getBoundingClientRect();
        this.zoomAt(Math.exp(-e.deltaY * 0.01), e.clientX - r.left, e.clientY - r.top);
      };
      c.addEventListener("pointerdown", this.onDown);
      c.addEventListener("pointermove", this.onMove);
      c.addEventListener("pointerup", this.onUp);
      c.addEventListener("pointercancel", this.onCancel);
      c.addEventListener("pointerleave", this.onLeave);
      c.addEventListener("keydown", this.onKey);
      c.addEventListener("wheel", this.onWheel, { passive: false });
    }
    startPinch() {
      this.cancelPress();
      if (this.drag && this.drag.n) this.drag.n.fixed = false;
      this.drag = null;
      this.hideTip();
      const [a, b] = Array.from(this.pointers.values());
      const r = this.canvas.getBoundingClientRect();
      const mx = (a.x + b.x) / 2 - r.left, my = (a.y + b.y) / 2 - r.top;
      const K = this.scale();
      this.pinch = { d0: Math.hypot(a.x - b.x, a.y - b.y) || 1, z0: this.zoomK, wx: (mx - this.w / 2 - this.pan.x) / K, wy: (my - this.h / 2 - this.pan.y) / K };
      this.kick();
    }
    movePinch() {
      const [a, b] = Array.from(this.pointers.values());
      if (!a || !b) return;
      const r = this.canvas.getBoundingClientRect();
      const mx = (a.x + b.x) / 2 - r.left, my = (a.y + b.y) / 2 - r.top;
      this.zoomK = clamp(this.pinch.z0 * (Math.hypot(a.x - b.x, a.y - b.y) / this.pinch.d0), 0.35, 4);
      const K = this.scale();
      this.pan.x = mx - this.w / 2 - this.pinch.wx * K;
      this.pan.y = my - this.h / 2 - this.pinch.wy * K;
      this.kick();
    }
    clickNode(n, shift) {
      const selected = this.handlers.selectedResidentId();
      if (this.connect || this.pending) {
        if (!this.pending) this.setPending(n.id);
        else if (this.pending !== n.id) { const from = this.pending; this.setPending(null); this.handlers.connect(from, n.id); }
        else this.setPending(null);
        return;
      }
      if (shift && selected && selected !== n.id) { this.handlers.connect(selected, n.id); return; }
      this.handlers.selectResident(n.index);
    }
    updateTip(e) {
      const n = this.hover;
      if (!n) { this.tip.style.opacity = "0"; return; }
      const lib = archetypesEff();
      const st = residentState(n.r);
      const degree = ties().filter((t) => Array.isArray(t) && (t[0] === n.id || t[1] === n.id)).length;
      fill(this.tip, h("b", null, n.r.name || n.id),
        h("div", { class: "muted" }, `${titleCase(n.r.role || "farmer")} · ${factionName(n.r.faction)}`),
        h("div", null, (lib[n.r.archetype || "conditional_cooperator"] || {}).label || n.r.archetype),
        h("div", { class: "muted" }, `Coins ${fmt.num(st.coins, 0)} · Health ${fmt.num(st.health, 0)} · ${degree} tie${degree === 1 ? "" : "s"}`));
      const rect = this.wrap.getBoundingClientRect();
      const tw = this.tip.offsetWidth || 220, th = this.tip.offsetHeight || 90;
      const x = clamp(e.clientX - rect.left + 14, 6, Math.max(6, rect.width - tw - 6));
      const y = clamp(e.clientY - rect.top + 14, 6, Math.max(6, rect.height - th - 6));
      this.tip.style.left = `${x}px`;
      this.tip.style.top = `${y}px`;
      this.tip.style.opacity = "1";
    }
    sx(n) { return this.w / 2 + this.pan.x + n.x * this.scale(); }
    sy(n) { return this.h / 2 + this.pan.y + n.y * this.scale(); }
    draw(now) {
      const ctx = this.ctx;
      ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
      ctx.clearRect(0, 0, this.w, this.h);
      const nodes = Array.from(this.nodes.values());
      if (!nodes.length) return;
      this.maxCoins = Math.max(30, ...nodes.map((m) => Number(residentState(m.r).coins) || 0));
      let mx = 40, my = 40;
      for (const n of nodes) { mx = Math.max(mx, Math.abs(n.x)); my = Math.max(my, Math.abs(n.y)); }
      const target = clamp(Math.min((this.w / 2 - (this.w < 520 ? 36 : 70)) / mx, (this.h / 2 - (this.w < 520 ? 74 : 70)) / my), 0.25, 1.6);
      if (!this.fitted && this.w > 10) { this.k = target; this.fitted = true; }
      else if (!this.drag && !this.pinch) this.k += (target - this.k) * 0.08;
      this.drawHulls(ctx, nodes);
      this.drawTies(ctx);
      const selectedId = this.handlers.selectedResidentId();
      const tieCount = new Map();
      for (const t of ties()) if (Array.isArray(t)) { tieCount.set(t[0], (tieCount.get(t[0]) || 0) + 1); tieCount.set(t[1], (tieCount.get(t[1]) || 0) + 1); }
      for (const n of nodes) this.drawNode(ctx, n, now, n.id === selectedId, !tieCount.get(n.id));
      if (this.pending && this.mouse) {
        const p = this.nodes.get(this.pending);
        if (p) {
          ctx.setLineDash([6, 6]);
          ctx.strokeStyle = "rgba(226,194,28,0.8)";
          ctx.lineWidth = 2;
          ctx.beginPath(); ctx.moveTo(this.sx(p), this.sy(p)); ctx.lineTo(this.sx(this.mouse), this.sy(this.mouse)); ctx.stroke();
          ctx.setLineDash([]);
        }
      }
    }
    drawHulls(ctx, nodes) {
      for (const f of factions()) {
        const pts = nodes.filter((n) => n.r.faction === f.id).map((n) => [this.sx(n), this.sy(n)]);
        if (!pts.length) continue;
        const hull = convexHull(pts);
        const color = safeColor(f.color);
        ctx.fillStyle = rgba(color, 0.08);
        ctx.strokeStyle = rgba(color, 0.08);
        ctx.lineJoin = "round";
        ctx.lineCap = "round";
        ctx.lineWidth = 58;
        ctx.beginPath();
        hull.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
        ctx.closePath();
        ctx.fill();
        ctx.stroke();
        const top = hull.reduce((a, b) => (b[1] < a[1] ? b : a));
        ctx.fillStyle = rgba(color, 0.85);
        ctx.font = "650 11px system-ui, sans-serif";
        ctx.textAlign = "center";
        ctx.fillText(String(f.name || f.id).toUpperCase(), top[0], top[1] - 36);
      }
      ctx.textAlign = "start";
    }
    drawTies(ctx) {
      const selectedTie = this.handlers.selectedTie();
      const focus = this.handlers.selectedResidentId();
      ties().forEach((t, i) => {
        if (!Array.isArray(t)) return;
        const a = this.nodes.get(t[0]), b = this.nodes.get(t[1]);
        if (!a || !b) return;
        const strength = clamp(Number(t[2]) || 0, 0, 1);
        const lit = i === selectedTie;
        const near = focus && (a.id === focus || b.id === focus);
        const ax = this.sx(a), ay = this.sy(a), bx = this.sx(b), by = this.sy(b);
        const g = ctx.createLinearGradient(ax, ay, bx, by);
        const alpha = lit ? 1 : near ? 0.55 + 0.4 * strength : 0.16 + 0.45 * strength;
        g.addColorStop(0, rgba(factionColor(a.r.faction), alpha));
        g.addColorStop(1, rgba(factionColor(b.r.faction), alpha));
        ctx.strokeStyle = lit ? "#e2c21c" : g;
        ctx.lineWidth = 0.8 + strength * 6 + (lit ? 2 : 0);
        if (lit) { ctx.shadowColor = "rgba(226,194,28,0.8)"; ctx.shadowBlur = 14; }
        ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke();
        ctx.shadowBlur = 0;
      });
    }
    drawNode(ctx, n, now, selected, isolated) {
      const r = this.radius(n);
      const x = this.sx(n), y = this.sy(n);
      const color = factionColor(n.r.faction);
      const st = residentState(n.r);
      if (selected) {
        const pulse = reducedMotion() ? 0 : Math.sin(now / 280) * 2;
        ctx.strokeStyle = "rgba(226,194,28,0.9)";
        ctx.lineWidth = 2;
        ctx.shadowColor = "rgba(226,194,28,0.8)";
        ctx.shadowBlur = 16;
        ctx.beginPath(); ctx.arc(x, y, r + 9 + pulse, 0, Math.PI * 2); ctx.stroke();
        ctx.shadowBlur = 0;
      }
      if (this.pending === n.id) {
        ctx.setLineDash([4, 4]); ctx.strokeStyle = "#e2c21c"; ctx.lineWidth = 2;
        ctx.beginPath(); ctx.arc(x, y, r + 7, 0, Math.PI * 2); ctx.stroke(); ctx.setLineDash([]);
      }
      if (isolated) {
        ctx.setLineDash([3, 4]); ctx.strokeStyle = "rgba(239,106,91,0.75)"; ctx.lineWidth = 1.5;
        ctx.beginPath(); ctx.arc(x, y, r + 7, 0, Math.PI * 2); ctx.stroke(); ctx.setLineDash([]);
      }
      ctx.shadowColor = rgba(color, 0.75);
      ctx.shadowBlur = 16;
      ctx.fillStyle = color;
      ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill();
      ctx.shadowBlur = 0;
      ctx.strokeStyle = "rgba(0,0,0,0.55)"; ctx.lineWidth = 1.5; ctx.stroke();
      const health = clamp(Number(st.health) || 0, 0, 100) / 100;
      ctx.strokeStyle = "rgba(255,255,255,0.08)"; ctx.lineWidth = 2.5;
      ctx.beginPath(); ctx.arc(x, y, r + 3.5, 0, Math.PI * 2); ctx.stroke();
      ctx.strokeStyle = healthColor(health * 100);
      ctx.beginPath(); ctx.arc(x, y, r + 3.5, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * health); ctx.stroke();
      ctx.fillStyle = "rgba(10,10,10,0.78)";
      ctx.font = `700 ${Math.max(9, r * 0.72)}px system-ui, sans-serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(initials(n.r.name), x, y + 0.5);
      if (this.labels || selected || this.hover === n) {
        const label = selected || this.hover === n ? n.r.name || n.id : String(n.r.name || n.id).split(" ")[0];
        ctx.font = `${selected ? 650 : 500} 11.5px system-ui, sans-serif`;
        ctx.fillStyle = selected ? "#f5e7a6" : "rgba(222,222,222,0.82)";
        ctx.fillText(label, x, y + r + 15);
      }
      ctx.textBaseline = "alphabetic";
      ctx.textAlign = "start";
    }
    destroy() {
      cancelAnimationFrame(this.raf);
      this.raf = 0;
      this.visible = false;
      this.cancelPress();
      this.ro.disconnect();
      this.unwatch();
      this.savePositions();
      this.canvas.remove();
      this.tip.remove();
      this.hint.remove();
    }
  }
  function convexHull(points) {
    if (points.length < 3) return points.slice();
    const pts = points.slice().sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
    const lower = [];
    for (const p of pts) { while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) lower.pop(); lower.push(p); }
    const upper = [];
    for (let i = pts.length - 1; i >= 0; i--) { const p = pts[i]; while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) upper.pop(); upper.push(p); }
    upper.pop(); lower.pop();
    return lower.concat(upper);
  }
  function networkStats() {
    const list = residents();
    const adj = new Map(list.map((r) => [r.id, []]));
    let total = 0;
    for (const t of ties()) {
      if (!Array.isArray(t) || !adj.has(t[0]) || !adj.has(t[1])) continue;
      total += 1;
      if ((Number(t[2]) || 0) >= 0.15) { adj.get(t[0]).push(t[1]); adj.get(t[1]).push(t[0]); }
    }
    const seen = new Set();
    let largest = 0;
    for (const id of adj.keys()) {
      if (seen.has(id)) continue;
      let size = 0;
      const stack = [id];
      seen.add(id);
      while (stack.length) { const cur = stack.pop(); size += 1; for (const nb of adj.get(cur)) if (!seen.has(nb)) { seen.add(nb); stack.push(nb); } }
      largest = Math.max(largest, size);
    }
    const tied = new Set();
    for (const t of ties()) if (Array.isArray(t)) { tied.add(t[0]); tied.add(t[1]); }
    const isolated = list.filter((r) => !tied.has(r.id));
    return { ties: total, degree: list.length ? (2 * total) / list.length : 0, cohesion: list.length ? largest / list.length : 0, isolated };
  }

  /** Rewrite every reference to a resident id (ties, sources, story effects and conditions). */
  function renameResidentRefs(from, to) {
    for (const t of ties()) if (Array.isArray(t)) { if (t[0] === from) t[0] = to; if (t[1] === from) t[1] = to; }
    for (const f of facts()) for (const src of f.sources || []) if (src && src.who === from) src.who = to;
    const fixCondition = (c) => {
      if (!isObject(c)) return;
      if (Array.isArray(c.all)) c.all.forEach(fixCondition);
      if (Array.isArray(c.any)) c.any.forEach(fixCondition);
      if (c.not) fixCondition(c.not);
      if (typeof c.metric === "string" && c.metric.endsWith(`:${from}`)) c.metric = c.metric.slice(0, -from.length) + to;
    };
    for (const node of storyNodes()) {
      for (const e of node.effects || []) for (const key of ["who", "from", "a", "b", "at_fault"]) if (e && e[key] === from) e[key] = to;
      if (node.trigger && node.trigger.when) fixCondition(node.trigger.when);
      for (const edge of node.edges || []) if (edge && edge.if) fixCondition(edge.if);
    }
    if (state.network) state.network.rename(from, to);
  }
  function countResidentRefs(id) {
    let n = 0;
    const scan = (value) => {
      if (Array.isArray(value)) value.forEach(scan);
      else if (isObject(value)) Object.values(value).forEach(scan);
      else if (typeof value === "string" && (value === id || value.endsWith(`:${id}`))) n += 1;
    };
    scan(facts());
    scan(storyNodes());
    return n;
  }

  renderers.people = (host) => {
    const addButton = h("button", { type: "button", class: "primary", onclick: addResident }, "+ Add resident");
    put(host, sectionHead("Society", "People & network",
      COARSE ? "Each resident is a scripted agent with hidden needs, emotions, beliefs and ties. Drag residents to arrange the map, pinch to zoom; long-press a resident (or use Connect) and pick a second one to create a tie."
        : "Each resident is a scripted agent with hidden needs, emotions, beliefs and ties. Drag residents to arrange the map; shift-click a second resident (or use Connect) to create a tie.", addButton));
    const wrap = h("div", { class: "network-wrap" });
    const connectButton = h("button", { type: "button", class: "small", "aria-pressed": "false", title: "Pick two residents to create or select their tie" }, "⟷ Connect");
    const labelsButton = h("button", { type: "button", class: "small", "aria-pressed": "true" }, "Labels");
    const layoutButton = h("button", { type: "button", class: "small" }, "Re-layout");
    const fitButton = h("button", { type: "button", title: "Fit the network (double-tap empty space)", "aria-label": "Fit the network in view" }, "⤢");
    const statsLine = h("span", { class: "small muted" });
    const legend = h("div", { class: "network-legend" });
    const zoomIn = h("button", { type: "button", "aria-label": "Zoom in", title: "Zoom in (pinch)" }, "+");
    const zoomOut = h("button", { type: "button", "aria-label": "Zoom out", title: "Zoom out (pinch)" }, "−");
    put(wrap, legend, h("div", { class: "network-help" }, "size = coins · ring = health · line width = tie strength", h("br"), "dashed red ring = no ties"),
      h("div", { class: "story-zoom" }, zoomIn, zoomOut, fitButton));
    const networkCard = h("div", { class: "card network-card" }, h("div", { class: "network-toolbar" }, connectButton, labelsButton, layoutButton, h("span", { class: "spacer" }), statsLine), wrap);
    const factionsBox = h("div", { class: "card" });
    const roster = h("div", { class: "roster" });
    const rosterCard = h("div", { class: "card" }, h("h3", null, h("span", { class: "grow" }, "Roster"), h("span", { class: "tiny muted" }, "Select a resident to edit")), roster);
    const panel = h("div", { class: "panel-body" });
    const aside = h("aside", { class: "card editor-panel", "aria-label": "Resident editor" }, panel);
    put(host, h("div", { class: "people-layout" }, h("div", { class: "stack" }, networkCard, rosterCard, factionsBox), aside));
    const sheet = makeSheet(aside, { title: "Resident" });
    const openEditor = () => sheet.open(editorTitle());
    const editorTitle = () => {
      const tie = state.sel.tie != null ? ties()[state.sel.tie] : null;
      if (tie) return "Social tie";
      const r = residents()[state.sel.resident];
      return r ? r.name || r.id : "Resident";
    };

    const view = new NetworkView(wrap, {
      selectedResidentId: () => (state.sel.tie == null ? (residents()[state.sel.resident] || {}).id : null),
      selectedTie: () => state.sel.tie,
      selectResident: (index) => { state.sel.resident = index; state.sel.tie = null; refreshSelection(); openEditor(); },
      selectTie: (index) => { state.sel.tie = index; refreshSelection(); openEditor(); },
      connect: (a, b) => connectResidents(a, b),
    });
    state.network = view;
    connectButton.addEventListener("click", () => {
      view.connect = !view.connect;
      view.setPending(null);
      connectButton.setAttribute("aria-pressed", String(view.connect));
    });
    labelsButton.addEventListener("click", () => { view.labels = !view.labels; labelsButton.setAttribute("aria-pressed", String(view.labels)); view.kick(); });
    layoutButton.addEventListener("click", () => view.relayout());
    fitButton.addEventListener("click", () => view.resetView());
    zoomIn.addEventListener("click", () => view.zoomAt(1.25, view.w / 2, view.h / 2));
    zoomOut.addEventListener("click", () => view.zoomAt(1 / 1.25, view.w / 2, view.h / 2));

    function drawLegend() {
      fill(legend, ...factions().map((f) => h("span", null, h("i", { class: "swatch", style: { background: safeColor(f.color), color: safeColor(f.color) } }), f.name || f.id)));
      const st = networkStats();
      statsLine.textContent = `${st.ties} ties · mean degree ${fmt.num(st.degree, 1)} · largest group ${fmt.pct(st.cohesion)}${st.isolated.length ? ` · ${st.isolated.length} isolated` : ""}`;
    }
    function drawRoster() {
      const lib = archetypesEff();
      const selectedId = state.sel.tie == null ? (residents()[state.sel.resident] || {}).id : null;
      fill(roster, ...residents().map((r, i) => {
        const color = factionColor(r.faction);
        const b = h("button", { type: "button", class: r.id === selectedId ? "selected" : "", "aria-pressed": String(r.id === selectedId),
          onclick: () => { state.sel.resident = i; state.sel.tie = null; refreshSelection(); openEditor(); } },
          h("span", { class: "avatar", style: { background: color, color, width: "30px", height: "30px", fontSize: "11px" } }, initials(r.name)),
          h("span", { class: "who" }, h("b", null, r.name || r.id), h("small", null, `${titleCase(r.role || "farmer")} · ${(lib[r.archetype || "conditional_cooperator"] || {}).label || r.archetype}`)));
        return b;
      }));
    }
    function drawPanel() {
      fill(panel);
      sheet.setTitle(editorTitle());
      const tie = state.sel.tie != null ? ties()[state.sel.tie] : null;
      if (tie) put(panel, ...tieEditor(state.sel.tie));
      else {
        if (!residents()[state.sel.resident]) state.sel.resident = 0;
        const r = residents()[state.sel.resident];
        if (r) put(panel, ...residentEditor(r, state.sel.resident));
        else put(panel, h("div", { class: "empty-note" }, "No residents yet. Add at least four."));
      }
    }
    function refreshSelection() { drawRoster(); drawPanel(); view.kick(); }
    function addResident() {
      const taken = allIds();
      const current = residents()[state.sel.resident];
      const r = { id: uniqueId("r", taken), name: "New resident", age: 30, role: "farmer", faction: current ? current.faction : (factions()[0] || {}).id,
        archetype: "conditional_cooperator" };
      if (!r.faction) delete r.faction;
      residents().push(r);
      state.sel.resident = residents().length - 1;
      state.sel.tie = null;
      view.sync();
      touch("residents");
      refreshSelection();
      openEditor();
      toast("Resident added. Give them a name, a role and at least one tie.", "good");
    }
    function connectResidents(a, b) {
      let index = ties().findIndex((t) => Array.isArray(t) && ((t[0] === a && t[1] === b) || (t[0] === b && t[1] === a)));
      if (index < 0) { ties().push([a, b, 0.5]); index = ties().length - 1; touch("ties"); toast(`Tie created between ${personLabel(a)} and ${personLabel(b)}.`, "good"); }
      state.sel.tie = index;
      view.reheat(0.3);
      refreshSelection();
      openEditor();
    }
    function tieEditor(index) {
      const t = ties()[index];
      const a = residents().find((r) => r.id === t[0]) || { id: t[0], name: t[0] };
      const b = residents().find((r) => r.id === t[1]) || { id: t[1], name: t[1] };
      const avatar = (r) => h("span", { class: "avatar", style: { background: factionColor(r.faction), color: factionColor(r.faction) } }, initials(r.name));
      const selectPerson = (id) => { state.sel.resident = residents().findIndex((r) => r.id === id); state.sel.tie = null; refreshSelection(); openEditor(); };
      return [
        h("div", { class: "panel-title" }, avatar(a), h("span", { class: "muted" }, "⟷"), avatar(b), h("h3", null, "Social tie")),
        h("p", { class: "small" }, `${a.name} and ${b.name}`),
        F.range({ label: "Tie strength", value: Number(t[2]) || 0, path: `ties[${index}][2]`, min: 0, max: 1, step: 0.05,
          hint: "≥ 0.3 counts as a close tie (support, contact, gossip). Ties below 0.15 do not hold groups together for cohesion.",
          onChange: (v) => { t[2] = v ?? 0; touch("ties"); view.reheat(0.25); } }),
        h("div", { class: "callout info", style: { marginTop: "12px" } }, "Ties evolve during the run: contact strengthens them, conflicts and neglect weaken them. Strong ties pull residents closer on the map."),
        h("div", { class: "row", style: { marginTop: "14px" } },
          h("button", { type: "button", class: "small", onclick: () => selectPerson(a.id) }, `Edit ${String(a.name).split(" ")[0]}`),
          h("button", { type: "button", class: "small", onclick: () => selectPerson(b.id) }, `Edit ${String(b.name).split(" ")[0]}`),
          h("span", { class: "spacer" }),
          h("button", { type: "button", class: "small danger", onclick: () => { ties().splice(index, 1); state.sel.tie = null; touch("ties"); view.reheat(0.4); refreshSelection(); } }, "Remove tie")),
      ];
    }
    function residentEditor(r, index) {
      const color = factionColor(r.faction);
      const avatar = h("span", { class: "avatar", style: { background: color, color } }, initials(r.name));
      const title = h("h3", null, r.name || r.id);
      const parts = residentForm(r, `residents[${index}]`, {
        local: true,
        onIdentity: () => { title.textContent = r.name || r.id; avatar.textContent = initials(r.name); avatar.style.background = factionColor(r.faction); avatar.style.color = factionColor(r.faction); drawRoster(); view.kick(); },
        onStructure: () => { view.sync(); drawRoster(); drawLegend(); },
      });
      const tieList = residentTies(r);
      const remove = h("button", { type: "button", class: "danger small", onclick: () => removeResident(index) }, "Delete resident");
      return [h("div", { class: "panel-title" }, avatar, h("div", null, title, h("p", { class: "tiny muted mono" }, r.id))), ...parts, tieList,
        h("div", { class: "row", style: { marginTop: "16px" } }, h("span", { class: "spacer" }), remove)];
    }
    function residentTies(r) {
      const list = h("div");
      const draw = () => {
        const rows = [];
        ties().forEach((t, i) => {
          if (!Array.isArray(t) || (t[0] !== r.id && t[1] !== r.id)) return;
          const other = t[0] === r.id ? t[1] : t[0];
          const person = residents().find((x) => x.id === other);
          const slider = F.range({ label: person ? person.name : other, value: Number(t[2]) || 0, min: 0, max: 1, step: 0.05, path: `ties[${i}][2]`,
            onChange: (v) => { t[2] = v ?? 0; touch("ties"); view.reheat(0.2); } });
          rows.push(h("div", { class: "row", style: { alignItems: "flex-end", flexWrap: "nowrap" } }, h("div", { style: { flex: 1 } }, slider),
            h("button", { type: "button", class: "icon ghost", title: "Remove tie", "aria-label": `Remove tie with ${person ? person.name : other}`,
              onclick: () => { ties().splice(i, 1); touch("ties"); view.reheat(0.3); draw(); drawLegend(); } }, "✕")));
        });
        fill(list, ...(rows.length ? rows : [h("p", { class: "small muted" }, COARSE ? "No ties: this resident starts isolated. Long-press them on the map, then pick another resident to connect."
          : "No ties: this resident starts isolated. Shift-click another resident on the map to connect.")]));
      };
      draw();
      return foldCard("Social ties", `${ties().filter((t) => Array.isArray(t) && (t[0] === r.id || t[1] === r.id)).length}`, true, list);
    }
    async function removeResident(index) {
      const r = residents()[index];
      const refs = countResidentRefs(r.id);
      const ok = await confirmDialog({ title: `Delete ${r.name || r.id}?`, ok: "Delete", danger: true,
        text: `Their ties and rumour sources are removed too.${refs ? ` ${refs} story or fact reference(s) still mention '${r.id}' and will need fixing.` : ""}` });
      if (!ok) return;
      residents().splice(index, 1);
      state.raw.ties = ties().filter((t) => !Array.isArray(t) || (t[0] !== r.id && t[1] !== r.id));
      for (const f of facts()) if (Array.isArray(f.sources)) f.sources = f.sources.filter((s2) => !s2 || s2.who !== r.id);
      state.sel.resident = Math.max(0, index - 1);
      state.sel.tie = null;
      view.sync();
      touch("residents");
      refreshSelection();
      drawLegend();
      sheet.close();
    }
    function drawFactions() {
      const list = factions();
      const rows = list.map((f, i) => {
        const members = residents().filter((r) => r.faction === f.id).length + newcomers().filter((n) => n.spec.faction === f.id).length;
        const idInput = h("input", { type: "text", value: f.id, "data-path": `factions[${i}].id`, class: "mono", "aria-label": "Faction id" });
        idInput.addEventListener("change", () => {
          const next = idInput.value.trim();
          if (next === f.id) return;
          if (!IDENT.test(next) || list.some((x) => x !== f && x.id === next)) { idInput.classList.add("invalid"); toast("Faction ids must be unique identifiers.", "bad"); idInput.value = f.id; return; }
          renameFaction(f.id, next);
          f.id = next;
          touch("factions");
          drawFactions();
        });
        return h("div", { class: "faction-row faction-edit" },
          F.color({ label: `Colour of ${f.name}`, value: f.color, path: `factions[${i}].color`, onChange: (v) => { f.color = v; touch("factions"); drawLegend(); drawRoster(); view.kick(); } }),
          idInput,
          (() => { const inp = h("input", { type: "text", value: f.name || "", maxlength: 60, "data-path": `factions[${i}].name`, "aria-label": "Faction name" });
            inp.addEventListener("input", () => { f.name = inp.value; touch("factions"); drawLegend(); view.kick(); }); return inp; })(),
          h("span", { class: "tiny muted nowrap" }, `${members} member${members === 1 ? "" : "s"}`),
          h("button", { type: "button", class: "icon ghost", "aria-label": `Remove faction ${f.name}`, title: "Remove faction",
            onclick: async () => {
              if (members && !(await confirmDialog({ title: `Remove ${f.name}?`, text: `${members} resident(s) will have no faction.`, ok: "Remove", danger: true }))) return;
              list.splice(i, 1);
              for (const r of residents()) if (r.faction === f.id) delete r.faction;
              for (const n of newcomers()) if (n.spec.faction === f.id) delete n.spec.faction;
              touch("factions");
              drawFactions(); drawLegend(); drawRoster(); drawPanel(); view.kick();
            } }, "✕"));
      });
      const palette = ["#4aa3df", "#8bc34a", "#c792ea", "#ff8a65", "#e2c21c", "#4dd0a8", "#f06292", "#90a4ae", "#ffb74d", "#7986cb", "#a1887f", "#81c784"];
      const add = h("button", { type: "button", class: "small", disabled: list.length >= 12, onclick: () => {
        list.push({ id: uniqueId("faction-", new Set(list.map((x) => x.id))), name: "New faction", color: palette[list.length % palette.length] });
        touch("factions");
        drawFactions(); drawLegend();
      } }, "+ Add faction");
      fill(factionsBox, h("h3", null, h("span", { class: "grow" }, "Factions"), add),
        rows.length ? h("div", null, rows) : h("p", { class: "small muted" }, "No factions. Factions colour the map and can be targeted by story effects (faction:id)."),
        h("p", { class: "tiny muted" }, "At most 12 factions. Story effects can target a faction as faction:id."));
    }
    function renameFaction(from, to) {
      for (const r of residents()) if (r.faction === from) r.faction = to;
      for (const n of newcomers()) if (n.spec.faction === from) n.spec.faction = to;
      for (const node of storyNodes()) for (const e of node.effects || []) if (e && e.who === `faction:${from}`) e.who = `faction:${to}`;
    }

    if (state.sel.tie != null && !ties()[state.sel.tie]) state.sel.tie = null;
    drawLegend();
    drawRoster();
    drawPanel();
    drawFactions();
    subscribe("ties", debounce(drawLegend, 200));
    subscribe("residents", debounce(drawLegend, 200));
    return () => { sheet.destroy(); view.destroy(); state.network = null; };
  };

  /**
   * Form for one resident spec (initial resident or a story newcomer). `base` is the
   * validator path prefix, e.g. "residents[3]" or "story.riverfolk.arrive[0]".
   */
  function residentForm(r, base, { local = false, onIdentity = () => {}, onStructure = () => {} } = {}) {
    const lib = archetypesEff();
    const idInput = h("input", { type: "text", value: r.id || "", class: "mono", "data-path": `${base}.id`, maxlength: 64 });
    idInput.addEventListener("change", () => {
      const next = idInput.value.trim();
      if (next === r.id) return;
      const taken = allIds();
      taken.delete(r.id);
      if (!IDENT.test(next) || taken.has(next)) { idInput.classList.add("invalid"); toast(IDENT.test(next) ? `'${next}' is already used.` : "Ids are lowercase letters, digits, '-' or '_'.", "bad"); idInput.value = r.id; return; }
      idInput.classList.remove("invalid");
      const from = r.id;
      r.id = next;
      if (local) renameResidentRefs(from, next);
      else renameResidentRefs(from, next);
      touch("residents");
      onIdentity();
    });
    const roleOptions = Object.entries(cat().roles).map(([key, role]) => [key, `${role.label}${role.skill ? ` · ${role.skill}` : ""}`]);
    const factionOptions = [[undefined, "No faction"]].concat(factions().map((f) => [f.id, f.name || f.id]));
    const identity = h("div", { class: "form-grid two-col" },
      field("Id", idInput, local ? "Renaming updates ties, sources and story references." : null),
      F.text({ label: "Name", value: r.name || "", path: `${base}.name`, max: 60, onChange: (v) => { r.name = v; touch("residents"); onIdentity(); } }),
      F.number({ label: "Age", value: r.age ?? 35, path: `${base}.age`, min: 12, max: 110, integer: true, onChange: (v) => { if (v === undefined) delete r.age; else r.age = v; touch("residents"); } }),
      F.select({ label: "Role", value: r.role || "farmer", options: roleOptions, path: `${base}.role`,
        onChange: (v) => { r.role = v; touch("residents"); onIdentity(); drawSkills(); } }),
      h("div", { class: "span-all" }, F.select({ label: "Faction", value: r.faction ?? undefined, options: factionOptions, path: `${base}.faction`,
        onChange: (v) => { if (v === undefined) delete r.faction; else r.faction = v; touch("residents"); onIdentity(); onStructure(); } })),
      h("div", { class: "span-all" }, F.area({ label: "Biography", value: r.bio || "", path: `${base}.bio`, max: 300, rows: 2,
        onChange: (v) => { r.bio = v; touch("residents"); } })));

    // archetype
    const basis = h("p", { class: "basis" });
    const picker = h("div", { class: "archetype-picker", role: "radiogroup", "aria-label": "Behaviour archetype", "data-path": `${base}.archetype` });
    const drawPicker = () => {
      const current = r.archetype || "conditional_cooperator";
      fill(picker, ...Object.entries(lib).map(([key, a]) => h("button", { type: "button", class: "arch-option", role: "radio",
        "aria-checked": String(key === current), title: a.basis || "",
        onclick: () => { r.archetype = key; touch("residents"); drawPicker(); drawTraits(); onIdentity(); } },
        h("b", null, a.label || key), h("small", null, truncate(a.summary || "", 90)))));
      const a = lib[current];
      basis.textContent = a ? `Research basis: ${a.basis || "—"}` : `Unknown archetype '${current}'`;
    };
    drawPicker();

    // traits
    const traitBox = h("div", { class: "trait-grid" });
    const overrideHint = h("span", { class: "hint" });
    const drawTraits = () => {
      const base2 = archetypeTraits(r.archetype || "conditional_cooperator", lib);
      const over = isObject(r.traits) ? r.traits : {};
      overrideHint.textContent = Object.keys(over).length ? `${Object.keys(over).length} personal override(s)` : "from the archetype";
      fill(traitBox, ...Object.keys(cat().traits).map((key) => traitSlider(key, key in over ? over[key] : base2[key], base2[key], key in over, `${base}.traits.${key}`,
        (v) => { if (!isObject(r.traits)) r.traits = {}; r.traits[key] = v; touch("residents"); overrideHint.textContent = `${Object.keys(r.traits).length} personal override(s)`; },
        () => { if (isObject(r.traits)) { delete r.traits[key]; if (!Object.keys(r.traits).length) delete r.traits; } touch("residents"); drawTraits(); })));
    };
    drawTraits();

    // starting state
    const st = residentState(r);
    const setState = (key) => (v) => { if (!isObject(r.state)) r.state = {}; if (v === undefined) delete r.state[key]; else r.state[key] = v; touch("residents"); };
    const stateGrid = h("div", { class: "form-grid two-col" },
      F.number({ label: "Coins", value: st.coins, min: 0, max: 1e6, path: `${base}.state.coins`, onChange: (v) => { setState("coins")(v); onIdentity(); } }),
      F.number({ label: "Food (rations)", value: st.food, min: 0, max: 1e6, path: `${base}.state.food`, onChange: setState("food") }),
      h("div", { class: "span-all" }, F.range({ label: "Health", value: st.health, min: 0, max: 100, step: 1, path: `${base}.state.health`, onChange: (v) => { setState("health")(v); onIdentity(); } })),
      F.range({ label: "Stress", value: st.stress, path: `${base}.state.stress`, onChange: setState("stress") }),
      F.range({ label: "Loneliness", value: st.loneliness, path: `${base}.state.loneliness`, onChange: setState("loneliness") }),
      F.range({ label: "Grievance", value: st.grievance, path: `${base}.state.grievance`, onChange: setState("grievance") }),
      F.range({ label: "Trust in the steward", value: st.trust, path: `${base}.state.trust`, onChange: setState("trust") }));

    // concern
    const concernBox = h("div");
    const drawConcern = () => {
      const has = isObject(r.concern);
      const parts = [F.toggle({ label: "Carries a hidden concern", checked: has, path: `${base}.concern`,
        onChange: (on) => { if (on) r.concern = { kind: "loneliness", intensity: 0.5, cause: "" }; else delete r.concern; touch("residents"); drawConcern(); } })];
      if (has) {
        parts.push(h("div", { class: "form-grid two-col", style: { marginTop: "10px" } },
          h("div", { class: "span-all" }, F.select({ label: "Kind", value: r.concern.kind, path: `${base}.concern.kind`,
            options: Object.entries(cat().concerns).map(([k, text]) => [k, `${titleCase(k)} — ${text}`]),
            onChange: (v) => { r.concern.kind = v; touch("residents"); } })),
          h("div", { class: "span-all" }, F.range({ label: "Intensity", value: r.concern.intensity ?? 0.5, path: `${base}.concern.intensity`,
            onChange: (v) => { r.concern.intensity = v; touch("residents"); } })),
          h("div", { class: "span-all" }, F.text({ label: "Cause (revealed only when the resident confides)", value: r.concern.cause || "", max: 200,
            path: `${base}.concern.cause`, onChange: (v) => { r.concern.cause = v; touch("residents"); } }))));
      }
      fill(concernBox, ...parts, h("p", { class: "hint", style: { marginTop: "6px" } },
        "The steward never sees concerns directly: residents hint at them depending on pride and trust. The right approach (listen, reassure, support…) relieves them (Cutrona & Russell 1990)."));
    };
    drawConcern();

    // skills
    const skillsBox = h("div");
    const drawSkills = () => {
      const eff = residentSkills(r);
      const own = roleSkill(r.role || "farmer");
      fill(skillsBox, ...cat().skills.map((skill) => h("div", { class: "skill-row" },
        h("span", null, titleCase(skill), own === skill ? h("small", null, "role skill · default 2") : null),
        F.pips({ label: `${skill} level`, value: eff[skill] || 0, path: `${base}.skills.${skill}`,
          onChange: (v) => { if (!isObject(r.skills)) r.skills = {}; r.skills[skill] = v; touch("residents"); } }))),
      h("p", { class: "hint" }, "0 none · 1 apprentice · 2 master · 3 expert. Skills held by a single person are lost with them (Henrich 2004)."));
    };
    drawSkills();

    // flags, secrets, decline
    const flags = Array.isArray(r.flags) ? r.flags : [];
    const secrets = Array.isArray(r.secrets) ? r.secrets : [];
    const keepList = (key, list) => () => { if (list.length) r[key] = list; else delete r[key]; touch("residents"); };
    const declineBox = h("div");
    const drawDecline = () => {
      const has = isObject(r.decline);
      const parts = [F.toggle({ label: "Health declines over time", checked: has, path: `${base}.decline`,
        onChange: (on) => { if (on) r.decline = { start: 10, rate: 1.0 }; else delete r.decline; touch("residents"); drawDecline(); } })];
      if (has) parts.push(h("div", { class: "form-grid two-col", style: { marginTop: "10px" } },
        F.number({ label: "From week", value: r.decline.start ?? 1, min: 1, max: 240, integer: true, path: `${base}.decline.start`, onChange: (v) => { r.decline.start = v ?? 1; touch("residents"); } }),
        F.range({ label: "Health lost / week", value: r.decline.rate ?? 1, min: 0, max: 10, step: 0.1, path: `${base}.decline.rate`, onChange: (v) => { r.decline.rate = v ?? 1; touch("residents"); } })));
      fill(declineBox, ...parts);
    };
    drawDecline();

    return [
      identity,
      foldCard("Behaviour archetype", (lib[r.archetype || "conditional_cooperator"] || {}).label || r.archetype, true, picker, basis),
      foldCard("Personality & behaviour traits", null, false, overrideHint, traitBox),
      foldCard("Starting state", null, false, stateGrid),
      foldCard("Hidden concern", isObject(r.concern) ? r.concern.kind : "none", isObject(r.concern), concernBox),
      foldCard("Skills", null, false, skillsBox),
      foldCard("Public flags & secrets", `${flags.length + secrets.length}`, false,
        F.tags({ label: "Public flags", values: flags, max: 80, path: `${base}.flags`, hint: "Facts anyone can see (e.g. “lives alone”).", onChange: keepList("flags", flags) }),
        h("div", { style: { height: "10px" } }),
        F.tags({ label: "Secrets", values: secrets, max: 160, path: `${base}.secrets`, hint: "Hidden truths the steward may uncover.", onChange: keepList("secrets", secrets) })),
      foldCard("Health decline", isObject(r.decline) ? `from week ${r.decline.start}` : "none", isObject(r.decline), declineBox),
    ];
  }
  /** One trait slider with explanation, reference marker and reset. */
  function traitSlider(key, value, reference, overridden, path, onChange, onReset) {
    const help = TRAIT_HELP[key] || (cat().traits[key] || {}).help || "";
    const name = TRAIT_NAMES[key] || key;
    const dot = h("span", { class: "override-dot", hidden: !overridden, title: "Personal override" });
    const reset = h("button", { type: "button", class: "reset ghost", hidden: !overridden, title: `Back to ${fmt.num(reference)}`, onclick: onReset }, "↺");
    const el = F.range({ label: name, value: Number(value), path, min: 0, max: 1, step: 0.01, title: help,
      extra: h("span", { class: "row", style: { gap: "4px" } }, dot, reset),
      onChange: (v) => { dot.hidden = false; reset.hidden = false; onChange(v ?? 0); } });
    el.classList.add("trait");
    put(el, h("span", { class: "help" }, help.includes(":") ? help.slice(help.indexOf(":") + 1).trim() : help));
    const label = el.querySelector(".label > span");
    if (label) fill(label, h("abbr", { title: help }, name));
    return el;
  }

  // =====================================================================================
  // 11. Section: Behaviour archetypes
  // =====================================================================================
  const TRAIT_SHORT = { O: "O", C: "C", E: "E", A: "A", N: "N", prosocial: "pro", honesty: "hon", credulity: "cred", threshold: "thr",
    pride: "pride", reciprocity: "recip", conformity: "conf", greed: "greed", cond_coop: "c-coop", inequity: "ineq", resilience: "resil", gatekeeper: "gate" };
  /** Radar chart of a trait profile against the population defaults. */
  function radar(traits, size = 240) {
    const keys = Object.keys(cat().traits);
    const defaults = traitDefaults();
    const c = size / 2;
    const R = size / 2 - 30;
    const point = (i, v) => {
      const a = (i / keys.length) * Math.PI * 2 - Math.PI / 2;
      return [c + Math.cos(a) * R * v, c + Math.sin(a) * R * v];
    };
    const poly = (values) => keys.map((k, i) => point(i, clamp(Number(values[k]) || 0, 0, 1)).map((n) => n.toFixed(1)).join(",")).join(" ");
    const svg = s("svg", { class: "radar", viewBox: `0 0 ${size} ${size}`, role: "img", "aria-label": "Trait profile" });
    for (const ring of [0.25, 0.5, 0.75, 1]) put(svg, s("polygon", { class: "ring", points: keys.map((k, i) => point(i, ring).join(",")).join(" ") }));
    keys.forEach((k, i) => {
      const [x, y] = point(i, 1);
      put(svg, s("line", { class: "axis", x1: c, y1: c, x2: x, y2: y }));
      const [lx, ly] = point(i, 1.17);
      const hot = Math.abs((Number(traits[k]) || 0) - defaults[k]) >= 0.2;
      put(svg, s("text", { x: lx, y: ly, "text-anchor": "middle", "dominant-baseline": "middle", class: hot ? "hot" : null },
        s("title", null, `${TRAIT_NAMES[k] || k}: ${fmt.num(Number(traits[k]))} (default ${fmt.num(defaults[k])})`), TRAIT_SHORT[k] || k));
    });
    put(svg, s("polygon", { class: "base", points: poly(defaults) }));
    put(svg, s("polygon", { class: "shape", points: poly(traits) }));
    return svg;
  }
  function archetypeUsage(key) {
    return people().filter((p) => {
      const spec = residents().find((r) => r.id === p.id) || (newcomers().find((n) => n.spec.id === p.id) || {}).spec;
      return spec && (spec.archetype || "conditional_cooperator") === key;
    });
  }
  renderers.archetypes = (host) => {
    const addButton = h("button", { type: "button", class: "primary", onclick: addArchetype }, "+ New archetype");
    put(host, sectionHead("Behaviour", "Archetypes",
      "Archetypes are behaviour profiles grounded in social science. Residents inherit their archetype's traits and may override any of them. Editing an archetype here changes every resident who uses it.", addButton));
    const grid = h("div", { class: "grid auto" });
    const panel = h("div", { class: "panel-body" });
    const aside = h("aside", { class: "card editor-panel", "aria-label": "Archetype editor" }, panel);
    put(host, h("div", { class: "people-layout" }, grid, aside));
    const sheet = makeSheet(aside, { title: "Archetype" });
    const openEditor = () => { const a = archetypesEff()[state.sel.archetype]; sheet.open(a ? a.label || state.sel.archetype : "Archetype"); };

    function drawGrid() {
      const lib = archetypesEff();
      fill(grid, ...Object.entries(lib).map(([key, a]) => {
        const users = archetypeUsage(key).length;
        const el = h("button", { type: "button", class: `card arch-card${key === state.sel.archetype ? " selected" : ""}`, "aria-pressed": String(key === state.sel.archetype),
          onclick: () => { state.sel.archetype = key; drawGrid(); drawPanel(); openEditor(); } },
          radar(archetypeTraits(key, lib), 220),
          h("div", { class: "row between" }, h("h4", null, a.label || key),
            h("span", { class: "chips" }, a.builtin ? null : h("span", { class: "badge gold" }, "custom"), a.builtin && a.modified ? h("span", { class: "badge watch" }, "modified") : null,
              h("span", { class: "badge" }, `${users} ${users === 1 ? "person" : "people"}`))),
          h("p", { class: "summary" }, a.summary || "No summary yet."),
          a.basis ? h("p", { class: "basis" }, a.basis) : null);
        return el;
      }));
    }
    function spec(key) {
      if (!isObject(state.raw.archetypes)) state.raw.archetypes = {};
      if (!isObject(state.raw.archetypes[key])) state.raw.archetypes[key] = {};
      return state.raw.archetypes[key];
    }
    const redrawGrid = debounce(drawGrid, 120);
    function drawPanel() {
      const lib = archetypesEff();
      const key = state.sel.archetype;
      const a = lib[key];
      fill(panel);
      if (!a) return;
      const base = `archetypes.${key}`;
      const catalogTraits = Object.assign(traitDefaults(), (cat().archetypes[key] || {}).traits || {});
      const effective = archetypeTraits(key, lib);
      const overrides = getIn(state.raw, ["archetypes", key, "traits"], {});
      const users = archetypeUsage(key);
      const traitBox = h("div", { class: "trait-grid" });
      for (const t of Object.keys(cat().traits)) {
        put(traitBox, traitSlider(t, effective[t], catalogTraits[t], t in overrides && (!a.builtin || overrides[t] !== (cat().archetypes[key].traits || {})[t]), `${base}.traits.${t}`,
          (v) => { const sp = spec(key); if (!isObject(sp.traits)) sp.traits = {}; sp.traits[t] = v; touch("archetypes"); redrawGrid(); },
          () => { const sp = spec(key); if (isObject(sp.traits)) delete sp.traits[t]; cleanupSpec(key); touch("archetypes"); drawPanel(); drawGrid(); }));
      }
      const text = (field2, label, max, rows) => F.area({ label, value: a[field2] || "", path: `${base}.${field2}`, max, rows,
        onChange: (v) => { spec(key)[field2] = v; touch("archetypes"); redrawGrid(); } });
      put(panel, 
        h("div", { class: "panel-title" }, h("div", null, h("h3", null, a.label || key), h("p", { class: "tiny muted mono" }, key))),
        h("div", { class: "form-grid", style: { gridTemplateColumns: "1fr" } },
          F.text({ label: "Label", value: a.label || "", path: `${base}.label`, max: 300, onChange: (v) => { spec(key).label = v; touch("archetypes"); redrawGrid(); } }),
          text("summary", "Summary", 300, 2),
          text("basis", "Research basis", 300, 2)),
        foldCard("Trait profile", a.builtin ? "markers = catalog values" : null, true, traitBox),
        foldCard("Residents using it", String(users.length), users.length > 0,
          users.length ? h("div", { class: "chips" }, users.map((p) => h("span", { class: "chip" }, h("i", { class: "swatch", style: { background: factionColor(p.faction), color: factionColor(p.faction) } }), p.name || p.id)))
            : h("p", { class: "small muted" }, "Nobody uses this archetype yet.")),
        h("div", { class: "row", style: { marginTop: "16px" } }, h("span", { class: "spacer" }),
          a.builtin && a.modified ? h("button", { type: "button", class: "small", onclick: () => { delete state.raw.archetypes[key]; cleanupArchetypes(); touch("archetypes"); drawGrid(); drawPanel(); } }, "Reset to catalog") : null,
          !a.builtin ? h("button", { type: "button", class: "small danger", disabled: users.length > 0, title: users.length ? "Reassign its residents first" : "Delete",
            onclick: () => { delete state.raw.archetypes[key]; cleanupArchetypes(); state.sel.archetype = Object.keys(archetypesEff())[0]; touch("archetypes"); drawGrid(); drawPanel(); sheet.close(); } }, "Delete archetype") : null));
    }
    function cleanupSpec(key) {
      const sp = getIn(state.raw, ["archetypes", key]);
      if (isObject(sp) && isObject(sp.traits) && !Object.keys(sp.traits).length) delete sp.traits;
      if (isObject(sp) && !Object.keys(sp).length && cat().archetypes[key]) delete state.raw.archetypes[key];
      cleanupArchetypes();
    }
    function cleanupArchetypes() { if (isObject(state.raw.archetypes) && !Object.keys(state.raw.archetypes).length) delete state.raw.archetypes; }
    async function addArchetype() {
      const taken = new Set(Object.keys(archetypesEff()));
      const id = await promptDialog({ title: "New archetype", text: "A custom behaviour profile. Start from the population defaults and shape its traits.", label: "Archetype id",
        value: uniqueId("custom-", taken), check: identCheck(taken) });
      if (!id) return;
      spec(id).label = titleCase(id);
      spec(id).summary = "";
      spec(id).basis = "";
      spec(id).traits = {};
      state.sel.archetype = id;
      touch("archetypes");
      drawGrid();
      drawPanel();
      openEditor();
    }
    if (!archetypesEff()[state.sel.archetype]) state.sel.archetype = Object.keys(archetypesEff())[0];
    drawGrid();
    drawPanel();
    return () => sheet.destroy();
  };

  // =====================================================================================
  // 12. Section: World (stores, production, commons, policies, conventions, illness)
  // =====================================================================================
  function commonsGrowth(stock, c) {
    const K = c.capacity;
    if (stock < c.critical * K) return 0;
    let g = c.growth * stock * (1 - stock / K);
    if (stock < 0.3 * K) g *= clamp((stock - 0.1 * K) / (0.2 * K), 0, 1);
    return Math.max(0, g);
  }
  function projectCommons(c, effort, weeks) {
    const out = [];
    let S = c.stock;
    let collapsedAt = null;
    for (let w = 1; w <= weeks; w++) {
      const efficiency = Math.min(1, S / (c.efficiency_floor * c.capacity));
      S = Math.max(0, S - effort * efficiency);
      if (S < c.critical * c.capacity) { collapsedAt = w; out.push(S / c.capacity); break; }
      S = Math.min(c.capacity, S + commonsGrowth(S, c));
      out.push(S / c.capacity);
    }
    return { series: out, collapsedAt };
  }
  function commonsChart() {
    const box = h("div");
    const effortSlider = h("input", { type: "range", min: 0, max: 100, step: 1, value: 0, "aria-label": "Weekly catch effort" });
    const effortValue = h("span", { class: "value" });
    const verdict = h("p", { class: "small" });
    let effort = null;
    let drawnWidth = 0;
    function draw() {
      const c = worldEff().commons;
      if (!(c.capacity > 0)) { fill(box, h("p", { class: "muted" }, "Set a capacity to see the regeneration curve.")); return; }
      // Drawn at the box's own width so that labels keep their real size on a phone.
      drawnWidth = box.clientWidth;
      const W = Math.round(clamp(drawnWidth || 520, 300, 760)), H = 190, P = { l: 40, r: 14, t: 22, b: 28 };
      const msy = (c.growth * c.capacity) / 4;
      const maxG = Math.max(1, msy * 1.15);
      const x = (share) => P.l + share * (W - P.l - P.r);
      const y = (g) => H - P.b - (g / maxG) * (H - P.t - P.b);
      const pts = [];
      for (let i = 0; i <= 200; i++) { const sh = i / 200; pts.push(`${x(sh).toFixed(1)},${y(commonsGrowth(sh * c.capacity, c)).toFixed(1)}`); }
      const svg = s("svg", { class: "chart", viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Logistic regeneration of the commons" },
        s("defs", null, s("linearGradient", { id: "cg-fill", x1: 0, y1: 0, x2: 0, y2: 1 }, s("stop", { offset: 0, "stop-color": "#4aa3df", "stop-opacity": 0.45 }), s("stop", { offset: 1, "stop-color": "#4aa3df", "stop-opacity": 0 })),
          s("pattern", { id: "cg-hatch", width: 6, height: 6, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" }, s("line", { x1: 0, y1: 0, x2: 0, y2: 6, stroke: "rgba(239,106,91,0.35)", "stroke-width": 2 }))),
        s("rect", { x: x(0.1), y: P.t, width: x(0.3) - x(0.1), height: H - P.t - P.b, fill: "url(#cg-hatch)" }, s("title", null, "Depensation: below 30% breeding fails, reaching zero at 10% (Allee effect)")),
        s("rect", { x: x(0), y: P.t, width: x(c.critical) - x(0), height: H - P.t - P.b, fill: "rgba(239,83,80,0.12)" }),
        [0, 0.25, 0.5, 0.75, 1].map((t) => s("line", { class: "grid-line", x1: x(t), x2: x(t), y1: P.t, y2: H - P.b })),
        s("line", { class: "axis-line", x1: P.l, x2: W - P.r, y1: H - P.b, y2: H - P.b }),
        s("polygon", { points: `${x(0)},${y(0)} ${pts.join(" ")} ${x(1)},${y(0)}`, fill: "url(#cg-fill)" }),
        s("polyline", { points: pts.join(" "), fill: "none", stroke: "#4aa3df", "stroke-width": 2.2, "stroke-linejoin": "round" }),
        s("line", { x1: x(0.5), x2: x(0.5), y1: P.t, y2: H - P.b, stroke: "#7cc47f", "stroke-dasharray": "4 4" }),
        s("text", { x: x(0.5) + 5, y: H - P.b - 8, fill: "#7cc47f" }, `MSY ${fmt.num(msy, 1)}/wk`),
        s("line", { x1: x(c.critical), x2: x(c.critical), y1: P.t, y2: H - P.b, stroke: "#ef5350", "stroke-width": 1.5 }),
        s("text", { x: x(c.critical), y: P.t - 4, "text-anchor": "middle", fill: "#ef8a80" }, fmt.pct(c.critical)),
        s("line", { x1: x(c.efficiency_floor), x2: x(c.efficiency_floor), y1: P.t, y2: H - P.b, stroke: "#e0b030", "stroke-dasharray": "2 3" }),
        s("text", { x: x(c.efficiency_floor), y: P.t - 4, "text-anchor": "middle", fill: "#e0b030" }, fmt.pct(c.efficiency_floor)),
        s("circle", { cx: x(c.stock / c.capacity), cy: y(commonsGrowth(c.stock, c)), r: 5, fill: "#e2c21c", stroke: "#121212", "stroke-width": 2 }, s("title", null, "Starting stock")),
        s("text", { x: x(c.stock / c.capacity), y: y(commonsGrowth(c.stock, c)) + 18, "text-anchor": "middle", fill: "#e2c21c" }, "start"),
        [0, 0.5, 1].map((t) => s("text", { x: x(t), y: H - 10, "text-anchor": "middle" }, fmt.pct(t))),
        s("text", { x: 4, y: P.t + 4 }, `${fmt.num(maxG, 0)}`), s("text", { x: 4, y: H - P.b }, "0"));
      // what-if projection
      if (effort == null) effort = Math.round(msy * 0.9);
      effortSlider.max = String(Math.max(10, Math.round(msy * 2.2)));
      effortSlider.value = String(effort);
      effortSlider.style.setProperty("--fill", `${(effort / Number(effortSlider.max)) * 100}%`);
      effortValue.textContent = `${effort} units/week`;
      const weeks = ticks();
      const proj = projectCommons(c, effort, weeks);
      const PW = W, PH = 90;
      const px = (w) => 40 + (w / weeks) * (PW - 54);
      const py = (share) => PH - 16 - share * (PH - 26);
      const line = [`${px(0)},${py(c.stock / c.capacity)}`].concat(proj.series.map((v, i) => `${px(i + 1).toFixed(1)},${py(v).toFixed(1)}`)).join(" ");
      const projSvg = s("svg", { class: "chart", viewBox: `0 0 ${PW} ${PH}`, role: "img", "aria-label": "Projected stock under a constant effort" },
        s("line", { class: "grid-line", x1: 40, x2: PW - 14, y1: py(0.5), y2: py(0.5) }),
        s("line", { x1: 40, x2: PW - 14, y1: py(c.critical), y2: py(c.critical), stroke: "#ef5350", "stroke-dasharray": "3 3" }),
        s("polyline", { points: line, fill: "none", stroke: proj.collapsedAt ? "#ef6a5b" : "#4dd0a8", "stroke-width": 2 }),
        s("text", { x: 4, y: py(1) + 4 }, "100%"), s("text", { x: 4, y: py(0.5) + 4 }, "50%"),
        s("text", { x: px(weeks), y: PH - 2, "text-anchor": "end" }, `week ${weeks}`));
      const last = proj.series[proj.series.length - 1];
      fill(verdict, proj.collapsedAt
        ? h("span", { style: { color: "#ffb3aa" } }, `At this effort the lake collapses in week ${proj.collapsedAt}.`)
        : h("span", { style: { color: "#a8e6c9" } }, `At this effort the stock ends near ${fmt.pct(last)} of capacity.`),
      " Catch efficiency stays at 100% while the stock is above the floor (hyperstability), so fishers do not notice the decline until late.");
      fill(box, svg,
        h("div", { class: "legend" }, h("span", null, h("i", { style: { background: "#4aa3df" } }), "regeneration per week"), h("span", null, h("i", { style: { background: "#7cc47f" } }), "maximum sustainable yield"),
          h("span", null, h("i", { style: { background: "#ef5350" } }), "critical stock"), h("span", null, h("i", { style: { background: "#e0b030" } }), "hyperstability floor")),
        h("h4", null, "What if fishers keep a constant effort?"),
        h("div", { class: "field" }, h("span", { class: "label" }, h("span", null, "Weekly catch effort"), effortValue), effortSlider), projSvg, verdict);
    }
    effortSlider.addEventListener("input", () => { effort = Number(effortSlider.value); draw(); });
    draw();
    box.redraw = draw;
    const ro = typeof ResizeObserver === "function" ? new ResizeObserver(() => { if (Math.abs(box.clientWidth - drawnWidth) > 16) draw(); }) : null;
    if (ro) ro.observe(box);
    box.dispose = () => { if (ro) ro.disconnect(); };
    return box;
  }
  renderers.world = (host) => {
    const world = worldEff();
    const fishers = residents().filter((r) => r.role === "fisher").length;
    const chart = commonsChart();
    subscribe("commons", debounce(() => chart.redraw(), 60));
    const commonsNum = (label, key, opts = {}) => bindNumber(label, ["world", "commons", key], world.commons[key], Object.assign({ topic: "commons" }, opts));
    const commonsRange = (label, key, opts) => bindRange(label, ["world", "commons", key], world.commons[key], Object.assign({ topic: "commons" }, opts));
    const farm = Array.isArray(world.production.farm) ? world.production.farm.slice() : [1, 1, 1, 1];
    const farmBars = h("div");
    const drawFarm = () => {
      const max = Math.max(1, ...farm);
      fill(farmBars, s("svg", { class: "chart", viewBox: "0 0 240 70", style: "max-width:340px;margin:0 auto 8px", role: "img", "aria-label": "Seasonal farm output" },
        farm.map((v, i) => {
          const hgt = (v / max) * 46;
          const season = cat().seasons[i];
          return s("g", null, s("rect", { x: 10 + i * 58, y: 52 - hgt, width: 42, height: hgt, rx: 5, fill: SEASON_COLOR[season], opacity: 0.85 }),
            s("text", { x: 31 + i * 58, y: 66, "text-anchor": "middle" }, titleCase(season)), s("text", { x: 31 + i * 58, y: 48 - hgt, "text-anchor": "middle", fill: "#dedede" }, fmt.num(v, 1)));
        })));
    };
    drawFarm();
    const farmSliders = cat().seasons.map((season, i) => F.range({ label: titleCase(season), value: farm[i], min: 0, max: 12, step: 0.1, path: `world.production.farm[${i}]`,
      onChange: (v) => { farm[i] = v ?? 0; setIn(state.raw, ["world", "production", "farm"], farm.slice()); touch("world"); drawFarm(); } }));
    const policies = Object.entries(cat().policies).map(([key, spec]) => policyField(key, spec, world.policies[key], `world.policies.${key}`,
      (v) => { setIn(state.raw, ["world", "policies", key], v); touch("world"); }));
    put(host, 
      sectionHead("Environment", "World", "The material world the society lives in: stores, prices, seasonal production, the shared lake, default policies and illness."),
      h("div", { class: "grid cols-2" },
        h("div", { class: "card" }, h("h3", null, "The commons"),
          h("div", { class: "form-grid three-col" },
            bindText("Name", ["world", "commons", "name"], world.commons.name, { max: 60 }),
            commonsNum("Capacity (K)", "capacity", { min: 1 }),
            commonsNum("Starting stock", "stock", { min: 0, hint: `${fmt.pct(world.commons.stock / world.commons.capacity)} of capacity` }),
            commonsRange("Growth rate (r)", "growth", { min: 0, max: 0.6, step: 0.01, hint: "Logistic regeneration per week." }),
            commonsRange("Critical share", "critical", { min: 0, max: 0.5, step: 0.01, hint: "Below this the lake is dead (collapse rule)." }),
            commonsRange("Efficiency floor", "efficiency_floor", { min: 0.01, max: 1, step: 0.01, hint: "Below this share, catches fall with the stock." })),
          h("div", { class: "divider" }), chart,
          h("p", { class: "tiny muted", style: { marginTop: "8px" } }, `${fishers} fisher${fishers === 1 ? "" : "s"} at up to ${world.production.fish_max} units/week each. Gordon–Schaefer model; Ostrom 1990 on governing the commons.`)),
        h("div", { class: "stack" },
          card("Stores & prices", h("div", { class: "form-grid" },
            bindNumber("Treasury (coins)", ["world", "treasury"], world.treasury, { min: 0 }),
            bindNumber("Granary (food)", ["world", "granary"], world.granary, { min: 0 }),
            bindNumber("Medicine (doses)", ["world", "medicine"], world.medicine, { min: 0 }),
            bindNumber("Tools (sets)", ["world", "tools"], world.tools, { min: 0 })),
          h("h4", null, "Prices"),
          h("div", { class: "form-grid" }, Object.keys(world.prices).map((k) => bindNumber(titleCase(k), ["world", "prices", k], world.prices[k], { min: 0, max: 10000, step: 0.1 })))),
          card("Production", farmBars, h("div", { class: "form-grid two-col" }, farmSliders),
            h("h4", null, "Other work"),
            h("div", { class: "form-grid three-col" },
              bindNumber("Fish max / fisher", ["world", "production", "fish_max"], world.production.fish_max, { min: 0, max: 100, step: 0.5 }),
              ...["craft", "trade", "heal", "lore"].map((k) => bindNumber(titleCase(k), ["world", "production", k], world.production[k], { min: 0, max: 100, step: 0.1 }))),
            h("h4", null, "Consumption"),
            h("div", { class: "form-grid three-col" },
              bindNumber("Food need / week", ["world", "food_need"], world.food_need, { min: 0.1, max: 5, step: 0.1 }),
              bindNumber("Household spoilage", ["world", "household_spoilage"], world.household_spoilage, { min: 0, max: 0.5, step: 0.01 }),
              bindNumber("Granary spoilage", ["world", "granary_spoilage"], world.granary_spoilage, { min: 0, max: 0.5, step: 0.01 }))))),
      h("div", { class: "grid cols-2", style: { marginTop: "18px" } },
        card("Default policies", h("p", { class: "small muted", style: { marginBottom: "12px" } }, "The rules in force in week 1. The steward can propose or decree changes; story effects can force them."),
          h("div", { class: "stack", style: { gap: "14px" } }, policies)),
        h("div", { class: "stack" },
          conventionsCard(),
          card("Illness", h("div", { class: "form-grid three-col" },
            ...Object.entries(world.illness).map(([k, v]) => bindNumber(titleCase(k), ["world", "illness", k], v, { min: 0, max: 100, step: 0.005,
              hint: { base: "weekly risk", elder: "extra risk for elders", recovery: "weekly recovery chance", per_level: "healer bonus per skill level", medicine: "medicine cure bonus", damage: "health lost / sick week" }[k] })))),
          card("Council", bindRange("Majority needed", ["world", "council", "majority"], world.council.majority, { min: 0.2, max: 1, step: 0.01,
            hint: "Share of votes a proposal needs to pass the council." })))));
    return () => chart.dispose();
  };
  function policyField(key, spec, value, path, onChange) {
    const label = spec.label || titleCase(key);
    const hint = spec.help;
    if (spec.kind === "number") return F.range({ label, value: value ?? spec.default, min: spec.minimum, max: spec.maximum, step: 0.01, path, hint, onChange: (v) => onChange(v ?? spec.default) });
    if (spec.kind === "int") return F.range({ label, value: value ?? spec.default, min: spec.minimum, max: spec.maximum, step: 1, integer: true, path, hint, onChange: (v) => onChange(v ?? spec.default) });
    if (spec.kind === "bool") return F.toggle({ label, checked: Boolean(value), path, hint, onChange });
    if (spec.kind === "choice") return h("div", null, F.segmented({ label, value, path, options: spec.choices.map((c) => [c, titleCase(c)]), onChange }), h("p", { class: "hint" }, hint));
    // quota: null (no limit) or an integer
    const box = h("div");
    const draw = (v) => {
      const on = v !== null && v !== undefined;
      fill(box, F.toggle({ label: `${label}: ${on ? "limited" : "no limit"}`, checked: on, path, onChange: (checked) => { const nv = checked ? 6 : null; onChange(nv); draw(nv); } }),
        on ? F.range({ label: "Units per fisher per week", value: v, min: spec.minimum, max: spec.maximum, step: 1, integer: true, path, onChange: (n) => onChange(n ?? spec.minimum) }) : null,
        h("p", { class: "hint" }, hint));
    };
    draw(value);
    return box;
  }
  function conventionsCard() {
    const box = h("div");
    const draw = () => {
      const current = worldEff().conventions || {};
      const ensure = () => { if (!isObject(getIn(state.raw, ["world", "conventions"]))) setIn(state.raw, ["world", "conventions"], clone(current)); return state.raw.world.conventions; };
      const rows = Object.entries(current).map(([domain, variant], i) => {
        const d = h("input", { type: "text", value: domain, class: "mono", "aria-label": "Convention domain", "data-path": "world.conventions" });
        const v = h("input", { type: "text", value: variant, class: "mono", "aria-label": "Official variant", "data-path": `world.conventions.${domain}` });
        d.addEventListener("change", () => { const conv = ensure(); const next = d.value.trim(); if (!next || next === domain) return; const val = conv[domain]; delete conv[domain]; conv[next] = val; touch("world"); draw(); });
        v.addEventListener("input", () => { ensure()[domain] = v.value.trim(); touch("world"); });
        return h("div", { class: "faction-row", style: { gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr) auto" }, "data-index": i }, d, v,
          h("button", { type: "button", class: "icon ghost", "aria-label": `Remove convention ${domain}`, onclick: () => { delete ensure()[domain]; touch("world"); draw(); } }, "✕"));
      });
      fill(box, 
        rows.length ? h("div", null, h("div", { class: "faction-row tiny muted", style: { gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr) auto" } }, h("span", null, "Domain"), h("span", null, "Official variant"), h("span")), rows)
          : h("p", { class: "small muted" }, "No conventions."),
        h("button", { type: "button", class: "small", onclick: () => { const conv = ensure(); conv[uniqueId("domain-", new Set(Object.keys(conv)))] = "default"; touch("world"); draw(); } }, "+ Add convention"));
    };
    draw();
    return card("Conventions", h("p", { class: "small muted", style: { marginBottom: "10px" } },
      "Shared ways of doing things (e.g. how trade is negotiated). A story “convention shift” can silently make the environment require a new variant; residents adapt only if the steward notices (Young 1993; Centola 2018)."), box);
  }

  // =====================================================================================
  // 13. Section: Merchants
  // =====================================================================================
  const ITEMS = ["food", "medicine", "tools"];
  function merchantEff(m) { return deepMerge(Object.assign({ id: "", name: "" }, cat().defaults.merchant), m || {}); }
  function merchantWindows(m) {
    const eff = merchantEff(m);
    const stay = Number(eff.schedule && eff.schedule.stay) || 1;
    const starts = Array.isArray(eff.visits) ? eff.visits.filter(Number.isInteger)
      : (() => { const out = []; const every = Math.max(1, Number(eff.schedule.every) || 1); for (let w = Number(eff.schedule.start) || 1; w <= ticks(); w += every) out.push(w); return out; })();
    return starts.map((w) => [w, Math.min(ticks(), w + stay - 1)]);
  }
  function seasonBands(x, y, height) {
    const out = [];
    let week = 1;
    while (week <= ticks()) {
      const season = seasonAt(week);
      let end = week;
      while (end + 1 <= ticks() && seasonAt(end + 1) === season) end += 1;
      out.push(s("rect", { x: x(week), y, width: x(end + 1) - x(week), height, fill: SEASON_COLOR[season], opacity: 0.07 }),
        s("text", { x: x(week) + 4, y: y + 11, fill: SEASON_COLOR[season], opacity: 0.85 }, titleCase(season)));
      week = end + 1;
    }
    return out;
  }
  function merchantTimeline() {
    const list = merchants();
    const T = ticks();
    const L = 150, cell = 13, W = L + T * cell + 10, rowH = 26, top = 34;
    const H = top + Math.max(1, list.length) * rowH + 24;
    const x = (w) => L + (w - 1) * cell;
    const svg = s("svg", { class: "chart", viewBox: `0 0 ${W} ${H}`, style: `min-width:${Math.min(W, 900)}px`, role: "img", "aria-label": "Merchant visits by week" },
      seasonBands(x, 0, H - 18),
      Array.from({ length: Math.floor(T / 4) + 1 }, (_, i) => i * 4 + 1).filter((w) => w <= T).map((w) => s("g", null,
        s("line", { class: "grid-line", x1: x(w), x2: x(w), y1: top - 6, y2: H - 18 }), s("text", { x: x(w) + 2, y: H - 6 }, `w${w}`))));
    storyNodes().forEach((n) => {
      if (n.trigger && Number.isInteger(n.trigger.at) && n.trigger.at <= T) {
        const cx = x(n.trigger.at) + cell / 2;
        put(svg, s("path", { d: `M${cx} ${top - 14} l4 4 l-4 4 l-4 -4z`, fill: KIND_COLOR[nodeKindOf(n)] || "#888" }, s("title", null, `Week ${n.trigger.at}: ${n.title || n.id}`)));
      }
    });
    list.forEach((m, i) => {
      const eff = merchantEff(m);
      const y = top + i * rowH;
      const color = STYLE_COLOR[eff.style] || "#b59a00";
      put(svg, s("text", { x: 6, y: y + 16, fill: "#dedede" }, truncate(eff.name || eff.id, 22)));
      for (const [a, b] of merchantWindows(m)) {
        put(svg, s("rect", { x: x(a) + 1, y: y + 4, width: (b - a + 1) * cell - 2, height: rowH - 9, rx: 5, fill: color, opacity: eff.active === false ? 0.25 : 0.85,
          style: `filter: drop-shadow(0 0 4px ${color}88)` }, s("title", null, `${eff.name}: weeks ${a}–${b}`)));
      }
    });
    if (!list.length) put(svg, s("text", { x: L, y: top + 16 }, "No merchants: the village trades only at its local market."));
    return h("div", { class: "timeline-wrap" }, svg);
  }
  renderers.merchants = (host) => {
    const list = merchants();
    const timelineBox = h("div");
    const grid = h("div", { class: "grid wide-auto", style: { marginTop: "18px" } });
    const addButton = h("button", { type: "button", class: "primary", disabled: list.length >= 8, onclick: () => {
      const taken = new Set(list.map((m) => m.id));
      list.push({ id: uniqueId("merchant-", taken), name: "New merchant", style: "fair", schedule: { every: 4, start: 3, stay: 2 },
        sells: { food: { price: 5, stock: 40 } }, buys: { food: { price: 3, max: 30 } }, margin: 0.15 });
      touch("merchants");
      rerender();
    } }, "+ Add merchant");
    put(host, sectionHead("Economy", "Merchants",
      "Travelling traders bring food, medicine and tools. Fair traders are honest; sharks sell fakes (quality = share of genuine goods); guilds may change the rules of trade.", addButton),
      h("div", { class: "card" }, h("h3", null, "Who is in town, week by week"), timelineBox,
        h("div", { class: "legend" }, Object.entries(STYLE_COLOR).map(([k, c]) => h("span", null, h("i", { style: { background: c } }), titleCase(k))),
          h("span", null, "◆ story events with a fixed week"))),
      grid);
    const redrawTimeline = () => fill(timelineBox, merchantTimeline());
    redrawTimeline();
    subscribe("merchants", debounce(redrawTimeline, 120));
    put(grid, ...list.map((m, i) => merchantCard(m, i)));
  };
  function merchantCard(m, index) {
    const base = `merchants[${index}]`;
    const eff = merchantEff(m);
    const color = STYLE_COLOR[eff.style] || "#b59a00";
    const el = h("div", { class: "card merchant-card", style: { "--style-color": color, borderTopColor: color } });
    const scheduleBox = h("div");
    const drawSchedule = () => {
      const explicit = Array.isArray(m.visits);
      const schedule = merchantEff(m).schedule;
      const setSchedule = (key) => (v) => { if (!isObject(m.schedule)) m.schedule = clone(cat().defaults.merchant.schedule); m.schedule[key] = v; touch("merchants"); };
      const visits = h("input", { type: "text", class: "mono", value: explicit ? m.visits.join(", ") : "", "data-path": `${base}.visits`, placeholder: "e.g. 5, 18, 30" });
      visits.addEventListener("input", () => { m.visits = visits.value.split(/[\s,;]+/).filter(Boolean).map((t) => (/^\d+$/.test(t) ? Number(t) : t)); touch("merchants"); });
      fill(scheduleBox, F.segmented({ label: "Schedule", value: explicit ? "explicit" : "regular", options: [["regular", "Regular"], ["explicit", "Explicit weeks"]],
        onChange: (v) => { if (v === "explicit") m.visits = merchantWindows(m).map(([a]) => a); else delete m.visits; touch("merchants"); drawSchedule(); } }),
      h("div", { class: "form-grid three-col", style: { marginTop: "10px" } },
        explicit ? h("div", { class: "span-2" }, field("Arrival weeks", visits)) : F.number({ label: "Every (weeks)", value: schedule.every, min: 1, max: 52, integer: true, path: `${base}.schedule.every`, onChange: setSchedule("every") }),
        explicit ? null : F.number({ label: "First visit (week)", value: schedule.start, min: 1, max: ticks(), integer: true, path: `${base}.schedule.start`, onChange: setSchedule("start") }),
        F.number({ label: "Stays (weeks)", value: schedule.stay, min: 1, max: 12, integer: true, path: `${base}.schedule.stay`, onChange: setSchedule("stay") })));
    };
    drawSchedule();
    const idInput = h("input", { type: "text", value: m.id || "", class: "mono", "data-path": `${base}.id` });
    idInput.addEventListener("change", () => {
      const next = idInput.value.trim();
      if (next === m.id) return;
      if (!IDENT.test(next) || merchants().some((x) => x !== m && x.id === next)) { idInput.classList.add("invalid"); toast("Merchant ids must be unique identifiers.", "bad"); idInput.value = m.id; return; }
      for (const node of storyNodes()) for (const e of node.effects || []) if (e && e.type === "merchant" && e.id === m.id) e.id = next;
      m.id = next;
      touch("merchants");
    });
    put(el, 
      h("div", { class: "row between", style: { marginBottom: "12px" } },
        h("div", { class: "row" }, h("span", { class: "swatch", style: { background: color, color } }), h("strong", { style: { fontSize: "15px" } }, eff.name || eff.id)),
        h("button", { type: "button", class: "icon ghost", "aria-label": `Remove merchant ${eff.name}`, onclick: async () => {
          if (!(await confirmDialog({ title: `Remove ${eff.name}?`, ok: "Remove", danger: true }))) return;
          merchants().splice(index, 1); touch("merchants"); rerender();
        } }, "✕")),
      h("div", { class: "form-grid two-col" },
        F.text({ label: "Name", value: m.name || "", max: 60, path: `${base}.name`, onChange: (v) => { m.name = v; touch("merchants"); } }),
        field("Id", idInput),
        h("div", { class: "span-all" }, F.segmented({ label: "Style", value: eff.style, path: `${base}.style`,
          options: [["fair", "Fair", "Honest prices and genuine goods"], ["shark", "Shark", "High margins and fake goods"], ["guild", "Guild", "Follows (and changes) the rules of trade"]],
          onChange: (v) => { m.style = v; touch("merchants"); el.style.borderTopColor = STYLE_COLOR[v]; } })),
        F.toggle({ label: "Active", checked: eff.active !== false, path: `${base}.active`, onChange: (v) => { m.active = v; touch("merchants"); } }),
        F.text({ label: "Trade protocol", value: eff.protocol, mono: true, path: `${base}.protocol`, hint: "Convention variant it expects (e.g. haggle, sealed_bid).", onChange: (v) => { m.protocol = v.trim(); touch("merchants"); } }),
        h("div", { class: "span-all" }, F.range({ label: "Margin", value: eff.margin, min: 0, max: 0.6, step: 0.01, path: `${base}.margin`,
          hint: "How far the merchant will move from its price when haggling.", onChange: (v) => { m.margin = v ?? 0.15; touch("merchants"); } }))),
      h("div", { class: "divider" }), scheduleBox,
      h("div", { class: "divider" }), tradeTable(m, base, "sells"), h("div", { style: { height: "10px" } }), tradeTable(m, base, "buys"),
      foldCard("Shock prices", `${Object.keys(eff.shock_prices || {}).length}`, false, shockPrices(m, base)));
    return el;
  }
  function tradeTable(m, base, side) {
    const sells = side === "sells";
    const table = h("table", { class: "trade-table" });
    const draw = () => {
      const book = isObject(m[side]) ? m[side] : {};
      const rows = ITEMS.map((item) => {
        const terms = book[item];
        const on = isObject(terms);
        const toggle = h("input", { type: "checkbox", checked: on, "aria-label": `${sells ? "Sells" : "Buys"} ${item}` });
        toggle.addEventListener("change", () => {
          if (toggle.checked) book[item] = sells ? { price: 5, stock: 40, quality: 1 } : { price: 3, max: 40 };
          else delete book[item];
          m[side] = book;
          touch("merchants");
          draw();
        });
        const num = (key, min, max, step, fallback) => {
          const input = h("input", { type: "number", min, max, step, value: on ? terms[key] ?? fallback : "", disabled: !on, "data-path": `${base}.${side}.${item}.${key}`, "aria-label": `${item} ${key}` });
          input.addEventListener("input", () => { const v = Number(input.value); if (input.value === "") delete terms[key]; else if (Number.isFinite(v)) terms[key] = v; touch("merchants"); });
          return input;
        };
        return h("tr", null, h("td", null, h("label", { class: "row", style: { gap: "6px" } }, toggle, titleCase(item))),
          h("td", null, num("price", 0.1, 10000, 0.1, 5)),
          h("td", null, num(sells ? "stock" : "max", 0, 10000, 1, 40)),
          sells ? h("td", null, num("quality", 0, 1, 0.05, 1)) : null);
      });
      fill(table, h("thead", null, h("tr", null, h("th", null, sells ? "Sells" : "Buys"), h("th", null, "Price"), h("th", null, sells ? "Stock" : "Max"), sells ? h("th", { title: "Share of genuine goods" }, "Quality") : null)),
        h("tbody", null, rows));
    };
    draw();
    return table;
  }
  function shockPrices(m, base) {
    const box = h("div");
    const list = h("datalist", { id: `shocks-${base.replace(/\W/g, "")}` }, knownShocks().map((k) => h("option", { value: k })));
    const draw = () => {
      const shocks = isObject(m.shock_prices) ? m.shock_prices : {};
      const keep = () => { if (Object.keys(shocks).length) m.shock_prices = shocks; else delete m.shock_prices; touch("merchants"); };
      const rows = Object.entries(shocks).map(([shock, items]) => {
        const name = h("input", { type: "text", value: shock, class: "mono", list: list.id, "aria-label": "Shock name", "data-path": `${base}.shock_prices` });
        name.addEventListener("change", () => { const next = name.value.trim(); if (!next || next === shock) return; shocks[next] = shocks[shock]; delete shocks[shock]; keep(); draw(); });
        const factors = ITEMS.map((item) => {
          const input = h("input", { type: "number", min: 0, max: 20, step: 0.1, value: isObject(items) && items[item] != null ? items[item] : "", placeholder: "×1", "aria-label": `${shock} ${item} factor` });
          input.addEventListener("input", () => { if (!isObject(shocks[shock])) shocks[shock] = {}; if (input.value === "") delete shocks[shock][item]; else shocks[shock][item] = Number(input.value); keep(); });
          return h("td", null, input);
        });
        return h("tr", null, h("td", null, name), factors, h("td", null, h("button", { type: "button", class: "icon ghost", "aria-label": `Remove shock ${shock}`, onclick: () => { delete shocks[shock]; keep(); draw(); } }, "✕")));
      });
      fill(box, list, h("p", { class: "hint" }, "Price multipliers while a named shock (a story modifier with that shock, e.g. drought) is active."),
        h("table", { class: "trade-table shock-table" }, h("thead", null, h("tr", null, h("th", null, "Shock"), ITEMS.map((i) => h("th", null, titleCase(i))), h("th"))), h("tbody", null, rows)),
        h("button", { type: "button", class: "small", onclick: () => { shocks[uniqueId("shock-", new Set(Object.keys(shocks)))] = { food: 1.5 }; keep(); draw(); } }, "+ Add shock"));
    };
    draw();
    return box;
  }

  // =====================================================================================
  // 14. Section: Projects
  // =====================================================================================
  function projectSpec(key) {
    if (!isObject(state.raw.projects)) state.raw.projects = Object.fromEntries(Object.keys(cat().projects).map((k) => [k, true]));
    if (!isObject(state.raw.projects[key])) state.raw.projects[key] = {};
    return state.raw.projects[key];
  }
  function projectDag(eff) {
    const keys = Object.keys(eff);
    const level = {};
    const depth = (k, seen = new Set()) => {
      if (level[k] != null) return level[k];
      if (seen.has(k)) return 0;
      seen.add(k);
      const req = (eff[k].requires || []).filter((r) => eff[r]);
      level[k] = req.length ? 1 + Math.max(...req.map((r) => depth(r, seen))) : 0;
      return level[k];
    };
    keys.forEach((k) => depth(k));
    const byLevel = {};
    keys.forEach((k) => { (byLevel[level[k]] = byLevel[level[k]] || []).push(k); });
    const NW = 168, NH = 46, GX = 64, GY = 14, ROWS = 4;
    const columns = [];
    Object.keys(byLevel).map(Number).sort((a, b) => a - b).forEach((lvl) => {
      const required = new Set(keys.flatMap((k) => eff[k].requires || []));
      const list = byLevel[lvl].slice().sort((a, b) => Number(required.has(a)) - Number(required.has(b)));
      for (let i = 0; i < list.length; i += ROWS) columns.push(list.slice(i, i + ROWS));
    });
    const pos = {};
    const maxRows = Math.max(1, ...columns.map((c) => c.length));
    columns.forEach((list, col) => list.forEach((k, i) => { pos[k] = { x: 16 + col * (NW + GX), y: 14 + i * (NH + GY) + ((maxRows - list.length) * (NH + GY)) / 2 }; }));
    const W = 32 + columns.length * (NW + GX) - GX;
    const H = 28 + maxRows * (NH + GY) - GY;
    const svg = s("svg", { class: "chart", viewBox: `0 0 ${W} ${H}`, width: W, height: H, style: `width:${W}px;max-width:none;height:${H}px`, role: "img", "aria-label": "Project dependency graph" },
      s("defs", null, s("marker", { id: "dag-arrow", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse" }, s("path", { d: "M0 0 L10 5 L0 10z", fill: "#b59a00" }))));
    for (const k of keys) for (const r of eff[k].requires || []) {
      if (!pos[r]) continue;
      const a = pos[r], b = pos[k];
      const x1 = a.x + NW, y1 = a.y + NH / 2, x2 = b.x, y2 = b.y + NH / 2;
      put(svg, s("path", { d: `M${x1} ${y1} C${x1 + GX / 2} ${y1} ${x2 - GX / 2} ${y2} ${x2 - 2} ${y2}`, fill: "none", stroke: "#b59a00", "stroke-width": 1.6, "marker-end": "url(#dag-arrow)", opacity: eff[k].enabled ? 0.9 : 0.3 }));
    }
    for (const k of keys) {
      const p = eff[k];
      const { x, y } = pos[k];
      put(svg, s("g", { opacity: p.enabled ? 1 : 0.35 },
        s("rect", { x, y, width: NW, height: NH, rx: 10, fill: "#202020", stroke: p.enabled ? "rgba(226,194,28,0.55)" : "#3a3a3a" }),
        s("text", { x: x + 12, y: y + 19, fill: "#f0f0f0", "font-size": 12, "font-weight": 600 }, truncate(p.name || k, 22)),
        s("text", { x: x + 12, y: y + 35 }, `cost ${p.cost} · value ${p.value}${p.together ? " · together" : ""}`)));
    }
    return svg;
  }
  renderers.projects = (host) => {
    const dagBox = h("div", { class: "timeline-wrap" });
    const grid = h("div", { class: "grid wide-auto", style: { marginTop: "18px" } });
    const addButton = h("button", { type: "button", class: "primary", onclick: async () => {
      const taken = new Set(Object.keys(projectsEff()));
      const id = await promptDialog({ title: "Custom project", text: "A public work the steward can organise. It needs at least one skill.", label: "Project id", value: uniqueId("project-", taken), check: identCheck(taken) });
      if (!id) return;
      Object.assign(projectSpec(id), { name: titleCase(id), needs: { building: 2 }, together: false, cost: 20, requires: [], value: 2, benefit: {}, description: "" });
      touch("projects");
      rerender();
    } }, "+ Custom project");
    put(host, sectionHead("Public works", "Projects", "Projects the steward can organise with the residents' skills. Completed projects earn service-weeks (D3) and change the world.", addButton),
      h("div", { class: "card" }, h("h3", null, "Dependencies"), dagBox, h("p", { class: "tiny muted" }, "Arrows point from a requirement to the project that needs it. Faded projects are disabled in this society.")),
      grid);
    const drawDag = () => fill(dagBox, projectDag(projectsEff()));
    drawDag();
    subscribe("projects", debounce(drawDag, 120));
    const eff = projectsEff();
    put(grid, ...Object.keys(eff).map((key) => projectCard(key)));
  };
  function projectCard(key) {
    const el = h("div", { class: "card" });
    const draw = () => {
      const p = projectsEff()[key];
      const base = `projects.${key}`;
      const set = (fieldName, value) => { projectSpec(key)[fieldName] = value; touch("projects"); };
      const enabled = F.toggle({ label: p.enabled ? "Enabled" : "Disabled", checked: p.enabled, path: base, onChange: (on) => {
        projectSpec(key);
        if (on) state.raw.projects[key] = isObject(state.raw.projects[key]) && Object.keys(state.raw.projects[key]).length ? state.raw.projects[key] : true;
        else delete state.raw.projects[key];
        touch("projects");
        draw();
      } });
      const needs = h("div", { class: "form-grid three-col" }, cat().skills.map((skill) => F.number({ label: titleCase(skill), value: p.needs[skill] ?? "", min: 0, max: 52, integer: true, placeholder: "–",
        path: `${base}.needs.${skill}`, onChange: (v) => { const n = Object.assign({}, projectsEff()[key].needs); if (!v) delete n[skill]; else n[skill] = v; set("needs", n); } })));
      const others = Object.entries(projectsEff()).filter(([k]) => k !== key);
      const requires = h("div", { class: "chips", "data-path": `${base}.requires` }, others.map(([k, o]) => {
        const on = (p.requires || []).includes(k);
        return h("button", { type: "button", class: `small${on ? " active" : ""}`, "aria-pressed": String(on), onclick: () => {
          const list = new Set(projectsEff()[key].requires || []);
          if (list.has(k)) list.delete(k); else list.add(k);
          set("requires", Array.from(list));
          draw();
        } }, o.name || k);
      }));
      const benefitRows = Object.entries(p.benefit || {}).map(([bk, bv]) => h("div", { class: "faction-row", style: { gridTemplateColumns: "minmax(0,1.4fr) 90px auto" } },
        h("span", { class: "small", title: BENEFIT_HELP[bk] || "" }, h("span", { class: "mono" }, bk), h("br"), h("span", { class: "tiny muted" }, BENEFIT_HELP[bk] || "custom effect")),
        (() => { const inp = h("input", { type: "number", step: 0.05, value: bv, "aria-label": `${bk} value`, "data-path": `${base}.benefit.${bk}` });
          inp.addEventListener("input", () => { const b2 = Object.assign({}, projectsEff()[key].benefit); b2[bk] = Number(inp.value); set("benefit", b2); }); return inp; })(),
        h("button", { type: "button", class: "icon ghost", "aria-label": `Remove ${bk}`, onclick: () => { const b2 = Object.assign({}, projectsEff()[key].benefit); delete b2[bk]; set("benefit", b2); draw(); } }, "✕")));
      const addBenefit = F.select({ title: "Add a benefit", value: "", options: [["", "+ Add benefit…"]].concat(Object.keys(BENEFIT_HELP).filter((b) => !(b in (p.benefit || {}))).map((b) => [b, `${b} — ${BENEFIT_HELP[b]}`])),
        onChange: (v) => { if (!v) return; const b2 = Object.assign({}, projectsEff()[key].benefit); b2[v] = 1; set("benefit", b2); draw(); } });
      el.style.opacity = p.enabled ? "1" : "0.62";
      fill(el, 
        h("div", { class: "row between", style: { marginBottom: "10px" } }, h("div", null, h("strong", { style: { fontSize: "15px" } }, p.name || key), h("p", { class: "tiny muted mono" }, key)),
          h("div", { class: "row" }, p.custom ? h("span", { class: "badge gold" }, "custom") : null, enabled)),
        h("div", { class: "form-grid two-col" },
          F.text({ label: "Name", value: p.name || "", max: 60, path: `${base}.name`, onChange: (v) => set("name", v) }),
          F.toggle({ label: "Workers must work together", checked: Boolean(p.together), path: `${base}.together`, onChange: (v) => set("together", v), hint: "All needed skills in the same weeks." }),
          F.number({ label: "Cost (coins)", value: p.cost, min: 0, max: 1e6, path: `${base}.cost`, onChange: (v) => set("cost", v ?? 0) }),
          F.number({ label: "Service value / week (D3)", value: p.value, min: 0, max: 1e4, step: 0.5, path: `${base}.value`, onChange: (v) => set("value", v ?? 0) }),
          h("div", { class: "span-all" }, F.area({ label: "Description", value: p.description || "", rows: 2, max: 400, path: `${base}.description`, onChange: (v) => set("description", v) }))),
        h("h4", null, "Skill-weeks needed"), needs,
        h("h4", null, "Requires"), others.length ? requires : h("p", { class: "small muted" }, "No other projects."),
        h("h4", null, "Benefits when complete"), benefitRows.length ? h("div", null, benefitRows) : h("p", { class: "small muted" }, "No benefits."), addBenefit,
        !p.custom && isObject(getIn(state.raw, ["projects", key])) && Object.keys(state.raw.projects[key]).length
          ? h("div", { class: "row", style: { marginTop: "12px" } }, h("span", { class: "spacer" }), h("button", { type: "button", class: "small", onclick: () => { state.raw.projects[key] = true; touch("projects"); draw(); } }, "Reset to catalog")) : null,
        p.custom ? h("div", { class: "row", style: { marginTop: "12px" } }, h("span", { class: "spacer" }), h("button", { type: "button", class: "small danger", onclick: () => { delete state.raw.projects[key]; touch("projects"); rerender(); } }, "Delete project")) : null);
    };
    draw();
    return el;
  }

  // =====================================================================================
  // 15. Section: Facts & rumours
  // =====================================================================================
  function factTimeline(onSelect) {
    const list = facts();
    const T = ticks();
    const L = 210, cell = 12, rowH = 28, top = 26;
    const W = L + (T + 1) * cell + 12;
    const H = top + Math.max(1, list.length) * rowH + 22;
    const x = (w) => L + (w - 1) * cell;
    const svg = s("svg", { class: "chart", viewBox: `0 0 ${W} ${H}`, style: `min-width:${Math.min(W, 860)}px`, role: "img", "aria-label": "Facts timeline" },
      seasonBands(x, 0, H - 16),
      Array.from({ length: Math.floor(T / 4) + 1 }, (_, i) => i * 4 + 1).filter((w) => w <= T).map((w) => s("g", null,
        s("line", { class: "grid-line", x1: x(w), x2: x(w), y1: top - 4, y2: H - 16 }), s("text", { x: x(w) + 2, y: H - 4 }, `w${w}`))));
    list.forEach((f, i) => {
      const y = top + i * rowH;
      const rumor = Number.isInteger(f.rumor_week) ? f.rumor_week : 1;
      const resolve = Number.isInteger(f.resolve_week) ? f.resolve_week : T;
      const color = f.truth === true ? "#7cc47f" : f.truth === false ? "#ef6a5b" : "#9e9e9e";
      const selected = i === state.sel.fact;
      const g = s("g", { style: "cursor:pointer", onclick: () => onSelect(i) },
        s("rect", { x: 0, y: y - 2, width: W, height: rowH - 2, fill: selected ? "rgba(181,154,0,0.09)" : "transparent" }),
        s("text", { x: 8, y: y + 14, fill: selected ? "#f5e7a6" : "#dedede" }, truncate(f.statement || f.id, 32)),
        s("rect", { x: x(rumor), y: y + 4, width: Math.max(cell, (resolve - rumor) * cell), height: rowH - 12, rx: 6, fill: color, opacity: 0.75,
          style: `filter: drop-shadow(0 0 4px ${color}66)` }, s("title", null, `${f.statement}: rumour week ${rumor}, resolves week ${resolve} (${f.truth ? "true" : "false"})`)));
      if (Number.isInteger(f.probe_week)) {
        const cx = x(f.probe_week) + cell / 2;
        put(g, s("path", { d: `M${cx} ${y + 2} l6 8 l-6 8 l-6 -8z`, fill: "#e2c21c", stroke: "#121212" }, s("title", null, `Forecast asked in week ${f.probe_week}`)));
      }
      put(svg, g);
    });
    if (!list.length) put(svg, s("text", { x: L, y: top + 14 }, "No facts yet."));
    return h("div", { class: "timeline-wrap" }, svg);
  }
  renderers.facts = (host) => {
    const timelineBox = h("div");
    const listBox = h("div", { class: "stack", style: { gap: "8px" } });
    const panel = h("div", { class: "panel-body" });
    const aside = h("aside", { class: "card editor-panel", "aria-label": "Fact editor" }, panel);
    const sheet = makeSheet(aside, { title: "Fact" });
    const openEditor = () => { const f = facts()[state.sel.fact]; sheet.open(f ? truncate(f.statement || f.id, 60) : "Fact"); };
    const addButton = h("button", { type: "button", class: "primary", disabled: facts().length >= 16, onclick: () => {
      const taken = new Set(facts().map((f) => f.id));
      facts().push({ id: uniqueId("fact-", taken), statement: "Something is happening", truth: true, category: "rumour", rumor_week: 4, resolve_week: Math.min(ticks(), 16),
        sources: residents()[0] ? [{ who: residents()[0].id, accuracy: 0.7 }] : [], effects: [] });
      state.sel.fact = facts().length - 1;
      touch("facts");
      addButton.disabled = facts().length >= 16;
      drawAll();
      openEditor();
    } }, "+ Add fact");
    put(host, sectionHead("Information", "Facts & rumours",
      "Hidden truths and falsehoods that spread through the network. Residents' beliefs move with gossip, sources and evidence; believers change their behaviour. Forecast probes ask the steward for a probability (D1).", addButton),
      h("div", { class: "card" }, h("h3", null, "Timeline"), timelineBox,
        h("div", { class: "legend" }, h("span", null, h("i", { style: { background: "#7cc47f" } }), "true"), h("span", null, h("i", { style: { background: "#ef6a5b" } }), "false"),
          h("span", null, "◆ forecast probe"), h("span", null, "bar = from the rumour to its resolution"))),
      h("div", { class: "people-layout", style: { marginTop: "18px" } }, listBox, aside));
    const select = (i) => { state.sel.fact = i; drawAll(); openEditor(); };
    const drawTimeline = () => fill(timelineBox, factTimeline(select));
    const drawList = () => {
      fill(listBox, ...facts().map((f, i) => h("button", { type: "button", class: `card fact-card ${f.truth ? "true" : "false"}${i === state.sel.fact ? " selected" : ""}`,
        style: { textAlign: "left", display: "block", width: "100%", whiteSpace: "normal", borderColor: i === state.sel.fact ? "var(--accent)" : null },
        onclick: () => select(i) },
        h("div", { class: "row between" }, h("strong", null, f.statement || f.id), h("span", { class: `badge ${f.truth ? "good" : "bad"}` }, f.truth ? "true" : "false")),
        h("p", { class: "small muted" }, `${titleCase(f.category || "rumour")} · weeks ${f.rumor_week ?? 1}–${f.resolve_week ?? ticks()}${Number.isInteger(f.probe_week) ? ` · forecast in week ${f.probe_week}` : ""} · ${(f.sources || []).length} source(s), ${(f.effects || []).length} effect(s)`))));
      if (!facts().length) put(listBox, h("div", { class: "empty-note" }, "No facts. Add rumours, warnings, forecasts or accusations."));
    };
    const drawPanel = () => {
      const f = facts()[state.sel.fact];
      fill(panel, ...(f ? factEditor(f, state.sel.fact, { onChange: debounce(() => { drawTimeline(); drawList(); }, 150), onRemove: () => {
        facts().splice(state.sel.fact, 1); state.sel.fact = Math.max(0, state.sel.fact - 1); touch("facts"); addButton.disabled = facts().length >= 16; drawAll(); sheet.close();
      } }) : [h("p", { class: "muted" }, "Select or add a fact.")]));
    };
    const drawAll = () => { drawTimeline(); drawList(); drawPanel(); };
    if (!facts()[state.sel.fact]) state.sel.fact = 0;
    drawAll();
    return () => sheet.destroy();
  };
  function factEditor(f, index, { onChange, onRemove }) {
    const base = `facts[${index}]`;
    const changed = () => { if (sources.length || Array.isArray(f.sources)) f.sources = sources; if (beliefs.length || Array.isArray(f.effects)) f.effects = beliefs; touch("facts"); onChange(); };
    const sources = Array.isArray(f.sources) ? f.sources : [];
    const beliefs = Array.isArray(f.effects) ? f.effects : [];
    const idInput = h("input", { type: "text", value: f.id || "", class: "mono", "data-path": `${base}.id` });
    idInput.addEventListener("change", () => {
      const next = idInput.value.trim();
      if (next === f.id) return;
      if (!IDENT.test(next) || facts().some((x) => x !== f && x.id === next)) { idInput.classList.add("invalid"); toast("Fact ids must be unique identifiers.", "bad"); idInput.value = f.id; return; }
      renameFactRefs(f.id, next);
      f.id = next;
      changed();
    });
    const probeBox = h("div");
    const drawProbe = () => {
      const on = Number.isInteger(f.probe_week);
      fill(probeBox, F.toggle({ label: "Ask the steward for a forecast", checked: on, path: `${base}.probe_week`,
        onChange: (v) => { if (v) f.probe_week = Math.min((f.resolve_week ?? ticks()) - 1, (f.rumor_week ?? 1) + 2); else delete f.probe_week; changed(); drawProbe(); } }),
      on ? F.number({ label: "Probe week", value: f.probe_week, min: f.rumor_week ?? 1, max: (f.resolve_week ?? ticks()) - 1, integer: true, path: `${base}.probe_week`,
        hint: "Between the rumour week and the week before resolution.", onChange: (v) => { f.probe_week = v; changed(); } }) : null);
    };
    drawProbe();
    const sourcesBox = h("div");
    const drawSources = () => {
      fill(sourcesBox, ...sources.map((src, j) => h("div", { class: "source-row" },
        F.select({ title: "Source resident", value: src.who, options: peopleOptions(), path: `${base}.sources[${j}]`, onChange: (v) => { src.who = v; changed(); } }),
        F.range({ label: "Accuracy", value: src.accuracy ?? 0.7, path: `${base}.sources[${j}].accuracy`, onChange: (v) => { src.accuracy = v; changed(); } }),
        F.toggle({ label: "Lies", checked: Boolean(src.lies), onChange: (v) => { if (v) src.lies = true; else delete src.lies; changed(); } }),
        h("button", { type: "button", class: "icon ghost", "aria-label": "Remove source", onclick: () => { sources.splice(j, 1); changed(); drawSources(); } }, "✕"))),
      h("button", { type: "button", class: "small", onclick: () => { const p = people()[0]; sources.push({ who: p ? p.id : "", accuracy: 0.7 }); changed(); drawSources(); } }, "+ Add source"),
      h("p", { class: "hint" }, "Who starts the rumour, and how often what they say matches the truth. A liar spreads the falsehood on purpose."));
    };
    drawSources();
    const effectsBox = h("div");
    const shockList = h("datalist", { id: `fact-shocks-${index}` }, knownShocks().map((k) => h("option", { value: k })));
    const drawEffects = () => {
      fill(effectsBox, shockList, ...beliefs.map((e, j) => {
        const path = `${base}.effects[${j}]`;
        const roles = h("div", { class: "chips" }, Object.keys(cat().roles).map((role) => {
          const on = (e.roles || []).includes(role);
          return h("button", { type: "button", class: `small${on ? " active" : ""}`, "aria-pressed": String(on), onclick: () => {
            const set = new Set(e.roles || []);
            if (set.has(role)) set.delete(role); else set.add(role);
            e.roles = Array.from(set);
            if (!e.roles.length) delete e.roles;
            changed();
            drawEffects();
          } }, role);
        }));
        const shock = h("input", { type: "text", value: e.shock || "", list: shockList.id, class: "mono", placeholder: "drought", "aria-label": "Shock name" });
        shock.addEventListener("input", () => { if (shock.value.trim()) e.shock = shock.value.trim(); else delete e.shock; changed(); });
        return h("div", { class: "effect-card" },
          h("header", null, F.select({ title: "Belief effect", value: e.kind, path: `${path}`, options: Object.keys(cat().belief_effects).map((k) => [k, titleCase(k)]), onChange: (v) => { e.kind = v; changed(); drawEffects(); } }),
            h("span", { class: "grow" }), h("button", { type: "button", class: "icon ghost", "aria-label": "Remove effect", onclick: () => { beliefs.splice(j, 1); changed(); drawEffects(); } }, "✕")),
          h("p", { class: "help" }, cat().belief_effects[e.kind] || ""),
          h("div", { class: "form-grid" },
            F.range({ label: "Amount", value: e.amount ?? 0.5, min: 0, max: 5, step: 0.01, path: `${path}.amount`, onChange: (v) => { e.amount = v; changed(); } }),
            F.range({ label: "Belief threshold", value: e.threshold ?? 0.6, min: 0.5, max: 1, step: 0.01, path: `${path}.threshold`, onChange: (v) => { e.threshold = v; changed(); } }),
            e.kind === "prepare" ? field("Shock prepared for", shock) : null,
            h("div", { class: "span-all" }, group("Only for roles (none = everyone)", roles))));
      }), h("button", { type: "button", class: "small", onclick: () => { beliefs.push({ kind: "stress", amount: 0.1, threshold: 0.6 }); changed(); drawEffects(); } }, "+ Add belief effect"));
    };
    drawEffects();
    return [
      h("div", { class: "panel-title" }, h("div", null, h("h3", null, f.statement || f.id), h("p", { class: "tiny muted mono" }, f.id))),
      h("div", { class: "form-grid two-col" },
        h("div", { class: "span-all" }, F.text({ label: "Statement", value: f.statement || "", max: 200, path: `${base}.statement`, onChange: (v) => { f.statement = v; changed(); } })),
        field("Id", idInput),
        F.text({ label: "Topic", value: f.topic || "", max: 40, path: `${base}.topic`, placeholder: f.id, onChange: (v) => { if (v) f.topic = v; else delete f.topic; changed(); } }),
        F.segmented({ label: "Truth", value: f.truth, path: `${base}.truth`, options: [[true, "True"], [false, "False"]], onChange: (v) => { f.truth = v; changed(); } }),
        F.select({ label: "Category", value: f.category || "rumour", path: `${base}.category`, options: ["rumour", "forecast", "accusation", "warning"].map((c) => [c, titleCase(c)]), onChange: (v) => { f.category = v; changed(); } }),
        F.number({ label: "Rumour week", value: f.rumor_week ?? 1, min: 1, max: ticks(), integer: true, path: `${base}.rumor_week`, onChange: (v) => { f.rumor_week = v; changed(); } }),
        F.number({ label: "Resolve week", value: f.resolve_week ?? ticks(), min: 1, max: ticks() + 1, integer: true, path: `${base}.resolve_week`, onChange: (v) => { f.resolve_week = v; changed(); } }),
        h("div", { class: "span-all" }, probeBox),
        h("div", { class: "span-all" }, F.range({ label: "Evidence accuracy", value: f.evidence_accuracy ?? 0.85, min: 0.5, max: 1, step: 0.01, path: `${base}.evidence_accuracy`,
          hint: "How reliable inspections and evidence are when the steward investigates.", onChange: (v) => { f.evidence_accuracy = v; changed(); } })),
        h("div", { class: "span-all" }, F.area({ label: "Reveal (announced at resolution)", value: f.reveal || "", max: 300, rows: 2, path: `${base}.reveal`, onChange: (v) => { f.reveal = v; changed(); } }))),
      foldCard("Sources", String(sources.length), true, sourcesBox),
      foldCard("Effects on believers", String(beliefs.length), true, effectsBox),
      h("div", { class: "row", style: { marginTop: "14px" } }, h("span", { class: "spacer" }), h("button", { type: "button", class: "small danger", onclick: onRemove }, "Delete fact")),
    ];
  }
  function renameFactRefs(from, to) {
    const fix = (c) => {
      if (!isObject(c)) return;
      (c.all || []).forEach(fix); (c.any || []).forEach(fix); if (c.not) fix(c.not);
      if (c.metric === `belief:${from}`) c.metric = `belief:${to}`;
      if (c.metric === `informed:${from}`) c.metric = `informed:${to}`;
    };
    for (const node of storyNodes()) {
      for (const e of node.effects || []) if (e && e.type === "probe" && e.fact === from) e.fact = to;
      if (node.trigger && node.trigger.when) fix(node.trigger.when);
      for (const edge of node.edges || []) if (edge && edge.if) fix(edge.if);
    }
  }

  // =====================================================================================
  // 16. Section: Story graph (canvas editor, node and edge panels, condition builder)
  // =====================================================================================
  const NODE_W = 158;
  const NODE_H = 46;
  const OUTCOME_H = 38;
  const OPS = ["<", "<=", ">", ">=", "==", "!="];
  function nodeKindOf(node) {
    if (node && isObject(node.outcome) && (node.outcome.type === "collapse" || node.outcome.type === "flourish")) return node.outcome.type;
    return node && node.kind ? node.kind : "event";
  }
  function condShort(c, depth = 0) {
    if (!isObject(c)) return "?";
    const wrap = (text, n) => (depth > 0 && n > 1 ? `(${text})` : text);
    if (Array.isArray(c.all)) return wrap(c.all.map((x) => condShort(x, depth + 1)).join(" and "), c.all.length);
    if (Array.isArray(c.any)) return wrap(c.any.map((x) => condShort(x, depth + 1)).join(" or "), c.any.length);
    if ("not" in c) return `not ${condShort(c.not, depth + 1)}`;
    return `${c.metric} ${c.op || ">="} ${c.value}`;
  }
  function windowText(t) {
    if (!isObject(t) || (t.from == null && t.to == null)) return "";
    return ` · w${t.from ?? 1}–${t.to ?? ticks()}`;
  }
  function triggerSummary(node, incoming) {
    const t = node.trigger;
    if (!isObject(t)) return incoming ? "reached via edges" : "never triggered";
    if ("at" in t) return `at week ${t.at}`;
    if ("chance" in t) return `${Math.round((Number(t.chance) || 0) * 100)}% / week${windowText(t)}`;
    if ("when" in t) return `when ${condShort(t.when)}${windowText(t)}`;
    return "trigger?";
  }
  function rewardText(outcome) {
    const reward = isObject(outcome.reward) ? outcome.reward : {};
    const parts = Object.entries(reward).filter(([, v]) => v).map(([d, v]) => `${v > 0 ? "+" : ""}${v} ${d}`);
    return parts.join(" ") || (outcome.type === "collapse" ? "score → 0" : "no reward");
  }
  function incomingMap(nodes) {
    const map = new Map();
    nodes.forEach((n) => (n.edges || []).forEach((e, j) => {
      if (!e || !e.to) return;
      if (!map.has(e.to)) map.set(e.to, []);
      map.get(e.to).push({ from: n.id, index: j });
    }));
    return map;
  }
  /** Layered (left-to-right) layout of the story graph. */
  function layeredLayout(nodes) {
    const byId = new Map(nodes.map((n) => [n.id, n]));
    const incoming = incomingMap(nodes);
    const depth = new Map();
    const roots = nodes.filter((n) => isObject(n.trigger) || !incoming.has(n.id))
      .sort((a, b) => ((a.trigger && (a.trigger.at || a.trigger.from)) || 0) - ((b.trigger && (b.trigger.at || b.trigger.from)) || 0));
    const queue = roots.map((n) => n.id);
    queue.forEach((id) => depth.set(id, 0));
    let guard = 0;
    while (queue.length && guard++ < 4000) {
      const id = queue.shift();
      for (const e of byId.get(id).edges || []) {
        if (!e || !byId.has(e.to)) continue;
        const d = depth.get(id) + 1;
        if (!depth.has(e.to) || (depth.get(e.to) < d && d < 10)) { depth.set(e.to, d); queue.push(e.to); }
      }
    }
    nodes.forEach((n) => { if (!depth.has(n.id)) depth.set(n.id, 0); });
    const pos = new Map();
    const colY = new Map();
    const place = (id, prefer) => {
      if (pos.has(id)) return;
      const d = depth.get(id);
      const y = Math.max(prefer, colY.get(d) ?? 20);
      pos.set(id, { x: 60 + d * 250, y });
      colY.set(d, y + 74);
      for (const e of byId.get(id).edges || []) if (e && byId.has(e.to)) place(e.to, y);
    };
    roots.forEach((n) => place(n.id, colY.get(0) ?? 20));
    nodes.forEach((n) => place(n.id, colY.get(depth.get(n.id)) ?? 20));
    return pos;
  }

  /** Pannable, zoomable canvas of the choice graph with animated flow along edges. */
  class StoryCanvas {
    constructor(host, opts) {
      this.opts = Object.assign({ editable: true, getNodes: () => [], onSelect() {}, onMove() {}, onLink() {}, onAdd() {}, onDelete() {} }, opts);
      this.host = host;
      this.canvas = h("canvas", { tabindex: "0", "aria-label": this.opts.editable
        ? "Story graph editor. Arrow keys move the selected node, Delete removes it, F fits the view, plus and minus zoom."
        : "Story graph playback" });
      this.tip = h("div", { class: "canvas-tip", style: { opacity: "0" }, "aria-hidden": "true" });
      this.hint = h("div", { class: "canvas-hint", hidden: true, role: "status" });
      put(host, this.canvas, this.tip, this.hint);
      this.ctx = this.canvas.getContext("2d");
      this.view = { x: 30, y: 30, k: 0.85 };
      this.sel = null;
      this.hover = null;
      this.drag = null;
      this.link = null;
      this.pointers = new Map();
      this.pinch = null;
      this.press = 0;
      this.lastTap = null;
      this.lastDraw = 0;
      this.connectMode = false;
      this.connectFrom = null;
      this.pinned = null;
      this.play = null;
      this.auto = new Map();
      this.widths = new Map();
      this.visible = true;
      this.raf = 0;
      this.w = 1; this.h = 1; this.dpr = 1;
      this.fitted = false;
      this.bind();
      this.ro = new ResizeObserver(() => this.resize());
      this.ro.observe(host);
      this.unwatch = watchVisibility(host, (v) => { this.visible = v; if (v) this.request(); });
      this.resize();
    }
    nodes() { return this.opts.getNodes() || []; }
    refreshAuto() {
      const nodes = this.nodes();
      const missing = nodes.filter((n) => !isObject(n.position));
      this.auto.clear();
      if (!missing.length) return;
      if (missing.length === nodes.length) { this.auto = layeredLayout(nodes); return; }
      let maxY = 0, minX = Infinity;
      nodes.forEach((n) => { if (isObject(n.position)) { maxY = Math.max(maxY, Number(n.position.y) || 0); minX = Math.min(minX, Number(n.position.x) || 0); } });
      missing.forEach((n, i) => this.auto.set(n.id, { x: (Number.isFinite(minX) ? minX : 60) + (i % 4) * 230, y: maxY + 90 + Math.floor(i / 4) * 74 }));
    }
    rect(node) {
      const p = isObject(node.position) ? node.position : this.auto.get(node.id) || { x: 0, y: 0 };
      const kind = nodeKindOf(node);
      const outcome = kind === "collapse" || kind === "flourish" || node.kind === "outcome";
      return { x: Number(p.x) || 0, y: Number(p.y) || 0, w: NODE_W, h: outcome ? OUTCOME_H : NODE_H, outcome, kind };
    }
    resize() {
      const r = this.host.getBoundingClientRect();
      this.w = Math.max(1, r.width);
      this.h = Math.max(1, r.height);
      this.dpr = Math.min(window.devicePixelRatio || 1, 2.5);
      this.canvas.width = Math.round(this.w * this.dpr);
      this.canvas.height = Math.round(this.h * this.dpr);
      if (!this.fitted && this.w > 10) { this.fitWidth(); this.fitted = true; }
      this.request();
    }
    fit() {
      this.refreshAuto();
      const nodes = this.nodes();
      if (!nodes.length) { this.view = { x: 40, y: 40, k: 1 }; this.updateGrid(); return; }
      let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      for (const n of nodes) { const r = this.rect(n); x0 = Math.min(x0, r.x); y0 = Math.min(y0, r.y); x1 = Math.max(x1, r.x + r.w); y1 = Math.max(y1, r.y + r.h); }
      const k = clamp(Math.min((this.w - 60) / (x1 - x0 || 1), (this.h - 60) / (y1 - y0 || 1)), 0.22, 1.15);
      this.view = { k, x: (this.w - (x1 - x0) * k) / 2 - x0 * k, y: Math.max(24, (this.h - (y1 - y0) * k) / 2) - y0 * k };
      this.updateGrid();
      this.request();
    }
    fitWidth() {
      this.refreshAuto();
      const nodes = this.nodes();
      if (!nodes.length) { this.fit(); return; }
      let x0 = Infinity, y0 = Infinity, x1 = -Infinity;
      for (const n of nodes) { const r = this.rect(n); x0 = Math.min(x0, r.x); y0 = Math.min(y0, r.y); x1 = Math.max(x1, r.x + r.w); }
      // Small screens start a little larger so that node titles stay legible; the rest is a pan away.
      const k = clamp((this.w - 60) / (x1 - x0 || 1), this.w < 520 ? 0.82 : 0.72, 1.05);
      this.view = { k, x: (x1 - x0) * k < this.w - 60 ? (this.w - (x1 - x0) * k) / 2 - x0 * k : 30 - x0 * k, y: 30 - y0 * k };
      this.updateGrid();
      this.request();
    }
    center(id) {
      const node = this.nodes().find((n) => n.id === id);
      if (!node) return;
      const r = this.rect(node);
      this.view.x = this.w / 2 - (r.x + r.w / 2) * this.view.k;
      this.view.y = this.h / 2 - (r.y + r.h / 2) * this.view.k;
      this.updateGrid();
      this.request();
    }
    zoom(factor, cx = this.w / 2, cy = this.h / 2) {
      const k = clamp(this.view.k * factor, 0.2, 2.5);
      this.view.x = cx - ((cx - this.view.x) * k) / this.view.k;
      this.view.y = cy - ((cy - this.view.y) * k) / this.view.k;
      this.view.k = k;
      this.updateGrid();
      this.request();
    }
    updateGrid() {
      const size = 24 * this.view.k;
      this.host.style.backgroundImage = "radial-gradient(circle, rgba(255,255,255,0.07) 1px, transparent 1.4px)";
      this.host.style.backgroundSize = `${size}px ${size}px`;
      this.host.style.backgroundPosition = `${this.view.x}px ${this.view.y}px`;
    }
    select(sel) { this.sel = sel; this.request(); }
    setPlayback(play) {
      const before = this.play && this.play.states ? this.play.states : {};
      this.play = play;
      const fresh = Object.entries(play.states || {}).filter(([id, st]) => st && st.activated === play.week && !before[id]).map(([id]) => id);
      if (fresh.length && this.follow !== false) this.panTo(fresh[fresh.length - 1]);
      this.request();
    }
    panTo(id) {
      const node = this.nodes().find((n) => n.id === id);
      if (!node) return;
      const r = this.rect(node);
      const target = { x: this.w / 2 - (r.x + r.w / 2) * this.view.k, y: this.h / 2 - (r.y + r.h / 2) * this.view.k };
      if (reducedMotion()) { this.view.x = target.x; this.view.y = target.y; this.updateGrid(); return; }
      this.panTarget = target;
    }
    request() { if (!this.raf && this.visible) this.raf = requestAnimationFrame((t) => this.frame(t)); }
    frame(now) {
      this.raf = 0;
      if (!this.visible) return;
      // Phones: the idle flow animation runs at ~30 fps; gestures stay at full rate.
      if (COARSE && !this.drag && !this.pinch && !this.link && !this.panTarget && now - this.lastDraw < 32) { this.request(); return; }
      this.lastDraw = now;
      if (this.panTarget && !this.drag) {
        this.view.x += (this.panTarget.x - this.view.x) * 0.12;
        this.view.y += (this.panTarget.y - this.view.y) * 0.12;
        if (Math.hypot(this.panTarget.x - this.view.x, this.panTarget.y - this.view.y) < 0.5) this.panTarget = null;
        this.updateGrid();
      }
      this.draw(now);
      if (!reducedMotion() || this.panTarget) this.request();
    }
    toWorld(e) {
      const r = this.canvas.getBoundingClientRect();
      return { x: (e.clientX - r.left - this.view.x) / this.view.k, y: (e.clientY - r.top - this.view.y) / this.view.k, sx: e.clientX - r.left, sy: e.clientY - r.top };
    }
    hitNode(p) {
      const nodes = this.nodes();
      for (let i = nodes.length - 1; i >= 0; i--) {
        const r = this.rect(nodes[i]);
        if (p.x >= r.x && p.x <= r.x + r.w && p.y >= r.y && p.y <= r.y + r.h) return nodes[i];
      }
      return null;
    }
    hitPort(p, slop = 10) {
      if (!this.opts.editable) return null;
      for (const n of this.nodes()) {
        const r = this.rect(n);
        if (Math.hypot(p.x - (r.x + r.w), p.y - (r.y + r.h / 2)) < slop / this.view.k) return n;
      }
      return null;
    }
    geometry(a, b) {
      if (a === b) return [a.x + a.w - 20, a.y, a.x + a.w + 40, a.y - 60, a.x + 20, a.y - 60, a.x + 30, a.y];
      const x0 = a.x + a.w, y0 = a.y + a.h / 2, x3 = b.x, y3 = b.y + b.h / 2;
      const dx = Math.max(46, Math.abs(x3 - x0) * 0.5);
      return [x0, y0, x0 + dx, y0, x3 - dx, y3, x3, y3];
    }
    static bez(g, t, out) {
      const u = 1 - t;
      const a = u * u * u, b = 3 * u * u * t, c = 3 * u * t * t, d = t * t * t;
      out.x = a * g[0] + b * g[2] + c * g[4] + d * g[6];
      out.y = a * g[1] + b * g[3] + c * g[5] + d * g[7];
      return out;
    }
    hitEdge(p, slop = 7) {
      const nodes = this.nodes();
      const byId = new Map(nodes.map((n) => [n.id, n]));
      const tmp = { x: 0, y: 0 };
      let best = null, bestD = slop / this.view.k;
      for (const n of nodes) (n.edges || []).forEach((e, index) => {
        const target = e && byId.get(e.to);
        if (!target) return;
        const g = this.geometry(this.rect(n), this.rect(target));
        for (let t = 0; t <= 1.0001; t += 1 / 24) {
          StoryCanvas.bez(g, t, tmp);
          const d = Math.hypot(tmp.x - p.x, tmp.y - p.y);
          if (d < bestD) { bestD = d; best = { type: "edge", from: n.id, index }; }
        }
      });
      return best;
    }
    bind() {
      const c = this.canvas;
      c.addEventListener("pointerdown", (e) => this.onDown(e));
      c.addEventListener("pointermove", (e) => this.onMove(e));
      c.addEventListener("pointerup", (e) => this.onUp(e));
      c.addEventListener("pointercancel", (e) => this.onCancel(e));
      c.addEventListener("pointerleave", (e) => { if (e.pointerType === "mouse") { this.hover = null; this.tip.style.opacity = "0"; } });
      c.addEventListener("wheel", (e) => { e.preventDefault(); const p = this.toWorld(e); this.zoom(Math.exp(-e.deltaY * 0.0015), p.sx, p.sy); }, { passive: false });
      c.addEventListener("dblclick", (e) => {
        if (!this.opts.editable || this.lastPointer !== "mouse" || this.connectMode) return;
        const p = this.toWorld(e);
        if (!this.hitNode(p)) this.opts.onAdd({ x: Math.round((p.x - NODE_W / 2) / 10) * 10, y: Math.round((p.y - NODE_H / 2) / 10) * 10 });
      });
      c.addEventListener("keydown", (e) => this.onKey(e));
    }
    cancelPress() { clearTimeout(this.press); this.press = 0; }
    setConnectMode(on) { this.connectMode = Boolean(on); this.setConnectFrom(null); }
    /** Pending edge source while connecting with taps (Connect mode or after a long press). */
    setConnectFrom(id) {
      this.connectFrom = id || null;
      const node = id ? this.nodes().find((n) => n.id === id) : null;
      this.hint.hidden = !node && !this.connectMode;
      this.hint.textContent = node ? `Now pick the node “${truncate(node.title || node.id, 26)}” leads to · empty space cancels`
        : "Connect: pick the node an edge starts from";
      this.request();
    }
    tapConnect(node) {
      if (!this.connectFrom) { this.setConnectFrom(node.id); return; }
      const from = this.connectFrom;
      this.setConnectFrom(null);
      if (node.id !== from) this.opts.onLink(from, node.id);
    }
    startPinch() {
      this.cancelPress();
      if (this.drag && this.drag.type === "node" && this.drag.moved && this.opts.editable) this.opts.onMove(this.drag.node);
      this.drag = null;
      this.link = null;
      this.panTarget = null;
      this.tip.style.opacity = "0";
      const [a, b] = Array.from(this.pointers.values());
      const r = this.canvas.getBoundingClientRect();
      const mx = (a.x + b.x) / 2 - r.left, my = (a.y + b.y) / 2 - r.top;
      this.pinch = { d0: Math.hypot(a.x - b.x, a.y - b.y) || 1, k0: this.view.k, wx: (mx - this.view.x) / this.view.k, wy: (my - this.view.y) / this.view.k };
      this.request();
    }
    movePinch() {
      const [a, b] = Array.from(this.pointers.values());
      if (!a || !b) return;
      const r = this.canvas.getBoundingClientRect();
      const mx = (a.x + b.x) / 2 - r.left, my = (a.y + b.y) / 2 - r.top;
      const k = clamp(this.pinch.k0 * (Math.hypot(a.x - b.x, a.y - b.y) / this.pinch.d0), 0.2, 2.5);
      this.view = { k, x: mx - this.pinch.wx * k, y: my - this.pinch.wy * k };
      this.updateGrid();
      this.request();
    }
    onDown(e) {
      if (e.pointerType === "mouse" && e.button !== 0 && e.button !== 1) return;
      this.lastPointer = e.pointerType;
      try { this.canvas.setPointerCapture(e.pointerId); } catch { /* pointer already gone */ }
      this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (this.pointers.size === 2) { this.startPinch(); return; }
      if (this.pointers.size > 2 || this.pinch) return;
      const touch = e.pointerType !== "mouse";
      const primary = e.button === 0 || touch;
      this.cancelPress();
      if (!this.pinned) this.tip.style.opacity = "0";
      const p = this.toWorld(e);
      const base = { sx: e.clientX, sy: e.clientY, vx: this.view.x, vy: this.view.y, moved: false, touch };
      if (this.connectMode && this.opts.editable && primary) {
        const node = this.hitNode(p);
        this.drag = Object.assign({ type: node ? "tap" : "pan", node }, base);
        return;
      }
      const port = primary ? this.hitPort(p, touch ? 22 : 10) : null;
      if (port) { this.link = { from: port.id, x: p.x, y: p.y }; this.request(); return; }
      const node = primary ? this.hitNode(p) : null;
      if (node) {
        if (this.opts.editable && e.shiftKey) { this.link = { from: node.id, x: p.x, y: p.y }; return; }
        const r = this.rect(node);
        this.drag = Object.assign({ type: "node", node, dx: p.x - r.x, dy: p.y - r.y }, base);
        if (this.connectFrom) return;
        this.sel = { type: "node", id: node.id };
        this.request();
        // Touch: a long press on a node starts an edge (then drag to the target, or lift and tap it).
        if (touch && this.opts.editable) {
          this.press = setTimeout(() => {
            if (!this.drag || this.drag.node !== node || this.drag.moved) return;
            this.drag = null;
            this.link = { from: node.id, x: p.x, y: p.y, press: true };
            this.hint.hidden = false;
            this.hint.textContent = "Drag to the node it leads to, or lift and tap it";
            if (navigator.vibrate) navigator.vibrate(12);
            this.request();
          }, 460);
        }
        return;
      }
      const edge = primary ? this.hitEdge(p, touch ? 16 : 7) : null;
      this.panTarget = null;
      this.drag = Object.assign({ type: "pan", edge }, base);
      this.canvas.style.cursor = "grabbing";
    }
    onMove(e) {
      if (this.pointers.has(e.pointerId)) this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (this.pinch) { this.movePinch(); return; }
      const p = this.toWorld(e);
      this.pointer = p;
      if (this.link) { this.link.x = p.x; this.link.y = p.y; this.hover = this.hitNode(p); this.tip.style.opacity = "0"; this.request(); return; }
      const d = this.drag;
      if (d) {
        if (!d.moved && Math.hypot(e.clientX - d.sx, e.clientY - d.sy) > (d.touch ? 8 : 3)) { d.moved = true; this.cancelPress(); if (!this.pinned) this.tip.style.opacity = "0"; }
        if (d.type === "node") {
          if (d.moved && this.opts.editable && !this.connectFrom) {
            d.node.position = { x: Math.round((p.x - d.dx) / 10) * 10, y: Math.round((p.y - d.dy) / 10) * 10 };
            this.request();
          } else if (d.moved) this.panBy(d, e);
          return;
        }
        if (d.moved) this.panBy(d, e);
        return;
      }
      if (e.pointerType !== "mouse") return;
      const port = this.hitPort(p);
      const node = this.hitNode(p);
      const edge = node || port ? null : this.hitEdge(p);
      this.canvas.style.cursor = this.connectMode || this.connectFrom ? (node ? "crosshair" : "grab") : port ? "crosshair" : node ? (this.opts.editable ? "move" : "pointer") : edge ? "pointer" : "grab";
      if (node !== this.hover) { this.hover = node; this.request(); }
      if (!this.pinned) this.updateTip(node, p);
      if (this.connectFrom) this.request();
    }
    panBy(d, e) {
      this.view.x = d.vx + (e.clientX - d.sx);
      this.view.y = d.vy + (e.clientY - d.sy);
      this.updateGrid();
      this.request();
    }
    onUp(e) {
      this.pointers.delete(e.pointerId);
      if (this.pinch) { if (this.pointers.size < 2) { this.pinch = null; this.drag = null; } return; }
      this.cancelPress();
      const p = this.toWorld(e);
      if (this.link) {
        const target = this.hitNode(p);
        const { from, press } = this.link;
        this.link = null;
        if (press) this.hint.hidden = !this.connectMode;
        if (target && (!press || target.id !== from)) this.opts.onLink(from, target.id);
        else if (press) this.setConnectFrom(from);
        this.request();
        return;
      }
      const d = this.drag;
      this.drag = null;
      this.canvas.style.cursor = "grab";
      if (!d) return;
      if (d.type === "tap") { if (!d.moved) this.tapConnect(d.node); return; }
      if (d.type === "node") {
        if (this.connectFrom && !d.moved) { this.tapConnect(d.node); return; }
        if (d.moved && this.opts.editable && !this.connectFrom) this.opts.onMove(d.node);
        if (!this.opts.editable && d.touch && !d.moved) { this.pinTip(d.node, p); return; }
        this.opts.onSelect(this.sel, { open: !d.moved });
        return;
      }
      if (d.moved) return;
      if (this.pinned) { this.pinned = null; this.tip.style.opacity = "0"; }
      if (d.edge) { this.sel = d.edge; this.opts.onSelect(d.edge, { open: true }); this.request(); return; }
      if (this.connectFrom) { this.setConnectFrom(null); return; }
      if (this.connectMode) return;
      // Touch: a double tap on empty space adds an event there.
      const now = performance.now();
      if (d.touch && this.opts.editable && this.lastTap && now - this.lastTap.t < 360 && Math.hypot(e.clientX - this.lastTap.x, e.clientY - this.lastTap.y) < 32) {
        this.lastTap = null;
        this.opts.onAdd({ x: Math.round((p.x - NODE_W / 2) / 10) * 10, y: Math.round((p.y - NODE_H / 2) / 10) * 10 });
        return;
      }
      this.lastTap = { t: now, x: e.clientX, y: e.clientY };
      if (this.sel) { this.sel = null; this.opts.onSelect(null); this.request(); }
    }
    onCancel(e) {
      this.pointers.delete(e.pointerId);
      this.cancelPress();
      if (this.drag && this.drag.type === "node" && this.drag.moved && this.opts.editable) this.opts.onMove(this.drag.node);
      this.drag = null;
      if (this.link) { if (this.link.press) this.setConnectFrom(this.link.from); this.link = null; }
      if (this.pointers.size < 2) this.pinch = null;
      this.request();
    }
    /** Touch on a read-only graph: tapping a node pins its tooltip until the next tap. */
    pinTip(node, p) {
      this.pinned = this.pinned === node.id ? null : node.id;
      if (this.pinned) this.updateTip(node, p); else this.tip.style.opacity = "0";
    }
    onKey(e) {
      const key = e.key;
      if (key === "+" || key === "=") { this.zoom(1.15); e.preventDefault(); return; }
      if (key === "-" || key === "_") { this.zoom(1 / 1.15); e.preventDefault(); return; }
      if (key === "f" || key === "F") { this.fit(); e.preventDefault(); return; }
      if (key === "Escape") { this.link = null; if (this.connectFrom) { this.setConnectFrom(null); return; } this.sel = null; this.opts.onSelect(null); this.request(); return; }
      if ((key === "Delete" || key === "Backspace") && this.sel && this.opts.editable) { e.preventDefault(); this.opts.onDelete(this.sel); return; }
      if (key.startsWith("Arrow")) {
        e.preventDefault();
        const step = e.shiftKey ? 50 : 10;
        const dx = key === "ArrowLeft" ? -step : key === "ArrowRight" ? step : 0;
        const dy = key === "ArrowUp" ? -step : key === "ArrowDown" ? step : 0;
        const node = this.sel && this.sel.type === "node" ? this.nodes().find((n) => n.id === this.sel.id) : null;
        if (node && this.opts.editable) {
          const r = this.rect(node);
          node.position = { x: r.x + dx, y: r.y + dy };
          this.opts.onMove(node);
        } else { this.view.x -= dx * 2; this.view.y -= dy * 2; this.updateGrid(); }
        this.request();
      }
    }
    updateTip(node, p) {
      if (!node) { this.tip.style.opacity = "0"; return; }
      const incoming = incomingMap(this.nodes()).has(node.id);
      const state2 = this.play && this.play.states ? this.play.states[node.id] : null;
      fill(this.tip, h("b", null, node.title || node.id),
        h("div", { class: "muted" }, `${titleCase(nodeKindOf(node))} · ${triggerSummary(node, incoming)}`),
        node.text ? h("div", null, truncate(node.text, 180)) : null,
        isObject(node.outcome) ? h("div", null, `${node.outcome.label || ""} (${rewardText(node.outcome)})`) : null,
        state2 ? h("div", { style: { color: "#e2c21c" } }, `Activated week ${state2.activated}${state2.finished ? `, done week ${state2.finished}` : ""}`) : null);
      const tw = this.tip.offsetWidth || 260, th = this.tip.offsetHeight || 110;
      const x = clamp(p.sx + 16, 6, Math.max(6, this.w - tw - 6));
      const y = clamp(p.sy + 16, 6, Math.max(6, this.h - th - 6));
      this.tip.style.left = `${x}px`;
      this.tip.style.top = `${y}px`;
      this.tip.style.opacity = "1";
    }
    textWidth(ctx, text, font) {
      const key = font + text;
      let w = this.widths.get(key);
      if (w == null) { ctx.font = font; w = ctx.measureText(text).width; if (this.widths.size > 4000) this.widths.clear(); this.widths.set(key, w); }
      return w;
    }
    fitText(ctx, text, font, max) {
      const t = String(text);
      if (this.textWidth(ctx, t, font) <= max) return t;
      let lo = 0, hi = t.length;
      while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (this.textWidth(ctx, t.slice(0, mid) + "…", font) <= max) lo = mid; else hi = mid - 1; }
      return t.slice(0, lo) + "…";
    }
    draw(now) {
      const ctx = this.ctx;
      const nodes = this.nodes();
      this.refreshAuto();
      ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
      ctx.clearRect(0, 0, this.w, this.h);
      ctx.setTransform(this.dpr * this.view.k, 0, 0, this.dpr * this.view.k, this.dpr * this.view.x, this.dpr * this.view.y);
      const byId = new Map(nodes.map((n) => [n.id, n]));
      const rects = new Map(nodes.map((n) => [n.id, this.rect(n)]));
      const incoming = incomingMap(nodes);
      const focus = this.sel && this.sel.type === "node" ? this.sel.id : this.hover ? this.hover.id : null;
      const traversed = new Set();
      if (this.play && this.play.path) for (const step of this.play.path) if (step.week <= this.play.week) traversed.add(`${step.source}>${step.target}`);
      const motion = !reducedMotion();
      const tmp = { x: 0, y: 0 };
      // edges
      for (const n of nodes) {
        (n.edges || []).forEach((e, index) => {
          const target = e && byId.get(e.to);
          if (!target) return;
          const g = this.geometry(rects.get(n.id), rects.get(target.id));
          const color = KIND_COLOR[nodeKindOf(target)] || "#888";
          const selected = this.sel && this.sel.type === "edge" && this.sel.from === n.id && this.sel.index === index;
          const lit = selected || focus === n.id || focus === target.id;
          const walked = traversed.has(`${n.id}>${target.id}`);
          let alpha = lit ? 0.95 : 0.42;
          let width = lit ? 2.4 : 1.5;
          if (this.play) { alpha = walked ? 0.95 : 0.1; width = walked ? 3 : 1.2; }
          ctx.strokeStyle = selected ? "#e2c21c" : rgba(color, alpha);
          ctx.lineWidth = width;
          ctx.setLineDash(e.if ? [] : [7, 5]);
          if (selected || (this.play && walked)) { ctx.shadowColor = selected ? "rgba(226,194,28,0.8)" : rgba(color, 0.8); ctx.shadowBlur = 12; }
          ctx.beginPath();
          ctx.moveTo(g[0], g[1]);
          ctx.bezierCurveTo(g[2], g[3], g[4], g[5], g[6], g[7]);
          ctx.stroke();
          ctx.shadowBlur = 0;
          ctx.setLineDash([]);
          ctx.fillStyle = selected ? "#e2c21c" : rgba(color, Math.min(1, alpha + 0.1));
          ctx.beginPath(); ctx.moveTo(g[6], g[7]); ctx.lineTo(g[6] - 9, g[7] - 5); ctx.lineTo(g[6] - 9, g[7] + 5); ctx.closePath(); ctx.fill();
          if (motion && (!this.play || walked)) {
            const count = this.play ? 3 : lit ? 3 : 2;
            const seed = hashString(`${n.id}>${e.to}:${index}`);
            for (let i = 0; i < count; i++) {
              const u = ((now / 1000) * (this.play ? 0.45 : 0.28) + i / count + seed) % 1;
              StoryCanvas.bez(g, u, tmp);
              ctx.fillStyle = rgba(color, this.play ? 0.35 : lit ? 0.3 : 0.16);
              ctx.beginPath(); ctx.arc(tmp.x, tmp.y, 4.5, 0, Math.PI * 2); ctx.fill();
              ctx.fillStyle = this.play || lit ? "#fff6d0" : rgba(color, 0.8);
              ctx.beginPath(); ctx.arc(tmp.x, tmp.y, 1.8, 0, Math.PI * 2); ctx.fill();
            }
          }
          const label = e.label || (e.if ? condShort(e.if) : "otherwise");
          const window2 = (e.after ? ` ≥${e.after}w` : "") + (e.until != null ? ` ≤${e.until}w` : "");
          if ((this.play ? walked && this.view.k > 0.5 : lit || this.view.k >= 1.15)) {
            StoryCanvas.bez(g, 0.5, tmp);
            const font = "500 10.5px system-ui, sans-serif";
            const text = this.fitText(ctx, label + window2, font, 120);
            const tw = this.textWidth(ctx, text, font) + 14;
            ctx.fillStyle = selected ? "rgba(60,50,10,0.95)" : "rgba(22,22,22,0.92)";
            ctx.strokeStyle = selected ? "#e2c21c" : rgba(color, lit ? 0.6 : 0.3);
            ctx.lineWidth = 1;
            roundRect(ctx, tmp.x - tw / 2, tmp.y - 9, tw, 18, 9);
            ctx.fill(); ctx.stroke();
            ctx.fillStyle = lit || selected ? "#f3f3f3" : "#b5b5b5";
            ctx.font = font;
            ctx.textAlign = "center";
            ctx.fillText(text, tmp.x, tmp.y + 3.5);
            ctx.textAlign = "start";
          }
        });
      }
      // link preview
      if (this.link) {
        const from = rects.get(this.link.from);
        if (from) {
          ctx.strokeStyle = "rgba(226,194,28,0.9)";
          ctx.lineWidth = 2;
          ctx.setLineDash([6, 5]);
          ctx.beginPath();
          ctx.moveTo(from.x + from.w, from.y + from.h / 2);
          ctx.bezierCurveTo(from.x + from.w + 60, from.y + from.h / 2, this.link.x - 60, this.link.y, this.link.x, this.link.y);
          ctx.stroke();
          ctx.setLineDash([]);
        }
      }
      if (this.connectFrom && this.pointer && this.lastPointer === "mouse") {
        const from = rects.get(this.connectFrom);
        if (from) {
          ctx.strokeStyle = "rgba(226,194,28,0.75)";
          ctx.lineWidth = 2;
          ctx.setLineDash([6, 5]);
          ctx.beginPath();
          ctx.moveTo(from.x + from.w, from.y + from.h / 2);
          ctx.lineTo(this.pointer.x, this.pointer.y);
          ctx.stroke();
          ctx.setLineDash([]);
        }
      }
      // nodes
      for (const n of nodes) this.drawNode(ctx, n, rects.get(n.id), now, incoming.has(n.id), motion);
    }
    drawNode(ctx, n, r, now, hasIncoming, motion) {
      const kind = r.kind;
      const color = KIND_COLOR[kind] || "#9e9e9e";
      const selected = this.sel && this.sel.type === "node" && this.sel.id === n.id;
      const hovered = this.hover === n;
      const st = this.play && this.play.states ? this.play.states[n.id] : null;
      let alpha = 1;
      if (this.play) alpha = st ? 1 : 0.26;
      ctx.globalAlpha = alpha;
      if (st && st.status === "active" && motion) {
        const pulse = 0.5 + 0.5 * Math.sin(now / 260);
        ctx.shadowColor = rgba(color, 0.9);
        ctx.shadowBlur = 18 + 14 * pulse;
      } else if (st) { ctx.shadowColor = rgba(color, 0.7); ctx.shadowBlur = 14; }
      if (selected) { ctx.shadowColor = "rgba(226,194,28,0.85)"; ctx.shadowBlur = 22; }
      const radius = r.outcome ? r.h / 2 : 11;
      roundRect(ctx, r.x, r.y, r.w, r.h, radius);
      ctx.fillStyle = r.outcome ? (kind === "collapse" ? "#2a1514" : kind === "flourish" ? "#14261a" : "#222") : "#1d1d1d";
      ctx.fill();
      ctx.shadowBlur = 0;
      ctx.lineWidth = selected ? 2.2 : hovered ? 1.6 : 1.2;
      ctx.strokeStyle = selected ? "#e2c21c" : rgba(color, hovered ? 0.95 : 0.6);
      ctx.stroke();
      if (!r.outcome) {
        ctx.save();
        roundRect(ctx, r.x, r.y, r.w, r.h, radius);
        ctx.clip();
        ctx.fillStyle = color;
        ctx.fillRect(r.x, r.y, 4, r.h);
        const g = ctx.createLinearGradient(r.x, r.y, r.x, r.y + r.h);
        g.addColorStop(0, rgba(color, 0.14));
        g.addColorStop(1, rgba(color, 0));
        ctx.fillStyle = g;
        ctx.fillRect(r.x, r.y, r.w, r.h);
        ctx.restore();
      }
      this.drawGlyph(ctx, kind, r.x + r.w - 16, r.y + (r.outcome ? r.h / 2 : 15), color);
      const titleFont = "650 12.5px system-ui, sans-serif";
      const subFont = "500 10.5px system-ui, sans-serif";
      ctx.fillStyle = "#f2f2f2";
      ctx.font = titleFont;
      const left = r.outcome ? r.x + 16 : r.x + 13;
      if (r.outcome) {
        ctx.fillText(this.fitText(ctx, n.title || n.id, titleFont, r.w - 44), left, r.y + 16);
        ctx.font = subFont;
        ctx.fillStyle = kind === "collapse" ? "#ffb3aa" : "#a8e6b0";
        ctx.fillText(this.fitText(ctx, isObject(n.outcome) ? rewardText(n.outcome) : "outcome not set", subFont, r.w - 44), left, r.y + 29);
      } else {
        ctx.fillText(this.fitText(ctx, n.title || n.id, titleFont, r.w - 40), left, r.y + 19);
        ctx.font = subFont;
        ctx.fillStyle = isObject(n.trigger) ? "#e8d78a" : hasIncoming ? "#9a9a9a" : "#ef8a80";
        ctx.fillText(this.fitText(ctx, triggerSummary(n, hasIncoming), subFont, r.w - 30), left, r.y + 35);
      }
      const effects = (n.effects || []).length;
      if (effects && !r.outcome) {
        ctx.font = "600 9.5px system-ui, sans-serif";
        ctx.fillStyle = "#c9c9c9";
        ctx.textAlign = "right";
        ctx.fillText(`${effects} fx`, r.x + r.w - 9, r.y + r.h - 7);
        ctx.textAlign = "start";
      }
      if (st) {
        ctx.font = "700 9.5px system-ui, sans-serif";
        const label = `w${st.activated}`;
        const tw = this.textWidth(ctx, label, ctx.font) + 10;
        ctx.fillStyle = rgba(color, 0.95);
        roundRect(ctx, r.x + 6, r.y - 9, tw, 15, 7);
        ctx.fill();
        ctx.fillStyle = "#111";
        ctx.fillText(label, r.x + 11, r.y + 2);
      }
      if (this.connectFrom === n.id) {
        ctx.setLineDash([6, 4]);
        ctx.strokeStyle = "#e2c21c";
        ctx.lineWidth = 2;
        roundRect(ctx, r.x - 5, r.y - 5, r.w + 10, r.h + 10, radius + 5);
        ctx.stroke();
        ctx.setLineDash([]);
      }
      if (this.opts.editable) {
        const px = r.x + r.w, py = r.y + r.h / 2;
        ctx.fillStyle = "#121212";
        ctx.strokeStyle = hovered || selected ? "#e2c21c" : rgba(color, 0.8);
        ctx.lineWidth = 1.5;
        const port = (hovered || selected ? 6 : 4.5) * (COARSE ? 1.35 : 1);
        ctx.beginPath(); ctx.arc(px, py, port, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
        ctx.fillStyle = rgba(color, 0.7);
        ctx.beginPath(); ctx.arc(r.x, py, 3, 0, Math.PI * 2); ctx.fill();
      }
      ctx.globalAlpha = 1;
    }
    drawGlyph(ctx, kind, x, y, color) {
      ctx.fillStyle = color;
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.beginPath();
      if (kind === "crisis") { ctx.moveTo(x, y - 6); ctx.lineTo(x + 6, y + 5); ctx.lineTo(x - 6, y + 5); ctx.closePath(); ctx.fill(); }
      else if (kind === "branch") { ctx.moveTo(x, y - 6); ctx.lineTo(x + 6, y); ctx.lineTo(x, y + 6); ctx.lineTo(x - 6, y); ctx.closePath(); ctx.fill(); }
      else if (kind === "collapse") { ctx.moveTo(x - 5, y - 5); ctx.lineTo(x + 5, y + 5); ctx.moveTo(x + 5, y - 5); ctx.lineTo(x - 5, y + 5); ctx.stroke(); }
      else if (kind === "flourish") {
        for (let i = 0; i < 5; i++) { const a = -Math.PI / 2 + (i * 2 * Math.PI) / 5; ctx.lineTo(x + Math.cos(a) * 6.5, y + Math.sin(a) * 6.5); const b = a + Math.PI / 5; ctx.lineTo(x + Math.cos(b) * 2.8, y + Math.sin(b) * 2.8); }
        ctx.closePath(); ctx.fill();
      } else { ctx.arc(x, y, 4.5, 0, Math.PI * 2); ctx.fill(); }
    }
    destroy() {
      cancelAnimationFrame(this.raf);
      this.raf = 0;
      this.visible = false;
      this.cancelPress();
      this.ro.disconnect();
      this.unwatch();
      this.canvas.remove();
      this.tip.remove();
      this.hint.remove();
    }
  }
  function roundRect(ctx, x, y, w, h2, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h2, r);
    ctx.arcTo(x + w, y + h2, x, y + h2, r);
    ctx.arcTo(x, y + h2, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  // ----- story section
  renderers.story = (host) => {
    const wrap = h("div", { class: "story-wrap" });
    const panel = h("div", { class: "panel-body" });
    const aside = h("aside", { class: "card editor-panel", "aria-label": "Story node editor" }, panel);
    const kindButton = (kind, label) => h("button", { type: "button", class: "small kind-btn", title: `Add ${label.toLowerCase()} node`, onclick: () => addNode(kind) }, h("i", { style: { background: KIND_COLOR[kind] } }), `+ ${label}`);
    const connectButton = h("button", { type: "button", class: "small", "aria-pressed": "false", title: "Pick a node, then the node it leads to" }, "⟶ Connect");
    const overviewButton = h("button", { type: "button", class: "small sheet-only", title: "The story at a glance" }, "☰ Overview");
    const toolbar = h("div", { class: "story-toolbar" },
      kindButton("event", "Event"), kindButton("crisis", "Crisis"), kindButton("branch", "Branch"), kindButton("collapse", "Collapse"), kindButton("flourish", "Flourish"),
      h("span", { class: "spacer" }),
      connectButton,
      h("button", { type: "button", class: "small", title: "Arrange all nodes left to right", onclick: autoLayout }, "Auto-layout"),
      h("button", { type: "button", class: "small", title: "Fit the graph (F)", onclick: () => sc.fit() }, "Fit"),
      overviewButton);
    put(wrap, 
      h("div", { class: "story-zoom" }, h("button", { type: "button", "aria-label": "Zoom in", onclick: () => sc.zoom(1.2) }, "+"), h("button", { type: "button", "aria-label": "Zoom out", onclick: () => sc.zoom(1 / 1.2) }, "−")),
      h("div", { class: "story-legend" }, ["event", "crisis", "branch", "collapse", "flourish"].map((k) => h("span", null, h("i", { style: { background: KIND_COLOR[k] } }), titleCase(k))),
        h("span", null, "— conditional"), h("span", null, "- - otherwise")),
      h("div", { class: "story-hint" },
        h("span", { class: "hint-mouse" }, "Drag from a node's right port (or shift-drag) to connect · double-click to add · scroll to zoom · drag the background to pan"),
        h("span", { class: "hint-touch" }, "Long-press a node or drag its port to connect · double-tap to add · pinch to zoom")));
    put(host, sectionHead("Narrative", "Story graph",
      "Authored situations layered on the emergent society. Nodes activate at a week, by chance or when a condition on the society holds; their effects apply, then the first edge whose window and condition hold moves the story on. Outcome nodes end the run in collapse or award a flourishing bonus. The steward never sees the graph — only its consequences."),
    h("div", { class: "story-layout" }, h("div", { class: "card story-card" }, toolbar, wrap), aside));
    const sheet = makeSheet(aside, { title: "Story" });
    const stopFades = edgeFades(toolbar);
    const sc = new StoryCanvas(wrap, {
      editable: true,
      getNodes: storyNodes,
      onSelect: (sel, info) => {
        applySelection(sel);
        drawPanel();
        if (!sel) sheet.close();
        else if (!info || info.open !== false) sheet.open();
      },
      onMove: () => touch("story-move"),
      onLink: (from, to) => linkNodes(from, to),
      onAdd: (pos) => addNode("event", pos),
      onDelete: (sel) => deleteSelection(sel),
    });
    function applySelection(sel) {
      state.sel.node = sel && sel.type === "node" ? sel.id : sel && sel.type === "edge" ? sel.from : null;
      state.sel.edge = sel && sel.type === "edge" ? { from: sel.from, index: sel.index } : null;
    }
    function currentSel() {
      if (state.sel.edge) return { type: "edge", from: state.sel.edge.from, index: state.sel.edge.index };
      if (state.sel.node) return { type: "node", id: state.sel.node };
      return null;
    }
    connectButton.addEventListener("click", () => {
      const on = connectButton.getAttribute("aria-pressed") !== "true";
      connectButton.setAttribute("aria-pressed", String(on));
      sc.setConnectMode(on);
    });
    overviewButton.addEventListener("click", () => { sc.select(null); applySelection(null); drawPanel(); sheet.open(); });
    function viewCenter() {
      return { x: Math.round(((sc.w / 2 - sc.view.x) / sc.view.k - NODE_W / 2) / 10) * 10, y: Math.round(((sc.h / 2 - sc.view.y) / sc.view.k - NODE_H / 2) / 10) * 10 };
    }
    function addNode(kind, pos, from) {
      const nodes = storyNodes();
      if (nodes.length >= 80) { toast("A story can hold at most 80 nodes.", "bad"); return null; }
      const taken = new Set(nodes.map((n) => n.id));
      const id = uniqueId(kind === "collapse" || kind === "flourish" ? kind : kind, taken);
      const node = { id, title: { event: "New event", crisis: "New crisis", branch: "Branch", collapse: "Collapse", flourish: "Flourishing" }[kind], position: pos || viewCenter() };
      if (kind === "collapse") { node.kind = "outcome"; node.outcome = { type: "collapse", label: "The society collapses" }; }
      else if (kind === "flourish") { node.kind = "outcome"; node.outcome = { type: "flourish", label: "The society flourishes", reward: { D5: 20 } }; }
      else { node.kind = kind; if (!from && kind !== "branch") node.trigger = { at: Math.min(ticks(), 10) }; }
      nodes.push(node);
      if (from) {
        const source = nodes.find((n) => n.id === from);
        if (source) { if (!Array.isArray(source.edges)) source.edges = []; if (source.edges.length < 8) source.edges.push({ to: id }); }
      }
      touch("story");
      sc.select({ type: "node", id });
      applySelection({ type: "node", id });
      drawPanel();
      sheet.open();
      return node;
    }
    function linkNodes(from, to) {
      const source = storyNodes().find((n) => n.id === from);
      if (!source) return;
      if (!Array.isArray(source.edges)) source.edges = [];
      if (source.edges.length >= 8) { toast("A node can have at most 8 edges.", "bad"); return; }
      source.edges.push({ to });
      touch("story");
      const sel = { type: "edge", from, index: source.edges.length - 1 };
      sc.select(sel);
      applySelection(sel);
      drawPanel();
      toast(`Edge ${from} → ${to} added. Give it a condition or leave it as “otherwise”.`, "good");
    }
    async function deleteSelection(sel) {
      if (!sel) return;
      if (sel.type === "edge") {
        const node = storyNodes().find((n) => n.id === sel.from);
        if (node && Array.isArray(node.edges)) node.edges.splice(sel.index, 1);
        touch("story");
        const next = { type: "node", id: sel.from };
        sc.select(next);
        applySelection(next);
        drawPanel();
        return;
      }
      const nodes = storyNodes();
      const index = nodes.findIndex((n) => n.id === sel.id);
      if (index < 0) return;
      const node = nodes[index];
      const incoming = incomingMap(nodes).get(node.id) || [];
      if (((node.effects || []).length || (node.edges || []).length || incoming.length)
        && !(await confirmDialog({ title: `Delete “${node.title || node.id}”?`, text: `${(node.effects || []).length} effect(s), ${(node.edges || []).length} outgoing and ${incoming.length} incoming edge(s) are removed with it.`, ok: "Delete node", danger: true }))) return;
      nodes.splice(index, 1);
      for (const n of nodes) if (Array.isArray(n.edges)) n.edges = n.edges.filter((e) => !e || e.to !== node.id);
      touch("story");
      sc.select(null);
      applySelection(null);
      drawPanel();
      sheet.close();
    }
    function autoLayout() {
      const pos = layeredLayout(storyNodes());
      for (const n of storyNodes()) { const p = pos.get(n.id); if (p) n.position = { x: p.x, y: p.y }; }
      touch("story-move");
      sc.fit();
    }
    function drawPanel() {
      fill(panel);
      const nodes = storyNodes();
      const index = nodes.findIndex((n) => n.id === state.sel.node);
      if (index < 0) {
        state.sel.node = null; state.sel.edge = null;
        sheet.setTitle("The story at a glance");
        put(panel, ...storyOverview(nodes, (id) => { const sel = { type: "node", id }; sc.select(sel); applySelection(sel); sc.center(id); drawPanel(); }));
        return;
      }
      const node = nodes[index];
      sheet.setTitle(state.sel.edge ? "Edge" : node.title || node.id);
      if (state.sel.edge && Array.isArray(node.edges) && node.edges[state.sel.edge.index]) {
        put(panel, ...edgePanel(node, index, state.sel.edge.index, {
          onChange: () => { touch("story"); sc.request(); },
          openNode: (id) => { const sel = { type: "node", id }; sc.select(sel); applySelection(sel); sc.center(id); drawPanel(); },
          redraw: drawPanel,
          remove: () => deleteSelection({ type: "edge", from: node.id, index: state.sel.edge.index }),
        }));
        return;
      }
      state.sel.edge = null;
      put(panel, ...nodePanel(node, index, {
        onChange: () => { touch("story"); sc.request(); },
        redraw: drawPanel,
        openNode: (id) => { const sel = { type: "node", id }; sc.select(sel); applySelection(sel); sc.center(id); drawPanel(); },
        openEdge: (from, i) => { const sel = { type: "edge", from, index: i }; sc.select(sel); applySelection(sel); drawPanel(); },
        remove: () => deleteSelection({ type: "node", id: node.id }),
        next: () => { const r = sc.rect(node); addNode("event", { x: r.x + 250, y: r.y }, node.id); sc.request(); },
        center: () => sc.center(node.id),
        renamed: (from, to) => { state.sel.node = to; sc.select({ type: "node", id: to }); },
      }));
    }
    const initial = currentSel();
    if (initial) { sc.select(initial); if (initial.type === "node" || initial.type === "edge") setTimeout(() => sc.center(initial.type === "node" ? initial.id : initial.from), 30); }
    drawPanel();
    return () => { stopFades.dispose(); sheet.destroy(); sc.destroy(); };
  };
  function storyOverview(nodes, open) {
    const incoming = incomingMap(nodes);
    const unreachable = nodes.filter((n) => !isObject(n.trigger) && !incoming.has(n.id));
    const byKind = {};
    nodes.forEach((n) => { (byKind[nodeKindOf(n)] = byKind[nodeKindOf(n)] || []).push(n); });
    const edges = nodes.reduce((a, n) => a + (n.edges || []).length, 0);
    const late = nodes.filter((n) => n.trigger && Number.isInteger(n.trigger.at) && n.trigger.at > ticks());
    return [
      h("div", { class: "panel-title" }, h("h3", null, "The story at a glance")),
      h("div", { class: "stats" }, h("div", { class: "stat" }, h("strong", null, String(nodes.length)), h("span", null, "nodes")),
        h("div", { class: "stat" }, h("strong", null, String(edges)), h("span", null, "edges")),
        h("div", { class: "stat" }, h("strong", null, String((byKind.collapse || []).length)), h("span", null, "collapses")),
        h("div", { class: "stat" }, h("strong", null, String((byKind.flourish || []).length)), h("span", null, "flourishes"))),
      unreachable.length || late.length ? h("div", { class: "callout bad", style: { marginTop: "14px" } },
        unreachable.length ? h("div", null, `Never reachable (no trigger, no incoming edge): ${unreachable.map((n) => n.id).join(", ")}`) : null,
        late.length ? h("div", null, `Triggered after the last week: ${late.map((n) => n.id).join(", ")}`) : null) : null,
      h("div", { class: "callout info", style: { marginTop: "14px" } },
        h("div", null, COARSE ? "Tap a node to edit its trigger, effects and edges. Long-press a node (or use Connect) and pick another node to add an edge; double-tap empty space to add an event."
          : "Select a node to edit its trigger, effects and edges. Drag from the round port on a node's right edge to another node to add an edge; double-click empty space to add an event."),
        h("div", { style: { marginTop: "6px" } }, "Edges are tried in order every week: the first whose window [after, until] contains the node's age and whose condition holds fires. An edge without a condition is the “otherwise” path.")),
      ...["event", "crisis", "branch", "flourish", "collapse", "outcome"].filter((k) => byKind[k]).map((k) => foldCard(titleCase(k), String(byKind[k].length), k === "crisis" || k === "collapse",
        h("div", { class: "chips" }, byKind[k].map((n) => h("button", { type: "button", class: "small", onclick: () => open(n.id) }, h("i", { class: "swatch", style: { background: KIND_COLOR[k], color: KIND_COLOR[k] } }), n.title || n.id))))),
    ];
  }
  function nodePanel(node, index, actions) {
    const base = `story.nodes[${index}]`;
    const kind = nodeKindOf(node);
    const color = KIND_COLOR[kind] || "#9e9e9e";
    const changed = () => actions.onChange();
    const title = h("h3", null, node.title || node.id);
    const idInput = h("input", { type: "text", value: node.id, class: "mono", "data-path": `${base}.id` });
    idInput.addEventListener("change", () => {
      const next = idInput.value.trim();
      if (next === node.id) return;
      if (!IDENT.test(next) || storyNodes().some((n) => n !== node && n.id === next)) { idInput.classList.add("invalid"); toast("Node ids must be unique identifiers.", "bad"); idInput.value = node.id; return; }
      const from = node.id;
      for (const n of storyNodes()) for (const e of n.edges || []) if (e && e.to === from) e.to = next;
      node.id = next;
      actions.renamed(from, next);
      changed();
    });
    const kindControl = F.segmented({ label: "Kind", value: node.kind === "outcome" || node.outcome ? "outcome" : node.kind || "event", path: `${base}.kind`,
      options: [["event", "Event"], ["crisis", "Crisis"], ["branch", "Branch"], ["outcome", "Outcome"]],
      onChange: (v) => {
        if (v === "outcome") { node.kind = "outcome"; if (!isObject(node.outcome)) node.outcome = { type: "flourish", label: node.title || node.id, reward: {} }; }
        else { node.kind = v; delete node.outcome; }
        changed();
        actions.redraw();
      } });
    const incoming = incomingMap(storyNodes()).get(node.id) || [];
    const parts = [
      h("div", { class: "panel-title" },
        h("span", { class: "node-kind-pill", style: { background: rgba(color, 0.16), color, border: `1px solid ${rgba(color, 0.5)}` } }, kind),
        h("div", { style: { flex: 1, minWidth: 0 } }, title, h("p", { class: "tiny muted mono" }, node.id)),
        h("button", { type: "button", class: "icon ghost", title: "Center in view", "aria-label": "Center in view", onclick: actions.center }, "◎"),
        h("button", { type: "button", class: "icon ghost danger", title: "Delete node", "aria-label": "Delete node", onclick: actions.remove }, "✕")),
      h("div", { class: "form-grid two-col" },
        F.text({ label: "Title", value: node.title || "", max: 80, path: `${base}.title`, onChange: (v) => { node.title = v; title.textContent = v || node.id; changed(); } }),
        field("Id", idInput),
        h("div", { class: "span-all" }, kindControl),
        h("div", { class: "span-all" }, F.area({ label: "Text (said when it happens)", value: node.text || "", max: 400, rows: 3, path: `${base}.text`,
          hint: "Added to the public chronicle the steward reads.", onChange: (v) => { node.text = v; changed(); } }))),
      foldCard("Trigger", triggerSummary(node, incoming.length > 0), true, triggerEditor(node, base, changed)),
    ];
    if (node.kind === "outcome" || node.outcome) parts.push(foldCard("Outcome", isObject(node.outcome) ? node.outcome.type : "not set", true, outcomeEditor(node, base, changed, actions.redraw)));
    parts.push(
      foldCard("Effects", String((node.effects || []).length), true, effectsEditor(node, base, changed)),
      foldCard("Outgoing edges", String((node.edges || []).length), true, edgesEditor(node, base, changed, actions)),
      foldCard("Incoming edges", String(incoming.length), incoming.length > 0,
        incoming.length ? h("div", { class: "chips" }, incoming.map(({ from, index: i }) => h("button", { type: "button", class: "small", onclick: () => actions.openEdge(from, i) }, `${from} →`)))
          : h("p", { class: "small muted" }, isObject(node.trigger) ? "None: this node starts from its own trigger." : "None, and no trigger: this node can never happen.")),
      foldCard("Author notes", null, Boolean(node.notes), F.area({ label: "Notes (not used by the engine)", value: node.notes || "", max: 600, rows: 3, path: `${base}.notes`, onChange: (v) => { node.notes = v; changed(); } })),
      h("div", { class: "row", style: { marginTop: "16px" } }, h("button", { type: "button", class: "small", onclick: actions.next, disabled: (node.edges || []).length >= 8 }, "+ Next node →"), h("span", { class: "spacer" }),
        h("button", { type: "button", class: "small danger", onclick: actions.remove }, "Delete node")));
    return parts;
  }
  function triggerEditor(node, base, changed) {
    const box = h("div");
    const draw = () => {
      const t = node.trigger;
      const mode = !isObject(t) ? "none" : "at" in t ? "at" : "chance" in t ? "chance" : "when" in t ? "when" : "none";
      const keep = (obj) => { if (isObject(t)) { if (t.from != null) obj.from = t.from; if (t.to != null) obj.to = t.to; } return obj; };
      const parts = [F.segmented({ label: "Starts", value: mode, path: `${base}.trigger`,
        options: [["none", "Via edges"], ["at", "At week"], ["chance", "Chance"], ["when", "Condition"]],
        onChange: (m) => {
          if (m === "none") delete node.trigger;
          else if (m === "at") node.trigger = { at: isObject(t) && Number.isInteger(t.from) ? t.from : Math.min(ticks(), 10) };
          else if (m === "chance") node.trigger = keep({ chance: 0.2 });
          else node.trigger = keep({ when: isObject(t) && t.when ? t.when : defaultLeaf() });
          changed();
          draw();
        } })];
      if (mode === "at") parts.push(F.number({ label: "Week", value: t.at, min: 1, max: ticks(), integer: true, path: `${base}.trigger.at`, onChange: (v) => { t.at = v; changed(); } }));
      if (mode === "chance") parts.push(F.range({ label: "Chance each week", value: t.chance, min: 0, max: 1, step: 0.01, path: `${base}.trigger.chance`, onChange: (v) => { t.chance = v ?? 0; changed(); } }));
      if (mode === "when") parts.push(group("Fires the first week this holds", conditionEditor(t, "when", `${base}.trigger.when`, changed)));
      if (mode === "chance" || mode === "when") parts.push(windowFields(t, `${base}.trigger`, changed));
      parts.push(h("p", { class: "hint" }, {
        none: "Only an edge from another node can activate it.",
        at: "Activates at the start of that week.",
        chance: "Rolled every week inside the window (seeded, reproducible).",
        when: "Checked every week inside the window on the society's state.",
      }[mode]));
      fill(box, ...parts);
    };
    draw();
    return box;
  }
  function windowFields(t, base, changed) {
    const setWeek = (key) => (v) => { if (v === undefined) delete t[key]; else t[key] = v; changed(); };
    return h("div", { class: "form-grid two-col" },
      F.number({ label: "From week", value: t.from ?? "", min: 1, max: ticks(), integer: true, placeholder: "1", path: `${base}.from`, onChange: setWeek("from") }),
      F.number({ label: "To week", value: t.to ?? "", min: 1, max: ticks(), integer: true, placeholder: String(ticks()), path: `${base}.to`, onChange: setWeek("to") }));
  }
  function outcomeEditor(node, base, changed, redraw) {
    if (!isObject(node.outcome)) node.outcome = { type: "flourish", label: node.title || node.id, reward: {} };
    const o = node.outcome;
    const rewards = h("div", { class: "form-grid reward-grid" }, Object.entries(cat().domains).map(([d, info]) => F.number({
      label: `${d} ${info.name}`, value: isObject(o.reward) && o.reward[d] != null ? o.reward[d] : "", placeholder: "0", path: `${base}.outcome.reward.${d}`,
      onChange: (v) => { if (!isObject(o.reward)) o.reward = {}; if (!v) delete o.reward[d]; else o.reward[d] = v; changed(); } })));
    return h("div", null,
      F.segmented({ label: "Type", value: o.type, path: `${base}.outcome.type`, options: [["collapse", "Collapse"], ["flourish", "Flourish"]],
        onChange: (v) => { o.type = v; if (v === "collapse") delete o.reward; else if (!isObject(o.reward)) o.reward = {}; changed(); redraw(); } }),
      h("div", { style: { height: "10px" } }),
      F.text({ label: "Label", value: o.label || "", max: 120, path: `${base}.outcome.label`, onChange: (v) => { o.label = v; changed(); } }),
      o.type === "flourish"
        ? h("div", null, h("h4", null, "Reward (domain units, then × exchange rate)"), rewards)
        : h("p", { class: "callout bad", style: { marginTop: "10px" } }, "Reaching this node ends the run immediately: the score becomes 0 and accumulated points are forfeited."));
  }

  // ----- effects
  function effectTemplate(type) {
    const p = people();
    const first = p[0] ? p[0].id : "r01";
    const second = p[1] ? p[1].id : first;
    const takenConflicts = new Set(conflictIds());
    const takenProbes = new Set(storyNodes().flatMap((n) => (n.effects || []).filter((e) => e && e.type === "probe").map((e) => e.id)));
    switch (type) {
      case "chronicle": return { type, text: "Something happened in the village." };
      case "message": return { type, from: first, text: "Steward, we need to talk." };
      case "modifier": return { type, key: "farm", factor: 0.5, weeks: 4 };
      case "resource": return { type, target: "granary", delta: -10 };
      case "resident": return { type, who: "all", field: "stress", delta: 0.05 };
      case "concern": return { type, who: first, kind: "fear", intensity: 0.6, cause: "" };
      case "illness": return { type, who: "random:2" };
      case "death": return { type, who: first };
      case "depart": return { type, who: first };
      case "arrive": return { type, residents: [newcomerTemplate()] };
      case "conflict": return { type, id: uniqueId("conflict-", takenConflicts), a: first, b: second, kind: "property", at_fault: null, intensity: 0.3 };
      case "convention_shift": return { type, domain: conventionDomains()[0] || "trade", variant: "sealed_bid" };
      case "merchant": return { type, id: (merchants()[0] || {}).id || "merchant", field: "active", value: false };
      case "policy": return { type, key: "tax", value: 0.1 };
      case "probe": return { type, id: uniqueId("probe-", takenProbes), fact: (facts()[0] || {}).id || "fact", question: "How likely is it that this is true?" };
      case "epidemic": return { type, weeks: 4, rate: 0.05 };
      default: return { type };
    }
  }
  function newcomerTemplate() {
    const taken = allIds();
    newcomers().forEach(({ spec }) => taken.add(spec.id));
    return { id: uniqueId("n", taken), name: "New arrival", age: 30, role: "farmer", archetype: "newcomer",
      state: { coins: 5, food: 3, trust: 0.45, stress: 0.5 }, ...(factions()[0] ? { faction: factions()[factions().length - 1].id } : {}) };
  }
  function effectsEditor(node, base, changed) {
    const box = h("div");
    const draw = () => {
      const list = Array.isArray(node.effects) ? node.effects : [];
      const cards = list.map((e, j) => {
        const path = `${base}.effects[${j}]`;
        const body = h("div");
        const drawBody = () => fill(body, ...effectFields(node, e, path, changed, drawBody));
        drawBody();
        const typeSelect = F.select({ title: "Effect type", value: e.type, path, options: Object.keys(cat().effects).map((t) => [t, titleCase(t)]),
          onChange: (t) => { list[j] = effectTemplate(t); changed(); draw(); } });
        const move = (d) => () => { const k = j + d; if (k < 0 || k >= list.length) return; [list[j], list[k]] = [list[k], list[j]]; changed(); draw(); };
        return h("div", { class: "effect-card" },
          h("header", null, typeSelect, h("span", { class: "grow" }),
            h("button", { type: "button", class: "icon ghost", "aria-label": "Move up", disabled: j === 0, onclick: move(-1) }, "↑"),
            h("button", { type: "button", class: "icon ghost", "aria-label": "Move down", disabled: j === list.length - 1, onclick: move(1) }, "↓"),
            h("button", { type: "button", class: "icon ghost", "aria-label": "Remove effect", onclick: () => { list.splice(j, 1); changed(); draw(); } }, "✕")),
          h("p", { class: "help" }, (cat().effects[e.type] || {}).help || "Unknown effect type."), body);
      });
      const add = F.select({ title: "Add an effect", value: "", options: [["", "+ Add effect…"]].concat(Object.keys(cat().effects).map((t) => [t, `${titleCase(t)} — ${cat().effects[t].help}`])),
        onChange: (t) => { if (!t) return; if (list.length >= 24) { toast("At most 24 effects per node.", "bad"); return; } list.push(effectTemplate(t)); node.effects = list; changed(); draw(); } });
      fill(box, ...cards, add);
    };
    draw();
    return box;
  }
  function whoField(label, value, path, onChange) {
    const random = typeof value === "string" && value.startsWith("random:");
    const count = h("input", { type: "number", min: 1, max: 48, step: 1, value: random ? Number(value.slice(7)) || 1 : 2, "aria-label": "How many random residents", style: { width: "72px" } });
    const options = [
      { group: "Groups", options: [["all", "Everyone present"], ["locals", "Locals"], ["newcomers", "Newcomers"]] },
      { group: "Residents", options: people().map((p) => [p.id, `${p.name || p.id} · ${p.id}`]) },
      { group: "Factions", options: factions().map((f) => [`faction:${f.id}`, `Faction: ${f.name || f.id}`]) },
      { group: "Roles", options: Object.keys(cat().roles).map((r) => [`role:${r}`, `Role: ${r}`]) },
      { group: "Random", options: [["random", "Random residents…"]] },
    ];
    const box = h("div", { class: "row", style: { flexWrap: "nowrap", gap: "6px" } });
    const select = F.select({ title: label, value: random ? "random" : value, options, path,
      onChange: (v) => { count.hidden = v !== "random"; onChange(v === "random" ? `random:${Number(count.value) || 1}` : v); } });
    count.hidden = !random;
    count.addEventListener("input", () => onChange(`random:${Math.max(1, Math.round(Number(count.value) || 1))}`));
    put(box, select, count);
    return group(label, box);
  }
  function effectFields(node, e, path, changed, redraw) {
    const set = (key) => (v) => { if (v === undefined || v === "") delete e[key]; else e[key] = v; changed(); };
    const num = (label, key, opts = {}) => F.number(Object.assign({ label, value: e[key] ?? "", path: `${path}.${key}`, onChange: set(key) }, opts));
    const text = (label, key, max, opts = {}) => F.text(Object.assign({ label, value: e[key] ?? "", max, path: `${path}.${key}`, onChange: set(key) }, opts));
    const area = (label, key, max) => F.area({ label, value: e[key] ?? "", max, rows: 2, path: `${path}.${key}`, onChange: set(key) });
    const grid = (...children) => [h("div", { class: "form-grid" }, children)];
    const span = (child) => h("div", { class: "span-all" }, child);
    const datalist = (id, values) => h("datalist", { id }, values.map((v) => h("option", { value: v })));
    switch (e.type) {
      case "chronicle": return grid(span(area("Text", "text", 400)));
      case "message": return grid(span(F.select({ label: "From", value: e.from, path: `${path}.from`, options: peopleOptions(), onChange: set("from") })), span(area("Message", "text", 400)));
      case "modifier": {
        const listId = `mods-${path.replace(/\W/g, "")}`;
        const shockId = `shock-${path.replace(/\W/g, "")}`;
        return [datalist(listId, MODIFIER_KEYS), datalist(shockId, knownShocks()), h("div", { class: "form-grid" },
          F.text({ label: "Multiplies", value: e.key || "", path: `${path}.key`, mono: true, onChange: set("key") }),
          F.range({ label: "Factor", value: e.factor ?? 1, min: 0, max: 3, step: 0.05, path: `${path}.factor`, onChange: set("factor") }),
          num("Weeks", "weeks", { min: 1, max: 240, integer: true }),
          F.text({ label: "Shock name (optional)", value: e.shock || "", path: `${path}.shock`, mono: true, hint: "Lets merchants and prepared believers react.", onChange: set("shock") }))].map((el) => {
          el.querySelectorAll && el.querySelectorAll("input[type=text]").forEach((inp, i) => inp.setAttribute("list", i === 0 ? listId : shockId));
          return el;
        });
      }
      case "resource": return grid(F.select({ label: "Store", value: e.target, path: `${path}.target`, options: ["granary", "treasury", "medicine", "tools", "commons"].map((t) => [t, titleCase(t)]), onChange: set("target") }),
        num("Change (+/−)", "delta", { step: 1 }));
      case "resident": return grid(span(whoField("Who", e.who, `${path}.who`, set("who"))),
        F.select({ label: "Field", value: e.field, path: `${path}.field`, options: ["health", "stress", "grievance", "trust", "loneliness", "coins", "food"].map((f) => [f, titleCase(f)]), onChange: (v) => { set("field")(v); redraw(); } }),
        num("Change (+/−)", "delta", { step: ["health", "coins", "food"].includes(e.field) ? 1 : 0.01, hint: ["health"].includes(e.field) ? "Health is 0–100." : ["coins", "food"].includes(e.field) ? "Absolute amount." : "Shares are 0–1: ±0.05 is noticeable." }));
      case "concern": return grid(span(whoField("Who", e.who, `${path}.who`, set("who"))),
        F.select({ label: "Concern", value: e.kind, path: `${path}.kind`, options: Object.entries(cat().concerns).map(([k, t]) => [k, `${titleCase(k)} — ${t}`]), onChange: set("kind") }),
        F.range({ label: "Intensity", value: e.intensity ?? 0.6, path: `${path}.intensity`, onChange: set("intensity") }),
        span(text("Cause", "cause", 200)));
      case "illness": case "death": case "depart": return grid(span(whoField("Who", e.who, `${path}.who`, set("who"))));
      case "arrive": return arriveEditor(node, e, path, changed);
      case "conflict": {
        const parties = [[null, "Nobody"], [e.a, `A · ${personLabel(e.a)}`], [e.b, `B · ${personLabel(e.b)}`]];
        return grid(text("Conflict id", "id", 64, { mono: true }),
          F.select({ label: "Kind", value: e.kind, path: `${path}.kind`, options: cat().conflict_kinds.map((k) => [k, titleCase(k)]), onChange: set("kind") }),
          F.select({ label: "Party A", value: e.a, path: `${path}.a`, options: peopleOptions(), onChange: (v) => { set("a")(v); redraw(); } }),
          F.select({ label: "Party B", value: e.b, path: `${path}.b`, options: peopleOptions(), onChange: (v) => { set("b")(v); redraw(); } }),
          F.select({ label: "At fault", value: e.at_fault ?? null, path: `${path}.at_fault`, options: parties, onChange: (v) => { e.at_fault = v; changed(); } }),
          F.range({ label: "Intensity", value: e.intensity ?? 0.3, path: `${path}.intensity`, onChange: set("intensity") }));
      }
      case "convention_shift": {
        const listId = `conv-${path.replace(/\W/g, "")}`;
        const domain = F.text({ label: "Domain", value: e.domain || "", mono: true, path: `${path}.domain`, onChange: set("domain") });
        domain.querySelector("input").setAttribute("list", listId);
        return [datalist(listId, conventionDomains()), h("div", { class: "form-grid" }, domain,
          text("New required variant", "variant", 64, { mono: true }),
          span(text("Public hint (optional)", "hint", 400)),
          span(F.toggle({ label: "Merchants switch protocol too", checked: e.merchants !== false, onChange: (v) => { if (v) delete e.merchants; else e.merchants = false; changed(); } })))];
      }
      case "merchant": {
        const value = e.field === "active" ? F.toggle({ label: "Active", checked: Boolean(e.value), onChange: (v) => { e.value = v; changed(); } })
          : e.field === "protocol" ? F.text({ label: "Protocol", value: e.value ?? "", mono: true, onChange: (v) => { e.value = v; changed(); } })
            : F.number({ label: "Price factor", value: e.value ?? 1, min: 0, max: 20, step: 0.05, onChange: (v) => { e.value = v ?? 1; changed(); } });
        return grid(F.select({ label: "Merchant", value: e.id, path: `${path}.id`, options: merchants().map((m) => [m.id, m.name || m.id]), onChange: set("id") }),
          F.select({ label: "Field", value: e.field, path: `${path}.field`, options: [["active", "Active"], ["protocol", "Protocol"], ["price_factor", "Price factor"]],
            onChange: (v) => { e.field = v; e.value = v === "active" ? false : v === "protocol" ? "haggle" : 1.2; changed(); redraw(); } }), span(value));
      }
      case "policy": {
        const spec = cat().policies[e.key];
        return grid(span(F.select({ label: "Policy", value: e.key, path: `${path}.key`, options: Object.entries(cat().policies).map(([k, p]) => [k, p.label]),
          onChange: (v) => { e.key = v; e.value = cat().policies[v].default; changed(); redraw(); } })),
        span(spec ? policyField(e.key, spec, e.value, `${path}.value`, (v) => { e.value = v; changed(); }) : h("p", { class: "muted" }, "Unknown policy")));
      }
      case "probe": return grid(text("Probe id", "id", 64, { mono: true }),
        F.select({ label: "Fact", value: e.fact, path: `${path}.fact`, options: facts().map((f) => [f.id, truncate(f.statement || f.id, 60)]), onChange: set("fact") }),
        span(area("Question to the steward", "question", 300)));
      case "epidemic": return grid(num("Weeks", "weeks", { min: 1, max: 240, integer: true }),
        F.range({ label: "Extra weekly illness risk", value: e.rate ?? 0.05, min: 0, max: 1, step: 0.01, path: `${path}.rate`, onChange: set("rate") }));
      default: return [h("p", { class: "muted" }, "No fields.")];
    }
  }
  function arriveEditor(node, e, path, changed) {
    if (!Array.isArray(e.residents)) e.residents = [];
    const box = h("div");
    const draw = () => {
      const items = e.residents.map((spec, k) => {
        const summaryName = h("span", null, `${spec.name || spec.id} · ${spec.id}`);
        const body = h("div", { class: "fold-body" }, ...residentForm(spec, `story.${node.id}.arrive[${k}]`, {
          local: false, onIdentity: () => { summaryName.textContent = `${spec.name || spec.id} · ${spec.id}`; changed(); } }),
        h("div", { class: "row", style: { marginTop: "10px" } }, h("span", { class: "spacer" }),
          h("button", { type: "button", class: "small danger", disabled: e.residents.length <= 1, onclick: () => { e.residents.splice(k, 1); changed(); draw(); } }, "Remove newcomer")));
        return h("details", { class: "fold" }, h("summary", null, h("span", { class: "swatch", style: { background: factionColor(spec.faction), color: factionColor(spec.faction) } }), summaryName), body);
      });
      fill(box, ...items, h("button", { type: "button", class: "small", disabled: e.residents.length >= 12, onclick: () => { e.residents.push(newcomerTemplate()); changed(); draw(); } }, "+ Add newcomer"),
        h("p", { class: "hint" }, "Newcomers arrive with their own conventions and only belong if contact is built (Allport 1954). Arrivals are tied to each other at 0.7."));
    };
    draw();
    return [box];
  }

  // ----- edges
  function edgesEditor(node, base, changed, actions) {
    const box = h("div");
    const draw = () => {
      const list = Array.isArray(node.edges) ? node.edges : [];
      fill(box, ...list.map((edge, j) => edgeCard(node, base, j, changed, draw, actions)),
        h("button", { type: "button", class: "small", disabled: list.length >= 8, onclick: () => {
          const other = storyNodes().find((n) => n.id !== node.id);
          list.push({ to: other ? other.id : node.id });
          node.edges = list;
          changed();
          draw();
        } }, "+ Add edge"),
        h("p", { class: "hint" }, "Tried in order each week: the first edge whose window contains the node's age (weeks since activation) and whose condition holds fires. No condition = otherwise."));
    };
    draw();
    return box;
  }
  function edgeCard(node, base, j, changed, redraw, actions) {
    const edge = node.edges[j];
    const path = `${base}.edges[${j}]`;
    const list = node.edges;
    const target = storyNodes().find((n) => n.id === edge.to);
    const color = KIND_COLOR[nodeKindOf(target)] || "#9e9e9e";
    const move = (d) => () => { const k = j + d; if (k < 0 || k >= list.length) return; [list[j], list[k]] = [list[k], list[j]]; changed(); redraw(); };
    const condBox = h("div");
    const drawCond = () => {
      const has = isObject(edge.if);
      fill(condBox, F.toggle({ label: has ? "Only if…" : "No condition (otherwise)", checked: has, path: `${path}.if`,
        onChange: (on) => { if (on) edge.if = defaultLeaf(); else delete edge.if; changed(); drawCond(); } }),
      has ? conditionEditor(edge, "if", `${path}.if`, changed) : null);
    };
    drawCond();
    return h("div", { class: "edge-card", style: { borderLeft: `3px solid ${color}` } },
      h("header", null, h("span", { class: "tiny muted" }, `#${j + 1} →`),
        F.select({ title: "Target node", value: edge.to, path: `${path}.to`, options: storyNodes().map((n) => [n.id, `${n.title || n.id} (${n.id})`]), onChange: (v) => { edge.to = v; changed(); redraw(); } }),
        h("span", { class: "grow" }),
        actions && actions.openNode ? h("button", { type: "button", class: "icon ghost", title: "Open target", "aria-label": "Open target node", onclick: () => actions.openNode(edge.to) }, "↗") : null,
        h("button", { type: "button", class: "icon ghost", "aria-label": "Move up", disabled: j === 0, onclick: move(-1) }, "↑"),
        h("button", { type: "button", class: "icon ghost", "aria-label": "Move down", disabled: j === list.length - 1, onclick: move(1) }, "↓"),
        h("button", { type: "button", class: "icon ghost", "aria-label": "Remove edge", onclick: () => { list.splice(j, 1); changed(); redraw(); } }, "✕")),
      h("div", { class: "form-grid" },
        h("div", { class: "span-all" }, F.text({ label: "Label", value: edge.label || "", max: 80, path: `${path}.label`, onChange: (v) => { if (v) edge.label = v; else delete edge.label; changed(); } })),
        F.number({ label: "After (weeks)", value: edge.after ?? "", min: 0, max: 240, integer: true, placeholder: "0", path: `${path}.after`, onChange: (v) => { if (!v) delete edge.after; else edge.after = v; changed(); } }),
        F.number({ label: "Until (weeks)", value: edge.until ?? "", min: 0, max: 240, integer: true, placeholder: "never", path: `${path}.until`, onChange: (v) => { if (v === undefined) delete edge.until; else edge.until = v; changed(); } })),
      condBox);
  }
  function edgePanel(node, index, j, actions) {
    const edge = node.edges[j];
    const target = storyNodes().find((n) => n.id === edge.to);
    return [
      h("div", { class: "panel-title" }, h("h3", null, "Edge"), h("span", { class: "spacer" }),
        h("button", { type: "button", class: "icon ghost danger", title: "Delete edge", "aria-label": "Delete edge", onclick: actions.remove }, "✕")),
      h("div", { class: "row", style: { marginBottom: "12px" } },
        h("button", { type: "button", class: "small", onclick: () => actions.openNode(node.id) }, `${node.title || node.id}`), h("span", { class: "muted" }, "→"),
        target ? h("button", { type: "button", class: "small", onclick: () => actions.openNode(target.id) }, `${target.title || target.id}`) : h("span", { class: "badge bad" }, `missing ${edge.to}`)),
      edgeCard(node, `story.nodes[${index}]`, j, actions.onChange, actions.redraw, actions),
      h("p", { class: "hint" }, `Edge ${j + 1} of ${node.edges.length} from this node. Earlier edges win when several could fire.`),
    ];
  }

  // ----- condition builder
  function defaultLeaf() { return { metric: "trust", op: "<", value: 0.4 }; }
  function splitMetric(metric) {
    const text = String(metric || "");
    const i = text.indexOf(":");
    return i < 0 ? [text, null] : [text.slice(0, i), text.slice(i + 1)];
  }
  function metricGroups() {
    const metrics = cat().metrics;
    const known = (k) => k in metrics;
    const opt = (k) => [k, `${k.replace(":{x}", "")} — ${metrics[k]}`];
    return [
      { group: "Society", options: Object.keys(metrics).filter((k) => !k.includes(":")).map(opt) },
      { group: "Facts", options: ["belief:{x}", "informed:{x}"].filter(known).map(opt) },
      { group: "Skills & projects", options: ["skill:{x}", "holders:{x}", "project:{x}"].filter(known).map(opt) },
      { group: "Rules & disputes", options: ["convention:{x}", "policy:{x}", "conflict:{x}"].filter(known).map(opt) },
      { group: "One resident", options: ["alive:{x}", "present:{x}", "health:{x}", "wellbeing:{x}", "trust:{x}", "loneliness:{x}"].filter(known).map(opt) },
      { group: "Steward", options: ["actions:{x}"].filter(known).map(opt) },
    ].filter((g) => g.options.length);
  }
  function paramOptions(head) {
    switch (head) {
      case "belief": case "informed": return facts().map((f) => [f.id, `${f.id} — ${truncate(f.statement || "", 40)}`]);
      case "skill": case "holders": return cat().skills.map((s2) => [s2, s2]);
      case "project": return Object.entries(projectsEff()).filter(([, p]) => p.enabled).map(([k, p]) => [k, p.name || k]);
      case "convention": return conventionDomains().map((d) => [d, d]);
      case "policy": return Object.entries(cat().policies).map(([k, p]) => [k, p.label || k]);
      case "conflict": return conflictIds().map((c) => [c, c]);
      case "actions": return VERBS.map((v) => [v, v]);
      default: return people().map((p) => [p.id, `${p.name || p.id} · ${p.id}`]);
    }
  }
  function metricRange(head) {
    if (head === "wellbeing") return "0–10";
    if (head === "health") return "0–100";
    if (["alive", "present", "project"].includes(head)) return "1 = yes, 0 = no";
    if (head === "skill") return "best level, 0–3";
    if (["holders", "population", "actions"].includes(head)) return "a count";
    if (head === "week") return `1–${ticks()}`;
    if (["granary", "treasury", "medicine", "networth"].includes(head)) return "an amount";
    if (head === "policy") return "value; choices by index, no limit = -1";
    if (head === "conflict") return "intensity 0–1, -1 once resolved";
    return "a share, 0–1";
  }
  function conditionEditor(holder, key, path, onChange) {
    const root = h("div");
    const sentence = h("div", { class: "cond-sentence", "aria-live": "polite" });
    const redraw = () => { fill(root, conditionNode(holder, key, path, 0, redraw, changed, null)); sentence.textContent = condShort(holder[key]); };
    const changed = () => { sentence.textContent = condShort(holder[key]); onChange(); };
    redraw();
    return h("div", { class: "cond-root" }, root, sentence);
  }
  function conditionNode(holder, key, path, depth, redraw, changed, onRemove) {
    const cond = holder[key];
    const negated = isObject(cond) && "not" in cond;
    const innerHolder = negated ? cond : holder;
    const innerKey = negated ? "not" : key;
    const innerPath = negated ? `${path}.not` : path;
    let inner = innerHolder[innerKey];
    if (!isObject(inner)) { inner = defaultLeaf(); innerHolder[innerKey] = inner; }
    const notButton = h("button", { type: "button", class: "small not-btn", "aria-pressed": String(negated), title: "Negate this condition",
      onclick: () => { holder[key] = negated ? cond.not : { not: cond }; changed(); redraw(); } }, "NOT");
    const removeButton = onRemove ? h("button", { type: "button", class: "icon ghost", "aria-label": "Remove condition", onclick: onRemove }, "✕") : null;
    if (Array.isArray(inner.all) || Array.isArray(inner.any)) {
      const mode = Array.isArray(inner.all) ? "all" : "any";
      const list = inner[mode];
      const head = h("div", { class: "cond-head" }, notButton,
        F.segmented({ label: "Combine", value: mode, options: [["all", "ALL of"], ["any", "ANY of"]], onChange: (m) => { if (m === mode) return; inner[m] = inner[mode]; delete inner[mode]; changed(); redraw(); } }),
        h("span", { class: "spacer" }),
        h("button", { type: "button", class: "small", onclick: () => { list.push(defaultLeaf()); changed(); redraw(); } }, "+ condition"),
        depth < 6 ? h("button", { type: "button", class: "small", onclick: () => { list.push({ all: [defaultLeaf()] }); changed(); redraw(); } }, "+ group") : null,
        removeButton);
      const children = list.map((c, i) => conditionNode(list, i, `${innerPath}.${mode}[${i}]`, depth + 1, redraw, changed, () => {
        list.splice(i, 1);
        if (!list.length) list.push(defaultLeaf());
        changed();
        redraw();
      }));
      return h("div", { class: `cond ${mode}${negated ? " negated" : ""}`, "data-path": innerPath }, head, children);
    }
    const [head, param] = splitMetric(inner.metric);
    const template = param !== null ? `${head}:{x}` : head;
    const metricSelect = F.select({ title: "Metric", value: template, path: `${innerPath}.metric`, options: metricGroups(), onChange: (t) => {
      if (t.includes(":{x}")) { const h2 = t.split(":")[0]; const first = paramOptions(h2)[0]; inner.metric = `${h2}:${first ? first[0] : ""}`; }
      else inner.metric = t;
      changed();
      redraw();
    } });
    let paramControl = null;
    if (param !== null) {
      if (head === "actions") {
        const listId = `verbs-${innerPath.replace(/\W/g, "")}`;
        const input = h("input", { type: "text", value: param, list: listId, class: "mono", "aria-label": "Action kind (verb or verb:target)" });
        input.addEventListener("input", () => { inner.metric = `${head}:${input.value.trim()}`; changed(); });
        paramControl = h("span", null, h("datalist", { id: listId }, VERBS.map((v) => h("option", { value: v }))), input);
      } else {
        paramControl = F.select({ title: "Which", value: param, options: paramOptions(head), onChange: (v) => { inner.metric = `${head}:${v}`; changed(); } });
      }
    }
    const op = F.select({ title: "Operator", value: inner.op || ">=", options: OPS.map((o) => [o, o]), path: `${innerPath}.op`, onChange: (v) => { inner.op = v; changed(); } });
    const value = h("input", { type: "number", step: "any", value: inner.value ?? 0, "aria-label": "Value", "data-path": `${innerPath}.value` });
    value.addEventListener("input", () => { const v = Number(value.value); if (value.value !== "" && Number.isFinite(v)) { inner.value = v; changed(); } });
    const wrapGroup = h("button", { type: "button", class: "icon ghost", title: "Wrap in a group (to add more conditions)", "aria-label": "Wrap in a group",
      onclick: () => { innerHolder[innerKey] = { all: [inner] }; changed(); redraw(); } }, "⊕");
    const description = cat().metrics[template] || "";
    const leaf = h("div", { class: `cond-leaf${param === null ? " no-param" : ""}` }, metricSelect, paramControl, op, value, h("span", { class: "row", style: { gap: "2px", flexWrap: "nowrap" } }, notButton, wrapGroup, removeButton));
    return h("div", { class: `cond-leaf-wrap${negated ? " cond negated" : ""}` }, leaf, h("span", { class: "hint" }, `${description}${description ? " · " : ""}${metricRange(head)}`));
  }

  // =====================================================================================
  // 17. Section: Collapse & scoring
  // =====================================================================================
  function collapseEff() { return Object.assign(Object.fromEntries(Object.entries(cat().collapse).map(([k, v]) => [k, v.default])), isObject(state.raw.collapse) ? state.raw.collapse : {}); }
  function setCollapse(key, value) { if (!isObject(state.raw.collapse)) state.raw.collapse = {}; state.raw.collapse[key] = value; touch("collapse"); }
  function ruleCard(title, keys, sociology) {
    const el = h("div", { class: "card rule-card" });
    const draw = () => {
      const eff = collapseEff();
      const [main, weeks] = keys;
      const spec = cat().collapse[main];
      const isBool = typeof spec.default === "boolean";
      const on = isBool ? eff[main] === true : eff[main] !== null && eff[main] !== undefined;
      el.classList.toggle("off", !on);
      const controls = [];
      if (on && !isBool) {
        controls.push(F.range({ label: spec.label, value: Number(eff[main]), min: 0, max: 1, step: 0.01, path: `collapse.${main}`, hint: spec.help,
          onChange: (v) => setCollapse(main, v ?? spec.default) }));
        if (weeks) {
          const ws = cat().collapse[weeks];
          controls.push(F.range({ label: ws.label, value: eff[weeks] ?? ws.default, min: 1, max: 52, step: 1, integer: true, path: `collapse.${weeks}`, hint: ws.help,
            onChange: (v) => setCollapse(weeks, v ?? ws.default) }));
        }
      } else if (isBool) controls.push(h("p", { class: "hint" }, spec.help));
      fill(el, 
        h("div", { class: "row between" }, h("h3", { style: { margin: 0 } }, title),
          F.toggle({ label: on ? "Active" : "Off", checked: on, path: `collapse.${main}`, onChange: (v) => {
            setCollapse(main, isBool ? v : v ? (cat().collapse[main].default ?? 0.5) : null);
            draw();
          } })),
        ...controls,
        h("p", { class: "sociology" }, sociology));
    };
    draw();
    return el;
  }
  renderers.collapse = (host) => {
    const scoring = deepMerge(cat().defaults.scoring, state.raw.scoring || {});
    const domainBox = h("div", { class: "grid auto" });
    const expertUnits = () => {
      const rows = state.calib && state.calib.data.rows.filter((r) => r.policy === "expert");
      if (!rows || !rows.length) return null;
      const out = {};
      for (const d of Object.keys(cat().domains)) out[d] = rows.reduce((a, r) => a + Math.max(0, r.units[d] || 0), 0) / rows.length;
      return out;
    };
    const drawDomains = () => {
      const units = expertUnits();
      const rates = deepMerge(cat().defaults.scoring, state.raw.scoring || {}).rates;
      fill(domainBox, ...Object.entries(cat().domains).map(([d, info]) => {
        const preview = h("span", { class: "preview" }, units ? `≈ ${fmt.int(units[d] * rates[d])} pts for the expert` : "");
        return h("div", { class: "card domain-card" },
          h("div", { class: "row between" }, h("h3", { style: { margin: 0 } }, `${d} · ${info.name}`), preview),
          h("p", { class: "units" }, info.unit),
          h("p", { class: "small", style: { margin: "6px 0 10px" } }, info.help),
          F.number({ label: "Points per unit (exchange rate)", value: rates[d], min: 0, max: 1e6, step: 0.01, path: `scoring.rates.${d}`,
            onChange: (v) => { setIn(state.raw, ["scoring", "rates", d], v ?? 1); touch("scoring"); if (units) preview.textContent = `≈ ${fmt.int(units[d] * (v ?? 0))} pts for the expert`; } }));
      }));
    };
    drawDomains();
    subscribe("calib", drawDomains);
    const target = h("input", { type: "number", min: 1, max: 1e7, step: 50, value: (state.rates && state.rates.target) || 1000, style: { width: "110px" }, "aria-label": "Target points per domain" });
    const ratesBox = h("div");
    const calibrateButton = h("button", { type: "button", class: "primary small" }, "Calibrate rates from the expert");
    const drawRates = () => {
      if (!state.rates) { fill(ratesBox, h("p", { class: "small muted" }, "Runs the expert reference on the recipe's seeds and suggests exchange rates that give it the target points in every domain, so no domain dominates the total.")); return; }
      const current = deepMerge(cat().defaults.scoring, state.raw.scoring || {}).rates;
      const rows = Object.keys(cat().domains).map((d) => h("tr", null, h("td", null, `${d} ${cat().domains[d].name}`), h("td", null, fmt.num(state.rates.units[d], 1)),
        h("td", null, fmt.num(current[d], 4)), h("td", { style: { color: "var(--accent-hi)" } }, fmt.num(state.rates.suggested[d], 4)),
        h("td", null, fmt.int(state.rates.units[d] * state.rates.suggested[d]))));
      fill(ratesBox, h("div", { class: "table-scroll" }, h("table", { class: "data" },
        h("thead", null, h("tr", null, h("th", null, "Domain"), h("th", null, "Expert units"), h("th", null, "Current rate"), h("th", null, "Suggested"), h("th", null, "Expert points"))),
        h("tbody", null, rows))),
      h("div", { class: "row", style: { marginTop: "12px" } }, h("span", { class: "small muted" }, `Target ${fmt.int(state.rates.target)} points per domain for the expert.`), h("span", { class: "spacer" }),
        h("button", { type: "button", class: "primary small", onclick: () => {
          if (!isObject(state.raw.scoring)) state.raw.scoring = {};
          state.raw.scoring.rates = Object.assign({}, state.rates.suggested);
          touch("scoring");
          drawDomains();
          drawRates();
          toast("Applied the suggested exchange rates.", "good");
        } }, "Apply suggested rates")));
    };
    calibrateButton.addEventListener("click", async () => {
      calibrateButton.disabled = true;
      fill(ratesBox, h("p", { class: "row muted" }, h("span", { class: "spinner" }), "Running the expert steward…"));
      try {
        const t = clamp(Number(target.value) || 1000, 1, 1e7);
        const data = await API.calibrate(state.raw, { policies: ["expert"], target: t });
        const units = {};
        for (const d of Object.keys(cat().domains)) units[d] = data.rows.reduce((a, r) => a + Math.max(0, r.units[d] || 0), 0) / Math.max(1, data.rows.length);
        state.rates = { target: t, suggested: data.suggested_rates, units };
      } catch (error) {
        toast(`Calibration failed: ${error.message}`, "bad");
      } finally {
        calibrateButton.disabled = false;
        drawRates();
      }
    });
    drawRates();
    put(host, 
      sectionHead("Rules", "Collapse & scoring", "When does the society fall apart, and how are its achievements counted? A collapse ends the run with a score of zero."),
      h("h3", { class: "eyebrow", style: { margin: "6px 0 12px" } }, "Collapse rules"),
      h("div", { class: "grid auto" },
        ruleCard("Depopulation", ["population"], COLLAPSE_SOCIOLOGY.population),
        ruleCard("Ecological collapse", ["commons"], COLLAPSE_SOCIOLOGY.commons),
        ruleCard("Revolt", ["revolt_share", "revolt_weeks"], COLLAPSE_SOCIOLOGY.revolt),
        ruleCard("No confidence", ["legitimacy", "legitimacy_weeks"], COLLAPSE_SOCIOLOGY.legitimacy),
        ruleCard("Famine", ["famine_share", "famine_weeks"], COLLAPSE_SOCIOLOGY.famine),
        ruleCard("Fragmentation", ["fragmentation"], COLLAPSE_SOCIOLOGY.fragmentation)),
      h("h3", { class: "eyebrow", style: { margin: "26px 0 12px" } }, "Domains & exchange rates"),
      h("div", { class: "card glow", style: { marginBottom: "18px" } }, h("h3", null, h("span", { class: "grow" }, "Calibrate rates"), h("label", { class: "row small muted" }, "Target points / domain", target), calibrateButton), ratesBox),
      domainBox,
      h("h3", { class: "eyebrow", style: { margin: "26px 0 12px" } }, "Event points"),
      card(null, h("div", { class: "form-grid" }, Object.keys(cat().defaults.scoring).filter((k) => k !== "rates").map((k) => F.number({
        label: titleCase(k), value: scoring[k], min: -1e4, max: 1e4, step: 0.5, path: `scoring.${k}`, hint: SCORING_HELP[k],
        onChange: (v) => { setIn(state.raw, ["scoring", k], v); touch("scoring"); } })))));
  };

  // =====================================================================================
  // 18. Section: Simulate (animated preview with reference stewards)
  // =====================================================================================
  let sparkId = 0;
  const CHARTS = [
    { label: "Population", lines: [["population", "#e8e8e8"]], min: 0, format: (v) => fmt.int(v) },
    { label: "Wellbeing", lines: [["wellbeing", "#7cc47f"]], min: 0, max: 10, format: (v) => fmt.num(v, 1) },
    { label: "Trust · legitimacy", lines: [["trust", "#e2c21c"], ["legitimacy", "#b388ff"]], min: 0, max: 1, format: (v) => fmt.num(v, 2) },
    { label: "Protest · starving", lines: [["protest", "#ef6a5b"], ["starving", "#ff9f43"]], min: 0, max: 1, format: (v) => fmt.pct(v) },
    { label: "Lake stock", lines: [["commons", "#4aa3df"]], min: 0, max: 1, format: (v) => fmt.pct(v), critical: true },
    { label: "Granary · treasury", lines: [["granary", "#e0b030"], ["treasury", "#4dd0a8"]], normalize: true, format: (v) => fmt.int(v) },
    { label: "Gini (inequality)", lines: [["gini", "#f06292"]], min: 0, max: 1, format: (v) => fmt.num(v, 2) },
  ];
  function sparkChart(def, timeline, recipe) {
    const W = 300, H = 38;
    const id = `spark-${++sparkId}`;
    const n = Math.max(1, timeline.length - 1);
    const x = (i) => (i / n) * W;
    const clipRect = s("rect", { x: 0, y: -4, width: 0, height: H + 8 });
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "none", role: "img", "aria-label": def.label }, s("defs", null, s("clipPath", { id }, clipRect)));
    if (def.critical) {
      const crit = Number(getIn(recipe, ["world", "commons", "critical"], cat().defaults.world.commons.critical));
      const y = H - crit * H;
      put(svg, s("line", { x1: 0, x2: W, y1: y, y2: y, stroke: "#ef5350", "stroke-dasharray": "3 3", "vector-effect": "non-scaling-stroke", opacity: 0.7 }));
    }
    for (const [key, color] of def.lines) {
      const values = timeline.map((r) => Number(r[key]) || 0);
      const lo = def.normalize ? 0 : def.min ?? Math.min(...values);
      const hi = def.normalize ? Math.max(1, ...values) : def.max ?? Math.max(lo + 1e-9, ...values);
      const pts = values.map((v, i) => `${x(i).toFixed(1)},${(H - ((v - lo) / (hi - lo || 1)) * (H - 4) - 2).toFixed(1)}`).join(" ");
      put(svg, s("polyline", { points: pts, fill: "none", stroke: color, "stroke-width": 1.4, opacity: 0.22, "vector-effect": "non-scaling-stroke" }),
        s("polyline", { points: pts, fill: "none", stroke: color, "stroke-width": 2, "clip-path": `url(#${id})`, "vector-effect": "non-scaling-stroke", style: `filter: drop-shadow(0 0 3px ${color})` }));
    }
    const marker = s("line", { x1: 0, x2: 0, y1: 0, y2: H, stroke: "rgba(255,255,255,0.5)", "vector-effect": "non-scaling-stroke" });
    put(svg, marker);
    const now = h("span", { class: "now" });
    const el = h("div", { class: "spark" }, h("span", { class: "name" }, def.label), now, svg);
    return {
      el,
      update(i) {
        const xi = x(i);
        clipRect.setAttribute("width", String(xi));
        marker.setAttribute("x1", String(xi));
        marker.setAttribute("x2", String(xi));
        const row = timeline[i] || {};
        now.textContent = def.lines.map(([key]) => def.format(Number(row[key]) || 0)).join(" · ");
      },
    };
  }
  async function runSimulation(policy, seed) {
    if (state.simBusy) return;
    state.simBusy = true;
    bus.emit("sim");
    const snapshot = JSON.stringify(state.raw);
    try {
      const report = await API.simulate(state.raw, policy, seed);
      state.sim = { report, policy, seed, recipe: JSON.parse(snapshot), snapshot };
    } catch (error) {
      toast(`Simulation failed: ${error.message}`, "bad");
      if (error.status === 422 && /^\S+: /.test(error.message)) showErrorBanner(error.message);
    } finally {
      state.simBusy = false;
      bus.emit("sim");
    }
  }
  renderers.simulate = (host) => {
    const pick = h("div", { class: "steward-pick", role: "radiogroup", "aria-label": "Reference steward" });
    const drawPick = () => fill(pick, ...Object.entries(STEWARDS).map(([key, text]) => h("button", { type: "button", role: "radio", "aria-checked": String(key === state.policy),
      onclick: () => { state.policy = key; drawPick(); } }, h("b", null, key), h("small", null, text))));
    drawPick();
    const seed = h("input", { type: "number", min: 0, max: 2147483647, step: 1, value: state.seed ?? 1, style: { width: "120px" }, "aria-label": "Seed" });
    seed.addEventListener("input", () => { const v = Number(seed.value); if (Number.isInteger(v) && v >= 0) state.seed = v; });
    const runButton = h("button", { type: "button", class: "primary", onclick: () => runSimulation(state.policy, state.seed) }, "▶ Run preview");
    const compareButton = h("button", { type: "button", onclick: () => runCalibration() }, "Compare all stewards");
    const stage = h("div");
    const compareBox = h("div");
    put(host, sectionHead("Preview", "Simulate", "Run the society with a scripted reference steward and watch it live: residents walk to their weekly work, the lake rises and falls, rumours and crises unfold along the story graph."),
      h("div", { class: "card" }, h("div", { class: "sim-controls" }, pick,
        h("div", { class: "stack", style: { gap: "8px" } }, h("label", { class: "field" }, h("span", { class: "label" }, "Seed"), seed), runButton, compareButton))),
      stage, compareBox);
    let cleanupStage = null;
    const drawStage = () => {
      if (cleanupStage) { cleanupStage(); cleanupStage = null; }
      runButton.disabled = state.simBusy;
      runButton.textContent = state.simBusy ? "Running…" : "▶ Run preview";
      fill(stage);
      if (state.simBusy && !state.sim) { put(stage, h("div", { class: "card", style: { marginTop: "18px" } }, h("p", { class: "row muted" }, h("span", { class: "spinner" }), "Simulating…"))); return; }
      if (!state.sim) {
        put(stage, h("div", { class: "card empty-note", style: { marginTop: "18px" } }, h("p", null, "Choose a reference steward and run the preview. A run takes a fraction of a second; the playback is yours to scrub.")));
        return;
      }
      cleanupStage = buildStage(stage, state.sim);
    };
    const drawCompare = () => {
      compareButton.disabled = state.calibBusy;
      fill(compareBox);
      if (state.calibBusy) { put(compareBox, h("div", { class: "card", style: { marginTop: "18px" } }, h("p", { class: "row muted" }, h("span", { class: "spinner" }), "Running all reference stewards…"))); return; }
      if (state.calib) put(compareBox, compareTable(state.calib, (policy, s2) => { state.policy = policy; state.seed = s2; seed.value = String(s2); drawPick(); runSimulation(policy, s2); window.scrollTo({ top: 0, behavior: reducedMotion() ? "auto" : "smooth" }); }));
    };
    subscribe("sim", drawStage);
    subscribe("calib", drawCompare);
    drawStage();
    drawCompare();
    return () => { if (cleanupStage) cleanupStage(); };
  };
  function compareTable(calib, open) {
    const stale = calib.snapshot !== JSON.stringify(state.raw);
    const max = Math.max(1, ...calib.data.rows.map((r) => Math.max(r.total, r.earned)));
    const rows = calib.data.rows.map((r) => h("tr", { class: "clickable", tabindex: "0", title: "Play this run",
      onclick: () => open(r.policy, r.seed), onkeydown: (e) => { if (e.key === "Enter") open(r.policy, r.seed); } },
      h("td", null, h("strong", { style: { textTransform: "capitalize" } }, r.policy)),
      h("td", null, String(r.seed)),
      h("td", null, r.collapsed ? h("span", { class: "collapse-tag" }, "COLLAPSE") : fmt.int(r.total)),
      h("td", { style: { minWidth: "160px" } }, h("div", { class: "bar" }, h("i", { class: r.collapsed ? "ghost" : "", style: { width: `${((r.collapsed ? r.earned : r.total) / max) * 100}%` } }))),
      h("td", null, `${r.survived_weeks} wk`),
      h("td", { class: "small nowrap" }, r.collapsed ? `week ${r.collapsed.week} · ${r.collapsed.cause}` : "—"),
      h("td", null, fmt.num(r.wellby, 0)),
      h("td", { class: "small muted path-cell", title: (r.path || []).join(" → ") }, truncate((r.path || []).join(" → "), 70))));
    return h("div", { class: "card", style: { marginTop: "18px" } },
      h("h3", null, h("span", { class: "grow" }, "All reference stewards"), stale ? h("span", { class: "badge watch" }, "recipe changed since") : null),
      h("div", { class: "table-scroll" }, h("table", { class: "data" },
        h("thead", null, h("tr", null, ["Steward", "Seed", "Score", "", "Survived", "Collapse", "WELLBY", "Story path"].map((t) => h("th", null, t)))),
        h("tbody", null, rows))),
      h("p", { class: "tiny muted", style: { marginTop: "8px" } }, "Click a row to play it in the village. Striped bars are points forfeited by a collapse."));
  }
  function buildStage(stage, sim) {
    const report = sim.report;
    const frames = report.frames || [];
    const timeline = report.timeline || [];
    const stale = sim.snapshot !== JSON.stringify(state.raw);
    const names = new Map((report.residents || []).map((r) => [r.id, r.name]));
    const nameOf = (id) => names.get(id) || id;
    const villageBox = h("div", { class: "village-box" });
    const playIcon = () => s("svg", { viewBox: "0 0 16 16", "aria-hidden": "true" }, s("path", { d: "M4 2.5v11l9-5.5z" }));
    const pauseIcon = () => s("svg", { viewBox: "0 0 16 16", "aria-hidden": "true" }, s("path", { d: "M4 2.5h3v11H4zM9 2.5h3v11H9z" }));
    const playButton = h("button", { type: "button", class: "play-btn", "aria-label": "Play" }, playIcon());
    const speed = h("select", { "aria-label": "Playback speed" }, [0.5, 1, 2, 4, 8].map((v) => h("option", { value: String(v), selected: v === 1 }, `${v}×`)));
    const scrub = h("input", { type: "range", class: "scrub", min: 0, max: Math.max(0, frames.length - 1), step: 1, value: 0, "aria-label": "Week" });
    const weekLabel = h("span", { class: "week" });
    const prefs = Object.assign({ steward: true, thoughts: true, notebook: true }, storageGet("society-lab:village") || {});
    const toggle = (key, text) => {
      const input = h("input", { type: "checkbox", checked: prefs[key] });
      input.addEventListener("change", () => {
        prefs[key] = input.checked;
        storageSet("society-lab:village", prefs);
        if (key === "steward") village.setStewardVisible(input.checked);
        if (key === "thoughts") village.setThoughts(input.checked);
        if (key === "notebook") village.setNotebook(input.checked);
      });
      return h("label", { class: "switch small" }, input, h("span", null, text));
    };
    const runningTotal = h("div", { class: "score-hero" });
    const charts = CHARTS.map((def) => sparkChart(def, timeline, sim.recipe));
    const feed = h("div", { class: "feed", "aria-live": "off" });
    const notes = h("div");
    const log = h("div", { class: "steward-log", "aria-live": "off" });
    const storyWrap = h("div", { class: "story-mini" });
    const logButton = h("button", { type: "button", class: "small sheet-phone-only log-toggle", "aria-expanded": "false", title: "Show the steward's actions this week" }, "Log");
    const logCard = h("aside", { class: "card log-card", "aria-label": "Steward log" }, h("h3", null, h("span", { class: "grow" }, "Steward log"), h("span", { class: "tiny muted" }, "this week")), notes, log);
    put(stage, 
      h("div", { class: "sim-stage" },
        h("div", { class: "card village-card" }, villageBox,
          h("div", { class: "playbar" }, playButton, speed, scrub, weekLabel, logButton),
          h("div", { class: "playbar sub" }, toggle("steward", "Steward"), toggle("thoughts", "Thought bubbles"), toggle("notebook", "Notebook"),
            h("span", { class: "spacer" }), h("span", { class: "tiny muted hint-mouse" }, "Space play/pause · ←/→ step a week"))),
        h("div", { class: "stack", style: { gap: "14px" } },
          h("div", { class: "card" }, h("h3", null, h("span", { class: "grow", style: { textTransform: "capitalize" } }, `${sim.policy} steward · seed ${sim.seed}`),
            stale ? h("span", { class: "badge watch", title: "The recipe changed since this run" }, "stale") : null), runningTotal),
          logCard)),
      h("div", { class: "grid cols-2", style: { marginTop: "16px" } },
        h("div", { class: "card" }, h("h3", null, "Live indicators"), charts.map((c) => c.el)),
        h("div", { class: "card" }, h("h3", null, "Chronicle"), feed)),
      h("div", { class: "card story-card", style: { marginTop: "16px" } }, h("div", { class: "story-toolbar is-mini" }, h("strong", { class: "small" }, "Story path"), h("span", { class: "spacer" }),
        h("span", { class: "tiny muted" }, "Nodes light up in the week they activate; walked edges glow.")), storyWrap),
      finalScore(report));
    const logSheet = makeSheet(logCard, { title: "Steward log", phone: true, modal: false, half: true,
      onToggle: (open) => { logButton.setAttribute("aria-expanded", String(open)); logButton.classList.toggle("active", open); } });
    logButton.addEventListener("click", () => logSheet.toggle());
    const village = window.SocietyVillage.mount(villageBox, { speed: 1, captions: true, steward: prefs.steward, thoughts: prefs.thoughts, notebook: prefs.notebook, expand: true });
    const story = new StoryCanvas(storyWrap, { editable: false, getNodes: () => getIn(sim.recipe, ["story", "nodes"], []) });
    put(storyWrap, h("div", { class: "story-zoom" }, h("button", { type: "button", "aria-label": "Zoom in", onclick: () => story.zoom(1.2) }, "+"),
      h("button", { type: "button", "aria-label": "Zoom out", onclick: () => story.zoom(1 / 1.2) }, "−")));
    const update = (i) => {
      const frame = frames[i] || {};
      scrub.value = String(i);
      scrub.style.setProperty("--fill", `${(i / Math.max(1, frames.length - 1)) * 100}%`);
      weekLabel.textContent = `Week ${frame.week ?? i}${frame.season ? ` · ${titleCase(frame.season)}` : ""}`;
      charts.forEach((c) => c.update(i));
      drawFeed(i);
      drawLog(i);
      story.setPlayback({ states: frame.story || {}, path: report.story ? report.story.path : [], week: frame.week ?? i });
      const points = Object.values(frame.points || {}).reduce((a, v) => a + (Number(v) || 0), 0);
      const last = i === frames.length - 1;
      fill(runningTotal, 
        frame.collapsed ? h("span", { class: "total collapse" }, "COLLAPSE") : h("span", { class: "total running" }, fmt.int(points)),
        h("span", { class: "muted small" }, frame.collapsed ? `${frame.collapsed.label} (week ${frame.collapsed.week})` : last ? "final points" : "points so far"),
        h("div", { class: "small muted", style: { width: "100%" } }, `${fmt.int(getIn(frame, ["metrics", "population"], 0))} residents · wellbeing ${fmt.num(getIn(frame, ["metrics", "wellbeing"], 0), 1)} · trust ${fmt.num(getIn(frame, ["metrics", "trust"], 0), 2)}`));
    };
    function drawFeed(i) {
      const items = [];
      for (let k = i; k >= Math.max(1, i - 6); k--) {
        const f = frames[k];
        if (!f || !f.events || !f.events.length) continue;
        items.push(h("div", { class: "feed-week" }, `Week ${f.week}`));
        for (const text of f.events) {
          const kind = /COLLAPSE|riot|died|dies|dead|famine|revolt|left|seized|fever/i.test(text) ? " bad" : /turning point|recover|settle|welcome|preserved|vindicated|survives/i.test(text) ? " good" : "";
          items.push(h("div", { class: `feed-item${kind}${k === i ? "" : " old"}` }, text));
        }
      }
      fill(feed, ...(items.length ? items : [h("p", { class: "small muted" }, i === 0 ? "Week 0: the society before the steward's first decision." : "A quiet week.")]));
    }
    function drawLog(i) {
      const frame = frames[i] || {};
      const info = frame.steward || {};
      const forecasts = info.forecasts ? Object.entries(info.forecasts) : [];
      fill(notes, 
        info.note ? h("details", { class: "fold notebook", open: true }, h("summary", null, "📓 Notebook"), h("p", { class: "small notebook-text" }, info.note)) : null,
        forecasts.length ? h("p", { class: "small", style: { color: "#c9e6ff", margin: "6px 0" } }, `Forecasts: ${forecasts.map(([k, v]) => `${k} ${fmt.pct(Number(v))}`).join(" · ")}`) : null,
        info.error ? h("p", { class: "callout bad small" }, `Unreadable answer: ${info.error}`) : null);
      const list = Array.isArray(frame.actions) ? frame.actions : [];
      logButton.textContent = list.length ? `Log · ${list.length}` : "Log";
      logSheet.setTitle(`Steward log · week ${frame.week ?? i}`);
      fill(log, ...(list.length ? list.map((a, k) => {
        const meta = window.SocietyVillage.describeAction(a, nameOf);
        const reply = window.SocietyVillage.replyOf(a);
        const refused = a.status && a.status !== "done";
        return h("div", { class: `log-item${refused ? " refused" : ""}`, "data-k": String(k) },
          h("span", { class: "log-icon", "aria-hidden": "true" }, meta.icon),
          h("div", { class: "log-body" },
            h("div", { class: "log-head" }, h("b", null, meta.label), h("span", { class: `badge ${refused ? "bad" : "good"}` }, refused ? "refused" : a.status || "done")),
            (a.targets || []).length ? h("div", { class: "tiny muted" }, (a.targets || []).map(nameOf).join(" · ")) : a.place ? h("div", { class: "tiny muted" }, `at the ${a.place}`) : null,
            a.say ? h("div", { class: "log-say" }, `“${a.say}”`) : null,
            reply.text ? h("div", { class: "log-reply" }, reply.speaker ? h("b", null, `${reply.speaker}: `) : null, reply.text) : null));
      }) : [h("p", { class: "small muted" }, i === 0 ? "No decisions yet: week 0 is the starting state." : "No actions this week.")]));
    }
    function highlight(event) {
      if (!event || event.frameIndex !== village.index) return;
      for (const el of log.querySelectorAll(".log-item")) {
        const on = Number(el.dataset.k) === event.index;
        el.classList.toggle("acting", on);
        if (!on) continue;
        const behavior = reducedMotion() ? "auto" : "smooth";
        if (log.scrollHeight > log.clientHeight + 2) log.scrollTo({ top: el.offsetTop - log.offsetTop - 8, behavior });
        else if (logSheet.isOpen && logCard.scrollHeight > logCard.clientHeight + 2) logCard.scrollTo({ top: Math.max(0, el.offsetTop - 70), behavior });
      }
    }
    village.on("frame", update);
    village.on("action", highlight);
    village.on("play", () => { fill(playButton, pauseIcon()); playButton.setAttribute("aria-label", "Pause"); });
    village.on("pause", () => { fill(playButton, playIcon()); playButton.setAttribute("aria-label", "Play"); });
    playButton.addEventListener("click", () => village.toggle());
    speed.addEventListener("change", () => village.setSpeed(Number(speed.value)));
    scrub.addEventListener("input", () => { village.pause(); village.seek(Number(scrub.value)); });
    const onKey = (e) => {
      if (e.target.closest && e.target.closest("input, select, textarea")) return;
      if (e.key === " " || e.key === "k") { e.preventDefault(); village.toggle(); }
      else if (e.key === "ArrowRight" || e.key === "l") { village.pause(); village.seek(village.index + 1); }
      else if (e.key === "ArrowLeft" || e.key === "j") { village.pause(); village.seek(village.index - 1); }
    };
    stage.addEventListener("keydown", onKey);
    stage.tabIndex = -1;
    village.load({ frames, residents: report.residents, factions: report.factions, recipe: sim.recipe });
    update(0);
    if (!reducedMotion()) setTimeout(() => village.play(), 400);
    return () => { stage.removeEventListener("keydown", onKey); logSheet.destroy(); village.destroy(); story.destroy(); };
  }
  function finalScore(report) {
    const score = report.score;
    const domains = Object.entries(score.domains || {});
    const maxPts = Math.max(1, ...domains.map(([, d]) => d.points));
    const ev = score.events || {};
    const outcomes = (report.story && report.story.outcomes) || [];
    return h("div", { class: "card", style: { marginTop: "16px" } },
      h("h3", null, "Final result"),
      h("div", { class: "grid cols-2" },
        h("div", null,
          h("div", { class: "score-hero" },
            score.collapsed ? h("span", { class: "total collapse" }, "COLLAPSE") : h("span", { class: "total" }, fmt.int(score.total)),
            h("span", { class: "muted" }, score.collapsed ? `${score.collapsed.label} · week ${score.collapsed.week}` : `points · survived all ${score.survived_weeks} weeks`)),
          score.collapsed ? h("p", { class: "small", style: { color: "#ffb3aa" } }, `Score 0 — ${fmt.int(score.forfeited)} points forfeited after ${score.survived_weeks} weeks.`) : null,
          h("p", { class: "small muted", style: { margin: "8px 0 14px" } }, `${fmt.num(score.wellby, 1)} WELLBYs · ${fmt.num(score.wellby_equity, 1)} equity-weighted`),
          domains.map(([d, info]) => h("div", { class: "domain-bar", title: `${fmt.num(info.units, 1)} ${info.unit} × ${info.rate}` },
            h("span", null, `${d} ${info.name}`), h("div", { class: "bar" }, h("i", { class: score.collapsed ? "ghost" : "", style: { width: `${(info.points / maxPts) * 100}%` } })), h("span", { class: "v" }, fmt.int(info.points)))),
          h("div", { class: "chips", style: { marginTop: "14px" } },
            [["promises kept", ev.promises_kept], ["promises broken", ev.promises_broken], ["disputes settled", ev.conflicts_resolved], ["violent", ev.violence],
              ["concerns relieved", ev.concerns_relieved], ["apprentice levels", ev.apprentice_levels], ["deaths", ev.deaths], ["departures", ev.departures], ["harvest", ev.harvest]]
              .filter(([, v]) => v != null).map(([label, v]) => h("span", { class: "chip" }, h("b", null, fmt.compact(Number(v))), ` ${label}`)))),
        h("div", null,
          h("p", { style: { fontSize: "14px", lineHeight: "1.6" } }, report.narrative || ""),
          outcomes.length ? h("div", { style: { marginTop: "12px" } }, h("h4", null, "Turning points"),
            h("div", { class: "chips" }, outcomes.map((o) => h("span", { class: `badge ${o.type === "collapse" ? "bad" : "good"}` }, `w${o.week} · ${o.label}`)))) : null)),
      h("h4", { style: { marginTop: "20px" } }, "Sociological diagnosis"),
      h("div", { class: "diag-grid" }, (report.diagnosis || []).map((d) => h("div", { class: `diag ${d.level}` },
        h("h4", null, h("span", { class: `badge ${d.level === "good" ? "good" : d.level === "bad" ? "bad" : "watch"}` }, d.level), d.title),
        h("p", null, d.text), h("p", { class: "basis" }, d.basis)))));
  }

  // =====================================================================================
  // 19. Boot
  // =====================================================================================
  function onHash() {
    const section = location.hash.slice(1) || "overview";
    if (SECTIONS.includes(section) && section !== state.section) { state.section = section; renderSection(section); }
  }
  function bindBar() {
    $("#recipe-select").addEventListener("change", (e) => { const v = e.target.value; if (v !== "__current__") openRecipe(v); });
    $("#btn-new").addEventListener("click", newSociety);
    $("#btn-duplicate").addEventListener("click", () => duplicateSociety());
    $("#btn-validate").addEventListener("click", () => runValidate({ jump: true }));
    $("#btn-save").addEventListener("click", saveSociety);
    $("#btn-delete").addEventListener("click", deleteSociety);
    $("#btn-export").addEventListener("click", exportSociety);
    $("#import-file").addEventListener("change", (e) => { const file = e.target.files && e.target.files[0]; e.target.value = ""; importSociety(file); });
    window.addEventListener("hashchange", onHash);
    window.addEventListener("popstate", onHash);
    window.addEventListener("beforeunload", (e) => { if (state.dirty) { e.preventDefault(); e.returnValue = ""; } });
    document.addEventListener("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); saveSociety(); }
    });
  }
  /** Keep the active tab of the (horizontally scrolling) phone tab bar in view. */
  function revealNavItem(a) {
    const list = $("#nav-list");
    if (!a || list.scrollWidth <= list.clientWidth + 2) return;
    const box = list.getBoundingClientRect();
    const item = a.getBoundingClientRect();
    const delta = item.left + item.width / 2 - (box.left + box.width / 2);
    if (Math.abs(delta) > 4) list.scrollBy({ left: delta, behavior: reducedMotion() ? "auto" : "smooth" });
  }
  /** Fade the edges of a horizontal scroller where more content hides. Returns the refresh function; `.dispose()` stops watching. */
  function edgeFades(el) {
    const update = () => {
      const max = el.scrollWidth - el.clientWidth;
      el.classList.toggle("fade-l", max > 2 && el.scrollLeft > 2);
      el.classList.toggle("fade-r", max > 2 && el.scrollLeft < max - 2);
    };
    el.classList.add("edge-fade");
    el.addEventListener("scroll", update, { passive: true });
    const ro = typeof ResizeObserver === "function" ? new ResizeObserver(update) : null;
    if (ro) ro.observe(el);
    update();
    update.dispose = () => { if (ro) ro.disconnect(); };
    return update;
  }
  let badgeFades = null;
  function bindNav() {
    const list = $("#nav-list");
    for (const a of $$("a", list)) { const label = $("span", a); if (label) a.title = label.textContent; }
    edgeFades(list);
    badgeFades = edgeFades($("#recipe-badges"));
    window.addEventListener("resize", () => revealNavItem($("#nav-list a[aria-current]")));
  }
  /** Phones: the header tucks away while scrolling down and returns on the way up. */
  function bindHeaderTuck() {
    const top = $("#lab-top");
    let last = window.scrollY;
    let queued = false;
    const update = () => {
      queued = false;
      const y = window.scrollY;
      if (!phoneQuery.matches || y < 72) { top.classList.remove("is-tucked"); last = y; return; }
      if (y > last + 10) { top.classList.add("is-tucked"); last = y; } else if (y < last - 10) { top.classList.remove("is-tucked"); last = y; }
    };
    window.addEventListener("scroll", () => { if (!queued) { queued = true; requestAnimationFrame(update); } }, { passive: true });
    top.addEventListener("focusin", () => top.classList.remove("is-tucked"));
  }
  /** The ⋯ menu proxies the recipe-bar actions that do not fit on small screens. */
  function bindMoreMenu() {
    const dialog = $("#dlg-more");
    $("#btn-more").addEventListener("click", () => {
      for (const b of $$("[data-proxy]", dialog)) { const target = $(`#${b.dataset.proxy}`); b.disabled = Boolean(target && target.disabled); }
      const del = $("[data-proxy=btn-delete]", dialog);
      del.title = state.meta.bundled ? "Bundled societies cannot be deleted" : !state.meta.saved ? "Not saved yet" : "";
      $("#dlg-more-sub").textContent = state.raw
        ? `${state.raw.name || state.raw.id}${state.meta.bundled ? " · bundled, read-only" : state.meta.saved ? "" : " · not saved yet"}${state.dirty ? " · unsaved changes" : ""}` : "";
      dialog.showModal();
    });
    dialog.addEventListener("click", (event) => {
      const r = dialog.getBoundingClientRect();
      if (event.target === dialog && (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom)) { dialog.close(); return; }
      const b = event.target.closest("[data-proxy]");
      if (!b || b.disabled) return;
      dialog.close();
      const target = $(`#${b.dataset.proxy}`);
      if (target) target.click();
    });
  }
  function trackHeaderHeight() {
    const top = $(".lab-top");
    const apply = () => document.documentElement.style.setProperty("--top-h", `${Math.ceil(top.getBoundingClientRect().height)}px`);
    if (typeof ResizeObserver === "function") new ResizeObserver(apply).observe(top);
    apply();
  }
  async function boot() {
    trackHeaderHeight();
    bindBar();
    bindNav();
    bindHeaderTuck();
    bindMoreMenu();
    try {
      state.catalog = await API.catalog();
      await loadRecipeList();
    } catch (error) {
      $("#loading").replaceChildren(h("div", { class: "callout bad" }, `The Society Lab could not load: ${error.message}`));
      return;
    }
    const section = location.hash.slice(1);
    state.section = SECTIONS.includes(section) ? section : "overview";
    const last = storageGet("society-lab:last");
    const pick = state.recipes.find((r) => r.id === last) || state.recipes.find((r) => r.bundled) || state.recipes[0];
    if (pick) await openRecipe(pick.id, { force: true });
    else setRaw(blankRecipe("my-society", "My society"), { bundled: false, saved: false }, { clean: false });
  }
  boot();
})();
