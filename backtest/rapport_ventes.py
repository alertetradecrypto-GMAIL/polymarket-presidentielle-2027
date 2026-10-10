"""Rapport simple : variantes de vente par paliers pour les 4 positions de Nick,
avec le détail de chaque vente sur les trajectoires US 2024 projetées (J-185 → vote).

Exécution réaliste : un ordre à cours limité est posé à chaque palier et s'exécute
AU PRIX DU PALIER quand le prix du jour l'atteint (pas de vente au-dessus).
Résultats : trajectoire réelle (ventes détaillées) + moyenne / 10e centile sur N_VAR variantes.
Lecture seule, aucun ordre réel.

Usage : python backtest/rapport_ventes.py  → backtest/out/rapport_ventes.html (+ .json)
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np

from strategie_paliers import (ES_Q, HOLD, MARCHE, N_VAR, OUT, POS, SCEN, SEED, expit, label, logit,
                               us_paths, variants)

FR_J185 = dt.date(2027, 4, 11) - dt.timedelta(days=185)  # J-185 avant le 1er tour 2027
ALLOC = [250, 100, 350, 300]  # Le Pen Non, Philippe Oui, Mélenchon Non, Lisnard Oui


def P(step, frac, keep):
    return (("pts", step), frac, keep)


# Variantes : (nom, explication courte, règles LP, PH, ME, LI)
VARIANTES = [
    ("A. Ton réglage", "Le Pen et Philippe : +3 pts, 5 % par palier, 35 % gardés. Mélenchon conservé. "
     "Lisnard : +15 pts, 20 % par palier, 20 % gardés.",
     [P(.03, .05, .35), P(.03, .05, .35), HOLD, P(.15, .20, .20)]),
    ("B. Ton réglage, Lisnard vendu en entier", "Comme A, mais Lisnard continue de vendre jusqu'à 0 % "
     "(5e palier à 0,876).", [P(.03, .05, .35), P(.03, .05, .35), HOLD, P(.15, .20, 0)]),
    ("C. Paliers espacés (+5 pts)", "Comme A, mais Le Pen et Philippe vendent tous les +5 pts au lieu de +3.",
     [P(.05, .05, .35), P(.05, .05, .35), HOLD, P(.15, .20, .20)]),
    ("D. Seul Lisnard est vendu", "Le Pen, Philippe et Mélenchon conservés. Lisnard : +15 pts, 20 %, vendu en entier.",
     [HOLD, HOLD, HOLD, P(.15, .20, 0)]),
    ("E. Lisnard plus tôt (+10 pts)", "Le Pen, Philippe, Mélenchon conservés. Lisnard : +10 pts, 20 %, 20 % gardés.",
     [HOLD, HOLD, HOLD, P(.10, .20, .20)]),
    ("F. Tout conserver", "Aucune vente : on attend la résolution.", [HOLD, HOLD, HOLD, HOLD]),
]


def plan(pe, prm, mise):
    """Ordres à poser : liste (palier, prix, jetons, encaissé, stock restant %)."""
    (_, step), frac, keep = prm
    if keep >= 1:
        return []
    q0 = mise / pe
    out, q, k, px = [], q0, 0, pe
    while q > keep * q0 + 1e-9:
        k += 1
        px = round(pe + k * step, 4)
        if px >= 1:
            break
        sq = min(frac * q0, q - keep * q0)
        q -= sq
        out.append(dict(n=k, px=px, q=round(sq, 1), cash=round(sq * px, 2), reste=round(100 * q / q0)))
    return out


def run_limit(px, pe, prm):
    """Vectorisé sur les variantes, mise 1 $ : ventes au prix du palier. Renvoie (encaissé, jetons)."""
    (_, step), frac, keep = prm
    V, T = px.shape
    q0 = 1 / pe
    q, cash, k = np.full(V, q0), np.zeros(V), np.zeros(V)
    if keep >= 1:
        return cash, q
    for t in range(1, T):
        for _ in range(20):
            lvl = pe + (k + 1) * step
            can = (q > keep * q0 + 1e-12) & (px[:, t] >= lvl) & (lvl < 1)
            if not can.any():
                break
            sq = np.where(can, np.minimum(frac * q0, q - keep * q0), 0)
            q, cash, k = q - sq, cash + sq * lvl, k + can
    return cash, q


def ventes_reelles(path, pe, prm, mise, days):
    """Détail des ventes sur la trajectoire réelle (variante 0)."""
    (_, step), frac, keep = prm
    if keep >= 1:
        return []
    q0, q, k, out = mise / pe, mise / pe, 0, []
    for t in range(1, len(path)):
        while q > keep * q0 + 1e-9:
            lvl = pe + (k + 1) * step
            if lvl >= 1 or path[t] < lvl:
                break
            k += 1
            sq = min(frac * q0, q - keep * q0)
            q -= sq
            out.append(dict(j=t - 185, date=(FR_J185 + dt.timedelta(days=t)).isoformat(), n=k, px=round(lvl, 3),
                            q=round(sq, 1), cash=round(sq * lvl, 2), reste=round(100 * q / q0)))
    return out


def main():
    rng = np.random.default_rng(SEED)
    days, Z = us_paths()
    probs, pmkt = np.array([s[1] for s in SCEN]), np.array(MARCHE)
    act = [i for i, s in enumerate(SCEN) if s[1] > 0]
    # Tokens par scénario/position (mêmes variantes pour toutes les stratégies)
    TOK = []
    for sn, pr, mp, win in SCEN:
        Pv = variants(Z, set(mp.values()), rng)
        row = {}
        for k, _, side, pe, y0 in POS:
            y = expit(logit(y0) + Pv[mp[k]] - Pv[mp[k]][:, :1])
            tok = y if side == "Y" else 1 - y
            tok[:, 0] = pe
            row[k] = (tok, float((win == k) if side == "Y" else (win != k)))
        TOK.append(row)

    res = []
    for nom, expl, prms in VARIANTES:
        tot = np.zeros((len(SCEN), N_VAR))
        pos_out, per_scen = [], [dict() for _ in SCEN]
        for (k, name, side, pe, _), prm, mise in zip(POS, prms, ALLOC):
            ventes = []
            for si, (sn, *_r) in enumerate(SCEN):
                tok, settle = TOK[si][k]
                cash, q = run_limit(tok, pe, prm)
                val = mise * (cash + q * settle)
                tot[si] += val
                v = ventes_reelles(tok[0], pe, prm, mise, days)
                encaisse = sum(x["cash"] for x in v)
                reste_q = mise / pe - sum(x["q"] for x in v)
                fin = reste_q * settle
                per_scen[si][name] = round(float(val.mean()) - mise)
                ventes.append(dict(scen=sn, ventes=v, encaisse=round(encaisse, 2), reste_q=round(reste_q, 1),
                                   resolution=round(fin, 2), pnl=round(encaisse + fin - mise, 2),
                                   gagne=bool(settle)))
            pos_out.append(dict(name=name, side=side, pe=pe, mise=mise, q0=round(mise / pe, 1), regle=label(prm),
                                plan=plan(pe, prm, mise), ventes=ventes))
        m = tot.mean(axis=1) - sum(ALLOC)
        es = np.sort(tot, axis=1)[:, : int(ES_Q * N_VAR)].mean(axis=1) - sum(ALLOC)
        scen = [dict(n=s[0], p=s[1], pm=pm, reel=round(float(tot[i, 0]) - sum(ALLOC)), moy=round(float(m[i])),
                     p10=round(float(np.percentile(tot[i], 10)) - sum(ALLOC)), per=per_scen[i])
                for i, (s, pm) in enumerate(zip(SCEN, MARCHE))]
        res.append(dict(nom=nom, expl=expl, E=round(float(probs @ m)), Em=round(float(pmkt @ m)),
                        pire=round(float(min(es[i] for i in act))),
                        pire_scen=SCEN[min(act, key=lambda i: es[i])][0], scen=scen, pos=pos_out))
    data = dict(alloc=ALLOC, n_var=N_VAR, fr_j185=FR_J185.isoformat(), variantes=res,
                scen=[dict(n=s[0], p=s[1], pm=pm, map={n: s[2][k] for k, n, *_x in POS}) for s, pm in zip(SCEN, MARCHE)])
    OUT.mkdir(exist_ok=True)
    json.dump(data, open(OUT / "rapport_ventes.json", "w"), ensure_ascii=False, indent=1)
    tpl = (Path(__file__).resolve().parent / "rapport_ventes_page.html").read_text()
    (OUT / "rapport_ventes.html").write_text(tpl.replace("__DATA__", json.dumps(data, ensure_ascii=False)))
    for r in res:
        print(f"{r['nom']:42} E {r['E']:+5}  Emkt {r['Em']:+5}  pire {r['pire']:+5} ({r['pire_scen']})")
        print("   " + "  ".join(f"{s['n'][:14]} {s['moy']:+5}" for s in r["scen"][:5]))


if __name__ == "__main__":
    main()
