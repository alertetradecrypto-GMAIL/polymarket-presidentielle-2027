from datetime import datetime
from zoneinfo import ZoneInfo

import daily
import polymarket as pm

TZ = ZoneInfo("Europe/Paris")
DAY = 86400


def ts(y, mo, d, h, mi=0):
    return int(datetime(y, mo, d, h, mi, tzinfo=TZ).timestamp())


# ---------------------------------------------------------------- créneau

def test_should_send_ete_hiver():
    # Été (CEST) : 06:00 UTC = 8h Paris ; hiver (CET) : 07:00 UTC = 8h Paris
    assert daily.should_send(ts(2026, 10, 8, 8, 3), {})[0]
    assert not daily.should_send(ts(2026, 10, 30, 7, 2), {})[0]   # 06:02 UTC en hiver
    assert daily.should_send(ts(2026, 10, 30, 8, 2), {})[0]


def test_should_send_retard_et_doublon():
    assert daily.should_send(ts(2026, 10, 8, 9, 40), {})[0]          # cron en retard
    assert not daily.should_send(ts(2026, 10, 8, 12, 5), {})[0]
    ok, _ = daily.should_send(ts(2026, 10, 8, 9, 0), {"last_sent": "2026-10-08"})
    assert not ok


# ---------------------------------------------------------------- calculs

CANDS = [
    {"slug": "le-pen", "name": "Marine Le Pen", "condition_id": "0xAA", "price": 0.43,
     "active": True, "volume24h": 1000},
    {"slug": "attal", "name": "Gabriel Attal", "condition_id": "0xBB", "price": 0.03,
     "active": True},
    {"slug": "petit", "name": "Petit", "condition_id": "0xCC", "price": 0.01, "active": True},
    {"slug": "detenu", "name": "Détenu", "condition_id": "0xDD", "price": 0.005, "active": True},
]
NOW = ts(2026, 10, 8, 8)


def histories():
    return {
        "le-pen": [[NOW - DAY, 0.40], [NOW, 0.43]],
        "attal": [[NOW - DAY, 0.04], [NOW, 0.03]],
        "petit": [[NOW, 0.01]],            # pas de point il y a 24 h
        "detenu": [[NOW - DAY, 0.005], [NOW, 0.005]],
    }


RAW = [
    {"conditionId": "0xaa", "outcome": "Yes", "size": 100, "avgPrice": 0.35,
     "curPrice": 0.43, "initialValue": 35, "currentValue": 43},
    {"conditionId": "0xBB", "outcome": "No", "size": 50, "avgPrice": 0.95, "curPrice": 0.97},
    {"conditionId": "0xDD", "outcome": "Yes", "size": 10, "avgPrice": 0.01, "curPrice": 0.005},
    {"conditionId": "0xZZ", "eventSlug": "autre-marche", "outcome": "Yes", "size": 5,
     "avgPrice": 0.5, "curPrice": 0.5},
    {"conditionId": "0xAA", "outcome": "No", "size": 0, "avgPrice": 0.5, "curPrice": 0.57},
]


def test_market_positions_filtre_et_calcule():
    pos = daily.market_positions(RAW, CANDS)
    assert [p["slug"] for p in pos] == ["le-pen", "attal", "detenu"]   # autre marché et taille 0 exclus
    lp, at = pos[0], pos[1]
    assert lp["pnl"] == 8 and lp["side"] == "Oui"
    assert at["side"] == "Non" and abs(at["cost"] - 47.5) < 1e-9 and abs(at["value"] - 48.5) < 1e-9


def test_summarize_pnl_24h_cote_non():
    moves = daily.candidate_moves(CANDS, histories(), NOW)
    accs = [{"label": "A1", "short": "#1", "cash": 100.0,
             "positions": daily.market_positions(RAW[:2], CANDS)},
            {"label": "A2", "short": "#2", "cash": None, "positions": []}]
    total = daily.summarize(accs, moves)
    # Le Pen Oui : +3 pt × 100 = +3 ; Attal Non : Oui −1 pt → Non +1 pt × 50 = +0,5
    assert abs(total["pnl24"] - 3.5) < 1e-9
    assert abs(total["equity"] - (43 + 48.5 + 100)) < 1e-9
    assert total["cash_known"] is False


