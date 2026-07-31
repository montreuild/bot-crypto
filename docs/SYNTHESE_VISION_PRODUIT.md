# Synthèse — Vision produit & plan de changements

> Document d'entrée qui consolide les analyses de la refonte. Lecture dans l'ordre pour le détail :
> 1. `ANALYSE_CYCLE_DE_VIE_STRATEGIES.md` — état des lieux du bot actuel.
> 2. `VISION_CIBLE_BOTS_AUTONOMES.md` — la cible (portefeuille de bots autonomes).
> 3. `REVUE_CRITIQUE_VISION_CIBLE.md` — incohérences/risques relevés + corrections.
> 4. `CHOIX_EXCHANGE_ET_NETTING.md` — spot/margin vs perpétuels, Binance vs OKX.
>
> Le présent document décrit la vision **corrigée** (revue critique intégrée) et le plan d'exécution.

---

## 1. La vision produit en une phrase

Transformer le bot — aujourd'hui « multi-stratégies avec activation manuelle et garde-fous de risque
éparpillés » — en un **portefeuille de bots autonomes piloté comme un fonds pilote ses traders** :
l'utilisateur décide du capital et du profil de risque, le système recrute, évalue, dote et retire
les bots tout seul, et **explique chaque décision en une phrase**.

## 2. Les 5 idées directrices

1. **Le bot est l'unité de tout.** Un bot = `(stratégie, timeframe, params figés, version, venue)`.
   Il a son budget propre, sa courbe d'équité, son sizing sur **son** budget, et prend **tous** ses
   signaux tant qu'il a du budget (fidélité au backtest). Il ne peut perdre que son budget.
2. **Budget continu, pas d'ON/OFF.** Le score d'un bot monte → son budget monte ; il baisse → le
   budget fond ; sous un plancher → retrait + re-optimisation. Les « états » (Candidat / Essai /
   Actif / Retiré) ne sont qu'une **lecture humaine** de la trajectoire du budget.
3. **Le déterminant d'activation = forward-test glissant + réalisation live.** Ni l'OOS figé (photo
   périmée) ni le backtest complet (overfitté). On re-backteste chaque jour les params figés sur les
   données fraîches, et on compare les trades réels à une **fourchette Monte-Carlo glissante**.
4. **La méta-couche alloue, elle ne trade pas.** Elle répartit les budgets selon le score, applique
   un malus de corrélation, garde une réserve, et n'a qu'**un seul veto global** : le kill-switch
   catastrophe. Les ex-circuit-breakers deviennent des entrées du score d'allocation.
5. **L'UI raconte une équipe de bots, pas du YAML.** L'utilisateur ne choisit jamais un seuil ; il
   voit l'état de chaque bot, sa confiance (réel vs simulation), et garde un droit de veto.

## 3. Corrections clés issues de la revue critique (à intégrer dès le départ)

| Sujet | Correction |
|---|---|
| Score | **Indépendant du budget** (rendement % / R-multiple / Sharpe), jamais le PnL absolu → casse la circularité budget→PnL→score→budget. |
| Fourchette MC | **Glissante** (recalculée avec le forward-test), jamais figée à la création. |
| Régime de marché | **Plancher de bots actifs** + lissage des transitions (quota de rétrogradations/jour, file de re-opts) → évite le flush général + la tempête de re-optimisations. |
| Netting | Choix explicite **fidélité vs netting** (cf. §5). Démarrer Option A (bots indépendants), netting plus tard via perps hedge mode. |
| Stops | **Deux niveaux** : logiciels par bot + stop persistant sur le net côté exchange. Dead-man switch ⇒ **watchdog séparé** (un process unique ne peut pas se surveiller lui-même). |
| Sizing | Cap **notional ≤ budget × levier** ; `risk%` rattaché au profil du bot. |
| Entraînement | **Deflated Sharpe** au gate de naissance (corrige le biais des 40 trials) ; ≥ 10 trades OOS ; walk-forward dans la décision d'apply. |
| Notifications | **3 niveaux** (info / avertissement / critique) + throttling ; le **mismatch de réconciliation** est une alerte critique. |
| Identité | `(strat, tf, hash, génération, venue)` — génération monotone anti-collision. |

## 4. Décision d'exchange

