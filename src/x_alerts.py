"""Tweet d'alerte « Mouvement fort / Strong move » (visuel B), en plus de l'email.

Deux temps, pour n'installer Chromium que s'il y a vraiment un tweet à faire :
1. volatility.py appelle queue() avec les alertes de l'email (fenêtres 24 h et 7 j,
   déjà confirmées sur 2 collectes). queue() applique les règles propres à X et,
   s'il reste quelque chose, écrit out/x_alert.json (non commité).
2. collect.yml lance ce script (étape conditionnelle) : rendu PNG + publication.

Règles X (state/x_alerts.json) :
- au plus 1 tweet toutes les 6 h par candidat et par sens (24 h et 7 j confondus) ;
- au plus 3 tweets par jour (jour de Paris), alertes et changements de Top 5
  (x_top5.py) confondus ; 3 candidats max par tweet ;
- une tentative consomme le créneau, même en échec : aucune nouvelle tentative
  au passage suivant (pas de boucle).
Hors DRY_RUN=0, rien n'est publié et l'état n'est pas modifié.
"""
from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from collect import read_json, write_json
from config import ROOT, STATE_DIR, TIMEZONE

log = logging.getLogger("x_alerts")
TZ = ZoneInfo(TIMEZONE)

X_ALERT_COOLDOWN_S = 6 * 3600
X_ALERT_MAX_PER_DAY = 3
X_ALERT_MAX_CANDIDATES = 3
X_ALERT_MAX_AGE_S = 3600          # alerte en attente plus vieille : abandonnée
X_ALERTS_STATE = STATE_DIR / "x_alerts.json"
PENDING_FILE = ROOT / "out" / "x_alert.json"
HASHTAGS = "#Presidentielle2027 #Polymarket"


# ---------------------------------------------------------------- règles

def _today(now: int) -> str:
    return datetime.fromtimestamp(now, TZ).date().isoformat()


def sent_today(state: dict, now: int) -> int:
    return state.get("count", 0) if state.get("day") == _today(now) else 0


def select(alerts: list, state: dict, now: int) -> list:
    """Lignes à tweeter (groupées par candidat et sens, plus forts mouvements d'abord)."""
    if sent_today(state, now) >= X_ALERT_MAX_PER_DAY:
        return []
    last = state.get("last", {})
    groups: dict[str, list] = {}
    for a in alerts:
        key = f"{a['slug']}|{a['sens']}"
        if now - last.get(key, 0) < X_ALERT_COOLDOWN_S:
            continue
        groups.setdefault(key, []).append(a)
    ranked = sorted(groups.values(), key=lambda g: max(abs(a["rel"]) for a in g), reverse=True)
    rows = []
    for g in ranked[:X_ALERT_MAX_CANDIDATES]:
        rows += sorted(g, key=lambda a: a["window"] != "24h")   # 24 h avant 7 j
    return rows


def count_one(state: dict, now: int) -> dict:
    """Compte un tweet dans le plafond du jour (partagé avec x_top5.py)."""
    today = _today(now)
    if state.get("day") != today:
        state["day"], state["count"] = today, 0
    state["count"] = state.get("count", 0) + 1
    return state


def record(state: dict, rows: list, now: int) -> dict:
    count_one(state, now)
    last = {k: t for k, t in state.get("last", {}).items() if now - t < 7 * 86400}
    for a in rows:
        last[f"{a['slug']}|{a['sens']}"] = now
    state["last"] = last
    return state


def queue(alerts: list, now: int) -> int:
    """Appelé par volatility.py. Renvoie le nombre de lignes mises en attente."""
    rows = select(alerts, read_json(X_ALERTS_STATE, None) or {}, now)
    if not rows:
        PENDING_FILE.unlink(missing_ok=True)
        return 0
    keep = ("slug", "name", "window", "sens", "ref", "cur", "delta", "rel")
    PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
    PENDING_FILE.write_text(json.dumps({"t": now, "alerts": [{k: a[k] for k in keep}
                                                             for a in alerts]},
                                       ensure_ascii=False), encoding="utf-8")
    log.info("Tweet d'alerte en attente : %d ligne(s)", len(rows))
    return len(rows)


# ---------------------------------------------------------------- texte

