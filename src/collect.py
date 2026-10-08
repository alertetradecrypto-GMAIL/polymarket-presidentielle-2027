"""Collecte des prix « Oui » de tous les candidats → docs/data/*.json.

Lancé toutes les 15 min par collect.yml. Idempotent : on peut le relancer
sans risque, les points sont fusionnés par tranche de 15 min.
Le prix « Non » n'est pas stocké : Non = 1 - Oui (calculé à l'affichage).
"""
from __future__ import annotations

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
    BACKFILL_FIDELITY_MIN,
    CANDIDATES_FILE,
    FIDELITY_MIN,
    HISTORY_DIR,
    OVERLAP_S,
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


def fetch_new_points(token: str, last_t: int | None, now: int) -> list:
    if last_t is None:
        # Initialisation : historique complet à l'heure + dernière semaine à 15 min
        full = []
        for fid in (BACKFILL_FIDELITY_MIN, 180, 720):
            full = pm.get_price_history(token, interval="max", fidelity=fid)
            if full:
                break
        week = pm.get_price_history(token, interval="1w", fidelity=FIDELITY_MIN)
        return merge_points(merge_points([], full, 3600), week)
    if now - last_t > REFETCH_WEEK_S:
        return pm.get_price_history(token, interval="1w", fidelity=FIDELITY_MIN)
    return pm.get_price_history(
        token, start_ts=last_t - OVERLAP_S, end_ts=now, fidelity=FIDELITY_MIN
    )


def update_candidate(c: dict, now: int) -> bool:
    """Met à jour docs/data/history/<slug>.json. Renvoie True si modifié."""
    path = HISTORY_DIR / f"{c['slug']}.json"
    hist = read_json(path, None) or {"name": c["name"], "token_yes": c["token_yes"],
                                      "p": [], "v": []}
    hist["name"] = c["name"]
    hist["token_yes"] = c["token_yes"]

    if c["active"]:
        last_t = hist["p"][-1][0] if hist["p"] else None
        new = fetch_new_points(c["token_yes"], last_t, now)
        hist["p"] = compact(merge_points(hist["p"], new), now)
        # Volume cumulé relevé à chaque passage ; le dashboard affiche les deltas
        hist["v"] = compact(merge_points(hist["v"], [[now, c["volume"]]]), now)

    return write_json(path, hist)


# ---------------------------------------------------------------- principal

def run() -> int:
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
    for c in candidates:
        try:
            update_candidate(c, now)
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
