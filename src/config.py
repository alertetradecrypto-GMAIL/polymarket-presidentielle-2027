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
