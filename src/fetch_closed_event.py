"""Télécharge l'historique complet d'un événement Polymarket (ouvert ou clos).

Usage :
  python src/fetch_closed_event.py presidential-election-winner-2024 --fidelity 60 --min-volume 100000

Sortie : backtest/events/<slug>/_event.json  (métadonnées + résolution)
         backtest/events/<slug>/<candidat>.json  ({"yes": [[t, p], ...], "no": [...]})
Lecture seule, aucun ordre.
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import datetime
from pathlib import Path

import polymarket as pm
import requests
from collect import slugify
from config import GAMMA_URL

CHUNK_S = 30 * 86400          # le CLOB tronque les grandes plages : tranches de 30 jours
OUT = Path(__file__).resolve().parent.parent / "backtest" / "events"
log = logging.getLogger("fetch_event")


def ts(iso: str | None) -> int | None:
    if not iso:
        return None
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


def _chunk(token: str, a: int, b: int, fidelity: int, errs: list) -> list:
    """Le CLOB renvoie 400 si la plage est trop longue : on coupe en deux jusqu'à 1 jour."""
    try:
        return pm.get_price_history(token, start_ts=a, end_ts=b, fidelity=fidelity)
    except requests.HTTPError as e:
        if e.response is None or e.response.status_code != 400 or b - a <= 86400:
            errs.append(f"{a}-{b}")
            return []
        mid = (a + b) // 2
        return _chunk(token, a, mid, fidelity, errs) + _chunk(token, mid, b, fidelity, errs)


def history(token: str, start: int, end: int, fidelity: int) -> list[list]:
    pts: dict[int, float] = {}
    errs: list[str] = []
    t = start
    while t < end:
        for tt, p in _chunk(token, t, min(t + CHUNK_S, end), fidelity, errs):
            pts[tt] = p
        t += CHUNK_S
        time.sleep(0.2)
    if not pts:  # dernier recours : historique complet à la résolution maximale servie
        try:
            pts = dict(pm.get_price_history(token, interval="max", fidelity=max(fidelity, 720)))
        except requests.HTTPError:
            pass
    if errs:
        print(f"::warning::{token[:8]}… {len(errs)} plages sans données")
    return [[k, pts[k]] for k in sorted(pts)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("slug")
    ap.add_argument("--fidelity", type=int, default=60, help="minutes par point (1440 = quotidien)")
    ap.add_argument("--min-volume", type=float, default=0.0)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    events = pm._get(f"{GAMMA_URL}/events", {"slug": a.slug, "closed": "true"}) \
        or pm._get(f"{GAMMA_URL}/events", {"slug": a.slug})
    if not events:
        print(f"::error::Événement introuvable : {a.slug}")
        return 1
    print(f"::notice::{events[0].get('title')} : {len(events[0].get('markets', []))} marchés")
    ev = events[0]
    out = OUT / a.slug
    out.mkdir(parents=True, exist_ok=True)
    ev_start = ts(ev.get("startDate") or ev.get("creationDate"))
    meta = {"slug": a.slug, "title": ev.get("title"), "start": ev_start,
            "end": ts(ev.get("closedTime") or ev.get("endDate")), "fidelity": a.fidelity, "markets": []}

    for m in ev.get("markets", []):
        vol = float(m.get("volume") or 0)
        tokens = pm._as_list(m.get("clobTokenIds"))
        if vol < a.min_volume or len(tokens) != 2:
            print(f"::notice::ignoré {m.get('groupItemTitle')} vol={vol:.0f} tokens={len(tokens)}")
            continue
        name = m.get("groupItemTitle") or m.get("question")
        start = ts(m.get("startDate")) or ev_start
        end = ts(m.get("closedTime") or m.get("endDate")) or int(time.time())
        end = min(end + 86400, int(time.time()))
        log.info("%s : %s → %s", name, datetime.fromtimestamp(start).date(), datetime.fromtimestamp(end).date())
        data = {"name": name, "yes": history(tokens[0], start, end, a.fidelity),
                "no": history(tokens[1], start, end, a.fidelity)}
        (out / f"{slugify(name)}.json").write_text(json.dumps(data, separators=(",", ":")))
        meta["markets"].append({"name": name, "file": f"{slugify(name)}.json", "volume": vol,
                                "outcome_prices": pm._as_list(m.get("outcomePrices")),
                                "points": len(data["yes"])})
    (out / "_event.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    print(f"::notice::{len(meta['markets'])} marchés écrits dans {out.name}")
    return 0 if meta["markets"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:  # visible dans les annotations GitHub
        import traceback
        tb = traceback.format_exc().strip().splitlines()[-4:]
        print("::error::" + " | ".join(tb).replace("\n", " "))
        raise