def test_listed_candidates_seuil_et_detenus():
    moves = daily.candidate_moves(CANDS, histories(), NOW)
    assert moves["petit"]["delta"] is None
    slugs = [m["slug"] for m in daily.listed_candidates(moves, {"detenu"})]
    assert slugs == ["le-pen", "attal", "detenu"]          # « petit » (1 %) exclu


def test_format():
    assert daily.usd(1234.5) == "1 234,50 $"
    assert daily.usd(-3.2, True) == "−3,20 $"
    assert daily.usd(3.2, True) == "+3,20 $"
    assert daily.px(0.1234) == "0,123 $" and daily.px(None) == "—"
    assert daily.px(-0.01, True) == "−0,010 $"
    assert daily.cote(0.4) == "2,50" and daily.cote(0) == "—"


# ---------------------------------------------------------------- run

def _loader():
    return ([{"label": "Adresse 1", "short": "#1", "raw": RAW, "cash": 12.0, "positions": []},
             {"label": "Adresse 2", "short": "#2", "raw": [], "cash": 0.0, "positions": []}], [])


def _setup(tmp_path, monkeypatch):
    import collect
    hist_dir = tmp_path / "history"
    for slug, pts in histories().items():
        collect.write_json(hist_dir / f"{slug}.json", {"p": pts})
    collect.write_json(tmp_path / "candidates.json", {"updated": NOW, "candidates": CANDS})
    monkeypatch.setattr(daily, "HISTORY_DIR", hist_dir)
    monkeypatch.setattr(daily, "CANDIDATES_FILE", tmp_path / "candidates.json")
    monkeypatch.setattr(daily, "DAILY_STATE_FILE", tmp_path / "daily.json")


def test_run_envoie_une_fois(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    sent = []
    send = lambda s, b, t: sent.append((s, b))
    assert daily.run(NOW, send=send, loader=_loader) == 0
    assert daily.run(NOW + 3600, send=send, loader=_loader) == 0   # 2e cron : rien
    assert len(sent) == 1
    subject, body = sent[0]
    assert "08/10/2026" in subject and "Marine Le Pen" in body and "Petit<" not in body
    assert " pt<" not in body and "Cote Oui" in body
    assert (tmp_path / "daily.json").read_text().count("2026-10-08") == 1


def test_run_echec_email_reessaie(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    def boom(*a):
        raise OSError("smtp")
    assert daily.run(NOW, send=boom, loader=_loader) == 1
    assert not (tmp_path / "daily.json").exists()


def test_run_force_hors_creneau_ne_bloque_pas(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    sent = []
    daily.run(NOW - 5 * 3600, force=True, send=lambda *a: sent.append(1), loader=_loader)
    assert sent and not (tmp_path / "daily.json").exists()


# ---------------------------------------------------------------- cash

def test_balance_of_call():
    call = pm.balance_of_call("0xTOKEN", "0xAbC")
    assert call["data"] == "0x70a08231" + "0" * 61 + "abc"


def test_get_cash_repli_rpc(monkeypatch):
    calls = []

    class R:
        def __init__(self, payload, fail=False):
            self.payload, self.fail = payload, fail

        def raise_for_status(self):
            if self.fail:
                raise OSError("down")

        def json(self):
            return self.payload

    def post(url, json, timeout):
        calls.append(url)
        if len(calls) == 1:
            return R(None, fail=True)
        return R([{"id": 0, "result": hex(12_500_000)}, {"id": 1, "result": "0x0"}])

    monkeypatch.setattr(pm.SESSION, "post", post)
    monkeypatch.delenv("POLYGON_RPC_URL", raising=False)
    assert pm.get_cash("0x" + "1" * 40) == 12.5
    assert len(calls) == 2
