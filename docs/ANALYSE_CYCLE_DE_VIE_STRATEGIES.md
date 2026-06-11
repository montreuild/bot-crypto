# Analyse complète — Cycle de vie automatique des stratégies

> Activation/désactivation automatique, pertinence OOS ↔ backtest ↔ live, gestion des slots,
> gestion du risque, répartition budgétaire et expérience utilisateur.
>
> Date : 2026-06-11 — basée sur l'état actuel du code (`app/engine`, `app/live`, `app/core`, `app/web`).

---

## 1. État des lieux : ce qui existe déjà

### 1.1 Activation / désactivation des stratégies (aujourd'hui : 100 % manuel)

Trois mécanismes se superposent actuellement :

| Niveau | Mécanisme | Stockage | Automatique ? |
|---|---|---|---|
| Stratégie | `enabled: true/false` | `strategies/{nom}.yaml` (lu par `app/core/config.py:73-116`) | ❌ Manuel (page Config ou YAML) |
| Slot (stratégie × TF) | `disabled_slots` | `config.yaml` → `capital_allocator.disabled_slots` | ❌ Manuel (page Slots, API toggle) |
| Slot (sélection par TF) | Top-N par score OOS | `get_active_strategies_per_tf()` (`app/engine/opt_persistence.py:223-292`) | ⚠️ Semi-auto : top `top_strategies_per_tf: 2` par TF, seuil `MIN_VIABLE_SCORE = -0.05` |
| Slot (pause temporaire) | Circuit breakers | En mémoire (`app/core/risk.py`) | ✅ Auto, mais temporaire (30 min / minuit UTC / 24 h) |

**Constat clé** : il existe déjà une brique de sélection automatique (`get_active_strategies_per_tf`)
pilotée par le score OOS de l'optimiseur, mais :
- elle ne s'exécute qu'au (re)chargement de la config, pas en continu ;
- le seuil `-0.05` accepte des stratégies *légèrement perdantes* en OOS ;
- la performance **live/paper réelle** n'entre jamais dans la décision : une stratégie qui perd en
  live reste active tant que son vieux score OOS est bon ;
- aucune désactivation définitive automatique n'existe — seules les pauses circuit-breaker, qui se
  réarment toutes seules.

### 1.2 Optimiseur et OOS

- Split IS/OOS **65/35** (`auto_optimizer.py:216-219`), Walk-Forward 5 folds (`backtest.py:883-957`),
  Monte-Carlo disponible mais **non utilisé** dans la sélection de l'optimiseur.
- Détection d'overfitting : ratio IS/OOS, pénalité au-delà de **2.5** (`optimizer.py:147-155`).
- Score composite (`opt_scoring.py:11-73`) : Sharpe 22 %, PnL 20 %, WR 15 %, PF 15 %, DD 10 %,
  nb trades 10 %, expectancy 8 %, bonus alpha ±1.0, malus ×0.3 si PnL ≤ 0. Minimum **2 trades**.
- Auto-apply conditionnel (`auto_optimizer.py:355-401`) : ≥ 3 trades OOS, PnL OOS > 0,
  bat la baseline en PnL **et** en WR ou Sharpe. Audit dans `optimizer_changelog.json` (200 entrées).

### 1.3 Slots et budget

- Slot = `stratégie::timeframe` (`app/live/capital_allocator.py`). Nombre = stratégies actives × TFs.
- Modes d'allocation : `equal` (défaut), `manual` (`slot_budgets`, 11 slots × 0.0909 dans la config
  actuelle), `performance` (PF 7 j : ×1.3 si PF > 1.5, ×0.75 si PF < 0.8, rebalance quotidien,
  min 3 trades).
- Garde-fous : `max_slot_pct: 0.50`, `max_symbol_exposure_pct: 0.25`, `max_pyramiding: 2`,
  refus d'entrée si budget de slot épuisé (tolérance 5 %).

### 1.4 Risque

- Global : DD journalier 5 % → HALT, DD max 20 % → HALT (reset manuel), volatility brake
  (ATR BTC > 5 % → tailles ×0.5), max 5 positions (3 longs / 3 shorts), 3 trades/min.