- Le vrai choix n'est pas « Binance ou OKX » mais **« spot/margin ou perpétuels »**.
- Le **netting** (réduire les frais entre bots opposés) est **natif en perpétuels hedge mode** :
  l'exchange tient une position par bot, pas de réconciliation maison. OKX/Bybit (compte unifié)
  sont plus élégants que Binance pour ça, sans écart décisif.
- **Préalable bloquant aux perps : modéliser le funding dans le backtest** (sinon on récrée l'écart
  live/backtest qu'on cherche à fermer).
- Recommandation : garder l'abstraction **CCXT**, faire du `venue` (modèle de marché + exchange) un
  attribut **par bot**, démarrer en spot/Option A, ajouter un venue « perp hedge OKX » ensuite.

## 5. Plan de changements (phasé, incrémental, réversible)

### Phase 0 — Fondations observationnelles (aucun impact trading)
- **Forward-test glissant** : job quotidien qui re-backteste chaque slot actif sur 30–60 j avec ses
  params figés. _Touche : `app/engine/backtest.py` (réutilisé), nouveau scheduler dans
  `app/live/live_trader.py`._
- **Score budget-indépendant** : refonte de `composite_score`. _Touche : `app/engine/opt_scoring.py`._
- **Contrat Monte-Carlo glissant** : brancher le `MonteCarloSimulator` (existant mais inutilisé) +
  comparer les trades réels. _Touche : `app/engine/backtest.py`, nouveau `app/core/oos_tracker.py`._
- **Durcissement optimiseur** : Deflated Sharpe, ≥ 10 trades, WF dans l'apply. _Touche :
  `app/engine/auto_optimizer.py`, `app/engine/optimizer.py`._

### Phase 1 — Le bot comme unité
- **Identité de bot** versionnée + attribut `venue`. _Nouveau module ; remplace `margin_mode` /
  `max_leverage` globaux de `config.yaml`._
- **Budget virtuel par bot** : sizing sur le budget du bot. _Touche : `app/live/capital_allocator.py`
  (`SlotBudget` déjà présent), `app/core/risk.py` (`compute_size`)._
- **Suppression progressive des vetos globaux** (`max_positions`, pauses CB) → d'abord en paper,
  mesurer l'écart. _Touche : `app/core/risk.py`, `app/live/live_trader.py`._

### Phase 2 — Cycle de vie & allocation automatiques
- **Machine à états** Candidat/Essai/Actif/Retiré + transitions persistées. _Nouveau
  `app/live/slot_lifecycle.py` ; stats live par bot depuis `app/core/database.py`._
- **Allocation continue** pilotée par le score (malus corrélation, réserve, minimums exchange,
  variation bornée ±25 %, plancher de bots actifs). D'abord en **shadow** (afficher ce que
  l'allocateur _aurait_ fait). _Touche : `app/live/capital_allocator.py`._
- **Garde-fou de rebalance** (ne pas retirer de collatéral d'un wallet à position ouverte).

### Phase 3 — Sécurité & résilience
- **Stops à deux niveaux** + **watchdog séparé** (dead-man) + **kill-switch** d'équité persistant.
- **Persistance de l'état risque** (compteurs, pauses) en DB pour reprise propre. _Touche :
  `app/core/risk.py`, `app/core/database.py`._

### Phase 4 — Refonte UI (peut démarrer dès la Phase 0)
- 5 pages : **Portefeuille** (santé, allocation, fil d'activité), **Mes Bots** (kanban par état,
  fiche bot avec cône MC vs réel), **Laboratoire** (optimisation → verdict clair → « Créer le bot »),
  **Marché** (scanner), **Réglages** (3 presets de risque, mode expert opt-in). _Touche :
  `app/web/templates/*`, `app/api/routes/*`._
- **Hiérarchie de notifications** 3 niveaux + throttling, miroir Telegram/UI.

### Phase 5 — Netting natif (optionnel, après preuve de la boucle)
- **Funding modélisé dans le backtest** (préalable bloquant).
- **Venue « perp hedge » (OKX)** via CCXT pour les bots à short/levier → netting natif, **zéro
  moteur de réconciliation maison**.

---

## 6. Règle d'or

Chaque phase produit de la valeur seule et est réversible. **Phase 0 d'abord** : c'est la donnée
(« le live confirme-t-il la simulation ? ») qui rend tout le reste possible. Ne jamais automatiser
une décision d'allocation tant que cette donnée n'est pas fiable — sinon on automatise du bruit.
