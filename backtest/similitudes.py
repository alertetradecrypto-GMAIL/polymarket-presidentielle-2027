"""Similitudes FR 2027 ↔ US 2024, au même compte à rebours avant le scrutin.

Pour chaque candidat FR : prix du Oui au jour J (dernier jour de données FR), variations
sur 30 et 90 jours, part du volume → les 2 candidats US les plus proches au même J.
Lecture seule, aucun ordre réel.

Usage : python backtest/similitudes.py
Sortie : backtest/out/similitudes.html (+ .json)
Limite : pour les US on n'a que le volume total final (y compris l'après-J) → faible poids.
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import math
from pathlib import Path

from oui_non import daily

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
FR_VOTE, US_VOTE = dt.date(2027, 4, 12), dt.date(2024, 11, 5)
US_DIR = HERE / "events" / "presidential-election-winner-2024"
FR_MIN_PRICE = 0.004  # candidats FR sous 0,4 ¢ ignorés
W_TREND, W_VOL = 8.0, 0.3  # poids des variations (en $) et du volume (log) dans la distance


def feats(d, t):
    ds = sorted(x for x in d if x <= t)
    if not ds or ds[-1] < t - dt.timedelta(days=3):
        return None
    p = d[ds[-1]]

    def at(k):
        dd = [x for x in ds if x <= t - dt.timedelta(days=k)]
        return d[dd[-1]] if dd else d[ds[0]]

    return dict(p=p, c30=p - at(30), c90=p - at(90))


def dist(a, b):
    lg = lambda x: math.log(max(x, 0.002))
    return math.sqrt((lg(a["p"]) - lg(b["p"])) ** 2
                     + (W_TREND * (a["c90"] - b["c90"])) ** 2 + (W_TREND * (a["c30"] - b["c30"])) ** 2
                     + W_VOL * (lg(a["vs"] + .002) - lg(b["vs"] + .002)) ** 2)


def quality(x):
    return "bonne" if x < 0.6 else "moyenne" if x < 1.3 else "faible"


def series(d, vote):
    return [[(day - vote).days, round(v * 100, 2)] for day, v in sorted(d.items())]


def main():
    FR, US = {}, {}
    for f in glob.glob(str(ROOT / "docs" / "data" / "history" / "*.json")):
        h = json.load(open(f))
        d = daily(h["p"])
        if d:
            FR[h["name"]] = dict(d=d, vol=sum(x[1] for x in h.get("vol", [])))
    fr_t = max(max(c["d"]) for c in FR.values())
    J = (FR_VOTE - fr_t).days
    us_t = US_VOTE - dt.timedelta(days=J)
    for m in json.load(open(US_DIR / "_event.json"))["markets"]:
        d = daily(json.load(open(US_DIR / m["file"]))["yes"])
        US[m["name"]] = dict(d=d, vol=m["volume"], win=float(m["outcome_prices"][0]))
    for D, t in ((FR, fr_t), (US, us_t)):
        for n in list(D):
            f = feats(D[n]["d"], t)
            if f is None or (D is FR and f["p"] < FR_MIN_PRICE):
                del D[n]
            else:
                D[n].update(f)
        tv = sum(c["vol"] for c in D.values())
        for c in D.values():
            c["vs"] = c["vol"] / tv
    for c in US.values():
        fut = [v for day, v in c["d"].items() if day > us_t]
        c["after"] = dict(max=max(fut, default=c["p"]), fin=c["win"])

    row = lambda n, c: dict(name=n, p=round(c["p"] * 100, 2), c30=round(c["c30"] * 100, 2),
                            c90=round(c["c90"] * 100, 2), vs=round(c["vs"] * 100, 1))
    matches = []
    for n, c in sorted(FR.items(), key=lambda x: -x[1]["p"]):
        best = sorted(US, key=lambda u: dist(c, US[u]))[:2]
        matches.append(row(n, c) | dict(us=[dict(name=u, d=round(dist(c, US[u]), 2), q=quality(dist(c, US[u])),
                                                  max=round(US[u]["after"]["max"] * 100, 1), fin=US[u]["win"])
                                             for u in best]))
    data = dict(J=J, fr_t=str(fr_t), us_t=str(us_t), matches=matches,
                us=[row(n, c) | dict(max=round(c["after"]["max"] * 100, 1), fin=c["win"])
                    for n, c in sorted(US.items(), key=lambda x: -x[1]["p"])],
                fr_series={n: series(c["d"], FR_VOTE) for n, c in FR.items()},
                us_series={n: series(c["d"], US_VOTE) for n, c in US.items()})
    OUT.mkdir(exist_ok=True)
    json.dump(data, open(OUT / "similitudes.json", "w"), ensure_ascii=False)
    tpl = (HERE / "similitudes_page.html").read_text()
    (OUT / "similitudes.html").write_text(tpl.replace("__DATA__", json.dumps(data, ensure_ascii=False)))
    print(f"J-{J} : FR au {fr_t}, US au {us_t}")
    for m in matches:
        print(f"  {m['name']:24} {m['p']:5.1f} ¢ → " + ", ".join(f"{u['name']} ({u['d']}, {u['q']})" for u in m["us"]))


if __name__ == "__main__":
    main()
