import mailer


class FakeSMTP:
    sent = []

    def __init__(self, *a, **kw): pass
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def login(self, user, pwd): FakeSMTP.login_args = (user, pwd)
    def send_message(self, msg): FakeSMTP.sent.append(msg)


def test_send(monkeypatch):
    monkeypatch.setenv("GMAIL_USER", "a@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "abcd efgh ijkl mnop")
    monkeypatch.setenv("ALERT_TO", "b@gmail.com, c@gmail.com")
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", FakeSMTP)
    mailer.send("URGENT test", "<b>x</b>", urgent=True)
    msg = FakeSMTP.sent[-1]
    assert FakeSMTP.login_args == ("a@gmail.com", "abcdefghijklmnop")
    assert msg["To"] == "b@gmail.com, c@gmail.com" and msg["Importance"] == "high"
