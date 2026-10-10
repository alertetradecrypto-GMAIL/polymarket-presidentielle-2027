"""Recherche de la meilleure stratégie de vente par paliers pour les 4 positions de Nick,
projetées sur les trajectoires US 2024 à partir du même compte à rebours (J-185).

Projection : décalage en cote (logit) depuis J-185 ; jetons réglés à 1 (gagnant) ou 0.
Robustesse : N_VAR variantes par scénario, en permutant les variations journalières par
blocs (même permutation pour tous les candidats d'un scénario → corrélations et point
d'arrivée conservés). La variante 0 est la trajectoire réelle.
Critère : espérance selon les probabilités de Nick, sous contrainte de perte maximale
si Le Pen gagne. Option : répartition des 1 000 $ entre les positions.
Lecture seule, aucun ordre réel.

Usage : python backtest/strategie_paliers.py [--perte-max 700]
Sortie : backtest/out/strategie_paliers.html (+ .json)
"""
from __future__ import annotations

import datetime as dt
import itertools
import json
import sys
from pathlib import Path

import numpy as np

from oui_non import daily

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
US_DIR = HERE / "events" / "presidential-election-winner-2024"
US_VOTE, J = dt.date(2024, 11, 5), 185
BUDGET, PERTE_MAX, PERTE_EQ = 1000.0, 700.0, 600.0  # pire cas toléré : max / version équilibrée
N_VAR, BLOC, SEED = 200, 10, 7
ES_Q = 0.20  # pire cas = moyenne des 20 % pires variantes
STAKES = range(100, 501, 50)  # répartition : 100 à 500 $ par position, pas de 50 $

# (clé, libellé, côté, prix d'entrée du jeton, prix du Oui FR à J-185)
POS = [("LP", "Le Pen", "N", 0.592, 0.408), ("PH", "Philippe", "Y", 0.225, 0.225),
       ("ME", "Mélenchon", "N", 0.875, 0.125), ("LI", "Lisnard", "Y", 0.126, 0.126)]

# Scénarios : proba de Nick, trajectoire US suivie par chaque candidat FR, gagnant.
# HARRIS_WIN = montée de Harris jusqu'à son pic, puis fin de la courbe de Trump (→ 1).
# Probabilités du marché (Oui au 9 oct.) pour comparaison ; « autre » = reste.
MARCHE = [.41, .125, .1125, .1125, .128, .112]
SCEN = [
    ("Le Pen gagne", .20, dict(LP="Trump", PH="Biden", ME="RFK", LI="RFK"), "LP"),
    ("Mélenchon gagne", .20, dict(LP="Biden", PH="RFK", ME="Trump", LI="RFK"), "ME"),
    ("Philippe gagne", .175, dict(LP="Biden", PH="Trump", ME="RFK", LI="RFK"), "PH"),
    ("Philippe gagne, Lisnard pic (Harris)", .175, dict(LP="Biden", PH="Trump", ME="RFK", LI="Harris"), "PH"),
    ("Lisnard gagne", .25, dict(LP="Biden", PH="RFK", ME="RFK", LI="HarrisWin"), "LI"),
    ("Un autre gagne (contrôle)", 0.0, dict(LP="Biden", PH="Biden", ME="RFK", LI="RFK"), None),
]
US_NAMES = dict(Trump="Donald Trump", Biden="Joe Biden", RFK="Robert F. Kennedy Jr.", Harris="Kamala Harris")

# Grille : (type, palier) × fraction vendue par palier × part conservée
STEPS = [("pts", s) for s in (.02, .03, .05, .08, .10, .15)] + [("rel", s) for s in (.10, .20, .30, .50)]
FRACS, KEEPS = (.10, .20, .25), (0.0, .20, .40)
GRID = list(itertools.product(STEPS, FRACS, KEEPS))
# Échelle fine : petits paliers réguliers, petite fraction vendue à chaque palier → nombreuses ventes
LADDER = list(itertools.product([("pts", s) for s in (.02, .03, .04, .05)], (.05, .10), (0.0, .20, .40)))
BASE = {"N": (("pts", .02), .20, .40), "Y": (("pts", .02), .20, .20)}  # règle actuelle de Nick
HOLD = (("pts", 9.0), .20, 1.0)  # tout conserver

