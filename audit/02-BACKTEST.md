# Audit — Moteur de backtest

> Périmètre : `app/engine/backtest.py` (1 809 lignes), `backtest_risk_gate.py`,
> `walk_forward.py`, `monte_carlo.py`, `forward_test.py`, `regime_stress_test.py`,
> `app/core/is_oos.py`, `app/api/routes/backtest.py`.
>
> Les constats purement monétaires (frais, PnL, Sharpe, Monte-Carlo) sont dans
> [`01-FINANCIER.md`](01-FINANCIER.md) et référencés ici sous `F-xx`.

---

## Tableau de bord

| # | Sévérité | Titre | Fichier |
|---|----------|-------|---------|
| B-01 | 🔴 Critique | Les stops et TP sont remplis au niveau, jamais au gap | `backtest.py:928-944` |
| B-02 | 🔴 Critique | Une seule position à la fois : le backtest ne simule pas le portefeuille | `backtest.py:1611-1613` |
| B-03 | 🟠 Majeur | Walk-forward : `timeframe` jamais transmis au `Backtester` | `walk_forward.py:103-104` |
| B-04 | 🟠 Majeur | Walk-forward ne réoptimise rien — ce n'est pas un walk-forward | `walk_forward.py:82-110` |
| B-05 | 🟠 Majeur | `min_notional` vérifié avant `partial_fill`, jamais après | `backtest.py:1184-1210` |
| B-06 | 🟠 Majeur | Le pyramidage ignore la courbe de dé-risquage et le circuit breaker | `backtest.py:1022-1050` |
| B-07 | 🟡 Moyen | `realistic_risk` désactivé par défaut : les circuit breakers ne sont jamais simulés | `backtest.py:563,581` |
| B-08 | 🟡 Moyen | Aucun embargo entre IS et OOS | `core/is_oos.py:29-40` |
| B-09 | 🟡 Moyen | Trois constantes de warmup indépendantes (210 / 220 / 250) | 3 fichiers |
| B-10 | 🟡 Moyen | Clôture de fin de série sans spread ni frais taker | `backtest.py:1643-1645` |
| B-11 | 🟡 Moyen | `capital_before` incohérent après sorties partielles | `backtest.py:788-790` |
| B-12 | 🔵 Mineur | `rejected_notional` agrège trois causes distinctes | `backtest.py:1154,1186,1202` |
| B-13 | 🔵 Mineur | Buy & Hold démarre au `close[warmup]`, le bot à `open[warmup+1]` | `backtest.py:1723` |
| B-14 | 🔵 Mineur | Walk-forward renvoie l'intégralité des trades de chaque fold | `walk_forward.py:126-127` |

---

## B-01 🔴 Les stops sont remplis au niveau, jamais au gap

### Constat

`_manage_open_position` (`backtest.py:879-945`) :

```python
stop_hit = (side == "long"  and c_low  <= stop) or \
           (side == "short" and c_high >= stop)
...
if stop_hit:
    exec_price = stop * (1 - self.spread_pct) if side == "long" \
                 else stop * (1 + self.spread_pct)
```

Le prix d'exécution est **toujours** dérivé du niveau de stop, quelle que soit
l'ouverture de la barre. Si la bougie ouvre à 5 % sous le stop (gap), le backtest
remplit quand même au stop moins le spread (0,05 %), alors qu'un ordre stop-market
réel serait rempli à l'ouverture.

Même chose pour le take-profit (`backtest.py:928-931`) et pour les cibles
partielles (`backtest.py:956-958`).

### Pourquoi c'est critique ici

1. **Le crypto gappe.** Les liquidations en cascade produisent des bougies 1h/4h
   dont l'ouverture est loin de la clôture précédente. Le backtest efface
   exactement les pires trades.
