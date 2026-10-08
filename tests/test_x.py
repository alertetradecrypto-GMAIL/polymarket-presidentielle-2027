import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import render
import x_alerts as xa
import x_daily as xd
import x_post

TZ = ZoneInfo("Europe/Paris")
H = 3600


def ts(y, mo, d, h, mi=0):
    return int(datetime(y, mo, d, h, mi, tzinfo=TZ).timestamp())


def alert(slug, sens="up", window="24h", rel=0.2):
    return {"slug": slug, "name": slug.title(), "window": window, "sens": sens,
            "ref": 0.10, "cur": 0.10 * (1 + rel), "delta": 0.10 * rel, "rel": rel}


# ---------------------------------------------------------------- texte / x_post

def test_check_text_refuse_les_liens():
    with pytest.raises(x_post.XError):
        x_post.check_text("Voir https://polymarket.com/event/x")
    with pytest.raises(x_post.XError):
        x_post.check_text("voir polymarket.com")
    x_post.check_text("Présidentielle 2027 #Polymarket")


def test_texte_quotidien():
    rows = [{"name": f"Candidat Numéro {i}", "cur": 0.3 - i / 100, "delta": 0.012 * (-1) ** i}
            for i in range(5)]
    text = xd.build_text(rows)
    lines = text.splitlines()
    assert lines[0].startswith("Présidentielle 2027") and lines[1].startswith("French")
    assert sum(ln[:2] in ("1.", "2.", "3.") for ln in lines) == 3
    assert lines[-1] == "#Presidentielle2027 #Polymarket"
    assert "http" not in text and "★" not in text
    assert x_post.weighted_len(text) <= 280
    x_post.check_text(text)


def test_texte_alerte():
    rows = [alert("bardella", rel=0.18), alert("bardella", window="7j", rel=0.35),
            alert("philippe", "down", rel=-0.2)]
    text = xa.build_text(rows)
    assert "Mouvement fort" in text and "Strong move" in text
    assert "+1 autre" in text and "+1 more" in text
    assert text.splitlines()[-1] == "#Presidentielle2027 #Polymarket"
    x_post.check_text(text)


def test_dry_run_et_anti_doublon(tmp_path, monkeypatch):
    now = 1_800_000_000
    assert x_post.publish("Bonjour", None, now=now) == "dry_run"
    assert not x_post.POSTS_FILE.exists()          # rien d'enregistré en DRY_RUN
    x_post.POSTS_FILE.parent.mkdir(parents=True)
    x_post.POSTS_FILE.write_text(json.dumps({"recent": [{"h": x_post.text_hash("Bonjour"),
                                                         "t": now - 3600}]}))
    assert x_post.publish("Bonjour", None, now=now) == "duplicate"
    assert x_post.publish("Bonjour", None, now=now + 8 * 86400) == "dry_run"   # expiré


def test_dry_run_par_defaut(monkeypatch):
    monkeypatch.delenv("DRY_RUN")
    assert x_post.is_dry_run()
    monkeypatch.setenv("DRY_RUN", "0")
    assert not x_post.is_dry_run()


# ---------------------------------------------------------------- créneau quotidien

def test_creneau_quotidien():
    assert not xd.should_post(ts(2026, 10, 8, 15, 29), {})[0]
    assert xd.should_post(ts(2026, 10, 8, 15, 30), {})[0]
    assert xd.should_post(ts(2026, 10, 8, 17, 59), {})[0]
    assert not xd.should_post(ts(2026, 10, 8, 18, 0), {})[0]
    assert not xd.should_post(ts(2026, 10, 8, 16), {"last_attempt": "2026-10-08"})[0]


@pytest.mark.parametrize("utc_hour,expected", [(13, True), (14, False)])
def test_crons_ete(utc_hour, expected):
    # 8 octobre = heure d'été : 13:30 UTC = 15:30 Paris, 14:30 UTC déjà fait
    now = int(datetime(2026, 10, 8, utc_hour, 30, tzinfo=ZoneInfo("UTC")).timestamp())
    state = {} if expected else {"last_attempt": "2026-10-08"}
    assert xd.should_post(now, state)[0] is expected


def test_crons_hiver():
    # 8 décembre = heure d'hiver : 13:30 UTC = 14:30 Paris (trop tôt), 14:30 UTC = 15:30
    utc = ZoneInfo("UTC")
    assert not xd.should_post(int(datetime(2026, 12, 8, 13, 30, tzinfo=utc).timestamp()), {})[0]
    assert xd.should_post(int(datetime(2026, 12, 8, 14, 30, tzinfo=utc).timestamp()), {})[0]


def _daily_data(tmp_path, monkeypatch, now):
    hist_dir = tmp_path / "history"
    hist_dir.mkdir()
    cands = []
    for i, (name, ref, cur) in enumerate([("Alpha", 0.30, 0.33), ("Beta", 0.20, 0.18),
                                          ("Gamma", 0.10, 0.11), ("Delta", 0.01, 0.01)]):
        slug = name.lower()
        cands.append({"slug": slug, "name": name, "active": True, "price": cur,
                      "volume24h_usd": 1000.0 * (i + 1)})
        pts = [[t, ref] for t in range(now - 2 * 86400, now - 3 * H, 900)] + [[now - 600, cur]]
        (hist_dir / f"{slug}.json").write_text(json.dumps({"p": pts}))
    cf = tmp_path / "candidates.json"
    cf.write_text(json.dumps({"updated": now - 600, "candidates": cands}))
    monkeypatch.setattr(xd, "CANDIDATES_FILE", cf)
    monkeypatch.setattr(xd, "HISTORY_DIR", hist_dir)


