"""Email « TRADE JOURNALIER POLYMARKET » : tous les trades de la veille (Paris).

Lancé par daily.yml juste après le récap (mêmes créneaux 06:00/07:00 UTC).
Envoi entre 8h et 11h59 à Paris, une fois par jour (state/trades_daily.json
ne contient que la date). `--force` ignore ces règles ; `--day AAAA-MM-JJ`
choisit le jour couvert (par défaut la veille).

Contenu : résumé par candidat, plus gros ordres, CSV complet en pièce jointe.
Résumé et gros ordres ignorent les trades < TRADES_MIN_USD (allers-retours
de farming) ; le CSV contient tout.
"""
from __future__ import annotations

import argparse
import csv
import html
import io
import logging
import sys
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import mailer
from collect import read_json, write_json
from config import (
    CANDIDATES_FILE,
    DAILY_HOUR,
    DAILY_LATEST_HOUR,
    MARKET_URL,
    TIMEZONE,
    TRADES_DAILY_STATE_FILE,
    TRADES_EMAIL_SUBJECT,
    TRADES_MIN_USD,
    TRADES_TOP_N,
)
from daily import num, usd
from trades import COLS, load_day, pending_backfills

log = logging.getLogger("trades_daily")
TZ = ZoneInfo(TIMEZONE)
PROFILE_URL = "https://polymarket.com/profile/"
TX_URL = "https://polygonscan.com/tx/"


def should_send(now: int, state: dict) -> tuple[bool, str]:
    local = datetime.fromtimestamp(now, TZ)
    today = local.date().isoformat()
    if state.get("last_sent") == today:
        return False, "déjà envoyé aujourd'hui"
    if not DAILY_HOUR <= local.hour < DAILY_LATEST_HOUR:
        return False, f"{local:%H:%M} à Paris, hors créneau"
    return True, today


def as_dicts(rows: list) -> list[dict]:
    return [dict(zip(COLS, r)) for r in rows]


def summarize(trades: list[dict], min_usd: float = TRADES_MIN_USD) -> list[dict]:
    """Par candidat (trades ≥ min_usd) : volume, nb, flux nets Oui/Non, plus gros ordre."""
    out: dict[str, dict] = {}
    for t in trades:
        if t["usd"] < min_usd:
            continue
        s = out.setdefault(t["cand"], {"cand": t["cand"], "usd": 0.0, "n": 0,
                                       "net_yes": 0.0, "net_no": 0.0, "max": 0.0,
                                       "wallets": set()})
        s["usd"] += t["usd"]
        s["n"] += 1
        sign = 1 if t["side"] == "BUY" else -1
        s["net_yes" if t["out"] == "Yes" else "net_no"] += sign * t["usd"]
        s["max"] = max(s["max"], t["usd"])
        s["wallets"].add(t["wallet"])
    for s in out.values():
        s["wallets"] = len(s["wallets"])
    return sorted(out.values(), key=lambda s: s["usd"], reverse=True)


def top_trades(trades: list[dict], n: int = TRADES_TOP_N,
               min_usd: float = TRADES_MIN_USD) -> list[dict]:
    big = [t for t in trades if t["usd"] >= min_usd]
    return sorted(big, key=lambda t: (-t["usd"], -t["t"]))[:n]


def to_csv(trades: list[dict], names: dict) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["heure_paris", "candidat", "issue", "sens", "prix", "parts", "montant_usd",
                "adresse", "pseudo", "transaction"])
    for t in sorted(trades, key=lambda t: t["t"]):
        w.writerow([datetime.fromtimestamp(t["t"], TZ).strftime("%Y-%m-%d %H:%M:%S"),
                    names.get(t["cand"], t["cand"]), t["out"], t["side"],
                    str(t["price"]).replace(".", ","), str(t["size"]).replace(".", ","),
                    str(t["usd"]).replace(".", ","), t["wallet"], t["name"], t["tx"]])
    return ("﻿" + buf.getvalue()).encode("utf-8")   # BOM : accents OK dans Excel


def color(x: float) -> str:
    return "#555" if abs(x) < 0.005 else ("#16a34a" if x > 0 else "#dc2626")


