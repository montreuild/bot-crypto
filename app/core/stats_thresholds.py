"""Seuils statistiques partagés pour les décisions engageantes (BT-06).

Le repo utilisait trois seuils de « significativité » différents (2, 3, 10)
selon le module, sans justification commune. Deux familles distinctes :

- ``MIN_TRADES_DEGENERATE`` : filtre anti-dégénérescence (division par zéro,
  moyenne sur échantillon vide). N'autorise AUCUNE décision — il rend juste
  le calcul possible. Utilisé par ``composite_score`` pour écarter les
  résultats vides du classement.

- ``MIN_SIGNIFICANT_TRADES`` : seuil de DÉCISION engageante (appliquer un
  paramétrage optimisé, promouvoir un bot en ACTIF). Justification : la marge
  d'erreur binomiale sur un win-rate estimé à N trades vaut ≈ 1/(2√N) à 95 % ;
  à N=10 elle est déjà de ±16 points — en dessous, un WR ou un PnL OOS positif
  est indiscernable du bruit. À N=2-3, la probabilité qu'une stratégie SANS
  edge affiche 100 % de réussite est de 25-12,5 % : appliquer/promouvoir sur
  cette base revient à sélectionner du bruit (biais de tests multiples,
  cf. TODO « Deflated Sharpe » dans auto_optimizer).
"""

MIN_TRADES_DEGENERATE = 2
MIN_SIGNIFICANT_TRADES = 10
