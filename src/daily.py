"""Récap quotidien par email (8h00 heure de Paris).

Lancé par daily.yml à 06:00 et 07:00 UTC : le script n'envoie que si l'heure
de Paris est entre 8h et 11h59 et qu'aucun récap n'est parti ce jour-là
(state/daily.json ne contient que la date). `--force` ignore ces deux règles.

Périmètre : uniquement les positions du marché présidentielle 2027, plus le
cash (pUSD + USDC.e) des 2 adresses. Les logs d'un dépôt public sont visibles :
aucune adresse ni aucun montant n'y est écrit, uniquement dans l'email.
"""
from __future__ import annotations

import argparse
import html
import logging
import os
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import mailer
import polymarket as pm
from check_setup import mask
from collect import read_json, write_json
from config import (
    CANDIDATES_FILE,
    DAILY_HOUR,
    DAILY_LATEST_HOUR,
    DAILY_MIN_PRICE,
    DAILY_STALE_S,
    DAILY_STATE_FILE,
    EVENT_SLUG,
    HISTORY_DIR,
    MARKET_URL,
    TIMEZONE,
)
from volatility import price_at

log = logging.getLogger("daily")
TZ = ZoneInfo(TIMEZONE)
DAY = 86400


# ---------------------------------------------------------------- déclenchement

def should_send(now: int, state: dict) -> tuple[bool, str]:
    local = datetime.fromtimestamp(now, TZ)
    today = local.date().isoformat()
    if state.get("last_sent") == today:
        return False, "récap déjà envoyé aujourd'hui"
    if not DAILY_HOUR <= local.hour < DAILY_LATEST_HOUR:
        return False, f"{local:%H:%M} à Paris, hors créneau"
    return True, today


# ---------------------------------------------------------------- calculs

def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def candidate_moves(candidates: list, histories: dict, now: int) -> dict:
    """Par slug : prix Oui actuel, prix il y a 24 h, variation (None si inconnu)."""
    out = {}
    for c in candidates:
        pts = histories.get(c["slug"]) or []
        if pts:
            last_t, cur = pts[-1]
            ref = price_at(pts, last_t - DAY)
        else:
            cur, ref = c.get("price"), None
        out[c["slug"]] = {
            "slug": c["slug"], "name": c["name"], "cur": cur, "ref": ref,
            "delta": None if cur is None or ref is None else cur - ref,
            "rel": None if cur is None or not ref else (cur - ref) / ref,
            # Volume 24 h en dollars (trades) ; à défaut, chiffre Gamma (en parts)
            "volume24h": _f(c.get("volume24h_usd")) if c.get("volume24h_usd") is not None
            else _f(c.get("volume24h")),
            "active": c.get("active", True),
        }
    return out


def market_positions(raw: list, candidates: list) -> list:
    """Positions de ce marché seulement, rattachées à leur candidat."""
    by_cond = {(c.get("condition_id") or "").lower(): c for c in candidates}
    out = []
    for p in raw:
        cond = (p.get("conditionId") or "").lower()
        cand = by_cond.get(cond)
        if cand is None and p.get("eventSlug") != EVENT_SLUG:
            continue
        size = _f(p.get("size"))
        if size <= 0:
            continue
        avg, cur = _f(p.get("avgPrice")), _f(p.get("curPrice"))
        cost = _f(p.get("initialValue")) or size * avg
        value = _f(p.get("currentValue")) if p.get("currentValue") is not None else size * cur
        side = "Oui" if str(p.get("outcome", "")).lower() == "yes" else "Non"
        out.append({
            "slug": cand["slug"] if cand else None,
            "name": cand["name"] if cand else (p.get("title") or "?"),
            "side": side, "size": size, "avg": avg, "cur": cur,
            "cost": cost, "value": value, "pnl": value - cost,
            "realized": _f(p.get("realizedPnl")),
        })
    return out


def side_delta(move: dict | None, side: str) -> float | None:
    """Variation 24 h du prix du côté détenu (Non = 1 − Oui)."""
    if not move or move["delta"] is None:
        return None
    return move["delta"] if side == "Oui" else -move["delta"]


