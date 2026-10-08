# Polymarket – Présidentielle 2027

Outil de suivi (lecture seule, aucun ordre) du marché Polymarket
« Next French Presidential Election » (`next-french-presidential-election`).

- `src/collect.py` : prix « Oui » de tous les candidats toutes les 15 min → `docs/data/`
  (le prix « Non » = 1 − Oui est calculé à l'affichage)
- `docs/` : tableau de bord GitHub Pages (à venir)
- Alerte email « URGENT » : variation 24 h (> 15 % et ≥ 1 pt) ou 7 j (> 30 % et ≥ 2 pts), confirmée sur 2 collectes, sans répétition tant que le mouvement ne s'aggrave pas
- Récap quotidien par email (à venir)

Aucune donnée personnelle n'est commitée : les positions ne sont lues qu'en mémoire.

## Tests

```
pip install -r requirements-dev.txt
pytest
```
