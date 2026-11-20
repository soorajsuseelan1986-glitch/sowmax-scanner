// If the login session expires, any API call returns 401: send the user back to the sign-in page.
{
  const _fetch = window.fetch.bind(window);
  window.fetch = async (...args) => {
    const r = await _fetch(...args);
    if (r.status === 401) location.href = "/login?next=" + encodeURIComponent(location.pathname + location.search);
    return r;
  };
}

const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);
const state = {
  mode: "intraday", universe: "nifty50", data: null,
  q: "", sig: "all", tags: new Set(), sort: { key: "score", dir: -1 },
  timer: null, chart: null, deepDone: false,
};

const BULL_TAGS = /Breakout|Bull|↑|Golden|Surge|Open = Low|Gap Up|52W High|Pullback|Bounce|Uptrend/;
const BEAR_TAGS = /Breakdown|Bear|↓|Death|Open = High|Gap Down|52W Low|Downtrend|Overbought/;

const fmt = (v, d = 2) => (v == null ? "—" : Number(v).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d }));
const pct = (v) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}%`);
const cls = (v) => (v > 0 ? "up" : v < 0 ? "down" : "");
const sigCls = { "Strong Buy": "sb", Buy: "b", Neutral: "n", Sell: "s", "Strong Sell": "ss" };
const tagCls = (t) => (BULL_TAGS.test(t) ? "bu" : BEAR_TAGS.test(t) ? "be" : "");
const hue = (s) => [...s].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7);
const initials = (s) => s.replace(/[^A-Z&]/g, "").slice(0, 2);
const avatar = (s, extra = "") => `<span class="avatar ${extra}" style="--h:${hue(s)}">${initials(s)}</span>`;
const chgPill = (v) => `<span class="chg ${cls(v)}">${pct(v)}</span>`;
const ICONS = {
  grid: '<svg viewBox="0 0 24 24" width="14" height="14"><path d="M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z" fill="currentColor"/></svg>',
  up: '<svg viewBox="0 0 24 24" width="14" height="14"><path d="M4 17l6-6 4 4 7-8M15 7h6v6" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  down: '<svg viewBox="0 0 24 24" width="14" height="14"><path d="M4 7l6 6 4-4 7 8M15 17h6v-6" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  star: '<svg viewBox="0 0 24 24" width="14" height="14"><path d="M12 2.5l2.9 6 6.6.9-4.8 4.6 1.2 6.5L12 17.4l-5.9 3.1 1.2-6.5L2.5 9.4l6.6-.9z" fill="currentColor"/></svg>',
};

// ---------- columns ----------
function symCell(x) {
  const sub = state.mode === "intraday"
    ? (x.rvol != null ? `Vol ${x.rvol.toFixed(1)}× avg` : "")
    : `${x.trend} trend`;
  return `<div class="symcell">${avatar(x.symbol)}<div><b>${x.symbol}</b><small>${sub}</small></div></div>`;
}
function rsiCell(x) {
  if (x.rsi == null) return "—";
  const c = x.rsi >= 70 ? "var(--down)" : x.rsi >= 55 ? "var(--up)" : x.rsi <= 30 ? "var(--up)" : x.rsi <= 45 ? "var(--down)" : "var(--muted)";
  return `<span class="rsi"><span class="rsi-bar"><i style="width:${x.rsi}%;background:${c}"></i></span>${x.rsi.toFixed(1)}</span>`;
}
const planCell = (x) => (x.sl == null ? `<span class="muted">—</span>`
  : `<span class="down">${fmt(x.sl)}</span> <span class="muted">→</span> <span class="up">${fmt(x.target)}</span>`);

const COLS = {
  intraday: [
    { k: "symbol", t: "Stock", r: symCell },
    { k: "ltp", t: "LTP", n: 1, r: (x) => fmt(x.ltp) },
    { k: "chg", t: "Change", n: 1, r: (x) => chgPill(x.chg) },
    { k: "score", t: "Signal", r: signalCell },
    { k: "tags", t: "Setups", r: tagsCell, cls: "tagcell", nosort: 1 },
    { k: "vwapd", t: "vs VWAP", n: 1, v: (x) => (x.vwap ? (x.ltp / x.vwap - 1) * 100 : null), r: (x) => { const d = x.vwap ? (x.ltp / x.vwap - 1) * 100 : null; return `<span class="${cls(d)}">${pct(d)}</span>`; } },
    { k: "rsi", t: "RSI", n: 1, r: rsiCell },
    { k: "rvol", t: "Rel Vol", n: 1, r: (x) => (x.rvol == null ? "—" : `<span class="${x.rvol >= 1.5 ? "up" : ""}">${x.rvol.toFixed(2)}×</span>`) },
    { k: "sl", t: "Stop → Target", n: 1, r: planCell, cls: "plancell" },
  ],
  swing: [
    { k: "symbol", t: "Stock", r: symCell },
    { k: "ltp", t: "Close", n: 1, r: (x) => fmt(x.ltp) },
    { k: "chg", t: "1D", n: 1, r: (x) => chgPill(x.chg) },
    { k: "chg_1w", t: "1W", n: 1, r: (x) => `<span class="${cls(x.chg_1w)}">${pct(x.chg_1w)}</span>` },
    { k: "chg_1m", t: "1M", n: 1, r: (x) => `<span class="${cls(x.chg_1m)}">${pct(x.chg_1m)}</span>` },
    { k: "score", t: "Signal", r: signalCell },
    { k: "tags", t: "Setups", r: tagsCell, cls: "tagcell", nosort: 1 },
    { k: "rsi", t: "RSI", n: 1, r: rsiCell },
    { k: "adx", t: "ADX", n: 1, r: (x) => `<span class="${x.adx > 25 ? "" : "muted"}">${fmt(x.adx, 1)}</span>` },
    { k: "from_hi52", t: "From 52WH", n: 1, r: (x) => pct(x.from_hi52) },
    { k: "sl", t: "Stop → Target", n: 1, r: planCell, cls: "plancell" },
  ],
};

function signalCell(x) {
  const bars = x.checks.map((c) => `<i class="${c.v > 0 ? "u" : c.v < 0 ? "d" : ""}"></i>`).join("");
  return `<span class="sig"><span class="pill ${sigCls[x.signal]}">${x.signal}</span><span class="meter" title="Score ${x.score}/${x.max_score}">${bars}</span></span>`;
}
function tagsCell(x) {
  return `<div class="tags">${x.tags.map((t) => `<span class="tag ${tagCls(t)}">${t}</span>`).join("")}</div>`;
}

// ---------- data ----------
async function load(force = false) {
  if (state.mode === "magic") return loadMagic();
  const btn = $("#refresh");
  btn.disabled = true;
  btn.querySelector("span").textContent = "Scanning…";
  $("#loading").hidden = false;
  try {
    const r = await fetch(`/api/scan/${state.mode}?universe=${state.universe}${force ? "&force=1" : ""}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    if (d.mode !== state.mode || d.universe !== state.universe) return; // changed mid-request
    state.data = d;
    renderAll();
    const deep = new URLSearchParams(location.search).get("stock");
    if (deep && !state.deepDone) { state.deepDone = true; openDrawer(deep.toUpperCase()); }
  } catch (e) {
    $("#foot").textContent = `Scan failed: ${e.message}. Check that the server is running.`;
  } finally {
    btn.disabled = false;
    btn.querySelector("span").textContent = "Scan now";
    $("#loading").hidden = true;
  }
}

