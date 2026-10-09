"""Valeur théorique d'un candidat à partir des sondages (stratégie 3).

P(victoire) = P(qualifié au 2nd tour) × P(gagne son duel), par Monte-Carlo :
- 1er tour : moyenne des 5 derniers sondages + bruit normal (SIGMA_1T points) ;
- 2nd tour : P(A bat RN) = Φ((score moyen de A − 50) / SIGMA_2T) ;
  duels sans sondage = hypothèses fixes (DEFAULT_DUELS).
Le camp RN est modélisé d'un bloc ; son partage Le Pen / Bardella vient du marché
(les sondages ne disent rien de l'éligibilité de Le Pen).
"""
from __future__ import annotations

import csv
import math
from datetime import date
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
N_POLLS = 5          # moyenne glissante des 5 derniers sondages
SIGMA_1T = 3.0       # incertitude 1er tour (points), variante « fixe »
SIGMA_2T = 4.0       # incertitude 2nd tour (points), variante « fixe »
ELECTION = date(2027, 4, 11)  # 1er tour (date indicative)
N_SIMS = 20_000
PUBLICATION_LAG_DAYS = 1

# Candidats non suivis par les sondages relevés : niveaux 1er tour fixes (hypothèses).
OTHERS_1T = {"glucksmann": 11.0, "attal": 8.0, "retailleau": 7.0,
             "zemmour": 3.5, "lisnard": 1.5}
# Duels face au RN sans sondage : on reprend un duel proche.
DUEL_PROXY = {"lisnard": "retailleau"}
# Duels sans RN : probabilité que le 1er nommé gagne (hypothèses).
DEFAULT_DUELS = {("philippe", "melenchon"): 0.85}
DEFAULT_VS_MELENCHON = 0.75   # tout candidat non-RN face à Mélenchon
DEFAULT_PHILIPPE_VS = 0.60    # Philippe face aux autres non-RN


def _rows(name: str) -> list[dict]:
    with open(HERE / name, encoding="utf-8") as f:
        return list(csv.DictReader(line for line in f if not line.startswith("#")))


def load_polls():
    p1 = [{**r, "end": date.fromisoformat(r["end_date"])} for r in _rows("polls_1t.csv")]
    p2 = [{**r, "end": date.fromisoformat(r["end_date"])} for r in _rows("polls_2t.csv")]
    return sorted(p1, key=lambda r: r["end"]), sorted(p2, key=lambda r: r["end"])


def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def poll_state(p1, p2, day: date) -> dict | None:
    """Moyennes disponibles au jour `day` (avec délai de publication). None si aucun sondage."""
    known1 = [r for r in p1 if (day - r["end"]).days >= PUBLICATION_LAG_DAYS]
    if not known1:
        return None
    last = known1[-N_POLLS:]
    state = {k: round(sum(float(r[k]) for r in last) / len(last), 2)
             for k in ("rn", "philippe", "melenchon")}
    duels = {}
    known2 = [r for r in p2 if (day - r["end"]).days >= PUBLICATION_LAG_DAYS]
    for ch in {r["challenger"] for r in known2}:
        rows = [r for r in known2 if r["challenger"] == ch][-N_POLLS:]
        duels[ch] = round(sum(float(r["a_pct"]) for r in rows) / len(rows), 2)
    state["duels"] = duels
    return state


def sigmas(day: date, widen: bool) -> tuple[float, float]:
    """Variante « élargie » : l'incertitude croît avec le temps restant
    (≈ 6 pts au 1er tour à 7 mois, 3 pts la veille)."""
    if not widen:
        return SIGMA_1T, SIGMA_2T
    months = max((ELECTION - day).days / 30.4, 0.0)
    return 2.0 + 0.6 * months, 3.0 + 0.6 * months


def _p_beats(a: str, b: str, duels: dict, s2: float = SIGMA_2T) -> float:
    """Probabilité que a batte b au 2nd tour."""
    if b == "rn":
        ch = DUEL_PROXY.get(a, a)
        pct = duels.get(ch, 45.0)
        return _phi((pct - 50) / s2)
    if a == "rn":
        return 1 - _p_beats(b, a, duels, s2)
    if (a, b) in DEFAULT_DUELS:
        return DEFAULT_DUELS[(a, b)]
    if (b, a) in DEFAULT_DUELS:
        return 1 - DEFAULT_DUELS[(b, a)]
    if b == "melenchon":
        return DEFAULT_VS_MELENCHON
    if a == "melenchon":
        return 1 - DEFAULT_VS_MELENCHON
    if a == "philippe":
        return DEFAULT_PHILIPPE_VS
    if b == "philippe":
        return 1 - DEFAULT_PHILIPPE_VS
    return 0.5


def win_probs(state: dict, s1: float = SIGMA_1T, s2: float = SIGMA_2T,
              seed: int = 0) -> dict[str, float]:
    """P(victoire) par candidat / camp (clé 'rn' pour le camp RN)."""
    names = ["rn", "philippe", "melenchon", *OTHERS_1T]
    mu = np.array([state["rn"], state["philippe"], state["melenchon"],
                   *OTHERS_1T.values()])
    rng = np.random.default_rng(seed)
    shares = mu + rng.normal(0, s1, (N_SIMS, len(names)))
    top2 = np.argsort(-shares, axis=1)[:, :2]
    pair_p = np.array([[_p_beats(names[i], names[j], state["duels"], s2) if i != j else 0.0
                        for j in range(len(names))] for i in range(len(names))])
    a, b = top2[:, 0], top2[:, 1]
    a_wins = rng.random(N_SIMS) < pair_p[a, b]
    winners = np.where(a_wins, a, b)
    counts = np.bincount(winners, minlength=len(names)) / N_SIMS
    return dict(zip(names, counts.tolist()))
