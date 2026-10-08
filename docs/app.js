/* Tableau de bord Polymarket – Présidentielle 2027 (lecture seule).
 * Données : data/candidates.json + data/history/<slug>.json (collect.py).
 *   history.p = [[t, prixOui 0-1], …]  history.v = [[t, volume cumulé], …]
 * Heures affichées en Europe/Paris. Aucune donnée personnelle.
 */
"use strict";

const LWC = window.LightweightCharts;
const MAX_SEL = 8;
const REFRESH_MS = 5 * 60 * 1000;
const TF = {
  "1D":  { win: 86400,      step: 900,   label: "pas 15 min" },
  "1W":  { win: 7 * 86400,  step: 3600,  label: "pas 1 h" },
  "1M":  { win: 30 * 86400, step: 14400, label: "pas 4 h" },
  "ALL": { win: null,       step: 86400, label: "pas 1 jour" },
};

const $ = (s) => document.querySelector(s);
const state = {
  data: null,             // candidates.json
  hist: new Map(),        // slug -> history
  sel: [],                // [{slug, slot}]
  focus: null,
  tf: "1M",
  side: "yes",
  ma: new Set([20]),
  vol: true,
  hideSmall: true,
  sort: { k: "yes", asc: false },
};
let chart = null;
let series = [];          // [{slug, s, data}]
let volSeries = null;

// ---------------------------------------------------------------- formats

