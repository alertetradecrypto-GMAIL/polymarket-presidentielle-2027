"""Tweet quotidien des cotes (15h30 heure de Paris) : texte + visuel A.

Lancé par tweet.yml à 13:30 et 14:30 UTC : le script ne publie qu'entre 15h30
et 17h59 à Paris, une seule fois par jour (state/x_daily.json ne contient que la
date de la dernière tentative). `--force` ignore ces deux règles.
Hors DRY_RUN=0, rien n'est publié et l'état n'est pas modifié.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import render
import x_post
from collect import read_json, write_json
from config import CANDIDATES_FILE, DAILY_MIN_PRICE, HISTORY_DIR, ROOT, STATE_DIR, TIMEZONE
from daily import candidate_moves

log = logging.getLogger("x_daily")
TZ = ZoneInfo(TIMEZONE)

X_DAILY_FROM = (15, 30)            # 15h30 à Paris
X_DAILY_UNTIL_HOUR = 18            # cron retardé : on publie encore jusqu'à 17h59
X_DAILY_ROWS = 10                  # lignes du visuel
X_STALE_S = 2 * 3600               # données plus vieilles : pas de tweet
X_DAILY_STATE = STATE_DIR / "x_daily.json"
OUT_DIR = ROOT / "out"
HASHTAGS = "#Presidentielle2027 #Polymarket"


def should_post(now: int, state: dict) -> tuple[bool, str]:
    local = datetime.fromtimestamp(now, TZ)
    today = local.date().isoformat()
    if state.get("last_attempt") == today:
        return False, "tweet du jour déjà tenté"
    if not X_DAILY_FROM <= (local.hour, local.minute) or local.hour >= X_DAILY_UNTIL_HOUR:
        return False, f"{local:%H:%M} à Paris, hors créneau"
    return True, today


def rows_for(moves: dict) -> list:
    rows = [m for m in moves.values()
            if m["active"] and (m["cur"] or 0) >= DAILY_MIN_PRICE - 1e-9]
    return sorted(rows, key=lambda m: m["cur"] or 0, reverse=True)[:X_DAILY_ROWS]


def build_text(rows: list) -> str:
    top = [f"{i}. {m['name']} {render.pct(m['cur'])}"
           + ("" if m["delta"] is None else f" ({render.pts(m['delta'])})")
           for i, m in enumerate(rows[:3], 1)]
    return "\n".join([
        "Présidentielle 2027 : les cotes Polymarket du jour",
        "French 2027 election: today's Polymarket odds",
        "", *top, "", HASHTAGS])


def run(now: int | None = None, force: bool = False, publish=x_post.publish,
        to_png=render.to_png) -> int:
    now = now or int(time.time())
    state = read_json(X_DAILY_STATE, None) or {}
    ok, info = should_post(now, state)
    if not ok and not force:
        log.info("Pas de tweet : %s", info)
        return 0

    data = read_json(CANDIDATES_FILE, None) or {}
    if now - int(data.get("updated") or 0) > X_STALE_S:
        log.error("Données de plus de 2 h : pas de tweet")
        return 1
    candidates = data.get("candidates", [])
    histories = {c["slug"]: (read_json(HISTORY_DIR / f"{c['slug']}.json", None) or {}).get("p", [])
                 for c in candidates}
    rows = rows_for(candidate_moves(candidates, histories, now))
    if not rows:
        log.error("Aucun candidat à afficher")
        return 1

    local = datetime.fromtimestamp(now, TZ)
    text = build_text(rows)
    stem = OUT_DIR / f"x_daily_{local:%Y%m%d}"
    image = to_png(render.daily_html(rows, local), stem.with_suffix(".png"))
    Path(stem.with_suffix(".txt")).write_text(text + "\n", encoding="utf-8")

    dry = x_post.is_dry_run()
    rc = 0
    try:
        result = publish(text, image, now=now)
        log.info("Tweet quotidien : %s", result)
    except Exception as exc:   # pas de nouvelle tentative : le créneau du jour est consommé
        log.error("Échec du tweet quotidien : %s", exc)
        rc = 1
    if ok and not dry:
        state["last_attempt"] = info
        write_json(X_DAILY_STATE, state, pretty=True)
    return rc


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="publier même hors créneau")
    sys.exit(run(force=ap.parse_args().force))
