"""Visuels PNG bilingues FR/EN pour X, rendus par Playwright (Chromium).

Mêmes colonnes que les emails, sans étoile, sans portefeuille, sans lien.
- Visuel A (daily_html) : tableau quotidien des candidats.
- Visuel B (alert_html) : alerte « Mouvement fort / Strong move ».
Pied de page : date, « Source: Polymarket », « Not financial advice ».
"""
from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

WIDTH = 1200
SCALE = 2

MOIS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
        "septembre", "octobre", "novembre", "décembre")
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December")


# ---------------------------------------------------------------- formats (neutres FR/EN)

def date_label(d: datetime, with_time: bool = False) -> str:
    fr = f"{d.day} {MOIS[d.month - 1]} {d.year}"
    en = f"{MONTHS[d.month - 1]} {d.day}, {d.year}"
    out = f"{fr} · {en}"
    return out + f" · {d:%H:%M} (Paris)" if with_time else out


def pct(p: float | None) -> str:
    return "—" if p is None else f"{p * 100:.1f}%"


def pts(d: float | None) -> str:
    return "—" if d is None else f"{d * 100:+.1f}".replace("-", "−") + " pt"


def rel(r: float | None) -> str:
    return "—" if r is None else f"{r * 100:+.1f}".replace("-", "−") + "%"


def money(x: float | None) -> str:
    if x is None:
        return "—"
    if x >= 1e6:
        return f"${x / 1e6:.1f}M"
    if x >= 1e4:
        return f"${x / 1e3:.0f}k"
    if x >= 1e3:
        return f"${x / 1e3:.1f}k"
    return f"${x:.0f}"


def tone(x: float | None) -> str:
    if not x:
        return "flat"
    return "up" if x > 0 else "down"


# ---------------------------------------------------------------- gabarit commun

CSS = """
:root { --ink:#0f172a; --muted:#64748b; --line:#e2e8f0; --bg:#f8fafc; --card:#ffffff;
        --up:#15803d; --down:#b91c1c; --accent:#1d4ed8; --hot:#b91c1c; }
* { box-sizing:border-box; margin:0; padding:0; }
body { width:%(w)dpx; background:var(--bg); color:var(--ink);
       font-family:"Inter","Helvetica Neue","DejaVu Sans",Arial,sans-serif;
       -webkit-font-smoothing:antialiased; }
.wrap { padding:44px 52px 36px; }
.kicker { font-size:15px; font-weight:700; letter-spacing:.14em; text-transform:uppercase;
          color:var(--accent); }
.kicker.hot { color:var(--hot); }
h1 { font-size:40px; line-height:1.15; font-weight:800; margin-top:10px; letter-spacing:-.01em; }
h1 .en { display:block; font-size:24px; font-weight:500; color:var(--muted); margin-top:6px;
         letter-spacing:0; }
.card { background:var(--card); border:1px solid var(--line); border-radius:14px;
        margin-top:28px; overflow:hidden; }
table { width:100%%; border-collapse:collapse; font-variant-numeric:tabular-nums; }
th { text-align:right; font-size:14px; font-weight:600; color:var(--muted);
     padding:16px 22px 14px; border-bottom:1px solid var(--line); line-height:1.3; }
th .en { display:block; font-weight:400; font-size:12.5px; }
th:first-child, td:first-child, th.l, td.name { text-align:left; }
td { text-align:right; font-size:21px; padding:15px 22px; border-bottom:1px solid var(--line); }
tr:last-child td { border-bottom:none; }
td.name { font-weight:700; }
td.rank { width:56px; color:var(--muted); font-weight:600; padding-right:0; }
td.big { font-weight:800; font-size:23px; }
td.sub { color:var(--muted); }
.up { color:var(--up); } .down { color:var(--down); } .flat { color:var(--muted); }
.foot { display:flex; justify-content:space-between; margin-top:20px; font-size:15px;
        color:var(--muted); }
.foot b { color:var(--ink); font-weight:600; }
"""


def _page(body: str) -> str:
    return ("<!doctype html><html><head><meta charset='utf-8'>"
            "<link rel='preconnect' href='https://fonts.gstatic.com'>"
            "<link href='https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800"
            "&display=swap' rel='stylesheet'>"
            f"<style>{CSS % {'w': WIDTH}}</style></head><body><div class='wrap'>{body}"
            "</div></body></html>")