const nfMoney = new Intl.NumberFormat("fr-FR", { notation: "compact", maximumFractionDigits: 1 });
const money = (v) => (v == null ? "—" : nfMoney.format(v) + " $");
function pct(v) {
  if (v == null || !isFinite(v)) return "—";
  const a = Math.abs(v);
  const d = a < 1 ? 2 : 1;
  return v.toLocaleString("fr-FR", { minimumFractionDigits: d, maximumFractionDigits: d }) + " %";
}
function signed(v, unit, dec = 1) {
  if (v == null || !isFinite(v)) return "—";
  if (Math.abs(v) < 0.005) return "0" + unit;
  const s = v > 0 ? "▲ +" : v < 0 ? "▼ −" : "";
  return s + Math.abs(v).toLocaleString("fr-FR", { minimumFractionDigits: dec, maximumFractionDigits: dec }) + unit;
}
const PRICE_FMT = { type: "custom", formatter: pct, minMove: 0.01 };
const cls = (v) => (v > 0 ? "up" : v < 0 ? "down" : "");
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// Heure de Paris : on décale les timestamps pour que le graphique (UTC) affiche l'heure locale.
const parisFmt = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Europe/Paris", hourCycle: "h23",
  year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
});
const offCache = new Map();
function parisOffset(t) {
  const h = Math.floor(t / 3600);
  if (offCache.has(h)) return offCache.get(h);
  const p = Object.fromEntries(parisFmt.formatToParts(new Date(h * 3600000)).map((x) => [x.type, x.value]));
  const local = Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute) / 1000;
  const off = local - h * 3600;
  offCache.set(h, off);
  return off;
}
const toLocal = (t) => t + parisOffset(t);
const dateFr = new Intl.DateTimeFormat("fr-FR", { timeZone: "UTC", day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
const fmtLocal = (L) => dateFr.format(new Date(L * 1000));
const fmtParis = (t) => fmtLocal(toLocal(t));

// ---------------------------------------------------------------- données

async function getJSON(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(`${url} : HTTP ${r.status}`);
  return r.json();
}
async function loadCandidates() {
  state.data = await getJSON(`data/candidates.json?t=${Date.now()}`);
}
async function loadHistories(slugs) {
  const v = state.data.updated;
  await Promise.all(slugs.filter((s) => !state.hist.has(s)).map(async (s) => {
    try { state.hist.set(s, await getJSON(`data/history/${s}.json?v=${v}`)); }
    catch (e) { console.error(e); state.hist.set(s, { p: [], v: [] }); }
  }));
}
const cand = (slug) => state.data.candidates.find((c) => c.slug === slug);

/** Barres de clôture au pas `step` (en heure de Paris) ; valeur en % (Oui ou Non). */
function bars(points, step, side) {
  const out = [];
  for (const [t, p] of points) {
    const L = toLocal(t);
    const b = L - (L % step);
    const val = (side === "no" ? 1 - p : p) * 100;
    if (out.length && out[out.length - 1].time === b) out[out.length - 1].value = val;
    else out.push({ time: b, value: val });
  }
  return out;
}
function sma(data, n) {
  const out = [];
  let sum = 0;
  for (let i = 0; i < data.length; i++) {
    sum += data[i].value;
    if (i >= n) sum -= data[i - n].value;
    if (i >= n - 1) out.push({ time: data[i].time, value: sum / n });
  }
  return out;
}
/** Volume échangé par barre = écarts du volume cumulé relevé à chaque collecte. */
function volumeBars(cum, step) {
  const out = [];
  for (let i = 1; i < cum.length; i++) {
    const d = Math.max(0, cum[i][1] - cum[i - 1][1]);
    const L = toLocal(cum[i][0]);
    const b = L - (L % step);
    if (out.length && out[out.length - 1].time === b) out[out.length - 1].value += d;
    else out.push({ time: b, value: d });
  }
  return out;
}

// ---------------------------------------------------------------- couleurs / thème

function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
const slotColor = (slot) => css(`--s${slot + 1}`);
function alpha(hex, a) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}
function chartTheme() {
  return {
    layout: { background: { type: "solid", color: css("--panel") }, textColor: css("--text-2"),
              panes: { separatorColor: css("--border"), separatorHoverColor: css("--hover") } },
    grid: { vertLines: { color: css("--grid") }, horzLines: { color: css("--grid") } },
    rightPriceScale: { borderColor: css("--border") },
    timeScale: { borderColor: css("--border") },
  };
}

// ---------------------------------------------------------------- graphique

function initChart() {
  chart = LWC.createChart($("#chart"), {
    autoSize: true,
    ...chartTheme(),
    layout: { ...chartTheme().layout, fontFamily: getComputedStyle(document.body).fontFamily, attributionLogo: true },
    crosshair: { mode: LWC.CrosshairMode.Normal },
    timeScale: { ...chartTheme().timeScale, timeVisible: true, secondsVisible: false, rightOffset: 4 },
    localization: { locale: "fr-FR", timeFormatter: fmtLocal },
  });
  chart.subscribeCrosshairMove((param) => {
    const vals = new Map();
    if (param.time !== undefined) {
      for (const it of series) {
        const d = param.seriesData.get(it.s);
        if (d) vals.set(it.slug, d.value);
      }
    }
    renderLegend(param.time !== undefined ? vals : null);
  });
  matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => {
    chart.applyOptions(chartTheme());
    renderChart(true);
    renderTable();
  });
}

function windowStart(data) {
  const tf = TF[state.tf];
  if (!tf.win || !data.length) return null;
  return toLocal(Math.floor(Date.now() / 1000)) - tf.win;
}

