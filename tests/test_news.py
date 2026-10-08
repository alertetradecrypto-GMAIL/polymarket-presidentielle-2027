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


def test_parse_rss_dedup_ignores_quotes():
    feed = rss(("«La violence» : Panot - LCP", "https://n.g/1", "Thu, 08 Oct 2026 08:00:00 GMT", "LCP"),
               ("'La violence' : Panot - LCP", "https://n.g/2", "Thu, 08 Oct 2026 07:00:00 GMT", "LCP"))
    assert len(news.parse_rss(feed, NOW)) == 1


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
    (tmp_path / "news.json").write_text(json.dumps({"updated": NOW - 60, "version": news.QUERY_VERSION, "items": {"a": []}}))
    news.run()
    assert calls == ["B"]
    assert read(tmp_path)["updated"] == NOW - 60


def test_run_full_refresh_when_filters_change(tmp_path, monkeypatch):
    calls = []
    setup(tmp_path, monkeypatch, lambda n, now: calls.append(n) or [])
    (tmp_path / "news.json").write_text(json.dumps({"updated": NOW - 60, "items": {"a": []}}))
    news.run()
    assert calls == ["A", "B"]
    assert read(tmp_path)["version"] == news.QUERY_VERSION


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


def art(title, source="Le Monde", t=NOW):
    return {"t": t, "title": title, "source": source, "url": "https://x"}


def test_surname():
    assert news.surname("Dominique de Villepin") == "villepin"
    assert news.surname("Marine Le Pen") == "le pen"
    assert news.surname("Jean-Luc Mélenchon") == "melenchon"
    assert news.surname("Nicolas Dupont-Aignan") == "dupont aignan"
    assert news.surname("Yaël Braun-Pivet") == "braun pivet"


def test_relevant():
    ok = news.relevant
    assert ok(art("Présidentielle : Édouard Philippe au défi"), "Édouard Philippe")
    assert ok(art("Dominique de Villepin affirme..."), "Dominique de Villepin")
    assert ok(art("Le Pen creuse l’écart"), "Marine Le Pen")
    assert ok(art("Bayrou jugé en appel"), "François Bayrou")
    # le titre ne cite pas le candidat
    assert not ok(art("Les députés rétablissent l’entretien obligatoire"), "Clémentine Autain")
    assert not ok(art("Pen-testing : le guide"), "Marine Le Pen")
    # médias people / satiriques / archives
    assert not ok(art("François Hollande et Ségolène Royal...", "Le Gorafi.fr Gorafi News Network"),
                  "François Hollande")
    assert not ok(art("Michèle Laroque et François Baroin", "Closer"), "François Baroin")
    assert not ok(art("Christine Lagarde inquiétée", "Orange Actualités"), "Christine Lagarde")
    # pages fiche / résultats
    assert not ok(art("Eric Zemmour : Actualités, vidéos, images et infos en direct"), "Éric Zemmour")
    assert not ok(art("Bernard Cazeneuve : homme politique, France - Actualité et infos"),
                  "Bernard Cazeneuve")
    assert not ok(art("Dominique de Villepin"), "Dominique de Villepin")


def test_fetch_news_election_first_then_fallback(monkeypatch):
    queries = []

    def search(q, now):
        queries.append(q)
        if "présidentielle" in q:
            return [art("Philippe candidat", t=NOW - 50), art("Hors sujet", t=NOW)]
        return [art("Philippe au Havre", t=NOW - 10), art("Philippe candidat", t=NOW - 50),
                art("Philippe ministre", t=NOW - 99)]

    monkeypatch.setattr(news, "search", search)
    monkeypatch.setattr(news.time, "sleep", lambda s: None)
    got = news.fetch_news("Édouard Philippe", NOW)
    assert len(queries) == 2 and queries[0].startswith('"Édouard Philippe" (présidentielle')
    assert [a["title"] for a in got] == ["Philippe au Havre", "Philippe candidat", "Philippe ministre"]


def test_fetch_news_no_fallback_when_enough(monkeypatch):
    queries = []
    monkeypatch.setattr(news, "search", lambda q, now: queries.append(q) or
                        [art(f"Bayrou {i}", t=NOW - i) for i in range(7)])
    assert len(news.fetch_news("François Bayrou", NOW)) == 5
    assert len(queries) == 1