logit = lambda p: np.log(p / (1 - p))
expit = lambda z: 1 / (1 + np.exp(-z))
clip = lambda p: np.clip(p, .001, .999)


def label(prm):
    (kind, s), f, k = prm
    if k >= 1:
        return "tout conserver"
    pal = f"+{s * 100:g} pts" if kind == "pts" else f"+{s * 100:g} % rel."
    return f"{pal}, vendre {f * 100:g} %, garder {k * 100:g} %"


def us_paths():
    t0 = US_VOTE - dt.timedelta(days=J)
    ev = json.load(open(US_DIR / "_event.json"))
    end = dt.datetime.fromtimestamp(ev["end"], dt.UTC).date() - dt.timedelta(days=1)
    days = [t0 + dt.timedelta(days=i) for i in range((end - t0).days + 1)]
    files = {m["name"]: m["file"] for m in ev["markets"]}
    Z = {}
    for k, n in US_NAMES.items():
        d = daily(json.load(open(US_DIR / files[n]))["yes"])
        last, row = None, []
        for day in days:  # dernier prix connu (report)
            last = d.get(day, last if last is not None else d[min(d, key=lambda x: abs((x - day).days))])
            row.append(last)
        Z[k] = logit(clip(np.array(row)))
    pk = int(np.argmax(Z["Harris"]))
    hw = Z["Harris"].copy()
    hw[pk:] = Z["Harris"][pk] + Z["Trump"][pk:] - Z["Trump"][pk]
    Z["HarrisWin"] = hw
    return days, Z


def variants(Z, keys, rng):
    """Variante 0 = réel ; autres = permutation des blocs de variations journalières."""
    T = len(next(iter(Z.values())))
    inc = {k: np.diff(Z[k]) for k in keys}
    blocks = [np.arange(i, min(i + BLOC, T - 1)) for i in range(0, T - 1, BLOC)]
    out = {k: np.empty((N_VAR, T)) for k in keys}
    for v in range(N_VAR):
        order = np.concatenate([blocks[i] for i in (range(len(blocks)) if v == 0 else rng.permutation(len(blocks)))])
        for k in keys:
            out[k][v] = Z[k][0] + np.concatenate([[0], np.cumsum(inc[k][order])])
    return out


def run(px, p0, prm):
    """Ventes par paliers, vectorisé sur les variantes. Mise = 1 $. Renvoie (encaissé, jetons, nb ventes)."""
    (kind, step), frac, keep = prm
    V, T = px.shape
    q0 = 1.0 / p0
    q, cash, ref, n = np.full(V, q0), np.zeros(V), np.full(V, p0), np.zeros(V)
    for t in range(1, T):
        pr = px[:, t]
        for _ in range(12):
            nxt = ref * (1 + step) if kind == "rel" else ref + step
            can = (q > keep * q0 + 1e-12) & (pr >= nxt)
            if not can.any():
                break
            sq = np.where(can, np.minimum(frac * q0, q - keep * q0), 0)
            q, cash, n = q - sq, cash + sq * pr, n + can
            ref = np.where(can, nxt, ref)
    return cash, q, n