function renderChart(keepRange = false) {
  const prevRange = keepRange ? chart.timeScale().getVisibleRange() : null;
  for (const it of series) chart.removeSeries(it.s);
  if (volSeries) chart.removeSeries(volSeries);
  series = []; volSeries = null;
  for (const p of chart.panes().slice(1).reverse()) chart.removePane(p.paneIndex());

  $("#chart-empty").hidden = state.sel.length > 0;
  const tf = TF[state.tf];
  $("#step").textContent = `Barres : ${tf.label} · heure de Paris`;

  for (const { slug, slot } of state.sel) {
    const h = state.hist.get(slug);
    if (!h) continue;
    const color = slotColor(slot);
    const data = bars(h.p, tf.step, state.side);
    const isFocus = slug === state.focus;
    const s = chart.addSeries(LWC.LineSeries, {
      color, lineWidth: 2, priceLineVisible: false, lastValueVisible: true,
      crosshairMarkerRadius: 4, priceFormat: PRICE_FMT,
    });
    s.setData(data);
    series.push({ slug, s, data, color, ma: false });

    if (isFocus) {
      for (const n of [...state.ma].sort((a, b) => a - b)) {
        const m = chart.addSeries(LWC.LineSeries, {
          color: alpha(color, n === 20 ? 0.75 : 0.5), lineWidth: 1,
          lineStyle: n === 20 ? LWC.LineStyle.Dashed : LWC.LineStyle.Dotted,
          priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
          priceFormat: PRICE_FMT,
        });
        m.setData(sma(data, n));
        series.push({ slug: `${slug}#mm${n}`, s: m, data: [], color, ma: n });
      }
      if (state.vol && h.v && h.v.length > 1) {
        volSeries = chart.addSeries(LWC.HistogramSeries, {
          color: alpha(color, 0.55), priceLineVisible: false, lastValueVisible: false,
          priceFormat: { type: "custom", formatter: money, minMove: 1 },
        }, 1);
        volSeries.setData(volumeBars(h.v, tf.step));
        const panes = chart.panes();
        panes[0].setStretchFactor(4);
        if (panes[1]) panes[1].setStretchFactor(1);
      }
    }
  }

  // Période visible
  const ts = chart.timeScale();
  const all = series.filter((x) => !x.ma).flatMap((x) => x.data);
  if (prevRange) ts.setVisibleRange(prevRange);
  else if (!tf.win || !all.length) ts.fitContent();
  else {
    const to = Math.max(...all.map((d) => d.time));
    ts.setVisibleRange({ from: to - tf.win, to });
  }
  renderLegend(null);
  renderNote();
}

/** Valeur au début de la période affichée, pour la variation sur la période. */
function windowRef(data) {
  if (!data.length) return null;
  const start = windowStart(data);
  if (start == null) return data[0].value;
  const i = data.findIndex((d) => d.time >= start);
  return i < 0 ? null : data[i].value;
}

function renderLegend(vals) {
  const box = $("#legend");
  box.innerHTML = "";
  for (const it of series.filter((x) => !x.ma)) {
    const c = cand(it.slug);
    const last = it.data.length ? it.data[it.data.length - 1].value : null;
    const v = vals ? vals.get(it.slug) : last;
    const ref = windowRef(it.data);
    const chg = vals || last == null || ref == null ? null : last - ref;
    const chip = document.createElement("button");
    chip.className = "chip" + (it.slug === state.focus ? " focus" : "");
    chip.title = "Afficher moyennes mobiles et volume de ce candidat";
    chip.innerHTML =
      `<span class="sw" style="background:${it.color}"></span>` +
      `<span>${esc(c ? c.name : it.slug)}</span>` +
      `<span class="val">${pct(v)}</span>` +
      (chg != null ? `<span class="chg ${cls(chg)}">${signed(chg, " pt", Math.abs(chg) < 1 ? 2 : 1)}</span>` : "") +
      `<span class="x" role="button" aria-label="Retirer">×</span>`;
    chip.addEventListener("click", (e) => {
      if (e.target.classList.contains("x")) toggle(it.slug);
      else { state.focus = it.slug; saveHash(); renderChart(true); }
    });
    box.appendChild(chip);
  }
  const mas = [...state.ma].sort((a, b) => a - b);
  if (state.focus && mas.length) {
    const s = document.createElement("span");
    s.className = "note";
    s.style.margin = "auto 0 auto 4px";
    s.textContent = `${mas.map((n) => `MM${n}`).join(" / ")} sur ${cand(state.focus)?.name ?? ""}`;
    box.appendChild(s);
  }
}

function renderNote() {
  const h = state.focus && state.hist.get(state.focus);
  const parts = [state.side === "yes" ? "Cours Oui (probabilité de victoire)." : "Cours Non = 100 − Oui."];
  if (state.tf !== "1D" && state.tf !== "1W") parts.push("Historique de plus de 30 jours : 1 point par heure.");
  if (state.vol && h && h.v && h.v.length) parts.push(`Volume relevé depuis le ${fmtParis(h.v[0][0])}.`);
  if (state.vol && h && (!h.v || h.v.length < 2)) parts.push("Volume : pas encore assez de relevés.");
  $("#note").textContent = parts.join(" ");
}

