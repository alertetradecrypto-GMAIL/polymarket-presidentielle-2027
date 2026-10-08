"""Publication sur X (@MarketSentinelX) : un tweet texte + une image PNG.

Modèle : tweet.py du dépôt marketsentinel-x (tweepy v2, Client.create_tweet).

- DRY_RUN=1 : rien n'est envoyé à X ; le texte est affiché, l'image reste sur disque.
- Anti-doublon : empreinte SHA-256 du texte, gardée 7 jours dans state/x_posts.json
  (enregistrée seulement après une publication réussie).
- Une seule tentative par appel, aucune boucle de nouvelle tentative. Seul repli :
  si l'upload d'image v1.1 est refusé, un essai unique sur l'endpoint v2.
- Aucun lien dans le texte (0,20 $ par post avec lien au lieu de 0,015 $).
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path

from collect import read_json, write_json
from config import STATE_DIR

log = logging.getLogger("x_post")

POSTS_FILE = STATE_DIR / "x_posts.json"
HASH_KEEP_S = 7 * 86400
V2_MEDIA_URL = "https://api.x.com/2/media/upload"
URL_RE = re.compile(r"(https?://|www\.)\S+|\b[\w-]+\.(com|fr|io|net|org|co)\b", re.I)


class XError(RuntimeError):
    pass


def is_dry_run() -> bool:
    return os.environ.get("DRY_RUN", "1").strip() != "0"   # par défaut : pas de publication


def text_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def check_text(text: str) -> None:
    if URL_RE.search(text):
        raise XError("Le texte contient un lien (tarif 0,20 $) : publication refusée")
    if weighted_len(text) > 280:
        raise XError(f"Texte trop long ({weighted_len(text)} > 280)")


def weighted_len(text: str) -> int:
    """Longueur comptée par X : les caractères hors latin courant comptent double."""
    return sum(1 if ord(ch) <= 0x10FF or 0x2000 <= ord(ch) <= 0x206F else 2 for ch in text)


def _env(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        raise XError(f"Secret manquant : {name}")
    return v


def _keys() -> dict:
    return {k: _env(k) for k in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")}


def upload_image(path: Path, k: dict) -> str:
    """Upload v1.1 (tweepy.API.media_upload) ; si refusé, un essai sur l'endpoint v2."""
    import tweepy

    auth = tweepy.OAuth1UserHandler(k["X_API_KEY"], k["X_API_SECRET"],
                                    k["X_ACCESS_TOKEN"], k["X_ACCESS_SECRET"])
    try:
        return str(tweepy.API(auth).media_upload(filename=str(path)).media_id)
    except tweepy.errors.HTTPException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status not in (400, 403, 404, 410):
            raise
        log.warning("Upload v1.1 refusé (HTTP %s), essai unique sur l'endpoint v2", status)

    import requests
    from requests_oauthlib import OAuth1

    oauth = OAuth1(k["X_API_KEY"], k["X_API_SECRET"], k["X_ACCESS_TOKEN"], k["X_ACCESS_SECRET"])
    with open(path, "rb") as fh:
        r = requests.post(V2_MEDIA_URL, auth=oauth, timeout=60,
                          files={"media": (path.name, fh, "image/png")},
                          data={"media_category": "tweet_image", "media_type": "image/png"})
    if r.status_code >= 300:
        raise XError(f"Upload v2 refusé : HTTP {r.status_code}")
    data = r.json().get("data") or {}
    media_id = data.get("id") or data.get("media_id_string")
    if not media_id:
        raise XError("Upload v2 : identifiant de média absent de la réponse")
    return str(media_id)


def publish(text: str, image: Path | None, *, now: int) -> str:
    """Publie le tweet. Renvoie "dry_run", "duplicate" ou l'identifiant du tweet.
    Lève une exception en cas d'échec (l'appelant n'insiste pas)."""
    check_text(text)
    if image is not None and not Path(image).is_file():
        raise XError(f"Image introuvable : {image}")

    state = read_json(POSTS_FILE, None) or {}
    recent = [e for e in state.get("recent", []) if now - e.get("t", 0) < HASH_KEEP_S]
    h = text_hash(text)
    if any(e.get("h") == h for e in recent):
        log.info("Tweet identique déjà publié dans les 7 derniers jours : ignoré")
        return "duplicate"

    if is_dry_run():
        print(f"\n[DRY_RUN — tweet non publié, {weighted_len(text)}/280 caractères]\n{text}")
        if image is not None:
            print(f"[image : {image}]")
        return "dry_run"

    import tweepy

    k = _keys()
    media_ids = [upload_image(Path(image), k)] if image is not None else None
    client = tweepy.Client(consumer_key=k["X_API_KEY"], consumer_secret=k["X_API_SECRET"],
                           access_token=k["X_ACCESS_TOKEN"],
                           access_token_secret=k["X_ACCESS_SECRET"])
    tweet_id = str(client.create_tweet(text=text, media_ids=media_ids).data["id"])

    recent.append({"h": h, "t": now})
    write_json(POSTS_FILE, {"recent": recent[-50:]}, pretty=True)
    log.info("Tweet publié (id %s)", tweet_id)
    return tweet_id
