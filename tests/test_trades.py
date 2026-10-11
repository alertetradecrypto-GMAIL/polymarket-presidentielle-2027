import csv
import io
import json

import pytest

import polymarket as pm
import trades
import trades_daily

# 2026-10-11 12:00 heure de Paris (UTC+2)
NOON = 1_791_712_800
DAY = 86400


@pytest.fixture
def env(tmp_path, monkeypatch):
    tdir = tmp_path / "trades"
    monkeypatch.setattr(trades, "TRADES_DIR", tdir)
    monkeypatch.setattr(trades, "TRADES_INDEX_FILE", tdir / "index.json")
    monkeypatch.setattr(trades, "TRADES_STATE_FILE", tmp_path / "state" / "trades.json")
    cands = tmp_path / "candidates.json"
    cands.write_text(json.dumps({"candidates": [
        {"slug": "a", "name": "Alice", "condition_id": "ca", "active": True, "volume24h": 5},
        {"slug": "b", "name": "Bob", "condition_id": "cb", "active": True, "volume24h": 9},
    ]}))
    monkeypatch.setattr(trades, "CANDIDATES_FILE", cands)
    monkeypatch.setattr(trades_daily, "CANDIDATES_FILE", cands)
    monkeypatch.setattr(trades_daily, "TRADES_DAILY_STATE_FILE", tmp_path / "state" / "td.json")
    store = {"ca": [], "cb": []}
    calls = []

    def fake_rows(cond, start, end):
        calls.append((cond, start, end))
        return [r for r in store[cond]
                if (start is None or r["timestamp"] >= start) and r["timestamp"] <= end]

    monkeypatch.setattr(pm, "iter_trade_rows", fake_rows)
    return {"dir": tdir, "store": store, "calls": calls, "tmp": tmp_path}


def raw(t, size=100.0, price=0.5, side="BUY", out="Yes", wallet="0xAbC", tx="0x1"):
    return {"timestamp": t, "size": size, "price": price, "side": side, "outcome": out,
            "proxyWallet": wallet, "name": "", "transactionHash": tx}


def rows_of(env, day):
    return json.loads((env["dir"] / f"{day}.json").read_text())["rows"]


def test_backfill_then_incremental_without_duplicates(env):
    env["store"]["ca"] = [raw(NOON - DAY, tx="0xold"), raw(NOON - 60, tx="0x2")]
    env["store"]["cb"] = [raw(NOON - 30, tx="0x3")]
    assert trades.run(now=NOON) == 0
    assert len(rows_of(env, "2026-10-10")) == 1
    today = rows_of(env, "2026-10-11")
    assert [r[9] for r in today] == ["0x3", "0x2"]            # plus récent d'abord
    assert today[0][7] == "0xabc"                              # adresse complète, minuscules
    assert today[0][6] == 50.0                                 # montant $

    # Nouveau trade + trade indexé en retard dans la fenêtre de recouvrement
    env["store"]["ca"].append(raw(NOON + 600, tx="0x4"))
    env["store"]["ca"].append(raw(NOON - 120, tx="0xlate"))
    trades.run(now=NOON + 900)
    assert env["calls"][-1][1] is not None                    # pas de nouveau rattrapage complet
    today = rows_of(env, "2026-10-11")
    assert sorted(r[9] for r in today) == ["0x2", "0x3", "0x4", "0xlate"]
    idx = json.loads((env["dir"] / "index.json").read_text())
    assert idx["days"]["2026-10-11"] == [4, 200.0]


def test_backfill_budget_and_pending(env):
    env["store"]["ca"] = [raw(NOON - 60)]
    env["store"]["cb"] = [raw(NOON - 30, tx="0x9")]
    trades.run(now=NOON, budget=1)
    assert trades.pending_backfills() == ["Alice"]            # Bob (plus actif) d'abord
    trades.run(now=NOON + 900, budget=1)
    assert trades.pending_backfills() == []


def test_closed_candidate_not_refetched(env, tmp_path):
    env["store"]["ca"] = [raw(NOON - 60)]
    trades.run(now=NOON)
    data = json.loads(trades.CANDIDATES_FILE.read_text())
    data["candidates"][0]["active"] = False
    trades.CANDIDATES_FILE.write_text(json.dumps(data))
    n = len(env["calls"])
    trades.run(now=NOON + 900)
    assert all(c[0] != "ca" for c in env["calls"][n:])


def test_daily_email_summary_top_and_csv(env):
    yesterday = NOON - DAY
    env["store"]["ca"] = [
        raw(yesterday, size=1000, price=0.2, side="BUY", tx="0xa"),          # 200 $
        raw(yesterday + 10, size=100, price=0.3, side="SELL", tx="0xb"),     # 30 $
        raw(yesterday + 20, size=1.1, price=0.999, out="No", tx="0xc"),      # bruit
    ]
    trades.run(now=NOON)
    sent = []
    now = NOON - 4 * 3600   # 8h00 à Paris
    rc = trades_daily.run(now=now, send=lambda *a, **k: sent.append((a, k)))
    assert rc == 0
    (subject, body, text), kw = sent[0]
    assert subject == "TRADE JOURNALIER POLYMARKET – 10/10/2026"
    assert "Alice" in body and "0xabc" in body
    name, data, *_ = kw["attachments"][0]
    assert name == "trades_2026-10-10.csv"
    lines = list(csv.reader(io.StringIO(data.decode("utf-8-sig")), delimiter=";"))
    assert len(lines) == 4                                    # en-tête + 3 trades (bruit inclus)

    s = trades_daily.summarize(trades_daily.as_dicts(trades.load_day("2026-10-10")))
    assert s[0]["n"] == 2 and s[0]["net_yes"] == pytest.approx(170.0)

    # Une seule fois par jour
    trades_daily.run(now=now + 3600, send=lambda *a, **k: sent.append((a, k)))
    assert len(sent) == 1


def test_daily_email_outside_window(env):
    sent = []
    trades_daily.run(now=NOON + 6 * 3600, send=lambda *a, **k: sent.append(a))
    assert sent == []


def test_backfill_resumes_in_chunks_without_gap(env):
    # 1 200 trades, dont des paquets dans la même seconde ; délai dépassé à chaque morceau
    env["store"]["ca"] = sorted((raw(NOON - 5000 + i // 3, tx=f"0x{i}") for i in range(1200)),
                                key=lambda r: -r["timestamp"])
    hi, chunks, total = NOON, 0, []
    while True:
        rows, stop = trades.backfill("a", "ca", hi, deadline=0)
        trades.merge_candidate("a", None if stop is None else stop + 1, hi, rows)
        chunks += 1
        if stop is None:
            break
        hi = stop
    assert chunks > 1
    got = [r[9] for d in trades.existing_days() for r in trades.load_day(d)]
    assert sorted(got) == sorted(f"0x{i}" for i in range(1200))
