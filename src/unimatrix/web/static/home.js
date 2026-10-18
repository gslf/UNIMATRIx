"use strict";
/*
 * UNIMATRIx · Polis home: the hub of the society benchmark (start runs, follow them, compare models).
 *
 * Hash routes: #/ (home), #/play (start a run), #/leaderboard, #/runs, #/models, #/about.
 * Everything comes from /api; every piece of data is inserted with textContent or
 * setAttribute, never parsed as HTML. The animated hero uses window.SocietyVillage.
 */
(function () {
  const API = "/api";
  const POLL_MS = 4000;
  const NS = "http://www.w3.org/2000/svg";
  const HEALTH_KEY = "unimatrix.home.health.v1";
  const DOMAINS = ["D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8"];
  const DOMAIN_INFO = {
    D1: { name: "Information", unit: "Residents confidently right about live issues, plus forecast skill in bits", icon: "megaphone" },
    D2: { name: "Economy", unit: "Coins of community net worth created", icon: "coins" },
    D3: { name: "Public works", unit: "Service-weeks of completed projects", icon: "works" },
    D4: { name: "Commons", unit: "Fish harvested plus what the lake regrew", icon: "lake" },
    D5: { name: "Relationships", unit: "Trust-weeks, kept promises, settled disputes, relieved concerns", icon: "heart" },
    D6: { name: "Institutions", unit: "Residents × legitimacy × (1 − Gini), every week", icon: "columns" },
    D7: { name: "Transmission", unit: "Successor depth per skill each week, apprentices trained", icon: "book" },
    D8: { name: "Adaptation", unit: "Residents using changed conventions, integrated newcomers", icon: "cycle" },
  };
  // Same categorical order as SocietyViz.palette.categorical, so domains match the run report.
  const DOMAIN_COLORS = { D1: "#3987e5", D2: "#d95926", D3: "#199e70", D4: "#b59a00", D5: "#d55181", D6: "#008300", D7: "#9085e9", D8: "#e66767" };
  const REFS = ["passive", "naive", "expert", "oracle"];
  const STATUS_LABEL = { running: "Running", paused: "Paused", interrupted: "Interrupted", completed: "Completed", failed: "Failed", pending: "Pending" };
  const ROUTES = { home: "Home", play: "Start a run", leaderboard: "Leaderboard", runs: "Runs", models: "Models", about: "About" };
  const TAGS = { hard: ["Hard", "bad"], standard: ["Standard", "gold"], easy: ["Easy", "good"], "all-domains": ["All 8 domains", ""] };
  const NUMERIC_FIELDS = [["temperature", "temperature"], ["top-p", "top_p"], ["top-k", "top_k"], ["presence-penalty", "presence_penalty"],
    ["frequency-penalty", "frequency_penalty"], ["repeat-penalty", "repeat_penalty"], ["seed", "seed"], ["input-price", "input_price_per_million"],
    ["output-price", "output_price_per_million"], ["max-output", "max_output_tokens"], ["max-input", "max_input_tokens"]];
  const KEY_FIELDS = [["key-env", "api_key_env"], ["key-file", "api_key_file"]];

  const ICONS = {
    home: ["M4 11.5 12 5l8 6.5V20a1 1 0 0 1-1 1h-5v-6h-4v6H5a1 1 0 0 1-1-1z"],
    board: ["M4 20h16", "M6 20v-7h3v7M10.5 20V5h3v15M15 20v-10h3v10"],
    runs: ["M4 6h16M4 12h9M4 18h6", "M15.5 14.5v6l5-3z"],
    chip: ["M7 7h10v10H7z", "M10 10h4v4h-4zM10 3v4M14 3v4M10 17v4M14 17v4M3 10h4M3 14h4M17 10h4M17 14h4"],
    flask: ["M9 3h6M10 3v6l-5.5 9.5A1.7 1.7 0 0 0 6 21h12a1.7 1.7 0 0 0 1.5-2.5L14 9V3", "M7.5 15h9"],
    play: ["M8 5.5v13l10.5-6.5z"],
    pause: ["M8 5h3v14H8zM13 5h3v14h-3z"],
    plus: ["M12 5v14M5 12h14"],
    edit: ["M4 20h4L19 9l-4-4L4 16z", "M13.5 6.5l4 4"],
    trash: ["M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v6M14 11v6"],
    report: ["M5 3h10l4 4v14H5z", "M14 3v5h5M8.5 17v-3M12 17v-6M15.5 17v-4"],
    pulse: ["M3 12h4l2-5 4 10 2-5h6"],
    upload: ["M12 16V4M7 9l5-5 5 5M4 20h16"],
    users: ["c 9 8 3", "M3.5 19a5.5 5.5 0 0 1 11 0", "c 17 9 2.5", "M15.5 14.4a4.5 4.5 0 0 1 5 4.6"],
    calendar: ["M4 6h16v14H4z", "M4 10h16M8 3v5M16 3v5"],
    dice: ["M5 5h14v14H5z", "c 9 9 1.1", "c 15 15 1.1", "c 15 9 1.1", "c 9 15 1.1"],
    bolt: ["M13 3 5 14h6l-1 7 8-11h-6z"],
    chat: ["M4 5h16v11H9l-5 4z", "M8 9h8M8 12h5"],
    clock: ["c 12 12 8.5", "M12 7.5V12l3 2"],
    key: ["c 8 15 4", "M11 12l8.5-8.5M16.5 6.5l2.5 2.5M14.5 8.5l2 2"],
    person: ["c 12 8 4", "M4.5 20.5a7.5 7.5 0 0 1 15 0"],
    server: ["M4 4h16v6H4z", "M4 14h16v6H4z", "M8 7h.01M8 17h.01"],
    eye: ["M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z", "c 12 12 3"],
    smile: ["c 12 12 8.5", "M8.5 14a4 4 0 0 0 7 0M9 9.5h.01M15 9.5h.01"],
    heart: ["M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z"],
    lake: ["M3 17c3-3 5-3 8 0s5 3 10 0", "M3 12.5c3-3 5-3 8 0s5 3 10 0", "M8 7.5c1.5-1.5 3-1.5 4.5 0"],
    tag: ["M3 12V4h8l10 10-8 8z", "c 7.5 8.5 1.3"],
    check: ["M5 12.5l4.5 4.5L19 7.5"],
    refresh: ["M20 11a8 8 0 0 0-14.9-3", "M4 4v4h4", "M4 13a8 8 0 0 0 14.9 3", "M20 20v-4h-4"],
    alert: ["M12 3.5 2.5 20h19z", "M12 10v4M12 17v.5"],
    megaphone: ["M3 10v4h3l6 4V6L6 10z", "M16 9a4 4 0 0 1 0 6M18.5 6.5a7.5 7.5 0 0 1 0 11"],
    coins: ["M4 7c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3z", "M4 7v5c0 1.7 3.6 3 8 3s8-1.3 8-3V7", "M4 12v5c0 1.7 3.6 3 8 3s8-1.3 8-3v-5"],
    works: ["M4 20V10l8-6 8 6v10", "M9 20v-5h6v5M4 14h16"],
    columns: ["M3 9l9-5 9 5", "M5 9v9M9.5 9v9M14.5 9v9M19 9v9M3 20h18"],
    book: ["M12 6.5C10 5 7 4.5 4 5v13c3-.5 6 0 8 1.5 2-1.5 5-2 8-1.5V5c-3-.5-6 0-8 1.5z", "M12 6.5v13"],
    cycle: ["M20 12a8 8 0 0 1-14 5.3", "M4 12a8 8 0 0 1 14-5.3", "M18 3.5V7h-3.5", "M6 20.5V17h3.5"],
  };

  const state = {
    recipes: null,
    models: null,
    runs: null,
    personalities: null,
    recipeDetails: new Map(),
    sims: new Map(),
    boards: new Map(),
    health: loadHealth(),
    healthPending: new Map(),
    route: null,
    params: new URLSearchParams(),
    heroRecipe: null,
    heroShown: null,
    boardRecipe: null,
    runsFilter: { recipe: "", status: "all" },
    wizard: { step: 1, recipe: null, model: null, parallel: 1, seconds: 15 },
    starting: false,
    prevStatus: new Map(),
    expected: new Map(),
    pollTimer: 0,
    polling: false,
    loadError: null,
  };
  const caches = { runCards: new Map(), liveCards: new Map() };
  const shownValues = new Map();
  let village = null;
  let heroFrames = [];
  let heroToken = 0;
  let modelOriginal = {};
  let dialogOpener = null;
  let estimateTimer = 0;
  let estimateToken = 0;
  let svgCounter = 0;

  // ------------------------------------------------------------------ DOM helpers
  const $ = (id) => document.getElementById(id);
  const reduced = () => Boolean(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);

  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    if (attrs) setAttrs(el, attrs);
    appendAll(el, children);
    return el;
  }
  function setAttrs(el, attrs) {
    for (const key of Object.keys(attrs)) {
      const value = attrs[key];
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") el.className = value;
      else if (key === "text") el.textContent = value;
      else if (key === "style") setStyle(el, value);
      else if (key === "dataset") Object.assign(el.dataset, value);
      else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2), value);
      else if (value === true) el.setAttribute(key, "");
      else el.setAttribute(key, String(value));
    }
  }
  function setStyle(el, styles) {
    for (const key of Object.keys(styles)) {
      if (key.startsWith("--")) el.style.setProperty(key, styles[key]);
      else el.style[key] = styles[key];
    }
  }
  function appendAll(el, children) {
    for (const child of children.flat(Infinity)) {
      if (child === null || child === undefined || child === false) continue;
      el.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
  }
  function svgEl(tag, attrs, parent) {
    const el = document.createElementNS(NS, tag);
    for (const key of Object.keys(attrs || {})) el.setAttribute(key, String(attrs[key]));
    if (parent) parent.append(el);
    return el;
  }
  function icon(name, extra) {
    const svg = svgEl("svg", { viewBox: "0 0 24 24", class: "ic" + (extra ? " " + extra : ""), "aria-hidden": "true", focusable: "false" });
    for (const part of ICONS[name] || []) {
      if (part.startsWith("c ")) {
        const [cx, cy, r] = part.slice(2).split(" ");
        svgEl("circle", { cx, cy, r }, svg);
      } else svgEl("path", { d: part }, svg);
    }
    return svg;
  }
  function uid(prefix) { svgCounter += 1; return `${prefix}-${svgCounter}`; }

  // ------------------------------------------------------------------ formatting
  const intFormat = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 });
  const isNum = (v) => typeof v === "number" && Number.isFinite(v);
  const pts = (n) => (isNum(Number(n)) && n !== null && n !== "" ? intFormat.format(Math.round(Number(n))) : "—");
  const times = (x) => (isNum(x) ? `${x.toFixed(2)}× expert` : "—");
  function compact(n) {
    if (!isNum(n)) return "—";
    const a = Math.abs(n);
    if (a >= 1e9) return `${(n / 1e9).toFixed(1)}B`;
    if (a >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
    if (a >= 1e4) return `${Math.round(n / 1e3)}k`;
    if (a >= 1e3) return `${(n / 1e3).toFixed(1)}k`;
    return String(Math.round(n));
  }
  function minutesText(minutes) {
    if (!isNum(minutes)) return "—";
    if (minutes < 1) return `${Math.max(1, Math.round(minutes * 60))} s`;
    if (minutes < 60) return `${Math.round(minutes)} min`;
    const hrs = Math.floor(minutes / 60), rest = Math.round(minutes % 60);
    return rest ? `${hrs} h ${rest} min` : `${hrs} h`;
  }
  function relTime(iso) {
    if (!iso) return "";
    const then = new Date(iso).getTime();
    if (!Number.isFinite(then)) return "";
    const diff = (then - Date.now()) / 1000;
    const units = [["year", 31536000], ["month", 2592000], ["week", 604800], ["day", 86400], ["hour", 3600], ["minute", 60]];
    const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
    for (const [unit, size] of units) if (Math.abs(diff) >= size) return rtf.format(Math.round(diff / size), unit);
    return "just now";
  }
  const dateTime = (iso) => (iso ? new Date(iso).toLocaleString() : "");
  function splitName(name) {
    const text = String(name || "");
    const at = text.indexOf(" — ");
    return at > 0 ? [text.slice(0, at), text.slice(at + 3)] : [text, ""];
  }
  function excerpt(text, max) {
    const value = String(text || "").trim();
    if (value.length <= max) return value;
    const cut = value.slice(0, max);
    const stop = cut.lastIndexOf(". ");
    if (stop > max * 0.55) return cut.slice(0, stop + 1);
    return cut.slice(0, cut.lastIndexOf(" ")) + "…";
  }
  function humanize(text) {
    const value = String(text || "");
    if (/^[A-Za-z][A-Za-z0-9]*(_[A-Za-z0-9]+)+$/.test(value)) {
      const words = value.replace(/_/g, " ");
      return words.charAt(0).toUpperCase() + words.slice(1) + ".";
    }
    return value;
  }
  const capitalize = (text) => String(text).charAt(0).toUpperCase() + String(text).slice(1);
  function hashText(text) {
    let x = 2166136261;
    for (let i = 0; i < text.length; i++) { x ^= text.charCodeAt(i); x = Math.imul(x, 16777619); }
    return (x >>> 0) / 4294967296;
  }
  const hueColor = (text) => `hsl(${Math.round(hashText(String(text)) * 360)} 62% 64%)`;
  function initials(name) {
    const parts = String(name || "?").split(/[^A-Za-z0-9]+/).filter(Boolean);
    const letters = parts.slice(0, 2).map((p) => p[0]).join("");
    return (letters || "?").toUpperCase();
  }
  const reportHref = (id) => `/society/runs/${encodeURIComponent(id)}`;

  // ------------------------------------------------------------------ API
  class ApiError extends Error {
    constructor(status, message) { super(message); this.status = status; }
  }
  function detailOf(data) {
    if (!data) return "";
    if (typeof data === "string") return data.slice(0, 400);
    const detail = data.detail;
    if (typeof detail === "string") return humanize(detail);
    if (Array.isArray(detail)) return detail.map((d) => (d && d.msg ? `${(d.loc || []).slice(1).join(".")}: ${d.msg}` : String(d))).join("; ");
    return detail ? JSON.stringify(detail) : "";
  }
  async function api(path, options = {}) {
    const init = { cache: "no-store", headers: {} };
    if (options.method) init.method = options.method;
    if (options.body !== undefined) {
      init.method = init.method || "POST";
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(options.body);
    }
    if (options.signal) init.signal = options.signal;
    let response;
    try {
      response = await fetch(API + path, init);
    } catch (error) {
      if (error && (error.name === "TimeoutError" || error.name === "AbortError")) throw new ApiError(0, "The request timed out.");
      throw new ApiError(0, "Cannot reach the UNIMATRIx server. Is it still running?");
    }
    const text = await response.text();
    let data = null;
    if (text) { try { data = JSON.parse(text); } catch (error) { data = text; } }
    if (!response.ok) throw new ApiError(response.status, detailOf(data) || `Request failed (${response.status}).`);
    return data;
  }
  const post = (path, body = {}) => api(path, { method: "POST", body });

  // ------------------------------------------------------------------ feedback: toasts, count-ups
  function toast(message, kind = "info", options = {}) {
    const box = $("toasts");
    const close = h("button", { type: "button", class: "t-close", "aria-label": "Dismiss notification" }, "×");
    const body = h("div", { class: "t-body" }, message);
    if (options.link) body.append(" ", h("a", { href: options.link.href }, options.link.label));
    const el = h("div", { class: `toast ${kind}` }, h("i", { "aria-hidden": "true" }), body, close);
    box.append(el);
    while (box.children.length > 4) box.firstElementChild.remove();
    let timer = setTimeout(dismiss, options.ms || (kind === "bad" ? 9000 : 5200));
    function dismiss() {
      clearTimeout(timer);
      if (!el.isConnected) return;
      el.classList.add("leaving");
      setTimeout(() => el.remove(), 260);
    }
    close.addEventListener("click", dismiss);
    el.addEventListener("mouseenter", () => clearTimeout(timer));
    el.addEventListener("mouseleave", () => { clearTimeout(timer); timer = setTimeout(dismiss, 3000); });
    return el;
  }

  function countUp(el, value, key, format = pts, duration = 1100) {
    const target = Number(value);
    if (!isNum(target)) { el.textContent = format(value); return; }
    const from = key && shownValues.has(key) ? shownValues.get(key) : 0;
    if (key) shownValues.set(key, target);
    if (reduced() || from === target) { el.textContent = format(target); return; }
    const t0 = performance.now();
    el.textContent = format(from);
    const step = (now) => {
      const t = Math.min(1, (now - t0) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      el.textContent = format(from + (target - from) * eased);
      if (t < 1 && el.isConnected) requestAnimationFrame(step);
      else el.textContent = format(target);
    };
    requestAnimationFrame(step);
  }

  // ------------------------------------------------------------------ small components
  function tag(text, kind, iconName) {
    return h("span", { class: "tag" + (kind ? " " + kind : "") }, iconName ? icon(iconName) : null, text);
  }
  function statusChip(status) {
    return h("span", { class: "status", "data-status": status }, h("i", { "aria-hidden": "true" }), STATUS_LABEL[status] || status);
  }
  function liveTag() {
    return h("span", { class: "live-tag" }, h("i", { "aria-hidden": "true" }), "LIVE");
  }
  function collapsedTag(text) {
    return h("span", { class: "tag bad collapsed-tag" }, icon("alert"), text || "Collapsed");
  }
  function skeletonBlocks(count, cls) {
    return Array.from({ length: count }, () => h("div", { class: `skeleton sk-block ${cls || ""}`, "aria-hidden": "true" },
      h("div", { class: "sk-line w60" }), h("div", { class: "sk-line w80" }), h("div", { class: "sk-line w40" })));
  }
  function emptyNote(iconName, title, text, action) {
    return h("div", { class: "empty-note" }, icon(iconName), h("b", null, title), text ? h("p", null, text) : null, action || null);
  }
  function setBusy(button, busy, label) {
    if (!button) return;
    if (busy) {
      const span = button.querySelector("span");
      if (span && !button.dataset.spanLabel) button.dataset.spanLabel = span.textContent;
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
      if (label && span) span.textContent = label;
    } else {
      button.disabled = false;
      button.removeAttribute("aria-busy");
      const span = button.querySelector("span");
      if (span && button.dataset.spanLabel) span.textContent = button.dataset.spanLabel;
    }
  }
  function actionButton(iconName, label, cls, handler, iconCls) {
    const button = h("button", { type: "button", class: cls || "" }, icon(iconName, iconCls), h("span", null, label));
    button.dataset.spanLabel = label;
    button.addEventListener("click", (event) => handler(event.currentTarget));
    return button;
  }
  function linkButton(iconName, label, href, cls, iconCls) {
    return h("a", { class: "button " + (cls || ""), href }, icon(iconName, iconCls), h("span", null, label));
  }

  /** Progress ring: an SVG arc with a label; `set` animates the arc. */
  function progressRing(small) {
    const C = 2 * Math.PI * 42;
    const wrap = h("div", { class: "ring" + (small ? " sm" : ""), role: "img" });
    const svg = svgEl("svg", { viewBox: "0 0 100 100", "aria-hidden": "true" });
    svgEl("circle", { class: "track", cx: 50, cy: 50, r: 42 }, svg);
    const arc = svgEl("circle", { class: "val", cx: 50, cy: 50, r: 42, "stroke-dasharray": C.toFixed(2), "stroke-dashoffset": C.toFixed(2) }, svg);
    const big = h("b"), small2 = h("span");
    wrap.append(svg, h("div", { class: "label" }, big, small2));
    wrap.set = (fraction, top, bottom, status, label) => {
      const f = Math.max(0, Math.min(1, Number(fraction) || 0));
      wrap.dataset.status = status || "";
      big.textContent = top;
      small2.textContent = bottom;
      wrap.setAttribute("aria-label", label);
      const offset = (C * (1 - f)).toFixed(2);
      if (reduced()) arc.setAttribute("stroke-dashoffset", offset);
      else requestAnimationFrame(() => requestAnimationFrame(() => arc.setAttribute("stroke-dashoffset", offset)));
    };
    return wrap;
  }

  function domainIcon(domain) {
    return h("span", { class: "domain-icon", style: { "--c": DOMAIN_COLORS[domain] } }, icon(DOMAIN_INFO[domain].icon));
  }

  /** A stylised landscape for a recipe card: day for ordinary years, dusk for hard ones. */
  function recipeArt(recipe) {
    const hard = (recipe.tags || []).includes("hard");
    const seed = hashText(recipe.id);
    const pal = hard
      ? { sky: ["#1a0f12", "#5a2618"], sun: "#ff7a4f", hill1: "#3a2f22", hill2: "#4f3f26", lake: "#1f3b4d", roof: "#7a3524", wall: "#8f7a5a" }
      : { sky: ["#0f1d2e", "#3b5e52"], sun: "#ffd75a", hill1: "#244a2c", hill2: "#356b38", lake: "#2a6f9e", roof: "#9a4b2f", wall: "#d8c08a" };
    const id = uid("sky");
    const svg = svgEl("svg", { viewBox: "0 0 400 112", preserveAspectRatio: "xMidYMid slice", "aria-hidden": "true" });
    const grad = svgEl("linearGradient", { id, x1: 0, y1: 0, x2: 0, y2: 1 }, svgEl("defs", {}, svg));
    svgEl("stop", { offset: 0, "stop-color": pal.sky[0] }, grad);
    svgEl("stop", { offset: 1, "stop-color": pal.sky[1] }, grad);
    svgEl("rect", { width: 400, height: 112, fill: `url(#${id})` }, svg);
    const sx = 250 + seed * 110;
    svgEl("circle", { class: "art-sun", cx: sx, cy: 38, r: 30, fill: pal.sun, opacity: 0.12 }, svg);
    svgEl("circle", { cx: sx, cy: 38, r: 13, fill: pal.sun, opacity: hard ? 0.75 : 0.9 }, svg);
    for (let i = 0; i < 2; i++) {
      const g = svgEl("g", { class: "art-cloud", style: `animation-delay:${-i * 17 - seed * 20}s` }, svg);
      svgEl("ellipse", { cx: 40 + i * 150, cy: 22 + i * 10, rx: 30, ry: 7, fill: hard ? "#2a1a1a" : "#c9d6e0", opacity: hard ? 0.8 : 0.18 }, g);
    }
    svgEl("path", { d: `M0 78 Q ${60 + seed * 40} 44 140 70 T 290 62 T 400 70 V112 H0z`, fill: pal.hill1 }, svg);
    svgEl("path", { d: "M0 92 Q 90 70 190 88 T 400 84 V112 H0z", fill: pal.hill2 }, svg);
    svgEl("ellipse", { cx: 88, cy: 98, rx: 74, ry: 11, fill: pal.lake, opacity: hard ? 0.7 : 0.9 }, svg);
    svgEl("path", { d: "M40 98h22M84 101h30M58 95h12", stroke: "rgba(255,255,255,0.35)", "stroke-width": 1.2, "stroke-linecap": "round" }, svg);
    for (let i = 0; i < 6; i++) {
      const x = 205 + i * 26 + Math.round(hashText(recipe.id + i) * 10);
      const y = 84 - (i % 2) * 5;
      svgEl("rect", { x, y, width: 14, height: 10, fill: pal.wall, opacity: 0.92 }, svg);
      svgEl("path", { d: `M${x - 2} ${y} L${x + 7} ${y - 7} L${x + 16} ${y}z`, fill: pal.roof }, svg);
      if (!hard || i % 2 === 0) svgEl("rect", { class: "art-window", x: x + 5, y: y + 3, width: 4, height: 4, fill: "#ffe27a", opacity: hard ? 0.9 : 0.55, style: `animation-delay:${i * 0.7}s` }, svg);
    }
    if (hard) svgEl("path", { class: "art-bolt", d: "M330 10 l-8 18 h7 l-6 16 l16 -22 h-8 l6 -12z", fill: "#ffe27a", opacity: 0 }, svg);
    return svg;
  }

  // ------------------------------------------------------------------ data helpers
  const isRunning = (run) => run.status === "running";
  const seedsOf = (run) => Object.values(run.seeds || {});
  function runWeek(run) {
    const seeds = seedsOf(run);
    if (!seeds.length) return 0;
    return Math.round(seeds.reduce((sum, s) => sum + (s.week || 0), 0) / seeds.length);
  }
  function liveScore(run) {
    const values = seedsOf(run).map((s) => (s.status === "completed" ? s.total : s.points)).filter(isNum);
    return values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
  }
  const fullyCollapsed = (score) => Boolean(score && score.seeds > 0 && score.collapsed >= score.seeds);
  function collapseWeek(run) {
    const weeks = seedsOf(run).filter((s) => s.collapsed && isNum(s.collapsed.week)).map((s) => s.collapsed.week);
    return weeks.length ? Math.min(...weeks) : null;
  }
  function collapseLabel(run) {
    const seed = seedsOf(run).find((s) => s.collapsed && s.collapsed.label);
    return seed ? seed.collapsed.label : "";
  }
  function recipeById(id) { return (state.recipes || []).find((r) => r.id === id) || null; }
  function modelById(id) { return (state.models || []).find((m) => m.id === id && m.config) || null; }
  function defaultRecipeId() {
    const list = state.recipes || [];
    return (list.find((r) => r.id === "standard-v2.1") || list[0] || {}).id || null;
  }
  function firstSeed(recipe) {
    const seeds = (recipe && recipe.seeds) || [];
    return seeds.includes(1) ? 1 : (seeds[0] ?? 1);
  }
  function bestRun(recipe) {
    let best = null;
    for (const run of state.runs || []) {
      if (run.recipe_id !== recipe.id || run.status !== "completed" || !run.score) continue;
      if (recipe.hash && run.recipe_hash !== recipe.hash) continue;
      if (!best || run.score.total > best.score.total) best = run;
    }
    return best;
  }
  function modelStats(id) {
    const runs = (state.runs || []).filter((r) => r.model_id === id);
    let best = null;
    for (const run of runs) if (run.status === "completed" && run.score && (!best || run.score.total > best.score.total)) best = run;
    return { count: runs.length, best };
  }

  function recipeDetail(id) {
    if (!state.recipeDetails.has(id)) {
      const promise = api(`/polis/recipes/${encodeURIComponent(id)}`);
      promise.catch(() => state.recipeDetails.delete(id));
      state.recipeDetails.set(id, promise);
    }
    return state.recipeDetails.get(id);
  }
  function simulation(id) {
    if (!state.sims.has(id)) {
      const promise = recipeDetail(id).then((detail) =>
        post("/polis/simulate", { recipe: detail.raw, policy: "expert", seed: firstSeed(recipeById(id)) }));
      promise.catch(() => state.sims.delete(id));
      state.sims.set(id, promise);
    }
    return state.sims.get(id);
  }

  function loadHealth() {
    try {
      const raw = window.localStorage.getItem(HEALTH_KEY);
      const data = raw ? JSON.parse(raw) : {};
      const map = new Map();
      for (const [id, value] of Object.entries(data)) if (value && (value.state === "ok" || value.state === "error")) map.set(id, value);
      return map;
    } catch (error) {
      return new Map();
    }
  }
  function saveHealth() {
    try {
      const data = {};
      for (const [id, value] of state.health) if (value.state === "ok" || value.state === "error") data[id] = value;
      window.localStorage.setItem(HEALTH_KEY, JSON.stringify(data));
    } catch (error) { /* storage unavailable: health stays in memory */ }
  }

  // ------------------------------------------------------------------ router
  function parseRoute() {
    const raw = location.hash.replace(/^#\/?/, "");
    const at = raw.indexOf("?");
    const path = at >= 0 ? raw.slice(0, at) : raw;
    const query = at >= 0 ? raw.slice(at + 1) : "";
    return { name: Object.prototype.hasOwnProperty.call(ROUTES, path) ? path : "home", params: new URLSearchParams(query) };
  }
  function onRoute(initial) {
    if (location.hash && !location.hash.startsWith("#/")) return;
    const { name, params } = parseRoute();
    const changed = name !== state.route;
    state.route = name;
    state.params = params;
    for (const view of document.querySelectorAll(".view")) view.hidden = view.dataset.view !== name;
    for (const link of document.querySelectorAll("[data-route]")) {
      if (link.dataset.route === name) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    }
    moveInk();
    document.title = name === "home" ? "UNIMATRIx · Polis" : `${ROUTES[name]} · UNIMATRIx · Polis`;
    enterView(name, params);
    if (!initial && changed) {
      window.scrollTo({ top: 0, behavior: "instant" });
      const heading = document.querySelector(`#view-${name} h1`);
      if (heading) heading.focus({ preventScroll: true });
    }
  }
  function enterView(name, params) {
    if (name === "home") renderHome();
    else if (name === "play") enterPlay(params);
    else if (name === "leaderboard") enterBoard(params.get("recipe"));
    else if (name === "runs") renderRuns();
    else if (name === "models") renderModels();
    else if (name === "about") renderAbout();
  }
  function moveInk() {
    const nav = $("tabbar"), ink = $("tab-ink");
    const active = nav.querySelector('a[aria-current="page"]');
    if (!active) { ink.style.setProperty("--ink-o", "0"); return; }
    const navRect = nav.getBoundingClientRect(), rect = active.getBoundingClientRect();
    const width = Math.min(rect.width * 0.6, 46);
    ink.style.setProperty("--ink-x", `${rect.left - navRect.left + (rect.width - width) / 2}px`);
    ink.style.setProperty("--ink-w", `${width}px`);
    ink.style.setProperty("--ink-o", "1");
  }

  // ------------------------------------------------------------------ runs: loading, polling, transitions
  function setRuns(runs, initial) {
    const transitions = [];
    for (const run of runs) {
      const before = state.prevStatus.get(run.id);
      if (!initial && before && before !== run.status) transitions.push([run, before]);
    }
    state.prevStatus = new Map(runs.map((r) => [r.id, r.status]));
    state.runs = runs;
    for (const [run, before] of transitions) announceTransition(run, before);
  }
  async function refreshRuns() {
    const runs = await api("/polis/runs");
    setRuns(runs, false);
    refreshRunViews();
    schedulePoll();
  }
  function schedulePoll() {
    clearTimeout(state.pollTimer);
    if (!(state.runs || []).some(isRunning)) return;
    state.pollTimer = setTimeout(pollOnce, POLL_MS);
  }
  async function pollOnce() {
    if (document.hidden || state.polling) return;
    state.polling = true;
    try {
      await refreshRuns();
      hideGlobalError();
    } catch (error) {
      showGlobalError(error.message, false);
      clearTimeout(state.pollTimer);
      state.pollTimer = setTimeout(pollOnce, POLL_MS * 2);
    } finally {
      state.polling = false;
    }
  }
  function announceTransition(run, before) {
    const expected = state.expected.get(run.id);
    state.expected.delete(run.id);
    if (expected === run.status) return;
    const link = { href: reportHref(run.id), label: "Open the report" };
    if (before === "running" && run.status === "completed") {
      state.boards.delete(run.recipe_id);
      if (fullyCollapsed(run.score)) {
        toast(`The village collapsed under ${run.model}. Final score: ${pts(run.score.total)} points after the collapse penalty.`, "bad", { link });
      } else {
        const total = run.score ? pts(run.score.total) : "—";
        const rel = run.score && isNum(run.score.relative_to_expert) ? ` (${times(run.score.relative_to_expert)})` : "";
        toast(`The run of ${run.model} finished: ${total} points${rel}.`, "good", { link, ms: 9000 });
      }
    } else if (before === "running" && run.status === "failed") {
      toast(`The run of ${run.model} failed${run.error ? ": " + run.error : "."}`, "bad", { link });
    } else if (before === "running" && (run.status === "paused" || run.status === "interrupted")) {
      toast(`The run of ${run.model} is ${run.status}.`, "info", { link });
    } else if (run.status === "running" && before !== "running") {
      toast(`The run of ${run.model} is live.`, "good", { link: { href: reportHref(run.id), label: "Watch it" } });
    }
  }
  function refreshRunViews() {
    renderLiveIndicators();
    if (state.route === "home") { renderContinue(); renderQuickStats(); renderRecipeCards(); }
    else if (state.route === "runs") renderRuns();
    else if (state.route === "play") updateStartAvailability();
    else if (state.route === "leaderboard" && !state.boards.has(state.boardRecipe)) renderBoard();
    else if (state.route === "models") renderModels();
  }
  function renderLiveIndicators() {
    const live = (state.runs || []).filter(isRunning);
    const pill = $("live-pill");
    $("tab-live-dot").hidden = !live.length;
    pill.hidden = !live.length;
    if (live.length) {
      const run = live[0];
      pill.setAttribute("href", reportHref(run.id));
      $("live-pill-text").textContent = `W${runWeek(run)}/${run.weeks}`;
      pill.setAttribute("aria-label", `Live run: ${run.model}, week ${runWeek(run)} of ${run.weeks}. Open its report.`);
    }
  }

  async function pauseRun(run, button) {
    setBusy(button, true, "Pausing…");
    state.expected.set(run.id, "paused");
    try {
      await post(`/polis/runs/${encodeURIComponent(run.id)}/pause`);
      toast(`Paused the run of ${run.model}. Resume it at any time.`, "info");
      await refreshRuns();
    } catch (error) {
      state.expected.delete(run.id);
      toast(error.message, "bad");
    } finally {
      setBusy(button, false);
    }
  }
  async function resumeRun(run, button) {
    setBusy(button, true, "Resuming…");
    state.expected.set(run.id, "running");
    try {
      await post(`/polis/runs/${encodeURIComponent(run.id)}/resume`);
      toast(`Resumed the run of ${run.model}.`, "good", { link: { href: reportHref(run.id), label: "Watch it" } });
      await refreshRuns();
    } catch (error) {
      state.expected.delete(run.id);
      const live = (state.runs || []).find(isRunning);
      toast(error.message, "bad", error.status === 409 && live ? { link: { href: reportHref(live.id), label: "Open the running run" } } : {});
    } finally {
      setBusy(button, false);
    }
  }
  async function deleteRun(run, button) {
    const ok = await confirmDialog({
      title: "Delete this run?",
      text: `The society run of ${run.model} on ${run.recipe_name} (${run.id}) is removed with its decisions and report. This cannot be undone.`,
      ok: "Delete run",
    });
    if (!ok) return;
    setBusy(button, true, "Deleting…");
    try {
      await api(`/polis/runs/${encodeURIComponent(run.id)}`, { method: "DELETE" });
      state.boards.delete(run.recipe_id);
      toast(`Deleted the run of ${run.model}.`, "info");
      await refreshRuns();
    } catch (error) {
      toast(error.message, "bad");
      setBusy(button, false);
    }
  }

  // ------------------------------------------------------------------ HOME
  function renderHome() {
    renderHeroRecipes();
    if (state.recipes) {
      if (!state.heroRecipe || !recipeById(state.heroRecipe)) state.heroRecipe = defaultRecipeId();
      updateHeroLinks();
      if (state.heroRecipe) loadHero(state.heroRecipe);
      else heroMessage("No society recipe is available.");
    }
    renderContinue();
    renderQuickStats();
    renderRecipeCards();
  }

  function renderHeroRecipes() {
    const box = $("hero-recipes");
    if (!state.recipes) { box.replaceChildren(h("span", { class: "muted small" }, "Loading villages…")); return; }
    box.closest("fieldset").hidden = state.recipes.length < 2;
    if (box.dataset.sig === sigOfRecipes() && box.children.length) return;
    box.dataset.sig = sigOfRecipes();
    box.replaceChildren(...state.recipes.map((r) => segOption("hero-recipe", r.id, splitName(r.name)[0], r.id === state.heroRecipe, (id) => {
      state.heroRecipe = id;
      updateHeroLinks();
      loadHero(id);
    })));
  }
  const sigOfRecipes = () => (state.recipes || []).map((r) => r.id + r.hash).join("|");
  function segOption(name, value, label, checked, onChange, extra) {
    const input = h("input", { type: "radio", name, value });
    input.checked = Boolean(checked);
    input.addEventListener("change", () => { if (input.checked) onChange(value); });
    return h("label", null, input, h("span", null, label), extra || null);
  }
  function updateHeroLinks() {
    const recipe = recipeById(state.heroRecipe);
    if (!recipe) return;
    $("hero-start").setAttribute("href", `#/play?recipe=${encodeURIComponent(recipe.id)}`);
    $("hero-board").setAttribute("href", `#/leaderboard?recipe=${encodeURIComponent(recipe.id)}`);
    $("hero-note").textContent = `Preview: the expert reference steward governs ${splitName(recipe.name)[0]} (seed ${firstSeed(recipe)}). Your model takes its place in a run.`;
    for (const input of $("hero-recipes").querySelectorAll("input")) input.checked = input.value === recipe.id;
  }
  function heroMessage(text, spinner) {
    const loading = $("hero-loading");
    loading.classList.remove("done");
    loading.querySelector(".spinner").hidden = !spinner;
    $("hero-loading-text").textContent = text;
  }
  async function loadHero(id) {
    if (state.heroShown === id) return;
    const token = ++heroToken;
    state.heroShown = id;
    heroMessage("Summoning the village…", true);
    try {
      const [detail, sim] = await Promise.all([recipeDetail(id), simulation(id)]);
      if (token !== heroToken) return;
      if (!window.SocietyVillage) throw new Error("the village renderer did not load");
      if (!village) mountVillage();
      heroFrames = Array.isArray(sim.frames) ? sim.frames : [];
      shownValues.delete("hud-points");
      village.load({ frames: heroFrames, residents: sim.residents || [], factions: sim.factions || [], recipe: detail.recipe });
      $("hero-hud").hidden = false;
      $("hero-toggle").hidden = heroFrames.length < 2;
      $("hero-loading").classList.add("done");
      syncHeroToggle();
    } catch (error) {
      if (token !== heroToken) return;
      state.heroShown = null;
      heroMessage(`The preview could not be played: ${error.message}`, false);
    }
  }
  function mountVillage() {
    village = window.SocietyVillage.mount($("hero-village"), {
      loop: true, autoplay: true, weekMs: 2100, speed: 1, captions: window.innerWidth >= 600, captionsAt: "top",
      steward: true, thoughts: false, notebook: false, interactive: false, labels: false, followLive: false,
    });
    village.on("frame", onHeroFrame);
    village.on("play", syncHeroToggle);
    village.on("pause", syncHeroToggle);
  }
  function onHeroFrame(index) {
    const frame = heroFrames[index];
    if (!frame) return;
    const metrics = frame.metrics || {};
    $("hud-week").textContent = `Week ${frame.week}`;
    $("hud-season").textContent = frame.season || "";
    $("hud-pop").textContent = isNum(metrics.population) ? String(metrics.population) : "–";
    $("hud-wb").textContent = isNum(metrics.wellbeing) ? metrics.wellbeing.toFixed(1) : "–";
    const total = Object.values(frame.points || {}).reduce((a, b) => a + (isNum(b) ? b : 0), 0);
    const el = $("hud-points");
    const previous = shownValues.get("hud-points") ?? 0;
    if (total < previous || reduced()) { shownValues.set("hud-points", total); el.textContent = pts(total); }
    else {
      countUp(el, total, "hud-points", pts, 700);
      if (total > previous) { el.classList.remove("bump"); void el.offsetWidth; el.classList.add("bump"); }
    }
  }
  function syncHeroToggle() {
    if (!village) return;
    const playing = village.isPlaying();
    const button = $("hero-toggle");
    button.setAttribute("aria-pressed", playing ? "false" : "true");
    $("hero-toggle-label").textContent = playing ? "Pause the village animation" : "Play the village animation";
    button.title = playing ? "Pause" : "Play";
    $("hero-toggle-icon").setAttribute("d", playing ? ICONS.pause[0] : ICONS.play[0]);
  }

  /** "Continue watching": the live run, or the most recent paused one. */
  function renderContinue() {
    const box = $("continue");
    if (!state.runs) { box.replaceChildren(); caches.liveCards.clear(); return; }
    const live = state.runs.filter(isRunning);
    const list = live.length ? live : state.runs.filter((r) => r.status === "paused" || r.status === "interrupted")
      .sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at))).slice(0, 1);
    reconcile(box, list, caches.liveCards, createLiveCard, updateLiveCard);
  }
  function createLiveCard(run) {
    const ring = progressRing(false);
    const statusSlot = h("span");
    const title = h("h2", { class: "lc-title" });
    const sub = h("p", { class: "lc-sub" });
    const score = h("strong");
    const scoreNote = h("span");
    const vitals = h("div", { class: "lc-vitals" });
    const actions = h("div", { class: "lc-actions" });
    const card = h("article", { class: "card live-card enter", "data-run-card": run.id, "aria-label": "Continue watching" },
      ring,
      h("div", { class: "lc-main" },
        h("div", { class: "lc-top" }, statusSlot, h("span", { class: "eyebrow", style: { margin: 0 } }, "Continue watching")),
        title, sub, h("div", { class: "lc-score" }, score, scoreNote)),
      vitals, actions);
    card.parts = { ring, statusSlot, title, sub, score, scoreNote, vitals, actions };
    return card;
  }
  function updateLiveCard(card, run) {
    const p = card.parts;
    const running = isRunning(run);
    card.classList.toggle("is-live", running);
    card.classList.toggle("is-paused", !running);
    const week = runWeek(run);
    p.ring.set(run.progress.total ? run.progress.weeks / run.progress.total : 0, String(week), `of ${run.weeks}`, run.status,
      `Week ${week} of ${run.weeks}${seedsOf(run).length > 1 ? `, ${seedsOf(run).length} seeds` : ""}`);
    if (card.dataset.status !== run.status) {
      p.statusSlot.replaceChildren(running ? liveTag() : statusChip(run.status));
      p.actions.replaceChildren(
        linkButton(running ? "eye" : "report", running ? "Watch live" : "Open report", reportHref(run.id), "primary"),
        running ? actionButton("pause", "Pause", "", (b) => pauseRun(run, b), "fill") : actionButton("play", "Resume", "", (b) => resumeRun(run, b), "fill"));
      card.dataset.status = run.status;
    }
    p.title.textContent = run.model;
    const seeds = seedsOf(run).length;
    p.sub.textContent = `${run.recipe_name} · ${run.snapshot || "no snapshot"}${seeds > 1 ? ` · ${seeds} seeds` : ""} · started ${relTime(run.created_at)}`;
    const value = liveScore(run);
    countUp(p.score, value ?? 0, `live-${run.id}`);
    p.scoreNote.textContent = running ? "points so far" : "points when paused";
    const seed = seedsOf(run).find((s) => s.metrics) || {};
    p.vitals.replaceChildren(...vitalsOf(seed.metrics));
  }
  function vitalsOf(metrics) {
    if (!metrics) return [h("p", { class: "muted small" }, "Vital signs appear after the first week.")];
    const tone = (v, lo, mid) => (v < lo ? "var(--bad)" : v < mid ? "var(--watch)" : "var(--good)");
    const items = [
      ["users", "Residents", isNum(metrics.population) ? String(metrics.population) : "–", null],
      ["smile", "Wellbeing", isNum(metrics.wellbeing) ? `${metrics.wellbeing.toFixed(1)} / 10` : "–", isNum(metrics.wellbeing) ? [metrics.wellbeing / 10, tone(metrics.wellbeing, 4, 6)] : null],
      ["heart", "Trust", isNum(metrics.trust) ? `${Math.round(metrics.trust * 100)}%` : "–", isNum(metrics.trust) ? [metrics.trust, tone(metrics.trust, 0.35, 0.55)] : null],
      ["lake", "Lake stock", isNum(metrics.commons) ? `${Math.round(metrics.commons * 100)}%` : "–", isNum(metrics.commons) ? [metrics.commons, tone(metrics.commons, 0.3, 0.5)] : null],
    ];
    return items.map(([name, label, value, meter]) => h("div", { class: "vital" }, icon(name),
      h("div", null, h("b", null, value), h("span", null, label),
        meter ? h("div", { class: "meter", "aria-hidden": "true" }, h("i", { style: { width: `${Math.round(Math.max(0, Math.min(1, meter[0])) * 100)}%`, background: meter[1] } })) : null)));
  }

  function renderQuickStats() {
    const box = $("quick-stats");
    if (!state.runs || !state.models || !state.recipes) {
      if (!box.children.length) box.replaceChildren(...Array.from({ length: 4 }, () => h("div", { class: "skeleton", style: { minHeight: "68px" }, "aria-hidden": "true" })));
      return;
    }
    const first = !box.dataset.ready;
    box.dataset.ready = "1";
    const runs = state.runs;
    const live = runs.filter(isRunning).length;
    const completed = runs.filter((r) => r.status === "completed").length;
    const valid = state.models.filter((m) => m.config).length;
    const invalid = state.models.length - valid;
    const tiles = [
      statTile("runs", "", runs.length, "Runs played", `${completed} completed${live ? ` · ${live} live` : ""}`, "#/runs", "qs-runs"),
      statTile("chip", "blue", valid, valid === 1 ? "Model ready" : "Models ready", invalid ? `${invalid} need attention` : "Saved configurations", "#/models", "qs-models"),
    ];
    for (const recipe of state.recipes.slice(0, 4)) {
      const best = bestRun(recipe);
      tiles.push(statTile("board", recipe.tags && recipe.tags.includes("hard") ? "violet" : "green",
        best ? best.score.total : null, `Best · ${splitName(recipe.name)[0]}`, best ? best.model : "No completed run yet",
        `#/leaderboard?recipe=${encodeURIComponent(recipe.id)}`, `qs-best-${recipe.id}`));
    }
    tiles.forEach((tile, i) => { if (first) { tile.classList.add("enter"); tile.style.setProperty("--i", i); } });
    box.replaceChildren(...tiles);
  }
  function statTile(iconName, tone, value, label, note, href, key) {
    const strong = h("strong");
    if (value === null || value === undefined) strong.textContent = "—";
    else countUp(strong, value, key);
    return h("a", { class: "qs", href }, h("span", { class: `qs-icon ${tone}` }, icon(iconName)),
      h("span", { class: "qs-text" }, strong, h("span", null, label), h("small", null, note)));
  }

  function renderRecipeCards() {
    const box = $("recipe-cards");
    if (!state.recipes) {
      if (!box.children.length) box.replaceChildren(...skeletonBlocks(2));
      return;
    }
    const sig = sigOfRecipes() + "#" + state.recipes.map((r) => { const b = bestRun(r); return b ? b.id + b.score.total : "-"; }).join(",");
    if (box.dataset.sig === sig) return;
    const first = !box.dataset.sig;
    box.dataset.sig = sig;
    if (!state.recipes.length) {
      box.replaceChildren(emptyNote("flask", "No society recipes", "Design one in the Society Lab.", linkButton("flask", "Open the Society Lab", "/recipe-lab", "primary")));
      return;
    }
    box.classList.toggle("single", state.recipes.length === 1);
    box.replaceChildren(...state.recipes.map((recipe, i) => recipeCard(recipe, i, first)));
  }
  function recipeTags(recipe) {
    const list = (recipe.tags || []).map((name) => (TAGS[name] ? tag(TAGS[name][0], TAGS[name][1]) : tag(capitalize(String(name).replace(/-/g, " ")))));
    list.push(recipe.bundled ? tag("Bundled", "info") : tag(recipe.author ? `Lab · ${recipe.author}` : "Lab-authored", "gold", "flask"));
    return list;
  }
  function recipeStats(recipe) {
    const seeds = (recipe.seeds || []).length;
    const stat = (iconName, value, label) => h("span", { class: "rs" }, icon(iconName), h("b", null, value), label);
    return h("div", { class: "recipe-stats" },
      stat("users", String(recipe.residents), "residents"),
      stat("calendar", String(recipe.ticks), "weeks"),
      stat("dice", String(seeds), seeds === 1 ? "seed" : "seeds"),
      stat("bolt", String(recipe.actions_per_week), "actions / week"),
      stat("chat", intFormat.format(recipe.decisions), "model calls"));
  }
  function recipeCard(recipe, index, animate) {
    const [title, subtitle] = splitName(recipe.name);
    const best = bestRun(recipe);
    const preview = actionButton("eye", "Preview", "small", () => {
      state.heroRecipe = recipe.id;
      updateHeroLinks();
      loadHero(recipe.id);
      $("hero").scrollIntoView({ behavior: reduced() ? "auto" : "smooth", block: "start" });
    });
    preview.setAttribute("aria-label", `Preview ${title} in the animated village`);
    return h("article", { class: "card recipe-card" + (animate ? " enter" : ""), style: { "--i": index } },
      h("div", { class: "recipe-art" }, recipeArt(recipe), h("div", { class: "art-tags" }, recipeTags(recipe))),
      h("div", { class: "recipe-body" },
        h("h3", null, title, subtitle ? h("small", null, subtitle) : null),
        h("p", null, excerpt(recipe.description, 190)),
        recipeStats(recipe),
        h("div", { class: "recipe-best" }, icon("board"), best
          ? h("span", null, "Best: ", h("b", null, pts(best.score.total)), ` by ${best.model}`)
          : h("span", null, "No completed run yet.")),
        h("div", { class: "recipe-actions" },
          linkButton("play", "Play", `#/play?recipe=${encodeURIComponent(recipe.id)}`, "primary small", "fill"),
          linkButton("board", "Leaderboard", `#/leaderboard?recipe=${encodeURIComponent(recipe.id)}`, "small"),
          preview)));
  }

  /** Keyed list rendering: existing elements are updated in place, so polling never restarts animations or steals focus. */
  function reconcile(container, items, cache, create, update) {
    const keep = new Set(items.map((item) => item.id));
    for (const [key, el] of cache) if (!keep.has(key)) { el.remove(); cache.delete(key); }
    const known = new Set(cache.values());
    for (const child of [...container.children]) if (!known.has(child)) child.remove();
    items.forEach((item, i) => {
      let el = cache.get(item.id);
      if (!el) { el = create(item, i); el.style.setProperty("--i", i); cache.set(item.id, el); }
      update(el, item);
      if (container.children[i] !== el) container.insertBefore(el, container.children[i] || null);
    });
  }

  // ------------------------------------------------------------------ START A RUN (wizard)
  function enterPlay(params) {
    const wiz = state.wizard;
    if (!state.recipes || !state.models) { renderPlay(); return; }
    const recipeParam = params.get("recipe"), modelParam = params.get("model");
    if (recipeParam && recipeById(recipeParam)) wiz.recipe = recipeParam;
    if (modelParam && modelById(modelParam)) wiz.model = modelParam;
    if (wiz.recipe && !recipeById(wiz.recipe)) wiz.recipe = null;
    if (wiz.model && !modelById(wiz.model)) wiz.model = null;
    if (!wiz.recipe && state.recipes.length === 1) wiz.recipe = state.recipes[0].id;
    if (recipeParam || modelParam) wiz.step = wiz.recipe && wiz.model ? 3 : wiz.recipe ? 2 : 1;
    if (wiz.step === 2 && !wiz.recipe) wiz.step = 1;
    if (wiz.step === 3 && !(wiz.recipe && wiz.model)) wiz.step = wiz.recipe ? 2 : 1;
    clampParallel();
    renderPlay();
  }
  /** Replace a fieldset's content while keeping its (visually hidden) legend. */
  function fillFieldset(box, nodes) {
    let legend = box.querySelector(":scope > legend");
    if (!legend) legend = h("legend", { class: "sr-only" }, box.dataset.legend || "");
    box.dataset.legend = legend.textContent;
    box.replaceChildren(legend, ...nodes);
  }
  function renderPlay() {
    const loaded = state.recipes && state.models;
    if (!loaded) {
      fillFieldset($("pick-recipes"), skeletonBlocks(2));
      return;
    }
    renderRecipePicks();
    renderModelPicks();
    showStep(state.wizard.step, false);
  }
  function renderRecipePicks() {
    const box = $("pick-recipes");
    const cards = state.recipes.map((recipe, i) => {
      const [title, subtitle] = splitName(recipe.name);
      const input = h("input", { type: "radio", name: "wiz-recipe", id: `wr-${i}`, value: recipe.id, class: "pick-input" });
      input.checked = recipe.id === state.wizard.recipe;
      input.addEventListener("change", () => { if (input.checked) chooseRecipe(recipe.id); });
      return h("div", { class: "pick-card enter" + (input.checked ? " selected" : ""), style: { "--i": i }, "data-value": recipe.id },
        input,
        h("label", { class: "pick-main", for: `wr-${i}` },
          h("span", { class: "pick-title" }, title, subtitle ? h("small", null, subtitle) : null),
          h("span", { class: "pick-meta" }, recipeTags(recipe)),
          h("span", { class: "pick-desc" }, excerpt(recipe.description, 150)),
          recipeStats(recipe)),
        h("span", { class: "pick-check", "aria-hidden": "true" }, icon("check")));
    });
    fillFieldset(box, (cards.length ? cards : [emptyNote("flask", "No society recipes", "Design one in the Society Lab first.")]));
  }
  function renderModelPicks() {
    const box = $("pick-models");
    const valid = state.models.filter((m) => m.config);
    const invalid = state.models.filter((m) => m.error);
    const cards = valid.map((m, i) => {
      const input = h("input", { type: "radio", name: "wiz-model", id: `wm-${i}`, value: m.id, class: "pick-input" });
      input.checked = m.id === state.wizard.model;
      input.addEventListener("change", () => { if (input.checked) chooseModel(m.id); });
      const health = healthBox(m.id, true);
      return h("div", { class: "pick-card enter" + (input.checked ? " selected" : ""), style: { "--i": i }, "data-value": m.id },
        input,
        h("label", { class: "pick-main", for: `wm-${i}` },
          h("span", { class: "row", style: { flexWrap: "nowrap", gap: "12px" } },
            h("span", { class: "model-avatar", style: { "--av": hueColor(m.config.model) }, "aria-hidden": "true" }, initials(m.config.model)),
            h("span", { class: "pick-title" }, m.config.model, h("small", null, `${m.config.snapshot} · ${m.id}`))),
          h("span", { class: "pick-meta" }, modelTags(m.config))),
        h("span", { class: "pick-check", "aria-hidden": "true" }, icon("check")),
        h("div", { class: "pick-foot" }, health));
    });
    for (const m of invalid) {
      cards.push(h("div", { class: "pick-card disabled" },
        h("div", { class: "pick-main" }, h("span", { class: "pick-title" }, m.id, h("small", null, m.error)),
          h("span", { class: "pick-desc" }, "This file cannot be used until it is fixed or replaced in Models."))));
    }
    if (!valid.length) {
      cards.unshift(emptyNote("chip", "No model yet", "Add the model that will govern the village: an LM Studio or OpenAI-compatible endpoint.",
        actionButton("plus", "Add a model", "primary", () => openModelDialog("", {}))));
    }
    fillFieldset(box, cards);
  }
  function chooseRecipe(id) {
    state.wizard.recipe = id;
    markSelected($("pick-recipes"), id);
    clampParallel();
    updateWizardChrome();
  }
  function chooseModel(id) {
    state.wizard.model = id;
    markSelected($("pick-models"), id);
    updateWizardChrome();
  }
  function markSelected(box, value) {
    for (const card of box.querySelectorAll(".pick-card")) card.classList.toggle("selected", card.dataset.value === value);
  }
  function showStep(step, focus) {
    const wiz = state.wizard;
    const back = step < wiz.step;
    wiz.step = step;
    for (const panel of document.querySelectorAll("#wizard .step")) {
      const on = Number(panel.dataset.step) === step;
      panel.classList.toggle("back", back);
      panel.hidden = !on;
    }
    if (step === 3) renderReady();
    updateWizardChrome();
    if (focus) {
      const title = document.querySelector(`#step-${step} .step-title`);
      if (title) { title.setAttribute("tabindex", "-1"); title.focus({ preventScroll: true }); }
      const top = $("stepper").getBoundingClientRect().top + window.scrollY - 90;
      if (window.scrollY > top) window.scrollTo({ top, behavior: reduced() ? "auto" : "smooth" });
    }
  }
  function updateWizardChrome() {
    const wiz = state.wizard;
    const recipe = recipeById(wiz.recipe), model = modelById(wiz.model);
    const done = { 1: Boolean(recipe), 2: Boolean(model), 3: false };
    for (const button of $("stepper").querySelectorAll("button")) {
      const n = Number(button.dataset.step);
      const li = button.parentElement;
      li.classList.toggle("done", done[n] && n !== wiz.step);
      if (n === wiz.step) button.setAttribute("aria-current", "step"); else button.removeAttribute("aria-current");
      button.disabled = n === 2 ? !recipe : n === 3 ? !(recipe && model) : false;
    }
    $("wiz-back").disabled = wiz.step === 1;
    const next = $("wiz-next");
    next.hidden = wiz.step === 3;
    next.disabled = wiz.step === 1 ? !recipe : wiz.step === 2 ? !model : true;
    const parts = [];
    parts.push(recipe ? `Village: ${splitName(recipe.name)[0]}` : "Choose a village");
    if (wiz.step >= 2) parts.push(model ? `Steward: ${model.config.model}` : "choose a model");
    $("wiz-status").textContent = parts.join(" · ");
    if (wiz.step === 3) updateStartAvailability();
  }
  function clampParallel() {
    const recipe = recipeById(state.wizard.recipe);
    const max = Math.max(1, Math.min(8, recipe ? recipe.seeds.length : 1));
    state.wizard.parallel = Math.max(1, Math.min(max, Math.round(state.wizard.parallel) || 1));
    return max;
  }
  function renderReady() {
    const wiz = state.wizard;
    const recipe = recipeById(wiz.recipe), model = modelById(wiz.model);
    const box = $("ready-summary");
    if (!recipe || !model) { box.replaceChildren(h("p", { class: "muted" }, "Choose a village and a model first.")); return; }
    const [title, subtitle] = splitName(recipe.name);
    box.replaceChildren(
      h("h3", null, icon("report"), "Summary"),
      h("div", { class: "ready-line" }, h("span", { class: "qs-icon green" }, icon("home")),
        h("div", null, h("b", null, title), h("span", null, `${subtitle ? subtitle + " · " : ""}${recipe.residents} residents · ${recipe.ticks} weeks · ${recipe.seeds.length} seed${recipe.seeds.length === 1 ? "" : "s"}`))),
      h("div", { class: "ready-line" }, h("span", { class: "model-avatar", style: { "--av": hueColor(model.config.model) }, "aria-hidden": "true" }, initials(model.config.model)),
        h("div", null, h("b", null, model.config.model), h("span", null, `${model.config.snapshot} · ${backendLabel(model.config)}`))),
      healthBox(model.id, true));
    const max = clampParallel();
    const input = $("par-value");
    input.max = String(max);
    input.value = String(wiz.parallel);
    input.disabled = max === 1;
    $("par-minus").disabled = max === 1 || wiz.parallel <= 1;
    $("par-plus").disabled = max === 1 || wiz.parallel >= max;
    $("par-hint").textContent = max === 1
      ? "This recipe plays a single seed, so the run is sequential."
      : `Up to ${max} seeds side by side on one loaded model (never loaded twice). LM Studio 0.4+ serves up to its "Max Concurrent Predictions" (default 4) at once; the context is shared between them.`;
    $("sec-value").value = String(wiz.seconds);
    const alert = $("start-alert");
    if (alert.dataset.auto !== "1") alert.hidden = true;
    queueEstimate(0);
    updateStartAvailability();
  }
  function setParallel(value) {
    state.wizard.parallel = Number(value);
    const max = clampParallel();
    $("par-value").value = String(state.wizard.parallel);
    $("par-minus").disabled = max === 1 || state.wizard.parallel <= 1;
    $("par-plus").disabled = max === 1 || state.wizard.parallel >= max;
    queueEstimate(150);
  }
  function queueEstimate(delay) {
    clearTimeout(estimateTimer);
    estimateTimer = setTimeout(updateEstimate, delay);
  }
  async function updateEstimate() {
    const box = $("estimate");
    const recipe = recipeById(state.wizard.recipe);
    const input = $("sec-value");
    const seconds = Number(input.value);
    if (!recipe) { box.replaceChildren(); return; }
    if (!(seconds > 0 && seconds <= 3600)) {
      input.classList.add("invalid");
      box.replaceChildren(icon("alert"), h("div", null, h("b", null, "Enter 1 to 3600 seconds"), h("small", null, "The typical time your model needs for one answer.")));
      return;
    }
    input.classList.remove("invalid");
    state.wizard.seconds = seconds;
    const token = ++estimateToken;
    box.setAttribute("aria-busy", "true");
    try {
      const data = await api(`/polis/estimate?recipe=${encodeURIComponent(recipe.id)}&seconds=${encodeURIComponent(seconds)}`);
      if (token !== estimateToken) return;
      const seeds = recipe.seeds.length, par = state.wizard.parallel;
      const parallelMinutes = data.minutes * Math.ceil(seeds / par) / seeds;
      box.replaceChildren(icon("clock"), h("div", null,
        h("b", null, `≈ ${minutesText(par > 1 ? parallelMinutes : data.minutes)}`),
        h("small", null, `${intFormat.format(data.decisions)} model calls at ${seconds} s each, with a safety margin${par > 1 ? `; ≈ ${minutesText(data.minutes)} one seed at a time` : ""}.`)));
    } catch (error) {
      if (token !== estimateToken) return;
      box.replaceChildren(icon("alert"), h("div", null, h("b", null, "No estimate"), h("small", null, error.message)));
    } finally {
      if (token === estimateToken) box.removeAttribute("aria-busy");
    }
  }
  function updateStartAvailability() {
    if (state.route !== "play" || state.wizard.step !== 3) return;
    const button = $("start-run");
    const alert = $("start-alert");
    const live = (state.runs || []).find(isRunning);
    if (live) {
      if (alert.dataset.run !== live.id) showBusyAlert(live, "Only one society run plays at a time. Pause it before starting another.", true);
      button.disabled = true;
      return;
    }
    if (alert.dataset.kind === "busy" && alert.dataset.auto === "1") { alert.hidden = true; alert.dataset.kind = ""; alert.dataset.run = ""; }
    button.disabled = state.starting || !(state.wizard.recipe && state.wizard.model);
  }
  function showBusyAlert(run, message, auto) {
    const alert = $("start-alert");
    alert.className = "alert warn";
    alert.dataset.kind = "busy";
    alert.dataset.auto = auto ? "1" : "";
    alert.dataset.run = run ? run.id : "";
    const actions = h("div", { class: "row" });
    if (run) {
      actions.append(linkButton("eye", `Watch ${run.model}`, reportHref(run.id), "small"),
        actionButton("pause", "Pause it", "small", (b) => pauseRun(run, b), "fill"));
    }
    alert.replaceChildren(h("b", null, auto ? "Another run is in progress. " : "Cannot start yet. "), message, actions);
    alert.hidden = false;
  }
  function showStartError(message) {
    const alert = $("start-alert");
    alert.className = "alert";
    alert.dataset.kind = "error";
    alert.dataset.auto = "";
    alert.dataset.run = "";
    alert.replaceChildren(h("b", null, "The run could not start. "), message);
    alert.hidden = false;
  }
  async function startRun() {
    const wiz = state.wizard;
    const recipe = recipeById(wiz.recipe), model = modelById(wiz.model);
    if (!recipe || !model) { showStartError("Choose a village and a model first."); return; }
    const button = $("start-run");
    state.starting = true;
    setBusy(button, true, "Starting…");
    $("start-alert").hidden = true;
    try {
      const run = await post("/polis/runs", { model_id: model.id, recipe_id: recipe.id, parallelism: Math.round(wiz.parallel) });
      toast(`The steward takes office. Opening the live report…`, "good");
      location.href = reportHref(run.id);
    } catch (error) {
      if (error.status === 409) {
        try { setRuns(await api("/polis/runs"), false); renderLiveIndicators(); schedulePoll(); } catch (ignored) { /* keep the message */ }
        showBusyAlert((state.runs || []).find(isRunning) || null, error.message, false);
      } else showStartError(error.message);
      $("start-alert").scrollIntoView({ behavior: reduced() ? "auto" : "smooth", block: "center" });
    } finally {
      state.starting = false;
      setBusy(button, false);
      updateStartAvailability();
    }
  }

  // ------------------------------------------------------------------ LEADERBOARD
  function enterBoard(recipeParam) {
    if (!state.recipes) { renderBoardRecipes(); $("board-body").replaceChildren(boardSkeleton()); return; }
    if (recipeParam && recipeById(recipeParam)) state.boardRecipe = recipeParam;
    if (!state.boardRecipe || !recipeById(state.boardRecipe)) state.boardRecipe = defaultRecipeId();
    renderBoard();
  }
  function renderBoardRecipes() {
    const box = $("board-recipes");
    if (!state.recipes) { box.replaceChildren(h("span", { class: "muted small" }, "Loading…")); return; }
    box.closest("fieldset").hidden = state.recipes.length < 2;
    box.replaceChildren(...state.recipes.map((r) => segOption("board-recipe", r.id, splitName(r.name)[0], r.id === state.boardRecipe, (id) => {
      state.boardRecipe = id;
      history.replaceState(null, "", `#/leaderboard?recipe=${encodeURIComponent(id)}`);
      renderBoard();
    })));
  }
  function boardSkeleton() {
    return h("div", { "aria-hidden": "true" }, ...skeletonBlocks(3));
  }
  async function renderBoard() {
    renderBoardRecipes();
    const id = state.boardRecipe;
    const body = $("board-body");
    if (!id) { body.replaceChildren(emptyNote("flask", "No society recipe", "Design one in the Society Lab first.")); body.removeAttribute("aria-busy"); return; }
    const cached = state.boards.get(id);
    if (cached) { drawBoard(cached, id); return; }
    body.setAttribute("aria-busy", "true");
    body.replaceChildren(boardSkeleton());
    try {
      const board = await api(`/polis/leaderboard?recipe=${encodeURIComponent(id)}`);
      state.boards.set(id, board);
      if (id !== state.boardRecipe || state.route !== "leaderboard") return;
      drawBoard(board, id);
    } catch (error) {
      if (id !== state.boardRecipe) return;
      body.removeAttribute("aria-busy");
      body.replaceChildren(emptyNote("alert", "The leaderboard could not be loaded", error.message,
        actionButton("refresh", "Try again", "", () => renderBoard())));
    }
  }
  /** Model rows keep the server's rank; reference stewards are interleaved by score, unranked. */
  function boardEntries(board) {
    const entries = (board.rows || []).map((row) => ({ kind: "model", row, total: row.total, order: row.rank }));
    const refs = board.references || {};
    const expert = refs.expert && refs.expert.total > 0 ? refs.expert.total : null;
    REFS.forEach((key, i) => {
      const ref = refs[key];
      if (!ref) return;
      const seeds = Object.keys(ref.seeds || {}).length || null;
      entries.push({ kind: "reference", key, total: ref.total, order: 1e6 + i,
        row: { model: capitalize(key), total: ref.total, collapsed: ref.collapsed, seeds, relative_to_expert: expert ? ref.total / expert : null } });
    });
    return entries.sort((a, b) => b.total - a.total || a.order - b.order);
  }
  function drawBoard(board, id) {
    const body = $("board-body");
    body.removeAttribute("aria-busy");
    const recipe = recipeById(id);
    const entries = boardEntries(board);
    const parts = [];
    if (!(board.rows || []).length) {
      parts.push(h("div", { class: "board-empty" }, icon("board"),
        h("p", null, h("b", null, "No completed run on this recipe yet. "), entries.length ? "Only the reference stewards are listed." : ""),
        linkButton("play", "Start a run", `#/play?recipe=${encodeURIComponent(id)}`, "primary small", "fill")));
    }
    if (entries.length) {
      const head = (text, cls) => h("th", { scope: "col", role: "columnheader", class: cls }, text);
      const table = h("table", { class: "board-table", role: "table" },
        h("caption", { class: "sr-only" }, `Leaderboard of ${recipe ? recipe.name : id}: models ranked by score; scripted reference stewards are listed for comparison and not ranked.`),
        h("thead", { role: "rowgroup" }, h("tr", { role: "row" },
          head("#", "c-rank"), head("Model", "c-model"), head("Score", "c-num c-score"), head("× expert", "c-num c-x"),
          head("Collapsed", "c-num c-col"), head(h("abbr", { title: "Wellbeing-years above a neutral point of 2" }, "WELLBY"), "c-num c-wellby"),
          head("Date", "c-date"), head(h("span", { class: "sr-only" }, "Report"), "c-link"))),
        h("tbody", { role: "rowgroup" }, entries.map(boardRow)));
      parts.push(h("div", { class: "card flush board-card enter" }, table));
    }
    const revision = board.revision ? ` Recipe revision ${String(board.revision).slice(0, 10)}.` : "";
    parts.push(h("p", { class: "board-note" },
      "Reference rows are scripted stewards played on the same seeds: milestones for comparison, not caps, and not ranked. "
      + `"× expert" divides a score by the expert reference. Each model appears with its latest completed run.${revision}`));
    body.replaceChildren(...parts);
  }
  const REF_NOTES = {
    passive: "Scripted: takes no action",
    naive: "Scripted: answers explicit requests",
    expert: "Scripted: sound heuristics on the briefing",
    oracle: "Scripted: the expert with hidden-state access",
  };
  const shortDate = (iso) => (iso ? new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : "—");
  function boardRow(entry) {
    const r = entry.row;
    const ref = entry.kind === "reference";
    const cell = (cls, label, ...content) => h("td", { role: "cell", class: cls, "data-label": label }, ...content);
    const model = ref
      ? cell("c-model", null, h("span", { class: "b-name" }, r.model), " ", tag("reference", "ref"), h("small", null, REF_NOTES[entry.key]))
      : cell("c-model", null, h("a", { class: "b-name", href: reportHref(r.id) }, r.model),
        h("small", null, [r.snapshot, r.model_id].filter(Boolean).join(" · ")),
        h("small", { class: "b-date-inline" }, shortDate(r.created_at)));
    const collapsed = r.seeds ? `${r.collapsed}/${r.seeds}` : isNum(r.collapsed) ? String(r.collapsed) : "—";
    return h("tr", { role: "row", class: ref ? "is-ref" : "" },
      cell("c-rank", null, ref ? h("span", { title: "Reference: not ranked" }, "–") : String(r.rank)),
      model,
      cell("c-num c-score", "Score", pts(r.total)),
      cell("c-num c-x", "× expert", isNum(r.relative_to_expert) ? `${r.relative_to_expert.toFixed(2)}×` : "—"),
      cell("c-num c-col" + (r.collapsed ? " bad" : ""), "Collapsed", collapsed),
      cell("c-num c-wellby", "WELLBY", ref ? "—" : pts(r.wellby)),
      cell("c-date", "Date", ref ? "—" : h("time", { datetime: r.created_at, title: dateTime(r.created_at) }, shortDate(r.created_at))),
      cell("c-link", null, ref ? null : h("a", { class: "button small", href: reportHref(r.id), "aria-label": `Open the report of ${r.model}` }, icon("report"), h("span", null, "Report"))));
  }

  // ------------------------------------------------------------------ RUNS
  function renderRuns() {
    renderRunFilters();
    const list = $("run-list");
    if (!state.runs) {
      if (!list.children.length) list.replaceChildren(...skeletonBlocks(3));
      return;
    }
    list.removeAttribute("aria-busy");
    const runs = filteredRuns();
    if (!runs.length) {
      caches.runCards.clear();
      const filtered = state.runs.length > 0;
      list.replaceChildren(filtered
        ? emptyNote("runs", "No run matches these filters", "Choose another recipe or status.")
        : emptyNote("runs", "No run yet", "Start your first run: pick a village and a model, and watch it live.",
          linkButton("play", "Start a run", "#/play", "primary", "fill")));
      return;
    }
    reconcile(list, runs, caches.runCards, createRunCard, updateRunCard);
  }
  function filteredRuns() {
    const f = state.runsFilter;
    return state.runs.filter((r) => (!f.recipe || r.recipe_id === f.recipe) && statusMatches(r.status, f.status));
  }
  function statusMatches(status, filter) {
    if (filter === "all") return true;
    if (filter === "paused") return status === "paused" || status === "interrupted";
    return status === filter;
  }
  function renderRunFilters() {
    const select = $("runs-recipe");
    const runs = state.runs || [];
    const options = new Map((state.recipes || []).map((r) => [r.id, splitName(r.name)[0]]));
    for (const r of runs) if (!options.has(r.recipe_id)) options.set(r.recipe_id, splitName(r.recipe_name)[0] || r.recipe_id);
    const sig = [...options].join("|");
    if (select.dataset.sig !== sig) {
      select.dataset.sig = sig;
      select.replaceChildren(new Option("All recipes", ""), ...[...options].map(([id, name]) => new Option(name, id)));
      if (!options.has(state.runsFilter.recipe)) state.runsFilter.recipe = "";
      select.value = state.runsFilter.recipe;
      select.closest("label").hidden = options.size < 2;
    }
    const scoped = runs.filter((r) => !state.runsFilter.recipe || r.recipe_id === state.runsFilter.recipe);
    const groups = [["all", "All"], ["running", "Live"], ["paused", "Paused"], ["completed", "Completed"], ["failed", "Failed"]];
    const box = $("runs-status");
    const counts = groups.map(([key]) => scoped.filter((r) => statusMatches(r.status, key)).length);
    const sig2 = counts.join(",") + state.runsFilter.status;
    if (box.dataset.sig === sig2) return;
    box.dataset.sig = sig2;
    box.replaceChildren(...groups.map(([key, label], i) => segOption("runs-status", key, label, state.runsFilter.status === key, (value) => {
      state.runsFilter.status = value;
      renderRuns();
    }, h("em", null, String(counts[i])))));
  }
  function createRunCard(run) {
    const ring = progressRing(true);
    const parts = {
      ring, status: h("span"), when: h("span", { class: "rc-when" }), model: h("div", { class: "rc-model" }), recipe: h("div", { class: "rc-recipe" }),
      score: h("div", { class: "rc-score" }), seeds: h("div", { class: "rc-seeds" }), usage: h("div", { class: "rc-usage" }),
      error: h("div", { class: "rc-error", role: "note" }), actions: h("div", { class: "rc-actions" }),
    };
    const card = h("article", { class: "card run-card enter", "data-run-card": run.id },
      h("div", { class: "rc-top" }, parts.status, parts.when),
      h("div", { class: "rc-body" }, ring, h("div", null, parts.model, parts.recipe, parts.score)),
      parts.seeds, parts.usage, parts.error, parts.actions);
    card.parts = parts;
    return card;
  }
  function updateRunCard(card, run) {
    const p = card.parts;
    const running = isRunning(run);
    const collapsedAll = run.status === "completed" && fullyCollapsed(run.score);
    card.classList.toggle("is-running", running);
    if (card.dataset.status !== run.status) {
      p.status.replaceChildren(running ? h("span", { class: "row", style: { gap: "6px" } }, liveTag(), statusChip(run.status)) : statusChip(run.status));
      p.actions.replaceChildren(...runActions(run));
      card.dataset.status = run.status;
    }
    p.when.textContent = relTime(run.created_at);
    p.when.title = dateTime(run.created_at);
    p.model.replaceChildren(document.createTextNode(run.model), h("small", null, `${run.snapshot || "—"} · ${run.model_id}`));
    p.recipe.textContent = run.recipe_name;
    const week = runWeek(run);
    const fraction = run.progress && run.progress.total ? run.progress.weeks / run.progress.total : 0;
    if (collapsedAll) {
      const cw = collapseWeek(run);
      p.ring.set(fraction, cw ? `W${cw}` : "✕", "collapse", "collapsed", `Collapsed${cw ? ` in week ${cw}` : ""}`);
    } else {
      p.ring.set(run.status === "completed" ? 1 : fraction, String(week), `of ${run.weeks}`, run.status, `Week ${week} of ${run.weeks}`);
    }
    p.score.replaceChildren(...runScore(run, collapsedAll));
    const seeds = seedsOf(run);
    p.seeds.hidden = seeds.length < 2;
    if (seeds.length > 1) {
      p.seeds.replaceChildren(...seeds.map((s) => h("span", { class: "seed-chip" + (s.collapsed ? " collapsed" : ""), "data-s": s.status, title: s.collapsed ? s.collapsed.label : STATUS_LABEL[s.status] || s.status },
        h("i", { "aria-hidden": "true" }), `Seed ${s.seed} · W${s.week}${s.status === "completed" && isNum(s.total) ? ` · ${pts(s.total)}` : ""}`)));
    }
    const u = run.usage || {};
    const usage = [];
    if (u.calls) usage.push(h("span", null, icon("chat"), `${intFormat.format(u.calls)} calls`));
    if (u.input_tokens || u.generated_tokens) usage.push(h("span", { title: "Prompt tokens / generated tokens" }, icon("bolt"), `${compact(u.input_tokens)} in · ${compact(u.generated_tokens)} out`));
    if (u.seconds) usage.push(h("span", null, icon("clock"), minutesText(u.seconds / 60)));
    if (run.parallelism > 1) usage.push(h("span", null, icon("dice"), `${run.parallelism} parallel seeds`));
    p.usage.replaceChildren(...usage);
    p.usage.hidden = !usage.length;
    p.error.hidden = !run.error;
    p.error.textContent = run.error || "";
  }
  function runScore(run, collapsedAll) {
    if (collapsedAll) {
      const cw = collapseWeek(run);
      const label = collapseLabel(run);
      const r = collapsedTag(cw ? `Collapsed · week ${cw}` : "Collapsed");
      if (label) r.title = label;
      return [h("strong", null, pts(run.score.total)), h("span", null, "points"), r];
    }
    if (run.status === "completed" && run.score) {
      const strong = h("strong");
      countUp(strong, run.score.total, `run-${run.id}`);
      const out = [strong, h("span", null, "points")];
      if (isNum(run.score.relative_to_expert)) out.push(h("span", { class: "rel-expert" }, `· ${times(run.score.relative_to_expert)}`));
      if (run.score.collapsed) out.push(tag(`${run.score.collapsed}/${run.score.seeds} collapsed`, "bad", "alert"));
      return out;
    }
    if (run.status === "failed") return [h("span", null, `Stopped at week ${runWeek(run)}. Resume replays the log and continues.`)];
    const value = liveScore(run);
    if (value === null) return [h("span", null, run.status === "running" ? "Week one in progress…" : "No week played yet")];
    const strong = h("strong");
    countUp(strong, value, `run-${run.id}`);
    return [strong, h("span", null, run.status === "running" ? "points so far" : "points so far · paused")];
  }
  function runActions(run) {
    const running = isRunning(run);
    const list = [linkButton(running ? "eye" : "report", running ? "Watch live" : "Open report", reportHref(run.id), "primary small")];
    if (running) list.push(actionButton("pause", "Pause", "small", (b) => pauseRun(run, b), "fill"));
    if (["paused", "interrupted", "failed"].includes(run.status)) list.push(actionButton("play", "Resume", "small", (b) => resumeRun(run, b), "fill"));
    const remove = actionButton("trash", "Delete", "small danger", (b) => deleteRun(run, b));
    remove.setAttribute("aria-label", `Delete the run of ${run.model}`);
    if (running) { remove.disabled = true; remove.title = "Pause the run before deleting it."; }
    list.push(remove);
    return list;
  }

  // ------------------------------------------------------------------ MODELS
  function renderModels() {
    const list = $("model-list");
    if (!state.models) {
      if (!list.children.length) list.replaceChildren(...skeletonBlocks(3));
      return;
    }
    list.removeAttribute("aria-busy");
    const sig = JSON.stringify(state.models) + "#" + (state.runs || []).filter((r) => r.status === "completed").length;
    if (list.dataset.sig === sig) return;
    const first = !list.dataset.sig;
    list.dataset.sig = sig;
    const cards = state.models.map((m, i) => (m.config ? modelCard(m, i, first) : invalidModelCard(m, i, first)));
    const add = h("button", { type: "button", class: "card model-card add" }, icon("plus"), h("span", null, "Add a model"),
      h("small", { class: "muted" }, "LM Studio or any OpenAI-compatible endpoint"));
    add.addEventListener("click", () => openModelDialog("", {}));
    list.replaceChildren(...cards, add);
  }
  function backendLabel(config) {
    return config.context_tokens ? `LM Studio · ${intFormat.format(config.context_tokens)} tokens` :
      isNum(config.context_bytes_verified) ? `OpenAI-compatible · ${intFormat.format(config.context_bytes_verified)} bytes` : "OpenAI-compatible";
  }
  function modelTags(config) {
    const list = [tag(config.context_tokens ? "LM Studio" : "OpenAI-compatible", "info", "server")];
    if (config.structured_output) list.push(tag(config.structured_output.replace(/_/g, " ")));
    if (config.system_prompt) list.push(tag(config.personality ? `Persona · ${config.personality}` : "Persona", "branch", "person"));
    if (config.api_key_file || config.api_key_env) list.push(tag("API key", "gold", "key"));
    if (config.managed_instance) list.push(tag("Managed instance"));
    return list;
  }
  function endpointHost(endpoint) {
    try { const url = new URL(endpoint); return url.host + (url.pathname !== "/" ? url.pathname : ""); } catch (error) { return String(endpoint || "—"); }
  }
  function modelCard(m, index, animate) {
    const c = m.config;
    const stats = modelStats(m.id);
    const fact = (iconName, label, value) => h("div", null, icon(iconName), h("span", null, label, " ", h("b", null, value)));
    const edit = actionButton("edit", "Edit", "", () => openModelDialog(m.id, c));
    edit.setAttribute("aria-label", `Edit ${m.id}`);
    return h("article", { class: "card model-card" + (animate ? " enter" : ""), style: { "--i": index } },
      h("div", { class: "mc-head" },
        h("span", { class: "model-avatar", style: { "--av": hueColor(c.model) }, "aria-hidden": "true" }, initials(c.model)),
        h("div", { class: "mc-name" }, h("b", null, c.model), h("span", null, `${m.id}.json`))),
      h("div", { class: "mc-tags" }, modelTags(c)),
      h("div", { class: "mc-facts" },
        fact("tag", "Snapshot", c.snapshot),
        fact("server", "Endpoint", endpointHost(c.endpoint)),
        fact("chip", "Context", backendLabel(c).split(" · ")[1] || "provider settings"),
        fact("board", stats.count ? `${stats.count} run${stats.count === 1 ? "" : "s"} · best` : "Runs", stats.best ? `${pts(stats.best.score.total)} on ${splitName(stats.best.recipe_name)[0]}` : stats.count ? "no completed run" : "none yet")),
      healthBox(m.id, false),
      h("div", { class: "mc-actions" }, edit, linkButton("play", "Play", `#/play?model=${encodeURIComponent(m.id)}`, "primary", "fill")));
  }
  function invalidModelCard(m, index, animate) {
    const replace = actionButton("edit", "Replace", "", () => openModelDialog(m.id, {}, "replace"));
    return h("article", { class: "card model-card invalid" + (animate ? " enter" : ""), style: { "--i": index } },
      h("div", { class: "mc-head" },
        h("span", { class: "model-avatar", style: { "--av": "#ef6a5b" }, "aria-hidden": "true" }, "!"),
        h("div", { class: "mc-name" }, h("b", null, m.id), h("span", null, `${m.id}.json`))),
      h("p", { class: "health-detail bad" }, m.error),
      h("p", { class: "hint" }, "This file cannot be read as a model configuration. Fix the JSON in the models folder, or replace it here with a new configuration."),
      h("div", { class: "mc-actions" }, replace));
  }

  /** Health status and "Test" button for a saved model; every box for the same id is repainted together. */
  function healthBox(id, compactBox) {
    const box = h("div", { class: compactBox ? "health-box" : "mc-health", "data-health-id": id, style: compactBox ? { display: "flex", flexDirection: "column", gap: "6px", width: "100%" } : null });
    paintHealth(box, id);
    return box;
  }
  function paintHealth(box, id) {
    const entry = state.health.get(id) || { state: "unknown" };
    const result = entry.result || {};
    let chipText = "Not tested", detail = "A short request with the saved settings checks the connection before a long run.";
    if (entry.state === "checking") { chipText = "Testing…"; detail = "One short request (128 output tokens at most, 30-second timeout)."; }
    else if (entry.state === "ok") { chipText = `Ready${isNum(result.latency_ms) ? ` · ${intFormat.format(result.latency_ms)} ms` : ""}`; detail = `${result.protocol || ""}${result.checked_at ? ` · checked ${relTime(result.checked_at)}` : ""}. ${result.hint || ""}`; }
    else if (entry.state === "error") { chipText = capitalize(humanize(result.code || "error").replace(/\.$/, "")); detail = `${result.message || ""} ${result.hint || ""}`.trim(); }
    const chip = h("span", { class: "health", "data-state": entry.state }, h("i", { "aria-hidden": "true" }), h("span", null, chipText));
    const button = h("button", { type: "button", class: "small" }, icon("pulse"), h("span", null, entry.state === "unknown" ? "Test" : "Test again"));
    button.disabled = entry.state === "checking";
    button.setAttribute("aria-label", `Test the connection of ${id}`);
    button.addEventListener("click", (event) => { event.stopPropagation(); testModel(id); });
    box.setAttribute("aria-busy", entry.state === "checking" ? "true" : "false");
    box.replaceChildren(h("div", { class: "row between", style: { flexWrap: "nowrap", width: "100%" } }, chip, button),
      h("p", { class: "health-detail" + (entry.state === "error" ? " bad" : ""), role: "status" }, detail));
  }
  function paintAllHealth(id) {
    for (const box of document.querySelectorAll("[data-health-id]")) if (box.dataset.healthId === id) paintHealth(box, id);
  }
  function testModel(id) {
    if (state.healthPending.has(id)) return state.healthPending.get(id);
    state.health.set(id, { state: "checking" });
    paintAllHealth(id);
    const signal = typeof AbortSignal !== "undefined" && AbortSignal.timeout ? AbortSignal.timeout(35000) : undefined;
    const run = api(`/models/${encodeURIComponent(id)}/test`, { method: "POST", signal })
      .then((result) => {
        state.health.set(id, { state: result.ok ? "ok" : "error", result });
        toast(result.ok ? `${id}: inference available.` : `${id}: ${result.message}`, result.ok ? "good" : "bad");
      })
      .catch((error) => {
        state.health.set(id, { state: "error", result: { ok: false, code: "unavailable", message: error.message, hint: "Check the application server connection and retry." } });
      })
      .finally(() => {
        state.healthPending.delete(id);
        saveHealth();
        paintAllHealth(id);
      });
    state.healthPending.set(id, run);
    return run;
  }

  // ------------------------------------------------------------------ MODEL DIALOG (every field of a candidate model file)
  async function ensurePersonalities() {
    if (state.personalities) return state.personalities;
    try {
      state.personalities = await api("/personalities");
    } catch (error) {
      state.personalities = {};
    }
    const select = $("model-persona");
    select.replaceChildren(new Option("None", ""), ...Object.entries(state.personalities).map(([id, p]) => new Option(p.name || id, id)));
    return state.personalities;
  }
  /** mode: "edit" (default), "replace" (unreadable file) or "import" (fields from a JSON file, not saved yet). */
  async function openModelDialog(id, config, mode) {
    const replace = mode === "replace";
    dialogOpener = document.activeElement;
    await ensurePersonalities();
    fillModel(id, config || {});
    $("model-dialog-title").textContent = !id ? "Add a model" : replace ? `Replace ${id}` : mode === "import" ? "Review the imported model" : `Edit ${id}`;
    $("model-advanced").open = false;
    const dialog = $("model-dialog");
    if (!dialog.open) dialog.showModal();
    dialog.querySelector(".sheet-body").scrollTop = 0;
    (id && !mode ? $("model-name") : $("model-id")).focus();
  }
  function fillModel(id, config = {}) {
    const personalities = state.personalities || {};
    modelOriginal = structuredClone(config);
    $("model-id").value = id;
    $("model-name").value = config.model || "";
    $("model-snapshot").value = config.snapshot || "";
    let url;
    try { url = new URL(config.endpoint || "http://localhost:1234"); } catch (error) { url = new URL("http://localhost:1234"); }
    $("model-port").value = url.port;
    url.port = "";
    $("model-address").value = url.href.replace(/\/$/, "");
    $("model-backend").value = config.context_bytes_verified !== undefined && !config.context_tokens ? "compatible" : "lmstudio";
    $("model-structured").value = config.structured_output || "compact_decision";
    $("model-constrain").checked = config.constrain_output ?? Boolean(config.model);
    $("model-managed").checked = config.managed_instance ?? !config.model;
    $("model-reasoning").checked = Boolean(config.reasoning_delimiters);
    $("model-timeout").value = config.request_timeout_seconds ?? 28800;
    $("model-effort").value = config.reasoning_effort || "";
    $("model-context").value = config.context_tokens ?? config.context_bytes_verified ?? 50000;
    updateBackend();
    $("model-track").value = config.budget_track || "opaque_compute";
    for (const [field, key] of [...NUMERIC_FIELDS, ...KEY_FIELDS]) $("model-" + field).value = config[key] ?? (key === "temperature" ? 0 : "");
    $("model-persona").value = config.persona && personalities[config.persona] ? config.persona : "";
    $("model-personality").value = config.personality || "";
    $("model-prompt").value = config.system_prompt || "";
    $("model-key").value = "";
    $("model-remove-key").checked = false;
    $("model-error").textContent = "";
    updateReasoning();
    for (const el of $("model-form").querySelectorAll(".invalid")) el.classList.remove("invalid");
  }
  function updateBackend() {
    const native = $("model-backend").value === "lmstudio";
    $("model-context-text").textContent = native ? "Maximum context (tokens)" : "Verified observation capacity (bytes)";
    $("model-context").min = native ? "1" : "24000";
    $("model-effort").disabled = !native;
    $("model-managed").disabled = !native;
  }
  function updateReasoning() {
    // <think> delimiters are only saved when generation is not constrained to JSON.
    $("model-reasoning").disabled = $("model-constrain").checked;
  }
  function fieldError(id, message) {
    const el = $(id);
    el.classList.remove("invalid");
    void el.offsetWidth;
    el.classList.add("invalid");
    if (el.closest("details") && !el.closest("details").open) el.closest("details").open = true;
    el.focus();
    $("model-error").textContent = message;
    return false;
  }
  function validateModelForm() {
    for (const el of $("model-form").querySelectorAll(".invalid")) el.classList.remove("invalid");
    const id = $("model-id").value;
    if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$/.test(id)) return fieldError("model-id", "Configuration ID: use letters, digits, '-' or '_' (up to 80 characters), starting with a letter or digit.");
    if (!$("model-name").value.trim()) return fieldError("model-name", "Enter the model name the server expects.");
    let url = null;
    try { url = new URL($("model-address").value.trim()); } catch (error) { url = null; }
    if (!url || !/^https?:$/.test(url.protocol)) return fieldError("model-address", "Address: enter a full URL such as http://localhost.");
    const port = $("model-port").value;
    if (port !== "" && !(Number.isInteger(Number(port)) && Number(port) >= 1 && Number(port) <= 65535)) return fieldError("model-port", "Port: 1 to 65535, or blank for the protocol default.");
    const context = Number($("model-context").value), min = Number($("model-context").min);
    if ($("model-context").value === "" || !Number.isInteger(context) || context < min) {
      return fieldError("model-context", $("model-backend").value === "lmstudio" ? "Maximum context: a whole number of tokens." : "Verified observation capacity: at least 24,000 bytes.");
    }
    if (!$("model-snapshot").value.trim()) return fieldError("model-snapshot", "Enter the exact snapshot or version of the model.");
    const timeout = Number($("model-timeout").value);
    if (!(Number.isInteger(timeout) && timeout >= 1 && timeout <= 86400)) return fieldError("model-timeout", "Request deadline: 1 to 86,400 seconds.");
    for (const [field] of NUMERIC_FIELDS) {
      const el = $("model-" + field);
      if (el.value !== "" && !Number.isFinite(Number(el.value))) return fieldError("model-" + field, "Enter a number or leave the field blank.");
    }
    return true;
  }
  async function saveModel(event) {
    event.preventDefault();
    $("model-error").textContent = "";
    if (!validateModelForm()) return;
    const save = $("save-model");
    save.disabled = true;
    try {
      const personalities = state.personalities || {};
      const config = { ...modelOriginal };
      const url = new URL($("model-address").value.trim());
      if ($("model-port").value) url.port = $("model-port").value;
      delete config.context_tokens;
      delete config.context_bytes_verified;
      config[$("model-backend").value === "lmstudio" ? "context_tokens" : "context_bytes_verified"] = Number($("model-context").value);
      Object.assign(config, {
        model: $("model-name").value.trim(), snapshot: $("model-snapshot").value.trim(), endpoint: url.href.replace(/\/$/, ""),
        structured_output: $("model-structured").value, constrain_output: $("model-constrain").checked, budget_track: $("model-track").value,
      });
      for (const [field, key] of NUMERIC_FIELDS) {
        delete config[key];
        if ($("model-" + field).value !== "") config[key] = Number($("model-" + field).value);
      }
      config.request_timeout_seconds = Number($("model-timeout").value);
      delete config.reasoning_effort;
      if ($("model-backend").value === "lmstudio" && $("model-effort").value) config.reasoning_effort = $("model-effort").value;
      delete config.managed_instance;
      if ($("model-backend").value === "lmstudio") config.managed_instance = $("model-managed").checked;
      delete config.reasoning_delimiters;
      if (!config.constrain_output && $("model-reasoning").checked) config.reasoning_delimiters = ["<think>", "</think>"];
      for (const [field, key] of KEY_FIELDS) {
        delete config[key];
        if ($("model-" + field).value.trim()) config[key] = $("model-" + field).value.trim();
      }
      delete config.persona;
      delete config.personality;
      delete config.system_prompt;
      const prompt = $("model-prompt").value.trim();
      if (prompt) {
        config.system_prompt = prompt;
        if ($("model-personality").value.trim()) config.personality = $("model-personality").value.trim();
        const preset = personalities[$("model-persona").value];
        if (preset && preset.system_prompt === prompt) config.persona = $("model-persona").value;
      }
      const body = { config };
      if ($("model-remove-key").checked) body.api_key = "";
      else if ($("model-key").value) body.api_key = $("model-key").value;
      const id = $("model-id").value;
      await api("/models/" + encodeURIComponent(id), { method: "PUT", body });
      $("model-dialog").close();
      state.health.delete(id);
      saveHealth();
      await reloadModels();
      toast(`Model ${id} saved.`, "good");
      if (!document.activeElement || document.activeElement === document.body) {
        const edit = [...document.querySelectorAll("#model-list button")].find((b) => b.getAttribute("aria-label") === `Edit ${id}`);
        if (edit) edit.focus();
      }
      if (state.route === "play") {
        state.wizard.model = id;
        renderModelPicks();
        updateWizardChrome();
      }
    } catch (error) {
      $("model-error").textContent = error.message;
      $("model-error").scrollIntoView({ behavior: reduced() ? "auto" : "smooth", block: "nearest" });
    } finally {
      save.disabled = false;
    }
  }
  async function reloadModels() {
    state.models = await api("/models");
    if (state.route === "models") renderModels();
  }
  async function readModelFile(input) {
    const file = input.files && input.files[0];
    input.value = "";
    if (!file) return null;
    const value = JSON.parse(await file.text());
    if (!value || typeof value !== "object" || !value.model || !value.endpoint) throw new Error("Choose a candidate model JSON, not a run or recipe file.");
    return { id: file.name.replace(/\.json$/i, "").replace(/[^A-Za-z0-9_-]/g, "-"), config: value };
  }

  function confirmDialog({ title, text, ok }) {
    const dialog = $("confirm-dialog");
    $("confirm-title").textContent = title;
    $("confirm-text").textContent = text;
    $("confirm-ok").textContent = ok || "Delete";
    const opener = document.activeElement;
    dialog.returnValue = "";
    dialog.showModal();
    $("confirm-cancel").focus();
    return new Promise((resolve) => {
      dialog.addEventListener("close", () => {
        resolve(dialog.returnValue === "ok");
        if (opener && opener.isConnected && typeof opener.focus === "function") opener.focus();
      }, { once: true });
    });
  }

  // ------------------------------------------------------------------ ABOUT
  function renderAbout() {
    const list = $("about-domains");
    if (list.children.length) return;
    list.replaceChildren(...DOMAINS.map((d) => h("li", { style: { "--c": DOMAIN_COLORS[d] } }, domainIcon(d),
      h("span", null, h("b", null, `${d} · ${DOMAIN_INFO[d].name}`), DOMAIN_INFO[d].unit))));
  }

  // ------------------------------------------------------------------ global error
  function showGlobalError(message, withRetry = true) {
    const box = $("global-error");
    const parts = [h("b", null, "Something went wrong. "), message];
    if (withRetry) parts.push(h("div", { class: "row" }, actionButton("refresh", "Try again", "small", () => { box.hidden = true; boot(); })));
    box.replaceChildren(...parts);
    box.hidden = false;
  }
  function hideGlobalError() { $("global-error").hidden = true; }

  // ------------------------------------------------------------------ wiring
  function wireStatic() {
    window.addEventListener("hashchange", () => onRoute(false));
    window.addEventListener("resize", debounce(moveInk, 80));
    if (typeof ResizeObserver === "function") new ResizeObserver(() => moveInk()).observe($("tabbar"));
    document.addEventListener("visibilitychange", () => { if (!document.hidden && state.runs) pollOnce(); });
    $("skip-link").addEventListener("click", (event) => { event.preventDefault(); $("main").focus(); });
    $("hero-toggle").addEventListener("click", () => { if (village) village.toggle(); syncHeroToggle(); });

    // wizard
    for (const button of $("stepper").querySelectorAll("button")) button.addEventListener("click", () => showStep(Number(button.dataset.step), true));
    $("wiz-next").addEventListener("click", () => showStep(Math.min(3, state.wizard.step + 1), true));
    $("wiz-back").addEventListener("click", () => showStep(Math.max(1, state.wizard.step - 1), true));
    $("wiz-add-model").addEventListener("click", () => openModelDialog("", {}));
    $("par-minus").addEventListener("click", () => setParallel(state.wizard.parallel - 1));
    $("par-plus").addEventListener("click", () => setParallel(state.wizard.parallel + 1));
    $("par-value").addEventListener("change", (event) => setParallel(event.target.value));
    $("sec-value").addEventListener("input", () => queueEstimate(350));
    $("start-run").addEventListener("click", startRun);

    // runs filters
    $("runs-recipe").addEventListener("change", (event) => { state.runsFilter.recipe = event.target.value; renderRuns(); });

    // models
    $("models-add").addEventListener("click", () => openModelDialog("", {}));
    $("models-import").addEventListener("change", async (event) => {
      try {
        const file = await readModelFile(event.target);
        if (file) await openModelDialog(file.id, file.config, "import");
      } catch (error) {
        toast(error instanceof SyntaxError ? "That file is not valid JSON." : error.message, "bad");
      }
    });

    // model dialog
    $("model-form").addEventListener("submit", saveModel);
    $("model-close").addEventListener("click", () => $("model-dialog").close());
    $("model-cancel").addEventListener("click", () => $("model-dialog").close());
    $("model-dialog").addEventListener("close", () => {
      if (dialogOpener && dialogOpener.isConnected && typeof dialogOpener.focus === "function") dialogOpener.focus();
      dialogOpener = null;
    });
    $("model-backend").addEventListener("change", () => {
      if ($("model-backend").value === "compatible") $("model-context").value = Math.max(24000, Number($("model-context").value) || 24000);
      updateBackend();
    });
    $("model-constrain").addEventListener("change", updateReasoning);
    $("model-persona").addEventListener("change", () => {
      const preset = (state.personalities || {})[$("model-persona").value];
      if (preset) { $("model-personality").value = preset.name; $("model-prompt").value = preset.system_prompt; }
    });
    $("import-model").addEventListener("change", async (event) => {
      try {
        const file = await readModelFile(event.target);
        if (file) { fillModel(file.id, file.config); $("model-dialog-title").textContent = "Review the imported model"; }
      } catch (error) {
        $("model-error").textContent = error instanceof SyntaxError ? "That file is not valid JSON." : error.message;
      }
    });
  }
  function debounce(fn, ms) {
    let timer = 0;
    return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
  }

  async function boot() {
    hideGlobalError();
    onRoute(true);
    try {
      const [recipes, models, runs] = await Promise.all([api("/polis/recipes"), api("/models"), api("/polis/runs")]);
      state.recipes = recipes;
      state.models = models;
      setRuns(runs, true);
    } catch (error) {
      showGlobalError(error.message);
      return;
    }
    renderLiveIndicators();
    enterView(state.route, state.params);
    schedulePoll();
    ensurePersonalities();
  }

  wireStatic();
  boot();
})();
