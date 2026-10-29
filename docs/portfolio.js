/* Portefeuille en temps réel.
 * Tout se passe dans le navigateur : les adresses sont lues depuis localStorage,
 * jamais depuis le dépôt. Sources :
 *  - positions : data-api.polymarket.com/positions (même logique que src/daily.py)
 *  - cash      : solde pUSD + USDC.e sur Polygon (eth_call balanceOf)
 *  - prix 24 h : data/candidates.json (price_24h, généré par collect.py)
 *  - devises   : api.frankfurter.app (USDC ≈ USD)
 */
"use strict";

const DATA_API = "https://data-api.polymarket.com";
const EVENT_SLUG = "next-french-presidential-election";
const RPCS = [
  "https://polygon-bor-rpc.publicnode.com",
  "https://polygon.drpc.org",
  "https://1rpc.io/matic",
];
const CASH_TOKENS = [
  "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB", // pUSD
  "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174", // USDC.e
];
const N_ADDR = 3;
const REFRESH_MS = 60_000;
const SNAP_EVERY_S = 15 * 60;
const SNAP_MAX = 3000;
const LS = { addrs: "pf.addrs.v1", hist: "pf.hist.v1", cur: "pf.cur.v1" };

const $ = (s) => document.querySelector(s);
const state = { addrs: [], cur: "USD", fx: { USD: 1 }, last: null, timer: null, chart: null, series: null, busy: false };

/* ---------- stockage local (peut être indisponible : navigation privée…) ---------- */
function lsGet(k, def) {
  try { const v = localStorage.getItem(k); return v == null ? def : JSON.parse(v); } catch { return def; }
}
function lsSet(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* ignoré */ } }
function lsDel(k) { try { localStorage.removeItem(k); } catch { /* ignoré */ } }

/* ---------- formats ---------- */
const nf = (d) => new Intl.NumberFormat("fr-FR", { minimumFractionDigits: d, maximumFractionDigits: d });
const f0 = nf(0), f2 = nf(2), f1 = nf(1);
function money(usd, signed = false) {
  if (usd == null || !isFinite(usd)) return "—";
  const v = usd * (state.fx[state.cur] ?? 1);
  const s = new Intl.NumberFormat("fr-FR", { style: "currency", currency: state.cur, maximumFractionDigits: 2, minimumFractionDigits: 2 }).format(Math.abs(v));
  return (v < 0 ? "−" : signed && v > 0 ? "+" : "") + s;
}
const cents = (p) => (p == null || !isFinite(p) ? "—" : f1.format(p * 100) + " ¢");
const pct = (x) => (x == null || !isFinite(x) ? "—" : (x > 0 ? "+" : x < 0 ? "−" : "") + f1.format(Math.abs(x) * 100) + " %");
const cls = (x) => (x > 0 ? "up" : x < 0 ? "down" : "");
const short = (a) => a.slice(0, 6) + "…" + a.slice(-4);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = (x) => { const v = parseFloat(x); return isFinite(v) ? v : 0; };

/* ---------- réseau ---------- */
async function getJSON(url, opts = {}) {
  const r = await fetch(url, { cache: "no-store", ...opts });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

async function fetchPositions(addr) {
  const out = [];
  for (let offset = 0; ; offset += 500) {
    const page = await getJSON(`${DATA_API}/positions?user=${addr}&sizeThreshold=0&limit=500&offset=${offset}`);
    if (!Array.isArray(page) || !page.length) break;
    out.push(...page);
    if (page.length < 500) break;
  }
  return out;
}

async function fetchCash(addr) {
  const owner = addr.toLowerCase().replace(/^0x/, "").padStart(64, "0");
  const batch = CASH_TOKENS.map((to, id) => ({
    jsonrpc: "2.0", id, method: "eth_call", params: [{ to, data: "0x70a08231" + owner }, "latest"],
  }));
  let lastErr;
  for (const url of RPCS) {
    try {
      const res = await getJSON(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(batch) });
      if (!Array.isArray(res) || res.length !== batch.length) throw new Error("réponse RPC inattendue");
      let total = 0n;
      for (const it of res) {
        if (it.error) throw new Error(it.error.message || "erreur RPC");
        total += BigInt(it.result && it.result !== "0x" ? it.result : "0x0");
      }
      return Number(total) / 1e6;
    } catch (e) { lastErr = e; }
  }
  throw lastErr || new Error("RPC indisponible");
}

