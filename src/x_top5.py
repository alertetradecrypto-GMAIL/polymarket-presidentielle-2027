"""Tweet « Nouveau Top 5 / New top 5 » : publié seulement quand l'ordre du Top 5 change.

Deux temps, comme x_alerts.py (tweepy n'est installé que s'il y a un tweet à faire) :
1. `x_top5.py check` (chaque collecte) compare le Top 5 actuel (prix médian Oui,
   candidats actifs ≥ 2 %) au dernier Top 5 publié. Un nouvel ordre doit être
   observé sur X_TOP5_CONFIRM collectes consécutives ; si c'est le cas, que le
   dernier tweet de classement date d'au moins X_TOP5_MIN_GAP_S et que le plafond
   commun avec les alertes (3/jour) n'est pas atteint, out/x_top5.json est écrit.
2. `x_top5.py post` publie le tweet (texte seul, sans visuel).

Une variation de % sans changement d'ordre ne déclenche rien. Le tweet quotidien
(x_daily.py) est sauté si le Top 5 n'a pas changé depuis le dernier publié, et
met à jour ce même état quand il publie.

État (state/x_top5.json) : `published` (dernier Top 5 publié), `t` (heure du
dernier tweet de classement), `cand`/`seen` (nouvel ordre en cours de
confirmation). La référence initiale est posée par le tweet quotidien. Hors DRY_RUN=0, seuls
`cand`/`seen` sont écrits.
"""
from __future__ import annotations

import json
import logging
import sys
import time

import x_alerts
from collect import read_json, write_json
from config import CANDIDATES_FILE, HISTORY_DIR, ROOT, STATE_DIR

log = logging.getLogger("x_top5")

X_TOP5_N = 5
X_TOP5_CONFIRM = 2                 # collectes consécutives (≈ 30 min)
X_TOP5_MIN_GAP_S = 2 * 3600        # au moins 2 h entre deux tweets de classement
X_TOP5_MAX_AGE_S = 3600            # tweet en attente plus vieux : abandonné
X_STALE_S = 2 * 3600
X_TOP5_STATE = STATE_DIR / "x_top5.json"
PENDING_FILE = ROOT / "out" / "x_top5.json"
HASHTAGS = "#Presidentielle2027 #Polymarket"


def current_rows(now: int) -> list | None:
    """Top 5 actuel (lignes de candidate_moves), None si données absentes ou périmées."""
    from daily import candidate_moves
    from x_daily import rows_for

    data = read_json(CANDIDATES_FILE, None) or {}
    if now - int(data.get("updated") or 0) > X_STALE_S:
        return None
    cands = data.get("candidates", [])
    hist = {c["slug"]: (read_json(HISTORY_DIR / f"{c['slug']}.json", None) or {}).get("p", [])
            for c in cands}
    return rows_for(candidate_moves(cands, hist, now))[:X_TOP5_N] or None


def slugs(rows: list) -> list:
    return [r["slug"] for r in rows]


def decide(top: list, state: dict, alerts_state: dict, now: int) -> tuple[bool, dict, str]:
    """Renvoie (tweeter ?, nouvel état de suivi, raison). Fonction pure."""
    state = dict(state)
    if not state.get("published"):
        return False, state, "aucun Top 5 publié : le tweet quotidien servira de référence"
    if top == state["published"]:
        state.update(cand=None, seen=0)
        return False, state, "Top 5 inchangé"
    state["seen"] = state.get("seen", 0) + 1 if state.get("cand") == top else 1
    state["cand"] = top
    if state["seen"] < X_TOP5_CONFIRM:
        return False, state, f"nouvel ordre vu {state['seen']}/{X_TOP5_CONFIRM}"
    if now - state.get("t", 0) < X_TOP5_MIN_GAP_S:
        return False, state, "dernier tweet de classement il y a moins de 2 h"
    if x_alerts.sent_today(alerts_state, now) >= x_alerts.X_ALERT_MAX_PER_DAY:
        return False, state, "plafond de 3 tweets/jour atteint"
    return True, state, "nouvel ordre confirmé"


def mark_published(state: dict, top: list, now: int) -> dict:
    state = dict(state)
    state.update(published=top, t=now, cand=None, seen=0)
    return state


def build_text(rows: list, prev: list, short: bool = False) -> str:
    import render

    lines = []
    for i, r in enumerate(rows, 1):
        name = r["name"].split()[-1] if short else r["name"]
        if r["slug"] not in prev:
            mark = " 🆕"
        else:
            d = prev.index(r["slug"]) + 1 - i
            mark = f" ▲{d}" if d > 0 else f" ▼{-d}" if d < 0 else ""
        lines.append(f"{i}. {name} {render.pct(r['cur'])}{mark}")
    return "\n".join(["Présidentielle 2027 : nouveau Top 5 Polymarket",
                      "French 2027 election: new Polymarket top 5",
                      "", *lines, "", HASHTAGS])


def fit_text(rows: list, prev: list) -> str:
    import x_post

    text = build_text(rows, prev)
    return text if x_post.weighted_len(text) <= 280 else build_text(rows, prev, short=True)


# ---------------------------------------------------------------- étapes

def check(now: int | None = None) -> int:
    import x_post

    now = now or int(time.time())
    PENDING_FILE.unlink(missing_ok=True)
    rows = current_rows(now)
    if not rows:
        log.warning("Données absentes ou de plus de 2 h : Top 5 non contrôlé")
        return 0
    state = read_json(X_TOP5_STATE, None) or {}
    go, new_state, why = decide(slugs(rows), state, read_json(x_alerts.X_ALERTS_STATE, None) or {},
                                now)
    log.info("Top 5 : %s", why)
    if go:
        PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
        PENDING_FILE.write_text(json.dumps(
            {"t": now, "prev": state["published"],
             "rows": [{k: r[k] for k in ("slug", "name", "cur")} for r in rows]},
            ensure_ascii=False), encoding="utf-8")
    if new_state != state:
        write_json(X_TOP5_STATE, new_state, pretty=True)
    return 0


def post(now: int | None = None, publish=None) -> int:
    import x_post

    publish = publish or x_post.publish
    now = now or int(time.time())
    pending = read_json(PENDING_FILE, None)
    if not pending:
        log.info("Aucun tweet de classement en attente")
        return 0
    PENDING_FILE.unlink(missing_ok=True)
    if now - int(pending.get("t") or 0) > X_TOP5_MAX_AGE_S:
        log.warning("Tweet de classement en attente trop ancien : abandonné")
        return 0
    alerts_state = read_json(x_alerts.X_ALERTS_STATE, None) or {}
    if x_alerts.sent_today(alerts_state, now) >= x_alerts.X_ALERT_MAX_PER_DAY:
        log.info("Plafond de 3 tweets/jour atteint")
        return 0

    rows = pending["rows"]
    text = fit_text(rows, pending.get("prev") or [])
    rc = 0
    try:
        log.info("Tweet de classement : %s", publish(text, None, now=now))
    except Exception as exc:   # pas de nouvelle tentative : le créneau est consommé
        log.error("Échec du tweet de classement : %s", exc)
        rc = 1
    if not x_post.is_dry_run():
        state = read_json(X_TOP5_STATE, None) or {}
        write_json(X_TOP5_STATE, mark_published(state, slugs(rows), now), pretty=True)
        write_json(x_alerts.X_ALERTS_STATE, x_alerts.count_one(alerts_state, now), pretty=True)
    return rc


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sys.exit(post() if sys.argv[1:2] == ["post"] else check())
