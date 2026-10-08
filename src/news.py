"""Actualités récentes de chaque candidat → docs/data/news.json.

Source : flux RSS de recherche Google News (français, sans clé d'API), requête
sur le nom exact du candidat. On garde les 5 articles les plus récents (titre,
média, date, lien) ; le contenu des articles n'est pas copié.

Lancé après collect.py (collect.yml) mais rafraîchi au plus une fois par heure
(NEWS_REFRESH_S). Un candidat en échec garde ses actualités précédentes ; une
panne de la source ne bloque jamais la collecte des prix.

    python src/news.py           # respecte le délai d'une heure
    python src/news.py --force   # rafraîchit tout de suite
"""
from __future__ import annotations

import logging
import sys
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import requests

from collect import read_json, write_json
from config import (
    CANDIDATES_FILE,
    HTTP_TIMEOUT_S,
    NEWS_FILE,
    NEWS_MAX_AGE_DAYS,
    NEWS_PAUSE_S,
    NEWS_PER_CANDIDATE,
    NEWS_REFRESH_S,
    NEWS_RSS_URL,
)

log = logging.getLogger("news")

MAX_CONSECUTIVE_ERRORS = 3   # source indisponible : on abandonne ce passage


def _ts(text: str | None) -> int | None:
    if not text:
        return None
    try:
        return int(parsedate_to_datetime(text.strip()).timestamp())
    except (TypeError, ValueError):
        return None


def parse_rss(xml_text: str, now: int, limit: int = NEWS_PER_CANDIDATE) -> list[dict]:
    """Articles du flux, du plus récent au plus ancien, sans doublon de titre."""
    root = ET.fromstring(xml_text)
    items, seen = [], set()
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        url = (it.findtext("link") or "").strip()
        t = _ts(it.findtext("pubDate"))
        src_el = it.find("source")
        source = (src_el.text or "").strip() if src_el is not None else ""
        # Google News suffixe le titre par « - Média »
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3].rstrip()
        if not title or not url.startswith("http") or t is None:
            continue
        if t < now - NEWS_MAX_AGE_DAYS * 86400 or t > now + 3600:
            continue
        key = title.casefold()
        if key in seen:
            continue
        seen.add(key)
        items.append({"t": t, "title": title, "source": source, "url": url})
    items.sort(key=lambda x: x["t"], reverse=True)
    return items[:limit]


def fetch_news(name: str, now: int) -> list[dict]:
    params = {"q": f'"{name}" when:{NEWS_MAX_AGE_DAYS}d', "hl": "fr", "gl": "FR", "ceid": "FR:fr"}
    # Pas de nouvelles tentatives automatiques : une source bloquée ne doit pas
    # rallonger la collecte (on réessaie au passage suivant)
    r = requests.get(NEWS_RSS_URL, params=params, timeout=HTTP_TIMEOUT_S,
                     headers={"User-Agent": "Mozilla/5.0 (polymarket-presidentielle-2027)"})
    r.raise_for_status()
    return parse_rss(r.text, now)


def run(force: bool = False) -> int:
    now = int(time.time())
    data = read_json(NEWS_FILE, {}) or {}
    items: dict = data.get("items", {})
    fresh = now - data.get("updated", 0) < NEWS_REFRESH_S

    cands = [c for c in read_json(CANDIDATES_FILE, {}).get("candidates", []) if c.get("active")]
    # Hors rafraîchissement horaire : seulement les candidats sans actualités
    todo = cands if force or not fresh else [c for c in cands if c["slug"] not in items]
    if not todo:
        log.info("Actualités à jour (moins d'une heure)")
        return 0

    ok = errors = streak = 0
    for i, c in enumerate(todo):
        if streak >= MAX_CONSECUTIVE_ERRORS:
            log.error("Actualités : %d échecs d'affilée, abandon de ce passage", streak)
            break
        if i:
            time.sleep(NEWS_PAUSE_S)
        try:
            items[c["slug"]] = fetch_news(c["name"], now)
            ok += 1
            streak = 0
        except Exception as exc:  # on garde les actualités précédentes
            errors += 1
            streak += 1
            log.error("Actualités %s : %s", c["name"], exc)

    known = {c["slug"] for c in cands}
    items = {k: v for k, v in sorted(items.items()) if k in known}
    if ok:
        # Passage horaire (même partiel) : prochain rafraîchissement dans 1 h
        write_json(NEWS_FILE, {"updated": now if todo is cands else data.get("updated", 0),
                               "items": items})
    log.info("Actualités : %d candidat(s) mis à jour, %d erreur(s)", ok, errors)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sys.exit(run(force="--force" in sys.argv))