def _win(w: str, lang: str) -> str:
    if lang == "fr":
        return "en 24 h" if w == "24h" else "en 7 j"
    return "over 24h" if w == "24h" else "over 7d"


def build_text(rows: list) -> str:
    import render

    top = max(rows, key=lambda a: abs(a["rel"]))
    arrow = "▲" if top["sens"] == "up" else "▼"
    move = f"{arrow} {render.rel(top['rel'])}"
    span = f"({render.pct(top['ref'])} → {render.pct(top['cur'])})"
    others = len({a["slug"] for a in rows}) - 1
    fr_more = f" · +{others} autre{'s' if others > 1 else ''}" if others else ""
    en_more = f" · +{others} more" if others else ""
    return "\n".join([
        f"Mouvement fort : {top['name']} {move} {_win(top['window'], 'fr')} {span}{fr_more}",
        f"Strong move: {top['name']} {move} {_win(top['window'], 'en')} {span}{en_more}",
        "", HASHTAGS])


# ---------------------------------------------------------------- principal

def run(now: int | None = None, publish=None, to_png=None) -> int:
    import render
    import x_post

    publish = publish or x_post.publish
    to_png = to_png or render.to_png
    now = now or int(time.time())

    pending = read_json(PENDING_FILE, None)
    if not pending:
        log.info("Aucun tweet d'alerte en attente")
        return 0
    PENDING_FILE.unlink(missing_ok=True)
    if now - int(pending.get("t") or 0) > X_ALERT_MAX_AGE_S:
        log.warning("Alerte en attente trop ancienne : abandonnée")
        return 0

    state = read_json(X_ALERTS_STATE, None) or {}
    rows = select(pending.get("alerts", []), state, now)
    if not rows:
        log.info("Plus rien à tweeter après application des règles")
        return 0

    local = datetime.fromtimestamp(now, TZ)
    text = build_text(rows)
    stem = PENDING_FILE.parent / f"x_alert_{local:%Y%m%d_%H%M}"
    image = to_png(render.alert_html(rows, local), stem.with_suffix(".png"))
    Path(stem.with_suffix(".txt")).write_text(text + "\n", encoding="utf-8")

    dry = x_post.is_dry_run()
    rc = 0
    try:
        log.info("Tweet d'alerte : %s", publish(text, image, now=now))
    except Exception as exc:   # pas de nouvelle tentative : le créneau est consommé
        log.error("Échec du tweet d'alerte : %s", exc)
        rc = 1
    if not dry:
        write_json(X_ALERTS_STATE, record(state, rows, now), pretty=True)
    return rc


def demo(now: int | None = None, to_png=None) -> int:
    """Visuel B de démonstration (jamais publié) : 2 plus forts mouvements 24 h réels."""
    import os

    import render
    from config import CANDIDATES_FILE, HISTORY_DIR
    from daily import candidate_moves

    os.environ["DRY_RUN"] = "1"
    to_png = to_png or render.to_png
    now = now or int(time.time())
    cands = [c for c in (read_json(CANDIDATES_FILE, None) or {}).get("candidates", [])
             if c.get("active", True) and (c.get("price") or 0) >= 0.02]
    hist = {c["slug"]: (read_json(HISTORY_DIR / f"{c['slug']}.json", None) or {}).get("p", [])
            for c in cands}
    moves = [m for m in candidate_moves(cands, hist, now).values() if m["rel"] is not None]
    moves.sort(key=lambda m: abs(m["rel"]), reverse=True)
    rows = [{"slug": m["slug"], "name": m["name"], "window": "24h",
             "sens": "up" if m["delta"] >= 0 else "down", "ref": m["ref"], "cur": m["cur"],
             "delta": m["delta"], "rel": m["rel"]} for m in moves[:2]]
    if not rows:
        log.error("Pas de données pour la démonstration")
        return 1
    local = datetime.fromtimestamp(now, TZ)
    text = build_text(rows)
    stem = PENDING_FILE.parent / f"x_alert_{local:%Y%m%d_%H%M}_demo"
    to_png(render.alert_html(rows, local), stem.with_suffix(".png"))
    stem.with_suffix(".txt").write_text(text + "\n", encoding="utf-8")
    print(f"\n[DÉMO — visuel B, non publié]\n{text}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sys.exit(demo() if "--demo" in sys.argv[1:] else run())
