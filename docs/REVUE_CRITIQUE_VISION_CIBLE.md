# Revue critique — Vision cible « Portefeuille de bots autonomes »

> Analyse critique de `VISION_CIBLE_BOTS_AUTONOMES.md` : incohérences internes, risques,
> améliorations. Couvre le cycle de vie, la gestion des positions, le sizing, l'entraînement,
> le backtest, les notifications et l'opérationnel.
>
> Légende : ⚠ incohérence interne · 🔴 risque · 💡 amélioration.

---

## 0. Synthèse : les 4 contradictions structurelles à régler d'abord

1. **⚠ Le netting contredit l'isolation « garantie par l'exchange ».** §6.3 recommande un wallet
   margin isolé par bot (« budget garanti par l'exchange, un bot ne perd que son budget »). §6.3 bis
   nette plusieurs bots dans le wallet isolé **unique** de la paire. Or Binance n'a qu'**un** wallet
   margin isolé par paire et par compte : deux bots à levier sur BTC/USDC **partagent** ce wallet.
   La garantie d'isolation par l'exchange **saute** dès qu'il y a netting — on revient à une
   comptabilité 100 % logicielle, et une liquidation du net touche **tous** les bots du wallet, pas
   seulement le fautif. Les deux promesses ne peuvent pas être vraies en même temps.

2. **⚠ Le netting est impossible entre spot et margin.** Le mapping §6.3 met les longs en **spot**
   et les shorts en **margin isolé** → deux wallets distincts. Mais l'exemple §6.3 bis nette A
   (short) et B (long) sur la même paire. Un short-margin et un long-spot vivent dans des wallets
   différents : **aucun netting possible**, on porte les deux positions pour de vrai en payant les
   deux coûts. Le netting n'existe que si A et B sont dans le **même** wallet (donc tous deux margin
   sur la même paire). Le titre « long spot + short margin nettés » est faux.

3. **⚠ La « fidélité au backtest » est sur-revendiquée.** §2.2 et §5 affirment que « la divergence
   live/backtest disparaît par construction ». Faux : le live introduit le netting, le stop de
   protection global, les transferts inter-wallets qui changent le budget quotidiennement, et un
   budget qui bouge alors que le backtest figé tourne sur capital fixe. Autant d'effets que le
   moteur de backtest ne modélise pas. La fidélité est *améliorée* (sizing sur budget, plus de
   vetos globaux), pas *garantie*.

4. **⚠ Dépendance circulaire budget ↔ score.** §1.5 pilote le budget par le score ; le score
   composite hérité du code (`opt_scoring.py`) utilise le **PnL absolu** (normalisé à 100 USDC).
   Chaîne circulaire : budget → taille des positions → PnL absolu → score → budget. Un bot bien
   doté « score » mieux mécaniquement. **Le score doit devenir indépendant du budget** (rendement %,
   R-multiple, Sharpe) — refonte non mentionnée dans la vision.

---

## 1. Cycle de vie

- **⚠ « Pas de seuils magiques » (§1.5) vs un cycle truffé de seuils (§3).** Candidat→Essai exige
  OOS > 0, ≥ 10 trades, overfit ≤ 2.0, WF positif sur la majorité des folds ; Essai→Actif exige
  N trades/jours + PnL dans la fourchette. Ce sont des gates binaires assumés. La formulation
  honnête : *continuum de budget pour les Actifs + gates discrets aux entrées/sorties d'incubation*.
  Le slogan « pas de seuils » est à abandonner.
- **🔴 Transitions corrélées en changement de régime.** Forward-test glissant sur params figés : un
  retournement de marché fait chuter le score de **tous** les bots en même temps → budgets→0
  simultanés → portefeuille en cash + **tempête de re-optimisations** concurrentes. Le « plancher de
  bots actifs » présent dans la 1re analyse a **disparu** de la cible. À réintroduire, avec un
  lissage des transitions (quota de rétrogradations/jour, étalement des re-opts).
- **⚠ Le contrat Monte-Carlo figé recommet le péché de l'OOS figé.** §3 fige la fourchette MC
  « à la création », alors que §1.2 reproche précisément à l'OOS d'être une photo figée. Le cône
  devrait **glisser** avec le forward-test, sinon il devient périmé de la même façon.
