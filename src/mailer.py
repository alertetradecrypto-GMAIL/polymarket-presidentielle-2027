"""Envoi d'emails via Gmail SMTP (mot de passe d'application)."""
from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Secret manquant : {name}")
    return value


def send(subject: str, html: str, text: str | None = None, *, urgent: bool = False) -> None:
    user = _env("GMAIL_USER")
    password = _env("GMAIL_APP_PASSWORD").replace(" ", "")
    to = [a.strip() for a in _env("ALERT_TO").split(",") if a.strip()]

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"Polymarket Présidentielle <{user}>"
    msg["To"] = ", ".join(to)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="polymarket-presidentielle.local")
    if urgent:
        msg["X-Priority"] = "1"
        msg["Importance"] = "high"
    msg.set_content(text or "Ouvrez cet email dans un client compatible HTML.")
    msg.add_alternative(html, subtype="html")

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ssl.create_default_context(),
                          timeout=30) as smtp:
        smtp.login(user, password)
        smtp.send_message(msg)