function renderAll() {
  const d = state.data;
  renderHeader(d);
  renderStats(d.results);
  renderPicks(d.results);
  renderChips(d.results);
  renderTable();
  const t = new Date(d.updated);
  $("#foot").textContent = `${d.universe_name} · ${d.results.length} stocks scanned in ${d.elapsed}s · updated ${t.toLocaleTimeString("en-IN")}` +
    (d.failed.length ? ` · no data: ${d.failed.join(", ")}` : "");
}

function spark(pts, prev, up) {
  if (!pts || pts.length < 2) return "";
  const all = prev != null ? [...pts, prev] : pts;
  const lo = Math.min(...all), hi = Math.max(...all), W = 200, H = 44, pad = 4;
  const y = (v) => H - pad - ((v - lo) / (hi - lo || 1)) * (H - pad * 2);
  const line = pts.map((v, i) => `${((i / (pts.length - 1)) * W).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const col = up ? "var(--up)" : "var(--down)";
  const id = "sg" + Math.random().toString(36).slice(2, 7);
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">
    <defs><linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${col}" stop-opacity=".28"/><stop offset="1" stop-color="${col}" stop-opacity="0"/></linearGradient></defs>
    ${prev != null ? `<line x1="0" x2="${W}" y1="${y(prev)}" y2="${y(prev)}" stroke="var(--muted)" stroke-dasharray="2 3" stroke-width="1" opacity=".5" vector-effect="non-scaling-stroke"/>` : ""}
    <polygon points="0,${H} ${line} ${W},${H}" fill="url(#${id})"/>
    <polyline points="${line}" fill="none" stroke="${col}" stroke-width="1.8" vector-effect="non-scaling-stroke" stroke-linejoin="round"/></svg>`;
}

function renderHeader(d) {
  if (d.indices.length) {
    $("#indices").innerHTML = d.indices.map((i) =>
      `<div class="idx"><div class="nm">${i.name}</div><div class="v">${fmt(i.ltp)}</div>
       <div class="c ${cls(i.chg)}">${i.pts > 0 ? "+" : ""}${fmt(i.pts)} (${pct(i.chg)})</div>${spark(i.spark, i.prev, i.chg >= 0)}</div>`).join("");
  }
  const m = $("#mkt");
  m.className = `mkt ${d.market.status}`;
  m.querySelector("span").textContent = d.market.label;
}

function renderStats(rs) {
  const count = (f) => rs.filter(f).length;
  const bull = count((x) => x.signal.includes("Buy")), bear = count((x) => x.signal.includes("Sell"));
  const adv = count((x) => x.chg > 0), dec = count((x) => x.chg < 0);
  const items = [
    ["Scanned", rs.length, "", ICONS.grid, ""],
    ["Bullish", bull, "up", ICONS.up, "u"],
    ["Bearish", bear, "down", ICONS.down, "d"],
    ["Strong Buy", count((x) => x.signal === "Strong Buy"), "up", ICONS.star, "u"],
    ["Strong Sell", count((x) => x.signal === "Strong Sell"), "down", ICONS.star, "d"],
  ];
  const ratio = adv + dec ? adv / (adv + dec) : 0.5;
  const mood = ratio >= 0.65 ? ["Bullish", "up"] : ratio >= 0.55 ? ["Mildly bullish", "up"] : ratio > 0.45 ? ["Mixed", "muted"] : ratio > 0.35 ? ["Mildly bearish", "down"] : ["Bearish", "down"];
  $("#stats").innerHTML = items.map(([k, v, c, ic, icc]) =>
    `<div class="stat"><div class="k"><span class="ic ${icc}">${ic}</span>${k}</div><div class="v ${c}">${v}</div></div>`).join("") +
    `<div class="stat sentiment"><div class="k">Market breadth</div>
      <div class="v"><span class="up">${adv}</span><span class="muted" style="font-size:16px">/</span><span class="down">${dec}</span><span class="lab ${mood[1]}">${mood[0]}</span></div>
      <div class="gauge"><div style="width:${ratio * 100}%"></div></div>
      <div class="gauge-l"><span class="up">${Math.round(ratio * 100)}% advancing</span><span class="down">${Math.round((1 - ratio) * 100)}% declining</span></div></div>`;
}

function rank(a, b) {
  const m = (x) => (state.mode === "intraday" ? Math.abs(x.chg) + (x.rvol || 0) : Math.abs(x.chg_1w || 0) + (x.adx || 0) / 10);
  return Math.abs(b.score) - Math.abs(a.score) || b.tags.length - a.tags.length || m(b) - m(a);
}
function renderPicks(rs) {
  const bulls = rs.filter((x) => x.signal.includes("Buy")).sort(rank).slice(0, 4);
  const bears = rs.filter((x) => x.signal.includes("Sell")).sort(rank).slice(0, 4);
  $("#picksWrap").hidden = !bulls.length && !bears.length;
  const card = (x, side) => {
    const p = Math.round((Math.abs(x.score) / x.max_score) * 100);
    return `<button class="pick ${side}" data-s="${x.symbol}">
      <div class="pick-top">
        <div class="pick-id">${avatar(x.symbol)}<div><b>${x.symbol}</b><small class="${side === "bull" ? "up" : "down"}">${x.signal}</small></div></div>
        <div class="ring" style="--p:${p};--col:var(--${side === "bull" ? "up" : "down"})"><span>${x.score > 0 ? "+" : ""}${x.score}</span></div>
      </div>
      <div class="pick-px"><span class="num">₹${fmt(x.ltp)}</span>${chgPill(x.chg)}</div>
      <div class="tags">${x.tags.slice(0, 3).map((t) => `<span class="tag ${tagCls(t)}">${t}</span>`).join("") || '<span class="muted" style="font-size:12px">Trend-following signal</span>'}</div>
    </button>`;
  };
  $("#picks").innerHTML =
    (bulls.length ? `<div class="picks-row-label up">▲ Long ideas</div>` + bulls.map((x) => card(x, "bull")).join("") : "") +
    (bears.length ? `<div class="picks-row-label down">▼ Short ideas</div>` + bears.map((x) => card(x, "bear")).join("") : "");
}

function renderChips(rs) {
  const counts = {};
  rs.forEach((x) => x.tags.forEach((t) => (counts[t] = (counts[t] || 0) + 1)));
  [...state.tags].forEach((t) => { if (!counts[t]) state.tags.delete(t); });
  const sorted = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  $("#tagChips").innerHTML = sorted.map(([t, n]) =>
    `<button class="chip ${tagCls(t)} ${state.tags.has(t) ? "on" : ""}" data-t="${t}">${t}<b>${n}</b></button>`).join("");
}

function filtered() {
  const q = state.q.trim().toUpperCase();
  const col = COLS[state.mode].find((c) => c.k === state.sort.key);
  const val = col && col.v ? col.v : (x) => x[state.sort.key];
  return state.data.results
    .filter((x) => !q || x.symbol.includes(q))
    .filter((x) => ({ all: true, bull: x.signal.includes("Buy"), bear: x.signal.includes("Sell"), strong: x.signal.startsWith("Strong") })[state.sig])
    .filter((x) => [...state.tags].every((t) => x.tags.includes(t)))
    .sort((a, b) => {
      const va = val(a), vb = val(b);
      if (va == null) return 1; if (vb == null) return -1;
      if (typeof va === "string") return state.sort.dir * va.localeCompare(vb);
      if (state.sort.key === "score" && va === vb) return state.sort.dir * (a.chg - b.chg) * Math.sign(va || 1);
      return state.sort.dir * (va - vb);
    });
}

function renderTable() {
  if (!state.data) return;
  const cols = COLS[state.mode];
  $("#thead").innerHTML = `<tr>${cols.map((c) =>
    `<th data-k="${c.k}" class="${c.n ? "n" : ""} ${state.sort.key === c.k ? "sorted" + (state.sort.dir > 0 ? " asc" : "") : ""}">${c.t}</th>`).join("")}</tr>`;
  const rows = filtered();
  $("#tbody").innerHTML = rows.map((x, i) =>
    `<tr data-s="${x.symbol}" style="animation-delay:${Math.min(i, 20) * 12}ms">${cols.map((c) => `<td class="${c.n ? "n" : ""} ${c.cls || ""}">${c.r(x)}</td>`).join("")}</tr>`).join("");
  $("#empty").hidden = rows.length > 0;
  $("#count").textContent = `${rows.length} of ${state.data.results.length} stocks`;
}

// ---------- drawer ----------
function openDrawer(sym) {
  const x = state.data && state.data.results.find((r) => r.symbol === sym);
  if (!x) return;
  $("#dAv").outerHTML = `<span class="avatar lg" id="dAv" style="--h:${hue(sym)}">${initials(sym)}</span>`;
  $("#dSym").textContent = sym;
  $("#dSub").innerHTML = `<span class="pill ${sigCls[x.signal]}">${x.signal}</span><span class="muted">Score ${x.score > 0 ? "+" : ""}${x.score} / ${x.max_score} · ${state.mode === "intraday" ? "Intraday scan (5 min)" : "Swing scan (daily)"}</span>`;
  $("#dPrice").innerHTML = `<span class="num">₹${fmt(x.ltp)}</span>${chgPill(x.chg)}`;
  $("#dChecks").innerHTML = x.checks.map((c) => {
    const k = c.v > 0 ? "u" : c.v < 0 ? "d" : "z";
    return `<li><span class="ic ${k}">${c.v > 0 ? "▲" : c.v < 0 ? "▼" : "–"}</span><span class="l">${c.label}</span>
      <span class="vv ${c.v > 0 ? "up" : c.v < 0 ? "down" : "muted"}">${c.v > 0 ? "Bullish" : c.v < 0 ? "Bearish" : "Neutral"}</span><span class="dt">${c.detail}</span></li>`;
  }).join("");
  const side = x.score > 0 ? "Long" : x.score < 0 ? "Short" : null;
  const risk = x.sl != null ? Math.abs(x.entry - x.sl) : 0;
  $("#dPlan").innerHTML = side
    ? `<div class="box"><div class="k">${side} entry</div><div class="v">${fmt(x.entry)}</div></div>
       <div class="box sl"><div class="k">Stop loss</div><div class="v down">${fmt(x.sl)}</div></div>
       <div class="box tg"><div class="k">Target</div><div class="v up">${fmt(x.target)}</div></div>
       <div class="rr"><div class="rr-bar"><i></i><i></i></div>
         <div class="rr-l"><span class="down">Risk ₹${fmt(risk)} (${(risk / x.entry * 100).toFixed(2)}%)</span><span class="up">Reward ₹${fmt(risk * 2)}</span></div></div>
       <p class="note">${state.mode === "intraday" ? "Stop is set from the Supertrend line and ATR." : "Stop is set from the 10-day swing low/high, kept within 1–3× ATR."}</p>`
    : `<p class="note">No clear direction, so there is no trade plan.</p>`;
  $("#dTags").innerHTML = x.tags.length ? x.tags.map((t) => `<span class="tag ${tagCls(t)}">${t}</span>`).join("") : `<span class="muted">None</span>`;

  $("#drawer").classList.add("open");
  $("#drawer").setAttribute("aria-hidden", "false");
  $("#drawerBg").hidden = false;
  drawChart(sym, x);
}

function closeDrawer() {
  if ($("#chartCard").classList.contains("fs")) { toggleFullscreen(false); return; }
  $("#drawer").classList.remove("open");
  $("#drawer").setAttribute("aria-hidden", "true");
  $("#drawerBg").hidden = true;
}

// ---------- chart ----------
const IND = [
  { k: "ma", t: "EMA", c: "#f5a524" },
  { k: "vwap", t: "VWAP", c: "#22b8f0" },
  { k: "st", t: "Supertrend", c: "#2fd897" },
  { k: "bb", t: "Bollinger", c: "#a78bfa" },
  { k: "sig", t: "Signals", c: "#ff8a3d" },
  { k: "lvl", t: "Levels", c: "#94a3b8" },
  { k: "plan", t: "SL / Target", c: "#ff6b6f" },
];
const OVERLAY_STYLE = {
  "EMA 9": { ind: "ma", color: "#f5a524" }, "EMA 21": { ind: "ma", color: "#9b85ff" },
  "EMA 20": { ind: "ma", color: "#f5a524" }, "EMA 50": { ind: "ma", color: "#9b85ff" }, "EMA 200": { ind: "ma", color: "#22b8f0", width: 2.5 },
  VWAP: { ind: "vwap", color: "#22b8f0", width: 2, style: 0 },
  Supertrend: { ind: "st", color: "var(--up)", width: 1.5 },
  "BB upper": { ind: "bb", color: "#a78bfa", width: 1, style: 2 }, "BB mid": { ind: "bb", color: "#a78bfa", width: 1, style: 1 }, "BB lower": { ind: "bb", color: "#a78bfa", width: 1, style: 2 },
};
const TF_LABEL = { "1m": "1 min", "5m": "5 min", "15m": "15 min", "30m": "30 min", "1h": "1 hour", "1d": "Daily", "1wk": "Weekly" };
const TF_BARS = { "1m": 240, "5m": 150, "15m": 130, "30m": 120, "1h": 150, "1d": 130, "1wk": 120 };
const chartCache = new Map();
const prefs = (() => {
  const def = { tf: { intraday: "5m", swing: "1d" }, ind: { ma: true, vwap: true, st: true, bb: false, sig: true, lvl: true, plan: true }, osc: "rsi" };
  try { const p = JSON.parse(localStorage.getItem("chartPrefs")); if (p) return { ...def, ...p, ind: { ...def.ind, ...p.ind }, tf: { ...def.tf, ...p.tf } }; } catch {}
  return def;
})();
const savePrefs = () => { try { localStorage.setItem("chartPrefs", JSON.stringify(prefs)); } catch {} };
let current = null; // { sym, x }

const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function fmtTime(t, intraday) {
  const d = new Date(t * 1000); // times are already shifted to IST, so read them as UTC
  const date = `${d.getUTCDate()} ${MON[d.getUTCMonth()]} '${String(d.getUTCFullYear()).slice(2)}`;
  return intraday ? `${date} · ${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}` : date;
}
const fmtVol = (v) => (v >= 1e7 ? (v / 1e7).toFixed(2) + " Cr" : v >= 1e5 ? (v / 1e5).toFixed(2) + " L" : v >= 1e3 ? (v / 1e3).toFixed(1) + " K" : String(Math.round(v)));

function renderChartControls() {
  const tf = prefs.tf[state.mode];
  $$("#tfGroup button").forEach((b) => b.classList.toggle("on", b.dataset.tf === tf));
  $$("#oscGroup button").forEach((b) => b.classList.toggle("on", b.dataset.osc === prefs.osc));
  const intraday = !["1d", "1wk"].includes(tf);
  $("#indGroup").innerHTML = IND.filter((i) => !(i.k === "vwap" && (!intraday || tf === "1h")))
    .map((i) => `<button class="ind ${prefs.ind[i.k] ? "on" : ""}" data-ind="${i.k}" style="--c:${i.c}"><i></i>${i.t}</button>`).join("");
}

async function drawChart(sym, x) {
  current = { sym, x };
  renderChartControls();
  const tf = prefs.tf[state.mode];
  const key = `${sym}|${tf}`;
  const el = $("#chart");
  if (state.chart) { state.chart.remove(); state.chart = null; }
  $("#ohlc").innerHTML = "";
  let d = chartCache.get(key);
  if (!d || Date.now() - d._t > (tf === "1d" || tf === "1wk" ? 600e3 : 60e3)) {
    el.innerHTML = `<div class="loading"><div class="spinner"></div><span>Loading ${TF_LABEL[tf]} chart…</span></div>`;
    try {
      const r = await fetch(`/api/chart/${encodeURIComponent(sym)}?tf=${tf}`);
      if (!r.ok) throw new Error();
      d = await r.json();
      d._t = Date.now();
      chartCache.set(key, d);
    } catch { el.innerHTML = `<div class="empty">Chart unavailable for ${TF_LABEL[tf]}</div>`; return; }
    if (!current || current.sym !== sym || prefs.tf[state.mode] !== tf) return; // user moved on
  }
  el.innerHTML = "";
  buildChart(el, d, x);
}

function buildChart(el, d, x) {
  const L = LightweightCharts;
  const css = getComputedStyle(document.documentElement);
  const v = (n) => css.getPropertyValue(n).trim();
  const col = (c) => (c.startsWith("var(") ? v(c.slice(4, -1)) : c);
  const UP = v("--up"), DN = v("--down");
  const intraday = d.intraday;

  const chart = L.createChart(el, {
    autoSize: true,
    layout: { attributionLogo: false, background: { color: "transparent" }, textColor: v("--muted"), fontFamily: "'Plus Jakarta Sans', -apple-system, system-ui, sans-serif", fontSize: 11, panes: { separatorColor: v("--line"), separatorHoverColor: v("--line") } },
    grid: { vertLines: { color: v("--line-2") }, horzLines: { color: v("--line-2") } },
    rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.14, bottom: 0.08 } },
    timeScale: { borderVisible: false, timeVisible: intraday, secondsVisible: false, rightOffset: 6 },
    crosshair: { mode: L.CrosshairMode.Normal },
    localization: { timeFormatter: (t) => fmtTime(t, intraday), priceFormatter: (p) => p.toFixed(2) },
  });
  state.chart = chart;

  const plan = prefs.ind.plan && x && x.sl != null ? x : null;
  const cs = chart.addSeries(L.CandlestickSeries, {
    upColor: UP, downColor: DN, borderVisible: false, wickUpColor: UP, wickDownColor: DN,
    autoscaleInfoProvider: (orig) => {
      const r = orig();
      if (r && plan) {
        r.priceRange.minValue = Math.min(r.priceRange.minValue, plan.sl, plan.target);
        r.priceRange.maxValue = Math.max(r.priceRange.maxValue, plan.sl, plan.target);
      }
      return r;
    },
  });
  cs.setData(d.candles);

  const vs = chart.addSeries(L.HistogramSeries, { priceScaleId: "vol", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false });
  chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.84, bottom: 0 } });
  vs.setData(d.volume.map((b) => ({ time: b.time, value: b.value, color: b.up ? v("--up-line") : v("--down-line") })));

  const legendSeries = [];
  Object.entries(d.overlays).forEach(([name, pts]) => {
    const st = OVERLAY_STYLE[name]; if (!st || !prefs.ind[st.ind]) return;
    const s = chart.addSeries(L.LineSeries, { color: col(st.color), lineWidth: st.width || 1.5, lineStyle: st.style || 0, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false });
    s.setData(name === "Supertrend" ? pts.map((p) => ({ time: p.time, value: p.value, color: p.up ? UP : DN })) : pts);
    if (name !== "Supertrend" && !name.startsWith("BB")) legendSeries.push([name, s, col(st.color)]);
  });

  if (prefs.ind.sig && d.markers.length) {
    L.createSeriesMarkers(cs, d.markers.map((m) => m.side === "buy"
      ? { time: m.time, position: "belowBar", color: UP, shape: "arrowUp", text: "BUY" }
      : { time: m.time, position: "aboveBar", color: DN, shape: "arrowDown", text: "SELL" }));
  }
  if (prefs.ind.lvl) {
    Object.entries(d.levels).forEach(([k, p]) => p != null && cs.createPriceLine({ price: p, color: "#94a3b8", lineWidth: 1, lineStyle: 1, axisLabelVisible: true, title: k }));
  }
  if (plan) {
    cs.createPriceLine({ price: plan.sl, color: DN, lineWidth: 1.5, lineStyle: 2, title: "SL" });
    cs.createPriceLine({ price: plan.target, color: UP, lineWidth: 1.5, lineStyle: 2, title: "Target" });
  }

  // lower pane
  let oscSeries = null;
  if (prefs.osc === "rsi") {
    oscSeries = chart.addSeries(L.LineSeries, { color: "#a78bfa", lineWidth: 1.5, lastValueVisible: true, priceLineVisible: false, crosshairMarkerVisible: false }, 1);
    oscSeries.setData(d.osc.rsi);
    [[70, DN], [50, v("--muted")], [30, UP]].forEach(([p, c]) => oscSeries.createPriceLine({ price: p, color: c, lineWidth: 1, lineStyle: 2, axisLabelVisible: false }));
  } else if (prefs.osc === "macd") {
    const h = chart.addSeries(L.HistogramSeries, { lastValueVisible: false, priceLineVisible: false }, 1);
    h.setData(d.osc.macd_hist.map((p) => ({ ...p, color: p.value >= 0 ? v("--up-line") : v("--down-line") })));
    oscSeries = chart.addSeries(L.LineSeries, { color: "#22b8f0", lineWidth: 1.5, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false }, 1);
    oscSeries.setData(d.osc.macd);
    const sg = chart.addSeries(L.LineSeries, { color: "#f5a524", lineWidth: 1.5, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false }, 1);
    sg.setData(d.osc.macd_signal);
  }
  if (oscSeries) { try { chart.panes()[1].setHeight(Math.round(el.clientHeight * 0.22)); } catch {} }

  const n = d.candles.length;
  chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - TF_BARS[d.tf]), to: n + 5 });

  // OHLC legend that follows the crosshair
  const idx = new Map(d.candles.map((c, i) => [c.time, i]));
  const volAt = new Map(d.volume.map((b) => [b.time, b.value]));
  const oscAt = new Map((prefs.osc === "rsi" ? d.osc.rsi : prefs.osc === "macd" ? d.osc.macd : []).map((p) => [p.time, p.value]));
  const overAt = legendSeries.map(([name, , c]) => [name, c, new Map(d.overlays[name].map((p) => [p.time, p.value]))]);
  const paint = (t) => {
    const i = idx.has(t) ? idx.get(t) : n - 1;
    const c = d.candles[i], prev = d.candles[i - 1];
    const ch = prev ? (c.close / prev.close - 1) * 100 : 0;
    const k = ch >= 0 ? "up" : "down";
    $("#ohlc").innerHTML = `
      <div class="r1"><span>${d.symbol}</span><span class="tfb">${TF_LABEL[d.tf]}</span><span class="muted" style="font-size:11.5px">NSE · IST</span><span class="when">${fmtTime(c.time, intraday)}</span></div>
      <div class="r2"><span>O<b class="${k}">${fmt(c.open)}</b></span><span>H<b class="${k}">${fmt(c.high)}</b></span><span>L<b class="${k}">${fmt(c.low)}</b></span><span>C<b class="${k}">${fmt(c.close)}</b></span><span><b class="${k}">${ch >= 0 ? "+" : ""}${ch.toFixed(2)}%</b></span><span>Vol<b>${fmtVol(volAt.get(c.time) || 0)}</b></span></div>
      <div class="r3">${overAt.map(([nm, cc, m]) => `<span style="--c:${cc}">${nm} ${m.has(c.time) ? fmt(m.get(c.time)) : "—"}</span>`).join("")}${oscAt.size ? `<span style="--c:#a78bfa">${prefs.osc.toUpperCase()} ${oscAt.has(c.time) ? oscAt.get(c.time).toFixed(prefs.osc === "rsi" ? 1 : 2) : "—"}</span>` : ""}</div>`;
  };
  paint(null);
  chart.subscribeCrosshairMove((p) => paint(p && p.time != null ? p.time : null));
}