def build_email(day: str, trades: list[dict], names: dict, warnings: list):
    label = date.fromisoformat(day).strftime("%d/%m/%Y")
    subject = f"{TRADES_EMAIL_SUBJECT} – {label}"
    summary = summarize(trades)
    top = top_trades(trades)
    total_usd = sum(t["usd"] for t in trades)

    th = "style='text-align:left;padding:4px 8px;border-bottom:1px solid #ccc'"

    def cell(v, c=None, right=True):
        st = f"padding:3px 8px;{'text-align:right;' if right else ''}"
        if c:
            st += f"color:{c};"
        return f"<td style='{st}'>{v}</td>"

    def table(headers, rows):
        return ("<table style='border-collapse:collapse;font-size:13px'>"
                "<tr>" + "".join(f"<th {th}>{h}</th>" for h in headers) + "</tr>"
                + "".join(rows) + "</table>")

    def name(slug):
        return html.escape(names.get(slug, slug))

    sum_rows = [
        "<tr>" + cell(f"<b>{name(s['cand'])}</b>", right=False) + cell(usd(s["usd"]))
        + cell(s["n"]) + cell(s["wallets"])
        + cell(usd(s["net_yes"], True), color(s["net_yes"]))
        + cell(usd(s["net_no"], True), color(s["net_no"])) + cell(usd(s["max"])) + "</tr>"
        for s in summary]
    sum_html = (table(["Candidat", "Volume", "Trades", "Adresses", "Net Oui", "Net Non",
                       "Plus gros"], sum_rows)
                if sum_rows else "<p>Aucun trade significatif.</p>")

    top_rows = []
    for t in top:
        hour = datetime.fromtimestamp(t["t"], TZ).strftime("%H:%M")
        who = (f"<a href='{PROFILE_URL}{t['wallet']}'><code>{t['wallet']}</code></a>"
               + (f" ({html.escape(t['name'])})" if t["name"] else ""))
        tx = f"<a href='{TX_URL}{t['tx']}'>tx</a>" if t["tx"] else ""
        side_c = "#16a34a" if t["side"] == "BUY" else "#dc2626"
        top_rows.append(
            "<tr>" + cell(hour, right=False) + cell(f"<b>{name(t['cand'])}</b>", right=False)
            + cell("Oui" if t["out"] == "Yes" else "Non", right=False)
            + cell("Achat" if t["side"] == "BUY" else "Vente", side_c, right=False)
            + cell(num(t["price"], 3)) + cell(num(t["size"], 0)) + cell(f"<b>{usd(t['usd'])}</b>")
            + cell(who, right=False) + cell(tx, right=False) + "</tr>")
    top_html = (table(["Heure", "Candidat", "Issue", "Sens", "Prix", "Parts", "Montant",
                       "Adresse", ""], top_rows)
                if top_rows else "<p>Aucun.</p>")

    warn = "".join(f"<p style='color:#b45309'>⚠️ {html.escape(w)}</p>" for w in warnings)
    body = (
        "<div style='font-family:Arial,sans-serif;font-size:14px;color:#111'>"
        f"<h2>{TRADES_EMAIL_SUBJECT} – {label}</h2>{warn}"
        f"<p><b>{len(trades)}</b> trades, <b>{usd(total_usd)}</b> échangés "
        f"(00h00–24h00, heure de Paris). Détail complet en pièce jointe (CSV).</p>"
        f"<h3>Par candidat</h3>{sum_html}"
        f"<h3>{len(top)} plus gros ordres</h3>{top_html}"
        f"<p><a href='{MARKET_URL}'>Ouvrir le marché sur Polymarket</a></p>"
        f"<p style='color:#888;font-size:12px'>Trades côté taker (définition du volume "
        f"Polymarket). Résumé et gros ordres : trades ≥ {usd(TRADES_MIN_USD)} uniquement "
        "(les allers-retours de farming sont ignorés), le CSV contient tout. Net = achats − "
        "ventes en $ du jeton concerné.</p></div>")
    text = "\n".join(
        [subject, *warnings, "", f"{len(trades)} trades, {usd(total_usd)}", "",
         *(f"- {names.get(s['cand'], s['cand'])} : {usd(s['usd'])}, {s['n']} trades"
           for s in summary), "", "CSV complet en pièce jointe.", MARKET_URL])
    return subject, body, text


def run(now: int | None = None, force: bool = False, day: str | None = None,
        send=mailer.send) -> int:
    now = now or int(time.time())
    state = read_json(TRADES_DAILY_STATE_FILE, None) or {}
    ok, info = should_send(now, state)
    if not ok and not force:
        log.info("Pas d'envoi : %s", info)
        return 0
    day = day or (datetime.fromtimestamp(now, TZ).date() - timedelta(days=1)).isoformat()

    candidates = (read_json(CANDIDATES_FILE, None) or {}).get("candidates", [])
    names = {c["slug"]: c["name"] for c in candidates}
    trades = as_dicts(load_day(day))
    warnings = []
    pending = pending_backfills()
    if pending:
        warnings.append(f"Historique encore en cours de récupération pour {len(pending)} "
                        f"candidat(s) : {', '.join(pending[:8])}{'…' if len(pending) > 8 else ''}.")

    subject, body, text = build_email(day, trades, names, warnings)
    attachments = [(f"trades_{day}.csv", to_csv(trades, names), "text", "csv")]
    try:
        send(subject, body, text, attachments=attachments)
    except Exception as exc:
        log.error("Échec d'envoi : %s", type(exc).__name__)
        return 1
    if ok:
        state["last_sent"] = info
        write_json(TRADES_DAILY_STATE_FILE, state, pretty=True)
    log.info("Email trades envoyé : %s, %d trades", day, len(trades))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="envoyer même hors créneau")
    ap.add_argument("--day", help="jour couvert AAAA-MM-JJ (défaut : la veille)")
    a = ap.parse_args()
    sys.exit(run(force=a.force, day=a.day))
