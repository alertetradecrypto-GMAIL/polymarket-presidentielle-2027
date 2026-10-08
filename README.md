# Polymarket – Présidentielle 2027

Outil de suivi (lecture seule, aucun ordre) du marché Polymarket
« Next French Presidential Election » (`next-french-presidential-election`).

- `src/collect.py` : prix « Oui » de tous les candidats toutes les 15 min → `docs/data/`
  (le prix « Non » = 1 − Oui est calculé à l'affichage)
- `docs/` : tableau de bord GitHub Pages (Lightweight Charts) — https://alertetradecrypto-gmail.github.io/polymarket-presidentielle-2027/
  courbes Oui/Non, vues 1J/1S/1M/Tout, comparaison (8 max), MM20/MM50 et volume du candidat sélectionné ; l'état de la vue est dans l'URL
- Alerte email « URGENT » : variation 24 h (> 15 % et ≥ 1 pt) ou 7 j (> 30 % et ≥ 2 pts), confirmée sur 2 collectes, sans répétition tant que le mouvement ne s'aggrave pas
- `src/daily.py` (daily.yml) : récap email à 8h00 heure de Paris — valeur (positions de ce marché + cash pUSD/USDC.e), PnL par position et par adresse, variation 24 h des candidats ≥ 2 % ou détenus. Lancement manuel : Actions → daily → Run workflow (envoi immédiat)

Aucune donnée personnelle n'est commitée : les positions ne sont lues qu'en mémoire.

## Tests

```
pip install -r requirements-dev.txt
pytest
```