2. **Le dépôt vise aussi les actions** (`venues.euronext-paper`, calendrier
   XPAR). Sur actions, le gap d'ouverture est la règle, pas l'exception : entre
   17h30 et 9h00 il ne se passe rien, et le stop d'un backtest journalier est
   systématiquement rempli au niveau exact — un biais optimiste massif sur toute
   stratégie actions.
3. Le code sait déjà gérer un cas voisin : `exit_reason="gap"` existe dans le
   vocabulaire live (`position_close_mixin.py:151`). Le backtest n'a pas
   l'équivalent, donc `by_exit_reason` ne peut pas révéler le problème.

### Correction proposée

```python
if stop_hit:
    o = ctx.open_arr[i]
    # Un gap franchit le stop avant que l'ordre puisse s'exécuter au niveau.
    gapped = (side == "long" and o < stop) or (side == "short" and o > stop)
    ref = o if gapped else stop
    exec_price = ref * (1 - self.spread_pct) if side == "long" \
                 else ref * (1 + self.spread_pct)
    reason = "gap" if gapped else ("stop_loss" if ... else "trailing_stop")
```

Symétriquement pour le TP : un gap **favorable** doit remplir à l'ouverture
(gain supérieur), sinon on introduit un biais dans l'autre sens. `open_arr`
n'est pas encore extrait dans `ctx` — une ligne à ajouter à côté de
`close_arr` (`backtest.py:1474-1477`).

Ajouter `gap` à `by_exit_reason` rendra l'ampleur du phénomène mesurable sur
l'historique existant.

---

## B-02 🔴 Une seule position à la fois

### Constat

La boucle principale (`backtest.py:1611-1613`) :

```python
if position is not None:
    position = self._manage_open_position(ctx, position, i)
    continue          # ← best_signal() n'est même pas appelé
```

Le backtest est **mono-position, mono-symbole, mono-stratégie**. Le live, lui,
gère `open_positions: Dict[str, dict]` avec une position par
`(symbole, stratégie, tf)` et un `RiskLedger` qui arbitre entre elles.

### Ce que cela invalide

| Grandeur | Backtest | Live |
|---|---|---|
| Corrélation entre positions simultanées | absente | réelle |
| `symbol_risk_budget` / `venue_risk_budget` | jamais atteints (un seul risque ouvert) | contraignants |
| `RiskLedger.reserve` | **jamais exercé** | chemin nominal |
| Drawdown de portefeuille | = drawdown d'un bot | somme corrélée |
| Fréquence de trade | sous-estimée (signaux ignorés en position) | réelle |
| `max_trades_per_minute` | absent | contraignant |

Le mode `realistic_risk` (B-07) réplique les *circuit breakers* mais pas la
concurrence : `BacktestRiskGate.can_slot_trade` est interrogé pour un slot alors
qu'un seul slot peut exister.

Conséquence directe sur la décision : `slot_weights` (`risk_envelope.py:52-73`)
répartit les enveloppes selon les edges mesurées **en mono-position**, puis les
applique à un portefeuille **multi-positions**. L'expectancy qui pilote
l'allocation n'a pas été mesurée dans les conditions où elle sera utilisée.

### Correction proposée

Ce n'est pas une correction ponctuelle mais un chantier. Le chemin le moins
coûteux :

1. `positions: Dict[str, dict]` au lieu de `position` dans `run()` ;
2. appeler `best_signal` à chaque barre indépendamment des positions ouvertes,
   avec une clé `pos_key = (symbol, strategy, tf)` comme en live ;
3. brancher le **vrai** `RiskLedger` (pas une copie) : il est déjà sans I/O et
   thread-safe, donc utilisable tel quel dans la boucle ;
4. le cas mono-position devient un cas particulier — les tests de parité
   existants restent valides.

À défaut, il faut **cesser de présenter les métriques de backtest comme
prédictives du portefeuille** : elles décrivent un bot isolé.

---

## B-03 🟠 Walk-forward : le timeframe n'est jamais transmis

`walk_forward.py:103-104` :

