import json

import collect
import polymarket as pm

NOW = 1_791_450_500


def market(mid, name, bid, ask, vol=1000.0, closed=False, enable=True):
    return {
        "id": mid, "groupItemTitle": name, "conditionId": f"0x{mid}",
        "outcomes": '["Yes", "No"]',
        "clobTokenIds": f'["{mid}111", "{mid}222"]',
        "bestBid": bid, "bestAsk": ask, "lastTradePrice": bid,
        "volumeNum": vol, "volume24hr": 10.0, "active": True,
        "closed": closed, "enableOrderBook": enable,
    }


EVENT = {
    "id": "79987", "slug": "next-french-presidential-election",
    "title": "Next French Presidential Election", "endDate": "2027-04-30T00:00:00Z",
    "markets": [
        market("1", "Marine Le Pen", 0.34, 0.35),
        market("2", "Édouard Philippe", 0.28, 0.29),
        market("3", "Person X", None, None, vol=0),          # placeholder exclu
        market("4", "Candidat Retiré", 0.0, 0.001, closed=True),
    ],
}


def setup(tmp_path, monkeypatch, history_fn):
    monkeypatch.setattr(collect, "HISTORY_DIR", tmp_path / "history")
    monkeypatch.setattr(collect, "CANDIDATES_FILE", tmp_path / "candidates.json")
    monkeypatch.setattr(collect.time, "time", lambda: NOW)
    monkeypatch.setattr(pm, "get_event", lambda: EVENT)
    calls = []

    def fake_hist(token, **kw):
        calls.append((token, kw))
        return history_fn(token, kw)

    monkeypatch.setattr(pm, "get_price_history", fake_hist)
    monkeypatch.setattr(pm, "get_trades", lambda *a, **kw: [])   # pas de réseau
    return calls


def test_slugify():
    assert collect.slugify("Édouard Philippe") == "edouard-philippe"
    assert collect.slugify("Jean-Luc Mélenchon") == "jean-luc-melenchon"


def test_display_price():
    assert pm.display_price(0.28, 0.29, 0.27) == 0.285
    assert pm.display_price(0.01, 0.30, 0.05) == 0.05  # spread > 10 c


def test_merge_points_bucket():
    pts = collect.merge_points([[900, 0.1]], [[1000, 0.2], [1799, 0.3], [1800, 0.4]])
    assert pts == [[1799, 0.3], [1800, 0.4]]


def test_compact_keeps_recent_fine():
    old = [[NOW - 40 * 86400 + i * 900, 0.1] for i in range(8)]   # 2 h à 15 min
    recent = [[NOW - i * 900, 0.2] for i in range(4)][::-1]
    out = collect.compact(old + recent, NOW)
    assert 2 <= len(out) - 4 <= 3  # 2 h de points fins → 2 ou 3 points horaires


def test_backfill_then_incremental(tmp_path, monkeypatch):
    def hist(token, kw):
        if kw.get("interval") == "max":
            assert kw["fidelity"] == 720
            return [(NOW - 200 * 86400 + h * 43200, 0.10) for h in range(10)]
        if kw.get("interval") == "1m":
            return [(NOW - 10 * 86400 + h * 3600, 0.20) for h in range(24)]
        if kw.get("interval") == "1w":
            return [(NOW - 3600 + i * 900, 0.30) for i in range(4)]
        return [(NOW, 0.31)]

    calls = setup(tmp_path, monkeypatch, hist)
    assert collect.run() == 0

    data = json.loads((tmp_path / "candidates.json").read_text())
    names = [c["name"] for c in data["candidates"]]
    assert names == ["Marine Le Pen", "Édouard Philippe", "Candidat Retiré"]
    assert data["candidates"][1]["price"] == 0.285
    assert data["candidates"][0]["token_yes"] == "1111"

    h = json.loads((tmp_path / "history" / "marine-le-pen.json").read_text())
    assert len(h["p"]) == 10 + 24 + 4 and h["vol"] == [] and h["vol_since"] == NOW
    assert not (tmp_path / "history" / "candidat-retire.json").read_text().count('"p":[[')

    # 2e passage : appel incrémental startTs/endTs
    calls.clear()
    collect.run()
    assert all("start_ts" in kw and kw["start_ts"] < NOW for _, kw in calls)


def test_one_failure_does_not_block(tmp_path, monkeypatch):
    def hist(token, kw):
        if token == "1111":
            raise RuntimeError("boom")
        return [(NOW, 0.3)]

    setup(tmp_path, monkeypatch, hist)
    assert collect.run() == 0
    assert (tmp_path / "history" / "edouard-philippe.json").exists()


def test_price_24h_for_dashboard(tmp_path, monkeypatch):
    def hist(token, kw):
        if kw.get("interval") == "1w":
            return [(NOW - 86400 - 600, 0.20), (NOW - 900, 0.24)]
        return []

    setup(tmp_path, monkeypatch, hist)
    collect.run()
    c = json.loads((tmp_path / "candidates.json").read_text())["candidates"][0]
    assert c["last_hist"] == 0.24
    assert c["price_24h"] == 0.20           # point à ±30 min de last_t − 24 h


def test_price_refs_1h_4h_12h(tmp_path, monkeypatch):
    last = NOW - 900
    pts = [(last - 12 * 3600 + 300, 0.10), (last - 4 * 3600 - 2400, 0.30),
           (last - 3600 - 600, 0.18), (last, 0.24)]

    def hist(token, kw):
        return pts if kw.get("interval") == "1w" else []

    setup(tmp_path, monkeypatch, hist)
    collect.run()
    c = json.loads((tmp_path / "candidates.json").read_text())["candidates"][0]
    assert c["price_ref"]["1h"] == 0.18      # à 10 min près (tolérance 15 min)
    assert c["price_ref"]["4h"] is None      # point à 40 min : hors tolérance (30 min)
    assert c["price_ref"]["12h"] == 0.10
    assert c["price_ref"]["24h"] is None
    assert c["price_24h"] is None
