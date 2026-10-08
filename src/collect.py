"""Collecte des prix « Oui » de tous les candidats → docs/data/*.json.

Lancé toutes les 15 min par collect.yml. Idempotent : on peut le relancer
sans risque, les points sont fusionnés par tranche de 15 min.
Le prix « Non » n'est pas stocké : Non = 1 - Oui (calculé à l'affichage).

Volume : reconstruit à partir des trades (Data API, côté taker), par tranche
de 15 min : [[t, parts, dollars]]. Parts = définition du volume Gamma ;
dollars = parts × prix de chaque trade. Rattrapage complet depuis l'ouverture
à la première rencontre d'un candidat (voir aussi backfill_volume.py).
"""
from __future__ import annotations

import bisect
import json
import logging
import os
import re
import sys
import time
import unicodedata
from pathlib import Path

import polymarket as pm
from config import (
    ALERT_REF_TOLERANCE_S,
    BACKFILL_FIDELITY_MIN,
    CANDIDATES_FILE,
    FIDELITY_MIN,
    HISTORY_DIR,
    OVERLAP_S,
    VOL_BACKFILL_PER_RUN,
)

log = logging.getLogger("collect")

BUCKET_S = FIDELITY_MIN * 60
FINE_RETENTION_S = 30 * 86400   # au-delà de 30 jours : 1 point par heure
REFETCH_WEEK_S = 6 * 86400      # trou > 6 jours : on recharge la dernière semaine


# ---------------------------------------------------------------- utilitaires

