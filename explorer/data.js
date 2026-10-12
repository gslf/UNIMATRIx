"use strict";
/*
 * UNIMATRIx Explorer · data layer.
 *
 * Reads the Explorer's xCDN files with the official library (window.xcdn, vendor/xcdn.js) and turns
 * the parsed documents into plain, JSON-friendly JavaScript:
 *   - integers (BigInt in xCDN) become Numbers; exact decimals d"…" become Numbers and keep their
 *     original text in a hidden `$decimals` map; timestamps t"…" become ISO strings; durations r"…"
 *     stay ISO-8601 strings;
 *   - semantic tags (#run, #week, #decision, #collapse, …) are kept in a hidden `$tag` property;
 *   - tables annotated @columns("id", "status", …) become arrays of objects keyed by those columns.
 *
 * It never talks to the UNIMATRIx application: over http(s) it fetches data/index.xcdn and the run
 * files lazily; from disk (file://), or whenever the visitor wants, it reads files picked or dropped.
 */
(function () {
  const FORMATS = {
    index: "unimatrix.explorer.index/1",
    run: "unimatrix.explorer.run/1",
    entry: "unimatrix.explorer.entry/1",
  };
  const DOMAINS = ["D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8"];
  const RESIDENT_COLUMNS = ["id", "status", "activity", "wb", "st", "lo", "gr", "tr", "lg", "hp", "coins", "food",
    "protest", "sick", "hungry", "concern", "intensity", "task"];
  const REFERENCE_KEYS = ["passive", "naive", "expert", "oracle"];
  const INDEX_URL = "data/index.xcdn";

  /** A friendly, user-facing loading error (file name, parser message, line and column when known). */
  class DataError extends Error {
    constructor(message, detail) {
      super(message);
      this.name = "DataError";
      Object.assign(this, detail || {});
    }
  }

  const lib = () => {
    if (!window.xcdn || typeof window.xcdn.parse !== "function") throw new DataError("The xCDN library (vendor/xcdn.js) did not load.");
    return window.xcdn;
  };

  // ---------------------------------------------------------------------------------- conversion
  function hidden(obj, key, value) {
    Object.defineProperty(obj, key, { value, enumerable: false, configurable: true, writable: true });
  }
  function bigToNumber(value) {
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  /** Plain JS for a raw xCDN value (XObject, XArray, Int, Decimal, …). */
  function plainValue(value) {
    const X = lib();
    if (value === null || value === undefined) return null;
    if (value instanceof X.Node) return plainNode(value);
    if (value instanceof X.XObject) {
      const out = {};
      let decimals = null;
      for (const [key, node] of value.value.entries()) {
        out[key] = plainNode(node);
        if (node && node.value instanceof X.DecimalValue) {
          if (!decimals) decimals = {};
          decimals[key] = node.value.value;
        }
      }
      if (decimals) hidden(out, "$decimals", decimals);
      return out;
    }
    if (value instanceof X.XArray) return value.value.map(plainNode);
    if (value instanceof X.Int) return bigToNumber(value.value);
    if (value instanceof X.Float) return Number.isFinite(value.value) ? value.value : null;
    if (value instanceof X.DecimalValue) {
      const n = Number(value.value);
      return Number.isFinite(n) ? n : null;
    }
    if (value instanceof X.Bool) return Boolean(value.value);
    if (value instanceof X.Null) return null;
    if (value instanceof X.XString) return value.value;
    if (value instanceof X.DateTime) {
      const d = value.value;
      return d instanceof Date && Number.isFinite(d.getTime()) ? d.toISOString() : null;
    }
    if (value instanceof X.Duration || value instanceof X.Uuid) return String(value.value);
    if (value instanceof X.Bytes) return Array.from(value.value || []);
    if (typeof value === "bigint") return bigToNumber(value);
    if (value instanceof Date) return value.toISOString();
    return value;
  }

  /** Plain JS for a decorated node: the first tag is kept as `$tag`, @columns tables become objects. */
  function plainNode(node) {
    const X = lib();
    if (!(node instanceof X.Node)) return plainValue(node);
    const columns = (node.annotations || []).find((a) => a.name === "columns");
    let out;
    if (columns && node.value instanceof X.XArray) {
      const names = columns.args.map((arg) => String(plainValue(arg)));
      out = node.value.value.map((rowNode) => {
        const row = plainNode(rowNode);
        if (!Array.isArray(row)) return row;
        const obj = {};
        names.forEach((name, i) => { obj[name] = i < row.length ? row[i] : null; });
        return obj;
      });
    } else {
      out = plainValue(node.value);
    }
    if (node.tags && node.tags.length && out && typeof out === "object") hidden(out, "$tag", node.tags[0].name);
    return out;
  }

  const decimalText = (obj, key) => (obj && obj.$decimals ? obj.$decimals[key] : undefined);
  const nodeHasTag = (node, name) => Boolean(node && node.tags && node.tags.some((t) => t.name === name));

  // ---------------------------------------------------------------------------------- parsing
  function baseName(path) {
    const parts = String(path || "").split(/[\\/]/);
    return parts[parts.length - 1] || String(path || "");
  }

  /** Parses xCDN text; wraps parser failures in a DataError with the line and column. */
  function parseText(text, name) {
    const label = baseName(name) || "file";
    let doc;
    try {
      doc = lib().parse(String(text));
    } catch (error) {
      if (error instanceof DataError) throw error;
      const span = error && error.span;
      const raw = String((error && error.message) || error).replace(/\s+at \d+:\d+$/, "");
      const where = span ? ` (line ${span.line}, column ${span.column})` : "";
      throw new DataError(`${label} is not valid xCDN: ${raw}${where}.`, {
        file: label, line: span ? span.line : null, column: span ? span.column : null, parser: raw,
      });
    }
    const directives = {};
    for (const d of doc.prolog || []) directives[d.name] = plainNode(d.value);
    const format = typeof directives.format === "string" ? directives.format : null;
    return { doc, directives, format, name: label };
  }

  function expectFormat(parsed, wanted) {
    const known = Object.values(FORMATS);
    if (!parsed.format) {
      throw new DataError(`${parsed.name} has no $format directive: it is not a UNIMATRIx Explorer file.`, { file: parsed.name });
    }
    if (!known.includes(parsed.format)) {
      throw new DataError(`${parsed.name} has an unknown $format "${parsed.format}". This Explorer reads ${known.join(", ")}.`, { file: parsed.name });
    }
    if (wanted && parsed.format !== wanted) {
      throw new DataError(`${parsed.name} is "${parsed.format}", expected "${wanted}".`, { file: parsed.name });
    }
  }

  /** Cheap sniff of the $format directive without parsing the whole file. */
  function sniffFormat(text) {
    const m = /\$format\s*:\s*"([^"\\]*)"/.exec(String(text).slice(0, 4096));
    return m ? m[1] : null;
  }

  // ---------------------------------------------------------------------------------- normalisation
  const isNum = (v) => typeof v === "number" && Number.isFinite(v);
  function num(v) {
    if (isNum(v)) return v;
    if (v === null || v === undefined || v === "" || typeof v === "boolean") return null;
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }
  const str = (v, fallback = "") => (v === null || v === undefined ? fallback : String(v));
  const arr = (v) => (Array.isArray(v) ? v : []);
  const obj = (v) => (v && typeof v === "object" && !Array.isArray(v) ? v : {});
  function numbers(o) {
    const out = {};
    for (const [k, v] of Object.entries(obj(o))) { const n = num(v); if (n !== null) out[k] = n; }
    return out;
  }
  function collapseOf(c) {
    if (!c || typeof c !== "object") return null;
    return { week: num(c.week), cause: str(c.cause), label: str(c.label, "Collapse") };
  }
  const refKey = (model) => str(model).toLowerCase().replace(/\s*steward$/, "").trim();

  function normalizeEntry(e, source) {
    const domains = {};
    const raw = obj(e.domains);
    for (const d of DOMAINS) domains[d] = num(raw[d]) || 0;
    const references = {};
    for (const [k, v] of Object.entries(obj(e.references))) { const n = num(v); if (n !== null) references[k] = n; }
    const v = obj(e.vitals);
    const out = {
      id: str(e.run),
      file: str(e.file) || `runs/${str(e.run)}.xcdn`,
      kind: e.kind === "reference" ? "reference" : "model",
      model: str(e.model, "Unknown model") || "Unknown model",
      snapshot: str(e.snapshot),
      recipe: str(e.recipe, "unknown") || "unknown",
      recipeName: str(e.recipe_name) || str(e.recipe, "Unknown recipe"),
      revision: str(e.revision),
      created: e.created ? str(e.created) : null,
      total: num(e.total) || 0,
      totalText: decimalText(e, "total") || null,
      relative: num(e.relative_to_expert),
      collapsed: num(e.collapsed) || 0,
      seeds: Math.max(1, num(e.seeds) || 1),
      collapse: collapseOf(e.collapse),
      wellby: num(e.wellby),
      domains,
      vitals: { population: num(v.population), wellbeing: num(v.wellbeing), trust: num(v.trust), commons: num(v.commons), gini: num(v.gini), deaths: num(v.deaths) },
      references,
      source: source || "index",
    };
    out.isRef = out.kind === "reference";
    out.refKey = out.isRef ? refKey(out.model) : null;
    out.fullyCollapsed = out.collapsed > 0 && out.collapsed >= out.seeds;
    if (out.relative === null && references.expert > 0) out.relative = out.total / references.expert;
    return out;
  }

  function residentOf(row) {
    let r = row;
    if (Array.isArray(r)) {
      const o = {};
      RESIDENT_COLUMNS.forEach((name, i) => { o[name] = r[i]; });
      r = o;
    }
    r = obj(r);
    const kind = r.concern && typeof r.concern === "object" ? r.concern.kind : r.concern;
    const intensity = r.concern && typeof r.concern === "object" ? r.concern.intensity : r.intensity;
    return {
      id: str(r.id), status: str(r.status, "present"), activity: str(r.activity, "rest"),
      wb: num(r.wb), st: num(r.st), lo: num(r.lo), gr: num(r.gr), tr: num(r.tr), lg: num(r.lg), hp: num(r.hp),
      coins: num(r.coins), food: num(r.food),
      protest: Boolean(r.protest), sick: Boolean(r.sick), hungry: Boolean(r.hungry),
      concern: kind ? { kind: str(kind), intensity: num(intensity) } : null,
      task: str(r.task, "work"),
    };
  }
  function actionOf(a) {
    a = obj(a);
    const status = str(a.status, "done");
    return {
      verb: str(a.verb, "action"), status, ok: status === "done" || status === "applied" || status === "ok",
      targets: arr(a.targets).map((t) => str(t)),
      place: a.place ? str(a.place) : null, say: a.say ? str(a.say) : null, text: a.text ? str(a.text) : "",
      label: a.label ? str(a.label) : "", params: obj(a.params),
    };
  }
  /** What the model read, answered and reasoned in one week (model runs; absent for week 0 and references). */
  function decisionOf(d) {
    if (!d || typeof d !== "object") return null;
    const text = (v) => (v === null || v === undefined || v === "" ? null : String(v));
    return {
      briefing: text(d.briefing),
      answer: text(d.answer),
      reasoning: text(d.reasoning),
      error: text(d.error),
      inputTokens: num(d.input_tokens),
      generatedTokens: num(d.generated_tokens),
      reasoningTokens: num(d.reasoning_tokens),
      finishReason: text(d.finish_reason),
      latency: num(d.latency),
      latencyText: decimalText(d, "latency") || null,
      retries: num(d.retries),
    };
  }
  function frameOf(w) {
    const commons = obj(w.commons);
    const steward = obj(w.steward);
    return {
      seed: num(w.seed),
      week: num(w.week) || 0,
      season: w.season ? str(w.season) : null,
      metrics: numbers(w.metrics),
      points: numbers(w.points),
      stores: numbers(w.stores),
      commons: { stock: num(commons.stock), harvest: num(commons.harvest) },
      skills: numbers(w.skills),
      beliefs: obj(w.beliefs),
      conventions: numbers(w.conventions),
      projects: obj(w.projects),
      conflicts: obj(w.conflicts),
      story: arr(w.story).map((s) => str(s)),
      residents: arr(w.residents).map(residentOf).filter((r) => r.id),
      ties: arr(w.ties).filter((t) => Array.isArray(t) && t.length >= 3).map((t) => [str(t[0]), str(t[1]), num(t[2]) || 0]),
      events: arr(w.events).map((e) => str(e)).filter(Boolean),
      actions: arr(w.actions).map(actionOf),
      steward: { note: steward.note ? str(steward.note) : null, forecasts: obj(steward.forecasts), error: steward.error ? str(steward.error) : null },
      decision: decisionOf(w.decision),
      collapsed: collapseOf(w.collapsed),
    };
  }
  function seedOf(s) {
    s = obj(s);
    return {
      seed: num(s.seed) || 1,
      total: num(s.total) || 0,
      earned: num(s.earned) || 0,
      forfeited: num(s.forfeited) || 0,
      survivedWeeks: num(s.survived_weeks),
      collapsed: collapseOf(s.collapsed),
      wellby: num(s.wellby),
      events: numbers(s.events),
      narrative: str(s.narrative),
      diagnosis: arr(s.diagnosis).map((f) => ({ level: str(obj(f).level, "watch"), title: str(obj(f).title), text: str(obj(f).text), basis: str(obj(f).basis) })),
      path: arr(s.path).map((p) => ({ week: num(obj(p).week), source: str(obj(p).source), target: str(obj(p).target), label: str(obj(p).label) })),
      outcomes: arr(s.outcomes).map((o) => ({ node: str(obj(o).node), week: num(obj(o).week), type: str(obj(o).type), label: str(obj(o).label), reward: numbers(obj(o).reward) })),
      actions: numbers(s.actions),
    };
  }

  function normalizeRun(r, weeks) {
    const recipe = obj(r.recipe);
    const commons = obj(recipe.commons);
    const score = obj(r.score);
    const domains = {};
    for (const d of DOMAINS) {
      const dom = obj(obj(score.domains)[d]);
      domains[d] = { name: str(dom.name), unit: str(dom.unit), points: num(dom.points) || 0, units: num(dom.units), rate: num(dom.rate) };
    }
    const references = {};
    for (const [k, v] of Object.entries(obj(r.references))) { const n = num(v); if (n !== null) references[k] = n; }
    const seeds = arr(r.seeds).map(seedOf);
    const frames = new Map();
    for (const w of weeks) {
      const seed = w.seed !== null ? w.seed : seeds.length ? seeds[0].seed : 1;
      if (!frames.has(seed)) frames.set(seed, []);
      frames.get(seed).push(w);
    }
    for (const list of frames.values()) list.sort((a, b) => a.week - b.week);
    for (const seed of frames.keys()) if (!seeds.some((s) => s.seed === seed)) seeds.push(seedOf({ seed }));
    seeds.sort((a, b) => a.seed - b.seed);
    const run = {
      id: str(r.id),
      kind: r.kind === "reference" ? "reference" : "model",
      model: str(r.model, "Unknown model") || "Unknown model",
      snapshot: str(r.snapshot),
      modelId: str(r.model_id),
      status: str(r.status, "completed"),
      created: r.created ? str(r.created) : null,
      finished: r.finished ? str(r.finished) : null,
      duration: r.duration ? str(r.duration) : null,
      usage: numbers(r.usage),
      recipe: {
        id: str(recipe.id, "unknown"), name: str(recipe.name) || str(recipe.id, "Unknown recipe"), revision: str(recipe.revision),
        description: str(recipe.description), weeks: num(recipe.weeks), seeds: arr(recipe.seeds).map(num).filter(isNum),
        actionsPerWeek: num(recipe.actions_per_week), world: str(recipe.world, "Village"), startSeason: str(recipe.start_season, "spring"),
        seasonLength: num(recipe.season_length),
        commons: { name: str(commons.name, "the lake"), capacity: num(commons.capacity) || 1000, critical: num(commons.critical) },
        collapse: obj(recipe.collapse),
      },
      score: {
        total: num(score.total) || 0, totalText: decimalText(score, "total") || null, collapsedSeeds: num(score.collapsed_seeds) || 0,
        relative: num(score.relative_to_expert), wellby: num(score.wellby), domains,
      },
      references,
      factions: arr(r.factions).map((f) => ({ id: str(obj(f).id), name: str(obj(f).name), color: str(obj(f).color, "#c8c8c8") })),
      residents: arr(r.residents).map((x) => {
        x = obj(x);
        return {
          id: str(x.id), name: str(x.name) || str(x.id), age: num(x.age), role: str(x.role, "resident"), faction: str(x.faction),
          newcomer: Boolean(x.newcomer), archetype: str(x.archetype), bio: str(x.bio), status: str(x.status, "present"),
          ended: num(x.ended), joined: num(x.joined), wellbeing: num(x.wellbeing), trust: num(x.trust), skills: numbers(x.skills),
          concern: x.concern ? str(x.concern) : null,
        };
      }).filter((x) => x.id),
      story: {
        nodes: arr(obj(r.story).nodes).map((n) => ({ id: str(obj(n).id), title: str(obj(n).title), kind: str(obj(n).kind, "event"),
          x: num(obj(n).x), y: num(obj(n).y), outcome: obj(n).outcome ? str(obj(n).outcome) : null, label: obj(n).label ? str(obj(n).label) : null })),
        edges: arr(obj(r.story).edges).filter(Array.isArray).map((e) => [str(e[0]), str(e[1]), str(e[2])]),
      },
      seeds,
      frames,
    };
    if (run.score.relative === null && references.expert > 0) run.score.relative = run.score.total / references.expert;
    run.isRef = run.kind === "reference";
    run.hasDecisions = weeks.some((w) => w.decision);
    return run;
  }

  // ---------------------------------------------------------------------------------- documents
  function readIndex(parsed) {
    expectFormat(parsed, FORMATS.index);
    let header = null;
    const entries = [];
    const warnings = [];
    for (const node of parsed.doc.values) {
      if (nodeHasTag(node, "explorer")) header = plainNode(node);
      else if (nodeHasTag(node, "entry")) {
        const e = normalizeEntry(plainNode(node), "index");
        if (e.id) entries.push(e); else warnings.push(`${parsed.name}: an #entry without a run id was skipped.`);
      }
    }
    return { header: normalizeHeader(header || {}), entries, warnings };
  }
  function readEntryFile(parsed) {
    expectFormat(parsed, FORMATS.entry);
    const node = parsed.doc.values.find((n) => nodeHasTag(n, "entry"));
    if (!node) throw new DataError(`${parsed.name} has no #entry value.`, { file: parsed.name });
    const e = normalizeEntry(plainNode(node), "entry");
    if (!e.id) throw new DataError(`${parsed.name}: the #entry has no run id.`, { file: parsed.name });
    return e;
  }
  function normalizeHeader(h) {
    const domains = {};
    for (const [k, v] of Object.entries(obj(h.domains))) domains[k] = { name: str(obj(v).name), unit: str(obj(v).unit) };
    return { generated: h.generated ? str(h.generated) : null, runs: num(h.runs), domains };
  }
  function readRun(parsed) {
    expectFormat(parsed, FORMATS.run);
    const head = parsed.doc.values.find((n) => nodeHasTag(n, "run"));
    if (!head) throw new DataError(`${parsed.name} has no #run header.`, { file: parsed.name });
    const weeks = [];
    for (const node of parsed.doc.values) if (nodeHasTag(node, "week")) weeks.push(frameOf(plainNode(node)));
    const run = normalizeRun(plainNode(head), weeks);
    if (!run.id) throw new DataError(`${parsed.name}: the #run header has no id.`, { file: parsed.name });
    if (!weeks.length) throw new DataError(`${parsed.name} has no #week values to replay.`, { file: parsed.name });
    return run;
  }

  /** The leaderboard entry of a run file opened on its own (no index): built from its #run header. */
  function entryFromRun(run, file) {
    const first = run.seeds[0];
    const list = first ? run.frames.get(first.seed) || [] : [];
    const end = list.length ? list[list.length - 1].metrics : {};
    const collapses = run.seeds.filter((s) => s.collapsed);
    const domains = {};
    for (const d of DOMAINS) domains[d] = run.score.domains[d].points;
    const entry = normalizeEntry({
      run: run.id, file, kind: run.kind, model: run.model, snapshot: run.snapshot, recipe: run.recipe.id,
      recipe_name: run.recipe.name, revision: run.recipe.revision, created: run.created, total: run.score.total,
      relative_to_expert: run.score.relative, collapsed: collapses.length, seeds: run.seeds.length || 1,
      collapse: collapses.length ? collapses[0].collapsed : null, wellby: run.score.wellby, domains,
      vitals: { population: end.population, wellbeing: end.wellbeing, trust: end.trust, commons: end.commons, gini: end.gini,
        deaths: run.seeds.reduce((sum, s) => sum + ((s.events && s.events.deaths) || 0), 0) },
      references: run.references,
    }, "run");
    entry.totalText = run.score.totalText;
    return entry;
  }

  // ---------------------------------------------------------------------------------- store
  const listeners = new Set();
  const store = {
    status: "idle", // idle | loading | ready | empty
    source: { kind: "none", label: "No data", base: "" },
    header: null,
    entries: [],
    byId: new Map(),
    errors: [],
    files: null, // file mode: { byPath: Map, byBase: Map, indexDir }
    runs: new Map(), // id → Promise<run>
    version: 0,
  };
  let generation = 0; // a newer load (boot or files) makes older, slower ones stale
  function emit() { store.version += 1; for (const fn of listeners) { try { fn(store); } catch (e) { console.error(e); } } }
  function onChange(fn) { listeners.add(fn); return () => listeners.delete(fn); }

  function setEntries(entries, header, source) {
    const map = new Map();
    const rank = (x) => (x.source === "index" ? 3 : x.source === "entry" ? 2 : 1);
    for (const e of entries) {
      const prev = map.get(e.id);
      // index entries win over per-run entry files, which win over entries built from run headers
      if (!prev || rank(e) >= rank(prev)) map.set(e.id, e);
    }
    store.entries = [...map.values()].sort((a, b) => String(b.created || "").localeCompare(String(a.created || "")));
    store.byId = map;
    store.header = header || { generated: null, runs: store.entries.length, domains: {} };
    store.source = source;
    store.status = store.entries.length ? "ready" : "empty";
  }

  async function fetchText(url, onProgress) {
    const response = await fetch(url, { cache: "no-cache" });
    if (!response.ok) throw new DataError(`${baseName(url)} could not be loaded (HTTP ${response.status}).`, { status: response.status, file: baseName(url) });
    const total = Number(response.headers.get("content-length")) || 0;
    if (!onProgress || !response.body || typeof response.body.getReader !== "function") return response.text();
    const reader = response.body.getReader();
    const chunks = [];
    let received = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
      received += value.length;
      onProgress(total ? Math.min(1, received / total) : null, received);
    }
    const all = new Uint8Array(received);
    let at = 0;
    for (const c of chunks) { all.set(c, at); at += c.length; }
    onProgress(1, received);
    return new TextDecoder("utf-8").decode(all);
  }

  /** Boot: data/index.xcdn next to the page. On file:// nothing is fetched (browsers forbid it). */
  async function boot() {
    const gen = ++generation;
    store.status = "loading";
    emit();
    store.runs = new Map();
    store.files = null;
    if (!/^https?:$/.test(location.protocol)) {
      store.errors = [];
      store.source = { kind: "none", label: "No data", base: "", reason: "file" };
      store.status = "empty";
      emit();
      return;
    }
    try {
      const text = await fetchText(INDEX_URL);
      if (gen !== generation) return;
      const index = readIndex(parseText(text, INDEX_URL));
      setEntries(index.entries, index.header, { kind: "data", label: "data/", base: new URL(".", new URL(INDEX_URL, location.href)).href });
      store.errors = index.warnings;
      if (!index.entries.length) store.source.reason = "no-entries";
    } catch (error) {
      if (gen !== generation) return;
      // a missing data/ folder is expected before the first export; anything else is worth reporting
      const missing = error instanceof DataError && error.status === 404;
      store.errors = missing ? [] : [error.message || String(error)];
      store.source = { kind: "none", label: "No data", base: "", reason: missing ? "missing" : "error" };
      store.entries = [];
      store.byId = new Map();
      store.status = "empty";
    }
    emit();
  }

  // ---------------------------------------------------------------------------------- local files
  function normPath(path) {
    const out = [];
    for (const part of String(path || "").replace(/\\/g, "/").split("/")) {
      if (!part || part === ".") continue;
      if (part === "..") out.pop(); else out.push(part);
    }
    return out.join("/");
  }
  const dirName = (path) => { const p = normPath(path); const i = p.lastIndexOf("/"); return i >= 0 ? p.slice(0, i) : ""; };

  /**
   * Loads picked or dropped files: [{file, path}]. The index (and per-run entry files) are parsed at once;
   * run files are parsed now only when no index describes them (their #run header gives the entry),
   * otherwise on demand when the run is opened.
   */
  async function loadFiles(items) {
    const xcdnItems = items.filter((it) => /\.xcdn$/i.test(it.path || it.file.name));
    const errors = [];
    if (!xcdnItems.length) {
      return { ok: false, errors: [items.length ? "No .xcdn files in the selection." : "No files selected."], loaded: 0 };
    }
    const sniffed = [];
    for (const it of xcdnItems) {
      const path = normPath(it.path || it.file.name);
      try {
        const text = await it.file.text();
        sniffed.push({ path, file: it.file, text, format: sniffFormat(text) });
      } catch (error) {
        errors.push(`${baseName(path)} could not be read: ${error.message || error}`);
      }
    }
    const indexes = [];
    for (const s of sniffed.filter((x) => x.format === FORMATS.index)) {
      try { indexes.push(Object.assign(readIndex(parseText(s.text, s.path)), { path: s.path })); }
      catch (error) { errors.push(error.message); }
    }
    // several indexes (e.g. a folder holding copies): prefer the one under data/, then the one with most entries
    indexes.sort((a, b) => Number(/(^|\/)data\/index\.xcdn$/i.test(b.path)) - Number(/(^|\/)data\/index\.xcdn$/i.test(a.path)) || b.entries.length - a.entries.length);
    const index = indexes[0] || null;
    const indexDir = index ? dirName(index.path) : "";
    const entries = index ? index.entries.slice() : [];
    if (index) errors.push(...index.warnings);
    for (const s of sniffed.filter((x) => x.format === FORMATS.entry)) {
      try { entries.push(readEntryFile(parseText(s.text, s.path))); } catch (error) { errors.push(error.message); }
    }
    const byPath = new Map();
    const byBase = new Map();
    const runCache = new Map();
    const known = new Set(entries.map((e) => e.id));
    for (const s of sniffed) {
      if (s.format === FORMATS.index || s.format === FORMATS.entry) continue;
      if (s.format !== FORMATS.run) {
        // parse to report the parser's own message, the missing directive or the unknown format
        try { expectFormat(parseText(s.text, s.path), FORMATS.run); } catch (error) { errors.push(error.message); }
        continue;
      }
      byPath.set(s.path, s);
      const b = baseName(s.path);
      if (!byBase.has(b)) byBase.set(b, []);
      byBase.get(b).push(s);
      const stem = b.replace(/\.xcdn$/i, "");
      if (known.has(stem)) continue;
      try {
        const run = readRun(parseText(s.text, s.path));
        runCache.set(run.id, Promise.resolve(run));
        if (known.has(run.id)) continue;
        const rel = indexDir && s.path.startsWith(indexDir + "/") ? s.path.slice(indexDir.length + 1) : s.path;
        entries.push(entryFromRun(run, rel));
        known.add(run.id);
      } catch (error) { errors.push(error.message); }
    }
    if (!entries.length) {
      return { ok: false, errors: errors.length ? errors : ["No runs found in the selected files."], loaded: 0 };
    }
    generation += 1;
    store.runs = runCache;
    store.files = { byPath, byBase, indexDir };
    setEntries(entries, index ? index.header : null, { kind: "files", label: `${entries.length} run${entries.length === 1 ? "" : "s"} from files`, base: indexDir });
    store.errors = errors;
    emit();
    return { ok: true, errors, loaded: entries.length };
  }

  function localRunFile(entry) {
    const f = store.files;
    if (!f) return null;
    const candidates = [normPath((f.indexDir ? f.indexDir + "/" : "") + entry.file), normPath(entry.file)];
    for (const c of candidates) if (f.byPath.has(c)) return f.byPath.get(c);
    const list = f.byBase.get(baseName(entry.file)) || f.byBase.get(`${entry.id}.xcdn`) || [];
    return list.find((s) => /(^|\/)runs\//.test(s.path)) || list[0] || null;
  }

  /** The parsed run of an entry, loaded once and cached (onProgress receives 0…1, or null when unknown). */
  function getRun(id, onProgress) {
    if (store.runs.has(id)) return store.runs.get(id);
    const entry = store.byId.get(id);
    if (!entry) return Promise.reject(new DataError("This run is not in the loaded data."));
    const promise = (async () => {
      let text, name;
      if (store.files) {
        const local = localRunFile(entry);
        if (!local) throw new DataError(`The run file ${entry.file} was not among the opened files. Open it (or its folder) to explore this run.`, { missingFile: true, file: entry.file });
        text = local.text;
        name = local.path;
        if (onProgress) onProgress(1);
      } else {
        const url = new URL(entry.file, store.source.base || location.href).href;
        text = await fetchText(url, onProgress);
        name = entry.file;
      }
      const run = readRun(parseText(text, name));
      if (run.id !== entry.id) throw new DataError(`${baseName(name)} holds run ${run.id}, not ${entry.id}.`, { file: baseName(name) });
      return run;
    })();
    store.runs.set(id, promise);
    promise.catch(() => { if (store.runs.get(id) === promise) store.runs.delete(id); });
    return promise;
  }

  window.ExplorerData = Object.freeze({
    FORMATS, DOMAINS, REFERENCE_KEYS, INDEX_URL, DataError, store, boot, loadFiles, getRun, onChange,
    parseText, readIndex, readRun, readEntryFile, entryFromRun, plainNode, plainValue, decimalText, refKey,
  });
})();
