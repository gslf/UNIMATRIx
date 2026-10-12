"use strict";
/*
 * SocietyVillage — an animated, stylised map of a Polis society and of its AI steward.
 *
 *   const village = SocietyVillage.mount(element, { speed: 1, steward: true });
 *   village.load({ frames, residents, factions, recipe });      // frames of /api/polis/simulate
 *   village.play(); village.pause(); village.seek(12); village.setSpeed(2);
 *   village.append(newFrames);                                   // live runs: stream frames in
 *   village.on("frame", (index) => ...);                         // also action, play, pause, end, select, live, expand
 *   village.setExpanded(true);                                   // full-window view (mount option expand: true adds a button)
 *   village.destroy();
 *
 * Residents walk to the zone of their weekly activity; glow colour encodes wellbeing, a red
 * pulse marks protest, pale dots are sick, the dead fade to a marker, departures walk off
 * along the road and newcomers arrive along it. The steward — a gold figure with a lantern —
 * acts out each week's decisions in order: it walks to the resident or place concerned,
 * speaks (its own words when the model gave some), and the target answers.
 * Touch: tap a resident for its card, tap empty ground to dismiss. On small canvases labels, marks
 * and the steward grow so they stay legible and tappable; the notebook overlay is left to the page.
 * Self-contained: no dependencies, no network, canvas + requestAnimationFrame.
 */
