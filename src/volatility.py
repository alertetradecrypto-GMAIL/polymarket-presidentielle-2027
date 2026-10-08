"""Contrôle de volatilité → email « URGENT ».

Lancé après collect.py (collect.yml, toutes les 15 min).

Règles :
- Fenêtres 24 h et 7 j (seuils dans config.ALERT_WINDOWS) : variation relative
  ET absolue du prix « Oui » entre le dernier point et le point de référence.
- Confirmation : seuil dépassé sur 2 collectes consécutives (anti-pic isolé).
- Alerte au franchissement : une seule alerte tant que le seuil reste dépassé.
- Rappel : au plus toutes les 6 h, et seulement si le cours a encore bougé
  d'au moins 1 pt dans le même sens depuis la dernière alerte.
- Réarmement : quand la variation repasse sous le seuil, l'état est effacé.
- L'état (state/alerts.json) n'enregistre une alerte que si l'email est parti.
"""
from __future__ import annotations

import bisect
import html
import logging
import sys
import time

import mailer
from collect import read_json, write_json
from config import (
    ALERT_CONFIRM_PASSES,
    ALERT_MAX_AGE_S,
    ALERT_REF_TOLERANCE_S,
    ALERT_REMIND_ABS,
    ALERT_REMIND_COOLDOWN_H,
    ALERT_STATE_FILE,
    ALERT_WINDOWS,
    CANDIDATES_FILE,
    HISTORY_DIR,
    MARKET_URL,
)

log = logging.getLogger("volatility")

EPS = 1e-9
SENS = ("up", "down")


# ---------------------------------------------------------------- calcul

def price_at(points: list, target: int, tol: int = ALERT_REF_TOLERANCE_S) -> float | None:
    """Prix du point le plus proche de `target`, à ±tol près (points triés par t)."""
    if not points:
        return None
    times = [p[0] for p in points]
    i = bisect.bisect_left(times, target)
    best = None
    for j in (i - 1, i):
        if 0 <= j < len(points):
            gap = abs(points[j][0] - target)
            if gap <= tol and (best is None or gap < best[0]):
                best = (gap, points[j][1])
    return None if best is None else best[1]


def evaluate(candidates: list, histories: dict, state: dict, now: int) -> list[dict]:
    """Met à jour les compteurs de `state` et renvoie les alertes à envoyer."""
    keys = state.setdefault("keys", {})
    alerts = []
    for c in candidates:
        if not c.get("active", True):
            continue
        slug = c["slug"]
        pts = histories.get(slug) or []
        if not pts:
            continue
        last_t, cur = pts[-1]
        if now - last_t > ALERT_MAX_AGE_S:
            continue  # données périmées : état inchangé
        for label, duration, rel_thr, abs_thr in ALERT_WINDOWS:
            ref = price_at(pts, last_t - duration)
            if ref is None or ref <= 0:
                continue  # historique insuffisant : état inchangé
            delta = cur - ref
            rel = delta / ref
            triggered = abs(rel) > rel_thr and abs(delta) >= abs_thr - EPS
            for sens in SENS:
                key = f"{slug}|{label}|{sens}"
                hit = triggered and (delta > 0) == (sens == "up")
                if not hit:
                    keys.pop(key, None)  # réarmement
                    continue
                e = keys.setdefault(key, {"streak": 0, "seen_t": 0})
                if last_t > e["seen_t"]:  # une collecte = un nouveau point
                    e["streak"] += 1
                    e["seen_t"] = last_t
                if e["streak"] < ALERT_CONFIRM_PASSES:
                    continue
                if "alert_t" in e:
                    gain = (cur - e["alert_price"]) * (1 if sens == "up" else -1)
                    if (now - e["alert_t"] < ALERT_REMIND_COOLDOWN_H * 3600
                            or gain < ALERT_REMIND_ABS - EPS):
                        continue
                    kind = "rappel"
                else:
                    kind = "nouvelle"
                alerts.append({
                    "key": key, "slug": slug, "name": c["name"], "window": label,
                    "sens": sens, "ref": ref, "cur": cur, "delta": delta,
                    "rel": rel, "kind": kind,
                })
    return alerts


def mark_sent(state: dict, alerts: list, now: int) -> None:
    for a in alerts:
        state["keys"][a["key"]].update(alert_t=now, alert_price=a["cur"])


