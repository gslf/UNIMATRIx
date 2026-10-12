"use strict";
/*
 * UNIMATRIx Explorer · UI kit: DOM helpers, icons, formatting and the shared components (chips, domain
 * bars, line charts with a week crosshair, toasts, count-ups). Same identity as the platform's home and
 * Society Lab. Data is always inserted with textContent or setAttribute, never parsed as HTML.
 */
(function () {
  const NS = "http://www.w3.org/2000/svg";
  const DOMAINS = ["D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8"];
  const DOMAIN_INFO = {
    D1: { name: "Information", unit: "Informed resident-weeks plus forecast skill in bits", icon: "megaphone" },
    D2: { name: "Economy", unit: "Coins of community net worth created", icon: "coins" },
    D3: { name: "Public works", unit: "Service-weeks of completed projects", icon: "works" },
    D4: { name: "Commons", unit: "Fish harvested plus what the lake regrew", icon: "lake" },
    D5: { name: "Relationships", unit: "Trust-weeks, kept promises, settled disputes, relieved concerns", icon: "heart" },
    D6: { name: "Institutions", unit: "Residents × legitimacy × (1 − Gini), every week", icon: "columns" },
    D7: { name: "Transmission", unit: "Successor depth per skill each week, apprentice levels", icon: "book" },
    D8: { name: "Adaptation", unit: "Residents using changed conventions, integrated newcomers", icon: "cycle" },
  };
  // Same categorical order as the platform, so domains match the run report.
  const DOMAIN_COLORS = { D1: "#3987e5", D2: "#d95926", D3: "#199e70", D4: "#b59a00", D5: "#d55181", D6: "#008300", D7: "#9085e9", D8: "#e66767" };

  const ICONS = {
    board: ["M4 20h16", "M6 20v-7h3v7M10.5 20V5h3v15M15 20v-10h3v10"],
    runs: ["M4 6h16M4 12h9M4 18h6", "M15.5 14.5v6l5-3z"],
    chip: ["M7 7h10v10H7z", "M10 10h4v4h-4zM10 3v4M14 3v4M10 17v4M14 17v4M3 10h4M3 14h4M17 10h4M17 14h4"],
    play: ["M8 5.5v13l10.5-6.5z"],
    pause: ["M8 5h3v14H8zM13 5h3v14h-3z"],
    plus: ["M12 5v14M5 12h14"],
    report: ["M5 3h10l4 4v14H5z", "M14 3v5h5M8.5 17v-3M12 17v-6M15.5 17v-4"],
    pulse: ["M3 12h4l2-5 4 10 2-5h6"],
    upload: ["M12 16V4M7 9l5-5 5 5M4 20h16"],
    users: ["c 9 8 3", "M3.5 19a5.5 5.5 0 0 1 11 0", "c 17 9 2.5", "M15.5 14.4a4.5 4.5 0 0 1 5 4.6"],
    calendar: ["M4 6h16v14H4z", "M4 10h16M8 3v5M16 3v5"],
    dice: ["M5 5h14v14H5z", "c 9 9 1.1", "c 15 15 1.1", "c 15 9 1.1", "c 9 15 1.1"],
    bolt: ["M13 3 5 14h6l-1 7 8-11h-6z"],
    chat: ["M4 5h16v11H9l-5 4z", "M8 9h8M8 12h5"],
    clock: ["c 12 12 8.5", "M12 7.5V12l3 2"],
    ruins: ["M3 20h18", "M5 20v-8.5l4.5-3.5 1.8 2.2", "M13 8.4 15 7l4 3.3V20", "M9 20v-4.5h3V20", "M15.5 13.5h2v2h-2z", "M11.5 4.5l1 3-1.5 1.5 1 2.5"],
    star: ["M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z"],
    flag: ["M5 21V4", "M5 4h11l-2 4 2 4H5"],
    person: ["c 12 8 4", "M4.5 20.5a7.5 7.5 0 0 1 15 0"],
    eye: ["M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z", "c 12 12 3"],
    smile: ["c 12 12 8.5", "M8.5 14a4 4 0 0 0 7 0M9 9.5h.01M15 9.5h.01"],
    heart: ["M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z"],
    lake: ["M3 17c3-3 5-3 8 0s5 3 10 0", "M3 12.5c3-3 5-3 8 0s5 3 10 0", "M8 7.5c1.5-1.5 3-1.5 4.5 0"],
    check: ["M5 12.5l4.5 4.5L19 7.5"],
    refresh: ["M20 11a8 8 0 0 0-14.9-3", "M4 4v4h4", "M4 13a8 8 0 0 0 14.9 3", "M20 20v-4h-4"],
    alert: ["M12 3.5 2.5 20h19z", "M12 10v4M12 17v.5"],
    megaphone: ["M3 10v4h3l6 4V6L6 10z", "M16 9a4 4 0 0 1 0 6M18.5 6.5a7.5 7.5 0 0 1 0 11"],
    coins: ["M4 7c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3z", "M4 7v5c0 1.7 3.6 3 8 3s8-1.3 8-3V7", "M4 12v5c0 1.7 3.6 3 8 3s8-1.3 8-3v-5"],
    works: ["M4 20V10l8-6 8 6v10", "M9 20v-5h6v5M4 14h16"],
    columns: ["M3 9l9-5 9 5", "M5 9v9M9.5 9v9M14.5 9v9M19 9v9M3 20h18"],
    book: ["M12 6.5C10 5 7 4.5 4 5v13c3-.5 6 0 8 1.5 2-1.5 5-2 8-1.5V5c-3-.5-6 0-8 1.5z", "M12 6.5v13"],
    cycle: ["M20 12a8 8 0 0 1-14 5.3", "M4 12a8 8 0 0 1 14-5.3", "M18 3.5V7h-3.5", "M6 20.5V17h3.5"],
    shield: ["M12 3 4.5 6v6c0 4.5 3.2 7.8 7.5 9 4.3-1.2 7.5-4.5 7.5-9V6z"],
    folder: ["M3 7a1.5 1.5 0 0 1 1.5-1.5H9l2 2h8.5A1.5 1.5 0 0 1 21 9v8.5a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z"],
    file: ["M6 3h8l4 4v14H6z", "M14 3v4h4M9 12h6M9 16h6"],
    prev: ["M6.5 5.5v13", "M18 6l-8.5 6 8.5 6z"],
    next: ["M17.5 5.5v13", "M6 6l8.5 6L6 18z"],
    x: ["M6 6l12 12M18 6 6 18"],
    chevR: ["M9 5l7 7-7 7"],
    back: ["M19 12H5M11 6l-6 6 6 6"],
    robot: ["M6 8.5h12v10H6z", "M12 4.5v4", "c 12 4 1.2", "c 9.5 13 1.2", "c 14.5 13 1.2", "M10 16.5h4", "M3.5 12.5v3M20.5 12.5v3"],
    expand: ["M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"],
    grave: ["M7 21V10a5 5 0 0 1 10 0v11", "M5 21h14", "M12 9v6M9.5 11.5h5"],
    scroll: ["M7 4h11a2 2 0 0 1 0 4h-1v10a2 2 0 0 1-2 2H6a2 2 0 0 1 0-4h1z", "M10 9h5M10 12.5h5"],
    notebook: ["M6 3h11a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H6z", "M9 3v18", "M12 8h3M12 11h3"],
    sparkle: ["M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z", "M19 15.5l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z"],
    info: ["c 12 12 9", "M12 11v5.5M12 7.8v.4"],
    granary: ["M4 20V11l8-6 8 6v9", "M4 20h16", "M9 20v-5a3 3 0 0 1 6 0v5"],
    skull: ["M12 3.5a7.5 7.5 0 0 0-4.5 13.5V20h9v-3A7.5 7.5 0 0 0 12 3.5z", "c 9.2 11.5 1.4", "c 14.8 11.5 1.4", "M10.5 20v-2M13.5 20v-2"],
    keyboard: ["M3 6h18v12H3z", "M6.5 9.5h.01M10 9.5h.01M13.5 9.5h.01M17 9.5h.01M7 14.5h10"],
    scale: ["M12 4v16M7.5 20h9", "M5 7h14", "M5 7l-2.5 6.5h5zM19 7l-2.5 6.5h5z"],
    brain: ["M9 4.5a3 3 0 0 0-3 3v.3A3 3 0 0 0 4 10.5a3 3 0 0 0 1 2.3A3 3 0 0 0 6 18a3 3 0 0 0 3 1.5 2.5 2.5 0 0 0 3-1V5.5A2.5 2.5 0 0 0 9 4.5z", "M15 4.5a3 3 0 0 1 3 3v.3a3 3 0 0 1 2 2.7 3 3 0 0 1-1 2.3 3 3 0 0 1-1 5.2 3 3 0 0 1-3 1.5 2.5 2.5 0 0 1-3-1"],
    code: ["M8.5 7 3.5 12l5 5", "M15.5 7l5 5-5 5", "M13.5 4.5l-3 15"],
    inbox: ["M4 13l2.5-7.5h11L20 13v6H4z", "M4 13h4.5l1 2h5l1-2H20"],
    graph: ["c 6 6 2.2", "c 18 6 2.2", "c 12 18 2.2", "M8 7l3 9M16 7l-3 9M8.2 6h7.6"],
    search: ["c 11 11 6.5", "M16 16l4.5 4.5"],
    route: ["c 6 18 2", "c 18 6 2", "M8 18h6a3.5 3.5 0 0 0 0-7h-4a3.5 3.5 0 0 1 0-7h6"],
    server: ["M4 4h16v6H4z", "M4 14h16v6H4z", "M8 7h.01M8 17h.01"],
    target: ["c 12 12 8.5", "c 12 12 4.5", "c 12 12 .8"],
  };

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
    for (const key of Object.keys(attrs || {})) if (attrs[key] !== null && attrs[key] !== undefined) el.setAttribute(key, String(attrs[key]));
    if (parent) parent.append(el);
    return el;
  }
  function icon(name, extra) {
    const svg = svgEl("svg", { viewBox: "0 0 24 24", class: "ic" + (extra ? " " + extra : ""), "aria-hidden": "true", focusable: "false" });
    for (const part of ICONS[name] || ICONS.star) {
      if (part.startsWith("c ")) {
        const [cx, cy, r] = part.slice(2).split(" ");
        svgEl("circle", { cx, cy, r }, svg);
      } else svgEl("path", { d: part }, svg);
    }
    return svg;
  }
  /** replaceChildren that skips null/undefined/false (the DOM would insert them as text). */
  function fill(el, ...children) {
    el.replaceChildren(...children.flat(Infinity).filter((c) => c !== null && c !== undefined && c !== false));
    return el;
  }
  /** Two rAFs: lets a freshly inserted element paint at its start value so a CSS transition runs. */
  function nextPaint(fn) { if (reduced()) fn(); else requestAnimationFrame(() => requestAnimationFrame(fn)); }

  // ------------------------------------------------------------------ formatting
  const intFormat = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 });
  const isNum = (v) => typeof v === "number" && Number.isFinite(v);
  const pts = (n) => (n !== null && n !== undefined && n !== "" && isNum(Number(n)) ? intFormat.format(Math.round(Number(n))) : "—");
  const times = (x) => (isNum(x) ? `${x.toFixed(2)}×` : "—");
  const pct = (x, digits = 0) => (isNum(x) ? `${(x * 100).toFixed(digits)}%` : "—");
  const fixed = (x, digits = 1) => (isNum(x) ? x.toFixed(digits) : "—");
  function compact(n) {
    if (!isNum(n)) return "—";
    const a = Math.abs(n);
    if (a >= 1e9) return `${(n / 1e9).toFixed(1)}B`;
    if (a >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
    if (a >= 1e4) return `${Math.round(n / 1e3)}k`;
    if (a >= 1e3) return `${(n / 1e3).toFixed(1)}k`;
    return String(Math.round(n));
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
  const dateTime = (iso) => { if (!iso) return ""; const d = new Date(iso); return Number.isFinite(d.getTime()) ? d.toLocaleString() : ""; };
  const dateShort = (iso) => { if (!iso) return ""; const d = new Date(iso); return Number.isFinite(d.getTime()) ? d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : ""; };
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
  const capitalize = (text) => String(text).charAt(0).toUpperCase() + String(text).slice(1);
  const humanize = (key) => capitalize(String(key || "").replace(/[_-]+/g, " "));
  function hashText(text) {
    let x = 2166136261;
    for (let i = 0; i < text.length; i++) { x ^= text.charCodeAt(i); x = Math.imul(x, 16777619); }
    return (x >>> 0) / 4294967296;
  }
  const hueColor = (text) => `hsl(${Math.round(hashText(String(text)) * 360)} 62% 64%)`;
  function initials(name) {
    const parts = String(name || "?").split(/[^A-Za-z0-9]+/).filter(Boolean);
    return (parts.slice(0, 2).map((p) => p[0]).join("") || "?").toUpperCase();
  }
  /** ISO-8601 duration (r"PT1H2M3S") as "1 h 2 min". */
  function durationText(iso) {
    const m = /^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$/.exec(String(iso || ""));
    if (!m) return iso ? String(iso) : "—";
    const d = Number(m[1] || 0), hr = Number(m[2] || 0), mi = Number(m[3] || 0), s = Number(m[4] || 0);
    if (!(d || hr || mi || s)) return "under a second";
    const parts = [];
    if (d) parts.push(`${d} d`);
    if (hr) parts.push(`${hr} h`);
    if (mi) parts.push(`${mi} min`);
    if (s && !d && !hr) parts.push(`${Math.round(s)} s`);
    return parts.join(" ");
  }
  const domainName = (d, header) => (header && header.domains && header.domains[d] && header.domains[d].name) || DOMAIN_INFO[d].name;
  const domainUnit = (d, header) => (header && header.domains && header.domains[d] && header.domains[d].unit) || DOMAIN_INFO[d].unit;
  const color70 = (hex) => `color-mix(in srgb, ${hex} 70%, white)`;
  /** Wellbeing 0–10 as the village renderer colours it. */
  function wbColor(wb) {
    const stops = [[0, [226, 87, 76]], [3, [232, 138, 58]], [5, [217, 180, 0]], [7, [156, 204, 101]], [10, [77, 208, 168]]];
    const v = Math.max(0, Math.min(10, isNum(wb) ? wb : 5));
    for (let i = 1; i < stops.length; i++) {
      if (v <= stops[i][0]) {
        const t = (v - stops[i - 1][0]) / (stops[i][0] - stops[i - 1][0]);
        return `rgb(${stops[i - 1][1].map((x, k) => Math.round(x + (stops[i][1][k] - x) * t)).join(",")})`;
      }
    }
    return "rgb(77,208,168)";
  }

  // ------------------------------------------------------------------ feedback: toasts, count-ups
  function toast(message, kind = "info", options = {}) {
    const box = $("toasts");
    if (!box) return null;
    const close = h("button", { type: "button", class: "t-close", "aria-label": "Dismiss notification" }, "×");
    const body = h("div", { class: "t-body" }, message);
    if (options.detail) body.append(h("small", { class: "t-detail" }, options.detail));
    if (options.action) {
      const btn = h("button", { type: "button", class: "small t-action" }, options.action.label);
      btn.addEventListener("click", () => { options.action.run(); dismiss(); });
      body.append(btn);
    }
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
  /** Animates a number from its last shown value (per element) to `value`; bumps when it grows. */
  const shown = new WeakMap();
  function countTo(el, value, duration = 900, format = pts) {
    const target = Number(value);
    if (!isNum(target)) { el.textContent = format(value); return; }
    const from = shown.has(el) ? shown.get(el) : 0;
    shown.set(el, target);
    if (reduced() || from === target || target < from) { el.textContent = format(target); return; }
    const t0 = performance.now();
    const step = (now) => {
      if (shown.get(el) !== target) return;
      const t = Math.min(1, (now - t0) / duration);
      el.textContent = format(from + (target - from) * (1 - Math.pow(1 - t, 3)));
      if (t < 1 && el.isConnected) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
    el.classList.remove("bump");
    void el.offsetWidth;
    el.classList.add("bump");
  }

  // ------------------------------------------------------------------ small components
  function badge(text, kind, iconName) {
    return h("span", { class: "badge" + (kind ? " " + kind : "") }, iconName ? icon(iconName) : null, text);
  }
  function refTag() {
    return h("span", { class: "ref-tag", title: "Scripted reference steward: a milestone, not a ranked model" }, icon("robot"), "reference");
  }
  function ruinsBadge(text) {
    return h("span", { class: "ruins" }, icon("ruins"), text || "Collapsed");
  }
  function emptyNote(iconName, title, text, ...actions) {
    return h("div", { class: "empty-note" }, icon(iconName), h("b", null, title), text ? h("p", null, text) : null,
      actions.length ? h("div", { class: "row center" }, actions) : null);
  }
  function actionButton(iconName, label, cls, handler, iconCls) {
    const button = h("button", { type: "button", class: cls || "" }, iconName ? icon(iconName, iconCls) : null, h("span", null, label));
    button.addEventListener("click", (event) => handler(event.currentTarget, event));
    return button;
  }
  function linkButton(iconName, label, href, cls, iconCls) {
    return h("a", { class: "button " + (cls || ""), href }, iconName ? icon(iconName, iconCls) : null, h("span", null, label));
  }
  /** Radio pills (the platform's segmented control). */
  function segmented(name, options, value, onChange, label) {
    const box = h("div", { class: "segmented", role: "radiogroup", "aria-label": label || name });
    for (const opt of options) {
      const input = h("input", { type: "radio", name, value: opt.value });
      input.checked = opt.value === value;
      input.addEventListener("change", () => { if (input.checked) onChange(opt.value); });
      box.append(h("label", { title: opt.title || null, class: opt.cls || null }, input, opt.icon ? icon(opt.icon, "sm") : null, h("span", null, opt.label),
        opt.count !== undefined && opt.count !== null ? h("em", null, String(opt.count)) : null));
    }
    return box;
  }
  function avatar(name, isRef, size) {
    return h("span", { class: "avatar" + (isRef ? " ref" : "") + (size ? " " + size : ""), style: { "--av": isRef ? "#8a8a8a" : hueColor(name) }, "aria-hidden": "true" },
      isRef ? icon("robot") : initials(name));
  }

  // ------------------------------------------------------------------ domain bars
  function domainIcon(domain) {
    return h("span", { class: "skill-icon", style: { "--c": DOMAIN_COLORS[domain] } }, icon(DOMAIN_INFO[domain].icon));
  }
  /** Eight tiny vertical bars (one per domain), the platform's leaderboard equalizer. */
  function equalizer(domains, expert, scales, header) {
    const label = DOMAINS.map((d) => `${domainName(d, header)} ${pts(domains[d])}`).join(", ");
    const eq = h("div", { class: "eq", role: "img", "aria-label": `Points by domain: ${label}` });
    DOMAINS.forEach((d, k) => {
      const value = Math.max(0, domains[d] || 0);
      const fill = h("i");
      const bar = h("div", { class: "eq-bar", style: { "--c": DOMAIN_COLORS[d], "--k": k }, title: `${d} ${domainName(d, header)}: ${pts(domains[d])} points${expert ? ` (expert ${pts(expert[d])})` : ""}` }, fill);
      if (expert && isNum(expert[d])) bar.append(h("u", { style: { bottom: `${Math.min(100, (Math.max(0, expert[d]) / scales[d]) * 100)}%` } }));
      const height = `${Math.min(100, (value / scales[d]) * 100)}%`;
      nextPaint(() => { fill.style.height = height; });
      eq.append(bar);
    });
    return eq;
  }
  /** Eight labelled skill bars; `expert` draws a reference tick and the value shows ×expert. */
  function skillBars(domains, expert, scales, header) {
    const box = h("div", { class: "skills" });
    for (const d of DOMAINS) {
      const value = (domains || {})[d] || 0;
      const fill = h("i");
      const track = h("div", { class: "skill-track", "aria-hidden": "true" }, fill);
      if (expert && isNum(expert[d])) track.append(h("u", { style: { left: `${Math.min(100, (Math.max(0, expert[d]) / scales[d]) * 100)}%` }, title: `Expert: ${pts(expert[d])}` }));
      const rel = expert && expert[d] > 0 ? ` · ${(value / expert[d]).toFixed(2)}×` : "";
      box.append(h("div", { class: "skill", style: { "--c": DOMAIN_COLORS[d] }, title: domainUnit(d, header) },
        domainIcon(d), h("span", { class: "skill-name" }, `${d} · ${domainName(d, header)}`),
        h("span", { class: "skill-val" }, pts(value), h("small", null, rel)), track));
      const width = `${Math.min(100, (Math.max(0, value) / (scales[d] || 1)) * 100)}%`;
      nextPaint(() => { fill.style.width = width; });
    }
    return box;
  }
  function domainScales(lists) {
    const scales = {};
    for (const d of DOMAINS) scales[d] = Math.max(1, ...lists.filter(Boolean).map((x) => Math.max(0, x[d] || 0))) * 1.1;
    return scales;
  }

  // ------------------------------------------------------------------ charts
  function pathFor(values, n, min, max, W, H) {
    let d = "";
    let pen = false;
    for (let i = 0; i < values.length; i++) {
      const v = values[i];
      if (!isNum(v)) { pen = false; continue; }
      const x = n > 1 ? (i / (n - 1)) * W : W / 2;
      const y = H - ((Math.max(min, Math.min(max, v)) - min) / (max - min || 1)) * H;
      d += `${pen ? "L" : "M"}${x.toFixed(2)} ${y.toFixed(2)}`;
      pen = true;
    }
    return d;
  }
  /**
   * A line chart with a week crosshair. series: [{values, color, label}]; options: {label, icon, min, max,
   * minSpan, format, height, markers: [{i, color, label}], refLines: [{value, color}], weekOf, onSeek}.
   * Returns an element with setIndex(i) to move the playhead.
   */
  function lineChart(series, options = {}) {
    const n = Math.max(1, ...series.map((s) => s.values.length));
    const all = series.flatMap((s) => s.values.filter(isNum));
    const min = isNum(options.min) ? options.min : Math.min(...all, 0);
    let max = isNum(options.max) ? options.max : Math.max(...all, 1);
    if (isNum(options.minSpan) && max - min < options.minSpan) max = min + options.minSpan;
    if (max === min) max = min + 1;
    const W = 200, H = 60;
    const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "none", class: "lc-svg", "aria-hidden": "true" });
    for (const g of [0.25, 0.5, 0.75]) svgEl("line", { x1: 0, x2: W, y1: H * g, y2: H * g, class: "lc-grid" }, svg);
    for (const ref of options.refLines || []) {
      if (!isNum(ref.value) || ref.value < min || ref.value > max) continue;
      const y = H - ((ref.value - min) / (max - min)) * H;
      svgEl("line", { x1: 0, x2: W, y1: y, y2: y, stroke: ref.color || "#ef5350", "stroke-width": 1, "stroke-dasharray": "4 3", "vector-effect": "non-scaling-stroke", opacity: 0.8 }, svg);
    }
    for (const s of series) {
      const d = pathFor(s.values, n, min, max, W, H);
      if (!d) continue;
      if (series.length === 1) {
        const first = s.values.findIndex(isNum);
        let last = s.values.length - 1;
        while (last > 0 && !isNum(s.values[last])) last -= 1;
        const x0 = n > 1 ? (first / (n - 1)) * W : 0, x1 = n > 1 ? (last / (n - 1)) * W : W;
        svgEl("path", { d: `${d}L${x1.toFixed(2)} ${H}L${x0.toFixed(2)} ${H}Z`, fill: s.color, opacity: 0.13, stroke: "none" }, svg);
      }
      svgEl("path", { d, fill: "none", stroke: s.color, "stroke-width": s.width || 1.6, "vector-effect": "non-scaling-stroke", "stroke-linejoin": "round" }, svg);
    }
    const plot = h("div", { class: "lc-plot", style: { height: `${options.height || 46}px` } }, svg);
    for (const m of options.markers || []) {
      plot.append(h("span", { class: "lc-marker", style: { left: `${n > 1 ? (m.i / (n - 1)) * 100 : 50}%`, "--mc": m.color || "var(--collapse)" }, title: m.label || "" }));
    }
    const cross = h("span", { class: "lc-cross", "aria-hidden": "true" });
    const dots = series.map((s) => h("span", { class: "lc-dot", style: { "--dc": s.color }, "aria-hidden": "true" }));
    plot.append(cross, ...dots);
    const readout = h("span", { class: "lc-now" });
    const fmt = options.format || ((v) => fixed(v, 2));
    const legend = series.length > 1 ? h("div", { class: "lc-legend" }, series.map((s) => h("span", null, h("i", { style: { background: s.color } }), s.label || ""))) : null;
    const el = h("div", { class: "lc" + (options.cls ? " " + options.cls : ""), role: "img", "aria-label": options.aria || options.label || "chart" },
      h("div", { class: "lc-head" }, h("span", { class: "lc-name" }, options.icon ? icon(options.icon, "sm") : null, options.label || ""), readout), plot, legend);
    let index = 0, hover = null;
    const place = (i) => {
      const k = Math.max(0, Math.min(n - 1, i));
      const left = n > 1 ? (k / (n - 1)) * 100 : 50;
      cross.style.left = `${left}%`;
      series.forEach((s, j) => {
        const v = s.values[k];
        const dot = dots[j];
        if (!isNum(v)) { dot.style.opacity = "0"; return; }
        dot.style.opacity = "1";
        dot.style.left = `${left}%`;
        dot.style.top = `${(1 - (Math.max(min, Math.min(max, v)) - min) / (max - min)) * 100}%`;
      });
      readout.replaceChildren();
      if (series.length === 1) readout.append(fmt(series[0].values[k]));
      else if (series.length <= 3) series.forEach((s) => readout.append(h("b", { style: { color: s.color } }, fmt(s.values[k]))));
      if (options.weekOf) readout.append(h("small", null, ` · w${options.weekOf(k)}`));
    };
    el.setIndex = (i) => { index = i; if (hover === null) place(i); };
    const pick = (event) => {
      const r = plot.getBoundingClientRect();
      return Math.round(Math.max(0, Math.min(1, (event.clientX - r.left) / (r.width || 1))) * (n - 1));
    };
    plot.addEventListener("pointermove", (event) => { if (event.pointerType === "touch") return; hover = pick(event); place(hover); plot.classList.add("hovering"); });
    plot.addEventListener("pointerleave", () => { hover = null; place(index); plot.classList.remove("hovering"); });
    if (options.onSeek) {
      plot.classList.add("seekable");
      plot.addEventListener("click", (event) => options.onSeek(pick(event)));
    }
    place(0);
    return el;
  }

  window.ExplorerUI = Object.freeze({
    DOMAINS, DOMAIN_INFO, DOMAIN_COLORS, ICONS,
    $, h, fill, svgEl, icon, reduced, nextPaint, setStyle,
    isNum, pts, times, pct, fixed, compact, relTime, dateTime, dateShort, splitName, excerpt, capitalize, humanize, hashText, hueColor, initials, durationText,
    domainName, domainUnit, color70, wbColor,
    toast, countTo,
    badge, refTag, ruinsBadge, emptyNote, actionButton, linkButton, segmented, avatar,
    domainIcon, equalizer, skillBars, domainScales, lineChart,
  });
})();
