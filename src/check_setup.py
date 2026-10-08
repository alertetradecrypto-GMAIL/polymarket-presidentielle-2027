"""Vérifie la configuration : secrets, adresses Polymarket et envoi d'email.

Les logs GitHub d'un dépôt public sont visibles par tous : on n'y écrit
AUCUNE adresse ni montant. Le détail part uniquement par email.
"""
from __future__ import annotations

import logging
import os
import sys
from html import escape

import mailer
import polymarket as pm

log = logging.getLogger("check")
SECRETS = ["GMAIL_USER", "GMAIL_APP_PASSWORD", "ALERT_TO", "POLY_ADDR_1", "POLY_ADDR_2"]


def mask(addr: str) -> str:
    return f"{addr[:6]}…{addr[-4:]}" if len(addr) > 12 else "?"


def main() -> int:
    missing = [s for s in SECRETS if not os.environ.get(s, "").strip()]
    for s in SECRETS:
        log.info("%-20s %s", s, "absent" if s in missing else "présent")
    if missing:
        return 1

    rows, ok = [], True
    for i in (1, 2):
        addr = os.environ[f"POLY_ADDR_{i}"].strip()
        valid = addr.startswith("0x") and len(addr) == 42
        try:
            positions = pm.get_positions(addr) if valid else []
            value = sum(float(p.get("currentValue") or 0) for p in positions)
            status = f"{len(positions)} position(s), valeur ≈ {value:,.2f} $"
            if not valid:
                status, ok = "format invalide (attendu : 0x + 40 caractères)", False
        except Exception as exc:
            status, ok = f"erreur API : {escape(str(exc))[:120]}", False
        log.info("Adresse %d : %s", i, "OK" if valid and "erreur" not in status else "PROBLÈME")
        rows.append(f"<tr><td>Adresse {i}</td><td><code>{mask(addr)}</code></td>"
                    f"<td>{status}</td></tr>")

    html = (
        "<h2>✅ Test de configuration réussi</h2>"
        "<p>Gmail fonctionne : vous recevez cet email.</p>"
        "<table border='1' cellpadding='6' style='border-collapse:collapse'>"
        + "".join(rows) + "</table>"
        "<p>Vérifiez que le nombre de positions correspond à ce que vous voyez sur Polymarket.</p>"
    )
    mailer.send("Polymarket Présidentielle — test de configuration", html)
    log.info("Email de test envoyé.")
    return 0 if ok else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sys.exit(main())
