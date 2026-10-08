import json

import news

NOW = 1_791_450_500  # jeu. 8 oct. 2026


def rss(*items):
    body = "".join(
        f"<item><title>{t}</title><link>{u}</link><pubDate>{d}</pubDate>"
        f'<source url="https://ex.fr">{s}</source></item>'
        for t, u, d, s in items
    )
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>{body}</channel></rss>'


FEED = rss(
    ("Ancien article - Le Monde", "https://n.g/1", "Mon, 05 Oct 2026 08:00:00 GMT", "Le Monde"),
    ("Dernier sondage - Les Échos", "https://n.g/2", "Thu, 08 Oct 2026 09:00:00 GMT", "Les Échos"),
    ("Dernier sondage - Ouest-France", "https://n.g/3", "Thu, 08 Oct 2026 07:00:00 GMT", "Ouest-France"),
    ("Trop vieux - X", "https://n.g/4", "Mon, 01 Jun 2026 08:00:00 GMT", "X"),
    ("Sans date", "https://n.g/5", "", "Y"),
    ("Mercredi - Libération", "https://n.g/6", "Wed, 07 Oct 2026 12:00:00 +0200", "Libération"),
)


def test_parse_rss_sorted_dedup_and_filtered():
    items = news.parse_rss(FEED, NOW)
    assert [i["title"] for i in items] == ["Dernier sondage", "Mercredi", "Ancien article"]
    assert items[0] == {"t": 1791450000, "title": "Dernier sondage", "source": "Les Échos",
                        "url": "https://n.g/2"}
    assert items[1]["t"] == 1791367200       # fuseau +0200 pris en compte


def test_parse_rss_limit():
    feed = rss(*[(f"A{i} - M", f"https://n.g/{i}", f"Thu, 08 Oct 2026 0{i}:00:00 GMT", "M")
                 for i in range(8)])
    items = news.parse_rss(feed, NOW)
    assert [i["title"] for i in items] == ["A7", "A6", "A5", "A4", "A3"]


def setup(tmp_path, monkeypatch, fetch):
    monkeypatch.setattr(news, "CANDIDATES_FILE", tmp_path / "candidates.json")
    monkeypatch.setattr(news, "NEWS_FILE", tmp_path / "news.json")
    monkeypatch.setattr(news.time, "time", lambda: NOW)
    monkeypatch.setattr(news.time, "sleep", lambda s: None)
    monkeypatch.setattr(news, "fetch_news", fetch)
    (tmp_path / "candidates.json").write_text(json.dumps({"candidates": [
        {"slug": "a", "name": "A", "active": True},
        {"slug": "b", "name": "B", "active": True},
        {"slug": "c", "name": "C", "active": False},
    ]}))


def read(tmp_path):
    return json.loads((tmp_path / "news.json").read_text())


def test_run_hourly_and_keeps_previous_on_error(tmp_path, monkeypatch):
    calls = []

    def fetch(name, now):
        calls.append(name)
        if name == "B":
            raise RuntimeError("503")
        return [{"t": now, "title": name, "source": "", "url": "https://x"}]

    setup(tmp_path, monkeypatch, fetch)
    (tmp_path / "news.json").write_text(json.dumps(
        {"updated": NOW - 7200, "items": {"b": [{"t": 1, "title": "old"}], "zz": []}}))
    assert news.run() == 0
    d = read(tmp_path)
    assert calls == ["A", "B"]                         # candidats inactifs ignorés
    assert d["updated"] == NOW
    assert d["items"]["a"][0]["title"] == "A"
    assert d["items"]["b"] == [{"t": 1, "title": "old"}]   # conservé
    assert "zz" not in d["items"]                       # candidat disparu

    calls.clear()
    assert news.run() == 0                              # moins d'une heure : rien
    assert calls == []


def test_run_fetches_only_missing_when_fresh(tmp_path, monkeypatch):
    calls = []
    setup(tmp_path, monkeypatch, lambda n, now: calls.append(n) or [])
    (tmp_path / "news.json").write_text(json.dumps({"updated": NOW - 60, "items": {"a": []}}))
    news.run()
    assert calls == ["B"]
    assert read(tmp_path)["updated"] == NOW - 60


def test_run_stops_after_consecutive_errors(tmp_path, monkeypatch):
    calls = []

    def fetch(name, now):
        calls.append(name)
        raise RuntimeError("bloqué")

    setup(tmp_path, monkeypatch, fetch)
    many = [{"slug": f"s{i}", "name": f"N{i}", "active": True} for i in range(10)]
    (tmp_path / "candidates.json").write_text(json.dumps({"candidates": many}))
    assert news.run() == 0
    assert len(calls) == news.MAX_CONSECUTIVE_ERRORS
    assert not (tmp_path / "news.json").exists()