def summarize(accounts: list, moves: dict) -> dict:
    """accounts : [{label, positions, cash (None si inconnu), errors}]"""
    total = {"value": 0.0, "cost": 0.0, "pnl": 0.0, "cash": 0.0, "pnl24": 0.0,
             "cash_known": True, "pnl24_known": True}
    for a in accounts:
        a["value"] = sum(p["value"] for p in a["positions"])
        a["cost"] = sum(p["cost"] for p in a["positions"])
        a["pnl"] = a["value"] - a["cost"]
        a["pnl24"] = 0.0
        for p in a["positions"]:
            d = side_delta(moves.get(p["slug"]), p["side"])
            p["d24"] = d
            if d is None:
                total["pnl24_known"] = False
            else:
                p["pnl24"] = d * p["size"]
                a["pnl24"] += p["pnl24"]
        for k in ("value", "cost", "pnl", "pnl24"):
            total[k] += a[k]
        if a["cash"] is None:
            total["cash_known"] = False
        else:
            total["cash"] += a["cash"]
    total["equity"] = total["value"] + total["cash"]
    return total


def listed_candidates(moves: dict, held_slugs: set) -> list:
    rows = [m for m in moves.values()
            if m["slug"] in held_slugs
            or (m["active"] and (m["cur"] or 0) >= DAILY_MIN_PRICE - 1e-9)]
    return sorted(rows, key=lambda m: m["cur"] or 0, reverse=True)


# ---------------------------------------------------------------- mise en forme

def usd(x: float, signed: bool = False) -> str:
    s = f"{abs(x):,.2f}".replace(",", " ").replace(".", ",")
    sign = ("+" if x > 0 else "−" if x < 0 else "") if signed else ("−" if x < 0 else "")
    return f"{sign}{s} $"


def num(x: float, d: int = 1, signed: bool = False) -> str:
    s = f"{x:+.{d}f}" if signed else f"{x:.{d}f}"
    return s.replace(".", ",").replace("-", "−")


def pct(p: float | None) -> str:
    return "—" if p is None else f"{num(p * 100)} %"


def px(p: float | None, signed: bool = False) -> str:
    """Prix d'un jeton en $ (3 décimales). Un jeton gagnant vaut 1 $."""
    return "—" if p is None else f"{num(p, 3, signed)} $"


def cote(p: float | None) -> str:
    """Cote décimale = 1 / prix."""
    return "—" if not p or p <= 0 else num(1 / p, 2)


def color(x: float | None) -> str:
    if not x:
        return "#555"
    return "#16a34a" if x > 0 else "#dc2626"