- **🔴 Essai « paper OU micro-budget » : ambigu et risqué.** Si paper → la donnée de « confirmation »
  est du paper (slippage simulé), pas du réel. Si micro-budget réel → de l'argent réel sur des bots
  non prouvés. À trancher. Recommandé : **paper pur en Essai**, puis un warm-up réel court et
  plafonné (montée en budget progressive) à l'entrée en Actif.
- **💡 overfit ≤ 2.0 (promotion) vs pénalité optimiseur à 2.5** : deux seuils différents pour la même
  notion. À réconcilier (un seul seuil configurable).
- **🔴 Zone grise du plancher.** « budget → 0 = retiré » mais le plancher = minimum de notional
  exchange. Un bot coincé juste au-dessus du plancher trade des tailles minuscules non
  représentatives de son backtest → **infidèle** et bruité. Définir une bande morte : sous X %, on
  retire franchement plutôt que de laisser vivoter.

## 2. Gestion des positions & netting

- **🔴 Réconciliation après coupure : l'attribution au prorata lèse des bots.** §6.3 quater propose
  de réattribuer « au prorata » un stop exchange déclenché pendant une panne. Mais les bots ont des
  stops individuels différents (l'un plus serré que l'autre) ; le prorata ignore cette réalité et
  attribue une exécution à un bot qui n'aurait pas dû sortir. Mécanisme d'attribution à durcir
  (priorité aux stops logiciels les plus proches, ordonnancement temporel reconstruit).
- **🔴 La borne « un bot ne perd que son budget » n'est pas absolue en spot.** Pas de stop
  structurel en spot ; si le marché gappe sous le stop logiciel pendant que le process est down, la
  perte réelle peut dépasser le budget. Le stop global protège le **net**, pas chaque bot. La
  garantie est *statistique*, pas *dure* — à formuler ainsi.
- **⚠ Multi-positions intra-bot non spécifié.** `max_pyramiding` et le scale-in existent : un bot
  peut tenir plusieurs positions. Comment répartit-il **son** budget entre elles ? Non défini →
  risque de sur-allocation interne. Préciser (budget résiduel vs budget plein par position).
- **💡 Crédit à donner : la facturation d'emprunt théorique (§6.3 ter) est cohérente avec la
  fidélité.** En facturant l'emprunt théorique (et non réel), le coût live matche le backtest —
  c'est l'un des rares endroits où la fidélité est réellement préservée. Bon design.

## 3. Sizing

- **⚠ Budget mouvant vs backtest figé.** §2.2 : « sizing = formule du backtest ». Mais le budget du
  bot change chaque jour (rebalance) tandis que le backtest figé tourne sur budget fixe → le sizing
  live ne reproduit pas celui du backtest dès le 2e jour. Cohérent seulement si le forward-test
  re-tourne sur le budget courant — ce qui ramène la circularité (§0.4). À résoudre via métrique
  budget-indépendante.
- **🔴 Levier non spécifié dans le sizing.** Le sizing par distance de stop fixe déjà la taille ;
  le levier ne fait qu'autoriser notional > budget. Sans cap explicite **notional ≤ budget ×
  levier**, un bot à levier 3× peut prendre 3× le risque validé. Définir l'interaction levier/sizing.
- **💡 `risk%` : par bot ou global ?** Le doc écrit « risque % × budget » sans dire si `risk%` fait
  partie de l'identité/venue du bot. Le rattacher au profil du bot (un bot prudent et un bot agressif
  n'ont pas le même `risk%`).
- **🔴 Transfert de budget hors d'un wallet isolé à position ouverte = liquidation rapprochée.** Le
  rebalance quotidien déplace du capital entre wallets ; retirer du collatéral d'un wallet isolé qui
  porte une position ouverte **rapproche son prix de liquidation**. Non traité. Règle nécessaire :
  ne jamais retirer de collatéral sous le seuil qui dégrade le margin level d'une position vivante.

## 4. Entraînement / optimisation

- **🔴 Biais de sélection multiple toujours non corrigé.** 40 trials → on sélectionne le tirage
  chanceux. La 1re analyse le pointait (Deflated Sharpe / White Reality Check) ; la cible ne le
  reprend pas. ≥ 10 trades OOS est mieux que 2 mais reste faible. À ajouter au gate de naissance.
