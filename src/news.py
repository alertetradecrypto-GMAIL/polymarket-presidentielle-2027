"""Actualités récentes de chaque candidat → docs/data/news.json.

Source : flux RSS de recherche Google News (français, sans clé d'API). On garde
les 5 articles les plus récents (titre, média, date, lien) ; le contenu des
articles n'est pas copié.

Pertinence :
  1. requête « nom exact » + termes électoraux (présidentielle, 2027, candidat,
     sondage…) ; complétée par le nom seul s'il manque des articles ;
  2. le titre doit citer le nom de famille du candidat (sinon l'article parle
     surtout d'autre chose) ;
  3. médias people / satiriques / archives vidéo et pages « fiche » écartés.

Lancé après collect.py (collect.yml) mais rafraîchi au plus une fois par heure
(NEWS_REFRESH_S). Un candidat en échec garde ses actualités précédentes ; une
panne de la source ne bloque jamais la collecte des prix.

    python src/news.py           # respecte le délai d'une heure
    python src/news.py --force   # rafraîchit tout de suite
"""
from __future__ import annotations

import logging
import re
import sys
import time
import unicodedata
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

# Changer cette valeur force un rafraîchissement complet (nouveaux filtres)
QUERY_VERSION = 2
ELECTION_TERMS = "(présidentielle OR 2027 OR candidat OR candidate OR candidature OR sondage OR primaire)"

# Médias people, satiriques, archives vidéo, agrégateurs de réseaux sociaux
BLOCKED_SOURCES = (
    "gorafi", "closer", "voici", "gala", "purepeople", "public.fr", "nextplz", "melty",
    "toutelatele", "tele-loisirs", "tele loisirs", "tv-programme", "programme-tv",
    "orange actualites", "howl.link",
)
# Pages « fiche » ou résultats d'anciennes élections
JUNK_TITLES = re.compile(
    r"actualites?,? (videos|et infos)|toute l'actualite|"
    r"^resultats|resultats (de l'election|des elections|municipales|legislatives)"
)


def norm(text: str) -> str:
    """Minuscules sans accents ; tirets et apostrophes → espaces."""
    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def surname(name: str) -> str:
    """Nom de famille normalisé : « Dominique de Villepin » → « villepin »,
    « Marine Le Pen » → « le pen », « Jean-Luc Mélenchon » → « melenchon »."""
    parts = name.split()[1:] or name.split()
    while len(parts) > 1 and norm(parts[0]) in ("de", "d"):
        parts = parts[1:]
    return norm(" ".join(parts))


def relevant(item: dict, name: str) -> bool:
    title = norm(item["title"])
    src = item.get("source", "").lower()
    src_n = norm(src)
    if any(b in src or norm(b) in src_n for b in BLOCKED_SOURCES):
        return False
    if title == norm(name) or JUNK_TITLES.search(title):
        return False
    return re.search(rf"\b{re.escape(surname(name))}\b", title) is not None


def _ts(text: str | None) -> int | None:
    if not text:
        return None
    try:
        return int(parsedate_to_datetime(text.strip()).timestamp())
    except (TypeError, ValueError):
        return None


def parse_rss(xml_text: str, now: int, limit: int | None = NEWS_PER_CANDIDATE) -> list[dict]:
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
    return items if limit is None else items[:limit]


def search(query: str, now: int) -> list[dict]:
    params = {"q": f"{query} when:{NEWS_MAX_AGE_DAYS}d", "hl": "fr", "gl": "FR", "ceid": "FR:fr"}
    # Pas de nouvelles tentatives automatiques : une source bloquée ne doit pas
    # rallonger la collecte (on réessaie au passage suivant)
    r = requests.get(NEWS_RSS_URL, params=params, timeout=HTTP_TIMEOUT_S,
                     headers={"User-Agent": "Mozilla/5.0 (polymarket-presidentielle-2027)"})
    r.raise_for_status()
    return parse_rss(r.text, now, limit=None)


def fetch_news(name: str, now: int) -> list[dict]:
    """Articles électoraux d'abord ; complétés par le nom seul s'il en manque."""
    picked = [a for a in search(f'"{name}" {ELECTION_TERMS}', now) if relevant(a, name)]
    if len(picked) < NEWS_PER_CANDIDATE:
        time.sleep(NEWS_PAUSE_S)
        seen = {norm(a["title"]) for a in picked}
        extra = [a for a in search(f'"{name}"', now)
                 if relevant(a, name) and norm(a["title"]) not in seen]
        picked += extra[: NEWS_PER_CANDIDATE - len(picked)]
    picked.sort(key=lambda a: a["t"], reverse=True)
    return picked[:NEWS_PER_CANDIDATE]


def run(force: bool = False) -> int:
    now = int(time.time())
    data = read_json(NEWS_FILE, {}) or {}
    items: dict = data.get("items", {})
    fresh = (now - data.get("updated", 0) < NEWS_REFRESH_S
             and data.get("version") == QUERY_VERSION)   # nouveaux filtres : tout refaire

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
        full = todo is cands
        write_json(NEWS_FILE, {"updated": now if full else data.get("updated", 0),
                               "version": QUERY_VERSION if full else data.get("version"),
                               "items": items})
    log.info("Actualités : %d candidat(s) mis à jour, %d erreur(s)", ok, errors)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sys.exit(run(force="--force" in sys.argv))
