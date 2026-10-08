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

# Alertes
ALERT_REL = 0.10             # +/-10 % relatif sur 24 h
ALERT_ABS = 0.01             # et au moins 1 point absolu
ALERT_COOLDOWN_H = 6         # une alerte par candidat et par sens

# Récap quotidien
TIMEZONE = "Europe/Paris"
DAILY_HOUR = 8