- **⚠ Jitter de bord du forward-test glissant.** Une fenêtre glissante 30–60 j fait sauter le score
  quand un gros trade **entre ou sort** de la fenêtre — pas parce que la stratégie a changé. Besoin
  de fenêtres chevauchantes / lissage, sinon le budget oscille sur du bruit de bord.
- **⚠ Le venue doit entrer dans l'optimisation.** Le levier change le sizing donc les résultats du
  backtest. Si le venue (levier) est un attribut du bot (§6.4), l'optimisation doit soit l'inclure
  dans l'espace de recherche, soit le fixer **avant** le labo. Non spécifié.
- **🔴 Coût CPU sous-estimé.** §1.3 dit « négligeable » : vrai pour 1 backtest/jour/bot. Faux pour la
  **re-optimisation** (40 trials × WF 5 folds × MC 200 runs) déclenchée en masse quand un régime
  retire beaucoup de bots d'un coup (cf. §1 transitions corrélées). Prévoir une file et un quota.

## 5. Backtest

- **⚠ « Moteur de backtest conservé tel quel » (§5) est faux.** Il faut au minimum le faire sizer sur
  le **budget du bot** (pas le capital global) et l'industrialiser pour le forward-test glissant
  quotidien. Ce n'est pas « tel quel ».
- **⚠ Tension fidélité ↔ netting.** Le backtest ne peut pas modéliser le netting (il ne connaît
  qu'une stratégie), ni le stop global, ni la latence des transferts. Donc soit on garde les bots
  *vraiment* indépendants (pas de netting → backtest fidèle), soit on accepte des effets live que le
  backtest ne voit pas. Le doc veut les deux. Choix à assumer explicitement.
- **💡 Biais paper vs MC.** Le moteur paper utilise un slippage fixe (0.001) ; la fourchette MC vient
  d'un backtest sans slippage. Comparer l'un à l'autre introduit un léger biais systématique. Aligner
  les hypothèses de coût entre paper et contrat MC.

## 6. Notifications

- **🔴 Spam de rebalance.** Budget recalculé quotidiennement → notifier chaque variation noie
  l'information. Définir des seuils (variation significative, franchissement d'état) ; ne pas
  notifier les micro-ajustements.
- **⚠ Pas de hiérarchie de sévérité.** Margin level critique par wallet, dead-man déclenché, mismatch
  de réconciliation, stop global tiré = **alertes critiques** à séparer du routinier (promotion de
  bot, rebalance). Définir 3 niveaux : info / avertissement / critique, avec canaux/priorités
  distincts.
- **🔴 Le mismatch de réconciliation doit alerter fort — non mentionné.** C'est le signal n° 1 que la
  comptabilité virtuelle a divergé du réel (donc que les décisions de budget reposent sur du faux).
  Alerte critique obligatoire + gel des nouvelles entrées jusqu'à résolution.
- **💡 Miroir Telegram ↔ UI (§4).** Si le throttling s'applique à Telegram mais pas au fil UI, les
  deux divergent. Préciser que la politique de notification est commune aux deux canaux.

## 7. Opérationnel & architecture

- **⚠🔴 Dead-man switch vs « un seul processus » (§6.4.5).** Un dead-man switch piloté par heartbeat
  suppose un **watchdog séparé** : si le process unique perd le réseau, il ne peut ni détecter son
  propre heartbeat manquant ni envoyer d'ordre. Seul un **stop persistant pré-posé sur l'exchange**
  fonctionne vraiment lors d'une coupure. La vision veut à la fois le dead-man actif et un process
  unique — incompatible. Assumer soit un 2e mini-process watchdog, soit le tout-stop-exchange.
- **🔴 SPOF.** Process unique = point de défaillance unique pour un système à levier. À mitiger
  (supervision externe, redémarrage auto, état persisté pour reprise propre).
- **🔴 Transferts inter-wallets quotidiens, présentés comme triviaux.** Beaucoup de transferts
  spot ↔ N wallets isolés, avec limites/timing Binance, friction, et interaction avec les positions
  ouvertes (§3 liquidation). À cadrer : fréquence, montants minimaux, fenêtre de rebalance.