- Par slot : 3 pertes consécutives → pause 30 min ; DD slot 3 %/jour → pause jusqu'à minuit UTC ;
  WR < 25 % sur 15 trades → pause 24 h ; max 5 trades/jour.
- Sizing live : `risk_amount / ATR` avec réduction dynamique selon le DD (×0.75 si DD > 5 %,
  ×0.5 si DD > 10 %) + facteur de score + brake.
- Trailing multi-phases (GRACE → WIDE → BREAKEVEN → LOCK → TIGHT) partagé live/backtest.

---

## 2. Activation / désactivation automatique : conception proposée

### 2.1 Principe : une machine à états par slot

Aujourd'hui les états sont implicites et éparpillés (enabled global, disabled_slots, pauses CB,
top-N OOS). La proposition est de les unifier dans **un cycle de vie explicite par slot**, persisté
(table SQL `slot_lifecycle` ou section YAML), avec ces états :

```
 CANDIDATE ──(optimisation OK)──► INCUBATION ──(validation paper)──► ACTIVE
     ▲                                 │                                │
     │                            (échec)                          (dégradation)
     │                                 ▼                                ▼
     └────────(re-optimisation)─── RETIRED ◄──(probation échouée)── PROBATION
                                                                        │
                                                              (récupération) ─► ACTIVE
```

| État | Signification | Trading | Budget |
|---|---|---|---|
| `CANDIDATE` | Découverte, jamais validée | Non | 0 % |
| `INCUBATION` | Score OOS validé, tourne en **paper shadow** (signaux trackés sans capital réel ou avec un micro-budget) | Paper uniquement | 0–2 % |
| `ACTIVE` | Validée, trade avec budget normal | Oui | Selon allocateur |
| `PROBATION` | Dégradation détectée — budget réduit, surveillance renforcée | Oui (réduit) | ×0.5 |
| `RETIRED` | Désactivée automatiquement, en attente de re-optimisation | Non | 0 % |

Chaque transition est journalisée (horodatage, raison, métriques) — c'est ce journal qui rend le
système **compréhensible** côté UI (voir §7).

### 2.2 Règles de promotion (CANDIDATE → INCUBATION → ACTIVE)

Réutiliser les briques existantes en durcissant les seuils :

