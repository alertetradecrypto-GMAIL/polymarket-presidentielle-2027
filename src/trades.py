"""Journal de tous les trades du marché → docs/data/trades/AAAA-MM-JJ.json.

Lancé par collect.yml après collect.py (qui fournit candidates.json).
Un fichier par jour de Paris, trades les plus récents d'abord :
  {"date": ..., "cols": COLS, "rows": [[t, candidat, Yes/No, BUY/SELL, prix,
   parts, $, adresse, pseudo, tx], ...]}
Côté taker uniquement (= définition du volume Polymarket). Adresses complètes :
ce sont des données publiques on-chain ; rien ne distingue les adresses de Nick.

Rattrapage depuis l'ouverture du marché, quelques candidats par collecte et
par morceaux reprenables (limite de temps). Ensuite, chaque passage recharge
depuis la dernière collecte − 1 h (trades indexés en retard) et remplace les
lignes correspondantes : ni doublon ni trou.
"""
from __future__ import annotations

import logging
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import polymarket as pm
from collect import read_json, write_json
from config import (
    CANDIDATES_FILE,
    OVERLAP_S,
    TIMEZONE,
    TRADES_BACKFILL_PER_RUN,
    TRADES_DIR,
    TRADES_INDEX_FILE,
    TRADES_STATE_FILE,
    TRADES_TIME_BUDGET_S,
)

log = logging.getLogger("trades")
TZ = ZoneInfo(TIMEZONE)
COLS = ["t", "cand", "out", "side", "price", "size", "usd", "wallet", "name", "tx"]
I_T, I_CAND = 0, 1


def day_of(t: int) -> str:
    return datetime.fromtimestamp(t, TZ).date().isoformat()


def to_row(r: dict, slug: str) -> list:
    size, price = float(r["size"]), float(r["price"])
    wallet = (r.get("proxyWallet") or "").lower()
    name = (r.get("name") or r.get("pseudonym") or "").strip()
    if name.lower() == wallet:
        name = ""
    return [int(r["timestamp"]), slug, r.get("outcome") or "", r.get("side") or "",
            round(price, 4), round(size, 2), round(size * price, 2), wallet, name[:40],
            r.get("transactionHash") or ""]


def day_path(day: str):
    return TRADES_DIR / f"{day}.json"


def load_day(day: str) -> list:
    return (read_json(day_path(day), None) or {}).get("rows", [])


def existing_days() -> list[str]:
    if not TRADES_DIR.exists():
        return []
    return sorted(p.stem for p in TRADES_DIR.glob("????-??-??.json"))


def merge_candidate(slug: str, lo: int | None, hi: int | None, rows: list) -> set[str]:
    """Remplace les trades de `slug` dont lo <= t <= hi (bornes None = infini) par `rows`.

    Renvoie les jours modifiés.
    """
    by_day: dict[str, list] = {}
    for row in rows:
        by_day.setdefault(day_of(row[I_T]), []).append(row)
    lo_day = day_of(lo) if lo is not None else ""
    hi_day = day_of(hi) if hi is not None else "9999"
    days = {d for d in existing_days() if lo_day <= d <= hi_day} | set(by_day)

    def replaced(r):
        return (r[I_CAND] == slug and (lo is None or r[I_T] >= lo)
                and (hi is None or r[I_T] <= hi))

    changed = set()
    for day in sorted(days):
        old = load_day(day)
        new = [r for r in old if not replaced(r)] + by_day.get(day, [])
        if not new and not old:
            continue
        new.sort(key=lambda r: (-r[I_T], r[I_CAND], r[9], r[7]))
        if write_json(day_path(day), {"date": day, "cols": COLS, "rows": new}):
            changed.add(day)
    return changed