def _th(fr: str, en: str, cls: str = "") -> str:
    attr = f" class='{cls}'" if cls else ""
    return f"<th{attr}>{html.escape(fr)}<span class='en'>{html.escape(en)}</span></th>"


def _foot(label: str) -> str:
    return (f"<div class='foot'><span>{html.escape(label)}</span>"
            "<span><b>Source: Polymarket</b> · Not financial advice</span></div>")


# ---------------------------------------------------------------- visuel A : quotidien

def daily_html(rows: list, when: datetime) -> str:
    """rows : [{name, cur, ref, delta, rel, volume24h}] déjà triés et filtrés."""
    head = ("<tr><th></th>" + _th("Candidat", "Candidate", "l") + _th("Oui", "Yes")
            + _th("Il y a 24 h", "24h ago") + _th("Var. pt", "Change (pt)")
            + _th("Var. %", "Change (%)") + _th("Volume 24 h", "24h volume") + "</tr>")
    body = []
    for i, m in enumerate(rows, 1):
        t = tone(m["delta"])
        body.append(
            f"<tr><td class='rank'>{i}</td><td class='name'>{html.escape(m['name'])}</td>"
            f"<td class='big'>{pct(m['cur'])}</td><td class='sub'>{pct(m['ref'])}</td>"
            f"<td class='{t}'>{pts(m['delta'])}</td><td class='{t}'>{rel(m['rel'])}</td>"
            f"<td class='sub'>{money(m.get('volume24h'))}</td></tr>")
    return _page(
        "<div class='kicker'>Polymarket · Présidentielle 2027</div>"
        "<h1>Les cotes du jour<span class='en'>French 2027 presidential election — "
        "today's odds</span></h1>"
        f"<div class='card'><table>{head}{''.join(body)}</table></div>"
        + _foot(date_label(when)))


# ---------------------------------------------------------------- visuel B : alerte

WINDOW_EN = {"24h": "24h", "7j": "7d"}


def alert_html(rows: list, when: datetime) -> str:
    """rows : [{name, window, ref, cur, delta, rel, sens}] (déjà triés)."""
    head = ("<tr>" + _th("Candidat", "Candidate") + _th("Fenêtre", "Window")
            + _th("Avant", "Before") + _th("Maintenant", "Now")
            + _th("Var. relative", "Relative change") + _th("Var. absolue", "Absolute change")
            + "</tr>")
    body = []
    for a in rows:
        t = "up" if a["sens"] == "up" else "down"
        arrow = "▲" if t == "up" else "▼"
        win = a["window"] if a["window"] == WINDOW_EN.get(a["window"]) else \
            f"{a['window']} · {WINDOW_EN.get(a['window'], a['window'])}"
        body.append(
            f"<tr><td class='name'>{html.escape(a['name'])}</td><td class='sub'>{win}</td>"
            f"<td class='sub'>{pct(a['ref'])}</td><td class='big'>{pct(a['cur'])}</td>"
            f"<td class='{t}'><b>{arrow} {rel(a['rel'])}</b></td>"
            f"<td class='{t}'>{pts(a['delta'])}</td></tr>")
    return _page(
        "<div class='kicker hot'>Polymarket · Présidentielle 2027</div>"
        "<h1>Mouvement fort<span class='en'>Strong move</span></h1>"
        f"<div class='card'><table>{head}{''.join(body)}</table></div>"
        + _foot(date_label(when, with_time=True)))


# ---------------------------------------------------------------- rendu

def to_png(page_html: str, out: Path) -> Path:
    from playwright.sync_api import sync_playwright

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".html").write_text(page_html, encoding="utf-8")   # pour contrôle
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": WIDTH, "height": 600},
                                    device_scale_factor=SCALE)
            page.set_content(page_html, wait_until="load", timeout=20000)
            try:   # police web si le réseau la sert, sinon police système
                page.wait_for_function("document.fonts.status === 'loaded'", timeout=5000)
            except Exception:
                pass
            page.locator(".wrap").screenshot(path=str(out))
        finally:
            browser.close()
    return out
