"""Backtest « Oui contre Non » : 100 $ par candidat achetés au premier jour, puis vente
de 20 % du stock initial à chaque palier franchi par le jeton détenu, jusqu'à la part
conservée. Lecture seule, aucun ordre réel.

Usage :
  python backtest/oui_non.py fr   # présidentielle 2027, docs/data/history (Non = 1 − Oui)
  python backtest/oui_non.py us   # présidentielle US 2024, backtest/events/... (Non réel, résolu)
Sorties : backtest/out/oui_non_<src>.json et .html
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent / "out"
STAKE, FRAC = 100.0, 0.2
BUDGET_US, US_DAYS_BEFORE = 1000.0, 205  # budget total réparti également, départ J-205
US_ELECTION = dt.date(2024, 11, 5)
# (libellé, côté, palier, relatif ?)
RULES = [("Oui +2 pts", "Y", .02, False), ("Non +2 pts", "N", .02, False),
         ("Non +1 pt", "N", .01, False), ("Non +2 % rel.", "N", .02, True),
         ("Non +1 % rel.", "N", .01, True)]
KEEPS = (0.0, 0.2, 0.4)


def daily(pts, start=None, end=None):
    d = {}
    for t, v in pts:
        day = dt.datetime.fromtimestamp(t, dt.UTC).date()
        if (start is None or day >= start) and (end is None or day <= end):
            d[day] = v  # dernier prix du jour
    return d


def load_fr():
    h_dir = ROOT / "docs" / "data" / "history"
    start, end = dt.date(2026, 4, 9), dt.date(2026, 10, 9)
    end_ts = int(dt.datetime(2026, 10, 9, 23, 59, tzinfo=dt.UTC).timestamp())
    rows = []
    for f in glob.glob(str(h_dir / "*.json")):
        h = json.load(open(f))
        y = daily(h["p"], start, end)
        if not y or min(y) != start:
            continue
        v7 = sum(x[1] for x in h.get("vol", []) if end_ts - 7 * 86400 <= x[0] <= end_ts)
        days = sorted(y)
        rows.append((v7, dict(name=h["name"], days=days, Y=[y[d] for d in days],
                              N=[1 - y[d] for d in days], win=None)))
    rows.sort(key=lambda r: -r[0])
    return "Présidentielle FR 2027 — 9 avr. → 9 oct. 2026 (Non = 1 − Oui, valeur au marché)", \
        [r[1] for r in rows[:17]]


def load_us():
    ev_dir = ROOT / "backtest" / "events" / "presidential-election-winner-2024"
    ev = json.load(open(ev_dir / "_event.json"))
    start = US_ELECTION - dt.timedelta(days=US_DAYS_BEFORE)
    end = dt.datetime.fromtimestamp(ev["end"], dt.UTC).date() - dt.timedelta(days=1)
    out = []
    for m in ev["markets"]:
        h = json.load(open(ev_dir / m["file"]))
        y, n = daily(h["yes"], start, end), daily(h["no"], start, end)
        days = sorted(set(y) & set(n))
        out.append(dict(name=m["name"], days=days, Y=[y[d] for d in days], N=[n[d] for d in days],
                        win=float(m["outcome_prices"][0])))
    return f"Présidentielle US 2024 — {out[0]['days'][0]:%d/%m/%Y} → {end:%d/%m/%Y} " \
           f"(J-{US_DAYS_BEFORE}, Non réel, valeur à la résolution : Trump gagne)", out


def run(c, side, step, rel, keep, stake=STAKE):
    px, days = c[side], c["days"]
    p0 = px[0]
    q0 = stake / p0
    q, cash, ref = q0, 0.0, p0
    log = [(str(days[0]), "achat", round(p0, 4), round(q0, 2), 0.0, round(q0, 2))]
    for d, pr in zip(days[1:], px[1:]):
        while q > keep * q0 + 1e-9:
            nxt = ref * (1 + step) if rel else ref + step
            if pr < nxt:
                break
            sq = min(FRAC * q0, q - keep * q0)
            q -= sq
            cash += sq * pr
            ref = nxt
            log.append((str(d), "vente", round(pr, 4), round(sq, 2), round(cash, 2), round(q, 2)))
    return dict(q=q, cash=cash, last=px[-1], log=log)


def settle(c, side):
    """Prix de règlement du jeton détenu : 1/0 si résolu, sinon dernier prix (valeur au marché)."""
    if c["win"] is None:
        return round(c[side][-1], 4)
    return c["win"] if side == "Y" else 1 - c["win"]


def main(src):
    title, C = (load_fr if src == "fr" else load_us)()
    stake = BUDGET_US / len(C) if src == "us" else STAKE
    names = [c["name"] for c in C]
    S = {}
    for lab, side, step, rel in RULES:
        for keep in KEEPS:
            R = [run(c, side, step, rel, keep, stake) for c in C]
            k = f"{lab} – conserver {int(keep * 100)} %"
            cash = sum(r["cash"] for r in R)
            mtm = sum(r["cash"] + r["q"] * r["last"] for r in R)
            # valeur si le candidat w gagne (jetons Oui de w = 1, Non des autres = 1)
            sc = {}
            for w in names + ["Autre"]:
                sc[w] = round(cash + sum(r["q"] * ((c["name"] == w) if side == "Y" else (c["name"] != w))
                                         for c, r in zip(C, R)), 2)
            S[k] = dict(ventes=sum(len(r["log"]) - 1 for r in R), cash=round(cash, 2),
                        tok=round(mtm - cash, 2), mtm=round(mtm, 2), sc=sc,
                        log={c["name"]: r["log"] for c, r in zip(C, R)},
                        pos={c["name"]: [round(r["q"], 2), round(r["last"], 4), settle(c, side), round(r["cash"], 2)]
                             for c, r in zip(C, R)})
            if C[0]["win"] is not None:  # résolu
                S[k]["final"] = sc[next(c["name"] for c in C if c["win"] == 1)]
    OUT.mkdir(exist_ok=True)
    json.dump(dict(title=title, stake=stake, data=S), open(OUT / f"oui_non_{src}.json", "w"), ensure_ascii=False)
    tpl = (Path(__file__).resolve().parent / "oui_non_page.html").read_text()
    (OUT / f"oui_non_{src}.html").write_text(
        tpl.replace("__TITLE__", title).replace("__STAKE__", f"{stake * len(C):,.0f}".replace(",", " ") + " $ au total, soit " + f"{stake:.2f}".replace(".", ",")).replace("__STAKE_N__", str(stake)).replace("__STAKE_ONE__", str(stake))
        .replace("__DATA__", json.dumps(S, ensure_ascii=False)))
    w = "final" if C[0]["win"] is not None else "mtm"
    for k, s in S.items():
        print(f"{k:32} ventes={s['ventes']:4} encaissé={s['cash']:8.0f} {w}={s.get(w, s['mtm']):8.0f} "
              f"pire={min(s['sc'].values()):8.0f}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "fr")