```python
r_is   = bt_is.run(df_is,  symbol)      # ← pas de timeframe=
r_oos  = bt_oos.run(df_oos, symbol)
```

`Backtester.run(df, symbol, timeframe=None)` retombe alors sur
`self.cfg["trading"].get("timeframe", "1h")` (`backtest.py:1402, 1496, 1684`).

Trois conséquences :

1. **Annualisation fausse** : `bars_per_year` du TF de config, pas du TF des
   données. Un walk-forward sur du 1d annualisé comme du 1h gonfle le Sharpe
   d'un facteur `√(8760/365) ≈ 4,9`.
2. **Coût d'emprunt faux** : `hours_held = bars_held × _bar_to_days(ctx.timeframe) × 24`
   (`backtest.py:703`) — un fold journalier facturé comme de l'horaire divise le
   portage par 24.
3. **Venue mal résolue** : `_resolve_venue(cfg, tf=None, ...)`
   (`backtest.py:1312`), donc toute assignation par timeframe est ignorée.

Le commentaire `backtest.py:1493-1496` documente précisément ce bug… pour le
chemin `run()`. Le chemin walk-forward ne l'a jamais reçu.

**Correction** : ajouter un paramètre `timeframe` à
`WalkForwardAnalyzer.run(df, symbol, timeframe)` et le propager. Vérifier les
appelants (`api/routes/backtest.py`, `cli.py`, `research/`).

---

## B-04 🟠 Le walk-forward ne réoptimise rien

`WalkForwardAnalyzer.run` découpe la série en folds, puis exécute **les mêmes
paramètres** sur `df_is` et sur `df_oos` de chaque fold. Aucun appel à
`OptimizerSearchEngine`, aucune sélection de paramètres par fold.

Un walk-forward analysis, par définition, (a) optimise sur `IS_k`, (b) évalue les
paramètres retenus sur `OOS_k`, (c) recommence. Ici, `IS_k` et `OOS_k` reçoivent
un traitement identique : le résultat mesure la **stabilité temporelle d'un
paramétrage figé**, ce qui est utile, mais ce n'est pas ce que le nom, la
docstring et l'UI annoncent.

Ce que produit le rapport actuel :

- `avg_oos_sharpe` = moyenne arithmétique de Sharpe par fold. Moyenner des
  Sharpes n'est pas défini (ce ne sont pas des grandeurs additives), et chacun
  souffre de **F-02** (folds courts ⇒ peu de trades ⇒ Sharpe explosif) ;
- `consistency` = % de folds à `total_pnl > 0` — biaisé par **F-01** ;
- `avg_oos_pnl` = moyenne de PnL sur des folds de tailles inégales (le dernier
  fold est tronqué par `min(fold_n*(k+2), n)`).

**Correction** : soit renommer honnêtement (`StabilityAnalyzer`,
`avg_fold_pnl`…), soit implémenter la réoptimisation par fold. La seconde option
est réaliste : `OptimizerSearchEngine` accepte déjà `df_is`/`df_oos` en entrée.

---

## B-05 🟠 `min_notional` vérifié sur la mauvaise taille

`backtest.py:1179-1210`, dans l'ordre :

```python
size     = _floor_to(risk_amount / stop_dist, 6)      # 1179
notional = _floor_to(size * exec_price, 4)            # 1182
if size <= 0 or notional < min_notional:  → refus     # 1185  ← contrôle
size    *= self.partial_fill                          # 1196  ← ×0.95 APRÈS
q_size   = _quantize_size(size, self._venue)          # 1200
size     = q_size
notional = size * exec_price                          # 1210  ← jamais recontrôlé
```

Une position dont le notionnel vaut exactement `min_notional` passe le contrôle,
puis se retrouve à `0,95 × min_notional` (voire moins après quantification par
lot). Sur `euronext-paper` (`min_notional: 200`, `fractional: false`), l'écart
est amplifié par l'arrondi à l'unité entière.