def fake_png(page_html, out):
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page_html)
    return out


def test_run_quotidien(tmp_path, monkeypatch):
    now = ts(2026, 10, 8, 15, 31)
    _daily_data(tmp_path, monkeypatch, now)
    sent = []
    pub = lambda text, image, now: sent.append((text, image)) or "dry_run"  # noqa: E731
    assert xd.run(now, publish=pub, to_png=fake_png) == 0
    text, image = sent[0]
    assert "1. Alpha 33.0% (+3.0 pt)" in text and "Delta" not in text   # < 2 % exclu
    page = Path(image).read_text()
    assert "Source: Polymarket" in page and "Not financial advice" in page
    assert "★" not in page and "<a " not in page and "polymarket.com" not in page
    assert not xd.X_DAILY_STATE.exists()                 # DRY_RUN : état inchangé


def test_run_quotidien_reel_une_seule_tentative(tmp_path, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "0")
    now = ts(2026, 10, 8, 15, 31)
    _daily_data(tmp_path, monkeypatch, now)
    calls = []

    def boom(text, image, now):
        calls.append(1)
        raise RuntimeError("HTTP 503")

    assert xd.run(now, publish=boom, to_png=fake_png) == 1
    assert xd.run(now + 3600, publish=boom, to_png=fake_png) == 0   # 2e cron : rien
    assert len(calls) == 1


def test_run_quotidien_donnees_perimees(tmp_path, monkeypatch):
    now = ts(2026, 10, 8, 15, 31)
    _daily_data(tmp_path, monkeypatch, now - 3 * H)
    assert xd.run(now, publish=lambda *a, **k: pytest.fail("publié"), to_png=fake_png) == 1


# ---------------------------------------------------------------- règles d'alerte

def test_regle_6h_par_candidat_et_sens():
    now = ts(2026, 10, 8, 12)
    state = xa.record({}, [alert("a")], now)
    assert xa.select([alert("a")], state, now + 5 * H) == []
    assert xa.select([alert("a", window="7j")], state, now + 5 * H) == []   # fenêtres confondues
    assert len(xa.select([alert("a", "down", rel=-0.2)], state, now + H)) == 1  # autre sens
    assert len(xa.select([alert("a")], state, now + 6 * H)) == 1


def test_plafond_3_par_jour():
    now = ts(2026, 10, 8, 8)
    state = {}
    for i in range(3):
        state = xa.record(state, [alert(f"c{i}")], now + i * H)
    assert xa.select([alert("nouveau")], state, now + 5 * H) == []
    assert len(xa.select([alert("nouveau")], state, ts(2026, 10, 9, 0, 5))) == 1  # lendemain


def test_trois_candidats_max_par_tweet():
    rows = xa.select([alert(f"c{i}", rel=0.2 + i / 100) for i in range(5)], {}, 1_800_000_000)
    assert [r["slug"] for r in rows] == ["c4", "c3", "c2"]


def test_queue_et_run_alerte(tmp_path):
    now = ts(2026, 10, 8, 12)
    assert xa.queue([alert("a")], now) == 1 and xa.PENDING_FILE.exists()
    sent = []
    pub = lambda text, image, now: sent.append(text) or "dry_run"  # noqa: E731
    assert xa.run(now + 60, publish=pub, to_png=fake_png) == 0
    assert len(sent) == 1 and not xa.PENDING_FILE.exists()
    assert not xa.X_ALERTS_STATE.exists()                 # DRY_RUN : état inchangé


def test_alerte_echec_consomme_le_creneau(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "0")
    now = ts(2026, 10, 8, 12)

    def boom(text, image, now):
        raise RuntimeError("HTTP 500")

    xa.queue([alert("a")], now)
    assert xa.run(now + 60, publish=boom, to_png=fake_png) == 1
    assert xa.queue([alert("a")], now + 900) == 0        # pas de nouvelle tentative


def test_queue_vide_supprime_l_attente():
    now = ts(2026, 10, 8, 12)
    xa.queue([alert("a")], now)
    assert xa.queue([], now + 900) == 0 and not xa.PENDING_FILE.exists()


# ---------------------------------------------------------------- visuels

def test_visuels_bilingues():
    when = datetime(2026, 10, 8, 15, 30, tzinfo=TZ)
    a = render.daily_html([{"name": "Alpha", "cur": 0.3, "ref": 0.28, "delta": 0.02,
                            "rel": 0.0714, "volume24h": 125000}], when)
    for s in ("Candidat", "Candidate", "Il y a 24 h", "24h ago", "$125k",
              "8 octobre 2026", "October 8, 2026", "Source: Polymarket", "Not financial advice"):
        assert s in a
    b = render.alert_html([alert("a", window="7j")], when)
    for s in ("Mouvement fort", "Strong move", "Avant", "Before", "7j · 7d", "15:30"):
        assert s in b