def main(perte_max=PERTE_MAX):
    rng = np.random.default_rng(SEED)
    days, Z = us_paths()
    probs = np.array([s[1] for s in SCEN])
    pmkt = np.array(MARCHE)
    # valeur finale par $ misé : R[pos][prm] = array (scénarios, variantes)
    R = {k: {} for k, *_ in POS}
    NS = {k: {} for k, *_ in POS}
    for si, (sn, pr, mp, win) in enumerate(SCEN):
        P = variants(Z, set(mp.values()), rng)
        for k, _, side, pe, y0 in POS:
            y = expit(logit(y0) + P[mp[k]] - P[mp[k]][:, :1])
            tok = y if side == "Y" else 1 - y
            tok[:, 0] = pe
            settle = float((win == k) if side == "Y" else (win != k))
            for prm in GRID + LADDER + [BASE[side], HOLD]:
                cash, q, n = run(tok, pe, prm)
                R[k].setdefault(prm, np.zeros((len(SCEN), N_VAR)))[si] = cash + q * settle
                NS[k].setdefault(prm, np.zeros((len(SCEN), N_VAR)))[si] = n
    LPi = 0  # index du scénario « Le Pen gagne »

    def stats(k, prm):
        m = R[k][prm].mean(axis=1)
        # espérance /$ (probas de Nick), valeur moyenne si Le Pen gagne /$ (additive → contrainte exacte)
        return float(probs @ m), float(m[LPi])

    # Tableau par position : top 5 de l'échelle fine + meilleur gros palier + règle actuelle + tout conserver
    tables = {}
    for k, name, side, pe, _ in POS:
        rows = sorted(LADDER, key=lambda p: -stats(k, p)[0])
        pick = rows[:5] + [max(GRID, key=lambda p: stats(k, p)[0]), BASE[side], HOLD]
        tables[name] = [dict(regle=label(p), e=round(250 * (stats(k, p)[0] - 1), 1),
                             em=round(250 * (float(pmkt @ R[k][p].mean(axis=1)) - 1), 1),
                             lp=round(250 * (stats(k, p)[1] - 1), 1),
                             ventes=round(float(NS[k][p].mean()), 1),
                             tag="actuelle" if p == BASE[side] else "conserver" if p == HOLD
                             else "gros palier" if p not in LADDER else "")
                        for p in pick]

    # Frontière (espérance, valeur si Le Pen gagne) par position
    # Pire cas = scénario de proba > 0 le plus défavorable, mesuré prudemment : moyenne des 20 %
    # pires variantes (ES20), sommée position par position (sous-additive → borne pessimiste).
    # Par position, on ne garde que les réglages non dominés sur (espérance, ES20 par scénario).
    act = [si for si, sc in enumerate(SCEN) if sc[1] > 0]

    def es(x, q=ES_Q):
        return np.sort(x, axis=-1)[..., : max(1, int(q * x.shape[-1]))].mean(axis=-1)

    def vec(k, p):
        m = R[k][p].mean(axis=1)
        return np.round(np.concatenate([[probs @ m], es(R[k][p][act])]) - 1, 5)

    def frontier(k, grid):
        pts, seen = [], set()
        for p in grid:
            v = vec(k, p)
            if tuple(v) not in seen:
                seen.add(tuple(v))
                pts.append((p, v))
        return [a for a in pts if not any((b[1] >= a[1]).all() and (b[1] > a[1]).any() for b in pts)]

    def build(grid):
        F = [frontier(k, grid) for k, *_ in POS]
        return F, [np.array([v for _, v in f]) for f in F]  # (n_i, 1 + nb scénarios)

    FV_all, FV_lad = build(GRID + [HOLD]), build(LADDER)
    allocs = [a for a in itertools.product(STAKES, repeat=4) if sum(a) == BUDGET]

    def optimise(alloc_list, keep_front=False, limit=perte_max, FV=None):
        F, V = FV or FV_all
        best, safest, front = None, None, {}
        n1, n3 = len(F[1]), len(F[3])
        for a in alloc_list:
            A = [a[i] * V[i] for i in range(4)]
            P12 = (A[0][:, None, :] + A[1][None, :, :]).reshape(-1, V[0].shape[1])
            P34 = (A[2][:, None, :] + A[3][None, :, :]).reshape(-1, V[0].shape[1])
            E = P12[:, 0][:, None] + P34[:, 0][None, :]
            W = np.full(E.shape, np.inf)
            for c in range(1, V[0].shape[1]):
                np.minimum(W, P12[:, c][:, None] + P34[:, c][None, :], out=W)
            ok = np.where(W >= -limit, E, -np.inf)
            idx = [np.unravel_index(np.argmax(ok), ok.shape), np.unravel_index(np.argmax(W + 1e-6 * E), W.shape)]
            for tag, (i, j) in zip(("best", "safe"), idx):
                cand = (float(E[i, j]), float(W[i, j]),
                        [F[0][i // n1][0], F[1][i % n1][0], F[2][j // n3][0], F[3][j % n3][0]], list(a))
                if tag == "best" and np.isfinite(ok[i, j]) and (best is None or cand[0] > best[0]):
                    best = cand
                if tag == "safe" and (safest is None or cand[1] > safest[1]):
                    safest = cand
            if keep_front:  # enveloppe : meilleure espérance par niveau de pire cas (pas de 5 $)
                b = np.round(W.ravel() / 5) * 5
                o = np.lexsort((-E.ravel(), b))
                bu, first = np.unique(b[o], return_index=True)
                for L_, E_ in zip(bu.tolist(), E.ravel()[o][first].tolist()):
                    front[L_] = max(front.get(L_, -1e9), E_)
        if best is None:
            print(f"  contrainte {limit:.0f} $ impossible : pire cas minimal {-safest[1]:.0f} $")
        return best or safest, front, safest

    best_fix, _, safe_fix = optimise([(250, 250, 250, 250)])
    best_alloc, front_all, safe_alloc = optimise(allocs, keep_front=True)
    best_eq, _, _ = optimise(allocs, limit=PERTE_EQ)
    lad_fix, _, _ = optimise([(250, 250, 250, 250)], limit=1e9, FV=FV_lad)
    lad_eq, front_lad, _ = optimise(allocs, keep_front=True, limit=PERTE_EQ, FV=FV_lad)
    print(f"contrainte : pire scénario ≥ -{perte_max:.0f} $ | répartitions {len(allocs)}")

    def profile(prms, alloc):
        out = []
        for si, (sn, pr, *_r) in enumerate(SCEN):
            tot = sum(a * R[k][p][si] for (k, *_x), p, a in zip(POS, prms, alloc))
            per = {name: round(float(a * R[k][p][si].mean() - a), 0) for (k, name, *_x), p, a in zip(POS, prms, alloc)}
            out.append(dict(scen=sn, prob=pr, pm=MARCHE[si], reel=round(float(tot[0]) - BUDGET, 0),
                            moy=round(float(tot.mean()) - BUDGET, 0),
                            p10=round(float(np.percentile(tot, 10)) - BUDGET, 0), per=per))
        E = sum(r["prob"] * r["moy"] for r in out)
        Em = sum(r["pm"] * r["moy"] for r in out)
        tot_es = min(float(sum(a * es(R[k][p][si]) for (k, *_x), p, a in zip(POS, prms, alloc))) - BUDGET
                     for si in act)
        return dict(rows=out, E=round(E, 0), Em=round(Em, 0), pire=round(tot_es, 0),
                    pire_reel=min(r["reel"] for r, sc in zip(out, SCEN) if sc[1] > 0))

    strategies = {
        "Ta règle actuelle (250 $ × 4)": ([BASE[s] for _, _, s, *_ in POS], [250] * 4),
        "Tout conserver (250 $ × 4)": ([HOLD] * 4, [250] * 4),
        f"Équilibrée précédente, gros paliers (pire cas ≤ {PERTE_EQ:.0f} $)": (best_eq[2], best_eq[3]),
        "Échelle fine, 250 $ × 4": (lad_fix[2], [250] * 4),
        f"Échelle fine, répartition libre (pire cas ≤ {PERTE_EQ:.0f} $)": (lad_eq[2], lad_eq[3]),
        "Pire cas minimal, gros paliers": (safe_alloc[2], safe_alloc[3]),
    }

    # Lisnard : part de la montée capturée (pic puis retour) contre ce qui est sacrifié s'il gagne
    ki = [k for k, *_ in POS].index("LI")
    s_pic = next(i for i, sc in enumerate(SCEN) if sc[2]["LI"] == "Harris")
    s_win = next(i for i, sc in enumerate(SCEN) if sc[3] == "LI")
    hold_win = float(R["LI"][HOLD][s_win].mean())
    P = variants(Z, {"Harris"}, np.random.default_rng(SEED + 1))  # pic potentiel (vente totale au plus haut)
    y_pk = expit(logit(POS[ki][4]) + P["Harris"] - P["Harris"][:, :1]).max(axis=1)
    pic = float((y_pk / POS[ki][3]).mean())
    lis = []
    for prm in sorted(LADDER, key=lambda p: -stats("LI", p)[0])[:8] + [lad_eq[2][ki], best_eq[2][ki], BASE["Y"], HOLD]:
        cap, win = float(R["LI"][prm][s_pic].mean()), float(R["LI"][prm][s_win].mean())
        row = dict(regle=label(prm), cap=round(250 * (cap - 1)), cap_pct=round(100 * (cap - 1) / (pic - 1)),
                   win=round(250 * (win - 1)), sac=round(250 * (hold_win - win)),
                   sac_pct=round(100 * (hold_win - win) / (hold_win - 1)), ventes=round(float(NS["LI"][prm][s_pic].mean()), 1),
                   tag="échelle retenue" if prm == lad_eq[2][ki] else "gros palier" if prm == best_eq[2][ki]
                   else "actuelle" if prm == BASE["Y"] else "conserver" if prm == HOLD else "")
        if row not in lis:
            lis.append(row)
    lis_pic = round(250 * (pic - 1))
    res = {n: dict(regles={name: label(p) for (_, name, *_x), p in zip(POS, prms)},
                   alloc={name: a for (_, name, *_x), a in zip(POS, alloc)}, **profile(prms, alloc))
           for n, (prms, alloc) in strategies.items()}
    # frontière globale (250 $ × 4) : meilleure espérance pour chaque niveau de perte si Le Pen gagne
    env, bestE = [], -1e9
    for L, E in sorted(front_all.items(), key=lambda x: -x[0]):
        if E > bestE:
            env.append([round(L, 1), round(E, 1)])
            bestE = E
    env_lad, bestE = [], -1e9
    for L, E in sorted(front_lad.items(), key=lambda x: -x[0]):
        if E > bestE:
            env_lad.append([round(L, 1), round(E, 1)])
            bestE = E
    data = dict(J=J, perte_max=perte_max, perte_eq=PERTE_EQ, n_var=N_VAR, es_q=ES_Q,
                scen=[dict(n=s[0], p=s[1], pm=pm, map={name: s[2][k] for k, name, *_x in POS},
                     win=next((name for k, name, *_x in POS if k == s[3]), None))
                      for s, pm in zip(SCEN, MARCHE)],
                pos=[dict(name=n, side=sd, pe=pe) for _, n, sd, pe, _ in POS],
                tables=tables, res=res, frontier=env, frontier_lad=env_lad, lis=lis, lis_pic=lis_pic)
    OUT.mkdir(exist_ok=True)
    json.dump(data, open(OUT / "strategie_paliers.json", "w"), ensure_ascii=False)
    tpl = (HERE / "strategie_paliers_page.html").read_text()
    (OUT / "strategie_paliers.html").write_text(tpl.replace("__DATA__", json.dumps(data, ensure_ascii=False)))

    for name, rows in tables.items():
        print(f"\n{name} (gain moyen pondéré / si Le Pen gagne, pour 250 $)")
        for r in rows:
            print(f"  {r['regle']:42} E {r['e']:+7.1f}  Emkt {r['em']:+7.1f}  LP {r['lp']:+7.1f}  ventes {r['ventes']:4.1f} {r['tag']}")
    print(f"\nLisnard — vente totale au pic (Harris) : +{lis_pic} $ ; tout conserver s'il gagne : +{round(250 * (hold_win - 1))} $")
    for x in lis:
        print(f"  {x['regle']:42} pic capturé {x['cap']:+6} $ ({x['cap_pct']:3} %)  s'il gagne {x['win']:+6} $ "
              f"(sacrifié {x['sac']:5} $, {x['sac_pct']} %)  ventes {x['ventes']} {x['tag']}")
    for n, r in res.items():
        print(f"\n== {n} — espérance Nick {r['E']:+.0f} $ / marché {r['Em']:+.0f} $ — pire cas {r['pire']:+.0f} $ — {r['alloc']}")
        for name, rg in r["regles"].items():
            print(f"   {name:10} {rg}")
        for x in r["rows"]:
            print(f"   {x['scen']:38} réel {x['reel']:+6.0f}  moyenne {x['moy']:+6.0f}  P10 {x['p10']:+6.0f}")


if __name__ == "__main__":
    a = sys.argv[1:]
    main(float(a[a.index("--perte-max") + 1]) if "--perte-max" in a else PERTE_MAX)
