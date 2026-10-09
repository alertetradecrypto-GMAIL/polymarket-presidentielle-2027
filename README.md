# Polymarket – Présidentielle 2027

Outil de suivi (lecture seule, aucun ordre) du marché Polymarket
« Next French Presidential Election » (`next-french-presidential-election`).

- `src/collect.py` : prix « Oui » de tous les candidats toutes les 15 min → `docs/data/`
  (le prix « Non » = 1 − Oui est calculé à l'affichage), et volume par tranche de 15 min
  reconstruit à partir des trades (Data API `/trades`, côté taker) : parts (= volume Gamma)
  et dollars (parts × prix de chaque trade)
- `src/backfill_volume.py` (backfill-volume.yml, lancement manuel) : historique du volume depuis
  l'ouverture du marché. Les nouveaux candidats sont rattrapés seuls par la collecte (3 max par passage)
- `src/news.py` (après la collecte, au plus 1 fois par heure) : 5 actualités les plus récentes
  de chaque candidat (flux RSS Google News, 30 derniers jours ; titre, média, date, lien) → `docs/data/news.json`.
  Requête « nom + présidentielle/2027/candidat/sondage… », complétée par le nom seul ; le titre doit citer
  le nom de famille ; médias people/satiriques, archives vidéo et pages « fiche » écartés
  Une panne de la source ne bloque pas la collecte. `python src/news.py --force` pour forcer
- `docs/` : tableau de bord GitHub Pages (Lightweight Charts) — https://alertetradecrypto-gmail.github.io/polymarket-presidentielle-2027/
  courbes Oui/Non, vues 1J/1S/1M/Tout, comparaison (8 max), MM20/MM50 et volume du candidat sélectionné ; variations du prix Oui sur 1 h / 4 h / 12 h / 24 h
  (en points ou en %, triables) ; clic sur un nom → fenêtre avec ses variations et ses 5 dernières actualités ; l'état de la vue est dans l'URL
- Alerte email « URGENT » : variation 24 h (> 15 % et ≥ 1 pt) ou 7 j (> 30 % et ≥ 2 pts), confirmée sur 2 collectes, sans répétition tant que le mouvement ne s'aggrave pas
- `src/daily.py` (daily.yml) : récap email à 8h00 heure de Paris — valeur (positions de ce marché + cash pUSD/USDC.e), PnL par position et par adresse, variation 24 h des candidats ≥ 2 % ou détenus. Lancement manuel : Actions → daily → Run workflow (envoi immédiat)
- Publication X (@MarketSentinelX), sans lien ni donnée de portefeuille :
  - `src/x_daily.py` (tweet.yml) : tweet quotidien à 15h30 heure de Paris, visuel bilingue des candidats ≥ 2 % + top 3 en texte
  - `src/x_alerts.py` (étape de collect.yml) : tweet « Mouvement fort / Strong move » sur les alertes 24 h / 7 j, 1 tweet max par candidat et par sens toutes les 6 h, 3 par jour maximum
  - `src/render.py` : visuels PNG via Playwright ; `src/x_post.py` : publication (tweepy), anti-doublon, une seule tentative
  - **Tout tourne en `DRY_RUN` tant que la variable de dépôt `X_LIVE` ne vaut pas `1`** (Settings → Secrets and variables → Actions → Variables). Test : Actions → tweet → Run workflow (dry_run coché) → PNG et texte dans l'artefact

- `backtest/` (backtest.yml, lancement manuel) : test des stratégies « ordres limites maker » + « valeur théorique
  issue des sondages » sur 6 mois (Le Pen, Philippe, Mélenchon, Lisnard), écarts 2/3/4 pts, comparaison taker
  et achat-conservation. Sondages relevés à la main dans `backtest/polls_*.csv`. Résultat : artefact `backtest`
  (`report.html`, `results.json`). En local : `python backtest/run.py && python backtest/report.py`

Aucune donnée personnelle n'est commitée : les positions ne sont lues qu'en mémoire.

## Tests

```
pip install -r requirements-dev.txt
pytest
```