def backfill(slug: str, cond: str, hi: int, deadline: float) -> tuple[list, int | None]:
    """Trades de l'ouverture jusqu'à hi, du plus récent au plus ancien.

    S'arrête à `deadline` (time.monotonic) : renvoie alors les lignes strictement
    postérieures à la seconde d'arrêt, et cette seconde (reprise au run suivant).
    Renvoie (lignes, None) si l'historique est complet.
    """
    rows = []
    for i, r in enumerate(pm.iter_trade_rows(cond, None, hi)):
        rows.append(to_row(r, slug))
        if i % 500 == 499 and time.monotonic() > deadline:
            stop = rows[-1][I_T]
            return [x for x in rows if x[I_T] > stop], stop
    return rows, None


def update_index(days: set[str]) -> None:
    index = read_json(TRADES_INDEX_FILE, None) or {"cols": ["trades", "usd"], "days": {}}
    for day in days:
        rows = load_day(day)
        if rows:
            index["days"][day] = [len(rows), round(sum(r[6] for r in rows), 2)]
        else:
            index["days"].pop(day, None)
    index["days"] = dict(sorted(index["days"].items()))
    write_json(TRADES_INDEX_FILE, index, pretty=True)


def run(now: int | None = None, budget: int = TRADES_BACKFILL_PER_RUN,
        time_budget: float = TRADES_TIME_BUDGET_S) -> int:
    """state/trades.json : {slug: {"last": dernière collecte, "back": reprise du
    rattrapage (None = historique complet)}}."""
    deadline = time.monotonic() + time_budget
    now = now or int(time.time())
    candidates = (read_json(CANDIDATES_FILE, None) or {}).get("candidates", [])
    state = read_json(TRADES_STATE_FILE, None) or {}
    changed: set[str] = set()
    errors = 0

    # 1) Mises à jour courantes (candidats déjà suivis et encore ouverts)
    for c in candidates:
        st, cond = state.get(c["slug"]), c.get("condition_id")
        if not st or not cond or not c.get("active"):
            continue
        since = max(0, int(st["last"]) - OVERLAP_S)
        try:
            rows = [to_row(r, c["slug"]) for r in pm.iter_trade_rows(cond, since, now)]
            changed |= merge_candidate(c["slug"], since, None, rows)
            st["last"] = now
        except Exception as exc:  # un candidat en échec ne bloque pas les autres
            errors += 1
            log.error("Trades %s : %s", c["name"], exc)

    # 2) Rattrapages de l'historique (reprises d'abord, puis les plus actifs)
    todo = [c for c in candidates if c.get("condition_id")
            and (c["slug"] not in state or state[c["slug"]].get("back") is not None)]
    todo.sort(key=lambda c: (c["slug"] not in state, -(c.get("volume24h") or 0)))
    for c in todo[:budget]:
        if time.monotonic() > deadline:
            break
        slug = c["slug"]
        st = state.get(slug) or {"last": now, "back": now}
        try:
            rows, stop = backfill(slug, c["condition_id"], st["back"], deadline)
            changed |= merge_candidate(slug, None if stop is None else stop + 1,
                                       st["back"], rows)
        except Exception as exc:
            errors += 1
            log.error("Rattrapage %s : %s", c["name"], exc)
            continue
        st["back"] = stop
        state[slug] = st
        log.info("Rattrapage %s : %d trades%s", c["name"], len(rows),
                 "" if stop is None else " (à suivre)")

    if changed:
        update_index(changed)
    write_json(TRADES_STATE_FILE, dict(sorted(state.items())), pretty=True)
    log.info("%d jour(s) modifié(s), %d rattrapage(s) en attente, %d erreur(s)",
             len(changed), len(pending_backfills()), errors)
    return 1 if candidates and errors > len(candidates) / 2 else 0


def pending_backfills() -> list[str]:
    """Candidats dont l'historique n'est pas encore complet (avertissement email)."""
    candidates = (read_json(CANDIDATES_FILE, None) or {}).get("candidates", [])
    state = read_json(TRADES_STATE_FILE, None) or {}
    return [c["name"] for c in candidates if c.get("condition_id")
            and (c["slug"] not in state or state[c["slug"]].get("back") is not None)]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sys.exit(run())
