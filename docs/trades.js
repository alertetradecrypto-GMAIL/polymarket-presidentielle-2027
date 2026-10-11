/* Onglet Trades – Présidentielle 2027 (lecture seule).
 * Données : data/trades/index.json (jours disponibles) + data/trades/AAAA-MM-JJ.json
 *   (trades.py) : {cols, rows: [[t, cand, out, side, price, size, usd, wallet, name, tx], …]}
 *   + data/candidates.json (noms). Heures en Europe/Paris.
 */
"use strict";

const PAGE = 200;
const BIG_USD = 1000;
const DEF_MIN = 5;
const REFRESH_MS = 5 * 60 * 1000;
const PROFILE = "https://polymarket.com/profile/";
const TX = "https://polygonscan.com/tx/";

const $ = (s) => document.querySelector(s);
const state = {
  index: null, names: {}, rows: [], loaded: "",
  day: "", cand: "", out: "", side: "", min: DEF_MIN, who: "",
  sort: { k: "t", asc: false }, sumSort: { k: "usd", asc: false }, shown: PAGE,
};

// ---------------------------------------------------------------- formats

const nf0 = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 0 });
const nf2 = new Intl.NumberFormat("fr-FR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const nf3 = new Intl.NumberFormat("fr-FR", { minimumFractionDigits: 3, maximumFractionDigits: 3 });
const nfC = new Intl.NumberFormat("fr-FR", { notation: "compact", maximumFractionDigits: 1 });
const usd = (v) => nf2.format(v) + " $";
const usdC = (v) => (Math.abs(v) >= 10000 ? nfC.format(v) : nf0.format(v)) + " $";
const signedUsd = (v) => (v > 0.005 ? "+" : v < -0.005 ? "−" : "") + usdC(Math.abs(v));
const cls = (v) => (v > 0.005 ? "up" : v < -0.005 ? "down" : "");
const tFmt = new Intl.DateTimeFormat("fr-FR", { timeZone: "Europe/Paris", hour: "2-digit", minute: "2-digit", second: "2-digit" });
const dFmt = new Intl.DateTimeFormat("fr-FR", { timeZone: "Europe/Paris", day: "2-digit", month: "2-digit" });
const dayLabel = (d) => { const [y, m, j] = d.split("-"); return `${j}/${m}/${y}`; };
const short = (w) => (w ? `${w.slice(0, 6)}…${w.slice(-4)}` : "");
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const name = (slug) => state.names[slug] || slug;

// ---------------------------------------------------------------- données

async function getJSON(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(`${url} : HTTP ${r.status}`);
  return r.json();
}

function daysFor(sel) {
  const all = Object.keys(state.index.days).sort();
  if (sel === "7d") return all.slice(-7);
  return all.includes(sel) ? [sel] : all.slice(-1);
}

async function loadRows() {
  const days = daysFor(state.day);
  const key = days.join(",") + "|" + JSON.stringify(days.map((d) => state.index.days[d]));
  if (key === state.loaded) return false;
  $("#status").textContent = "Chargement…";
  const files = await Promise.all(days.map((d) =>
    getJSON(`data/trades/${d}.json?v=${(state.index.days[d] || [0])[0]}`).catch(() => ({ rows: [] }))));
  state.rows = files.flatMap((f) => f.rows.map((r) => ({
    t: r[0], cand: r[1], out: r[2], side: r[3], price: r[4], size: r[5], usd: r[6],
    wallet: r[7], name: r[8], tx: r[9],
  })));
  state.loaded = key;
  return true;
}

async function loadMeta() {
  const [index, cands] = await Promise.all([
    getJSON(`data/trades/index.json?t=${Date.now()}`),
    getJSON(`data/candidates.json?t=${Date.now()}`).catch(() => ({ candidates: [] })),
  ]);
  state.index = index;
  state.names = Object.fromEntries((cands.candidates || []).map((c) => [c.slug, c.name]));
}

// ---------------------------------------------------------------- filtres

function filtered(ignoreCand = false) {
  const who = state.who.trim().toLowerCase();
  return state.rows.filter((r) =>
    (ignoreCand || !state.cand || r.cand === state.cand)
    && (!state.out || r.out === state.out)
    && (!state.side || r.side === state.side)
    && r.usd >= (state.min || 0)
    && (!who || r.wallet.includes(who) || r.name.toLowerCase().includes(who)));
}

function sorter({ k, asc }, key = (r) => r[k]) {
  return (a, b) => {
    const x = key(a), y = key(b);
    const c = typeof x === "string" ? x.localeCompare(y, "fr") : x - y;
    return (asc ? c : -c) || b.t - a.t;
  };
}

// ---------------------------------------------------------------- rendu

function renderKpis(rows) {
  $("#k-n").textContent = nf0.format(rows.length);
  $("#k-usd").textContent = usdC(rows.reduce((s, r) => s + r.usd, 0));
  $("#k-w").textContent = nf0.format(new Set(rows.map((r) => r.wallet)).size);
  $("#k-max").textContent = rows.length ? usdC(Math.max(...rows.map((r) => r.usd))) : "—";
}

function renderSummary() {
  const by = new Map();
  for (const r of filtered(true)) {
    let s = by.get(r.cand);
    if (!s) by.set(r.cand, s = { cand: r.cand, name: name(r.cand), usd: 0, n: 0, ws: new Set(), netYes: 0, netNo: 0, max: 0 });
    s.usd += r.usd; s.n++; s.ws.add(r.wallet); s.max = Math.max(s.max, r.usd);
    const v = r.side === "BUY" ? r.usd : -r.usd;
    if (r.out === "Yes") s.netYes += v; else s.netNo += v;
  }
  const list = [...by.values()].map((s) => ({ ...s, w: s.ws.size }));
  list.sort(sorter(state.sumSort));
  $("#sum tbody").innerHTML = list.length ? list.map((s) => `
    <tr data-cand="${esc(s.cand)}" class="${s.cand === state.cand ? "on" : ""}">
      <td class="name">${esc(s.name)}</td>
      <td class="num">${usdC(s.usd)}</td>
      <td class="num">${nf0.format(s.n)}</td>
      <td class="num">${nf0.format(s.w)}</td>
      <td class="num ${cls(s.netYes)}">${signedUsd(s.netYes)}</td>
      <td class="num ${cls(s.netNo)}">${signedUsd(s.netNo)}</td>
      <td class="num">${usdC(s.max)}</td>
    </tr>`).join("") : `<tr><td colspan="7" class="muted">Aucun trade.</td></tr>`;
  markSorted("#sum", state.sumSort);
}

function renderTrades() {
  const rows = filtered();
  rows.sort(sorter(state.sort));
  renderKpis(rows);
  const multiDay = daysFor(state.day).length > 1;
  const shown = rows.slice(0, state.shown);
  $("#trades tbody").innerHTML = shown.length ? shown.map((r) => {
    const d = new Date(r.t * 1000);
    const who = r.name
      ? `${esc(r.name)} <span class="muted">${short(r.wallet)}</span>`
      : `<code>${short(r.wallet)}</code>`;
    return `<tr class="${r.usd >= BIG_USD ? "big" : ""}">
      <td>${multiDay ? dFmt.format(d) + " " : ""}${tFmt.format(d)}</td>
      <td class="name">${esc(name(r.cand))}</td>
      <td>${r.out === "Yes" ? "Oui" : "Non"}</td>
      <td class="${r.side === "BUY" ? "up" : "down"}">${r.side === "BUY" ? "Achat" : "Vente"}</td>
      <td class="num">${nf3.format(r.price)}</td>
      <td class="num">${nf0.format(r.size)}</td>
      <td class="num amt">${usd(r.usd)}</td>
      <td class="who"><a href="${PROFILE}${esc(r.wallet)}" target="_blank" rel="noopener" title="${esc(r.wallet)}">${who}</a>
        <button class="flt" data-w="${esc(r.wallet)}" title="Filtrer sur cette adresse" aria-label="Filtrer sur cette adresse">⌕</button></td>
      <td>${r.tx ? `<a href="${TX}${esc(r.tx)}" target="_blank" rel="noopener" title="Transaction sur Polygonscan">tx ↗</a>` : ""}</td>
    </tr>`;
  }).join("") : `<tr><td colspan="9" class="muted">Aucun trade pour ces filtres.</td></tr>`;
  $("#count").textContent = rows.length > shown.length
    ? `${nf0.format(shown.length)} affichés sur ${nf0.format(rows.length)}` : `${nf0.format(rows.length)} trade(s)`;
  $("#more").hidden = rows.length <= shown.length;
  markSorted("#trades", state.sort);
}

function markSorted(table, sort) {
  document.querySelectorAll(`${table} th[data-k]`).forEach((th) => {
    th.classList.toggle("sorted", th.dataset.k === sort.k);
    th.classList.toggle("asc", th.dataset.k === sort.k && sort.asc);
  });
}

function renderDays() {
  const days = Object.keys(state.index.days).sort().reverse();
  $("#f-day").innerHTML = days.map((d) => {
    const [n, v] = state.index.days[d];
    return `<option value="${d}">${dayLabel(d)} · ${nf0.format(n)} trades · ${usdC(v)}</option>`;
  }).join("") + (days.length > 1 ? `<option value="7d">7 derniers jours</option>` : "");
  if (!state.day || (state.day !== "7d" && !days.includes(state.day))) state.day = days[0] || "";
  $("#f-day").value = state.day;
}

function renderCands() {
  const slugs = [...new Set(state.rows.map((r) => r.cand))];
  if (state.cand && !slugs.includes(state.cand)) slugs.push(state.cand);
  slugs.sort((a, b) => name(a).localeCompare(name(b), "fr"));
  $("#f-cand").innerHTML = `<option value="">Tous</option>` +
    slugs.map((s) => `<option value="${esc(s)}">${esc(name(s))}</option>`).join("");
  $("#f-cand").value = state.cand;
}

function syncControls() {
  for (const [id, v] of [["#f-out", state.out], ["#f-side", state.side]]) {
    $(id).querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.v === v));
  }
  $("#f-min").value = state.min;
  $("#f-who").value = state.who;
  $("#f-cand").value = state.cand;
}

