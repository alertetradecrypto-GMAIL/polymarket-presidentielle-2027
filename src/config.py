"""Paramètres centraux du projet (aucune donnée personnelle ici)."""
from pathlib import Path

# Marché Polymarket (titre affiché : « Next French Presidential Election »)
EVENT_SLUG = "next-french-presidential-election"
EVENT_ID = "79987"  # secours si le slug change

GAMMA_URL = "https://gamma-api.polymarket.com"
CLOB_URL = "https://clob.polymarket.com"
DATA_API_URL = "https://data-api.polymarket.com"

# Chemins
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "docs" / "data"
HISTORY_DIR = DATA_DIR / "history"
CANDIDATES_FILE = DATA_DIR / "candidates.json"
STATE_DIR = ROOT / "state"

# Collecte
FIDELITY_MIN = 15            # pas des points courants (minutes)
BACKFILL_FIDELITY_MIN = 60   # pas de l'historique complet initial
BACKFILL_FINE_DAYS = 7       # derniers jours récupérés au pas de 15 min à l'initialisation
OVERLAP_S = 3600             # recouvrement à chaque passage (comble les crons sautés)
MAX_SPREAD_FOR_MID = 0.10    # au-delà, Polymarket affiche le dernier prix échangé
HTTP_TIMEOUT_S = 20

# Alertes de volatilité (prix « Oui », en probabilité 0-1)
ALERT_WINDOWS = (
    # (libellé, durée en s, variation relative >, variation absolue ≥)
    ("24h", 24 * 3600, 0.15, 0.01),
    ("7j", 7 * 86400, 0.30, 0.02),
)
ALERT_CONFIRM_PASSES = 2       # seuil dépassé sur 2 collectes consécutives
ALERT_REMIND_COOLDOWN_H = 6    # délai minimum avant un rappel
ALERT_REMIND_ABS = 0.01        # rappel seulement si aggravation ≥ 1 pt depuis la dernière alerte
ALERT_REF_TOLERANCE_S = 1800   # point de référence à ±30 min de la cible
ALERT_MAX_AGE_S = 3600         # dernier point de plus d'1 h → candidat ignoré
ALERT_STATE_FILE = STATE_DIR / "alerts.json"
MARKET_URL = f"https://polymarket.com/event/{EVENT_SLUG}"

# Récap quotidien
TIMEZONE = "Europe/Paris"
DAILY_HOUR = 8
DAILY_LATEST_HOUR = 12       # cron retardé : on envoie encore jusqu'à 11h59
DAILY_STATE_FILE = STATE_DIR / "daily.json"   # contient uniquement la date du dernier envoi
DAILY_MIN_PRICE = 0.02       # candidats listés : prix Oui ≥ 2 % ou détenus
DAILY_STALE_S = 2 * 3600     # données de prix plus vieilles → avertissement dans l'email

# Cash on-chain (Polygon) : pUSD (collatéral actuel) + USDC.e (ancien), 6 décimales
POLYGON_RPCS = (
    "https://polygon-bor-rpc.publicnode.com",
    "https://polygon.drpc.org",
    "https://1rpc.io/matic",
    "https://polygon-rpc.com",
)
CASH_TOKENS = {
    "pUSD": "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB",
    "USDC.e": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
}
CASH_DECIMALS = 6