Le live ne reproduit pas la séquence : `compute_size` ne connaît pas
`partial_fill`, et `RiskLedger.reserve` vérifie `notional < env.min_notional`
sur la taille **finale** (`risk_ledger.py:94`). Le backtest laisse donc passer
des trades que le live refuserait — c'est exactement l'écart que la double passe
(`run_dual_pass`) prétend attribuer aux contraintes absolues.

**Correction** : déplacer le contrôle `min_notional` après la quantification,
ligne 1210.

---

## B-06 🟠 Le pyramidage échappe aux garde-fous de l'entrée

`_manage_open_position`, branche `check_scale_in` (`backtest.py:1019-1050`) :

```python
add_size = _base * ctx.risk / stop_dist * sf * self.partial_fill
```

Comparé à l'entrée initiale (`backtest.py:1164-1178`), il manque :

| Garde-fou | Entrée | Scale-in |
|---|---|---|
| `_risk_multiplier(dd)` (courbe de dé-risquage) | ✅ ligne 1168 | ❌ |
| `gate.volatility_brake_factor` | ✅ ligne 1175 | ❌ |
| `gate.can_slot_trade` (circuit breakers) | ✅ ligne 1081 | ❌ |
| `min_notional` de la venue | ✅ ligne 1184 | ❌ (`add_notional >= 1.0` en dur, ligne 1035) |
| Comptage dans `rejections` | ✅ | ❌ |

Un bot en drawdown de 12 % réduit son entrée à ×0,5 mais **pyramide à taille
pleine**. Sur une stratégie comme `snowball_pyramid`, dont le pyramidage est le
cœur, l'écart de risque engagé est de première importance.

Le plafond notionnel, lui, est bien appliqué (`room`, ligne 1029).

**Correction** : extraire un `_risk_amount(ctx, size_factor)` partagé entre
`_try_enter` et la branche scale-in, et appeler `_min_notional()` au lieu du
`1.0` littéral.

---

## B-07 🟡 `realistic_risk` est opt-in — donc jamais activé

`Backtester.__init__(..., realistic_risk: bool = False)`. Le mode réplique
consécutif/DD journalier/trades par jour/frein de volatilité/kill-switch.

Chemins d'appel vérifiés :

| Appelant | `realistic_risk` |
|---|---|
| `OptimizerSearchEngine._eval` (`optimizer_search.py:274`) | non passé → **False** |
| `opt_workers._eval_worker` | non passé → **False** |
| `WalkForwardAnalyzer.run` (`walk_forward.py:101`) | non passé → **False** |
| `forward_test._forward_test_slot` (`forward_test.py:102`) | non passé → **False** |
| `api/routes/backtest.py` | query param, défaut **False** |