def build_email(day_label: str, accounts: list, total: dict, cands: list,
                held_slugs: set, warnings: list) -> tuple[str, str, str]:
    subject = (f"📊 Récap Polymarket 2027 – {day_label} – "
               f"PnL {usd(total['pnl'], True)}")
    if total["pnl24_known"]:
        subject += f" ({usd(total['pnl24'], True)} 24 h)"

    th = "style='text-align:left;padding:4px 8px;border-bottom:1px solid #ccc'"
    td = "style='padding:3px 8px'"
    tdr = "style='padding:3px 8px;text-align:right'"

    def cell(v, c=None, right=True):
        st = f"padding:3px 8px;{'text-align:right;' if right else ''}"
        if c:
            st += f"color:{c};"
        return f"<td style='{st}'>{v}</td>"

    def table(headers, rows):
        return ("<table style='border-collapse:collapse;font-size:13px'>"
                "<tr>" + "".join(f"<th {th}>{h}</th>" for h in headers) + "</tr>"
                + "".join(rows) + "</table>")

    cash_txt = usd(total["cash"]) + ("" if total["cash_known"] else " (incomplet)")
    pnl_pct = total["pnl"] / total["cost"] if total["cost"] else None
    synth = table(["", ""], [
        f"<tr><td {td}>Valeur totale (positions + cash)</td>{cell('<b>' + usd(total['equity']) + '</b>')}</tr>",
        f"<tr><td {td}>Positions présidentielle</td>{cell(usd(total['value']))}</tr>",
        f"<tr><td {td}>Cash (pUSD + USDC.e)</td>{cell(cash_txt)}</tr>",
        f"<tr><td {td}>Coût d'entrée</td>{cell(usd(total['cost']))}</tr>",
        f"<tr><td {td}>PnL latent</td>{cell(usd(total['pnl'], True) + ' (' + pct(pnl_pct) + ')', color(total['pnl']))}</tr>",
        f"<tr><td {td}>Variation 24 h des positions</td>"
        + cell(usd(total['pnl24'], True) if total['pnl24_known'] else "—", color(total['pnl24'])) + "</tr>",
    ])

    acc_rows = []
    for a in accounts:
        acc_rows.append(
            f"<tr><td {td}>{html.escape(a['label'])}</td>"
            + cell(len(a["positions"])) + cell(usd(a["value"]))
            + cell("—" if a["cash"] is None else usd(a["cash"]))
            + cell(usd(a["pnl"], True), color(a["pnl"]))
            + cell(usd(a["pnl24"], True), color(a["pnl24"])) + "</tr>")
    acc = table(["Adresse", "Pos.", "Valeur", "Cash", "PnL latent", "24 h"], acc_rows)

    pos_rows, text_pos = [], []
    for a in accounts:
        for p in sorted(a["positions"], key=lambda p: p["value"], reverse=True):
            ppct = p["pnl"] / p["cost"] if p["cost"] else None
            pos_rows.append(
                f"<tr><td {td}><b>{html.escape(p['name'])}</b></td><td {td}>{p['side']}</td>"
                f"<td {td}>{html.escape(a['short'])}</td>"
                + cell(num(p["size"], 0)) + cell(px(p["avg"])) + cell(px(p["cur"]))
                + cell(usd(p["value"]))
                + cell(f"{usd(p['pnl'], True)} ({pct(ppct)})", color(p["pnl"]))
                + cell(px(p["d24"], True), color(p["d24"])) + "</tr>")
            text_pos.append(f"- {p['name']} {p['side']} [{a['short']}] : {usd(p['value'])}, "
                            f"PnL {usd(p['pnl'], True)}")
    pos = (table(["Candidat", "Côté", "Adr.", "Parts", "Prix moyen", "Prix actuel",
                  "Valeur", "PnL latent", "Var. 24 h"], pos_rows)
           if pos_rows else "<p>Aucune position ouverte sur ce marché.</p>")

    cand_rows, text_c = [], []
    for m in cands:
        star = " ★" if m["slug"] in held_slugs else ""
        d = m["delta"]
        cand_rows.append(
            f"<tr><td {td}><b>{html.escape(m['name'])}</b>{star}</td>"
            + cell(px(m["cur"])) + cell(px(None if m["cur"] is None else 1 - m["cur"]))
            + cell(cote(m["cur"])) + cell(px(m["ref"]))
            + cell(px(d, True), color(d))
            + cell("—" if m["rel"] is None else f"{num(m['rel'] * 100, 1, True)} %", color(d))
            + cell(usd(m["volume24h"]).replace(",00 $", " $")) + "</tr>")
        text_c.append(f"- {m['name']}{star} : {px(m['cur'])} (cote {cote(m['cur'])})"
                      + ("" if d is None else f" ({px(d, True)})"))
    cand = table(["Candidat", "Oui", "Non", "Cote Oui", "Oui il y a 24 h", "Var. $", "Var. %",
                  "Volume 24 h"],
                 cand_rows)

    warn = "".join(f"<p style='color:#b45309'>⚠️ {html.escape(w)}</p>" for w in warnings)
    body = (
        "<div style='font-family:Arial,sans-serif;font-size:14px;color:#111'>"
        f"<h2>📊 Récap Polymarket – Présidentielle 2027 – {day_label}</h2>{warn}"
        f"<h3>Synthèse</h3>{synth}"
        f"<h3>Par adresse</h3>{acc}"
        f"<h3>Positions</h3>{pos}"
        f"<h3>Candidats (Oui ≥ {px(DAILY_MIN_PRICE)} ou détenus ★)</h3>{cand}"
        f"<p><a href='{MARKET_URL}'>Ouvrir le marché sur Polymarket</a></p>"
        "<p style='color:#888;font-size:12px'>Prix des jetons en $ (1 $ si le pari gagne, "
        "0 sinon) ; Non = 1 − Oui ; cote décimale = 1 / prix. PnL latent = valeur actuelle "
        "− coût d'entrée. Var. 24 h d'une position = variation du prix du jeton détenu "
        "(par jeton) ; total = jetons × variation.</p></div>")
    text = "\n".join(
        [subject, *warnings, "",
         f"Valeur totale : {usd(total['equity'])} (cash {cash_txt})",
         f"PnL latent : {usd(total['pnl'], True)}", "", "Positions :", *text_pos,
         "", "Candidats :", *text_c, "", MARKET_URL])
    return subject, body, text