- **⚠ Localisation de la réserve 10 %.** Si les longs sont en spot et les shorts en wallets isolés,
  où vit la réserve ? Elle doit pouvoir couvrir les appels de marge → localisation et mobilité à
  définir.
- **🔴 Collision de hash d'identité.** Identité = `(stratégie, tf, hash_params)` (§2.3). Deux
  optimisations peuvent retomber sur des params identiques → même hash → l'historique d'un v2 se
  mélange avec un ancien bot retiré. Ajouter une **génération monotone** (et le venue) à l'identité.
- **⚠ Migration sous-séquencée (§7).** Le moteur de netting + stops découplés — le composant **le
  plus risqué** — n'est pas un palier explicite ; la migration saute de « budgets virtuels » à
  « allocation auto » à « cycle complet ». Isoler le netting/réconciliation comme étape dédiée, avec
  une longue phase shadow (compta virtuelle calculée mais ordres encore 1:1) avant de basculer.

---

## 8. Améliorations prioritaires (constructives)

| # | Amélioration | Règle | Résout |
|---|---|---|---|
| 1 | **Score budget-indépendant** | Rendement % / R-multiple / Sharpe au lieu du PnL absolu | §0.4 circularité |
| 2 | **Cône MC glissant** | Recalculé avec le forward-test, jamais figé | §1 photo figée |
| 3 | **Plancher de bots actifs + lissage** | Quota de rétrogradations/j, file de re-opts, garde N bots actifs | §1 flush systémique |
| 4 | **Netting borné au même wallet** | Documenter : long-spot + short-margin **ne se nettent pas** ; netting = même venue/paire ; wallet isolé partagé ⇒ compta logicielle + stop sur le net du wallet + surveillance liquidation partagée | §0.1, §0.2 |
| 5 | **Filet exchange-first** | Stop persistant pré-posé sur le net (survit à la panne) ; dead-man seulement si watchdog séparé | §7 dead-man/SPOF |
| 6 | **Cap notional = budget × levier + `risk%` par bot** | Inscrits dans l'identité/venue | §3 levier |
| 7 | **Garde-fou rebalance** | Ne pas retirer de collatéral d'un wallet à position ouverte sous le seuil de margin | §3 liquidation |
| 8 | **Hiérarchie de notifications** | 3 niveaux + throttling commun Telegram/UI + alerte critique sur mismatch | §6 |
| 9 | **Deflated Sharpe au gate de naissance** | Corriger le biais de 40 trials | §4 |
| 10 | **Identité = (strat, tf, hash, génération, venue)** | Génération monotone anti-collision | §7 |

---

## 9. Verdict

La vision est **directionnellement juste** : unité de pilotage = le bot, budget continu plutôt
qu'ON/OFF, forward-test glissant pour refermer la boucle, registre virtuel. Ces fondations tiennent.

Les faiblesses sont concentrées sur **deux promesses trop fortes** : (a) l'isolation « garantie par
l'exchange » qui ne survit pas au netting, et (b) la « fidélité au backtest » présentée comme
*automatique* alors que le netting, les transferts et le stop global réintroduisent des écarts
live-only. Le reste — circularité du score, transitions corrélées, dead-man vs process unique,
hiérarchie de notifications — relève de **spécifications manquantes**, pas d'erreurs de cap.

Recommandation : avant tout code de netting, trancher le couple **netting ↔ fidélité**. Deux options
cohérentes existent ; la vision actuelle les mélange.

- **Option A — Bots vraiment indépendants (pas de netting).** Chaque bot à levier = sa propre paire
  de cotation (BTC/USDT vs BTC/USDC) ou son moment ; on paie le hedge des deux côtés mais le backtest
  reste **réellement** fidèle et la compta triviale. Simple, robuste, légèrement plus cher.
- **Option B — Netting assumé.** On gagne l'économie de hedge mais on accepte une compta logicielle
  complète, des stops logiciels + un filet exchange, et une réconciliation industrielle. Puissant,
  nettement plus complexe et risqué.

Choisir A pour démarrer (et garder B comme évolution une fois la boucle forward-test/budget prouvée)
est le chemin le moins risqué.