function render() {
  syncControls();
  renderSummary();
  renderTrades();
  saveHash();
}

// ---------------------------------------------------------------- URL

function saveHash() {
  const h = new URLSearchParams();
  for (const k of ["day", "cand", "out", "side", "who"]) if (state[k]) h.set(k, state[k]);
  if (state.min !== DEF_MIN) h.set("min", state.min);
  history.replaceState(null, "", h.toString() ? "#" + h.toString() : location.pathname);
}

function readHash() {
  const h = new URLSearchParams(location.hash.slice(1));
  for (const k of ["day", "cand", "out", "side", "who"]) state[k] = h.get(k) || "";
  const m = parseFloat(h.get("min"));
  state.min = Number.isFinite(m) && m >= 0 ? m : DEF_MIN;
}

// ---------------------------------------------------------------- événements

function bind() {
  const reset = () => { state.shown = PAGE; render(); };
  $("#f-day").addEventListener("change", async (e) => {
    state.day = e.target.value; await loadRows(); renderCands(); reset(); setStatus();
  });
  $("#f-cand").addEventListener("change", (e) => { state.cand = e.target.value; reset(); });
  for (const id of ["#f-out", "#f-side"]) {
    $(id).addEventListener("click", (e) => {
      const b = e.target.closest("button"); if (!b) return;
      state[id === "#f-out" ? "out" : "side"] = b.dataset.v; reset();
    });
  }
  $("#f-min").addEventListener("input", (e) => {
    const v = parseFloat(e.target.value); state.min = Number.isFinite(v) && v >= 0 ? v : 0;
    state.shown = PAGE; renderSummary(); renderTrades(); saveHash();
  });
  let tm;
  $("#f-who").addEventListener("input", (e) => {
    clearTimeout(tm);
    tm = setTimeout(() => { state.who = e.target.value.trim(); state.shown = PAGE; renderSummary(); renderTrades(); saveHash(); }, 200);
  });
  $("#f-reset").addEventListener("click", () => {
    Object.assign(state, { cand: "", out: "", side: "", min: DEF_MIN, who: "" }); reset();
  });
  $("#sum tbody").addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-cand]"); if (!tr) return;
    state.cand = state.cand === tr.dataset.cand ? "" : tr.dataset.cand; reset();
  });
  $("#trades tbody").addEventListener("click", (e) => {
    const b = e.target.closest("button.flt"); if (!b) return;
    state.who = state.who === b.dataset.w ? "" : b.dataset.w; reset();
  });
  $("#more").addEventListener("click", () => { state.shown += PAGE; renderTrades(); });
  for (const [table, key] of [["#sum", "sumSort"], ["#trades", "sort"]]) {
    $(`${table} thead`).addEventListener("click", (e) => {
      const th = e.target.closest("th[data-k]"); if (!th) return;
      const s = state[key];
      state[key] = s.k === th.dataset.k ? { k: s.k, asc: !s.asc }
        : { k: th.dataset.k, asc: th.dataset.k === "name" || th.dataset.k === "cand" };
      if (table === "#sum") renderSummary(); else renderTrades();
    });
  }
}

function setStatus() {
  const days = daysFor(state.day);
  $("#status").textContent = days.length
    ? `${days.length > 1 ? `Du ${dayLabel(days[0])} au ${dayLabel(days.at(-1))}` : dayLabel(days[0])} · ${nf0.format(state.rows.length)} trades au total (avant filtres).`
    : "";
}

async function refresh() {
  try {
    await loadMeta();
    renderDays();
    if (await loadRows()) { renderCands(); render(); setStatus(); }
  } catch (e) { console.error(e); }
}

async function main() {
  readHash();
  try { await loadMeta(); }
  catch (e) {
    console.error(e);
    $("#status").textContent = "Aucune donnée de trades pour l'instant : la collecte démarre (rattrapage de l'historique en cours).";
    return;
  }
  renderDays();
  await loadRows();
  renderCands();
  bind();
  render();
  setStatus();
  setInterval(refresh, REFRESH_MS);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
}

main();