# ---------------------------------------------------------------- email

def _num(x: float, signed: bool = False) -> str:
    return (f"{x:+.1f}" if signed else f"{x:.1f}").replace(".", ",")


def _pct(p: float) -> str:
    return f"{_num(p * 100)} %"


def build_email(alerts: list) -> tuple[str, str, str]:
    alerts = sorted(alerts, key=lambda a: abs(a["rel"]), reverse=True)
    top = alerts[0]
    subject = (f"🚨 URGENT — {top['name']} {_num(top['rel'] * 100, True)} % {top['window']} "
               f"({_num(top['ref'] * 100)} → {_num(top['cur'] * 100)} %)")
    others = len({a["slug"] for a in alerts}) - 1
    if others:
        subject += f" · +{others} autre{'s' if others > 1 else ''}"

    # Regroupement par candidat et par sens (24 h et 7 j sur la même carte)
    groups: dict[tuple, list] = {}
    for a in alerts:
        groups.setdefault((a["slug"], a["sens"]), []).append(a)

    rows_html, lines = [], []
    for (_, sens), items in groups.items():
        arrow, color = ("▲", "#16a34a") if sens == "up" else ("▼", "#dc2626")
        for i, a in enumerate(items):
            name = html.escape(a["name"]) if i == 0 else ""
            tag = " <i>(rappel)</i>" if a["kind"] == "rappel" else ""
            rows_html.append(
                f"<tr><td><b>{name}</b></td><td>{a['window']}{tag}</td>"
                f"<td>{_pct(a['ref'])}</td><td>{_pct(a['cur'])}</td>"
                f"<td style='color:{color}'><b>{arrow} {_num(a['rel'] * 100, True)} %</b></td>"
                f"<td style='color:{color}'>{_num(a['delta'] * 100, True)} pt</td></tr>")
            lines.append(
                f"{a['name']} [{a['window']}{', rappel' if a['kind'] == 'rappel' else ''}] "
                f"{_pct(a['ref'])} → {_pct(a['cur'])} "
                f"({_num(a['rel'] * 100, True)} %, {_num(a['delta'] * 100, True)} pt)")

    th = "style='text-align:left;padding:4px 10px;border-bottom:1px solid #ccc'"
    body = (
        "<div style='font-family:Arial,sans-serif;font-size:14px'>"
        "<h2 style='color:#dc2626'>🚨 Mouvement fort sur la présidentielle 2027</h2>"
        "<table style='border-collapse:collapse' cellpadding='4'>"
        f"<tr><th {th}>Candidat</th><th {th}>Fenêtre</th><th {th}>Avant</th>"
        f"<th {th}>Maintenant</th><th {th}>Var. relative</th><th {th}>Var. absolue</th></tr>"
        + "".join(rows_html)
        + "</table>"
        f"<p><a href='{MARKET_URL}'>Ouvrir le marché sur Polymarket</a></p>"
        "<p style='color:#888;font-size:12px'>Prix « Oui ». Confirmé sur 2 collectes. "
        "Rappel seulement si le mouvement s'aggrave d'au moins 1 pt (6 h minimum).</p>"
        "</div>")
    text = "\n".join(lines) + f"\n\n{MARKET_URL}"
    return subject, body, text


# ---------------------------------------------------------------- principal

def run(now: int | None = None, send=mailer.send) -> int:
    now = now or int(time.time())
    candidates = (read_json(CANDIDATES_FILE, None) or {}).get("candidates", [])
    histories = {
        c["slug"]: (read_json(HISTORY_DIR / f"{c['slug']}.json", None) or {}).get("p", [])
        for c in candidates
    }
    state = read_json(ALERT_STATE_FILE, None) or {}
    alerts = evaluate(candidates, histories, state, now)

    rc = 0
    if alerts:
        subject, body, text = build_email(alerts)
        try:
            send(subject, body, text, urgent=True)
            mark_sent(state, alerts, now)
            log.info("Alerte envoyée : %d ligne(s) — %s", len(alerts), subject)
        except Exception as exc:  # l'alerte sera retentée au prochain passage
            log.error("Échec d'envoi de l'alerte : %s", exc)
            rc = 1
    else:
        log.info("Aucune alerte")

    write_json(ALERT_STATE_FILE, state, pretty=True)
    return rc


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sys.exit(run())
