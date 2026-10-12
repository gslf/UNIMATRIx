"use strict";
/*
 * SocietyViz: small, dependency-free SVG charts for the UNIMATRIx society report.
 *
 *   timeChart(el, spec)       lines, washes, columns or stacked areas over weeks, with a
 *                             crosshair + tooltip, keyboard navigation and a data table
 *   heatStrip(el, spec)       rows x weeks of discrete levels (e.g. skill mastery)
 *   network(el, spec)         a social network drawn at precomputed positions
 *   forceLayout(nodes, links) deterministic force-directed layout in the unit square
 *   compositionBar(el, spec)  a horizontal part-to-whole bar
 *   ladder(el, spec)          a value placed on an open-ended axis among references
 *   sparkline(el, spec)       a decorative trend line
 *
 * Charts sharing `spec.group` show a linked crosshair. On touch screens a tap (or a horizontal
 * drag) moves the crosshair and the tooltip stays up until the next tap elsewhere; vertical
 * swipes still scroll the page. All text is inserted with textContent and every colour coming
 * from data is validated as a hex string.
 */
(function () {
  const NS = "http://www.w3.org/2000/svg";

  const palette = Object.freeze({
    surface: "#1c1c1c",
    neutral: "#6f6f6f",
    accent: "#b59a00",
    // At most four series per chart: gold accent, blue, magenta, green (all pairs CVD-separated).
    lines: Object.freeze(["#b59a00", "#3987e5", "#d55181", "#008300"]),
    // Eight identities in an adjacency-validated order (used for the eight score domains).
    categorical: Object.freeze(["#3987e5", "#d95926", "#199e70", "#b59a00", "#d55181", "#008300", "#9085e9", "#e66767"]),
    status: Object.freeze({ good: "#0ca30c", watch: "#fab219", bad: "#d03b3b" }),
    references: Object.freeze({ passive: "#5b5b5b", naive: "#7d7d7d", expert: "#a3a3a3", oracle: "#cfcfcf" }),
  });

  const prefersReducedMotion = () => Boolean(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);

  // ------------------------------------------------------------------ basics
  function isNum(value) {
    return typeof value === "number" && Number.isFinite(value);
  }

  function safeColor(value, fallback) {
    return typeof value === "string" && /^#[0-9a-fA-F]{3,8}$/.test(value) ? value : (fallback || palette.neutral);
  }

  function luminance(hex) {
    let value = String(hex || "").replace("#", "");
    if (value.length === 3) value = value.split("").map(c => c + c).join("");
    const channels = [0, 2, 4].map(i => parseInt(value.slice(i, i + 2), 16) / 255).map(c => (c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)));
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
  }

  // Text on a coloured fill: whichever of near-black or white contrasts more.
  function inkFor(hex) {
    const l = luminance(safeColor(hex, palette.neutral));
    return (l + 0.05) / 0.0563 >= 1.05 / (l + 0.05) ? "#121212" : "#ffffff";
  }

  function svg(tag, attrs, parent) {
    const node = document.createElementNS(NS, tag);
    if (attrs) {
      for (const key of Object.keys(attrs)) {
        const value = attrs[key];
        if (value !== undefined && value !== null && value !== false) node.setAttribute(key, String(value));
      }
    }
    if (parent) parent.appendChild(node);
    return node;
  }

  function svgText(attrs, content, parent) {
    const node = svg("text", attrs, parent);
    node.textContent = content;
    return node;
  }

  function html(tag, className, parent, content) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (content !== undefined && content !== null) node.textContent = String(content);
    if (parent) parent.appendChild(node);
    return node;
  }

  // ------------------------------------------------------------------ formatting
  const formatters = new Map();

  function number(value, digits = 0, fixed = true) {
    if (!isNum(value)) return "–";
    const key = `${digits}:${fixed}`;
    let formatter = formatters.get(key);
    if (!formatter) {
      formatter = new Intl.NumberFormat("en-US", { minimumFractionDigits: fixed ? digits : 0, maximumFractionDigits: digits });
      formatters.set(key, formatter);
    }
    const out = formatter.format(value);
    return out === "-0" ? "0" : out;
  }

  function compact(value) {
    if (!isNum(value)) return "–";
    const abs = Math.abs(value);
    if (abs >= 1e6) return number(value / 1e6, 1, false) + "M";
    if (abs >= 1e4) return number(value / 1e3, 0) + "k";
    if (abs >= 1e3) return number(value / 1e3, 1, false) + "k";
    if (abs >= 100 || Number.isInteger(value)) return number(value, 0);
    if (abs >= 10) return number(value, 1, false);
    return number(value, 2, false);
  }

  function percent(value, digits = 0) {
    return isNum(value) ? number(value * 100, digits) + "%" : "–";
  }

  function decimal(value, digits = 2) {
    return isNum(value) ? number(value, digits) : "–";
  }

  const format = Object.freeze({ number, compact, percent, decimal });

  // ------------------------------------------------------------------ scales
  function niceStep(span, count) {
    const raw = Math.max(span, 1e-9) / Math.max(1, count);
    const power = Math.pow(10, Math.floor(Math.log10(raw)));
    const unit = raw / power;
    return (unit < 1.5 ? 1 : unit < 3 ? 2 : unit < 7 ? 5 : 10) * power;
  }

  function ticks(min, max, count) {
    if (!(max > min)) max = min + 1;
    const step = niceStep(max - min, count);
    const out = [];
    for (let i = Math.ceil(min / step - 1e-9); i * step <= max + step * 1e-6; i += 1) out.push(+(i * step).toPrecision(12));
    return out;
  }

  function domainOf(values, options) {
    const o = options || {};
    let lo = values.length ? Math.min(...values) : 0;
    let hi = values.length ? Math.max(...values) : 1;
    if (isNum(o.min)) lo = Math.min(o.min, lo);
    if (isNum(o.max)) hi = Math.max(o.max, hi);
    if (o.zero !== false) lo = Math.min(0, lo);
    if (!(hi > lo)) hi = lo + (Math.abs(lo) || 1);
    const step = niceStep(hi - lo, o.ticks || 4);
    if (!(isNum(o.max) && o.max >= hi)) hi = Math.ceil(hi / step - 1e-9) * step;
    if (!(isNum(o.min) && o.min <= lo)) lo = Math.floor(lo / step + 1e-9) * step;
    return [lo, hi];
  }

  function hash(text) {
    let h = 2166136261;
    for (let i = 0; i < text.length; i += 1) {
      h ^= text.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return (h >>> 0) / 4294967295;
  }

  // ------------------------------------------------------------------ registry, linking, resizing
  const groups = new Map();
  const observed = new WeakMap();
  const resizer = typeof ResizeObserver === "function"
    ? new ResizeObserver(entries => entries.forEach(entry => {
      const chart = observed.get(entry.target);
      if (chart) chart.resized();
    }))
    : null;

  function join(chart) {
    const key = chart.spec.group;
    if (!key) return;
    if (!groups.has(key)) groups.set(key, new Set());
    groups.get(key).add(chart);
  }

  function leave(chart) {
    const key = chart.spec.group;
    if (key && groups.has(key)) groups.get(key).delete(chart);
  }

  function broadcast(source, value) {
    const key = source.spec.group;
    if (!key || !groups.has(key)) return;
    groups.get(key).forEach(chart => {
      if (chart === source) return;
      if (!chart.root.isConnected) {
        groups.get(key).delete(chart);
        return;
      }
      chart.echo(value);
    });
  }

  // ------------------------------------------------------------------ touch: tooltips pinned until the next tap elsewhere
  const pinned = new Set();
  document.addEventListener("pointerdown", event => {
    pinned.forEach(item => {
      if (item.contains(event.target)) return;
      pinned.delete(item);
      item.clear();
    });
  }, true);
  const isTouch = event => Boolean(event && event.pointerType && event.pointerType !== "mouse");

  // ------------------------------------------------------------------ tooltip
  class Tip {
    constructor(frame) {
      this.frame = frame;
      this.node = html("div", "viz-tip", frame);
      this.node.hidden = true;
      this.node.setAttribute("aria-hidden", "true");
    }

    fill(model) {
      const node = this.node;
      node.textContent = "";
      if (model.title) html("div", "viz-tip-title", node, model.title);
      if (model.subtitle) html("div", "viz-tip-sub", node, model.subtitle);
      if (model.rows && model.rows.length) {
        const list = html("div", "viz-tip-rows", node);
        model.rows.forEach(row => {
          const line = html("div", "viz-tip-row" + (row.strong ? " is-total" : ""), list);
          const key = html("i", "viz-key " + (row.key || "line"), line);
          if (row.color) key.style.background = row.color;
          else key.classList.add("is-empty");
          html("strong", "", line, row.value);
          html("span", "", line, row.label);
        });
      }
      (model.notes || []).forEach(note => html("div", "viz-tip-note", node, note));
    }

    /** Beside the pointer; above the finger on touch. Always kept inside the frame and the window. */
    place(x, y, above) {
      const node = this.node;
      node.hidden = false;
      const width = this.frame.clientWidth;
      const w = node.offsetWidth;
      const h = node.offsetHeight;
      let left;
      let top;
      if (above) {
        left = x - w / 2;
        top = y - h - 26;
        if (top < -h * 0.6) top = y + 26;
      } else {
        left = x + 16;
        if (left + w > width - 2) left = x - w - 16;
        top = Math.max(-8, y - h / 2);
      }
      left = Math.max(2, Math.min(left, width - w - 2));
      // Keep it on screen even when the frame itself sits near an edge of the window.
      const box = this.frame.getBoundingClientRect();
      const viewW = document.documentElement.clientWidth || window.innerWidth;
      left = Math.min(left, viewW - 6 - box.left - w);
      left = Math.max(left, 6 - box.left);
      top = Math.max(top, 6 - box.top);
      node.style.left = `${Math.round(left)}px`;
      node.style.top = `${Math.round(top)}px`;
    }

    hide() {
      this.node.hidden = true;
    }

    static text(model) {
      const parts = [model.title, model.subtitle];
      (model.rows || []).forEach(row => parts.push(`${row.label} ${row.value}`));
      (model.notes || []).forEach(note => parts.push(note));
      return parts.filter(Boolean).join(". ");
    }
  }

  // ------------------------------------------------------------------ data table (text alternative)
  function attachTable(container, build, label) {
    const details = html("details", "viz-data", container);
    html("summary", "", details, label || "Data table");
    let dirty = true;
    const render = () => {
      if (!details.open || !dirty) return;
      dirty = false;
      details.querySelectorAll(".viz-table-wrap").forEach(node => node.remove());
      const model = build();
      const wrap = html("div", "viz-table-wrap", details);
      const table = html("table", "viz-table", wrap);
      const head = html("tr", "", html("thead", "", table));
      model.columns.forEach(column => {
        const th = html("th", "", head, column);
        th.scope = "col";
      });
      const body = html("tbody", "", table);
      model.rows.forEach(row => {
        const tr = html("tr", "", body);
        row.forEach((cell, index) => {
          const td = html(index === 0 ? "th" : "td", "", tr, cell);
          if (index === 0) td.scope = "row";
        });
      });
    };
    details.addEventListener("toggle", render);
    return { refresh() { dirty = true; render(); } };
  }

  function legend(node, items) {
    node.textContent = "";
    node.hidden = !items.length;
    items.forEach(item => {
      const entry = html("span", "viz-legend-item", node);
      const key = html("i", "viz-key " + (item.key || "line") + (item.dash ? " is-dashed" : ""), entry);
      key.style.background = item.color;
      if (item.dash) key.style.borderColor = item.color;
      html("span", "", entry, item.label);
    });
  }

  // ------------------------------------------------------------------ interactive base
  class Interactive {
    constructor(container, spec, kind) {
      this.root = container;
      this.spec = Object.assign({}, spec || {});
      this.index = null;
      this.first = true;
      this.lastWidth = 0;
      container.textContent = "";
      container.classList.add("viz", "viz-" + kind);
      this.legendNode = html("div", "viz-legend", container);
      this.legendNode.hidden = true;
      this.frame = html("div", "viz-frame", container);
      this.svgNode = svg("svg", { class: "viz-svg", role: "img", tabindex: "0" }, this.frame);
      this.tip = new Tip(this.frame);
      this.announcer = html("div", "sr-only", container);
      this.announcer.setAttribute("aria-live", "polite");
      this.bind();
      join(this);
      if (resizer) {
        observed.set(this.frame, this);
        resizer.observe(this.frame);
      }
    }

    // Subclasses implement: count(), locate(x, y), anchor(i), model(i), mark(i), unmark(), key(i), echo(value), render().
    key() { return null; }
    echo() {}

    bind() {
      const node = this.svgNode;
      const move = event => {
        const rect = node.getBoundingClientRect();
        const x = event.clientX - rect.left;
        const y = event.clientY - rect.top;
        const index = this.locate(x, y);
        if (index === null || index === undefined) {
          if (!this.touching) this.clear(true);
          return;
        }
        this.activate(index, x, y, false);
      };
      const pin = { contains: target => node.contains(target), clear: () => this.clear(true) };
      node.addEventListener("pointermove", event => {
        if (!isTouch(event) || this.touching) move(event);
      });
      node.addEventListener("pointerdown", event => {
        this.pointerAt = Date.now();
        this.touchMode = isTouch(event);
        this.touching = this.touchMode;
        if (this.touchMode) pinned.add(pin);
        move(event);
      });
      node.addEventListener("pointerup", () => { this.touching = false; });
      node.addEventListener("pointercancel", () => { this.touching = false; });
      node.addEventListener("pointerleave", event => { if (!isTouch(event)) this.clear(true); });
      node.addEventListener("focus", () => {
        if (Date.now() - (this.pointerAt || 0) < 400) return;
        const count = this.count();
        if (!count) return;
        const index = this.index === null ? this.defaultIndex(count) : Math.min(this.index, count - 1);
        const at = this.anchor(index);
        this.activate(index, at.x, at.y, true);
      });
      node.addEventListener("blur", () => this.clear(true));
      node.addEventListener("keydown", event => {
        const count = this.count();
        if (!count) return;
        let index = this.index === null ? this.defaultIndex(count) : this.index;
        switch (event.key) {
          case "ArrowRight": case "ArrowUp": index = Math.min(count - 1, index + 1); break;
          case "ArrowLeft": case "ArrowDown": index = Math.max(0, index - 1); break;
          case "PageUp": index = Math.min(count - 1, index + 5); break;
          case "PageDown": index = Math.max(0, index - 5); break;
          case "Home": index = 0; break;
          case "End": index = count - 1; break;
          case "Escape": this.clear(true); return;
          default: return;
        }
        event.preventDefault();
        const at = this.anchor(index);
        this.activate(index, at.x, at.y, true);
      });
    }

    defaultIndex(count) {
      return count - 1;
    }

    activate(index, x, y, keyboard) {
      this.index = index;
      this.mark(index);
      const model = this.model(index);
      if (model) {
        this.tip.fill(model);
        this.tip.place(x, y, this.touchMode && !keyboard);
        if (keyboard) this.announcer.textContent = Tip.text(model);
      } else {
        this.tip.hide();
      }
      broadcast(this, this.key(index));
    }

    clear(propagate) {
      this.unmark();
      this.tip.hide();
      if (propagate) broadcast(this, null);
    }

    resized() {
      if (!this.root.isConnected) return;
      const width = Math.round(this.frame.clientWidth);
      if (!width || Math.abs(width - this.lastWidth) < 2) return;
      cancelAnimationFrame(this.raf);
      this.raf = requestAnimationFrame(() => this.draw());
    }

    draw() {
      this.lastWidth = Math.round(this.frame.clientWidth);
      this.render();
      this.first = false;
      if (this.table) this.table.refresh();
    }

    update(spec) {
      leave(this);
      this.spec = Object.assign({}, this.spec, spec || {});
      join(this);
      this.draw();
    }

    destroy() {
      leave(this);
      if (resizer) resizer.unobserve(this.frame);
      cancelAnimationFrame(this.raf);
    }
  }

  // ------------------------------------------------------------------ time chart
  function stackValues(series, length) {
    const base = new Array(length).fill(0);
    return series.map(item => item.values.slice(0, length).map((value, index) => {
      const v = isNum(value) ? Math.max(0, value) : 0;
      const lower = base[index];
      base[index] = lower + v;
      return [lower, lower + v];
    }));
  }

  function segments(xs, values, px, py) {
    const out = [];
    let current = [];
    values.forEach((value, index) => {
      if (isNum(value) && index < xs.length) current.push([px(xs[index]), py(value)]);
      else if (current.length) {
        out.push(current);
        current = [];
      }
    });
    if (current.length) out.push(current);
    return out;
  }

  function pathOf(points, step) {
    if (!points.length) return "";
    let d = `M${points[0][0].toFixed(1)},${points[0][1].toFixed(1)}`;
    for (let i = 1; i < points.length; i += 1) {
      const [x, y] = points[i];
      d += step ? `H${x.toFixed(1)}V${y.toFixed(1)}` : `L${x.toFixed(1)},${y.toFixed(1)}`;
    }
    return d;
  }

  function columnPath(x, y, w, h, r) {
    const radius = Math.max(0, Math.min(r, w / 2, h));
    return `M${x.toFixed(1)},${(y + h).toFixed(1)}V${(y + radius).toFixed(1)}Q${x.toFixed(1)},${y.toFixed(1)} ${(x + radius).toFixed(1)},${y.toFixed(1)}`
      + `H${(x + w - radius).toFixed(1)}Q${(x + w).toFixed(1)},${y.toFixed(1)} ${(x + w).toFixed(1)},${(y + radius).toFixed(1)}V${(y + h).toFixed(1)}Z`;
  }

  function animateDraw(path) {
    path.setAttribute("pathLength", "1");
    path.classList.add("viz-draw");
  }

  class TimeChart extends Interactive {
    constructor(container, spec) {
      super(container, spec, "time");
      if (this.spec.table !== false) this.table = attachTable(container, () => this.tableModel(), this.spec.tableLabel);
      this.draw();
    }

    get xs() { return this.spec.x || []; }

    series() {
      return (this.spec.series || []).filter(item => item && !item.hidden).map((item, index) => Object.assign({
        type: "line",
        color: palette.lines[index % palette.lines.length],
      }, item, { color: safeColor(item.color, palette.lines[index % palette.lines.length]) }));
    }

    formatFor(item) {
      return item.format || (this.spec.y && this.spec.y.format) || compact;
    }

    render() {
      const s = this.spec;
      const node = this.svgNode;
      const xs = this.xs;
      const series = this.series();
      this.items = series;
      const width = Math.max(200, this.lastWidth || Math.round(this.frame.clientWidth) || 560);
      const height = s.height || 220;
      node.textContent = "";
      node.setAttribute("viewBox", `0 0 ${width} ${height}`);
      node.setAttribute("width", String(width));
      node.setAttribute("height", String(height));
      node.setAttribute("aria-label", s.label || "Chart");

      if (s.legend !== false) {
        legend(this.legendNode, series.length > 1 || s.legend === true
          ? series.map(item => ({ label: item.label, color: item.color, key: item.type === "bar" || s.stack ? "box" : "line", dash: item.dash }))
          : []);
      }

      const stacks = s.stack ? stackValues(series, xs.length) : null;
      this.stacks = stacks;
      const values = [];
      if (stacks) stacks.forEach(layer => layer.forEach(pair => values.push(pair[1])));
      else series.forEach(item => item.values.forEach(value => { if (isNum(value)) values.push(value); }));
      (s.thresholds || []).forEach(t => { if (isNum(t.y)) values.push(t.y); });
      const yOptions = s.y || {};
      const [lo, hi] = domainOf(values, Object.assign({ ticks: height < 140 ? 2 : 4 }, yOptions));
      const yFormat = yOptions.format || compact;
      const yTicks = ticks(lo, hi, yOptions.ticks || (height < 140 ? 2 : 4));
      const compactMode = Boolean(s.compact);
      const tickWidth = compactMode ? 0 : Math.max(...yTicks.map(t => yFormat(t).length), 1) * 6.6 + 12;
      const markers = (s.markers || []).filter(m => isNum(m.x));
      const hasBars = series.some(item => item.type === "bar");
      const margin = {
        top: markers.length && !compactMode ? 22 : compactMode ? 6 : 12,
        right: s.endLabels ? 52 : compactMode ? 6 : 14,
        bottom: compactMode ? 6 : s.xLabel ? 36 : 24,
        left: compactMode ? 6 : tickWidth,
      };
      const plotW = Math.max(40, width - margin.left - margin.right);
      const plotH = Math.max(30, height - margin.top - margin.bottom);
      const x0 = s.xDomain ? s.xDomain[0] : (xs.length ? xs[0] : 0);
      let x1 = s.xDomain ? s.xDomain[1] : (xs.length ? xs[xs.length - 1] : 1);
      if (!(x1 > x0)) x1 = x0 + 1;
      const pad = hasBars ? 0.5 : 0;
      const px = v => margin.left + ((v - (x0 - pad)) / ((x1 + pad) - (x0 - pad))) * plotW;
      const py = v => margin.top + (1 - (v - lo) / (hi - lo)) * plotH;
      const base = py(Math.max(lo, Math.min(0, hi)));
      Object.assign(this, { px, py, margin, plotW, plotH, width, height, lo, hi });

      // grid and axes
      const grid = svg("g", { class: "viz-grid", "aria-hidden": "true" }, node);
      yTicks.forEach(t => {
        const y = py(t);
        svg("line", { x1: margin.left, x2: width - margin.right, y1: y, y2: y, class: t === 0 || t === lo ? "viz-baseline" : "viz-gridline" }, grid);
        if (!compactMode) svgText({ x: margin.left - 8, y: y + 3.5, class: "viz-tick", "text-anchor": "end" }, yFormat(t), grid);
      });
      if (!compactMode) {
        const span = x1 - x0;
        const room = Math.max(2, Math.floor(plotW / 46));
        const step = [1, 2, 4, 5, 10, 13, 26, 52, 100, 200].find(st => span / st <= room) || Math.ceil(span / room);
        for (let v = Math.ceil(x0 / step) * step; v <= x1 + 1e-9; v += step) {
          const x = px(v);
          svg("line", { x1: x, x2: x, y1: height - margin.bottom, y2: height - margin.bottom + 4, class: "viz-axis" }, grid);
          svgText({ x, y: height - margin.bottom + 16, class: "viz-tick", "text-anchor": "middle" }, String(v), grid);
        }
        if (s.xLabel) svgText({ x: width - margin.right, y: height - 4, class: "viz-axis-label", "text-anchor": "end" }, s.xLabel, grid);
      }

      // shaded spans (e.g. the weeks after a collapse)
      (s.bands || []).forEach(band => {
        if (!isNum(band.from)) return;
        const from = Math.max(x0, band.from);
        const to = Math.min(x1, isNum(band.to) ? band.to : x1);
        if (!(to > from)) return;
        svg("rect", { x: px(from), y: margin.top, width: Math.max(0, px(to) - px(from)), height: plotH, class: "viz-band" + (band.kind ? " is-" + band.kind : ""), "aria-hidden": "true" }, node);
        if (!compactMode && band.label && px(to) - px(from) > band.label.length * 6 + 16) {
          svgText({ x: (px(from) + px(to)) / 2, y: margin.top + 16, class: "viz-band-label", "text-anchor": "middle", "aria-hidden": "true" }, band.label, node);
        }
      });

      // story markers
      if (markers.length) {
        const layer = svg("g", { class: "viz-markers", "aria-hidden": "true" }, node);
        const taken = [];
        const priority = m => (m.kind === "collapse" ? 0 : 1);
        markers.slice().sort((a, b) => priority(a) - priority(b) || a.x - b.x).forEach(marker => {
          if (marker.x < x0 || marker.x > x1) return;
          const x = px(marker.x);
          svg("line", { x1: x, x2: x, y1: margin.top - (compactMode ? 0 : 6), y2: height - margin.bottom, class: "viz-marker" + (marker.kind ? " is-" + marker.kind : "") }, layer);
          if (compactMode || !marker.label) return;
          const label = marker.label.length > 22 ? marker.label.slice(0, 21) + "…" : marker.label;
          const estimate = label.length * 5.4;
          const anchorEnd = x + estimate + 4 > width - margin.right;
          const start = anchorEnd ? x - estimate - 3 : x + 3;
          const end = start + estimate;
          if (taken.some(([a, b]) => start < b + 6 && end > a - 6)) return;
          taken.push([start, end]);
          svgText({ x: anchorEnd ? x - 3 : x + 3, y: margin.top - 9, class: "viz-marker-label" + (marker.kind ? " is-" + marker.kind : ""), "text-anchor": anchorEnd ? "end" : "start" }, label, layer);
        });
      }

      // thresholds (labels end where a shaded span begins)
      const bandStart = Math.min(...(s.bands || []).filter(b => isNum(b.from) && b.from > x0 && b.from < x1).map(b => px(b.from)), width - margin.right);
      (s.thresholds || []).forEach(t => {
        if (!isNum(t.y) || t.y < lo || t.y > hi) return;
        const y = py(t.y);
        const line = svg("line", { x1: margin.left, x2: width - margin.right, y1: y, y2: y, class: "viz-threshold" }, node);
        line.style.stroke = safeColor(t.color, palette.status.bad);
        if (!compactMode && t.label) svgText({ x: bandStart - 4, y: y - 5, class: "viz-threshold-label", "text-anchor": "end" }, t.label, node);
      });

      // series
      const plot = svg("g", { class: "viz-plot" }, node);
      if (stacks) {
        series.forEach((item, index) => {
          const layer = stacks[index];
          const top = layer.map((pair, i) => [px(xs[i]), py(pair[1])]);
          const bottom = layer.map((pair, i) => [px(xs[i]), py(pair[0])]).reverse();
          if (!top.length) return;
          const area = svg("path", { d: pathOf(top) + "L" + bottom.map(p => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join("L") + "Z", class: "viz-stack" }, plot);
          area.style.fill = item.color;
          svg("path", { d: pathOf(top), class: "viz-stack-edge" }, plot);
        });
      } else {
        series.filter(item => item.type === "bar").forEach(item => {
          const slot = plotW / Math.max(1, (x1 - x0) + 1);
          const w = Math.max(1, Math.min(24, slot * 0.62));
          item.values.forEach((value, i) => {
            if (!isNum(value) || value === 0 || i >= xs.length) return;
            const top = py(Math.max(value, 0));
            const bottom = py(Math.min(value, 0) < lo ? lo : Math.min(value, 0));
            const bar = svg("path", { d: columnPath(px(xs[i]) - w / 2, Math.min(top, bottom), w, Math.abs(bottom - top), 3), class: "viz-bar" + (this.first && !prefersReducedMotion() ? " viz-grow" : "") }, plot);
            bar.style.fill = item.color;
          });
        });
        series.filter(item => item.type !== "bar").forEach(item => {
          const parts = segments(xs, item.values, px, py);
          if (item.area) {
            parts.forEach(points => {
              const d = pathOf(points, item.step) + `V${base.toFixed(1)}H${points[0][0].toFixed(1)}Z`;
              const area = svg("path", { d, class: "viz-area" }, plot);
              area.style.fill = item.color;
            });
          }
          parts.forEach(points => {
            const line = svg("path", { d: pathOf(points, item.step), class: "viz-line" + (item.dash ? " is-dashed" : "") }, plot);
            line.style.stroke = item.color;
            if (item.width) line.style.strokeWidth = String(item.width);
            if (this.first && !prefersReducedMotion() && !item.dash) animateDraw(line);
          });
        });
        // end dots and end labels
        const ends = [];
        series.filter(item => item.type !== "bar").forEach(item => {
          for (let i = Math.min(item.values.length, xs.length) - 1; i >= 0; i -= 1) {
            if (isNum(item.values[i])) {
              ends.push({ item, x: px(xs[i]), y: py(item.values[i]), value: item.values[i] });
              break;
            }
          }
        });
        ends.forEach(end => {
          const dot = svg("circle", { cx: end.x, cy: end.y, r: compactMode ? 3 : 3.5, class: "viz-end" }, plot);
          dot.style.fill = end.item.color;
        });
        if (s.endLabels && series.length <= 4) {
          const placed = [];
          ends.slice().sort((a, b) => a.y - b.y).forEach(end => {
            if (placed.some(y => Math.abs(y - end.y) < 12)) return;
            placed.push(end.y);
            svgText({ x: end.x + 8, y: end.y + 4, class: "viz-end-label" }, this.formatFor(end.item)(end.value), plot);
          });
        }
      }

      // crosshair
      this.cross = svg("g", { class: "viz-cross", visibility: "hidden", "aria-hidden": "true" }, node);
      this.crossLine = svg("line", { y1: margin.top, y2: height - margin.bottom, class: "viz-crossline" }, this.cross);
      this.crossDots = (stacks ? series : series.filter(item => item.type !== "bar")).map(item => {
        const dot = svg("circle", { r: 4, class: "viz-crossdot" }, this.cross);
        dot.style.fill = item.color;
        return { item, dot };
      });
      if (this.index !== null && this.index < xs.length) this.mark(this.index);
      else this.index = null;
    }

    count() { return this.xs.length; }

    locate(x) {
      const xs = this.xs;
      if (!xs.length || x < this.margin.left - 12 || x > this.width - this.margin.right + 12) return null;
      let best = null;
      let distance = Infinity;
      xs.forEach((value, index) => {
        const d = Math.abs(this.px(value) - x);
        if (d < distance) {
          distance = d;
          best = index;
        }
      });
      return best;
    }

    anchor(index) {
      return { x: this.px(this.xs[index]), y: this.margin.top + 24 };
    }

    key(index) { return this.xs[index]; }

    echo(value) {
      const index = value === null || value === undefined ? -1 : this.xs.indexOf(value);
      if (index < 0) this.unmark();
      else this.mark(index);
      this.tip.hide();
    }

    valueAt(item, index) {
      if (this.stacks) {
        const position = this.items.indexOf(item);
        return this.stacks[position] ? this.stacks[position][index][1] : null;
      }
      return item.values[index];
    }

    mark(index) {
      if (!this.cross) return;
      const x = this.px(this.xs[index]);
      this.cross.setAttribute("visibility", "visible");
      this.crossLine.setAttribute("x1", x.toFixed(1));
      this.crossLine.setAttribute("x2", x.toFixed(1));
      this.crossDots.forEach(({ item, dot }) => {
        const value = this.valueAt(item, index);
        if (isNum(value)) {
          dot.setAttribute("cx", x.toFixed(1));
          dot.setAttribute("cy", this.py(value).toFixed(1));
          dot.setAttribute("visibility", "visible");
        } else dot.setAttribute("visibility", "hidden");
      });
    }

    unmark() {
      if (this.cross) this.cross.setAttribute("visibility", "hidden");
    }

    model(index) {
      const s = this.spec;
      const x = this.xs[index];
      const items = this.items || [];
      const rows = (s.stack ? items.slice().reverse() : items).map(item => ({
        color: item.color,
        key: item.type === "bar" || s.stack ? "box" : "line",
        value: this.formatFor(item)(item.values[index]),
        label: item.label,
      }));
      if (s.stack) {
        const total = items.reduce((sum, item) => sum + (isNum(item.values[index]) ? item.values[index] : 0), 0);
        rows.push({ value: (s.y && s.y.format ? s.y.format : compact)(total), label: s.totalLabel || "Total", strong: true });
      }
      return {
        title: s.xTitle ? s.xTitle(x, index) : `Week ${x}`,
        rows,
        notes: s.notes ? (s.notes(x, index) || []) : [],
      };
    }

    tableModel() {
      const s = this.spec;
      const items = this.items || this.series();
      return {
        columns: [s.xName || "Week", ...items.map(item => item.label)],
        rows: this.xs.map((x, i) => [String(x), ...items.map(item => this.formatFor(item)(item.values[i]))]),
      };
    }
  }

  // ------------------------------------------------------------------ heat strip
  const LEVEL_STEPS = ["#262626", "#4f4420", "#8f7718", "#d4b11c", "#ffe27a"];

  function levelColor(value, max) {
    if (!isNum(value) || value <= 0) return LEVEL_STEPS[0];
    if (max <= 4 && Number.isInteger(value)) return LEVEL_STEPS[Math.min(4, Math.round(value + (4 - max)))];
    return ramp(value / max);
  }

  function ramp(t) {
    const from = [42, 38, 22];
    const to = [242, 212, 92];
    const k = Math.max(0, Math.min(1, t));
    const c = from.map((f, i) => Math.round(f + (to[i] - f) * Math.pow(k, 0.85)));
    return `rgb(${c[0]},${c[1]},${c[2]})`;
  }

  class HeatStrip extends Interactive {
    constructor(container, spec) {
      super(container, spec, "heat");
      if (this.spec.table !== false) this.table = attachTable(container, () => this.tableModel(), this.spec.tableLabel);
      this.draw();
    }

    get xs() { return this.spec.x || []; }

    render() {
      const s = this.spec;
      const node = this.svgNode;
      const xs = this.xs;
      const rows = s.rows || [];
      const max = Math.max(1, s.max || 0, ...rows.flatMap(row => row.values.filter(isNum)));
      this.max = max;
      const width = Math.max(220, this.lastWidth || Math.round(this.frame.clientWidth) || 560);
      const rowH = s.rowHeight || 20;
      const labelW = Math.max(...rows.map(row => row.label.length), 4) * 6.6 + 14;
      const margin = { top: 6, right: 10, bottom: 24, left: labelW };
      const height = margin.top + rows.length * rowH + margin.bottom;
      const plotW = Math.max(40, width - margin.left - margin.right);
      const cellW = plotW / Math.max(1, xs.length);
      Object.assign(this, { margin, width, height, cellW, rowH });
      node.textContent = "";
      node.setAttribute("viewBox", `0 0 ${width} ${height}`);
      node.setAttribute("width", String(width));
      node.setAttribute("height", String(height));
      node.setAttribute("aria-label", s.label || "Heat strip");
      legend(this.legendNode, []);
      this.legendNode.hidden = false;
      this.legendNode.textContent = "";
      for (let level = 0; level <= max; level += 1) {
        const item = html("span", "viz-legend-item", this.legendNode);
        const key = html("i", "viz-key box", item);
        key.style.background = levelColor(level, max);
        html("span", "", item, s.levelLabel ? s.levelLabel(level) : `Level ${level}`);
      }
      const layer = svg("g", { "aria-hidden": "true" }, node);
      rows.forEach((row, r) => {
        const y = margin.top + r * rowH;
        svgText({ x: margin.left - 8, y: y + rowH / 2 + 4, class: "viz-tick viz-row-label", "text-anchor": "end" }, row.label, layer);
        row.values.forEach((value, i) => {
          if (i >= xs.length) return;
          const v = isNum(value) ? value : 0;
          const cell = svg("rect", { x: (margin.left + i * cellW + 0.5).toFixed(1), y: y + 1, width: Math.max(0.5, cellW - 1).toFixed(1), height: rowH - 2, rx: Math.min(2, cellW / 3).toFixed(1), class: "viz-cell" }, layer);
          cell.style.fill = levelColor(v, max);
        });
      });
      const span = xs.length;
      const room = Math.max(2, Math.floor(plotW / 46));
      const step = [1, 2, 4, 5, 10, 13, 26, 52].find(st => span / st <= room) || Math.ceil(span / room);
      xs.forEach((x, i) => {
        if (x % step !== 0) return;
        svgText({ x: margin.left + (i + 0.5) * cellW, y: height - 8, class: "viz-tick", "text-anchor": "middle" }, String(x), layer);
      });
      this.cross = svg("rect", { y: margin.top - 2, height: rows.length * rowH + 4, width: Math.max(2, cellW), rx: 2, class: "viz-colmark", visibility: "hidden" }, node);
      if (this.index !== null && this.index < xs.length) this.mark(this.index);
    }

    count() { return this.xs.length; }

    locate(x) {
      if (x < this.margin.left - 4 || x > this.width - this.margin.right + 4) return null;
      const index = Math.floor((x - this.margin.left) / this.cellW);
      return Math.max(0, Math.min(this.xs.length - 1, index));
    }

    anchor(index) { return { x: this.margin.left + (index + 0.5) * this.cellW, y: this.margin.top + 20 }; }
    key(index) { return this.xs[index]; }

    echo(value) {
      const index = value === null || value === undefined ? -1 : this.xs.indexOf(value);
      if (index < 0) this.unmark();
      else this.mark(index);
      this.tip.hide();
    }

    mark(index) {
      if (!this.cross) return;
      this.cross.setAttribute("x", (this.margin.left + index * this.cellW).toFixed(1));
      this.cross.setAttribute("visibility", "visible");
    }

    unmark() { if (this.cross) this.cross.setAttribute("visibility", "hidden"); }

    model(index) {
      const s = this.spec;
      const x = this.xs[index];
      return {
        title: s.xTitle ? s.xTitle(x, index) : `Week ${x}`,
        rows: (s.rows || []).map(row => {
          const v = row.values[index];
          return { color: levelColor(v, this.max), key: "box", value: s.format ? s.format(v) : String(isNum(v) ? v : 0), label: row.label };
        }),
        notes: s.notes ? (s.notes(x, index) || []) : [],
      };
    }

    tableModel() {
      const s = this.spec;
      const rows = s.rows || [];
      return {
        columns: [s.xName || "Week", ...rows.map(row => row.label)],
        rows: this.xs.map((x, i) => [String(x), ...rows.map(row => (s.format ? s.format(row.values[i]) : String(isNum(row.values[i]) ? row.values[i] : 0)))]),
      };
    }
  }

  // ------------------------------------------------------------------ force layout
  function forceLayout(nodes, links, options) {
    const o = options || {};
    const count = nodes.length;
    const index = new Map(nodes.map((node, i) => [node.id, i]));
    const groupOf = o.groupOf || (() => "");
    const groupNames = [...new Set(nodes.map(groupOf))];
    const pos = nodes.map(node => {
      const g = groupNames.indexOf(groupOf(node));
      const angle = (g / Math.max(1, groupNames.length)) * Math.PI * 2 + 0.6;
      return {
        x: Math.cos(angle) * 0.55 + (hash(node.id + "x") - 0.5) * 0.5,
        y: Math.sin(angle) * 0.55 + (hash(node.id + "y") - 0.5) * 0.5,
      };
    });
    const edges = links.map(link => [index.get(link.source), index.get(link.target), Math.max(0, Math.min(1, link.strength || 0))])
      .filter(edge => edge[0] !== undefined && edge[1] !== undefined && edge[0] !== edge[1]);
    const k = 0.9 / Math.sqrt(Math.max(1, count));
    const iterations = o.iterations || 420;
    let temperature = 0.16;
    for (let step = 0; step < iterations; step += 1) {
      const dx = new Float64Array(count);
      const dy = new Float64Array(count);
      for (let i = 0; i < count; i += 1) {
        for (let j = i + 1; j < count; j += 1) {
          let ex = pos[i].x - pos[j].x;
          let ey = pos[i].y - pos[j].y;
          let d = Math.hypot(ex, ey);
          if (d < 1e-4) {
            ex = hash(nodes[i].id + nodes[j].id) - 0.5;
            ey = 0.3;
            d = Math.hypot(ex, ey);
          }
          const force = (k * k) / d;
          dx[i] += (ex / d) * force;
          dy[i] += (ey / d) * force;
          dx[j] -= (ex / d) * force;
          dy[j] -= (ey / d) * force;
        }
      }
      edges.forEach(([a, b, strength]) => {
        const ex = pos[a].x - pos[b].x;
        const ey = pos[a].y - pos[b].y;
        const d = Math.max(1e-4, Math.hypot(ex, ey));
        const force = ((d * d) / k) * (0.25 + strength);
        dx[a] -= (ex / d) * force;
        dy[a] -= (ey / d) * force;
        dx[b] += (ex / d) * force;
        dy[b] += (ey / d) * force;
      });
      const centroids = new Map();
      nodes.forEach((node, i) => {
        const g = groupOf(node);
        const c = centroids.get(g) || { x: 0, y: 0, n: 0 };
        c.x += pos[i].x;
        c.y += pos[i].y;
        c.n += 1;
        centroids.set(g, c);
      });
      nodes.forEach((node, i) => {
        const c = centroids.get(groupOf(node));
        dx[i] += (c.x / c.n - pos[i].x) * 0.35 - pos[i].x * 0.45;
        dy[i] += (c.y / c.n - pos[i].y) * 0.35 - pos[i].y * 0.45;
      });
      for (let i = 0; i < count; i += 1) {
        const length = Math.hypot(dx[i], dy[i]);
        if (length > 0) {
          const move = Math.min(length, temperature);
          pos[i].x += (dx[i] / length) * move;
          pos[i].y += (dy[i] / length) * move;
        }
      }
      temperature = Math.max(0.002, temperature * 0.985);
    }
    const xs = pos.map(p => p.x);
    const ys = pos.map(p => p.y);
    const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
    const spanX = Math.max(1e-6, maxX - minX), spanY = Math.max(1e-6, maxY - minY);
    const out = new Map();
    nodes.forEach((node, i) => out.set(node.id, { x: (pos[i].x - minX) / spanX, y: (pos[i].y - minY) / spanY }));
    return out;
  }

  // ------------------------------------------------------------------ network
  class Network extends Interactive {
    constructor(container, spec) {
      super(container, spec, "network");
      this.draw();
    }

    render() {
      const s = this.spec;
      const node = this.svgNode;
      const width = Math.max(220, this.lastWidth || Math.round(this.frame.clientWidth) || 420);
      const height = s.height || Math.round(Math.min(460, Math.max(260, width * 0.78)));
      const pad = { x: 34, y: 26 };
      node.textContent = "";
      node.setAttribute("viewBox", `0 0 ${width} ${height}`);
      node.setAttribute("width", String(width));
      node.setAttribute("height", String(height));
      node.setAttribute("aria-label", s.label || "Social network");
      const positions = s.positions || new Map();
      const nodes = (s.nodes || []).filter(n => positions.has(n.id)).slice().sort((a, b) => String(a.name).localeCompare(String(b.name)));
      const at = id => {
        const p = positions.get(id);
        return p ? { x: pad.x + p.x * (width - pad.x * 2), y: pad.y + p.y * (height - pad.y * 2) } : null;
      };
      this.nodes = nodes.map(n => Object.assign({}, n, at(n.id), { r: 4 + 1.05 * Math.max(0, Math.min(10, n.wellbeing || 0)) }));
      const byId = new Map(this.nodes.map(n => [n.id, n]));
      const links = (s.links || []).filter(l => byId.has(l.source) && byId.has(l.target));
      this.degree = new Map();
      links.forEach(l => {
        if (l.strength >= 0.3) {
          this.degree.set(l.source, (this.degree.get(l.source) || 0) + 1);
          this.degree.set(l.target, (this.degree.get(l.target) || 0) + 1);
        }
      });
      const linkLayer = svg("g", { class: "net-links", "aria-hidden": "true" }, node);
      this.linkNodes = links.map(l => {
        const a = byId.get(l.source), b = byId.get(l.target);
        const line = svg("line", { x1: a.x.toFixed(1), y1: a.y.toFixed(1), x2: b.x.toFixed(1), y2: b.y.toFixed(1), class: "net-link" }, linkLayer);
        line.style.strokeWidth = (0.6 + 2.8 * l.strength).toFixed(2);
        line.style.strokeOpacity = (0.1 + 0.55 * l.strength).toFixed(2);
        return { line, link: l };
      });
      const nodeLayer = svg("g", { class: "net-nodes", "aria-hidden": "true" }, node);
      this.nodeShapes = this.nodes.map(n => {
        const group = svg("g", { class: "net-node is-" + (n.status || "present") }, nodeLayer);
        if (n.newcomer) svg("circle", { cx: n.x.toFixed(1), cy: n.y.toFixed(1), r: (n.r + 3.5).toFixed(1), class: "net-ring" }, group);
        const circle = svg("circle", { cx: n.x.toFixed(1), cy: n.y.toFixed(1), r: n.r.toFixed(1), class: "net-dot" }, group);
        if (n.status === "present") circle.style.fill = safeColor(s.colorOf ? s.colorOf(n) : null, palette.neutral);
        if (n.status === "dead") {
          const d = n.r * 0.5;
          svg("path", { d: `M${(n.x - d).toFixed(1)},${(n.y - d).toFixed(1)}L${(n.x + d).toFixed(1)},${(n.y + d).toFixed(1)}M${(n.x + d).toFixed(1)},${(n.y - d).toFixed(1)}L${(n.x - d).toFixed(1)},${(n.y + d).toFixed(1)}`, class: "net-cross" }, group);
        }
        if (s.labels !== false) {
          const right = n.x < width - 70;
          svgText({ x: (right ? n.x + n.r + 4 : n.x - n.r - 4).toFixed(1), y: (n.y + 3.5).toFixed(1), class: "net-label", "text-anchor": right ? "start" : "end" }, String(n.name || n.id).split(" ")[0], group);
        }
        return group;
      });
      if (this.index !== null && this.index < this.nodes.length) this.mark(this.index);
    }

    defaultIndex() { return 0; }
    count() { return (this.nodes || []).length; }

    locate(x, y) {
      let best = null;
      let distance = Infinity;
      (this.nodes || []).forEach((n, i) => {
        const d = Math.hypot(n.x - x, n.y - y) - n.r;
        if (d < distance) {
          distance = d;
          best = i;
        }
      });
      return distance <= (this.touchMode ? 22 : 12) ? best : null;
    }

    anchor(index) {
      const n = this.nodes[index];
      return { x: n.x + n.r, y: n.y };
    }

    mark(index) {
      const n = this.nodes[index];
      if (!n) return;
      this.svgNode.classList.add("is-focusing");
      this.nodeShapes.forEach((shape, i) => shape.classList.toggle("is-focus", i === index));
      this.linkNodes.forEach(({ line, link }) => line.classList.toggle("is-focus", link.source === n.id || link.target === n.id));
    }

    unmark() {
      this.svgNode.classList.remove("is-focusing");
      (this.nodeShapes || []).forEach(shape => shape.classList.remove("is-focus"));
      (this.linkNodes || []).forEach(({ line }) => line.classList.remove("is-focus"));
    }

    model(index) {
      const n = this.nodes[index];
      const s = this.spec;
      const color = n.status === "present" ? safeColor(s.colorOf ? s.colorOf(n) : null, palette.neutral) : palette.neutral;
      const ties = (s.links || []).filter(l => (l.source === n.id || l.target === n.id) && l.strength >= 0.3).length;
      return {
        title: n.name || n.id,
        subtitle: s.describe ? s.describe(n) : "",
        rows: [
          { color, key: "box", value: decimal(n.wellbeing, 1), label: "wellbeing (0–10)" },
          { value: decimal(n.trust, 2), label: "trust in the steward" },
          { value: String(ties), label: "close ties (≥ 0.3)" },
        ],
      };
    }
  }

  // ------------------------------------------------------------------ ladder
  class Ladder extends Interactive {
    constructor(container, spec) {
      super(container, spec, "ladder");
      this.draw();
    }

    render() {
      const s = this.spec;
      const node = this.svgNode;
      const marks = (s.marks || []).filter(m => isNum(m.value));
      const width = Math.max(260, this.lastWidth || Math.round(this.frame.clientWidth) || 600);
      const max = Math.max(1, ...marks.map(m => m.value)) * 1.08;
      const axisTicks = ticks(0, max, Math.max(2, Math.floor(width / 120)));
      const tickStep = axisTicks.length > 1 ? axisTicks[1] - axisTicks[0] : max;
      while (axisTicks[axisTicks.length - 1] < max) axisTicks.push(+(axisTicks[axisTicks.length - 1] + tickStep).toPrecision(12));
      const top = axisTicks[axisTicks.length - 1] || max;
      const margin = { left: 16, right: 46 };
      const plotW = width - margin.left - margin.right;
      const px = v => margin.left + (v / top) * plotW;
      const refs = marks.filter(m => m.kind !== "model" && m.kind !== "ghost").sort((a, b) => a.value - b.value);
      const rowsUsed = [];
      refs.forEach(m => {
        const x = px(m.value);
        const w = Math.max(m.label.length, (m.valueText || "").length) * 6.4 + 10;
        let row = 0;
        while (rowsUsed[row] !== undefined && rowsUsed[row] > x - w / 2 - 4) row += 1;
        rowsUsed[row] = x + w / 2;
        m.row = row;
      });
      const axisY = 58;
      const height = axisY + 22 + Math.max(1, rowsUsed.length) * 30 + 6;
      this.marks = marks;
      this.px = px;
      this.axisY = axisY;
      this.width = width;
      node.textContent = "";
      node.setAttribute("viewBox", `0 0 ${width} ${height}`);
      node.setAttribute("width", String(width));
      node.setAttribute("height", String(height));
      node.setAttribute("aria-label", s.label || "Reference ladder");
      const g = svg("g", { "aria-hidden": "true" }, node);
      svg("line", { x1: margin.left, x2: width - margin.right + 18, y1: axisY, y2: axisY, class: "ladder-axis" }, g);
      svg("path", { d: `M${width - margin.right + 18},${axisY - 4}L${width - margin.right + 26},${axisY}L${width - margin.right + 18},${axisY + 4}Z`, class: "ladder-arrow" }, g);
      svgText({ x: width - 2, y: axisY - 10, class: "viz-axis-label", "text-anchor": "end" }, "unbounded", g);
      axisTicks.forEach(t => {
        const x = px(t);
        svg("line", { x1: x, x2: x, y1: axisY - 3, y2: axisY + 3, class: "ladder-tick" }, g);
        svgText({ x, y: axisY + 15, class: "viz-tick", "text-anchor": "middle" }, compact(t), g);
      });
      refs.forEach(m => {
        const x = px(m.value);
        const y = axisY + 34 + m.row * 30;
        svg("line", { x1: x, x2: x, y1: axisY, y2: y - 10, class: "ladder-leader" }, g);
        const dot = svg("circle", { cx: x, cy: axisY, r: 4.5, class: "ladder-ref" }, g);
        dot.style.fill = safeColor(m.color, palette.neutral);
        svgText({ x, y, class: "ladder-label", "text-anchor": "middle" }, m.label, g);
        svgText({ x, y: y + 13, class: "ladder-value", "text-anchor": "middle" }, m.valueText || compact(m.value), g);
      });
      marks.filter(m => m.kind === "ghost").forEach(m => {
        const x = px(m.value);
        svg("circle", { cx: x, cy: axisY, r: 8, class: "ladder-ghost" }, g);
        svgText({ x, y: axisY - 16, class: "ladder-ghost-label", "text-anchor": x > width - 120 ? "end" : "middle" }, m.label, g);
      });
      marks.filter(m => m.kind === "model").forEach(m => {
        const x = px(m.value);
        svg("line", { x1: x, x2: x, y1: axisY - 30, y2: axisY, class: "ladder-stem" + (m.collapsed ? " is-collapse" : "") }, g);
        svg("path", { d: `M${x},${axisY - 9}L${x + 9},${axisY}L${x},${axisY + 9}L${x - 9},${axisY}Z`, class: "ladder-model" + (m.collapsed ? " is-collapse" : "") }, g);
        const anchor = x < 80 ? "start" : x > width - 80 ? "end" : "middle";
        svgText({ x: anchor === "start" ? x - 6 : anchor === "end" ? x + 6 : x, y: axisY - 36, class: "ladder-model-label", "text-anchor": anchor }, `${m.label} · ${m.valueText || compact(m.value)}`, g);
      });
    }

    count() { return (this.marks || []).length; }
    defaultIndex() { return Math.max(0, (this.marks || []).findIndex(m => m.kind === "model")); }

    locate(x) {
      let best = null;
      let distance = Infinity;
      (this.marks || []).forEach((m, i) => {
        const d = Math.abs(this.px(m.value) - x);
        if (d < distance) {
          distance = d;
          best = i;
        }
      });
      return distance <= (this.touchMode ? 24 : 14) ? best : null;
    }

    anchor(index) { return { x: this.px(this.marks[index].value), y: this.axisY }; }
    mark() {}
    unmark() {}

    model(index) {
      const m = this.marks[index];
      return { title: m.label, rows: [{ color: m.kind === "model" ? palette.accent : safeColor(m.color, palette.neutral), key: "box", value: m.valueText || number(m.value), label: "points" }], notes: m.note ? [m.note] : [] };
    }
  }

  // ------------------------------------------------------------------ composition bar (HTML)
  function compositionBar(container, spec) {
    const s = spec || {};
    container.textContent = "";
    container.classList.add("viz", "viz-composition");
    const frame = html("div", "viz-frame", container);
    const bar = html("div", "comp-bar" + (s.muted ? " is-muted" : ""), frame);
    bar.setAttribute("role", "group");
    bar.setAttribute("aria-label", s.label || "Composition");
    const tip = new Tip(frame);
    const segments = (s.segments || []).filter(seg => isNum(seg.value) && seg.value > 0);
    const pin = { contains: target => bar.contains(target), clear: () => tip.hide() };
    const total = segments.reduce((sum, seg) => sum + seg.value, 0) || 1;
    const fmt = s.format || compact;
    segments.forEach(seg => {
      const share = seg.value / total;
      const item = html("button", "comp-seg", bar);
      item.type = "button";
      item.style.flexGrow = String(share);
      item.style.background = safeColor(seg.color, palette.neutral);
      item.style.color = inkFor(seg.color);
      item.setAttribute("aria-label", `${seg.label}: ${fmt(seg.value)} (${percent(share, 1)})`);
      const text = html("span", "comp-label", item, seg.short || seg.label);
      text.setAttribute("aria-hidden", "true");
      const show = () => {
        tip.fill({ title: seg.label, rows: [{ color: safeColor(seg.color), key: "box", value: fmt(seg.value), label: s.unit || "" }, { value: percent(share, 1), label: "of the total" }], notes: seg.note ? [seg.note] : [] });
        const fr = frame.getBoundingClientRect();
        const r = item.getBoundingClientRect();
        tip.place(r.left - fr.left + r.width / 2, r.top - fr.top + r.height + 30);
      };
      item.addEventListener("pointerenter", event => { if (!isTouch(event)) show(); });
      // Touch: a tap shows the segment and keeps it shown until the next tap elsewhere.
      item.addEventListener("pointerdown", event => { if (isTouch(event)) { show(); pinned.add(pin); } });
      item.addEventListener("focus", show);
      item.addEventListener("pointerleave", event => { if (!isTouch(event)) tip.hide(); });
      item.addEventListener("blur", () => tip.hide());
      if (s.onSelect) item.addEventListener("click", () => s.onSelect(seg));
    });
    const fit = () => bar.querySelectorAll(".comp-seg").forEach(item => {
      const label = item.querySelector(".comp-label");
      label.hidden = false;
      if (label.scrollWidth + 10 > item.clientWidth) label.hidden = true;
    });
    requestAnimationFrame(fit);
    if (resizer) {
      const holder = { root: container, resized: fit };
      observed.set(frame, holder);
      resizer.observe(frame);
    }
    return { fit };
  }

  // ------------------------------------------------------------------ sparkline (decorative)
  function sparkline(container, spec) {
    const s = spec || {};
    const values = (s.values || []).map(v => (isNum(v) ? v : null));
    const width = s.width || 120;
    const height = s.height || 28;
    container.textContent = "";
    const node = svg("svg", { viewBox: `0 0 ${width} ${height}`, class: "sparkline", "aria-hidden": "true", preserveAspectRatio: "none" }, container);
    const finite = values.filter(isNum);
    if (finite.length < 2) return node;
    const lo = Math.min(0, ...finite);
    const hi = Math.max(...finite, lo + 1e-9);
    const px = i => 1 + (i / Math.max(1, values.length - 1)) * (width - 2);
    const py = v => height - 2 - ((v - lo) / (hi - lo || 1)) * (height - 4);
    const points = values.map((v, i) => (isNum(v) ? [px(i), py(v)] : null)).filter(Boolean);
    const color = safeColor(s.color, palette.accent);
    const area = svg("path", { d: pathOf(points) + `V${height}H${points[0][0].toFixed(1)}Z`, class: "spark-area" }, node);
    area.style.fill = color;
    const line = svg("path", { d: pathOf(points), class: "spark-line" }, node);
    line.style.stroke = color;
    return node;
  }

  window.SocietyViz = Object.freeze({
    palette,
    format,
    svg,
    svgText,
    safeColor,
    inkFor,
    isNum,
    ticks,
    hash,
    prefersReducedMotion,
    timeChart: (container, spec) => new TimeChart(container, spec),
    heatStrip: (container, spec) => new HeatStrip(container, spec),
    network: (container, spec) => new Network(container, spec),
    ladder: (container, spec) => new Ladder(container, spec),
    forceLayout,
    compositionBar,
    sparkline,
  });
})();
