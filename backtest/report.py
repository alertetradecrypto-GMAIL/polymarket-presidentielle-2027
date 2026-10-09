"""Rapport HTML du backtest : backtest/out/results.json → backtest/out/report.html."""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).parent / "out"
NAMES = {"marine-le-pen": "Marine Le Pen", "edouard-philippe": "Édouard Philippe",
         "jean-luc-melenchon": "Jean-Luc Mélenchon", "david-lisnard": "David Lisnard"}
SHOWN = "maker_souple_3_yes+no"

PAGE = """<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Backtest Polymarket</title>
<script src="https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"></script>
<style>
:root{--bg:#fff;--fg:#1b1d22;--mut:#6b7280;--line:#e5e7eb;--pos:#0f7b4d;--neg:#b42318}
@media (prefers-color-scheme:dark){:root{--bg:#131722;--fg:#e6e8ee;--mut:#9aa1ad;--line:#2a2e39;--pos:#3ccf8e;--neg:#ff6b5e}}
body{background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif;margin:0 auto;max-width:1100px;padding:16px}
h1{font-size:20px}h2{font-size:16px;margin-top:28px}small,.mut{color:var(--mut)}
table{border-collapse:collapse;width:100%;margin:8px 0;font-variant-numeric:tabular-nums}
th,td{border-bottom:1px solid var(--line);padding:4px 6px;text-align:right}th:first-child,td:first-child{text-align:left}
.pos{color:var(--pos)}.neg{color:var(--neg)}.chart{height:260px;margin-bottom:6px}
.wrap{overflow-x:auto}
</style></head><body>
<h1>Backtest stratégies 1 + 3 (maker + valeur théorique sondages)</h1>
<p class="mut">Généré le __GEN__ · source des prix : __SRC__ · 100 $ par trade · aucun ordre réel.</p>
<div id="tables"></div>
<h2>Prix « Oui » (bleu) et valeur théorique (orange, incertitude élargie) — trades __SHOWN__</h2>
<div id="charts"></div>
<script>
const R = __DATA__, NAMES = __NAMES__, SHOWN = "__SHOWN__";
const fmt = v => v == null ? "–" : `<span class="${v>=0?'pos':'neg'}">${v.toFixed(2)}</span>`;
let html = "";
for (const [name, v] of Object.entries(R.variants)) {
  html += `<h2>Variante ${name.replace('_',' ')}</h2><div class="wrap"><table><tr><th>Mode</th><th>Écart</th><th>Sens</th>
  <th>PnL total $</th><th>dont réalisé</th><th>Trades clos</th><th>Ouverts</th><th>DD max</th>` +
  Object.keys(NAMES).map(s => `<th>${NAMES[s].split(' ').pop()}</th>`).join("") + "</tr>";
  for (const r of v.results) html += `<tr><td>${r.mode}</td><td>${r.gap_pts} pts</td><td>${r.sides}</td>
   <td>${fmt(r.pnl)}</td><td>${fmt(r.realized)}</td><td>${r.trades_closed}</td><td>${r.open}</td><td>${fmt(r.max_drawdown)}</td>` +
   Object.keys(NAMES).map(s => `<td>${fmt(r.pnl_by_candidate[s] ?? null)}</td>`).join("") + "</tr>";
  const bh = v.buy_and_hold_yes;
  html += `<tr><td colspan="3"><b>Achat-conservation « Oui »</b></td><td>${fmt(bh.total)}</td><td colspan="4"></td>` +
   Object.keys(NAMES).map(s => `<td>${fmt(bh[s])}</td>`).join("") + "</tr></table></div>";
}
document.getElementById("tables").innerHTML = html;
const dark = matchMedia("(prefers-color-scheme: dark)").matches;
const v = R.variants.incertitude_elargie;
for (const slug of Object.keys(NAMES)) {
  const box = document.createElement("div");
  box.innerHTML = `<b>${NAMES[slug]}</b><div class="chart"></div>`;
  document.getElementById("charts").appendChild(box);
  const ch = LightweightCharts.createChart(box.querySelector(".chart"), {autoSize: true,
    layout: {background: {color: "transparent"}, textColor: dark ? "#9aa1ad" : "#6b7280"},
    grid: {vertLines: {visible: false}, horzLines: {color: dark ? "#2a2e39" : "#eef0f3"}},
    rightPriceScale: {borderVisible: false}, timeScale: {borderVisible: false}});
  const price = ch.addLineSeries({color: "#2962ff", lineWidth: 2, priceFormat: {type: "percent"}});
  price.setData(R.prices[slug].map(([t, p]) => ({time: t, value: p * 100})));
  const fv = ch.addLineSeries({color: "#f08c00", lineWidth: 2, lineStyle: 2});
  fv.setData(Object.entries(v.fair_values).map(([d, f]) => ({time: d, value: f[slug] * 100})));
  const mk = [];
  for (const t of v.trades[SHOWN] || []) if (t.slug === slug) {
    mk.push({time: t.t_in, position: "belowBar", color: "#0f7b4d", shape: "arrowUp", text: `achat ${t.side}`});
    mk.push({time: t.t_out, position: "aboveBar", color: "#b42318", shape: "arrowDown", text: `vente ${t.pnl}$`});
  }
  const open = v.results.find(r => `${r.mode}_${r.gap_pts}_${r.sides}` === SHOWN).open_positions;
  for (const o of open) if (o.slug === slug)
    mk.push({time: o.since, position: "belowBar", color: "#0f7b4d", shape: "arrowUp", text: `achat ${o.side} (ouvert)`});
  mk.sort((a, b) => (typeof a.time === "string" ? Date.parse(a.time)/1000 : a.time) - (typeof b.time === "string" ? Date.parse(b.time)/1000 : b.time));
  price.setMarkers(mk.map(m => ({...m, time: typeof m.time === "string" ? Date.parse(m.time)/1000 : m.time})));
  ch.timeScale().fitContent();
}
</script></body></html>"""


def main() -> int:
    r = json.loads((OUT / "results.json").read_text(encoding="utf-8"))
    for v in r["variants"].values():
        v.pop("equity", None)
        v["trades"] = {SHOWN: v["trades"].get(SHOWN, [])}
    html = (PAGE.replace("__DATA__", json.dumps(r, ensure_ascii=False))
            .replace("__NAMES__", json.dumps(NAMES, ensure_ascii=False))
            .replace("__SHOWN__", SHOWN).replace("__GEN__", r["generated"])
            .replace("__SRC__", "API CLOB horaire" if r["source"] == "api" else "historique du dépôt"))
    (OUT / "report.html").write_text(html, encoding="utf-8")
    print(OUT / "report.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
