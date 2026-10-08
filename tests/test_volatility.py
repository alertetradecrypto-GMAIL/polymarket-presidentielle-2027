import volatility as vol

Q = 900          # pas de 15 min
DAY = 86400
T0 = 1_800_000_000
CAND = [{"slug": "le-pen", "name": "Marine Le Pen", "active": True}]


def hist(base, now, days=8, last=None):
    """Historique plat à `base` jusqu'à `now`, dernier point remplacé par `last`."""
    pts = [[t, base] for t in range(now - int(days * DAY), now + 1, Q)]
    if last is not None:
        pts[-1][1] = last
    return pts


def step(state, now, base, last, days=8):
    return vol.evaluate(CAND, {"le-pen": hist(base, now, days, last)}, state, now)


# ---------------------------------------------------------------- référence

def test_price_at_tolerance():
    pts = [[1000, 0.1], [5000, 0.2]]
    assert vol.price_at(pts, 1500) == 0.1
    assert vol.price_at(pts, 4000) == 0.2
    assert vol.price_at(pts, 3000) is None          # > 30 min des deux points
    assert vol.price_at([], 1000) is None


# ---------------------------------------------------------------- seuils

def test_seuil_24h():
    s = {}
    step(s, T0, 0.10, 0.12)                          # +20 %, +2 pt → 1re collecte
    assert step(s, T0 + Q, 0.10, 0.12)               # 2e → alerte
    s = {}
    step(s, T0, 0.10, 0.114)                         # +14 % : sous 15 %
    assert not step(s, T0 + Q, 0.10, 0.114)


def test_seuil_absolu_24h():
    s = {}
    step(s, T0, 0.02, 0.028)                         # +40 % mais 0,8 pt
    assert not step(s, T0 + Q, 0.02, 0.028)


def test_seuil_7j_seul():
    # 24 h : +10 % (pas d'alerte) ; 7 j : +32 %, +4 pt → alerte 7j uniquement
    s = {}
    for k in range(2):
        now = T0 + k * Q
        pts = [[t, 0.125 if t <= now - 7 * DAY + 3600 else 0.15] for t in
               range(now - 8 * DAY, now + 1, Q)]
        pts[-1][1] = 0.165
        alerts = vol.evaluate(CAND, {"le-pen": pts}, s, now)
    assert [a["window"] for a in alerts] == ["7j"]


def test_baisse():
    s = {}
    step(s, T0, 0.40, 0.30)
    a = step(s, T0 + Q, 0.40, 0.30)
    assert {x["sens"] for x in a} == {"down"}


# ---------------------------------------------------------------- confirmation

def test_pic_isole_ignore():
    s = {}
    assert not step(s, T0, 0.10, 0.13)
    assert not step(s, T0 + Q, 0.10, 0.10)          # retombé : réarmé
    assert not step(s, T0 + 2 * Q, 0.10, 0.13)       # repart de zéro


def test_meme_point_ne_compte_pas_deux_fois():
    s = {}
    now = T0
    pts = {"le-pen": hist(0.10, now, last=0.13)}
    vol.evaluate(CAND, pts, s, now)
    assert not vol.evaluate(CAND, pts, s, now + 60)  # relance sans nouveau point


# ---------------------------------------------------------------- anti-bruit

def _alerte(s, now, last, base=0.10):
    step(s, now - Q, base, last)
    a = step(s, now, base, last)
    vol.mark_sent(s, a, now)
    return a


def test_pas_de_repetition_si_stable():
    s = {}
    assert _alerte(s, T0, 0.13)
    for k in range(1, 60):                           # 15 h au-dessus du seuil
        assert not step(s, T0 + k * Q, 0.10, 0.13)


def test_rappel_si_aggravation_apres_6h():
    s = {}
    assert _alerte(s, T0, 0.13)

    def d24(now, last):  # la fenêtre 7 j peut aussi se déclencher ici : on regarde la 24 h
        return [a for a in step(s, now, 0.10, last) if a["window"] == "24h"]
    assert not d24(T0 + 2 * 3600, 0.15)              # aggravé mais < 6 h
    assert not d24(T0 + 6 * 3600, 0.135)             # 6 h mais +0,5 pt seulement
    a = d24(T0 + 6 * 3600 + Q, 0.14)                 # 6 h et +1 pt
    assert a and a[0]["kind"] == "rappel"