(function () {
  const W = 1000;
  const H = 600;
  const TAU = Math.PI * 2;
  const FONT = "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif";
  const reducedMotion = () => Boolean(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);

  const ROLE_JOB = { farmer: "farm", fisher: "fish", healer: "heal", builder: "build", artisan: "craft",
    trader: "trade", elder: "lore", youth: "help" };
  const ZONE_OF = { fish: "water", monitor: "dock", farm: "fields", help: "fields", craft: "workshop",
    build: "workshop", project: "workshop", trade: "market", heal: "clinic", care: "clinic", sick: "beds",
    lore: "school", learn: "school", document: "school", rest: "home", withdrawn: "home", protest: "square" };
  const ACTIVITY_LABEL = { fish: "fishing on the lake", monitor: "monitoring the landings", farm: "working the fields",
    help: "helping in the fields", craft: "crafting", build: "building", project: "on public works", trade: "trading at the market",
    heal: "healing", care: "caring for someone", sick: "sick in bed", lore: "keeping the lore", learn: "learning a skill",
    document: "writing down a craft", rest: "resting at home", withdrawn: "withdrawn at home", protest: "protesting in the square",
    dead: "dead", departed: "left the community", work: "working" };
  const VERB_ICONS = { talk: "💬", give: "🎁", trade: "⚖", inspect: "🔍", assign: "🧭", build: "🔨", propose: "📜",
    decree: "📜", mediate: "🤝", introduce: "🔗", promise: "✋", gather: "🎪", convention: "🔄", announce: "📣", parse: "⚠" };
  const APPROACH_VERB = { listen: "listens", reassure: "reassures", support: "offers support", advise: "advises",
    ask: "asks how things are", apologize: "apologizes", encourage: "encourages", confront: "confronts" };
  // Where the steward stands for actions at a place (world coordinates).
  const PLACES = { hall: [655, 302], market: [548, 252], lake: [392, 304], square: [532, 350], workshop: [770, 380],
    school: [704, 520], homes: [640, 556] };
  const SEASON_TINT = { spring: [120, 210, 140, 0.05], summer: [255, 210, 100, 0.07], autumn: [235, 135, 55, 0.09],
    winter: [175, 205, 255, 0.11] };
  const SEASON_FIELD = { spring: ["#2c5a2f", "#3f7d3a"], summer: ["#5a6e22", "#86962e"], autumn: ["#76561c", "#a77a2c"],
    winter: ["#3a4249", "#58626b"] };
  const SEASON_TREE = { spring: "#2f6b3a", summer: "#2c5f2e", autumn: "#9a5a22", winter: "#5d6a72" };
  const WB_STOPS = [[0, [226, 87, 76]], [3, [232, 138, 58]], [5, [217, 180, 0]], [7, [156, 204, 101]], [10, [77, 208, 168]]];

  const LAKE = { cx: 205, cy: 318, rx: 160, ry: 205 };
  const ROAD_IN = { x: 1050, y: 410 };
  const SQUARE = { x: 515, y: 372, r: 50 };
  const TOWN_HALL = { x: 612, y: 232, w: 86, h: 52 };

  // ------------------------------------------------------------ small helpers
  function clamp(v, lo, hi) { return v < lo ? lo : v > hi ? hi : v; }
  function ease(t) { return t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2; }
  function hash(text) {
    let h = 2166136261;
    for (let i = 0; i < text.length; i++) { h ^= text.charCodeAt(i); h = Math.imul(h, 16777619); }
    return (h >>> 0) / 4294967296;
  }
  function rng(seed) {
    let s = (seed * 4294967296) >>> 0 || 1;
    return () => { s ^= s << 13; s ^= s >>> 17; s ^= s << 5; return ((s >>> 0) % 100000) / 100000; };
  }
  function hexRgb(hex) {
    const m = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(String(hex || "").trim());
    if (!m) return [200, 200, 200];
    let v = m[1];
    if (v.length === 3) v = v.split("").map((c) => c + c).join("");
    return [parseInt(v.slice(0, 2), 16), parseInt(v.slice(2, 4), 16), parseInt(v.slice(4, 6), 16)];
  }
  function rgba(c, a) { return `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${a})`; }
  function mix(a, b, t) { return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]; }
  function wellbeingColor(wb) {
    const v = clamp(wb, 0, 10);
    for (let i = 1; i < WB_STOPS.length; i++) {
      const [x1, c1] = WB_STOPS[i];
      const [x0, c0] = WB_STOPS[i - 1];
      if (v <= x1) return mix(c0, c1, (v - x0) / (x1 - x0));
    }
    return WB_STOPS[WB_STOPS.length - 1][1];
  }
  function lakePoint(theta, f) {
    const wobble = 1 + 0.11 * Math.sin(3 * theta + 0.6) + 0.06 * Math.sin(5 * theta + 1.9);
    return [LAKE.cx + Math.cos(theta) * LAKE.rx * wobble * f, LAKE.cy + Math.sin(theta) * LAKE.ry * wobble * f];
  }
  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }
  function firstName(name) { return String(name || "").split(/\s+/)[0] || String(name || ""); }
  function fmtNum(v) { return typeof v === "number" ? (Number.isInteger(v) ? String(v) : String(Math.round(v * 100) / 100)) : String(v); }

  /** Icon and a short human label for one steward action (shared with the pages that list actions). */
  function describeAction(action, nameOf) {
    const a = action || {};
    const p = a.params || {};
    const name = (id) => firstName(nameOf ? nameOf(id) : id) || id;
    const who = (a.targets || []).map(name);
    const verb = a.verb || "action";
    let label;
    switch (verb) {
      case "talk": label = `${APPROACH_VERB[p.approach] || p.approach || "talks"}${p.topic && p.topic !== "general" ? ` · ${p.topic}` : ""}`; break;
      case "give": label = `gives ${fmtNum(p.amount ?? "")} ${p.item || ""}`.trim(); break;
      case "trade": label = `${p.side === "sell" ? "sells" : "buys"} ${fmtNum(p.qty ?? "")} ${p.item || ""}${p.price != null ? ` at ${fmtNum(p.price)}` : ""}${p.with && p.with !== "market" ? ` · ${p.with}` : ""}`; break;
      case "inspect": {
        const t = String(p.target || "");
        const [kind, key] = [t.split(":")[0], t.split(":").slice(1).join(":")];
        label = t === "lake" ? "inspects the lake" : kind === "fact" ? `investigates “${key}”` : kind === "goods" ? `checks the ${key || "goods"}` : kind === "resident" ? `checks on ${name(key)}` : `inspects ${t || "the village"}`;
        break;
      }
      case "assign": {
        const [kind, skill, other] = String(p.task || "work").split(":");
        label = kind === "learn" ? `${who[0] || "someone"} → learn ${skill || ""}${other ? ` from ${name(other)}` : ""}` : kind === "project" ? `${who[0] || "someone"} → build the ${skill || "project"}`
          : kind === "care" ? `${who[0] || "someone"} → care for ${name(skill)}` : kind === "monitor" ? `${who[0] || "someone"} → watch the landings`
            : kind === "rest" ? `${who[0] || "someone"} → rest` : kind === "document" ? `${who[0] || "someone"} → write down ${skill || "a craft"}` : `${who[0] || "someone"} → ${kind}`;
        break;
      }
      case "build": label = `starts the ${String(p.project || "project").replace(/_/g, " ")}`; break;
      case "propose": label = `proposes ${p.policy} = ${fmtNum(p.value)}`; break;
      case "decree": label = `decrees ${p.policy} = ${fmtNum(p.value)}`; break;
      case "mediate": label = `mediates: ${p.resolution || "a settlement"}`; break;
      case "introduce": label = who.length >= 2 ? `introduces ${who[0]} & ${who[1]}` : "introduces neighbours"; break;
      case "promise": label = who.length ? `promises ${who[0]}` : "makes a promise"; break;
      case "gather": label = p.kind === "festival" ? "throws a festival" : "calls an assembly"; break;
      case "convention": label = `sets ${p.domain || "a convention"} → ${p.variant || "?"}`; break;
      case "announce": label = `announces: ${p.topic || "news"}${p.stance ? ` is ${p.stance}` : ""}`; break;
      case "parse": label = "unreadable answer"; break;
      default: label = a.label || verb;
    }
    return { icon: VERB_ICONS[verb] || "❔", label };
  }
  /** The target's reaction: the first sentence of the outcome text, with a speaker when it is a reply. */
  function replyOf(action) {
    let text = String((action && action.text) || "").trim();
    let speaker = null;
    const m = /^([^:"“]{2,48}):\s+([\s\S]*)$/.exec(text);
    if (m && (action.verb === "talk" || action.verb === "promise")) { speaker = m[1]; text = m[2]; }
    const s = /^([\s\S]{6,180}?[.!?…]["”’]?)(\s|$)/.exec(text);
    text = s ? s[1] : text.length > 170 ? `${text.slice(0, 167)}…` : text;
    return { speaker, text };
  }

  // ------------------------------------------------------------ static map layout
  const HOUSES = (() => {
    const out = [];
    const rand = rng(0.4242);
    for (let i = 0; i < 9; i++) out.push({ x: 420 + i * 62 + rand() * 14, y: 548 + (i % 2) * 22 });
    for (let i = 0; i < 5; i++) out.push({ x: 940 - i * 7 + rand() * 10, y: 250 + i * 52 });
    for (let i = 0; i < 4; i++) out.push({ x: 40 + i * 70 + rand() * 12, y: 575 - (i % 2) * 14 });
    for (let i = 0; i < 4; i++) out.push({ x: 430 + i * 48, y: 42 + (i % 2) * 18 });
    return out;
  })();
  function makeSlots() {
    const slots = {};
    const ring = (cx, cy, r0, step, count, start, span) => {
      const out = [];
      let r = r0;
      let made = 0;
      while (made < count) {
        const n = Math.max(6, Math.floor((span * r) / step));
        for (let i = 0; i < n && made < count; i++, made++) {
          const a = start + (span * (i + 0.5)) / n;
          out.push([cx + Math.cos(a) * r, cy + Math.sin(a) * r]);
        }
        r += step;
      }
      return out;
    };
    const grid = (x0, y0, cols, rows, dx, dy) => {
      const out = [];
      for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) out.push([x0 + c * dx + (r % 2) * dx * 0.5, y0 + r * dy]);
      return out;
    };
    slots.water = [];
    for (let k = 0; k < 64; k++) slots.water.push(lakePoint(-1.2 + (k * 2.39996) % TAU, 0.32 + 0.38 * ((k * 0.618) % 1)));
    slots.dock = grid(372, 296, 3, 6, 15, 16);
    slots.fields = grid(640, 70, 9, 6, 36, 26);
    slots.workshop = grid(712, 392, 7, 4, 18, 18);
    slots.market = ring(510, 212, 46, 18, 64, Math.PI * 0.35, Math.PI * 1.45);
    slots.clinic = grid(286, 470, 6, 4, 17, 16);
    slots.beds = grid(372, 448, 4, 8, 16, 14);
    slots.school = grid(640, 512, 8, 3, 18, 15);
    slots.square = ring(SQUARE.x, SQUARE.y, 26, 15, 64, 0, TAU);
    return slots;
  }
  const SLOTS = makeSlots();

  // ------------------------------------------------------------ background painter
  /** `ui.font` scales the map labels (in map units) so that they stay readable on small canvases. */
  function paintBackground(ctx, season, lakeName, ui) {
    const fontK = (ui && ui.font) || 1;
    const compact = Boolean(ui && ui.compact);
    const rand = rng(0.1337);
    const g = ctx.createLinearGradient(0, 0, 0, H);
    g.addColorStop(0, "#17201a");
    g.addColorStop(1, "#141915");
    ctx.fillStyle = g;
    ctx.fillRect(-400, -400, W + 800, H + 800);
    ctx.globalAlpha = 0.5;
    for (let i = 0; i < 900; i++) {
      ctx.fillStyle = rand() < 0.5 ? "#1d2a20" : "#121813";
      ctx.fillRect(rand() * W, rand() * H, 2, 2);
    }
    ctx.globalAlpha = 1;
    ctx.fillStyle = "#1b261d";
    ctx.beginPath();
    ctx.moveTo(380, 0);
    for (let x = 380; x <= W + 20; x += 20) ctx.lineTo(x, 30 + 16 * Math.sin(x / 70) + 8 * Math.sin(x / 23));
    ctx.lineTo(W + 20, 0);
    ctx.closePath();
    ctx.fill();
    const [fa, fb] = SEASON_FIELD[season] || SEASON_FIELD.spring;
    const patches = [[730, 48, 104, 80], [844, 58, 122, 70], [626, 134, 120, 84], [756, 138, 98, 76], [864, 136, 100, 82], [624, 52, 96, 74]];
    for (let i = 0; i < patches.length; i++) {
      const [x, y, w, h] = patches[i];
      ctx.save();
      roundRect(ctx, x, y, w, h, 6);
      ctx.fillStyle = i % 2 ? fa : fb;
      ctx.fill();
      ctx.clip();
      ctx.strokeStyle = "rgba(0,0,0,0.18)";
      ctx.lineWidth = 2;
      for (let k = -h; k < w + h; k += 9) {
        ctx.beginPath();
        if (i % 3 === 0) { ctx.moveTo(x + k, y); ctx.lineTo(x + k - h * 0.4, y + h); } else { ctx.moveTo(x, y + k * 0.7); ctx.lineTo(x + w, y + k * 0.7); }
        ctx.stroke();
      }
      ctx.restore();
      ctx.strokeStyle = "rgba(255,255,255,0.05)";
      ctx.lineWidth = 1;
      roundRect(ctx, x, y, w, h, 6);
      ctx.stroke();
    }
    ctx.fillStyle = "#2a2a1f";
    ctx.beginPath();
    for (let i = 0; i <= 72; i++) { const [x, y] = lakePoint((i / 72) * TAU, 1.07); if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y); }
    ctx.fill();
    ctx.fillStyle = "#3a3524";
    ctx.beginPath();
    for (let i = 0; i <= 72; i++) { const [x, y] = lakePoint((i / 72) * TAU, 1.0); if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y); }
    ctx.fill();
    // paths
    ctx.strokeStyle = "rgba(196,170,120,0.13)";
    ctx.lineCap = "round";
    ctx.lineWidth = 7;
    const paths = [[[SQUARE.x, SQUARE.y], [450, 330], [380, 322]], [[SQUARE.x, SQUARE.y], [515, 290], [515, 248]],
      [[SQUARE.x, SQUARE.y], [640, 360], [740, 386]], [[SQUARE.x, SQUARE.y], [600, 320], [655, 292]], [[655, 236], [680, 220], [700, 200]],
      [[SQUARE.x, SQUARE.y], [440, 440], [330, 470]], [[SQUARE.x, SQUARE.y], [590, 450], [680, 500]], [[SQUARE.x, SQUARE.y], [560, 480], [600, 545]]];
    for (const p of paths) { ctx.beginPath(); ctx.moveTo(p[0][0], p[0][1]); ctx.quadraticCurveTo(p[1][0], p[1][1], p[2][0], p[2][1]); ctx.stroke(); }
    // road
    ctx.strokeStyle = "#3b3326";
    ctx.lineWidth = 18;
    ctx.beginPath();
    ctx.moveTo(ROAD_IN.x, ROAD_IN.y);
    ctx.bezierCurveTo(900, 402, 700, 420, SQUARE.x + 40, SQUARE.y + 10);
    ctx.stroke();
    ctx.strokeStyle = "rgba(222,200,150,0.18)";
    ctx.lineWidth = 1.5;
    ctx.setLineDash([10, 12]);
    ctx.stroke();
    ctx.setLineDash([]);
    // trees
    const tree = SEASON_TREE[season] || SEASON_TREE.spring;
    const avoid = (x, y) => (x > 600 && x < 980 && y < 230) || Math.hypot((x - LAKE.cx) / 185, (y - LAKE.cy) / 235) < 1
      || (x > 260 && x < 830 && y > 170 && y < 530) || y > 528 || (y > 380 && y < 440 && x > 820);
    for (let i = 0; i < 140; i++) {
      const x = rand() * W;
      const y = 20 + rand() * (H - 40);
      if (avoid(x, y)) continue;
      const r = 5 + rand() * 7;
      ctx.fillStyle = "rgba(0,0,0,0.3)";
      ctx.beginPath(); ctx.arc(x + 2, y + 3, r, 0, TAU); ctx.fill();
      ctx.fillStyle = tree;
      ctx.globalAlpha = 0.65 + rand() * 0.3;
      ctx.beginPath(); ctx.arc(x, y, r, 0, TAU); ctx.fill();
      ctx.globalAlpha = 1;
    }
    // square
    ctx.fillStyle = "#2b2a26";
    ctx.beginPath(); ctx.arc(SQUARE.x, SQUARE.y, SQUARE.r + 12, 0, TAU); ctx.fill();
    ctx.strokeStyle = "rgba(255,255,255,0.06)";
    ctx.lineWidth = 1;
    for (let r = 18; r < SQUARE.r + 12; r += 11) { ctx.beginPath(); ctx.arc(SQUARE.x, SQUARE.y, r, 0, TAU); ctx.stroke(); }
    ctx.fillStyle = "#4a4436";
    ctx.beginPath(); ctx.arc(SQUARE.x, SQUARE.y, 9, 0, TAU); ctx.fill();
    ctx.fillStyle = "#1e3a52";
    ctx.beginPath(); ctx.arc(SQUARE.x, SQUARE.y, 5, 0, TAU); ctx.fill();
    // dock
    ctx.fillStyle = "#5a4630";
    ctx.fillRect(340, 312, 70, 9);
    for (let x = 344; x < 408; x += 10) { ctx.fillStyle = "#3e3020"; ctx.fillRect(x, 321, 3, 5); }
    const label = (text, x, y) => {
      ctx.fillStyle = compact ? "rgba(222,222,222,0.7)" : "rgba(222,222,222,0.55)";
      ctx.font = `600 ${10 * fontK}px ${FONT}`;
      ctx.textAlign = "center";
      ctx.fillText(text, x, y + (fontK - 1) * 7);
      ctx.textAlign = "start";
    };
    const building = (x, y, w, h, roof, name, glyph) => {
      ctx.fillStyle = "rgba(0,0,0,0.35)";
      roundRect(ctx, x + 4, y + 6, w, h, 5); ctx.fill();
      ctx.fillStyle = "#2e2a24";
      roundRect(ctx, x, y, w, h, 5); ctx.fill();
      ctx.fillStyle = roof;
      ctx.beginPath(); ctx.moveTo(x - 5, y + 4); ctx.lineTo(x + w / 2, y - h * 0.38); ctx.lineTo(x + w + 5, y + 4); ctx.closePath(); ctx.fill();
      ctx.fillStyle = "rgba(255,214,120,0.75)";
      for (let wx = x + 9; wx < x + w - 8; wx += 16) ctx.fillRect(wx, y + h * 0.38, 6, 6);
      if (glyph) glyph(x, y, w, h);
      label(name, x + w / 2, y + h + 15);
    };
    building(728, 300, 96, 58, "#6b4a33", "WORKSHOP", (x, y) => { ctx.fillStyle = "#3a332b"; ctx.fillRect(x + 72, y - 26, 9, 22); });
    building(300, 430, 70, 46, "#7b3b3b", "CLINIC", (x, y, w, h) => {
      ctx.fillStyle = "#f0f0f0"; ctx.fillRect(x + w / 2 - 2, y + h - 17, 4, 12); ctx.fillRect(x + w / 2 - 6, y + h - 13, 12, 4);
    });
    building(648, 456, 112, 44, "#4b4a6b", compact ? "SCHOOL" : "SCHOOL & ELDER HALL", (x, y, w, h) => {
      ctx.fillStyle = "#5a5852"; for (let cx = x + 10; cx < x + w - 6; cx += 18) ctx.fillRect(cx, y + h - 20, 4, 18);
    });
    building(TOWN_HALL.x, TOWN_HALL.y, TOWN_HALL.w, TOWN_HALL.h, "#8a6a1a", "TOWN HALL", (x, y, w, h) => {
      ctx.fillStyle = "#6e6550"; for (let cx = x + 8; cx < x + w - 6; cx += 14) ctx.fillRect(cx, y + h - 22, 4, 20);
      ctx.fillStyle = "#b59a00"; ctx.fillRect(x + w / 2 - 1, y - h * 0.38 - 16, 2, 16);
      ctx.beginPath(); ctx.moveTo(x + w / 2 + 1, y - h * 0.38 - 16); ctx.lineTo(x + w / 2 + 13, y - h * 0.38 - 11); ctx.lineTo(x + w / 2 + 1, y - h * 0.38 - 6); ctx.fill();
    });
    for (let i = 0; i < 4; i++) {
      const a = Math.PI * (0.2 + i * 0.42);
      const x = 510 + Math.cos(a) * 18 - 14;
      const y = 212 + Math.sin(a) * 12 - 10;
      ctx.fillStyle = "#2e2a24"; ctx.fillRect(x, y + 6, 28, 14);
      for (let s = 0; s < 4; s++) { ctx.fillStyle = s % 2 ? "#b59a00" : "#3a3020"; ctx.fillRect(x + s * 7, y, 7, 8); }
    }
    label("MARKET", 510, 186);
    label("SQUARE", SQUARE.x, SQUARE.y + SQUARE.r + 26);
    label("FIELDS", 790, 238);
    if (!compact) label(String(lakeName || "the lake").toUpperCase(), LAKE.cx, LAKE.cy - LAKE.ry - 12);
    label("HOMES", 700, 596);
    for (const house of HOUSES) {
      ctx.fillStyle = "rgba(0,0,0,0.35)"; ctx.fillRect(house.x - 8, house.y - 4, 20, 14);
      ctx.fillStyle = "#33302a"; ctx.fillRect(house.x - 10, house.y - 7, 20, 13);
      ctx.fillStyle = "#5c3d2c";
      ctx.beginPath(); ctx.moveTo(house.x - 12, house.y - 6); ctx.lineTo(house.x, house.y - 15); ctx.lineTo(house.x + 12, house.y - 6); ctx.fill();
      ctx.fillStyle = "rgba(255,200,110,0.8)"; ctx.fillRect(house.x - 2, house.y - 3, 4, 4);
    }
  }

  // ------------------------------------------------------------ component
  function mount(element, options) {
    if (!element) throw new Error("SocietyVillage.mount needs an element");
    const opts = Object.assign({ speed: 1, weekMs: 1500, loop: false, captions: true, interactive: true, ties: true,
      autoplay: false, labels: false, steward: true, thoughts: true, notebook: true, followLive: true, captionsAt: "bottom", expand: false }, options || {});
    const root = document.createElement("div");
    root.className = "society-village";
    Object.assign(root.style, { position: "relative", width: "100%", height: "100%", minHeight: "220px",
      overflow: "hidden", borderRadius: "inherit", background: "#141915", touchAction: "manipulation" });
    if (!element.style.height && !element.clientHeight) root.style.aspectRatio = "5 / 3";
    const canvas = document.createElement("canvas");
    Object.assign(canvas.style, { display: "block", width: "100%", height: "100%", cursor: "default" });
    canvas.setAttribute("role", "img");
    canvas.setAttribute("aria-label", "Animated village map");
    const tip = document.createElement("div");
    tip.setAttribute("role", "tooltip");
    Object.assign(tip.style, { position: "absolute", pointerEvents: "none", zIndex: 4, minWidth: "180px", maxWidth: "260px",
      padding: "10px 12px", borderRadius: "10px", background: "rgba(20,20,20,0.94)", border: "1px solid rgba(255,255,255,0.12)",
      boxShadow: "0 12px 30px rgba(0,0,0,0.5)", color: "#dedede", font: `12px/1.45 ${FONT}`,
      opacity: "0", transform: "translateY(4px)", transition: "opacity .14s ease, transform .14s ease" });
    const notebook = document.createElement("div");
    notebook.setAttribute("aria-live", "polite");
    Object.assign(notebook.style, { position: "absolute", right: "10px", top: "10px", zIndex: 3, maxWidth: "min(300px, 42%)", maxHeight: "46%",
      overflowY: "auto", padding: "10px 12px", borderRadius: "12px", background: "rgba(22,20,12,0.9)", border: "1px solid rgba(181,154,0,0.45)",
      boxShadow: "0 10px 28px rgba(0,0,0,0.5)", color: "#ece3c0", font: `12px/1.45 ${FONT}`, display: "none", backdropFilter: "blur(4px)" });
    const liveButton = document.createElement("button");
    liveButton.type = "button";
    liveButton.textContent = "Jump to live ▸";
    Object.assign(liveButton.style, { position: "absolute", left: "50%", bottom: "14px", transform: "translateX(-50%)", zIndex: 3, display: "none",
      padding: "7px 14px", borderRadius: "20px", border: "1px solid #e2c21c", background: "rgba(30,26,8,0.92)", color: "#f5e7a6",
      font: `600 12px ${FONT}`, cursor: "pointer", boxShadow: "0 0 18px rgba(226,194,28,0.35)" });
    root.append(canvas, tip, notebook, liveButton);
    element.append(root);
    const ctx = canvas.getContext("2d");

    const listeners = {};
    const emit = (name, value) => {
      if (name === "play" || name === "pause" || name === "frame") syncExpandBar();
      (listeners[name] || []).slice().forEach((fn) => { try { fn(value); } catch (e) { console.error(e); } });
    };

    let frames = [];
    let residents = new Map();
    let factionColors = new Map();
    let capacity = 1000;
    let lakeName = "the lake";
    let worldName = "";
    const agents = [];
    const agentById = new Map();
    let index = 0;
    let playing = false;
    let speed = opts.speed;
    let last = 0;
    let raf = 0;
    let visible = true;
    let destroyed = false;
    let hoverId = null;
    let selectedId = null;
    let cssW = 0, cssH = 0, dpr = 1, scale = 1, ox = 0, oy = 0;
    // Small canvases: marks grow (up to 1.6×) and map labels keep ~9px on screen; `compact` trims clutter.
    let markK = 1, fontK = 1, compact = false;
    let lastPointer = "mouse";
    const bgCache = new Map();
    let season = "spring";
    let prevSeason = null;
    let seasonT0 = 0;
    let lakeFrom = 0.6, lakeTo = 0.6, lakeT0 = 0, lakeDur = 1;
    const captions = Array.from({ length: 6 }, () => ({ text: "", t0: -1e9, x: 0, y: 0, bad: false, width: 0 }));
    let captionCursor = 0;
    const PARTICLES = 46;
    const px = new Float32Array(PARTICLES), py = new Float32Array(PARTICLES), pv = new Float32Array(PARTICLES);
    for (let i = 0; i < PARTICLES; i++) { px[i] = Math.random() * W; py[i] = Math.random() * H; pv[i] = 0.4 + Math.random(); }
    const zoneCount = Object.create(null);
    let collapsedLabel = null;
    // steward & week clock (week-time ms: advances only while playing, scaled by speed)
    let clock = 0;
    let weekStart = 0;
    let weekDur = opts.weekMs;
    let script = null;
    let liveMode = false;
    let followLive = Boolean(opts.followLive);
    let autoResume = true;
    const steward = { x: PLACES.hall[0], y: PLACES.hall[1], visible: opts.steward !== false, moving: false, acting: null,
      trail: new Float32Array(48), trailCount: 0, trailHead: 0, lastSample: 0 };
    const bubbles = [];
    const effects = [];

    const lakeColors = [];
    for (let i = 0; i <= 100; i++) {
      const share = i / 100;
      const c = mix([74, 84, 52], [38, 112, 160], clamp((share - 0.1) / 0.5, 0, 1));
      lakeColors.push([rgba(c, 1), rgba(mix(c, [10, 30, 50], 0.45), 1)]);
    }
    function sprite(color, alpha) {
      const size = 64;
      const c = document.createElement("canvas");
      c.width = c.height = size;
      const g = c.getContext("2d");
      const grad = g.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
      grad.addColorStop(0, rgba(color, alpha));
      grad.addColorStop(0.45, rgba(color, alpha * 0.35));
      grad.addColorStop(1, rgba(color, 0));
      g.fillStyle = grad;
      g.fillRect(0, 0, size, size);
      return c;
    }
    const glowSprites = [];
    for (let i = 0; i <= 20; i++) glowSprites.push(sprite(wellbeingColor(i / 2), 0.8));
    const protestSprite = sprite([239, 68, 68], 0.95);
    const selectSprite = sprite([255, 236, 160], 0.9);
    const stewardGlow = sprite([255, 214, 70], 0.75);
    const lanternGlow = sprite([255, 190, 90], 0.95);
    const rejectGlow = sprite([239, 83, 80], 0.9);

    // ------------------------------------------------ sizing
    function resize() {
      const rect = root.getBoundingClientRect();
      cssW = Math.max(1, rect.width);
      cssH = Math.max(1, rect.height);
      dpr = Math.min(window.devicePixelRatio || 1, 2.5);
      canvas.width = Math.round(cssW * dpr);
      canvas.height = Math.round(cssH * dpr);
      scale = Math.min(cssW / W, cssH / H);
      ox = (cssW - W * scale) / 2;
      oy = (cssH - H * scale) / 2;
      markK = clamp(0.5 / scale, 1, 1.6);
      fontK = Math.max(1, 0.92 / scale);
      const wasCompact = compact;
      compact = cssW < 560;
      tip.style.maxWidth = `${Math.max(150, Math.min(260, cssW - 16))}px`;
      tip.style.minWidth = `${Math.max(140, Math.min(180, cssW - 16))}px`;
      bgCache.clear();
      for (const b of bubbles) b.measured = false;
      if (wasCompact !== compact && frames[index]) renderNotebook(frames[index]);
      syncExpandBar();
      requestDraw();
    }
    function background(key) {
      let bg = bgCache.get(key);
      if (!bg) {
        bg = document.createElement("canvas");
        bg.width = canvas.width;
        bg.height = canvas.height;
        const g = bg.getContext("2d");
        g.setTransform(scale * dpr, 0, 0, scale * dpr, ox * dpr, oy * dpr);
        paintBackground(g, key, lakeName, { font: fontK, compact });
        bgCache.set(key, bg);
      }
      return bg;
    }
    function toWorld(clientX, clientY) {
      const rect = canvas.getBoundingClientRect();
      return { x: (clientX - rect.left - ox) / scale, y: (clientY - rect.top - oy) / scale };
    }
    const sx = (x) => ox + x * scale;
    const sy = (y) => oy + y * scale;

    // ------------------------------------------------ residents
    function nameOf(id) { const r = residents.get(id); return r ? r.name : id; }
    function agentFor(id) {
      let a = agentById.get(id);
      if (!a) {
        const info = residents.get(id) || { id, name: id };
        const color = hexRgb(factionColors.get(info.faction) || "#c8c8c8");
        const seed = hash(id);
        a = { id, info, color, colorStr: rgba(color, 1), paleStr: rgba(mix(color, [235, 235, 235], 0.65), 0.75), seed,
          home: HOUSES[Math.floor(seed * HOUSES.length) % HOUSES.length], x: ROAD_IN.x, y: ROAD_IN.y, fx: 0, fy: 0, tx: 0,
          ty: 0, t0: 0, dur: 1, bend: (seed - 0.5) * 40, alpha: 0, alphaFrom: 0, alphaTo: 0, present: false, shown: false,
          status: "present", activity: "rest", data: null, deadAt: 0, zone: "home", moving: false, bob: 0, pull: null };
        agentById.set(id, a);
        agents.push(a);
      }
      return a;
    }
    function targetFor(a, data) {
      if (data.status === "dead") return [a.x, a.y];
      if (data.status === "departed") return [ROAD_IN.x + 30, ROAD_IN.y + (a.seed - 0.5) * 20];
      let activity = data.activity;
      if (activity === "work" || !ZONE_OF[activity]) activity = ROLE_JOB[a.info.role] || "help";
      const zone = ZONE_OF[activity] || "square";
      a.zone = zone;
      if (zone === "home") return [a.home.x + (a.seed - 0.5) * 10, a.home.y + 10];
      const list = SLOTS[zone];
      const n = zoneCount[zone] = (zoneCount[zone] || 0) + 1;
      const slot = list[(n - 1) % list.length];
      const lap = Math.floor((n - 1) / list.length);
      return [slot[0] + lap * 5, slot[1] + lap * 5];
    }
    function applyFrame(i, animate) {
      const frame = frames[i];
      if (!frame) return;
      const now = performance.now();
      const dur = animate ? clamp((opts.weekMs / Math.max(0.1, speed)) * 0.72, 260, 1500) : 0;
      for (const key in zoneCount) zoneCount[key] = 0;
      for (const a of agents) a.present = false;
      for (const data of frame.residents || []) {
        const a = agentFor(data.id);
        const wasShown = a.shown;
        a.present = true;
        a.data = data;
        a.status = data.status;
        a.activity = data.activity;
        const [tx, ty] = targetFor(a, data);
        if (!wasShown) {
          if (animate && i > 0 && data.status === "present") { a.x = ROAD_IN.x; a.y = ROAD_IN.y + (a.seed - 0.5) * 16; }
          else { a.x = tx; a.y = ty; }
          a.alpha = animate && i > 0 ? 0 : 1;
        }
        a.fx = a.x; a.fy = a.y; a.tx = tx; a.ty = ty; a.t0 = now; a.dur = dur || 1;
        if (!dur) { a.x = tx; a.y = ty; }
        a.alphaFrom = a.alpha;
        a.alphaTo = data.status === "departed" ? 0 : 1;
        if (data.status === "dead" && !a.deadAt) a.deadAt = animate ? now : now - 5000;
        if (data.status !== "dead") a.deadAt = 0;
        a.shown = true;
        a.pull = null;
      }
      for (const a of agents) if (!a.present) { a.shown = false; a.alpha = 0; a.alphaTo = 0; }
      const share = frame.commons && typeof frame.commons.stock === "number" ? clamp(frame.commons.stock / capacity, 0, 1) : lakeTo;
      lakeFrom = currentLake(now);
      lakeTo = share;
      lakeT0 = now;
      lakeDur = dur || 1;
      if (!dur) lakeFrom = share;
      if (frame.season && frame.season !== season) { prevSeason = animate ? season : null; season = frame.season; seasonT0 = now; }
      collapsedLabel = frame.collapsed && i === frames.length - 1 ? frame.collapsed.label || "Collapse" : null;
      if (opts.captions && frame.events && frame.events.length && i > 0) {
        const events = frame.events.slice().sort((x, y) => Number(/COLLAPSE|turning point/i.test(y)) - Number(/COLLAPSE|turning point/i.test(x)));
        for (let e = 0; e < Math.min(3, events.length); e++) caption(events[e], now + e * 260);
      }
      startWeek(i, animate && playing);
      canvas.setAttribute("aria-label", describe(frame));
      renderNotebook(frame);
      if (hoverId || selectedId) updateTip();
      requestDraw();
    }
    function describe(frame) {
      const m = frame.metrics || {};
      const present = (frame.residents || []).filter((r) => r.status === "present");
      const protest = present.filter((r) => r.protest).length;
      const sick = present.filter((r) => r.sick).length;
      const acts = (frame.actions || []).length;
      return `${worldName || "Village"}, week ${frame.week}${frame.season ? ", " + frame.season : ""}: ${present.length} residents present`
        + `${protest ? `, ${protest} protesting` : ""}${sick ? `, ${sick} sick` : ""}`
        + (typeof m.wellbeing === "number" ? `, mean wellbeing ${m.wellbeing.toFixed(1)}` : "")
        + (acts ? `. The steward takes ${acts} action${acts > 1 ? "s" : ""}` : "")
        + (frame.collapsed ? `. Collapse: ${frame.collapsed.label}` : "");
    }
    function caption(text, t0) {
      const c = captions[captionCursor];
      captionCursor = (captionCursor + 1) % captions.length;
      const clean = String(text);
      c.text = clean.length > 90 ? clean.slice(0, 87) + "…" : clean;
      c.bad = /COLLAPSE|riot|died|dies|dead|famine|revolt/i.test(clean);
      c.t0 = t0;
      c.width = 0;
      c.slot = captionCursor % 3;
    }
    function currentLake(now) { return lakeFrom + (lakeTo - lakeFrom) * ease(clamp((now - lakeT0) / lakeDur, 0, 1)); }

    // ------------------------------------------------ steward script
    function liveAgent(id) { const a = id && agentById.get(id); return a && a.shown && a.status === "present" ? a : null; }
    function spotFor(action) {
      const people = (action.targets || []).map(liveAgent).filter(Boolean);
      if (people.length >= 2 && people[0] !== people[1]) {
        const [a, b] = people;
        return { kind: "pair", a, b, at: () => [(a.x + b.x) / 2, (a.y + b.y) / 2 + 12] };
      }
      if (people.length && !(action.place && ["hall", "market", "workshop"].includes(action.place) && action.verb !== "assign")) {
        const a = people[0];
        if (a.zone === "water") {
          // Stand on the shore facing the boat instead of walking on water.
          return { kind: "agent", a, at: () => lakePoint(Math.atan2((a.y - LAKE.cy) / LAKE.ry, (a.x - LAKE.cx) / LAKE.rx), 1.06) };
        }
        const side = a.x > 900 ? 16 : -16;
        return { kind: "agent", a, at: () => [a.x + side, a.y + 3] };
      }
      const place = PLACES[action.place] || PLACES.hall;
      return { kind: "place", at: () => place };
    }
    function startWeek(i, act) {
      weekStart = clock;
      for (const b of bubbles) b.t1 = Math.min(b.t1, clock + 260);
      effects.length = 0;
      for (const a of agents) a.pull = null;
      const frame = frames[i] || {};
      const actions = Array.isArray(frame.actions) ? frame.actions : [];
      const visibleSteward = steward.visible;
      const beats = [];
      let t = 0;
      const note = frame.steward && frame.steward.note;
      if (act && visibleSteward && note && opts.thoughts) {
        pushBubble({ kind: "thought", anchor: "steward", header: "thinking…", body: note, t0: clock, t1: clock + 1700 });
        t = 380;
      } else t = 140;
      let pos = [steward.x, steward.y];
      for (let k = 0; k < actions.length; k++) {
        const action = actions[k] || {};
        const spot = spotFor(action);
        const end = spot.at();
        const dist = Math.hypot(end[0] - pos[0], end[1] - pos[1]);
        const walk = !visibleSteward ? 0 : reducedMotion() ? 140 : clamp(dist * 1.5, 160, 430);
        const actDur = visibleSteward ? 540 : 260;
        beats.push({ action, k, start: t, walk, act: actDur, spot, began: false, acted: false, fx: pos[0], fy: pos[1] });
        t += walk + actDur;
        pos = end;
      }
      script = { frameIndex: i, beats, end: t, ended: !beats.length, played: Boolean(act) };
      weekDur = act && beats.length ? Math.max(opts.weekMs, t + 450) : opts.weekMs;
      if (!act) {
        if (beats.length && visibleSteward) { const endSpot = beats[beats.length - 1].spot.at(); steward.x = endSpot[0]; steward.y = endSpot[1]; }
        for (const b of beats) { b.began = true; b.acted = true; }
        script.ended = true;
        bubbles.length = 0;
        steward.acting = null;
        emit("action", { frameIndex: i, index: -1, action: null });
      }
    }
    function updateScript() {
      if (!script || !script.played) return;
      const wt = clock - weekStart;
      let current = null;
      for (const b of script.beats) {
        if (wt < b.start) break;
        current = b;
        if (!b.began) {
          b.began = true;
          b.fx = steward.x;
          b.fy = steward.y;
          emit("action", { frameIndex: script.frameIndex, index: b.k, action: b.action });
        }
        if (!b.acted && wt >= b.start + b.walk) { b.acted = true; act(b); }
      }
      if (current) {
        const [tx, ty] = current.spot.at();
        const t = current.walk ? clamp((wt - current.start) / current.walk, 0, 1) : 1;
        const e = reducedMotion() ? (t >= 1 ? 1 : 0) : ease(t);
        steward.x = current.fx + (tx - current.fx) * e;
        steward.y = current.fy + (ty - current.fy) * e;
        steward.moving = t < 1;
        steward.acting = wt < current.start + current.walk + current.act ? current : null;
      }
      if (!script.ended && wt >= script.end) {
        script.ended = true;
        steward.acting = null;
        emit("action", { frameIndex: script.frameIndex, index: -1, action: null });
      }
    }
    function pushBubble(b) {
      b.measured = false;
      bubbles.push(b);
      while (bubbles.length > 10) bubbles.shift();
    }
    function act(beat) {
      if (!steward.visible) return;
      const a = beat.action;
      const meta = describeAction(a, nameOf);
      const t0 = clock;
      const end = clock + beat.act;
      const rejected = a.status && a.status !== "done";
      pushBubble({ kind: "say", anchor: { x: steward.x, y: steward.y }, icon: meta.icon, header: meta.label, body: a.say ? `“${a.say}”` : null, t0, t1: end + 260 });
      const reply = replyOf(a);
      const spot = beat.spot;
      const anchor = spot.kind === "agent" ? spot.a : spot.kind === "pair" ? spot.b : { x: steward.x + 6, y: steward.y - 4 };
      if (rejected) {
        effects.push({ kind: "puff", x: steward.x, y: steward.y, t0: end - 260, t1: end + 420 });
        pushBubble({ kind: "reject", anchor, header: "✕ refused", body: reply.text, t0: t0 + 240, t1: end + 800 });
      } else if (reply.text) {
        const header = reply.speaker ? firstName(reply.speaker) : spot.kind === "agent" ? firstName(spot.a.info.name) : null;
        pushBubble({ kind: "reply", anchor, header, body: reply.text, t0: t0 + 230, t1: end + 650 });
      }
      if (rejected) return;
      if (a.verb === "introduce" && spot.kind === "pair") effects.push({ kind: "tie", a: spot.a, b: spot.b, t0, t1: end + 700 });
      if (a.verb === "mediate" && spot.kind === "pair") {
        const [cx, cy] = spot.at();
        spot.a.pull = { x: cx - 13, y: cy - 6, t0: t0 - 120, t1: end + 260 };
        spot.b.pull = { x: cx + 13, y: cy - 6, t0: t0 - 120, t1: end + 260 };
      }
      if (a.verb === "announce" || a.verb === "gather") effects.push({ kind: "ripple", x: steward.x, y: steward.y, t0, t1: t0 + 1100 });
      if (a.verb === "give" && spot.kind === "agent") effects.push({ kind: "gift", a: spot.a, x: steward.x, y: steward.y, icon: "🎁", t0, t1: t0 + 460 });
      if (a.verb === "trade") effects.push({ kind: "gift", a: null, x: steward.x, y: steward.y, icon: "🪙", t0, t1: t0 + 520 });
      if (a.verb === "inspect") effects.push({ kind: "scan", x: steward.x, y: steward.y, t0, t1: end + 200 });
    }

    // ------------------------------------------------ notebook (DOM) and live affordance
    function renderNotebook(frame) {
      const info = frame && frame.steward;
      const hasNote = info && (info.note || info.error || (info.forecasts && Object.keys(info.forecasts).length));
      if (!opts.notebook || !steward.visible || !hasNote || compact) { notebook.style.display = "none"; return; }
      notebook.replaceChildren();
      const head = document.createElement("div");
      Object.assign(head.style, { fontWeight: "700", color: "#e2c21c", fontSize: "11px", letterSpacing: "1.4px", textTransform: "uppercase", marginBottom: "4px" });
      head.textContent = `📓 Steward's notebook · week ${frame.week}`;
      notebook.append(head);
      if (info.note) {
        const p = document.createElement("div");
        p.style.whiteSpace = "pre-wrap";
        p.textContent = info.note;
        notebook.append(p);
      }
      const forecasts = info.forecasts ? Object.entries(info.forecasts) : [];
      if (forecasts.length) {
        const f = document.createElement("div");
        Object.assign(f.style, { marginTop: "6px", color: "#c9e6ff" });
        f.textContent = "Forecasts: " + forecasts.map(([k, v]) => `${k} ${Math.round(Number(v) * 100)}%`).join(" · ");
        notebook.append(f);
      }
      if (info.error) {
        const e = document.createElement("div");
        Object.assign(e.style, { marginTop: "6px", color: "#ffb3aa" });
        e.textContent = `⚠ ${info.error}`;
        notebook.append(e);
      }
      notebook.style.display = "block";
    }
    function updateLiveButton() {
      liveButton.style.display = liveMode && !followLive && frames.length > 1 ? "block" : "none";
    }
    liveButton.addEventListener("click", () => controller.setFollowLive(true));

    // ------------------------------------------------ drawing
    let drawQueued = false;
    function requestDraw() {
      if (!drawQueued && !raf) { drawQueued = true; requestAnimationFrame(() => { drawQueued = false; if (!raf) draw(performance.now()); }); }
    }
    function draw(now) {
      if (destroyed || !canvas.width) return;
      updateScript();
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.drawImage(background(season), 0, 0);
      if (prevSeason) {
        const t = clamp((now - seasonT0) / 1600, 0, 1);
        if (t < 1) { ctx.globalAlpha = 1 - t; ctx.drawImage(background(prevSeason), 0, 0); ctx.globalAlpha = 1; } else prevSeason = null;
      }
      ctx.setTransform(scale * dpr, 0, 0, scale * dpr, ox * dpr, oy * dpr);
      const motion = !reducedMotion();
      drawLake(now, motion);
      drawSeason(now, motion);
      updateAgents(now, motion);
      if (opts.ties) drawTies();
      drawConflicts(now);
      drawEffects();
      drawAgents(now, motion);
      drawSteward(now, motion);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      drawBubbles();
      drawCaptions(now);
      drawHud(now);
      const focus = hoverId || selectedId;
      const focused = focus && agentById.get(focus);
      if (focused && focused.moving && opts.interactive) positionTip(focused);
    }
    function drawLake(now, motion) {
      const share = currentLake(now);
      const f = 0.5 + 0.5 * Math.sqrt(share);
      const colors = lakeColors[Math.round(share * 100)];
      ctx.fillStyle = colors[0];
      ctx.beginPath();
      for (let i = 0; i <= 64; i++) {
        const theta = (i / 64) * TAU;
        const wobble = 1 + 0.11 * Math.sin(3 * theta + 0.6) + 0.06 * Math.sin(5 * theta + 1.9);
        const x = LAKE.cx + Math.cos(theta) * LAKE.rx * wobble * f;
        const y = LAKE.cy + Math.sin(theta) * LAKE.ry * wobble * f;
        if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y);
      }
      ctx.fill();
      ctx.fillStyle = colors[1];
      ctx.beginPath();
      for (let i = 0; i <= 48; i++) {
        const theta = (i / 48) * TAU;
        const wobble = 1 + 0.08 * Math.sin(3 * theta + 1.1);
        const x = LAKE.cx + 8 + Math.cos(theta) * LAKE.rx * wobble * f * 0.55;
        const y = LAKE.cy + 10 + Math.sin(theta) * LAKE.ry * wobble * f * 0.55;
        if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y);
      }
      ctx.fill();
      ctx.strokeStyle = "rgba(200,230,255,0.16)";
      ctx.lineWidth = 1.2;
      const t = motion ? now / 1000 : 0;
      for (let k = 0; k < 7; k++) {
        const yy = LAKE.cy - LAKE.ry * f * 0.6 + k * LAKE.ry * f * 0.19;
        const xx = LAKE.cx - 40 + Math.sin(t * 0.6 + k * 1.7) * 30;
        ctx.beginPath();
        ctx.moveTo(xx - 22, yy);
        ctx.quadraticCurveTo(xx, yy - 3 - Math.sin(t + k) * 2, xx + 22, yy);
        ctx.stroke();
      }
      ctx.fillStyle = compact ? "rgba(222,222,222,0.78)" : "rgba(222,222,222,0.6)";
      ctx.font = `600 ${10 * fontK}px ${FONT}`;
      ctx.textAlign = "center";
      const stock = Math.round(share * 100) + "% stock";
      ctx.fillText(compact ? `${String(lakeName || "the lake").toUpperCase()} · ${stock}` : stock, LAKE.cx, LAKE.cy + LAKE.ry + 12 + 10 * fontK);
      ctx.textAlign = "start";
    }
    function drawSeason(now, motion) {
      const tint = SEASON_TINT[season] || SEASON_TINT.spring;
      ctx.fillStyle = `rgba(${tint[0]},${tint[1]},${tint[2]},${tint[3]})`;
      ctx.fillRect(-ox / scale, -oy / scale, cssW / scale, cssH / scale);
      if (!motion || (season !== "winter" && season !== "autumn")) return;
      const winter = season === "winter";
      ctx.fillStyle = winter ? "rgba(235,242,255,0.7)" : "rgba(214,124,46,0.75)";
      for (let i = 0; i < PARTICLES; i++) {
        py[i] += pv[i] * (winter ? 0.5 : 0.7);
        px[i] += Math.sin(now / 900 + i) * (winter ? 0.25 : 0.6);
        if (py[i] > H) { py[i] = -5; px[i] = Math.random() * W; }
        if (winter) { ctx.beginPath(); ctx.arc(px[i], py[i], 1.3 + pv[i] * 0.6, 0, TAU); ctx.fill(); }
        else ctx.fillRect(px[i], py[i], 3, 2);
      }
    }
    function updateAgents(now, motion) {
      for (const a of agents) {
        if (!a.shown) continue;
        const t = clamp((now - a.t0) / a.dur, 0, 1);
        const e = ease(t);
        const dx = a.tx - a.fx, dy = a.ty - a.fy;
        const len = Math.hypot(dx, dy) || 1;
        const arc = Math.sin(Math.PI * e) * clamp(a.bend, -len * 0.25, len * 0.25);
        a.x = a.fx + dx * e + (-dy / len) * arc;
        a.y = a.fy + dy * e + (dx / len) * arc;
        a.moving = t < 1 && len > 2;
        a.alpha = a.alphaFrom + (a.alphaTo - a.alphaFrom) * (a.status === "departed" ? clamp((t - 0.6) / 0.4, 0, 1) : t);
        if (!a.dur || a.dur <= 1) a.alpha = a.alphaTo;
        a.bob = motion && !a.moving && a.status === "present" ? Math.sin(now / 520 + a.seed * 20) * 0.9 : 0;
        if (a.pull && clock >= a.pull.t0 && clock <= a.pull.t1) {
          const w = Math.min(clamp((clock - a.pull.t0) / 260, 0, 1), clamp((a.pull.t1 - clock) / 260, 0, 1));
          const k = reducedMotion() ? (w > 0 ? 1 : 0) : ease(w);
          a.x += (a.pull.x - a.x) * k;
          a.y += (a.pull.y - a.y) * k;
        }
      }
    }
    function drawTies() {
      const frame = frames[index];
      if (!frame || !frame.ties) return;
      ctx.strokeStyle = "#f2f2f2";
      const focus = hoverId || selectedId;
      for (const tie of frame.ties) {
        if (tie[2] < 0.3) continue;
        const a = agentById.get(tie[0]);
        const b = agentById.get(tie[1]);
        if (!a || !b || !a.shown || !b.shown || a.status !== "present" || b.status !== "present") continue;
        const lit = focus && (a.id === focus || b.id === focus);
        ctx.globalAlpha = (lit ? 0.5 : 0.05 + 0.13 * tie[2]) * Math.min(a.alpha, b.alpha);
        ctx.lineWidth = (lit ? 1.4 : 0.6 + tie[2]) * markK;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
    }
    function drawConflicts(now) {
      const frame = frames[index];
      if (!frame || !frame.conflicts) return;
      ctx.strokeStyle = "#ef5350";
      ctx.setLineDash([5, 5]);
      ctx.lineDashOffset = reducedMotion() ? 0 : -now / 60;
      for (const key in frame.conflicts) {
        const c = frame.conflicts[key];
        if (c.resolved || c.intensity <= 0) continue;
        const a = agentById.get(c.a);
        const b = agentById.get(c.b);
        if (!a || !b || !a.shown || !b.shown) continue;
        ctx.globalAlpha = 0.35 + 0.6 * clamp(c.intensity, 0, 1);
        ctx.lineWidth = 1 + 2 * c.intensity;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
      }
      ctx.setLineDash([]);
      ctx.lineDashOffset = 0;
      ctx.globalAlpha = 1;
    }
    function drawAgents(now, motion) {
      for (const a of agents) {
        if (!a.shown || a.alpha <= 0.01) continue;
        const d = a.data;
        const x = a.x, y = a.y + (a.bob || 0);
        if (a.status === "dead") {
          const k = clamp((now - a.deadAt) / 1400, 0, 1);
          if (k < 1) { ctx.globalAlpha = 1 - k; drawDot(a, x, y, d, now, motion); }
          ctx.globalAlpha = 0.35 + 0.4 * k;
          ctx.fillStyle = "#9a9a9a";
          const m = markK;
          roundRect(ctx, x - 3 * m, y - 4 * m, 6 * m, 8 * m, 2 * m); ctx.fill();
          ctx.fillRect(x - 4.5 * m, y + 3.5 * m, 9 * m, 1.5 * m);
          ctx.globalAlpha = 1;
          continue;
        }
        ctx.globalAlpha = a.alpha;
        drawDot(a, x, y, d, now, motion);
        ctx.globalAlpha = 1;
      }
      const acting = steward.acting && steward.visible ? steward.acting.spot : null;
      if (acting && (acting.kind === "agent" || acting.kind === "pair")) {
        for (const a of acting.kind === "pair" ? [acting.a, acting.b] : [acting.a]) {
          ctx.strokeStyle = "rgba(255,236,160,0.85)";
          ctx.lineWidth = 1.3 * markK;
          ctx.beginPath(); ctx.arc(a.x, a.y + (a.bob || 0), 9 * markK, 0, TAU); ctx.stroke();
        }
      }
      const focus = selectedId || hoverId;
      if (focus) {
        const a = agentById.get(focus);
        if (a && a.shown) {
          const m = markK;
          ctx.drawImage(selectSprite, a.x - 18 * m, a.y - 18 * m, 36 * m, 36 * m);
          ctx.strokeStyle = "#fff3c4";
          ctx.lineWidth = 1.4 * m;
          ctx.beginPath(); ctx.arc(a.x, a.y + (a.bob || 0), 8.5 * m, 0, TAU); ctx.stroke();
          label(a.info.name || a.id, a.x, a.y - 10 * m - 4 * fontK);
        }
      }
      if (opts.labels) for (const a of agents) if (a.shown && a.status === "present" && a.id !== focus) label(a.info.name || a.id, a.x, a.y - 8 * markK - 4 * fontK, true);
    }
    function drawDot(a, x, y, d, now, motion) {
      const wb = d ? d.wb : 6;
      const sick = d && d.sick;
      const m = markK;
      if (a.zone === "water" && a.status === "present" && !a.moving) {
        ctx.fillStyle = "#5b4128";
        ctx.beginPath(); ctx.ellipse(x, y + 4 * m, 9 * m, 3.2 * m, 0, 0, TAU); ctx.fill();
      }
      const glow = glowSprites[clamp(Math.round(wb * 2), 0, 20)];
      const g = (sick ? 16 : 26) * m;
      ctx.drawImage(glow, x - g / 2, y - g / 2, g, g);
      if (d && d.protest) {
        const p = motion ? (now / 700 + a.seed) % 1 : 0.4;
        const alphaNow = ctx.globalAlpha;
        ctx.globalAlpha = alphaNow * (1 - p) * 0.9;
        ctx.strokeStyle = "#ff5a4f";
        ctx.lineWidth = 2 * m;
        ctx.beginPath(); ctx.arc(x, y, (7 + p * 12) * m, 0, TAU); ctx.stroke();
        ctx.globalAlpha = alphaNow;
        ctx.drawImage(protestSprite, x - 13 * m, y - 13 * m, 26 * m, 26 * m);
      }
      ctx.fillStyle = sick ? a.paleStr : a.colorStr;
      ctx.beginPath(); ctx.arc(x, y, 5 * m, 0, TAU); ctx.fill();
      ctx.strokeStyle = "rgba(0,0,0,0.55)";
      ctx.lineWidth = m;
      ctx.stroke();
      if (a.info.newcomer) { ctx.strokeStyle = "rgba(255,255,255,0.55)"; ctx.beginPath(); ctx.arc(x, y, 7 * m, 0, TAU); ctx.stroke(); }
      if (sick) { ctx.fillStyle = "#f4f4f4"; ctx.fillRect(x - 0.8 * m, y - 11 * m, 1.6 * m, 5 * m); ctx.fillRect(x - 2.5 * m, y - 9.3 * m, 5 * m, 1.6 * m); }
      if (d && d.hungry) { ctx.fillStyle = "#ff9f43"; ctx.beginPath(); ctx.arc(x + 5 * m, y - 4 * m, 1.8 * m, 0, TAU); ctx.fill(); }
      if (d && d.activity === "withdrawn") { ctx.fillStyle = "rgba(20,25,22,0.45)"; ctx.beginPath(); ctx.arc(x, y, 5.5 * m, 0, TAU); ctx.fill(); }
    }
    /** A name tag in map units, sized so that it reads at ~10px on any canvas. */
    function label(text, x, y, faint) {
      const k = fontK;
      ctx.font = `${faint ? 500 : 650} ${(faint ? 9 : 11) * k}px ${FONT}`;
      const w = ctx.measureText(text).width + 10 * k;
      ctx.fillStyle = faint ? "rgba(15,15,15,0.55)" : "rgba(15,15,15,0.85)";
      roundRect(ctx, x - w / 2, y - 11 * k, w, (faint ? 13 : 16) * k, 6 * k); ctx.fill();
      ctx.fillStyle = faint ? "rgba(222,222,222,0.75)" : "#f3f3f3";
      ctx.textAlign = "center";
      ctx.fillText(text, x, y + (faint ? -1 : 1) * k);
      ctx.textAlign = "start";
    }
    function drawEffects() {
      for (const e of effects) {
        if (clock < e.t0 || clock > e.t1) continue;
        const k = (clock - e.t0) / (e.t1 - e.t0 || 1);
        if (e.kind === "tie") {
          const fade = Math.min(1, k * 4, (1 - k) * 3);
          ctx.save();
          ctx.globalAlpha = fade;
          ctx.strokeStyle = "#ffe27a";
          ctx.shadowColor = "rgba(255,226,122,0.9)";
          ctx.shadowBlur = 12;
          ctx.lineWidth = 2.4;
          ctx.setLineDash([6, 5]);
          ctx.lineDashOffset = -clock / 40;
          ctx.beginPath(); ctx.moveTo(e.a.x, e.a.y); ctx.lineTo(e.b.x, e.b.y); ctx.stroke();
          ctx.restore();
        } else if (e.kind === "ripple") {
          for (let r = 0; r < 3; r++) {
            const kk = (k + r / 3) % 1;
            ctx.globalAlpha = (1 - kk) * 0.7;
            ctx.strokeStyle = "#ffe27a";
            ctx.lineWidth = 1.6;
            ctx.beginPath(); ctx.arc(e.x, e.y, 8 + kk * 60, 0, TAU); ctx.stroke();
          }
          ctx.globalAlpha = 1;
        } else if (e.kind === "puff") {
          ctx.globalAlpha = 1 - k;
          ctx.drawImage(rejectGlow, e.x - 22 - k * 10, e.y - 22 - k * 10, 44 + k * 20, 44 + k * 20);
          ctx.strokeStyle = "#ff6b5e";
          ctx.lineWidth = 2.5;
          const s = 6 + k * 5;
          ctx.beginPath(); ctx.moveTo(e.x - s, e.y - 16 - s); ctx.lineTo(e.x + s, e.y - 16 + s); ctx.moveTo(e.x + s, e.y - 16 - s); ctx.lineTo(e.x - s, e.y - 16 + s); ctx.stroke();
          ctx.globalAlpha = 1;
        } else if (e.kind === "gift") {
          const tx = e.a ? e.a.x : e.x + 18, ty = e.a ? e.a.y : e.y - 10;
          const x = e.x + (tx - e.x) * k;
          const y = e.y + (ty - e.y) * k - Math.sin(Math.PI * k) * 26;
          ctx.globalAlpha = Math.min(1, (1 - k) * 4);
          ctx.font = `${13 * fontK}px ${FONT}`;
          ctx.textAlign = "center";
          ctx.fillText(e.icon, x, y);
          ctx.textAlign = "start";
          ctx.globalAlpha = 1;
        } else if (e.kind === "scan") {
          ctx.strokeStyle = "rgba(160,220,255,0.8)";
          ctx.lineWidth = 1.5;
          ctx.globalAlpha = Math.min(1, (1 - k) * 3);
          ctx.beginPath(); ctx.arc(e.x, e.y, 16 * markK, clock / 200, clock / 200 + Math.PI * 1.3); ctx.stroke();
          ctx.globalAlpha = 1;
        }
      }
    }
    function sampleTrail(now) {
      if (now - steward.lastSample < 34) return;
      steward.lastSample = now;
      const n = steward.trail.length / 2;
      const prev = (steward.trailHead - 1 + n) % n;
      if (steward.trailCount && Math.hypot(steward.trail[prev * 2] - steward.x, steward.trail[prev * 2 + 1] - steward.y) < 1.2) {
        if (steward.trailCount > 0) steward.trailCount -= 1;
        return;
      }
      steward.trail[steward.trailHead * 2] = steward.x;
      steward.trail[steward.trailHead * 2 + 1] = steward.y;
      steward.trailHead = (steward.trailHead + 1) % n;
      steward.trailCount = Math.min(n, steward.trailCount + 1);
    }
    function drawSteward(now, motion) {
      if (!steward.visible || !frames.length) return;
      if (motion) sampleTrail(now);
      const n = steward.trail.length / 2;
      for (let i = 0; i < steward.trailCount; i++) {
        const idx = (steward.trailHead - 1 - i + n * 2) % n;
        const k = 1 - i / n;
        ctx.globalAlpha = 0.45 * k * k;
        ctx.fillStyle = "#ffd75a";
        ctx.beginPath(); ctx.arc(steward.trail[idx * 2], steward.trail[idx * 2 + 1] + 4, 1.2 + 2.4 * k, 0, TAU); ctx.fill();
      }
      ctx.globalAlpha = 1;
      const bounce = motion ? (steward.moving ? Math.abs(Math.sin(now / 90)) * 1.6 : steward.acting ? Math.sin(now / 160) * 0.8 : Math.sin(now / 600) * 0.5) : 0;
      ctx.save();
      ctx.translate(steward.x, steward.y);
      ctx.scale(1.4 * markK, 1.4 * markK);
      ctx.translate(-steward.x, -steward.y);
      const x = steward.x, y = steward.y - bounce;
      ctx.drawImage(stewardGlow, x - 26, y - 30, 52, 52);
      ctx.fillStyle = "rgba(0,0,0,0.4)";
      ctx.beginPath(); ctx.ellipse(steward.x, steward.y + 9, 7, 2.4, 0, 0, TAU); ctx.fill();
      // cloak
      ctx.fillStyle = "#c9a40c";
      ctx.beginPath(); ctx.moveTo(x, y - 7); ctx.quadraticCurveTo(x + 8, y + 1, x + 6.5, y + 9); ctx.lineTo(x - 6.5, y + 9); ctx.quadraticCurveTo(x - 8, y + 1, x, y - 7); ctx.fill();
      ctx.strokeStyle = "rgba(60,45,0,0.8)";
      ctx.lineWidth = 0.8;
      ctx.stroke();
      ctx.fillStyle = "#fff0a8";
      ctx.fillRect(x - 0.6, y - 4, 1.2, 12);
      // head and crown
      ctx.fillStyle = "#f6e7bf";
      ctx.beginPath(); ctx.arc(x, y - 10, 3.6, 0, TAU); ctx.fill();
      ctx.fillStyle = "#ffd23f";
      ctx.beginPath(); ctx.moveTo(x - 4, y - 13); ctx.lineTo(x - 4, y - 17); ctx.lineTo(x - 2, y - 15); ctx.lineTo(x, y - 18.5); ctx.lineTo(x + 2, y - 15); ctx.lineTo(x + 4, y - 17); ctx.lineTo(x + 4, y - 13); ctx.closePath(); ctx.fill();
      // lantern
      const flicker = motion ? 0.85 + 0.15 * Math.sin(now / 70) * Math.sin(now / 130) : 1;
      const lx = x + 8, ly = y + 1;
      ctx.globalAlpha = flicker;
      ctx.drawImage(lanternGlow, lx - 11, ly - 11, 22, 22);
      ctx.globalAlpha = 1;
      ctx.strokeStyle = "#8a6a10";
      ctx.lineWidth = 0.8;
      ctx.beginPath(); ctx.moveTo(x + 4, y - 2); ctx.lineTo(lx, ly - 3); ctx.stroke();
      ctx.fillStyle = "#ffcf6a";
      roundRect(ctx, lx - 2, ly - 2.5, 4, 5, 1); ctx.fill();
      if (steward.acting && steward.acting.action && steward.acting.action.status && steward.acting.action.status !== "done") {
        ctx.drawImage(rejectGlow, x - 14, y - 14, 28, 28);
      }
      ctx.restore();
    }
    // Bubbles are drawn in screen space so text stays legible at any map size.
    function measureBubble(b) {
      const maxW = compact ? Math.max(140, Math.min(210, cssW * 0.58)) : Math.min(240, Math.max(150, cssW * 0.32));
      const tight = cssH < 300;
      const headFont = `650 11.5px ${FONT}`;
      const bodyFont = b.kind === "say" ? `italic 12px ${FONT}` : `12px ${FONT}`;
      const lines = [];
      ctx.font = bodyFont;
      if (b.body) {
        const words = String(b.body).split(/\s+/);
        let line = "";
        const maxLines = tight ? 2 : b.kind === "thought" ? 4 : 3;
        for (const word of words) {
          const test = line ? `${line} ${word}` : word;
          if (ctx.measureText(test).width > maxW - 20 && line) {
            lines.push(line);
            line = word;
            if (lines.length >= maxLines) break;
          } else line = test;
        }
        if (lines.length < maxLines && line) lines.push(line);
        else if (line && lines.length) lines[lines.length - 1] = lines[lines.length - 1].replace(/\s*\S*$/, "") + " …";
      }
      ctx.font = headFont;
      const header = b.header ? `${b.icon ? b.icon + " " : ""}${b.header}` : b.icon || "";
      let width = header ? ctx.measureText(header).width : 0;
      ctx.font = bodyFont;
      for (const l of lines) width = Math.max(width, ctx.measureText(l).width);
      b.headerText = header;
      b.lines = lines;
      b.headFont = headFont;
      b.bodyFont = bodyFont;
      b.w = Math.min(maxW, width + 20);
      b.h = 10 + (header ? 16 : 0) + lines.length * 16;
      b.measured = true;
    }
    function drawBubbles() {
      if (!steward.visible) return;
      for (let i = bubbles.length - 1; i >= 0; i--) if (clock > bubbles[i].t1 + 50) bubbles.splice(i, 1);
      for (const b of bubbles) {
        if (clock < b.t0 || clock > b.t1) continue;
        if (!b.measured) measureBubble(b);
        const alpha = Math.min(1, (clock - b.t0) / 140, (b.t1 - clock) / 260);
        const anchor = b.anchor === "steward" ? steward : b.anchor;
        const ax = sx(anchor.x), ay = sy(anchor.y);
        const left = b.kind === "say" || b.kind === "thought";
        // The steward speaks from the left, the target answers on the right: they never overlap.
        let x = left ? ax - b.w - 2 : ax + 8;
        let y = left ? ay - b.h - (28 * scale * markK + 8) : ay - b.h - (8 * scale * markK + 10);
        x = clamp(x, 6, cssW - b.w - 6);
        y = clamp(y, 34, cssH - b.h - 6);
        ctx.globalAlpha = alpha;
        const styles = {
          say: ["rgba(34,29,8,0.95)", "#e2c21c", "#ffe9a0", "#f5ecd0"],
          thought: ["rgba(22,22,34,0.92)", "rgba(190,190,255,0.6)", "#c9c3ff", "#e3e0ff"],
          reply: ["rgba(242,238,226,0.96)", "rgba(255,255,255,0.9)", "#4a3b00", "#1d1d1d"],
          reject: ["rgba(60,14,12,0.95)", "#ef5350", "#ffb3aa", "#ffd6d0"],
        }[b.kind];
        ctx.fillStyle = styles[0];
        ctx.strokeStyle = styles[1];
        ctx.lineWidth = 1.2;
        if (b.kind === "thought") ctx.setLineDash([4, 3]);
        roundRect(ctx, x, y, b.w, b.h, 9);
        ctx.fill();
        ctx.stroke();
        ctx.setLineDash([]);
        // tail
        const tx = clamp(left ? ax - 8 : ax + 2, x + 10, x + b.w - 10);
        if (b.kind === "thought") {
          ctx.beginPath(); ctx.arc(tx, y + b.h + 6, 3, 0, TAU); ctx.fill(); ctx.stroke();
          ctx.beginPath(); ctx.arc(tx + 3, y + b.h + 13, 2, 0, TAU); ctx.fill(); ctx.stroke();
        } else {
          ctx.beginPath(); ctx.moveTo(tx - 6, y + b.h - 0.5); ctx.lineTo(tx, y + b.h + 8); ctx.lineTo(tx + 6, y + b.h - 0.5); ctx.closePath(); ctx.fill();
        }
        let ty = y + 17;
        if (b.headerText) {
          ctx.font = b.headFont;
          ctx.fillStyle = styles[2];
          ctx.fillText(b.headerText, x + 10, ty);
          ty += 16;
        }
        ctx.font = b.bodyFont;
        ctx.fillStyle = styles[3];
        for (const l of b.lines) { ctx.fillText(l, x + 10, ty); ty += 16; }
      }
      ctx.globalAlpha = 1;
    }
    function drawCaptions(now) {
      const life = Math.max(2200, 3600 / Math.max(0.5, speed));
      ctx.font = `600 12px ${FONT}`;
      for (const c of captions) {
        const age = now - c.t0;
        if (age < 0 || age > life) continue;
        const k = age / life;
        const alpha = k < 0.12 ? k / 0.12 : k > 0.75 ? (1 - k) / 0.25 : 1;
        if (!c.width) c.width = Math.min(cssW - 20, ctx.measureText(c.text).width + 22);
        const x = cssW / 2;
        const y = opts.captionsAt === "top" ? (compact ? 48 : 62) + c.slot * (compact ? 26 : 28) + k * 10 : cssH - 30 - c.slot * (compact ? 26 : 28) - k * 14;
        ctx.globalAlpha = alpha;
        ctx.fillStyle = c.bad ? "rgba(70,18,16,0.9)" : "rgba(18,18,18,0.84)";
        roundRect(ctx, x - c.width / 2, y - 15, c.width, 24, 12); ctx.fill();
        ctx.strokeStyle = c.bad ? "rgba(239,83,80,0.7)" : "rgba(181,154,0,0.55)";
        ctx.lineWidth = 1;
        ctx.stroke();
        ctx.fillStyle = c.bad ? "#ffd4cf" : "#ece6cf";
        ctx.textAlign = "center";
        ctx.fillText(c.text, x, y + 1, c.width - 16);
        ctx.textAlign = "start";
      }
      ctx.globalAlpha = 1;
    }
    function drawHud(now) {
      const frame = frames[index];
      if (collapsedLabel) {
        ctx.fillStyle = "rgba(120,10,10,0.28)";
        ctx.fillRect(0, 0, cssW, cssH);
        ctx.textAlign = "center";
        ctx.font = `800 ${Math.max(22, 34 * scale)}px ${FONT}`;
        ctx.fillStyle = "#ffb3aa";
        ctx.fillText("COLLAPSE", cssW / 2, cssH / 2 - 8);
        ctx.font = `500 14px ${FONT}`;
        ctx.fillStyle = "#f3d6d2";
        ctx.fillText(collapsedLabel, cssW / 2, cssH / 2 + 18);
        ctx.textAlign = "start";
      }
      if (!frame) return;
      const text = `${worldName ? worldName + " · " : ""}Week ${frame.week}${frame.season ? " · " + frame.season[0].toUpperCase() + frame.season.slice(1) : ""}`;
      ctx.font = `650 12px ${FONT}`;
      const w = ctx.measureText(text).width + 22;
      ctx.fillStyle = "rgba(14,14,14,0.78)";
      roundRect(ctx, 10, 10, w, 26, 13); ctx.fill();
      ctx.strokeStyle = "rgba(181,154,0,0.6)";
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.fillStyle = "#f1e7b8";
      ctx.fillText(text, 21, 27);
      if (liveMode) {
        const atLive = followLive && index >= frames.length - 1;
        const lx = 18 + w;
        ctx.fillStyle = atLive ? "rgba(70,14,12,0.85)" : "rgba(30,30,30,0.8)";
        roundRect(ctx, lx, 10, 58, 26, 13); ctx.fill();
        ctx.fillStyle = atLive ? (reducedMotion() || Math.sin(now / 300) > -0.3 ? "#ff5a4f" : "#7a2a26") : "#888";
        ctx.beginPath(); ctx.arc(lx + 14, 23, 4, 0, TAU); ctx.fill();
        ctx.fillStyle = atLive ? "#ffd6d0" : "#bbb";
        ctx.font = `700 11px ${FONT}`;
        ctx.fillText("LIVE", lx + 23, 27);
      }
    }

    // ------------------------------------------------ loop
    function loop(now) {
      raf = 0;
      if (destroyed) return;
      const dt = last ? Math.min(100, now - last) : 16;
      last = now;
      if (playing) {
        const behind = frames.length - 1 - index;
        const rate = speed * (followLive && liveMode && behind > 2 ? 2.5 : 1);
        clock += dt * rate;
        if (clock - weekStart >= weekDur) {
          if (index < frames.length - 1) {
            index += 1;
            applyFrame(index, true);
            emit("frame", index);
            updateLiveButton();
          } else if (liveMode && followLive) {
            weekStart = clock - weekDur;
          } else if (opts.loop && frames.length > 1) {
            index = 0;
            applyFrame(0, false);
            emit("frame", index);
          } else {
            playing = false;
            emit("pause", index);
            emit("end", index);
          }
        }
      }
      draw(now);
      if (playing || !reducedMotion() || agents.some((a) => a.moving)) schedule();
    }
    function schedule() {
      if (!raf && !destroyed && visible && !document.hidden) raf = requestAnimationFrame(loop);
    }
    function onVisibility() { last = 0; if (document.hidden) { cancelAnimationFrame(raf); raf = 0; } else schedule(); }

    // ------------------------------------------------ tooltip & pointer
    /** The resident nearest to a map point, within `reach` screen pixels (fingers get a wider reach). */
    function nearest(x, y, reach) {
      const r = Math.max(14, (reach || 10) / scale);
      let best = null, bestD = r * r;
      for (const a of agents) {
        if (!a.shown || a.alpha < 0.2) continue;
        const d = (a.x - x) * (a.x - x) + (a.y - y) * (a.y - y);
        if (d < bestD) { bestD = d; best = a; }
      }
      return best;
    }
    function line(parent, text, strong) {
      const div = document.createElement("div");
      if (strong) { div.style.fontWeight = "650"; div.style.color = "#f5f5f5"; div.style.fontSize = "13px"; }
      div.textContent = text;
      parent.append(div);
      return div;
    }
    function num(v, digits) { return typeof v === "number" ? v.toFixed(digits) : "–"; }
    function updateTip() {
      const id = hoverId || selectedId;
      const a = id && agentById.get(id);
      if (!a || !a.shown || !opts.interactive) { tip.style.opacity = "0"; tip.style.transform = "translateY(4px)"; return; }
      const d = a.data || {};
      tip.replaceChildren();
      line(tip, a.info.name || a.id, true);
      const meta = line(tip, `${a.info.role || "resident"} · ${a.info.factionName || a.info.faction || "no faction"}${a.info.newcomer ? " · newcomer" : ""}`);
      meta.style.color = "#a3a3a3";
      const act = line(tip, ACTIVITY_LABEL[d.activity] || d.activity || "");
      act.style.color = "#e0c84a";
      act.style.margin = "3px 0 6px";
      if (d.status === "present") {
        line(tip, `Wellbeing ${num(d.wb, 1)}/10 · Health ${num(d.hp, 0)}`);
        line(tip, `Trust ${num(d.tr, 2)} · Legitimacy ${num(d.lg, 2)}`);
        line(tip, `Stress ${num(d.st, 2)} · Lonely ${num(d.lo, 2)} · Grievance ${num(d.gr, 2)}`);
        line(tip, `Coins ${num(d.coins, 1)} · Food ${num(d.food, 1)}`);
        const flags = [d.protest && "protesting", d.sick && "sick", d.hungry && "hungry"].filter(Boolean);
        if (flags.length) line(tip, flags.join(" · ")).style.color = "#ff8a80";
        if (d.concern) line(tip, `Hidden concern: ${d.concern.kind} (${num(d.concern.intensity, 2)})`).style.color = "#c7b8ff";
      }
      positionTip(a);
    }
    function positionTip(a) {
      const rect = root.getBoundingClientRect();
      const tx = sx(a.x), ty = sy(a.y);
      const tw = tip.offsetWidth || 200;
      const th = tip.offsetHeight || 120;
      let left = tx + 14;
      let top = ty - th - 10;
      if (left + tw > rect.width - 6) left = tx - tw - 14;
      if (top < 6) top = ty + 14;
      tip.style.left = clamp(left, 6, Math.max(6, rect.width - tw - 6)) + "px";
      tip.style.top = clamp(top, 6, Math.max(6, rect.height - th - 6)) + "px";
      tip.style.opacity = "1";
      tip.style.transform = "translateY(0)";
    }
    function onDown(event) { lastPointer = event.pointerType || "mouse"; }
    function onMove(event) {
      if (event.pointerType === "touch") return;
      const p = toWorld(event.clientX, event.clientY);
      const a = nearest(p.x, p.y, 10);
      const id = a ? a.id : null;
      canvas.style.cursor = id ? "pointer" : "default";
      if (id !== hoverId) { hoverId = id; updateTip(); requestDraw(); }
    }
    function onLeave(event) { if (event && event.pointerType === "touch") return; hoverId = null; updateTip(); requestDraw(); }
    // A tap (or click) on a resident shows its card; a tap on empty ground dismisses it.
    function onClick(event) {
      const p = toWorld(event.clientX, event.clientY);
      const a = nearest(p.x, p.y, lastPointer === "mouse" ? 10 : 26);
      selectedId = a ? (selectedId === a.id ? null : a.id) : null;
      if (lastPointer !== "mouse") hoverId = null;
      emit("select", selectedId);
      updateTip();
      requestDraw();
    }
    canvas.addEventListener("pointerdown", onDown);
    canvas.addEventListener("pointermove", onMove);
    canvas.addEventListener("pointerleave", onLeave);
    canvas.addEventListener("click", onClick);

    // ------------------------------------------------ expanded (full-window) view
    let expanded = false;
    let expandButton = null;
    let expandBar = null;
    let expandParts = null;
    const restore = { overflow: "", touchAction: "" };
    function chip(text, label) {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = text;
      if (label) { b.setAttribute("aria-label", label); b.title = label; }
      Object.assign(b.style, { minWidth: "44px", height: "44px", padding: "0 12px", borderRadius: "22px", border: "1px solid rgba(226,194,28,0.55)",
        background: "rgba(22,20,10,0.88)", color: "#f5e7a6", font: `650 15px ${FONT}`, cursor: "pointer", display: "inline-flex", alignItems: "center",
        justifyContent: "center", boxShadow: "0 6px 18px rgba(0,0,0,0.45)", touchAction: "manipulation" });
      return b;
    }
    if (opts.expand) {
      expandButton = chip("⤢", "Expand the map");
      Object.assign(expandButton.style, { position: "absolute", right: "8px", bottom: "8px", zIndex: 5, minWidth: "40px", height: "40px", padding: "0", borderRadius: "12px", fontSize: "18px" });
      expandButton.addEventListener("click", () => controller.setExpanded(!expanded));
      root.append(expandButton);
    }
    function buildExpandBar() {
      expandBar = document.createElement("div");
      Object.assign(expandBar.style, { position: "absolute", left: "50%", bottom: "calc(12px + env(safe-area-inset-bottom))", transform: "translateX(-50%)", zIndex: 6,
        display: "none", alignItems: "center", gap: "8px", padding: "6px", borderRadius: "28px", background: "rgba(12,12,12,0.72)", backdropFilter: "blur(8px)" });
      const prev = chip("‹", "Previous week");
      const play = chip("▶", "Play");
      const next = chip("›", "Next week");
      const week = document.createElement("span");
      Object.assign(week.style, { color: "#f1e7b8", font: `650 13px ${FONT}`, padding: "0 8px", whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums" });
      prev.addEventListener("click", () => { controller.pause(); controller.seek(index - 1); });
      next.addEventListener("click", () => { controller.pause(); controller.seek(index + 1); });
      play.addEventListener("click", () => controller.toggle());
      const hint = document.createElement("div");
      hint.textContent = "Turn the phone sideways for a larger map";
      Object.assign(hint.style, { position: "absolute", left: "50%", top: "calc(52px + env(safe-area-inset-top))", transform: "translateX(-50%)", zIndex: 6, display: "none",
        padding: "6px 14px", borderRadius: "16px", background: "rgba(12,12,12,0.8)", color: "#d8d8d8", font: `12.5px ${FONT}`, whiteSpace: "nowrap", pointerEvents: "none" });
      expandBar.append(prev, play, week, next);
      root.append(expandBar, hint);
      expandParts = { play, week, hint, prev, next };
    }
    function syncExpandBar() {
      if (!expandParts) return;
      expandBar.style.display = expanded ? "flex" : "none";
      const frame = frames[index];
      expandParts.week.textContent = frame ? `Week ${frame.week}` : "";
      expandParts.play.textContent = playing ? "❚❚" : "▶";
      expandParts.play.setAttribute("aria-label", playing ? "Pause" : "Play");
      expandParts.prev.disabled = index <= 0;
      expandParts.next.disabled = index >= frames.length - 1;
      expandParts.hint.style.display = expanded && cssH > cssW * 1.2 && cssW < 600 ? "block" : "none";
    }
    function onExpandKey(event) {
      if (event.key !== "Escape" || !expanded) return;
      event.preventDefault();
      event.stopPropagation();
      controller.setExpanded(false);
    }
    function setExpanded(on) {
      on = Boolean(on);
      if (on === expanded) return;
      expanded = on;
      if (on) {
        if (!expandBar) buildExpandBar();
        restore.overflow = document.documentElement.style.overflow;
        restore.touchAction = root.style.touchAction;
        document.documentElement.style.overflow = "hidden";
        // Moved to <body> so that no transformed or clipped ancestor can trap the fixed layer.
        document.body.append(root);
        Object.assign(root.style, { position: "fixed", inset: "0", zIndex: "1000", width: "100vw", height: "100dvh", minHeight: "0", borderRadius: "0",
          background: "#0b0e0b", touchAction: "none" });
        root.setAttribute("role", "dialog");
        root.setAttribute("aria-modal", "true");
        root.setAttribute("aria-label", "Village map, full screen");
        document.addEventListener("keydown", onExpandKey, true);
      } else {
        document.documentElement.style.overflow = restore.overflow;
        Object.assign(root.style, { position: "relative", inset: "", zIndex: "", width: "100%", height: "100%", minHeight: "220px", borderRadius: "inherit",
          background: "#141915", touchAction: restore.touchAction || "manipulation" });
        root.removeAttribute("role");
        root.removeAttribute("aria-modal");
        root.removeAttribute("aria-label");
        element.append(root);
        document.removeEventListener("keydown", onExpandKey, true);
      }
      if (expandButton) {
        expandButton.textContent = on ? "✕" : "⤢";
        expandButton.setAttribute("aria-label", on ? "Close the full-screen map" : "Expand the map");
        expandButton.title = expandButton.getAttribute("aria-label");
        Object.assign(expandButton.style, on ? { right: "calc(12px + env(safe-area-inset-right))", bottom: "", top: "calc(12px + env(safe-area-inset-top))" } : { right: "8px", top: "", bottom: "8px" });
        if (on) expandButton.focus({ preventScroll: true });
      }
      syncExpandBar();
      resize();
      emit("expand", expanded);
    }
    document.addEventListener("visibilitychange", onVisibility);
    const resizeObserver = typeof ResizeObserver === "function" ? new ResizeObserver(resize) : null;
    if (resizeObserver) resizeObserver.observe(root); else window.addEventListener("resize", resize);
    const intersection = typeof IntersectionObserver === "function"
      ? new IntersectionObserver((entries) => { visible = entries.some((e) => e.isIntersecting); last = 0; if (visible) schedule(); else { cancelAnimationFrame(raf); raf = 0; } })
      : null;
    if (intersection) intersection.observe(root);
    resize();
    schedule();

    const controller = {
      /** Replace the content: {frames, residents, factions, recipe, live?}. */
      load(data) {
        data = data || {};
        frames = Array.isArray(data.frames) ? data.frames.slice() : [];
        const recipe = data.recipe || {};
        const world = recipe.world || {};
        capacity = (world.commons && Number(world.commons.capacity)) || 1000;
        lakeName = (world.commons && world.commons.name) || "the lake";
        worldName = world.name || "";
        factionColors = new Map();
        const factionNames = new Map();
        for (const f of data.factions || recipe.factions || []) { factionColors.set(f.id, f.color); factionNames.set(f.id, f.name); }
        residents = new Map();
        for (const r of data.residents || []) residents.set(r.id, Object.assign({}, r, { factionName: factionNames.get(r.faction) || r.faction }));
        agents.length = 0;
        agentById.clear();
        for (const r of residents.values()) agentFor(r.id);
        bgCache.clear();
        index = 0;
        playing = false;
        liveMode = Boolean(data.live);
        followLive = Boolean(opts.followLive);
        autoResume = true;
        season = (frames[0] && frames[0].season) || world.start_season || "spring";
        prevSeason = null;
        selectedId = null;
        hoverId = null;
        bubbles.length = 0;
        effects.length = 0;
        steward.x = PLACES.hall[0];
        steward.y = PLACES.hall[1];
        steward.trailCount = 0;
        steward.acting = null;
        for (const c of captions) c.t0 = -1e9;
        applyFrame(0, false);
        emit("frame", 0);
        updateLiveButton();
        if (opts.autoplay && frames.length > 1 && !reducedMotion()) controller.play();
        requestDraw();
        return controller;
      },
      /** Stream more frames in (live runs). Follows the newest week unless the viewer scrubbed back. */
      append(more) {
        const list = Array.isArray(more) ? more : more ? [more] : [];
        if (!list.length) return controller;
        for (const f of list) {
          const known = frames.findIndex((x) => x.week === f.week);
          if (known >= 0) frames[known] = f; else frames.push(f);
        }
        for (const f of list) for (const r of f.residents || []) if (!residents.has(r.id)) residents.set(r.id, { id: r.id, name: r.id });
        liveMode = true;
        if (frames.length === list.length) { index = 0; applyFrame(0, false); emit("frame", 0); }
        if (followLive && !playing && autoResume && frames.length > 1) controller.play();
        updateLiveButton();
        requestDraw();
        return controller;
      },
      play() {
        if (!frames.length) return;
        if (index >= frames.length - 1 && !opts.loop && !(liveMode && followLive)) { index = 0; applyFrame(0, false); emit("frame", 0); }
        playing = true;
        autoResume = true;
        last = 0;
        const hasActions = frames[index] && Array.isArray(frames[index].actions) && frames[index].actions.length;
        if (script && !script.played && hasActions && steward.visible) startWeek(index, true);
        else weekStart = clock - weekDur + Math.min(450, weekDur);
        emit("play", index);
        schedule();
      },
      pause() { autoResume = false; if (playing) { playing = false; emit("pause", index); } },
      toggle() { if (playing) controller.pause(); else controller.play(); },
      seek(i) {
        if (!frames.length) return;
        const next = clamp(Math.round(Number(i) || 0), 0, frames.length - 1);
        const step = Math.abs(next - index) === 1;
        index = next;
        if (liveMode) {
          const wasFollowing = followLive;
          followLive = index >= frames.length - 1;
          if (wasFollowing !== followLive) emit("live", followLive);
        }
        applyFrame(index, step);
        emit("frame", index);
        updateLiveButton();
      },
      setSpeed(x) { speed = clamp(Number(x) || 1, 0.1, 16); },
      setFollowLive(on) {
        followLive = Boolean(on);
        if (followLive) liveMode = true;
        if (followLive && frames.length) {
          if (index < frames.length - 1) { index = Math.max(0, frames.length - 2); applyFrame(index, false); emit("frame", index); }
          if (!playing) controller.play();
        }
        emit("live", followLive);
        updateLiveButton();
      },
      isFollowingLive() { return followLive; },
      setStewardVisible(on) {
        steward.visible = Boolean(on);
        if (!steward.visible) { bubbles.length = 0; effects.length = 0; for (const a of agents) a.pull = null; }
        renderNotebook(frames[index]);
        requestDraw();
      },
      setThoughts(on) { opts.thoughts = Boolean(on); },
      setNotebook(on) { opts.notebook = Boolean(on); renderNotebook(frames[index]); },
      select(id) { selectedId = id || null; updateTip(); requestDraw(); },
      on(name, fn) {
        (listeners[name] = listeners[name] || []).push(fn);
        return () => { listeners[name] = (listeners[name] || []).filter((f) => f !== fn); };
      },
      isPlaying() { return playing; },
      /** Full-window view of the map with its own week controls (Escape or ✕ closes it). */
      setExpanded(on) { setExpanded(on); },
      isExpanded() { return expanded; },
      get index() { return index; },
      get length() { return frames.length; },
      get frames() { return frames; },
      destroy() {
        if (expanded) setExpanded(false);
        destroyed = true;
        cancelAnimationFrame(raf);
        raf = 0;
        canvas.removeEventListener("pointerdown", onDown);
        canvas.removeEventListener("pointermove", onMove);
        canvas.removeEventListener("pointerleave", onLeave);
        canvas.removeEventListener("click", onClick);
        document.removeEventListener("visibilitychange", onVisibility);
        if (resizeObserver) resizeObserver.disconnect(); else window.removeEventListener("resize", resize);
        if (intersection) intersection.disconnect();
        for (const key in listeners) delete listeners[key];
        bgCache.clear();
        root.remove();
      },
    };
    return controller;
  }

  window.SocietyVillage = { mount, describeAction, replyOf, VERB_ICONS, ROLE_JOB, ZONE_OF, ACTIVITY_LABEL };
})();