function redrawFromCache() { if (current) drawChart(current.sym, current.x); }

function toggleFullscreen(force) {
  const c = $("#chartCard");
  const on = force != null ? force : !c.classList.contains("fs");
  c.classList.toggle("fs", on);
}

// ---------- magic indicator ----------
const mg = { sel: null, data: null, view: "option", chart: null, ltp: {} };
const SIG_TXT = { CALL: "▲ BUY CALL", PUT: "▼ BUY PUT", WAIT: "◷ WAIT" };

async function loadMagic() {
  const btn = $("#refresh");
  btn.disabled = true; btn.querySelector("span").textContent = "Scanning…";
  try {
    const r = await fetch("/api/magic");
    const d = await r.json();
    renderHeader({ indices: d.bar || [], market: d.market });
    renderMagicCards(d.indices);
    $("#foot").textContent = `Magic Indicator · updated ${new Date().toLocaleTimeString("en-IN")}`;
    if (mg.sel) await loadMagicDetail(mg.sel, true);
  } catch (e) {
    $("#foot").textContent = `Magic Indicator failed: ${e.message}`;
  } finally {
    btn.disabled = false; btn.querySelector("span").textContent = "Scan now";
  }
}

function renderMagicCards(list) {
  $("#mgCards").innerHTML = list.map((i) => {
    const pos = (i.score + 100) / 2;
    return `<button class="mg-card ${mg.sel === i.key ? "sel" : ""}" data-k="${i.key}">
      <div class="top"><div><div class="nm">${i.name}</div><div class="ex">${i.exchange} · Index options</div></div>
        <span class="sigbadge ${i.signal}">${SIG_TXT[i.signal]}</span></div>
      <div class="px">${fmt(i.ltp)}</div>
      <div class="ch ${cls(i.chg)}">${i.pts > 0 ? "+" : ""}${fmt(i.pts)} (${pct(i.chg)})</div>
      ${spark(i.spark, i.prev, i.chg >= 0).replace("<svg", '<svg class="sp"')}
      <div class="meter-row"><span class="muted">MAGIC</span><span class="score-bar"><i style="left:${pos}%"></i></span><span class="${i.score >= 40 ? "up" : i.score <= -40 ? "down" : "muted"}">${i.score > 0 ? "+" : ""}${i.score}</span></div>
    </button>`;
  }).join("");
}