async function fetchCandidates() {
  try {
    const d = await getJSON(`data/candidates.json?t=${Date.now()}`);
    const m = new Map();
    for (const c of d.candidates || []) m.set((c.condition_id || "").toLowerCase(), c);
    return m;
  } catch { return new Map(); }
}

async function fetchFx() {
  try {
    const d = await getJSON("https://api.frankfurter.app/latest?from=USD&to=CHF,EUR");
    state.fx = { USD: 1, ...d.rates };
  } catch { /* garde USD seul */ }
  for (const b of document.querySelectorAll("#cur button")) b.disabled = !(b.dataset.v in state.fx);
  if (!(state.cur in state.fx)) state.cur = "USD";
}

/* ---------- calcul (miroir de daily.market_positions / summarize) ---------- */
function toPosition(p, cands, label) {
  const size = num(p.size);
  if (size <= 0) return null;
  const cand = cands.get((p.conditionId || "").toLowerCase());
  const inEvent = !!cand || p.eventSlug === EVENT_SLUG;
  const avg = num(p.avgPrice), cur = num(p.curPrice);
  const cost = num(p.initialValue) || size * avg;
  const value = p.currentValue != null ? num(p.currentValue) : size * cur;
  const yes = String(p.outcome || "").toLowerCase() === "yes";
  let d24 = null;
  if (cand && cand.price_24h != null && !p.redeemable) {
    const ref = yes ? cand.price_24h : 1 - cand.price_24h;
    d24 = cur - ref;
  }
  return {
    label, inEvent,
    name: cand ? cand.name : (p.title || "?"),
    side: inEvent ? (yes ? "Oui" : "Non") : (p.outcome || "?"),
    yes, size, avg, cur, cost, value, pnl: value - cost, d24, pnl24: d24 == null ? null : d24 * size,
  };
}

async function loadAccount(a, cands) {
  const acc = { ...a, positions: [], cash: null, errors: [] };
  const [pos, cash] = await Promise.allSettled([fetchPositions(a.addr), fetchCash(a.addr)]);
  if (pos.status === "fulfilled") acc.positions = pos.value.map((p) => toPosition(p, cands, a.label)).filter(Boolean);
  else acc.errors.push("positions");
  if (cash.status === "fulfilled") acc.cash = cash.value;
  else acc.errors.push("cash");
  acc.value = acc.positions.reduce((s, p) => s + p.value, 0);
  acc.cost = acc.positions.reduce((s, p) => s + p.cost, 0);
  acc.pnl = acc.value - acc.cost;
  acc.pnl24 = acc.positions.reduce((s, p) => s + (p.pnl24 ?? 0), 0);
  acc.equity = acc.value + (acc.cash ?? 0);
  return acc;
}

async function refresh() {
  if (state.busy || !state.addrs.length) return;
  state.busy = true;
  $("#status").textContent = "Mise à jour…";
  try {
    const cands = await fetchCandidates();
    const accounts = await Promise.all(state.addrs.map((a) => loadAccount(a, cands)));
    const t = {
      value: 0, cost: 0, cash: 0, pnl24: 0, cashOk: true,
      positions: accounts.flatMap((a) => a.positions),
    };
    for (const a of accounts) {
      t.value += a.value; t.cost += a.cost; t.pnl24 += a.pnl24;
      if (a.cash == null) t.cashOk = false; else t.cash += a.cash;
    }
    t.pnl = t.value - t.cost;
    t.equity = t.value + t.cash;
    const allFailed = accounts.every((a) => a.errors.includes("positions"));
    state.last = { accounts, t, at: new Date(), allFailed };
    if (!allFailed && t.cashOk) snapshot(t.equity);
    render();
    $("#status").textContent = "";
  } catch (e) {
    $("#status").textContent = "Erreur : " + e.message;
  } finally {
    state.busy = false;
  }
}

/* ---------- historique local ---------- */
function snapshot(equity) {
  const h = lsGet(LS.hist, []);
  const now = Math.floor(Date.now() / 1000);
  const last = h[h.length - 1];
  if (last && now - last[0] < SNAP_EVERY_S) h[h.length - 1] = [last[0], equity];
  else h.push([now, equity]);
  lsSet(LS.hist, h.slice(-SNAP_MAX));
}

function css(v) { return getComputedStyle(document.documentElement).getPropertyValue(v).trim(); }