1. **CANDIDATE → INCUBATION** : job d'optimisation terminé avec
   - score OOS > 0 (et non `-0.05` comme actuellement — accepter une stratégie perdante en OOS
     n'a pas de justification) ;
   - ratio d'overfit ≤ 2.0 ;
   - **≥ 10 trades OOS** (le minimum actuel de 2–3 trades n'a aucune valeur statistique) ;
   - Walk-Forward : score OOS positif sur ≥ 3 folds sur 5 (le `WalkForwardAnalyzer` existe déjà,
     il suffit de l'appeler dans `auto_optimizer` avant l'apply).
2. **INCUBATION → ACTIVE** : après N trades paper (ex. 10) ou N jours (ex. 14, premier atteint) :
   - PnL paper ≥ 0 **ou** dans la bande de confiance Monte-Carlo de l'OOS (cf. §3.3) ;
   - pas de pause circuit-breaker > 2 dans la période.

### 2.3 Règles de rétrogradation (ACTIVE → PROBATION → RETIRED)

Évaluées par un **job périodique** (ex. toutes les 6 h, dans la boucle du LiveTrader qui héberge
déjà le scheduler auto-optimizer, `live_trader.py:124-126`) :

- **ACTIVE → PROBATION** si, sur fenêtre glissante (ex. 20 derniers trades ou 14 jours) :
  - PnL réalisé < borne basse de l'intervalle Monte-Carlo issu de l'OOS (divergence OOS/live,
    cf. §3.3), **ou**
  - PF < 0.8 avec ≥ 8 trades, **ou**
  - 3ᵉ pause circuit-breaker du même type en 7 jours (signal fort que le CB compense un problème
    structurel — aujourd'hui cette information est perdue à chaque réarmement).
- **PROBATION → RETIRED** si pas de récupération après N trades/jours supplémentaires.
- **PROBATION → ACTIVE** si les métriques repassent au-dessus des seuils.
- **RETIRED → CANDIDATE** : l'auto-optimizer planifie automatiquement une re-optimisation du couple
  (stratégie, TF) ; si le nouveau score OOS valide §2.2, le slot repart en INCUBATION.

### 2.4 Garde-fous indispensables

- **Hystérésis et cooldown** : un slot rétrogradé ne peut pas être re-promu avant X jours, pour
  éviter le flapping (activation/désactivation en boucle sur du bruit).
- **Plancher de slots actifs** : ne jamais descendre sous N slots ACTIVE (sinon le bot s'éteint
  tout seul en marché difficile) — au pire tout passe en INCUBATION/paper, jamais en arrêt brutal
  non signalé.
- **Override manuel prioritaire** : un toggle utilisateur (`force_on` / `force_off`) gèle la machine
  à états et l'affiche clairement (« géré manuellement »).
- **Tout est journalisé et notifiable** (Telegram déjà branché : événement `on_optimization` existe,
  ajouter `on_slot_promoted` / `on_slot_demoted`).

### 2.5 Implémentation : où brancher

| Brique | Fichier existant | Modification |
|---|---|---|
| États + transitions | nouveau `app/live/slot_lifecycle.py` | Machine à états, persistance DB |
| Évaluation périodique | `app/live/live_trader.py` (boucle existante) | Appel toutes les N heures |
| Stats live par slot | `app/core/database.py` (table `Trade` a déjà `strategy` + `timeframe`) | Requête agrégée par slot |
| Promotion post-optimisation | `app/engine/auto_optimizer.py:355-401` (`_beats_baseline`) | Remplacer apply direct par transition INCUBATION |
| Budget par état | `app/live/capital_allocator.py:84-122` (`rebuild_slots`) | `budget × facteur(état)` |
| Filtrage trading | `app/core/risk.py:159-177` (`can_slot_trade`) | + check état lifecycle |

---

## 3. Pertinence OOS ↔ backtest ↔ live : le maillon manquant

### 3.1 Diagnostic

Le pipeline IS/OOS est sain en amont (split 65/35, pénalité overfit 2.5, exécution à l'open de la
bougie suivante donc pas de lookahead, frais/spread/borrow réalistes). **Mais la boucle n'est jamais
refermée** : rien ne compare la promesse OOS à la réalité live/paper. Concrètement :

- `optimizer_changelog.json` stocke le `oos_score` au moment de l'apply ;
- `backtest_history.py` stocke le dernier backtest par slot ;
- la table `Trade` stocke chaque trade live avec stratégie + TF ;
- **aucun module ne croise les trois.** C'est la lacune n° 1 du système, et c'est exactement la
  donnée dont la machine à états du §2 a besoin.

### 3.2 Pourquoi « OOS bon » ne suffit pas

- **2–3 trades OOS minimum** : à ce niveau, le score OOS est essentiellement du bruit. Avec 40
  trials d'optimisation, on sélectionne mécaniquement le tirage chanceux (biais de sélection
  multiple, non corrigé : pas de White Reality Check / Deflated Sharpe).
- **Le score composite mélange trop de choses** : le bonus alpha pèse jusqu'à ±1.0 soit autant que
  tout le reste combiné — en marché haussier, il favorise les stratégies « long & lucky ».
- **Non-stationnarité** : un OOS de 35 % sur 1 500 bougies 1h ≈ 22 jours. Le régime de marché des
  22 jours suivants peut être différent ; sans suivi post-déploiement, on ne le sait jamais.
- **Divergence de sizing** : le live réduit le risque dynamiquement selon le DD (×0.5 / ×0.75),
  le backtest non (`risk.py:270-304` vs `backtest.py:462-580`). Même avec des signaux identiques,
  le PnL live ne reproduira pas le PnL OOS. → soit porter le scaling DD dans le backtest, soit
  comparer en « R multiples » (PnL / risque engagé) plutôt qu'en USDC.

### 3.3 Proposition : un « tracker de réalisation » (OOS Realization Tracker)

Nouveau module `app/core/oos_tracker.py` :

1. **À l'apply** d'un résultat d'optimisation, figer un *contrat de performance* :
   distribution des trades OOS (déjà disponible), et via le `MonteCarloSimulator` existant
   (`backtest.py:960-992`, actuellement inutilisé !) calculer l'intervalle de confiance à 90 %
   du PnL attendu pour n trades.
2. **En continu**, comparer les n premiers trades live/paper du slot à cet intervalle :
   - dans la bande → conforme ✅ ;
   - sous la borne basse → divergence ⚠️ → événement consommé par la machine à états (§2.3) ;
   - calculer un **ratio de réalisation** = métrique live / métrique OOS (Sharpe, PF, expectancy),
     analogue au ratio d'overfit IS/OOS déjà affiché.
3. **Historiser** ces ratios pour répondre à la vraie question : *« sur ce bot, un score OOS de X
   prédit-il quoi en live ? »*. Après quelques mois, on peut calibrer le seuil de promotion
   empiriquement (ex. « les slots avec OOS > 0.5 ont réalisé un PnL positif dans 78 % des cas »).

### 3.4 Durcissements complémentaires de l'optimiseur

- Relever `min_trades` du score composite (2 → 10) et le minimum d'auto-apply (3 → 10 trades OOS).
- Intégrer le Walk-Forward dans la décision d'apply (consistance inter-folds), pas seulement comme
  option d'affichage du backtest.
- Pénaliser l'instabilité des paramètres : si les top-5 trials ont des paramètres très éloignés
  pour des scores proches, le maximum est probablement un pic d'overfit (mesure de stabilité du
  voisinage déjà calculable depuis les résultats stockés).
- Supprimer le seuil `MIN_VIABLE_SCORE = -0.05` (accepter du négatif n'a pas de sens) ou le passer
  à un seuil strictement positif configurable.

---

## 4. Gestion des slots : recommandations

1. **Le slot devient l'unité de pilotage unique.** Aujourd'hui trois notions se chevauchent
   (stratégie enabled, slot disabled, top-N par TF) — source de confusion majeure (« la stratégie
   est active mais le slot ne trade pas »). Avec la machine à états du §2, l'état du slot devient
   la seule vérité ; `enabled` au niveau stratégie ne sert plus qu'à exclure une stratégie de la
   découverte/optimisation.
2. **Limiter le nombre de slots ACTIVE simultanés** (ex. 8–12) plutôt que stratégies × TFs : avec
   10 stratégies × 6 TFs = 60 slots potentiels en mode `equal`, chaque slot reçoit ~1,7 % de
   1 000 USDC ≈ 17 USDC — sous les minimums de notional Binance. Le top-N par TF existant va dans
   ce sens ; le généraliser en « top-N global pondéré par TF ».
3. **Corrélation entre slots** : `check_correlation()` ne regarde que le ratio long/short et
   l'exposition par symbole. Deux slots trend-following sur BTC 1h et BTC 4h prendront les mêmes
   trades — ajouter une mesure de corrélation des PnL quotidiens entre slots (fenêtre 30 j) et la
   prendre en compte dans la sélection top-N et dans l'allocation (§6).
4. **Mémoire des circuit breakers** : compter les pauses par slot et par type (aujourd'hui
   en mémoire, perdu au restart) — c'est un signal d'entrée de la rétrogradation (§2.3) et une info
   précieuse à afficher.

---

## 5. Gestion du risque : recommandations

L'existant est solide (deux niveaux global/slot, brake volatilité, trailing multi-phases). Les
écarts à combler, par priorité :

1. **Aligner sizing backtest et live** (cf. §3.2) — sans cela, toute comparaison OOS/live est
   biaisée. Option recommandée : ajouter le scaling DD dans le backtest (faible effort, les deux
   utilisent déjà `app/core/execution.py`).
2. **Budget de risque global plutôt que par-position uniquement** : 5 positions × risque 1 % = 5 %
   de risque ouvert simultané, exactement la limite de DD journalier. Introduire un plafond de
   « risque ouvert total » (ex. 3 %) vérifié dans `can_trade()`.
3. **Désendettement progressif** : avant le HALT brutal à 5 % de DD journalier, réduire les tailles
   (le mécanisme existe déjà pour le DD global via `compute_risk()` — l'étendre au DD journalier)
   et envisager la réduction des positions ouvertes lors du déclenchement du volatility brake
   (aujourd'hui il ne touche que les nouvelles entrées).
4. **Corrélation cross-asset** : 3 longs BTC+ETH+SOL ≈ une seule position de 3× la taille. Au
   minimum, un facteur de réduction quand plusieurs positions du même côté sur des actifs corrélés.
5. **Stop temporel** : aucune limite de durée de détention n'existe ; ajouter un `max_bars_held`
   optionnel par slot (les stratégies mean-reversion en ont particulièrement besoin).
6. **Persister l'état risque** : `risk.halted`, compteurs de pertes consécutives et pauses de slots
   sont en mémoire — un restart efface tout. À persister en DB pour la cohérence et pour l'historique.

---

## 6. Répartition budgétaire : recommandations

Le mode `performance` existant (PF 7 j → ×1.3 / ×0.75) est un bon embryon. Proposition d'évolution,
intégrée à la machine à états :

1. **Allocation = f(état, performance, corrélation)** :
   - base par état : INCUBATION 0–2 %, PROBATION = part normale ×0.5, ACTIVE = part normale ;
   - part normale : départ égalitaire, puis ajustement progressif type **PF lissé sur 30 j** avec
     bornes (±25 % de variation max par rebalance) plutôt que les marches brutales actuelles
     (×1.3/×0.75 sur 7 j et 3 trades minimum = très bruité) ;
   - malus de corrélation : deux slots corrélés > 0.7 se partagent une enveloppe commune.
2. **Rebalance borné et journalisé** : garder l'intervalle quotidien, mais ne jamais retirer du
   budget à un slot ayant une position ouverte qui dépasserait le nouveau plafond (laisser
   l'enveloppe se résorber à la clôture) — à vérifier dans `_rebalance()`.
3. **Réserve de trésorerie** : garder X % (ex. 10 %) non alloué, tampon pour le slippage, les
   frais, et les minimums d'ordre.
4. **Respect des minimums exchange** : refuser d'activer un slot dont le budget × max_notional
   produirait des ordres sous le minimum Binance (~5–10 USDC) — aujourd'hui rien ne l'empêche.
5. À plus long terme, un mode « risk parity » simple (budget inversement proportionnel à la
   volatilité des PnL du slot) est plus robuste que le PF pur, qui est instable à faible nombre
   de trades.

---

## 7. Expérience utilisateur : rendre le système compréhensible

### 7.1 Problème central

L'UI actuelle (5+ pages riches) expose les *mécanismes* (YAML, slots, scores, overfit) mais pas le
*raisonnement*. Trois notions d'activation coexistent sans vue unifiée, le jargon (OOS, IS, overfit,
slot_key, PF) n'est pas expliqué, et il n'y a aucune réponse visuelle à la question que se pose
l'utilisateur : **« pourquoi cette stratégie trade-t-elle (ou pas), et est-ce que je peux lui faire
confiance ? »**

### 7.2 Principes directeurs

1. **Une seule vérité visible : l'état du cycle de vie.** Remplacer les 4 badges actuels
   (Live / En attente / En config / Inactive + pauses CB) par les 5 états du §2, avec un code
   couleur et une icône constants partout (Dashboard, Slots, Config) :
   - 🟢 **Active** — « trade normalement »
   - 🔵 **En essai** (incubation) — « validée en backtest, en observation paper »
   - 🟠 **Sous surveillance** (probation) — « performance en retrait, budget réduit »
   - 🔴 **Retirée** — « désactivée le 12/06 : PnL réel sous la fourchette attendue »
   - ⚪ **Candidate / Manuelle** — « jamais validée » / « gérée manuellement par vous »
2. **Chaque état affiche sa raison et sa prochaine étape**, en français simple :
   « En essai depuis 6 jours — passera Active après 10 trades paper positifs (7/10) », avec une
   barre de progression. C'est le journal de transitions (§2.1) qui alimente cela.
3. **La promesse vs le réel, en un graphique.** Sur chaque slot : la courbe d'équité live superposée
   au **cône Monte-Carlo de l'OOS** (§3.3). Lecture immédiate : dans le cône = conforme, sous le
   cône = divergence. C'est LA visualisation qui rend le lien OOS/live intuitif, et elle ne demande
   que des données déjà calculables.
4. **Traduire le jargon, garder les chiffres.** « Overfit 3.2 ⚠ » → « Résultat probablement trop
   optimiste : la stratégie marche 3× mieux sur les données d'entraînement que sur les données de
   test. Conseil : relancer avec plus de bougies. » Tooltips systématiques (composant unique) sur
   OOS, IS, Sharpe, PF, DD.
5. **Mode simple par défaut, mode expert en opt-in.** La page Config actuelle (9 panneaux, dizaines
   de champs) reste en « mode expert ». Le mode simple expose 3 décisions : profil de risque
   (Prudent / Équilibré / Agressif → presets de risk_per_trade, DD limits, CB), pilotage
   automatique des stratégies ON/OFF, et capital. Tout le reste a des valeurs par défaut saines.
6. **Feedback systématique** : toasts de confirmation après chaque apply (avec distinction « appliqué
   au bot en cours » vs « enregistré pour le prochain démarrage »), confirmation avant désactivation
   d'un slot ayant une position ouverte, validation de type/bornes sur les champs de paramètres.

### 7.3 Refonte de la page Slots → « Stratégies »

Fusionner la gestion d'activation des pages Config et Slots en une page unique « Stratégies » :

- **Vue kanban par état** (Candidates | En essai | Actives | Surveillance | Retirées) plutôt que
  par timeframe — l'utilisateur pense « est-ce que ça marche ? » avant « quel TF ? » ;
- chaque carte : nom + TF, sparkline PnL 30 j, indicateur conforme/divergent vs OOS, budget
  (lecture seule en mode auto, slider en mode manuel), bouton « Geler en manuel » ;
- un fil d'activité global : « 12/06 14:30 — breakout::1h passé en Surveillance (PF 0.6 sur 12
  trades) », « 11/06 — fear_momentum::4h promu Actif » — le même flux part vers Telegram ;
- la page Config ne garde que les paramètres (risque, notifications, exchange).

### 7.4 Dashboard

- Bandeau de santé unique : état global (Normal / Brake volatilité / HALT) avec phrase explicative
  et action proposée (« HALT : perte journalière de 5 % atteinte. Reprise automatique demain 00:00
  UTC, ou réarmez manuellement. »)
- Risque consommé du jour : jauge « 2,1 % perdus / 5 % autorisés » (existe à moitié, à légender) ;
- raison de pause des slots toujours affichée avec l'heure de reprise.

---

## 8. Feuille de route priorisée

| # | Chantier | Effort | Impact | Dépend de |
|---|---|---|---|---|
| 1 | **OOS Realization Tracker** (§3.3) : contrat de perf à l'apply + comparaison live, ratio de réalisation | M | ⭐⭐⭐ Fondation de tout le reste | — |
| 2 | Durcissement optimiseur (§3.4) : min 10 trades OOS, WF dans l'apply, suppression du seuil -0.05 | S | ⭐⭐⭐ | — |
| 3 | Alignement sizing backtest/live (§5.1) | S | ⭐⭐⭐ | — |
| 4 | **Machine à états des slots** (§2) avec INCUBATION paper et rétrogradation auto | L | ⭐⭐⭐ | 1, 2 |
| 5 | Allocation par état + PF lissé 30 j + réserve + minimums exchange (§6) | M | ⭐⭐ | 4 |
| 6 | Persistance de l'état risque + compteurs CB (§5.6) | S | ⭐⭐ | — |
| 7 | Page « Stratégies » unifiée + cône OOS vs live + fil d'activité (§7) | L | ⭐⭐⭐ UX | 1, 4 |
| 8 | Risque ouvert global + désendettement progressif + corrélation cross-asset (§5.2-5.4) | M | ⭐⭐ | — |
| 9 | Mode simple / presets de risque + tooltips + toasts (§7.2) | M | ⭐⭐ | — |

S = quelques jours, M = 1–2 semaines, L = 3+ semaines (ordres de grandeur).

**Recommandation de démarrage** : les chantiers 1 + 2 + 3 sont indépendants, courts, et corrigent
les faiblesses qui invalideraient tout le reste (scores OOS non significatifs, divergence de sizing,
absence totale de boucle de retour). La machine à états (4) ne doit être construite qu'une fois la
donnée « le live confirme-t-il l'OOS ? » fiable — sinon on automatise des décisions sur du bruit.