def slugify(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "inconnu"


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        log.error("JSON illisible : %s (réinitialisé)", path)
        return default


def write_json(path: Path, data, *, pretty: bool = False) -> bool:
    """Écrit seulement si le contenu change (évite les commits vides)."""
    text = (
        json.dumps(data, ensure_ascii=False, indent=2)
        if pretty
        else json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    ) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return True


# ---------------------------------------------------------------- séries

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



def merge_points(existing: list, new: list, bucket_s: int = BUCKET_S) -> list:
    """Fusionne des points [t, p] par tranche ; le plus récent l'emporte."""
    by_bucket: dict[int, list] = {}
    for t, p in list(existing) + list(new):
        b = int(t) - int(t) % bucket_s
        prev = by_bucket.get(b)
        if prev is None or t >= prev[0]:
            by_bucket[b] = [int(t), round(float(p), 4)]
    return [by_bucket[b] for b in sorted(by_bucket)]


def compact(points: list, now: int) -> list:
    """Points de plus de 30 jours ramenés à 1 par heure (taille du dépôt)."""
    cutoff = now - FINE_RETENTION_S
    old = [pt for pt in points if pt[0] < cutoff]
    recent = [pt for pt in points if pt[0] >= cutoff]
    return merge_points([], old, 3600) + recent


# ---------------------------------------------------------------- volume

def vol_add(buckets: dict, trades, bucket_s: int = BUCKET_S) -> dict:
    """Ajoute des trades (t, parts, prix) aux tranches {début: [parts, dollars]}."""
    for t, size, price in trades:
        b = t - t % bucket_s
        acc = buckets.setdefault(b, [0.0, 0.0])
        acc[0] += size
        acc[1] += size * price
    return buckets


def vol_series(buckets: dict) -> list:
    return [[b, round(v[0], 2), round(v[1], 2)] for b, v in sorted(buckets.items())]


def compact_vol(vol: list, now: int) -> list:
    """Tranches de plus de 30 jours regroupées par heure (sommées)."""
    cutoff = now - FINE_RETENTION_S
    hourly: dict = {}
    recent = []
    for t, sh, usd in vol:
        if t < cutoff:
            acc = hourly.setdefault(t - t % 3600, [0.0, 0.0])
            acc[0] += sh
            acc[1] += usd
        else:
            recent.append([t, sh, usd])
    return vol_series(hourly) + recent


def vol_totals(vol: list, now: int) -> tuple[float, float]:
    """(dollars total, dollars 24 h)."""
    total = sum(v[2] for v in vol)
    day = sum(v[2] for v in vol if v[0] >= now - 86400)
    return round(total, 2), round(day, 2)


def update_volume(hist: dict, c: dict, now: int, allow_backfill: bool) -> bool:
    """Met à jour hist["vol"]. Renvoie True si un rattrapage complet a été fait."""
    cond = c.get("condition_id")
    if not cond:
        return False
    if "vol_since" not in hist:
        if not allow_backfill:
            return False
        buckets = vol_add({}, pm.iter_trades(cond, None, now))
        vol = vol_series(buckets)
        hist["vol"] = compact_vol(vol, now)
        hist["vol_since"] = vol[0][0] if vol else now
        shares = sum(v[1] for v in vol)
        gap = shares - (c.get("volume") or 0)
        lvl = logging.WARNING if abs(gap) > 0.01 * max(shares, 1) + 100 else logging.INFO
        log.log(lvl, "Volume rattrapé %s : %d tranches, %.0f parts (Gamma %.0f)",
                c["name"], len(vol), shares, c.get("volume") or 0)
        return True
    if not c["active"]:
        return False
    vol = hist.get("vol", [])
    # On recharge la dernière tranche connue et la dernière heure (trades indexés en retard)
    recent = now - OVERLAP_S
    since = recent - recent % BUCKET_S
    if vol:
        since = min(since, vol[-1][0])
    kept = [v for v in vol if v[0] < since]
    new = vol_series(vol_add({}, pm.iter_trades(cond, since, now)))
    hist["vol"] = compact_vol(kept + new, now)
    return False


def fetch_new_points(token: str, last_t: int | None, now: int) -> list:
    if last_t is None:
        # Initialisation. Le CLOB tronque « max » aux 30 derniers jours sous
        # fidelity=720 : on assemble donc 3 résolutions.
        full = pm.get_price_history(token, interval="max", fidelity=720)   # 12 h, depuis l'ouverture
        month = pm.get_price_history(token, interval="1m", fidelity=BACKFILL_FIDELITY_MIN)
        week = pm.get_price_history(token, interval="1w", fidelity=FIDELITY_MIN)
        coarse = merge_points(merge_points([], full, 3600), month, 3600)
        return merge_points(coarse, week)
    if now - last_t > REFETCH_WEEK_S:
        return pm.get_price_history(token, interval="1w", fidelity=FIDELITY_MIN)
    return pm.get_price_history(
        token, start_ts=last_t - OVERLAP_S, end_ts=now, fidelity=FIDELITY_MIN
    )


def update_candidate(c: dict, now: int, allow_backfill: bool = True) -> bool:
    """Met à jour docs/data/history/<slug>.json. Renvoie True si un rattrapage
    complet du volume a été fait (pour limiter leur nombre par collecte)."""
    path = HISTORY_DIR / f"{c['slug']}.json"
    hist = read_json(path, None) or {"name": c["name"], "token_yes": c["token_yes"],
                                      "p": []}
    hist["name"] = c["name"]
    hist["token_yes"] = c["token_yes"]

    if c["active"]:
        last_t = hist["p"][-1][0] if hist["p"] else None
        new = fetch_new_points(c["token_yes"], last_t, now)
        hist["p"] = compact(merge_points(hist["p"], new), now)
    hist.pop("v", None)   # ancien format (cumul Gamma relevé), remplacé par "vol"

    backfilled = False
    try:
        backfilled = update_volume(hist, c, now, allow_backfill)
    except Exception as exc:  # le volume en échec ne bloque pas les prix
        log.error("Volume %s : %s", c["name"], exc)
    if "vol_since" in hist:
        c["volume_usd"], c["volume24h_usd"] = vol_totals(hist.get("vol", []), now)
    else:
        c["volume_usd"] = c["volume24h_usd"] = None

    # Variation 24 h pour le tableau de bord (même calcul que l'alerte et le récap)
    pts = hist["p"]
    c["last_hist"] = pts[-1][1] if pts else None
    c["price_24h"] = price_at(pts, pts[-1][0] - 86400) if pts else None

    write_json(path, hist)
    return backfilled


def _sum_or_none(values):
    """Somme, ou None si une valeur manque (rattrapage incomplet)."""
    vals = list(values)
    return None if not vals or any(v is None for v in vals) else round(sum(vals), 2)


# ---------------------------------------------------------------- principal

def run(backfill_budget: int | None = VOL_BACKFILL_PER_RUN) -> int:
    """backfill_budget : rattrapages de volume complets autorisés (None = illimité)."""
    now = int(time.time())
    event = pm.get_event()
    previous = {c["id"]: c for c in read_json(CANDIDATES_FILE, {}).get("candidates", [])}

    candidates, used_slugs = [], set()
    for raw in event.get("markets", []):
        c = pm.parse_market(raw)
        if c is None or not pm.is_tradable_candidate(c, raw):
            continue
        prev = previous.get(c["id"])
        slug = prev["slug"] if prev else slugify(c["name"])
        if slug in used_slugs:                      # homonymes improbables
            slug = f"{slug}-{c['id']}"
        used_slugs.add(slug)
        c["slug"] = slug
        c["first_seen"] = prev["first_seen"] if prev else now
        if not prev:
            log.info("Nouveau candidat : %s", c["name"])
        candidates.append(c)

    errors = 0
    budget = backfill_budget
    for c in candidates:
        try:
            if update_candidate(c, now, budget is None or budget > 0) and budget is not None:
                budget -= 1
        except Exception as exc:  # un candidat en échec ne bloque pas les autres
            errors += 1
            log.error("Échec %s : %s", c["name"], exc)

    candidates.sort(key=lambda c: (c["active"], c["price"] or 0), reverse=True)
    write_json(
        CANDIDATES_FILE,
        {
            "updated": now,
            "event": {
                "id": str(event.get("id")),
                "slug": event.get("slug"),
                "title": event.get("title"),
                "end_date": event.get("endDate"),
                "volume": event.get("volume"),
                "volume24h": event.get("volume24hr"),
                "volume_usd": _sum_or_none(c.get("volume_usd") for c in candidates),
                "volume24h_usd": _sum_or_none(c.get("volume24h_usd") for c in candidates),
            },
            "candidates": candidates,
        },
        pretty=True,
    )
    log.info("%d candidats, %d erreur(s)", len(candidates), errors)
    # Échec global seulement si plus de la moitié des candidats a échoué
    return 1 if candidates and errors > len(candidates) / 2 else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sys.exit(run())