function renderChart() {
  const h = lsGet(LS.hist, []);
  $("#hist-empty").hidden = h.length >= 2;
  if (h.length < 2 || !window.LightweightCharts) return;
  const LC = window.LightweightCharts;
  if (!state.chart) {
    state.chart = LC.createChart($("#pf-chart"), {
      autoSize: true,
      layout: { background: { color: "transparent" }, textColor: css("--text-2"), attributionLogo: false },
      grid: { vertLines: { color: css("--grid") }, horzLines: { color: css("--grid") } },
      rightPriceScale: { borderColor: css("--border") },
      timeScale: { borderColor: css("--border"), timeVisible: true },
      localization: { locale: "fr-FR" },
    });
    state.series = state.chart.addSeries(LC.AreaSeries, {
      lineColor: css("--accent"), topColor: css("--accent") + "55", bottomColor: css("--accent") + "05", lineWidth: 2,
    });
  }
  const k = state.fx[state.cur] ?? 1;
  state.series.applyOptions({ priceFormat: { type: "custom", formatter: (v) => money(v / k) } });
  state.series.setData(h.map(([time, v]) => ({ time, value: v * k })));
  state.chart.timeScale().fitContent();
}

/* ---------- rendu ---------- */
function setKpi(id, html, c = "") { const el = $(id); el.innerHTML = html; el.className = c; }