// ---------------------------------------------------------------- tableau

function rows() {
  return state.data.candidates.map((c) => {
    const d24 = c.last_hist != null && c.price_24h != null ? (c.last_hist - c.price_24h) * 100 : null;
    return {
      c, slug: c.slug, name: c.name,
      yes: c.price != null ? c.price * 100 : null,
      no: c.price != null ? 100 - c.price * 100 : null,
      d24, r24: d24 != null && c.price_24h > 0 ? (d24 / (c.price_24h * 100)) * 100 : null,
      vol24: c.volume24h, vol: c.volume,
    };
  });
}

function renderTable() {
  const { k, asc } = state.sort;
  const selSet = new Map(state.sel.map((x) => [x.slug, x.slot]));
  const list = rows()
    .filter((r) => !state.hideSmall || selSet.has(r.slug) || (r.c.active && (r.yes ?? 0) >= 1))
    .sort((a, b) => {
      const x = a[k], y = b[k];
      if (x == null && y == null) return 0;
      if (x == null) return 1;
      if (y == null) return -1;
      const r = typeof x === "string" ? x.localeCompare(y, "fr") : x - y;
      return asc ? r : -r;
    });
  const tb = $("#table tbody");
  tb.innerHTML = list.map((r) => {
    const slot = selSet.get(r.slug);
    const on = slot !== undefined;
    const dot = on ? `<span class="dot" style="background:${slotColor(slot)};border-color:${slotColor(slot)}"></span>` : `<span class="dot"></span>`;
    const tag = !r.c.active || r.c.closed ? `<span class="tag">clos</span>` : "";
    return `<tr data-slug="${esc(r.slug)}" class="${on ? "on" : ""}${!r.c.active ? " closed" : ""}" aria-selected="${on}">
      <td class="c-sel">${dot}</td>
      <td class="name">${esc(r.name)}${tag}</td>
      <td class="num">${pct(r.yes)}</td>
      <td class="num">${pct(r.no)}</td>
      <td class="num ${cls(r.d24)}">${signed(r.d24, " pt", r.d24 != null && Math.abs(r.d24) < 1 ? 2 : 1)}</td>
      <td class="num ${cls(r.r24)}">${signed(r.r24, " %")}</td>
      <td class="num">${money(r.vol24)}</td>
      <td class="num">${money(r.vol)}</td>
    </tr>`;
  }).join("");
  document.querySelectorAll("#table th[data-k]").forEach((th) => {
    th.classList.toggle("sorted", th.dataset.k === k);
    th.classList.toggle("asc", th.dataset.k === k && asc);
  });
}

function renderHeader() {
  const e = state.data.event || {};
  $("#k-vol").textContent = money(e.volume);
  $("#k-vol24").textContent = money(e.volume24h);
  const age = Date.now() / 1000 - state.data.updated;
  $("#k-upd").textContent = fmtParis(state.data.updated) + (age > 3600 ? " ⚠" : "");
  $("#k-upd").title = age > 3600 ? "Données de plus d'une heure : collecte en retard ?" : "";
  if (e.slug) $("#event-link").href = `https://polymarket.com/event/${e.slug}`;
}

// ---------------------------------------------------------------- sélection / état

async function toggle(slug) {
  const i = state.sel.findIndex((x) => x.slug === slug);
  if (i >= 0) {
    state.sel.splice(i, 1);
    if (state.focus === slug) state.focus = state.sel[0]?.slug ?? null;
  } else {
    if (state.sel.length >= MAX_SEL) {
      $("#note").textContent = `${MAX_SEL} candidats au maximum : retirez-en un d'abord.`;
      return;
    }
    const used = new Set(state.sel.map((x) => x.slot));
    let slot = 0;
    while (used.has(slot)) slot++;
    state.sel.push({ slug, slot });
    state.focus = slug;
    await loadHistories([slug]);
  }
  saveHash();
  renderChart();
  renderTable();
}

