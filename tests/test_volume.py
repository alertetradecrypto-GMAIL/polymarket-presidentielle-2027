import json

import collect
import polymarket as pm

NOW = 1_791_450_500


def trade(t, size=10.0, price=0.5):
    return {"timestamp": t, "size": size, "price": price}


def fake_api(monkeypatch, trades, page=3):
    """/v2/trades simulée : flux décroissant, pages par curseur, start/end ignorés."""
    monkeypatch.setattr(pm, "TRADES_PAGE", page)
    calls = []

    def _get(url, params=None):
        calls.append(dict(params))
        assert "offset" not in params and "start" not in params
        rows = sorted(trades, key=lambda t: -t["timestamp"])
        i = int(params.get("cursor") or 0)
        nxt = i + page if i + page < len(rows) else None
        return {"data": rows[i:i + page],
                "pagination": {"next_cursor": str(nxt) if nxt else None}}

    monkeypatch.setattr(pm, "_get", _get)
    return calls


def test_iter_trades_cursor_no_gap_no_dup(monkeypatch):
    trades = [trade(1000 + i // 3, size=i + 1) for i in range(40)]
    calls = fake_api(monkeypatch, trades)
    got = list(pm.iter_trades("c", None, 2000))
    assert sorted(s for _, s, _ in got) == [float(i + 1) for i in range(40)]
    assert len(calls) > 1                           # plusieurs pages


def test_iter_trades_respects_start(monkeypatch):
    fake_api(monkeypatch, [trade(t) for t in range(100, 130)])
    got = list(pm.iter_trades("c", 120, 200))
    assert sorted(t for t, _, _ in got) == list(range(120, 130))


def test_vol_buckets_and_compact():
    b = collect.vol_add({}, [(900, 10, 0.5), (1799, 2, 0.25), (1800, 4, 1.0)])
    assert collect.vol_series(b) == [[900, 12.0, 5.5], [1800, 4.0, 4.0]]
    old = NOW - 40 * 86400
    old -= old % 3600
    vol = [[old, 1.0, 0.5], [old + 900, 2.0, 1.0], [NOW - 900, 3.0, 3.0]]
    assert collect.compact_vol(vol, NOW) == [[old, 3.0, 1.5], [NOW - 900, 3.0, 3.0]]


def test_update_volume_backfill_then_incremental(monkeypatch):
    t0 = NOW - 3 * 86400
    trades = [trade(t0 + i * 600, size=100, price=0.2) for i in range(10)]
    fake_api(monkeypatch, trades, page=500)
    c = {"name": "X", "condition_id": "c", "active": True, "volume": 1000.0}
    hist = {}
    assert collect.update_volume(hist, c, NOW, True) is True
    assert hist["vol_since"] == t0 - t0 % 900
    assert sum(v[1] for v in hist["vol"]) == 1000.0
    assert round(sum(v[2] for v in hist["vol"]), 2) == 200.0

    # Nouveaux trades, dont un indexé en retard dans la dernière heure : pas de doublon
    trades += [trade(NOW - 1200, 50, 0.4), trade(NOW - 60, 50, 0.4)]
    assert collect.update_volume(hist, c, NOW, True) is False
    assert sum(v[1] for v in hist["vol"]) == 1100.0
    again = json.dumps(hist)
    collect.update_volume(hist, c, NOW, True)                # idempotent
    assert json.dumps(hist) == again
    assert collect.vol_totals(hist["vol"], NOW) == (240.0, 40.0)


def test_backfill_budget(tmp_path, monkeypatch):
    import test_collect as tc
    tc.setup(tmp_path, monkeypatch, lambda token, kw: [(NOW, 0.3)])
    monkeypatch.setattr(collect, "VOL_BACKFILL_PER_RUN", 1)
    collect.run(backfill_budget=1)
    data = json.loads((tmp_path / "candidates.json").read_text())
    done = [c for c in data["candidates"] if c["volume_usd"] is not None]
    assert len(done) == 1 and data["event"]["volume_usd"] is None
    collect.run(backfill_budget=None)
    data = json.loads((tmp_path / "candidates.json").read_text())
    assert data["event"]["volume_usd"] == 0


def test_get_positions_v2_mapped_to_v1(monkeypatch):
    row = {"condition_id": "0xab", "current_size": 300.0, "avg_price": 0.88,
           "current_price": 0.865, "current_value": 259.5, "entry_cost_usdc": 264.0,
           "event_slug": "next-french-presidential-election", "outcome": "No"}
    monkeypatch.setattr(pm, "_get", lambda url, params=None: {
        "data": [row], "pagination": {"next_cursor": None}})
    p, = pm.get_positions("0x1")
    assert (p["conditionId"], p["size"], p["avgPrice"], p["curPrice"],
            p["currentValue"], p["initialValue"]) == ("0xab", 300.0, 0.88, 0.865, 259.5, 264.0)