def test_rearmement():
    s = {}
    assert _alerte(s, T0, 0.13)
    step(s, T0 + Q, 0.10, 0.105)                     # sous le seuil
    assert not s["keys"]
    step(s, T0 + 2 * Q, 0.10, 0.13)
    a = step(s, T0 + 3 * Q, 0.10, 0.13)
    assert a and a[0]["kind"] == "nouvelle"


# ---------------------------------------------------------------- données

def test_donnees_perimees():
    s = {}
    pts = {"le-pen": hist(0.10, T0, last=0.13)}
    vol.evaluate(CAND, pts, s, T0 + 2 * 3600)
    assert not s["keys"]


def test_historique_court():
    s = {}
    step(s, T0, 0.10, 0.13, days=0.5)
    assert not step(s, T0 + Q, 0.10, 0.13, days=0.5)


# ---------------------------------------------------------------- run + email

def _setup_files(tmp_path, monkeypatch, last):
    import json
    hist_dir = tmp_path / "history"
    hist_dir.mkdir()
    (tmp_path / "c.json").write_text(json.dumps({"candidates": CAND}))
    (hist_dir / "le-pen.json").write_text(json.dumps({"p": hist(0.10, T0, last=last)}))
    monkeypatch.setattr(vol, "CANDIDATES_FILE", tmp_path / "c.json")
    monkeypatch.setattr(vol, "HISTORY_DIR", hist_dir)
    monkeypatch.setattr(vol, "ALERT_STATE_FILE", tmp_path / "alerts.json")
    return hist_dir


def test_run_etat_seulement_si_email_parti(tmp_path, monkeypatch):
    import json
    hist_dir = _setup_files(tmp_path, monkeypatch, 0.13)
    vol.run(T0)
    pts = hist(0.10, T0 + Q, last=0.13)
    (hist_dir / "le-pen.json").write_text(json.dumps({"p": pts}))

    def boom(*a, **k):
        raise OSError("smtp")
    assert vol.run(T0 + Q, send=boom) == 1
    st = json.loads((tmp_path / "alerts.json").read_text())
    assert all("alert_t" not in e for e in st["keys"].values())

    sent = []
    assert vol.run(T0 + Q + 60, send=lambda *a, **k: sent.append(a)) == 0  # retentée
    assert sent and sent[0][0].startswith("🚨 URGENT — Marine Le Pen +30,0 % 24h (0,100 → 0,130 $)")


def test_email_regroupe():
    a = [
        {"key": "a", "slug": "lp", "name": "Le Pen", "window": "24h", "sens": "up",
         "ref": 0.34, "cur": 0.40, "delta": 0.06, "rel": 0.176, "kind": "nouvelle"},
        {"key": "b", "slug": "lp", "name": "Le Pen", "window": "7j", "sens": "up",
         "ref": 0.28, "cur": 0.40, "delta": 0.12, "rel": 0.43, "kind": "nouvelle"},
        {"key": "c", "slug": "ph", "name": "Philippe", "window": "24h", "sens": "down",
         "ref": 0.20, "cur": 0.16, "delta": -0.04, "rel": -0.2, "kind": "rappel"},
    ]
    subject, body, text = vol.build_email(a)
    assert subject == "🚨 URGENT — Le Pen +43,0 % 7j (0,280 → 0,400 $) · +1 autre"
    assert body.count("<b>Le Pen</b>") == 1 and "(rappel)" in body
    assert "0,200 $ → 0,160 $ (cote 6,25)" in text and "−0,040 $" in text


def test_formats_jetons():
    assert vol._px(0.1234) == "0,123 $"
    assert vol._px(-0.04, True) == "−0,040 $" and vol._px(0.02, True) == "+0,020 $"
    assert vol._cote(0.25) == "4,00" and vol._cote(0) == "—"