function saveHash() {
  const h = new URLSearchParams({
    c: state.sel.map((x) => x.slug).join(","),
    f: state.focus || "",
    tf: state.tf, side: state.side,
    ma: [...state.ma].join(","), vol: state.vol ? "1" : "0",
  });
  history.replaceState(null, "", "#" + h.toString());
}

function readHash() {
  const h = new URLSearchParams(location.hash.slice(1));
  const known = new Set(state.data.candidates.map((c) => c.slug));
  if (h.has("c")) {
    state.sel = h.get("c").split(",").filter((s) => known.has(s)).slice(0, MAX_SEL)
      .map((slug, slot) => ({ slug, slot }));
  } else {
    state.sel = state.data.candidates.filter((c) => c.active).slice(0, 5)
      .map((c, slot) => ({ slug: c.slug, slot }));
  }
  if (TF[h.get("tf")]) state.tf = h.get("tf");
  if (["yes", "no"].includes(h.get("side"))) state.side = h.get("side");
  if (h.has("ma")) state.ma = new Set(h.get("ma").split(",").map(Number).filter((n) => n === 20 || n === 50));
  if (h.has("vol")) state.vol = h.get("vol") !== "0";
  const f = h.get("f");
  state.focus = state.sel.some((x) => x.slug === f) ? f : state.sel[0]?.slug ?? null;
}

function syncControls() {
  document.querySelectorAll("#tf button").forEach((b) => b.classList.toggle("on", b.dataset.v === state.tf));
  document.querySelectorAll("#side button").forEach((b) => b.classList.toggle("on", b.dataset.v === state.side));
  document.querySelectorAll("#ma button").forEach((b) => b.classList.toggle("on", state.ma.has(+b.dataset.v)));
  $("#vol-toggle").checked = state.vol;
  $("#hide-small").checked = state.hideSmall;
}

function bindControls() {
  $("#tf").addEventListener("click", (e) => {
    const v = e.target.dataset.v; if (!v) return;
    state.tf = v; syncControls(); saveHash(); renderChart();
  });
  $("#side").addEventListener("click", (e) => {
    const v = e.target.dataset.v; if (!v) return;
    state.side = v; syncControls(); saveHash(); renderChart(true);
  });
  $("#ma").addEventListener("click", (e) => {
    const v = +e.target.dataset.v; if (!v) return;
    state.ma.has(v) ? state.ma.delete(v) : state.ma.add(v);
    syncControls(); saveHash(); renderChart(true);
  });
  $("#vol-toggle").addEventListener("change", (e) => { state.vol = e.target.checked; saveHash(); renderChart(true); });
  $("#hide-small").addEventListener("change", (e) => { state.hideSmall = e.target.checked; renderTable(); });
  $("#table tbody").addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-slug]");
    if (tr) toggle(tr.dataset.slug);
  });
  $("#table thead").addEventListener("click", (e) => {
    const k = e.target.dataset.k; if (!k) return;
    state.sort = { k, asc: state.sort.k === k ? !state.sort.asc : k === "name" };
    renderTable();
  });
}

// ---------------------------------------------------------------- rafraîchissement

async function refresh() {
  const before = state.data?.updated;
  try { await loadCandidates(); } catch (e) { console.error(e); return; }
  if (state.data.updated === before) return;
  state.hist.clear();
  await loadHistories(state.sel.map((x) => x.slug));
  renderHeader();
  renderTable();
  renderChart(true);
}

async function main() {
  if (!LWC) { $("#chart-empty").hidden = false; $("#chart-empty").textContent = "Impossible de charger la librairie graphique."; return; }
  try { await loadCandidates(); }
  catch (e) { $("#chart-empty").hidden = false; $("#chart-empty").textContent = "Données indisponibles."; console.error(e); return; }
  readHash();
  syncControls();
  bindControls();
  initChart();
  renderHeader();
  renderTable();
  await loadHistories(state.sel.map((x) => x.slug));
  saveHash();
  renderChart();
  setInterval(refresh, REFRESH_MS);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
}

main();
