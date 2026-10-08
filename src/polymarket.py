"""Client HTTP minimal pour les API publiques Polymarket (lecture seule)."""
from __future__ import annotations

import json
import logging

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import (
    CLOB_URL,
    DATA_API_URL,
    EVENT_ID,
    EVENT_SLUG,
    GAMMA_URL,
    HTTP_TIMEOUT_S,
    MAX_SPREAD_FOR_MID,
)

log = logging.getLogger(__name__)


def _session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=4,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers["User-Agent"] = "polymarket-presidentielle-2027/1.0"
    return s


SESSION = _session()


def _get(url: str, params: dict | None = None):
    r = SESSION.get(url, params=params, timeout=HTTP_TIMEOUT_S)
    r.raise_for_status()
    return r.json()


def _as_list(value):
    """Gamma renvoie certains tableaux sous forme de chaîne JSON."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return []
    return value or []


def _f(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- Gamma

def get_event() -> dict:
    """Événement complet (avec tous ses marchés). Slug d'abord, id en secours."""
    data = _get(f"{GAMMA_URL}/events", {"slug": EVENT_SLUG})
    if isinstance(data, list) and data:
        return data[0]
    log.warning("Slug %s introuvable, repli sur l'id %s", EVENT_SLUG, EVENT_ID)
    return _get(f"{GAMMA_URL}/events/{EVENT_ID}")


def display_price(bid: float | None, ask: float | None, last: float | None) -> float | None:
    """Règle Polymarket : prix médian si spread <= 10 c, sinon dernier échange."""
    if bid is not None and ask is not None and ask > 0 and (ask - bid) <= MAX_SPREAD_FOR_MID:
        return round((bid + ask) / 2, 4)
    return last


def parse_market(m: dict) -> dict | None:
    """Extrait un candidat d'un marché Gamma. None si inexploitable."""
    name = (m.get("groupItemTitle") or "").strip()
    tokens = _as_list(m.get("clobTokenIds"))
    outcomes = _as_list(m.get("outcomes"))
    if not name or len(tokens) < 2:
        return None
    # Jeton « Oui » : on suit l'ordre des outcomes plutôt que de supposer l'index 0
    yes_idx = next((i for i, o in enumerate(outcomes) if str(o).lower() == "yes"), 0)
    bid, ask, last = _f(m.get("bestBid")), _f(m.get("bestAsk")), _f(m.get("lastTradePrice"))
    return {
        "id": str(m.get("id")),
        "name": name,
        "condition_id": m.get("conditionId"),
        "token_yes": str(tokens[yes_idx]),
        "price": display_price(bid, ask, last),
        "bid": bid,
        "ask": ask,
        "last": last,
        "volume": _f(m.get("volumeNum")) or _f(m.get("volume")) or 0.0,
        "volume24h": _f(m.get("volume24hr")) or 0.0,
        "active": bool(m.get("active")) and not m.get("closed"),
        "closed": bool(m.get("closed")),
        "image": m.get("icon") or m.get("image"),
    }


def is_tradable_candidate(c: dict, raw: dict) -> bool:
    """Exclut les marchés « placeholder » (negRisk augmenté) jamais échangés."""
    if c["closed"]:
        return True  # on garde les candidats clos (retirés) pour l'historique
    return bool(raw.get("enableOrderBook")) and c["volume"] > 0


# ---------------------------------------------------------------- CLOB

def get_price_history(
    token_id: str,
    *,
    interval: str | None = None,
    start_ts: int | None = None,
    end_ts: int | None = None,
    fidelity: int = 15,
) -> list[tuple[int, float]]:
    """Historique du prix « Oui » : liste de (timestamp, prix)."""
    params: dict = {"market": token_id, "fidelity": fidelity}
    if interval:
        params["interval"] = interval
    else:
        params["startTs"] = start_ts
        params["endTs"] = end_ts
    data = _get(f"{CLOB_URL}/prices-history", params)
    hist = data.get("history", []) if isinstance(data, dict) else []
    return [(int(h["t"]), float(h["p"])) for h in hist if "t" in h and "p" in h]


# ---------------------------------------------------------------- Data API

def get_positions(address: str) -> list[dict]:
    """Positions ouvertes d'une adresse proxy (utilisé par le récap)."""
    out, offset = [], 0
    while True:
        page = _get(
            f"{DATA_API_URL}/positions",
            {"user": address, "sizeThreshold": 0, "limit": 500, "offset": offset},
        )
        if not page:
            break
        out.extend(page)
        if len(page) < 500:
            break
        offset += 500
    return out