Autrement dit : **aucune décision automatique du dépôt (sélection de paramètres,
mesure d'edge, promotion de bot) n'est prise sur un backtest qui simule les
circuit breakers du live.** Le module est écrit, testé
(`tests/test_backtest_risk_gate.py`, 432 lignes) et inerte sur tous les chemins
qui comptent.

Le compromis énoncé (« préserver la parité avec les backtests existants ») est
légitime en tant que transition, mais il n'a pas d'échéance : il n'existe aucun
mécanisme qui bascule le défaut.

**Correction** : passer `realistic_risk=True` au moins dans `forward_test`
(mesure d'edge qui pilote les enveloppes) et dans l'optimiseur, en re-baselinant
`oos_tracker.json` d'un coup. Ou, a minima, afficher dans l'UI que la mesure
d'edge est faite sans circuit breakers.

Note de qualité : la logique de `BacktestRiskGate` elle-même est propre — bascule
de journée avant test de blocage, `pause_kind` machine séparé de `pause_reason`
humain, conversion secondes→bougies. C'est du bon travail rendu inopérant par son
câblage.

---

## B-08 🟡 Aucun embargo entre IS et OOS

`split_is_oos` (`core/is_oos.py:29-40`) coupe la série en deux tranches
strictement contiguës :

```python
split = max(warmup + 100, int(n * (1.0 - oos_fraction)))
return df[:split], df[split:], split
```

Il n'y a pas de **purge** ni d'**embargo** entre les deux. Deux fuites en
résultent :

1. **Fuite par les features** : une stratégie dont la fenêtre de calcul est de
   200 barres voit, sur les 200 premières barres de l'OOS, des données qui
   appartiennent à l'IS. Le warmup consomme ces barres sans trader, ce qui
   atténue le problème sans le supprimer (les indicateurs à mémoire longue —
   EMA — n'oublient jamais complètement).
2. **Fuite par les labels ML** : `labelling.build` construit `y[t]` à partir de
   `t + lookahead`. Les dernières barres de l'IS sont labellisées avec des
   informations situées **dans l'OOS**. En `ml_mode="inline"` (le mode de
   l'optimiseur, `optimizer_search.py:234`), le modèle est entraîné sur ces
   labels puis évalué sur l'OOS.

La littérature standard (López de Prado, *Advances in Financial ML*, ch. 7)
impose `purge = lookahead_label` et `embargo ≈ 1 % de la série`.

**Correction** :

```python
def split_is_oos(df, warmup=210, oos_fraction=0.35, purge_bars=0, embargo_bars=0):
    split = max(warmup + 100, int(n * (1.0 - oos_fraction)))
    return df[: split - purge_bars], df[split + embargo_bars:], split
```

avec `purge_bars` dérivé du `lookahead` de la recette ML de la stratégie.

---

## B-09 🟡 Trois warmups indépendants

| Valeur | Fichier | Usage |
|---|---|---|
| `210` | `backtest.py:1457` `_MIN_WARMUP` | boucle de trading |
| `210` | `core/is_oos.py:23` `WARMUP_BARS_DEFAULT` | split IS/OOS |
| `220` | `walk_forward.py:63` `WARMUP` | dimensionnement des folds |
| `250` | `forward_test.py:43` `_WARMUP_BARS` | fenêtre de forward-test |

`is_oos.py` a été créé explicitement pour supprimer ce genre de duplication (sa
docstring le dit). Deux des quatre lui ont échappé. Un backtest walk-forward
dimensionné pour 220 barres exécute une boucle qui en consomme 210 (ou davantage
si une stratégie déclare `warmup_bars`) : les folds ne contiennent pas ce que
le calcul de faisabilité annonce.

**Correction** : importer `WARMUP_BARS_DEFAULT` partout, et faire de
`_MIN_WARMUP` un alias.

---

## B-10 🟡 Clôture de fin de série gratuite

`backtest.py:1643-1645` :

```python
self._close_at(ctx, position, len(df) - 1, float(df["close"][-1]),
               "end_of_data", maker=True, status="closed_eod", append_ts=False)
```

- `maker=True` ⇒ frais maker (0,08 %) alors que toute liquidation forcée est
  taker (0,10 %) ;
- **aucun spread appliqué** (`ref_price=None`, donc `slip_exit = 0`), contrairement
  à toutes les autres sorties.

Sur un backtest court avec peu de trades, cette dernière sortie représente une
part non négligeable du résultat. Elle est en outre comptée dans `total_pnl`,
`win_rate` et `by_exit_reason` comme un trade ordinaire.

**Correction** : `maker=False`, appliquer le spread, et exclure `closed_eod` des
statistiques de win-rate (ou l'exposer à part) — une position tronquée par la fin
des données n'est pas un trade que la stratégie a décidé de fermer.

---

## B-11 🟡 `capital_before` faux après sorties partielles

`backtest.py:786-790` :

```python
capital_before = ctx.capital - pnl
gate.record_trade_result(i, slot_key, pnl + realized, day_key, capital_before)
```

`ctx.capital` inclut déjà les `realized` des jambes partielles (encaissés dans
`_close_partial_at:830`). Le montant transmis au gate est `pnl + realized` mais
le capital de référence n'a soustrait que `pnl`. Le DD journalier par slot calculé
par le gate est donc décalé du montant des jambes partielles.

**Correction** : `capital_before = ctx.capital - pnl - realized`.

---

## B-12 🔵 `rejected_notional` mélange trois causes

Le compteur est incrémenté pour :

- `stop_dist <= 0` (ligne 1154) → motif de rejet `stop_invalide` ;
- `notional < min_notional` (ligne 1186) → `notionnel_min` ;
- `q_size <= 0` après quantification (ligne 1202) → `venue`.

`self.rejections` distingue bien les trois, mais `diag["rejected_notional"]` —
celui qui apparaît dans le log de fin de run (`backtest.py:1668`) et dans
`result.diagnostics` — les agrège. Un utilisateur qui voit
« rejets notional = 340 » ne peut pas savoir s'il doit augmenter son enveloppe,
corriger sa stratégie ou changer de venue.

---

## B-13 🔵 Benchmark Buy & Hold décalé d'une barre

`_add_buy_and_hold:1723` : `first_price = float(df["close"][warmup])`.

Le premier trade possible entre à `df["open"][warmup + 1]` (`backtest.py:1120`,
avec `i = warmup`). Le B&H démarre donc une demi-barre plus tôt, sans spread ni
frais, alors que le bot en paie. `alpha = total_pnl − bnh_pnl` compare une
grandeur nette de frais de sortie (mais pas d'entrée, cf. F-01) à une grandeur
brute de tout.

Sur une stratégie à faible alpha, l'écart de convention dépasse le signal.

---

## B-14 🔵 Le walk-forward renvoie tout

`walk_forward.py:126-127` : `"in_sample": in_sample_results` et
`"out_of_sample": out_sample_results` contiennent les `to_dict()` complets de
chaque fold — donc `trades` (avec `stop_trail`, `conditions`, `indicators`,
`score_breakdown`), `equity_curve` et `timestamps`, pour 2 × `n_folds` runs.

Avec 5 folds et 60 trades par fold, la réponse JSON dépasse facilement 10 Mo.
Elle traverse `GZipMiddleware` (`middleware.py:201`) mais est intégralement
matérialisée en mémoire côté serveur et côté navigateur.

**Correction** : ne renvoyer que les agrégats par fold ; exposer le détail
derrière un `?detail=1`.

---

## Ce qui est solide

- **Pas de look-ahead sur le prix d'entrée** : le signal est calculé sur
  `df[:i+1]` (barre `i` close) et l'entrée se fait à `df["open"][i+1]`
  (`backtest.py:1120, 1582`). C'est correct, et c'est l'erreur la plus fréquente
  dans ce type de code.
- **Priorité conservatrice stop > TP** en cas d'ambiguïté intrabar
  (`backtest.py:905`), avec un compteur dédié `tp_sl_ambiguous_bars` qui rend le
  phénomène mesurable au lieu de le masquer. Bien vu.
- **`ml_mode="frozen"` avec `as_of=window_start`** (`backtest.py:99`) et
  détection de chevauchement train/test (`overlaps`, ligne 110) reportée dans
  `ml_info.overlap_warning` : la fuite temporelle ML est réellement traitée sur
  ce chemin, et le repli inline est rendu visible plutôt que silencieux.
- **`_n_bars` transmis au constructeur** pour l'annualisation, avec `None`
  explicite quand la durée est inconnue (`_years()`) : le raisonnement
  « mieux vaut pas de chiffre qu'un chiffre faux » est correctement appliqué.
- **`prepare_for_backtest`** comme hook générique de pré-calcul : bonne
  séparation, appliquée uniformément à tous les chemins (optimiseur, folds,
  replay).
- **Annulation coopérative** (`_cancel_event` testé toutes les 100 barres) et
  progression loggée avec ETA : l'ergonomie opérateur est soignée.
