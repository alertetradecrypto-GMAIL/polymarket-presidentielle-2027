"""Backtest stratégies 1 + 3 : ordres limites (maker) déclenchés par l'écart
prix / valeur théorique issue des sondages. Lecture seule, aucun ordre réel.

Usage :
  python backtest/run.py            # historique du dépôt (docs/data/history)
  python backtest/run.py --fetch    # historique horaire via l'API CLOB (GitHub Actions)

Règles (fixées avant résultats) :
- Entrée si prix du jeton < valeur théorique − écart (écarts 2 / 3 / 4 points).
  Côté « Oui » si le marché sous-évalue le candidat, côté « Non » s'il le surévalue.
- Maker : ordre limite à mi − 0,5¢, exécuté seulement si le prix passe ensuite sous
  la limite d'au moins 1¢ (« maker_strict ») ou 0,5¢ (« maker_souple ») dans les 48 h,
  sinon annulé. Pas de frais (rebates ignorés).
- Sortie quand le prix du jeton atteint la valeur théorique : limite à mi + 0,5¢,
  exécutée si le prix dépasse la limite d'au moins 1¢ dans les 48 h.
- Taker (comparaison) : achat à mi + 0,5¢, vente à mi − 0,5¢, frais 0,04 × p × (1 − p).
- 100 $ par trade, une position max par candidat et par sens.
- Positions encore ouvertes valorisées au dernier prix médian.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

import model

ROOT = Path(__file__).resolve().parent.parent
HIST = ROOT / "docs" / "data" / "history"
OUT = Path(__file__).parent / "out"

CANDIDATES = {  # slug → clé du modèle
    "marine-le-pen": "rn",
    "edouard-philippe": "philippe",
    "jean-luc-melenchon": "melenchon",
    "david-lisnard": "lisnard",
}
RN_SLUGS = ("marine-le-pen", "jordan-bardella")
DAYS = 183
GAPS = (0.02, 0.03, 0.04)
STAKE = 100.0
HALF_SPREAD = 0.005
CROSS = {"maker_strict": 0.01, "maker_souple": 0.005}
ORDER_TTL = 48 * 3600
FEE_RATE = 0.04


# ---------------------------------------------------------------- données

def load_local(slug: str, start: int) -> list[tuple[int, float]]:
    d = json.loads((HIST / f"{slug}.json").read_text(encoding="utf-8"))
    return [(int(t), float(p)) for t, p in d["p"] if t >= start]


def load_api(slug: str, start: int, end: int) -> list[tuple[int, float]]:
    sys.path.insert(0, str(ROOT / "src"))
    import polymarket as pm  # noqa: E402
    token = json.loads((HIST / f"{slug}.json").read_text(encoding="utf-8"))["token_yes"]
    pts, t0 = {}, start
    while t0 < end:
        t1 = min(t0 + 14 * 86400, end)
        for t, p in pm.get_price_history(token, start_ts=t0, end_ts=t1, fidelity=60):
            pts[t] = p
        t0 = t1
        time.sleep(0.2)
    return sorted(pts.items())


def hourly(points: list[tuple[int, float]]) -> dict[int, float]:
    """Une valeur par heure (dernière connue), heures manquantes reportées."""
    by_h: dict[int, float] = {}
    for t, p in points:
        by_h[t - t % 3600] = p
    if not by_h:
        return {}
    out, last = {}, None
    for h in range(min(by_h), max(by_h) + 1, 3600):
        last = by_h.get(h, last)
        out[h] = last
    return out


# ---------------------------------------------------------------- simulation

@dataclass
class Book:
    mode: str              # "maker_strict" | "maker_souple" | "taker"
    gap: float
    sides: tuple           # ("yes",) ou ("yes", "no")
    cash: float = 0.0
    trades: list = field(default_factory=list)
    pos: dict = field(default_factory=dict)      # (slug, side) -> dict
    orders: dict = field(default_factory=dict)   # (slug, side) -> dict
    equity: list = field(default_factory=list)


def fee(q: float) -> float:
    return FEE_RATE * q * (1 - q)


def tick(x: float) -> float:
    return round(min(max(x, 0.001), 0.999), 3)


def step(book: Book, h: int, slug: str, side: str, q: float, fv: float | None):
    key = (slug, side)
    order = book.orders.get(key)
    if order:   # ordre maker en attente
        c = CROSS[book.mode]
        hit = q <= order["px"] - c if order["kind"] == "buy" else q >= order["px"] + c
        if hit:
            fill(book, h, key, order["kind"], order["px"], order["fv"])
            book.orders.pop(key)
        elif h - order["t"] > ORDER_TTL:
            book.orders.pop(key)
        return
    if fv is None:
        return
    pos = book.pos.get(key)
    if pos is None and q < fv - book.gap:
        if book.mode == "taker":
            fill(book, h, key, "buy", tick(q + HALF_SPREAD), fv, taker=True)
        else:
            book.orders[key] = {"kind": "buy", "px": tick(q - HALF_SPREAD), "t": h, "fv": fv}
    elif pos is not None and q >= fv:
        if book.mode == "taker":
            fill(book, h, key, "sell", tick(q - HALF_SPREAD), fv, taker=True)
        else:
            book.orders[key] = {"kind": "sell", "px": tick(q + HALF_SPREAD), "t": h, "fv": fv}


def fill(book: Book, h: int, key, kind: str, px: float, fv: float, taker: bool = False):
    f = fee(px) if taker else 0.0
    if kind == "buy":
        shares = STAKE / (px + f)
        book.cash -= STAKE
        book.pos[key] = {"shares": shares, "px": px + f, "t": h, "fv": fv}
    else:
        pos = book.pos.pop(key)
        proceeds = pos["shares"] * (px - f)
        book.cash += proceeds
        book.trades.append({"slug": key[0], "side": key[1], "t_in": pos["t"], "t_out": h,
                            "px_in": round(pos["px"], 4), "px_out": round(px - f, 4),
                            "pnl": round(proceeds - STAKE, 2)})


def mark(book: Book, prices: dict) -> float:
    v = book.cash
    for (slug, side), pos in book.pos.items():
        p = prices[slug]
        v += pos["shares"] * (p if side == "yes" else 1 - p)
    return v


def fair_values(day: date, p1, p2, rn_share: dict, widen: bool, cache: dict) -> dict | None:
    state = model.poll_state(p1, p2, day)
    if state is None:
        return None
    s1, s2 = model.sigmas(day, widen)
    key = (json.dumps(state, sort_keys=True), round(s1, 1), round(s2, 1))
    if key not in cache:
        cache[key] = model.win_probs(state, s1, s2)
    w = cache[key]
    fv = {}
    for slug, k in CANDIDATES.items():
        fv[slug] = w["rn"] * rn_share.get(slug, 0.0) if k == "rn" else w[k]
    return fv


def run(series: dict[str, dict[int, float]], widen: bool) -> list[dict]:
    p1, p2 = model.load_polls()
    hours = sorted(set().union(*[set(s) for s in series.values()]))
    books = [Book(m, g, sides) for m in ("maker_strict", "maker_souple", "taker") for g in GAPS
             for sides in (("yes",), ("yes", "no"))]
    cache, last, fv_by_day, fv_log = {}, {}, {}, {}
    for h in hours:
        for slug, s in series.items():
            if h in s and s[h] is not None:
                last[slug] = s[h]
        if not all(slug in last for slug in series):
            continue
        day = datetime.fromtimestamp(h, UTC).date()
        if day not in fv_by_day:
            lp, jb = last["marine-le-pen"], last.get("jordan-bardella", 0.0)
            share = {"marine-le-pen": lp / (lp + jb) if lp + jb else 0.0}
            fv_by_day[day] = fair_values(day, p1, p2, share, widen, cache)
            if fv_by_day[day]:
                fv_log[day.isoformat()] = {k: round(v, 4) for k, v in fv_by_day[day].items()}
        fv = fv_by_day[day]
        for book in books:
            for slug in CANDIDATES:
                p = last[slug]
                for side in book.sides:
                    q = p if side == "yes" else 1 - p
                    f = None if fv is None else (fv[slug] if side == "yes" else 1 - fv[slug])
                    step(book, h, slug, side, q, f)
            book.equity.append((h, round(mark(book, last), 2)))
    return books, fv_log, last


def stats(book: Book, last: dict) -> dict:
    eq = [v for _, v in book.equity]
    peak, dd = 0.0, 0.0
    for v in eq:
        peak = max(peak, v)
        dd = min(dd, v - peak)
    closed = book.trades
    wins = sum(1 for t in closed if t["pnl"] > 0)
    pnl_slug: dict[str, float] = {}
    for t in closed:
        pnl_slug[t["slug"]] = pnl_slug.get(t["slug"], 0) + t["pnl"]
    mtm = dict(pnl_slug)
    for (slug, side), pos in book.pos.items():
        q = last[slug] if side == "yes" else 1 - last[slug]
        mtm[slug] = mtm.get(slug, 0) + pos["shares"] * q - STAKE
    return {
        "pnl_by_candidate": {k: round(v, 2) for k, v in mtm.items()},
        "mode": book.mode, "gap_pts": round(book.gap * 100), "sides": "+".join(book.sides),
        "pnl": round(eq[-1], 2) if eq else 0.0,
        "realized": round(sum(t["pnl"] for t in closed), 2),
        "trades_closed": len(closed), "open": len(book.pos),
        "win_rate": round(wins / len(closed), 2) if closed else None,
        "max_drawdown": round(dd, 2),
        "max_capital": round(STAKE * len(CANDIDATES) * len(book.sides), 0),
        "realized_by_candidate": {k: round(v, 2) for k, v in pnl_slug.items()},
        "open_positions": [{"slug": s, "side": sd, "px_in": round(p["px"], 4),
                            "since": datetime.fromtimestamp(p["t"], UTC).date().isoformat()}
                           for (s, sd), p in book.pos.items()],
    }


def buy_and_hold(series: dict, start_h: int, last: dict) -> dict:
    out = {}
    for slug in CANDIDATES:
        s = series[slug]
        h0 = min(h for h in s if h >= start_h and s[h] is not None)
        p0 = tick(s[h0] + HALF_SPREAD)
        out[slug] = round(STAKE / p0 * last[slug] - STAKE, 2)
    out["total"] = round(sum(out.values()), 2)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="historique horaire via l'API CLOB")
    args = ap.parse_args(argv)
    now = int(time.time())
    start = now - DAYS * 86400
    slugs = list(CANDIDATES) + ["jordan-bardella"]
    raw = {s: (load_api(s, start, now) if args.fetch else load_local(s, start)) for s in slugs}
    series = {s: hourly(v) for s, v in raw.items()}
    OUT.mkdir(exist_ok=True)
    report = {"generated": datetime.now(UTC).isoformat(timespec="seconds"),
              "source": "api" if args.fetch else "local",
              "points": {s: len(v) for s, v in raw.items()}, "variants": {}}
    for widen in (False, True):
        books, fv_log, last = run(series, widen)
        name = "incertitude_elargie" if widen else "incertitude_fixe"
        first_fv = min(fv_log) if fv_log else None
        start_h = int(datetime.fromisoformat(first_fv).replace(tzinfo=UTC).timestamp())
        report["variants"][name] = {
            "results": [stats(b, last) for b in books],
            "buy_and_hold_yes": buy_and_hold(series, start_h, last),
            "fair_values": fv_log,
            "trades": {f"{b.mode}_{round(b.gap*100)}_{'+'.join(b.sides)}": b.trades for b in books},
            "equity": {f"{b.mode}_{round(b.gap*100)}_{'+'.join(b.sides)}": b.equity[::24]
                       for b in books},
        }
    report["last_price"] = {k: v for k, v in last.items()}
    report["prices"] = {s: [(h, p) for h, p in sorted(v.items())][::6] for s, v in series.items()}
    (OUT / "results.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    for name, v in report["variants"].items():
        print(f"\n== {name} ==  buy&hold Oui : {v['buy_and_hold_yes']}")
        print(f"{'mode':12} {'écart':>5} {'sens':7} {'PnL $':>8} {'réalisé':>8} {'trades':>6} "
              f"{'ouv.':>4} {'réussite':>8} {'DD max':>8}")
        for r in v["results"]:
            print(f"{r['mode']:12} {r['gap_pts']:>5} {r['sides']:7} {r['pnl']:>8} {r['realized']:>8} "
                  f"{r['trades_closed']:>6} {r['open']:>4} {str(r['win_rate']):>8} "
                  f"{r['max_drawdown']:>8}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
