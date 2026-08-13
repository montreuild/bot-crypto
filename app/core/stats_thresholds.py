"""Seuil statistique partagé pour les décisions engageantes (BT-06).

``MIN_SIGNIFICANT_TRADES`` — nombre de trades en dessous duquel un résultat
n'autorise aucune décision : ni classer un paramétrage, ni l'appliquer, ni
promouvoir un bot en ACTIF.

Justification : la marge d'erreur binomiale sur un win-rate estimé à N trades
vaut ≈ 1/(2√N) à 95 % ; à N=10 elle est déjà de ±16 points — en dessous, un WR
ou un PnL OOS positif est indiscernable du bruit. À N=2-3, la probabilité qu'une
stratégie SANS edge affiche 100 % de réussite est de 25-12,5 % : décider sur
cette base revient à sélectionner du bruit.

──────────────────────────────────────────────────────────────────────────────
Pourquoi il n'y a plus deux seuils
──────────────────────────────────────────────────────────────────────────────

Le module portait aussi ``MIN_TRADES_DEGENERATE = 2``, présenté comme un filtre
« anti-dégénérescence » qui « n'autorise AUCUNE décision — il rend juste le
calcul possible ». La distinction était défendable sur le papier ; elle ne l'a
pas été à l'usage, pour deux raisons mesurées :

1. **Il n'avait qu'un seul point d'application** : le défaut de
   ``composite_score``, c'est-à-dire la métrique qui **classe** les paramétrages
   d'une optimisation. Ce classement EST une décision — c'est lui qui désigne le
   jeu retenu. Le seuil « qui n'autorise aucune décision » gouvernait donc
   exactement une décision, et une seule.

2. **Il n'atteignait même pas son but déclaré.** À N=2 le calcul est
   « possible » mais dominé par des ratios saturés : `docs/SUITE_ABLATION_V3.md`
   §1 relève des Sharpe de 7,83 sur deux trades et de 5,44 sur quatre qui
   remportaient la sélection face à des configurations à trente trades. Sur
   vingt recalibrations, **quatorze sortaient à moins de dix trades OOS**. Rendre
   le calcul possible sans le rendre significatif ne protège de rien : ça
   fabrique des gagnants.

Le dépôt refusait donc de PROMOUVOIR sous dix trades (``beats_baseline``,
``slot_lifecycle``, ``overfitting_gate``) tout en SÉLECTIONNANT avec un plancher
de deux. Un seul seuil supprime l'écart.

⚠️ **Conséquence assumée** : une optimisation dont aucun essai n'atteint dix
trades ne retourne plus de gagnant plutôt qu'un gagnant fabriqué. C'est le
comportement voulu. La sortie de secours est ``optimizer.min_trades`` dans la
config, pour les études qui ont besoin de l'ancien plancher.
"""

MIN_SIGNIFICANT_TRADES = 10