function render() {
  const L = state.last;
  if (!L) return;
  const { accounts, t } = L;
  setKpi("#k-eq", money(t.equity) + (t.cashOk ? "" : " *"));
  setKpi("#k-pnl", money(t.pnl, true), cls(t.pnl));
  setKpi("#k-24", money(t.pnl24, true), cls(t.pnl24));
  setKpi("#k-upd", L.at.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit", second: "2-digit" }));
  document.title = `${money(t.equity)} · Portefeuille`;

  const rows = accounts.map((a) => `<tr>
      <td><b>${esc(a.label)}</b> <a class="pf-addr" href="https://polymarket.com/profile/${a.addr}" target="_blank" rel="noopener">${short(a.addr)} ↗</a>${a.errors.length ? ` <span class="down" title="Échec : ${a.errors.join(", ")}">⚠</span>` : ""}</td>
      <td class="num">${money(a.value)}</td>
      <td class="num">${a.cash == null ? "—" : money(a.cash)}</td>
      <td class="num"><b>${money(a.equity)}</b></td>
      <td class="num ${cls(a.pnl)}">${money(a.pnl, true)}</td>
      <td class="num ${cls(a.pnl24)}">${money(a.pnl24, true)}</td></tr>`).join("");
  const total = accounts.length > 1 ? `<tr class="pf-total"><td>Total</td><td class="num">${money(t.value)}</td><td class="num">${money(t.cash)}${t.cashOk ? "" : " *"}</td><td class="num"><b>${money(t.equity)}</b></td><td class="num ${cls(t.pnl)}">${money(t.pnl, true)}</td><td class="num ${cls(t.pnl24)}">${money(t.pnl24, true)}</td></tr>` : "";
  $("#acc-table tbody").innerHTML = rows + total;

  const warns = [];
  if (L.allFailed) warns.push("Impossible de lire les positions (API Polymarket injoignable depuis ce navigateur ?).");
  if (!t.cashOk) warns.push("* Cash incomplet : solde Polygon indisponible pour au moins une adresse.");
  $("#warn").textContent = warns.join(" ");

  const pos = [...t.positions].sort((a, b) => b.value - a.value);
  $("#pos-count").textContent = `${pos.length} position${pos.length > 1 ? "s" : ""}`;
  $("#pos-table tbody").innerHTML = pos.length ? pos.map((p) => {
    const pp = p.cost > 0 ? p.pnl / p.cost : null;
    return `<tr>
      <td>${esc(p.name)}${p.inEvent ? "" : ' <span class="tag">autre marché</span>'}</td>
      <td><span class="pf-side ${p.yes ? "up" : "down"}">${esc(p.side)}</span></td>
      <td>${esc(p.label)}</td>
      <td class="num">${f0.format(p.size)}</td>
      <td class="num">${cents(p.avg)}</td>
      <td class="num">${cents(p.cur)}</td>
      <td class="num ${cls(p.d24)}">${p.d24 == null ? "—" : (p.d24 > 0 ? "+" : p.d24 < 0 ? "−" : "") + f1.format(Math.abs(p.d24) * 100) + " ¢"}</td>
      <td class="num">${money(p.value)}</td>
      <td class="num ${cls(p.pnl)}">${money(p.pnl, true)}</td>
      <td class="num ${cls(pp)}">${pct(pp)}</td></tr>`;
  }).join("") : `<tr><td colspan="10" class="note">Aucune position ouverte.</td></tr>`;

  renderChart();
}

/* ---------- adresses ---------- */
function showSetup(on) {
  $("#setup").hidden = !on;
  for (const id of ["#main-panel", "#pos-panel", "#hist-panel"]) $(id).hidden = on || !state.addrs.length;
  $("#addr-cancel").hidden = !state.addrs.length;
  if (on) {
    $("#addr-rows").innerHTML = Array.from({ length: N_ADDR }, (_, i) => {
      const a = state.addrs[i] || {};
      return `<div class="pf-row">
        <input name="label" placeholder="A${i + 1}" maxlength="12" value="${esc(a.label || "")}" aria-label="Nom de l'adresse ${i + 1}">
        <input name="addr" placeholder="0x… (adresse proxy ${i + 1})" spellcheck="false" value="${esc(a.addr || "")}" aria-label="Adresse ${i + 1}">
      </div>`;
    }).join("");
    $("#addr-err").textContent = "";
  }
}

function saveAddrs(ev) {
  ev.preventDefault();
  const rows = [...document.querySelectorAll("#addr-rows .pf-row")];
  const out = [];
  for (const [i, r] of rows.entries()) {
    const addr = r.querySelector('[name="addr"]').value.trim();
    if (!addr) continue;
    if (!/^0x[0-9a-fA-F]{40}$/.test(addr)) { $("#addr-err").textContent = `Adresse ${i + 1} invalide.`; return; }
    out.push({ label: r.querySelector('[name="label"]').value.trim() || `A${i + 1}`, addr: addr.toLowerCase() });
  }
  if (!out.length) { $("#addr-err").textContent = "Saisissez au moins une adresse."; return; }
  state.addrs = out;
  lsSet(LS.addrs, out);
  showSetup(false);
  refresh();
}

function forget() {
  if (!confirm("Effacer les adresses et l'historique enregistrés dans ce navigateur ?")) return;
  lsDel(LS.addrs); lsDel(LS.hist);
  state.addrs = []; state.last = null;
  if (state.chart) { state.chart.remove(); state.chart = state.series = null; }
  for (const id of ["#k-eq", "#k-pnl", "#k-24", "#k-upd"]) setKpi(id, "—");
  document.title = "Portefeuille · Présidentielle 2027";
  showSetup(true);
}

function setCur(c) {
  state.cur = c;
  lsSet(LS.cur, c);
  for (const b of document.querySelectorAll("#cur button")) b.classList.toggle("on", b.dataset.v === c);
  render();
}

/* ---------- démarrage ---------- */
/* Lien d'enregistrement : portfolio.html#setup=A1:0x…,A2:0x…  → localStorage, puis effacé de l'URL.
 * Les adresses restent dans le navigateur ; rien n'est commité. */
function importSetupLink() {
  const m = location.hash.match(/(?:^#|&)setup=([^&]+)/);
  if (!m) return;
  const out = decodeURIComponent(m[1]).split(",").slice(0, N_ADDR).map((it, i) => {
    const [a, b] = it.includes(":") ? it.split(":") : ["", it];
    return { label: a.trim() || `A${i + 1}`, addr: b.trim().toLowerCase() };
  }).filter((a) => /^0x[0-9a-f]{40}$/.test(a.addr));
  if (out.length) lsSet(LS.addrs, out);
  history.replaceState(null, "", location.pathname + location.search);
}

async function init() {
  importSetupLink();
  state.addrs = lsGet(LS.addrs, []).filter((a) => a && /^0x[0-9a-f]{40}$/i.test(a.addr));
  state.cur = lsGet(LS.cur, "USD");
  $("#addr-form").addEventListener("submit", saveAddrs);
  $("#addr-cancel").addEventListener("click", () => showSetup(false));
  $("#edit-addr").addEventListener("click", () => showSetup(true));
  $("#forget").addEventListener("click", forget);
  $("#refresh").addEventListener("click", refresh);
  $("#cur").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b && !b.disabled) setCur(b.dataset.v); });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });

  await fetchFx();
  setCur(state.cur);
  showSetup(!state.addrs.length);
  if (state.addrs.length) await refresh();
  state.timer = setInterval(() => { if (!document.hidden) refresh(); }, REFRESH_MS);
}

init();