async function loadMagicDetail(key, quiet = false) {
  mg.sel = key;
  if (document.body.classList.contains("fullchart")) history.replaceState(null, "", `/?mode=magic&index=${key}&view=${mg.view}&full=1`);
  $$(".mg-card").forEach((c) => c.classList.toggle("sel", c.dataset.k === key));
  $("#mgEmpty").hidden = true;
  if (!quiet) $("#mgLoading").hidden = false;
  try {
    const r = await fetch(`/api/magic/${key}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    if (mg.sel !== key) return;
    mg.data = d;
    renderMagicDetail(d);
    if (!quiet && !document.body.classList.contains("fullchart")) $("#mgDetail").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) {
    $("#foot").textContent = `Could not load ${key}: ${e.message}`;
  } finally {
    $("#mgLoading").hidden = true;
  }
}

function dial(score) {
  const a = Math.PI * (1 - (score + 100) / 200); // 180° (−100) … 0° (+100)
  const x = 60 + 46 * Math.cos(a), y = 60 - 46 * Math.sin(a);
  const c = score >= 40 ? "var(--up)" : score <= -40 ? "var(--down)" : "#e6a023";
  return `<div class="dial"><svg viewBox="0 0 120 68">
      <defs><linearGradient id="dg" x1="0" x2="1"><stop offset="0" stop-color="var(--down)"/><stop offset=".5" stop-color="#e6a023"/><stop offset="1" stop-color="var(--up)"/></linearGradient></defs>
      <path d="M14 60 A46 46 0 0 1 106 60" fill="none" stroke="var(--line)" stroke-width="10" stroke-linecap="round"/>
      <path d="M14 60 A46 46 0 0 1 106 60" fill="none" stroke="url(#dg)" stroke-width="10" stroke-linecap="round" opacity=".9"/>
      <line x1="60" y1="60" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}" stroke="var(--text)" stroke-width="3" stroke-linecap="round"/>
      <circle cx="60" cy="60" r="5" fill="var(--text)"/></svg>
    <div class="dv" style="color:${c}">${score > 0 ? "+" : ""}${score}</div><div class="dl">Magic score</div></div>`;
}

function renderMagicDetail(d) {
  $("#mgDetail").hidden = false;
  const p = d.plan;
  const ratio = mg.ltp[d.symbol] ? mg.ltp[d.symbol] / p.entry : 1;
  const sc = (v) => v * ratio;
  const pc = (v) => `${v >= p.entry ? "+" : ""}${((v / p.entry - 1) * 100).toFixed(0)}%`;
  const exp = new Date(d.expiry + "T00:00:00");
  const expTxt = exp.toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" });

  const w = $("#mgWait");
  w.hidden = d.signal !== "WAIT";
  w.innerHTML = `<span>⚠️</span><div><b>No confirmed signal yet.</b> The Magic score is ${d.score > 0 ? "+" : ""}${d.score}; it needs to pass ±${d.threshold} to trigger.
    The ${d.kind === "CE" ? "CALL" : "PUT"} setup it leans towards is shown below for reference only. Wait for the score to cross the threshold before entering.</div>`;

  $("#mgReco").className = `card mg-reco ${d.kind}`;
  $("#mgReco").innerHTML = `
    <div class="reco-head">
      <div>
        <span class="sigbadge ${d.signal}">${SIG_TXT[d.signal]}</span>
        <div class="reco-sym">${d.key} ${d.strike}<span class="k ${d.kind}">${d.kind}</span></div>
        <div class="reco-meta"><span>Exp ${expTxt} · ${d.days_to_expiry}d</span><span>Lot ${d.lot}</span><span>Δ ${d.delta.toFixed(2)}</span><span>IV ${d.iv}%</span><span>Spot ${fmt(d.spot)}</span></div>
      </div>
      ${dial(d.score)}
    </div>
    <div class="levels">
      <div class="lv buy"><div class="k"><span>Buy above</span><span>${mg.ltp[d.symbol] ? "your LTP" : d.source === "live" ? "live LTP" : "model premium"}</span></div><div class="v">₹${fmt(sc(p.entry))}</div><div class="s">Index at ${fmt(d.spot)}</div></div>
      <div class="lv sl"><div class="k"><span>Stop-loss</span><span class="pc">${pc(p.sl)}</span></div><div class="v">₹${fmt(sc(p.sl))}</div><div class="s">Index ${fmt(p.idx_sl)}</div></div>
      <div class="lv tg"><div class="k"><span>Target 1</span><span class="pc">${pc(p.t1)}</span></div><div class="v">₹${fmt(sc(p.t1))}</div><div class="s">Index ${fmt(p.idx_t1)}</div></div>
      <div class="lv tg" style="grid-column:1/-1"><div class="k"><span>Target 2 (runner)</span><span class="pc">${pc(p.t2)}</span></div><div class="v">₹${fmt(sc(p.t2))}</div><div class="s">Index ${fmt(p.idx_t2)}</div></div>
    </div>
    <div class="rr-row"><span class="down">Risk / lot ₹${fmt(p.risk_lot * ratio, 0)}</span><span class="up">Reward (T1) / lot ₹${fmt(p.reward_lot * ratio, 0)}</span></div>
    <div class="trail">Trailing stop: <b>₹${fmt(sc(p.trail))}</b> (Magic line on the premium chart). Book half at T1, then trail the rest.</div>
    <div class="ltp-in">
      <label for="ltpIn">Your broker's LTP<small>Enter the live premium to rescale Buy / SL / Targets</small></label>
      <input id="ltpIn" type="number" step="0.05" min="0" inputmode="decimal" placeholder="${fmt(p.entry)}" value="${mg.ltp[d.symbol] || ""}">
    </div>`;
  $("#ltpIn").addEventListener("change", (e) => {
    const v = parseFloat(e.target.value);
    if (v > 0) mg.ltp[d.symbol] = v; else delete mg.ltp[d.symbol];
    renderMagicDetail(mg.data);
  });

  const total = d.components.reduce((s, c) => s + c.pts, 0);
  $("#mgComps").innerHTML = d.components.map((c) => {
    const wpct = (Math.abs(c.pts) / 35) * 50;
    const bar = c.pts >= 0 ? `left:50%;width:${wpct}%;background:var(--up)` : `right:50%;width:${wpct}%;background:var(--down)`;
    return `<li><span class="l">${c.label} <span class="muted" style="font-size:11px">×${c.weight}</span></span>
      <span class="bar"><i style="${bar}"></i></span><span class="p ${c.pts > 0 ? "up" : c.pts < 0 ? "down" : "muted"}">${c.pts > 0 ? "+" : ""}${c.pts}</span>
      <span class="d">${c.detail}</span></li>`;
  }).join("") + `</ul><div class="mg-total"><span>Raw total</span><span class="${total >= 0 ? "up" : "down"} num">${total > 0 ? "+" : ""}${total.toFixed(0)} → smoothed ${d.score > 0 ? "+" : ""}${d.score}</span></div>`;

  $("#mgLadder").innerHTML = `<thead><tr><th>Strike</th><th></th><th class="n">Premium</th><th>Delta</th></tr></thead><tbody>` +
    d.ladder.map((r) => `<tr class="${r.picked ? "pk" : ""}"><td>${r.picked ? "★ " : ""}${r.strike} ${d.kind}</td><td><span class="mn ${r.moneyness}">${r.moneyness}</span></td>
      <td class="n">₹${fmt(r.premium)}</td><td><span class="dbar"><i style="width:${r.delta * 100}%"></i></span>${r.delta.toFixed(2)}</td></tr>`).join("") + "</tbody>";

  const full = document.body.classList.contains("fullchart");
  $("#mgTab").href = `/?mode=magic&index=${d.key}&view=${mg.view}&full=1`;
  $$("#mgIdx button").forEach((b) => b.classList.toggle("on", b.dataset.k === d.key));
  if (full) {
    document.title = `${d.symbol} · Magic · Sowmax Scanner`;
    const r = mg.ltp[d.symbol] ? mg.ltp[d.symbol] / p.entry : 1;
    $("#mgStrip").innerHTML = `<span class="sigbadge ${d.signal}">${SIG_TXT[d.signal]}</span>
      <span>${d.symbol}</span><span class="b">BUY ${fmt(p.entry * r)}</span><span class="s">SL ${fmt(p.sl * r)}</span>
      <span class="t">T1 ${fmt(p.t1 * r)}</span><span class="t">T2 ${fmt(p.t2 * r)}</span><span>Magic ${d.score > 0 ? "+" : ""}${d.score}</span>`;
  }
  $("#mgLadderNote").textContent = d.source === "live" ? "· live premiums from Zerodha, ★ = auto-selected" : "· model premiums, ★ = auto-selected";
  $("#mgModel").textContent = mg.view === "option"
    ? (d.chart_source === "live" ? `${d.symbol} · 5 min · live premium (Zerodha) · IV ${d.iv}%`
                                 : `${d.symbol} · 5 min · modelled premium (Black-Scholes, IV ${d.iv}%)`)
    : `${d.name} · 5 min · NSE/BSE · IST`;
  buildMagicChart(d);
}

function buildMagicChart(d) {
  const L = LightweightCharts;
  const el = $("#mgChart");
  if (mg.chart) { mg.chart.remove(); mg.chart = null; }
  el.innerHTML = "";
  const css = getComputedStyle(document.documentElement);
  const v = (n) => css.getPropertyValue(n).trim();
  const UP = v("--up"), DN = v("--down");
  const src = mg.view === "option" ? d.option : d.index;
  const ratio = mg.view === "option" && mg.ltp[d.symbol] ? mg.ltp[d.symbol] / d.plan.entry : 1;
  const chart = L.createChart(el, {
    autoSize: true,
    layout: { attributionLogo: false, background: { color: "transparent" }, textColor: v("--muted"), fontFamily: "'Plus Jakarta Sans', -apple-system, system-ui, sans-serif", fontSize: 11, panes: { separatorColor: v("--line") } },
    grid: { vertLines: { color: v("--line-2") }, horzLines: { color: v("--line-2") } },
    rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.16, bottom: 0.06 } },
    timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false, rightOffset: 6 },
    crosshair: { mode: L.CrosshairMode.Normal },
    localization: { timeFormatter: (t) => fmtTime(t, true), priceFormatter: (p) => p.toFixed(2) },
  });
  mg.chart = chart;
  const p = d.plan;
  const lv = mg.view === "option"
    ? [["BUY", p.entry * ratio, "#9b85ff", 0], ["SL", p.sl * ratio, DN, 2], ["T1", p.t1 * ratio, UP, 2], ["T2", p.t2 * ratio, UP, 1]]
    : [["Spot", d.spot, "#9b85ff", 0], ["SL", p.idx_sl, DN, 2], ["T1", p.idx_t1, UP, 2], ["T2", p.idx_t2, UP, 1]];
  const cs = chart.addSeries(L.CandlestickSeries, {
    upColor: UP, downColor: DN, borderVisible: false, wickUpColor: UP, wickDownColor: DN,
    autoscaleInfoProvider: (orig) => {
      const r = orig(); if (!r) return r;
      lv.forEach(([, price]) => { r.priceRange.minValue = Math.min(r.priceRange.minValue, price); r.priceRange.maxValue = Math.max(r.priceRange.maxValue, price); });
      return r;
    },
  });
  const sc = (c) => ratio === 1 ? c : { ...c, open: c.open * ratio, high: c.high * ratio, low: c.low * ratio, close: c.close * ratio };
  cs.setData(src.candles.map(sc));
  const lineColors = { "EMA 9": "#f5a524", "EMA 21": "#22b8f0", VWAP: "#a78bfa" };
  Object.entries(src.lines).forEach(([name, pts]) => {
    const isMagic = name === "Magic line";
    const s = chart.addSeries(L.LineSeries, { color: lineColors[name] || UP, lineWidth: isMagic ? 2.5 : 1.5, lastValueVisible: isMagic, priceLineVisible: false, crosshairMarkerVisible: false, title: isMagic ? "Magic" : "" });
    s.setData(pts.map((q) => ({ time: q.time, value: q.value * ratio, ...(isMagic ? { color: q.up ? UP : DN } : {}) })));
  });
  lv.forEach(([t, price, c, style]) => cs.createPriceLine({ price, color: c, lineWidth: t === "BUY" || t === "Spot" ? 2 : 1.5, lineStyle: style, title: t }));
  const callUp = mg.view === "index" || d.kind === "CE";
  L.createSeriesMarkers(cs, src.markers.map((m) => m.side === "buy"
    ? { time: m.time, position: "belowBar", color: UP, shape: "arrowUp", text: mg.view === "option" ? "BUY" : "CALL" }
    : { time: m.time, position: "aboveBar", color: DN, shape: "arrowDown", text: mg.view === "option" ? "EXIT" : "PUT" }));

  const hist = chart.addSeries(L.HistogramSeries, { priceLineVisible: false, lastValueVisible: true, title: "Magic" }, 1);
  hist.setData(src.magic.map((q) => ({ ...q, color: q.value >= 40 ? UP : q.value <= -40 ? DN : v("--neutral-bg") })));
  [[40, UP], [0, v("--muted")], [-40, DN]].forEach(([price, c]) => hist.createPriceLine({ price, color: c, lineWidth: 1, lineStyle: 2, axisLabelVisible: false }));
  try { chart.panes()[1].setHeight(Math.round(el.clientHeight * 0.24)); } catch {}

  const n = src.candles.length;
  chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - 110), to: n + 6 });

  const idx = new Map(src.candles.map((c, i) => [c.time, i]));
  const mAt = new Map(src.magic.map((q) => [q.time, q.value]));
  const title = mg.view === "option" ? d.symbol : d.name;
  const paint = (t) => {
    const i = idx.has(t) ? idx.get(t) : n - 1;
    const c = sc(src.candles[i]), pv = i > 0 ? sc(src.candles[i - 1]) : c;
    const ch = (c.close / pv.close - 1) * 100, k = ch >= 0 ? "up" : "down";
    const m = mAt.get(c.time);
    $("#mgOhlc").innerHTML = `<div class="r1"><span>${title}</span><span class="tfb">5 min</span><span class="when">${fmtTime(c.time, true)}</span></div>
      <div class="r2"><span>O<b class="${k}">${fmt(c.open)}</b></span><span>H<b class="${k}">${fmt(c.high)}</b></span><span>L<b class="${k}">${fmt(c.low)}</b></span><span>C<b class="${k}">${fmt(c.close)}</b></span><span><b class="${k}">${ch >= 0 ? "+" : ""}${ch.toFixed(2)}%</b></span>
      <span>Magic<b class="${m >= 40 ? "up" : m <= -40 ? "down" : ""}">${m != null ? (m > 0 ? "+" : "") + m.toFixed(0) : "—"}</b></span></div>`;
  };
  paint(null);
  chart.subscribeCrosshairMove((q) => paint(q && q.time != null ? q.time : null));
}

// ---------- misc ----------
function setAuto() {
  clearInterval(state.timer);
  const sec = state.mode === "swing" ? 900 : 60;
  $("#autoLbl").textContent = `Auto ${sec < 120 ? sec + "s" : sec / 60 + "m"}`;
  if ($("#auto").checked) state.timer = setInterval(() => load(), sec * 1000);
}

function moveIndicator() {
  const a = $(".mode.active"), ind = $("#modeInd");
  ind.style.width = `${a.offsetWidth}px`;
  ind.style.transform = `translateX(${a.offsetLeft - 5}px)`;
}

function tickClock() {
  $("#clock").textContent = new Date().toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }) + " IST";
}

function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem("theme", t); } catch {}
  if (state.data) renderHeader(state.data);
  if (state.mode === "magic" && mg.data) renderMagicDetail(mg.data);
  if ($("#drawer").classList.contains("open")) openDrawer($("#dSym").textContent);
}

function setMode(mode) {
  state.mode = mode;
  $$(".mode").forEach((x) => x.classList.toggle("active", x.dataset.mode === mode));
  const isMagic = mode === "magic";
  $("#magic").hidden = !isMagic;
  $("#stats").hidden = isMagic;
  $(".board").hidden = isMagic;
  $("#universeWrap").hidden = isMagic;
  if (isMagic) $("#picksWrap").hidden = true;
  moveIndicator();
}

// ---------- events ----------
function bind() {
  $$(".mode").forEach((b) => b.addEventListener("click", () => {
    if (b.dataset.mode === state.mode) return;
    setMode(b.dataset.mode);
    state.tags.clear();
    state.sort = { key: "score", dir: -1 };
    setAuto();
    load();
  }));
  $("#refresh").addEventListener("click", () => load(true));
  $("#universe").addEventListener("change", (e) => { state.universe = e.target.value; state.tags.clear(); load(); });
  $("#auto").addEventListener("change", setAuto);
  $("#q").addEventListener("input", (e) => { state.q = e.target.value; renderTable(); });
  $("#sigFilter").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    state.sig = b.dataset.f;
    $$("#sigFilter button").forEach((x) => x.classList.toggle("on", x === b));
    renderTable();
  });
  $("#tagChips").addEventListener("click", (e) => {
    const b = e.target.closest(".chip"); if (!b) return;
    const t = b.dataset.t;
    state.tags.has(t) ? state.tags.delete(t) : state.tags.add(t);
    b.classList.toggle("on");
    renderTable();
  });
  $("#thead").addEventListener("click", (e) => {
    const th = e.target.closest("th"); if (!th) return;
    const col = COLS[state.mode].find((c) => c.k === th.dataset.k);
    if (!col || col.nosort) return;
    state.sort = state.sort.key === col.k ? { key: col.k, dir: -state.sort.dir } : { key: col.k, dir: col.k === "symbol" ? 1 : -1 };
    renderTable();
  });
  $("#tbody").addEventListener("click", (e) => { const tr = e.target.closest("tr"); if (tr) openDrawer(tr.dataset.s); });
  $("#picks").addEventListener("click", (e) => { const p = e.target.closest(".pick"); if (p) openDrawer(p.dataset.s); });
  $("#dClose").addEventListener("click", closeDrawer);
  $("#tfGroup").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    prefs.tf[state.mode] = b.dataset.tf; savePrefs(); redrawFromCache();
  });
  $("#oscGroup").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    prefs.osc = b.dataset.osc; savePrefs(); redrawFromCache();
  });
  $("#indGroup").addEventListener("click", (e) => {
    const b = e.target.closest(".ind"); if (!b) return;
    prefs.ind[b.dataset.ind] = !prefs.ind[b.dataset.ind]; savePrefs(); redrawFromCache();
  });
  $("#fsBtn").addEventListener("click", () => toggleFullscreen());
  $("#mgCards").addEventListener("click", (e) => { const c = e.target.closest(".mg-card"); if (c && c.dataset.k) loadMagicDetail(c.dataset.k); });
  $("#mgIdx").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) loadMagicDetail(b.dataset.k); });
  $("#mgFs").addEventListener("click", () => $("#mgCard").classList.toggle("fs"));
  $("#mgView").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b || !mg.data) return;
    mg.view = b.dataset.v;
    if (document.body.classList.contains("fullchart")) history.replaceState(null, "", `/?mode=magic&index=${mg.sel}&view=${mg.view}&full=1`);
    $$("#mgView button").forEach((x) => x.classList.toggle("on", x === b));
    renderMagicDetail(mg.data);
  });
  $("#drawerBg").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { if ($("#mgCard").classList.contains("fs")) { $("#mgCard").classList.remove("fs"); return; } closeDrawer(); }
    const typing = document.activeElement && document.activeElement.tagName === "INPUT";
    if (!typing && $("#drawer").classList.contains("open")) {
      const keys = { 1: "1m", 2: "5m", 3: "15m", 4: "30m", 5: "1h", 6: "1d", 7: "1wk" };
      if (keys[e.key]) { prefs.tf[state.mode] = keys[e.key]; savePrefs(); redrawFromCache(); }
      if (e.key === "f" || e.key === "F") toggleFullscreen();
    }
    if (e.key === "/" && document.activeElement !== $("#q")) { e.preventDefault(); $("#q").focus(); }
  });
  $("#themeBtn").addEventListener("click", () => applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));
  window.addEventListener("resize", moveIndicator);
}

async function init() {
  let t = null;
  try { t = localStorage.getItem("theme"); } catch {}
  document.documentElement.dataset.theme = t || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  const p = new URLSearchParams(location.search);
  setMode(["swing", "magic"].includes(p.get("mode")) ? p.get("mode") : "intraday");
  if (p.get("index")) mg.sel = p.get("index").toUpperCase();
  if (p.get("view") === "index") {
    mg.view = "index";
    $$("#mgView button").forEach((x) => x.classList.toggle("on", x.dataset.v === "index"));
  }
  if (p.get("full") === "1" && state.mode === "magic") {
    document.body.classList.add("fullchart");
    $("#auto").checked = true; // the full-tab chart keeps itself fresh
    if (!mg.sel) mg.sel = "NIFTY";
  }
  if (document.fonts) document.fonts.ready.then(moveIndicator);
  tickClock(); setInterval(tickClock, 1000);
  try {
    const u = await (await fetch("/api/universes")).json();
    $("#universe").innerHTML = Object.entries(u).map(([k, n]) => `<option value="${k}">${n}</option>`).join("");
    $("#universe").value = state.universe;
  } catch {}
  bind();
  setAuto();
  load();
  kiteStatus();
}

async function kiteStatus() {
  const b = $("#kiteBtn");
  try {
    const k = await (await fetch("/api/kite")).json();
    if (!k.configured) return;
    b.hidden = false;
    b.classList.toggle("on", k.connected);
    b.textContent = k.connected ? "Zerodha live" : "Connect Zerodha";
    b.href = k.connected ? "/kite/disconnect" : "/kite/login";
    b.title = k.connected ? `Live data from Kite${k.user ? ` (${k.user})` : ""}. Click to disconnect.`
                          : "Not connected: using delayed Yahoo data. Click to log in to Zerodha (needed once a day).";
    if (k.connected && b.dataset.confirm !== "1") {
      b.dataset.confirm = "1";
      b.addEventListener("click", (e) => { if (!confirm("Disconnect Zerodha? Data will fall back to Yahoo.")) e.preventDefault(); });
    }
  } catch {}
  const p = new URLSearchParams(location.search);
  if (p.has("kite")) {
    if (p.get("kite") === "failed") alert("Zerodha login didn't complete. Please try again.");
    p.delete("kite");
    history.replaceState(null, "", location.pathname + (p.toString() ? "?" + p : ""));
  }
}
init();
