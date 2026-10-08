"""Rattrapage complet du volume depuis l'ouverture du marché (lancement manuel).

Usage : python src/backfill_volume.py [--force]
  --force : refait le rattrapage même pour les candidats déjà traités.

Effectue une collecte normale sans limite de rattrapages par passage :
les candidats sans historique de volume sont reconstruits trade par trade.
"""
from __future__ import annotations

import argparse
import logging
import sys

import collect
from config import HISTORY_DIR


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force", action="store_true", help="refaire tous les candidats")
    args = ap.parse_args()
    if args.force:
        for path in sorted(HISTORY_DIR.glob("*.json")):
            hist = collect.read_json(path, None)
            if hist and ("vol_since" in hist or "vol" in hist):
                hist.pop("vol_since", None)
                hist.pop("vol", None)
                collect.write_json(path, hist)
    return collect.run(backfill_budget=None)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sys.exit(main())