# ---------------------------------------------------------------- principal

def load_accounts() -> tuple[list, list]:
    """Lit positions et cash des 3 adresses. Renvoie (comptes, avertissements)."""
    accounts, warnings = [], []
    for i in (1, 2, 3):
        addr = os.environ.get(f"POLY_ADDR_{i}", "").strip()
        acc = {"label": f"Adresse {i} ({mask(addr)})", "short": f"#{i}",
               "raw": [], "positions": [], "cash": None}
        if not addr:
            warnings.append(f"Adresse {i} non configurée.")
            log.warning("Adresse %d non configurée", i)
            accounts.append(acc)
            continue
        try:
            acc["raw"] = pm.get_positions(addr)
        except Exception as exc:
            warnings.append(f"Positions de l'adresse {i} indisponibles ({type(exc).__name__}).")
            log.error("Adresse %d : positions indisponibles (%s)", i, type(exc).__name__)
        try:
            acc["cash"] = pm.get_cash(addr)
        except Exception as exc:
            warnings.append(f"Cash de l'adresse {i} indisponible.")
            log.error("Adresse %d : cash indisponible (%s)", i, type(exc).__name__)
        accounts.append(acc)
    return accounts, warnings


def run(now: int | None = None, force: bool = False, send=mailer.send,
        loader=load_accounts) -> int:
    now = now or int(time.time())
    state = read_json(DAILY_STATE_FILE, None) or {}
    ok, info = should_send(now, state)
    if not ok and not force:
        log.info("Pas d'envoi : %s", info)
        return 0
    today = datetime.fromtimestamp(now, TZ)

    data = read_json(CANDIDATES_FILE, None) or {}
    candidates = data.get("candidates", [])
    histories = {c["slug"]: (read_json(HISTORY_DIR / f"{c['slug']}.json", None) or {}).get("p", [])
                 for c in candidates}
    moves = candidate_moves(candidates, histories, now)

    accounts, warnings = loader()
    if now - int(data.get("updated") or 0) > DAILY_STALE_S:
        warnings.append("Les prix des candidats n'ont pas été mis à jour depuis plus de 2 h.")
    for a in accounts:
        a["positions"] = market_positions(a.pop("raw", []), candidates)
    total = summarize(accounts, moves)
    held = {p["slug"] for a in accounts for p in a["positions"] if p["slug"]}
    cands = listed_candidates(moves, held)

    subject, body, text = build_email(today.strftime("%d/%m/%Y"), accounts, total,
                                      cands, held, warnings)
    try:
        send(subject, body, text)
    except Exception as exc:
        log.error("Échec d'envoi du récap : %s", type(exc).__name__)
        return 1
    if ok:  # un envoi forcé hors créneau ne bloque pas le récap du jour
        state["last_sent"] = info
        write_json(DAILY_STATE_FILE, state, pretty=True)
    log.info("Récap envoyé : %d position(s), %d candidat(s), %d avertissement(s)",
             sum(len(a["positions"]) for a in accounts), len(cands), len(warnings))
    return 1 if warnings else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="envoyer même hors créneau")
    sys.exit(run(force=ap.parse_args().force))
