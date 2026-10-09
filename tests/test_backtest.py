import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backtest"))

import model  # noqa: E402
import run  # noqa: E402


def test_poll_state_respecte_le_delai_de_publication():
    p1, p2 = model.load_polls()
    assert model.poll_state(p1, p2, date(2026, 4, 22)) is None
    assert model.poll_state(p1, p2, date(2026, 4, 23))["rn"] == 32.5


def test_win_probs_somme_a_un():
    p1, p2 = model.load_polls()
    w = model.win_probs(model.poll_state(p1, p2, date(2026, 9, 1)), 6, 7)
    assert abs(sum(w.values()) - 1) < 1e-9


def _book(mode):
    return run.Book(mode, 0.03, ("yes",))


def test_maker_attend_le_croisement():
    b = _book("maker_strict")
    run.step(b, 0, "x", "yes", 0.20, 0.30)          # signal → ordre à 0,195
    assert b.orders and not b.pos
    run.step(b, 3600, "x", "yes", 0.19, 0.30)       # touché mais pas croisé de 1¢
    assert not b.pos
    run.step(b, 7200, "x", "yes", 0.185, 0.30)      # croisé → exécuté à 0,195
    assert b.pos[("x", "yes")]["px"] == 0.195


def test_maker_annule_apres_48h():
    b = _book("maker_strict")
    run.step(b, 0, "x", "yes", 0.20, 0.30)
    run.step(b, 49 * 3600, "x", "yes", 0.20, 0.30)
    assert not b.orders and not b.pos


def test_taker_paie_spread_et_frais():
    b = _book("taker")
    run.step(b, 0, "x", "yes", 0.20, 0.30)
    px = b.pos[("x", "yes")]["px"]
    assert abs(px - (0.205 + 0.04 * 0.205 * 0.795)) < 1e-9
