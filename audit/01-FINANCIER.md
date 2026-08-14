# Audit — Moteur financier (coûts, PnL, sizing, métriques)

> Périmètre : `app/core/execution.py`, `trade_economics.py`, `risk_sizer.py`,
> `risk_envelope.py`, `risk_ledger.py`, `risk_gate.py`, `risk_curve.py`,
> `performance_metrics.py`, `deflated_sharpe.py`, `app/engine/monte_carlo.py`,
> et les points de consommation dans `backtest.py` / `position_*_mixin.py`.
>
> Méthode : lecture du code seul (aucune documentation ni commentaire pris pour
> argent comptant), plus vérification empirique sur `data/backtest_history.json`
> et `data/oos_tracker.json`.

---

## Tableau de bord

| # | Sévérité | Titre | Fichier |
|---|----------|-------|---------|
| F-01 | 🔴 Critique | `total_pnl` exclut les frais d'entrée mais sert de PnL de référence partout | `engine/backtest.py:704-771` |
| F-02 | 🔴 Critique | Le Sharpe est calculé sur 1 à 3 observations et sort à ±1000 | `engine/backtest.py:211-227` |
| F-03 | 🔴 Critique | `max_dd_p95` du Monte-Carlo renvoie le MEILLEUR drawdown, pas le pire | `engine/monte_carlo.py:96` |
| F-04 | 🟠 Majeur | Coût d'emprunt facturé sur le notionnel entier à levier 1 (≈30 %/an fictifs) | `core/execution.py:26-38` + `config/venues.yaml` |
| F-05 | 🟠 Majeur | Aucun plafond de notionnel au niveau VENUE : le levier réel dépasse `max_leverage` | `core/risk_ledger.py:70-108` |
| F-06 | 🟠 Majeur | Le drawdown ne voit jamais les pertes latentes (courbe d'équité par trade) | `engine/backtest.py:211-215` |
| F-07 | 🟠 Majeur | Deux implémentations divergentes du Deflated Sharpe, la mauvaise est câblée | `core/deflated_sharpe.py` vs `engine/opt_scoring.py:249` |
| F-08 | 🟡 Moyen | `by_strategy` : courbe d'équité, Sharpe et DD calculés hors frais d'entrée | `engine/backtest.py:438-470` |
| F-09 | 🟡 Moyen | Sortino non standard (dénominateur sur les seules observations négatives) | `core/performance_metrics.py:75` |
| F-10 | 🟡 Moyen | Valeurs sentinelles (999, 100.0) mélangées aux vraies mesures | `backtest.py:242`, `performance_metrics.py:74,100` |
| F-11 | 🟡 Moyen | `_check_rate()` consomme un jeton même quand le trade est ensuite refusé | `core/risk_gate.py:297-303` |
| F-12 | 🟡 Moyen | `_pre_execution_check` impose un plafond caché à 25 % du capital en live | `live/balance_sync.py:196` |
| F-13 | 🔵 Mineur | `alpha_vs_buy_hold` en O(n²) | `core/performance_metrics.py:164-165` |
| F-14 | 🔵 Mineur | `RejectionCounter` accumulé entre deux `run()` du même Backtester | `engine/backtest.py:573` |

---

## F-01 🔴 `total_pnl` exclut les frais d'entrée — et c'est lui qui décide

### Constat

Le décompte monétaire est asymétrique par convention :

- les **frais d'entrée** sont retirés du capital à l'ouverture
  (`backtest.py:1215` → `ctx.capital -= entry_fees`) et **ne figurent pas** dans
  le `pnl` du trade ;
- les **frais de sortie** sont retirés dans `close_pnl`
  (`core/execution.py:87-108`) et figurent bien dans le `pnl`.

Conséquence arithmétique, énoncée par le code lui-même en `backtest.py:333-338` :

```
Σ trade["pnl"]  ==  (final_equity − initial_capital)  +  Σ entry_fees
   total_pnl                    net_profit
```

`total_pnl` **surestime donc systématiquement le résultat du montant total des
frais d'entrée**.

### Pourquoi c'est critique

Ce n'est pas un problème d'affichage : `total_pnl` est la grandeur consommée par
toute la chaîne de décision.

| Consommateur | Ligne | Effet |
|---|---|---|
| `composite_score` — signe du score | `opt_scoring.py:133` `if pnl > 0: score = quality` | un paramétrage **net perdant** peut être classé « qualité » |
| `composite_score` — terme rendement | `opt_scoring.py:99` `ret_pct = pnl / cap * 100` | rendement surévalué |
| Objectif de l'optimiseur | `optimizer_search.py:293-294` `is_pnl / oos_pnl = res.total_pnl` | sélection biaisée |
| Garde-fou de promotion | `opt_scoring.py:186-189` `if oos_pnl <= 0` puis `oos_pnl <= b_pnl` | promotion d'un bot net perdant |
| Alpha vs Buy & Hold | `backtest.py:1731` `alpha = total_pnl − bnh_pnl` | alpha surévalué |
| `win_rate`, `expectancy`, `profit_factor` | `backtest.py:202-242` | un trade dont le gain brut < frais d'entrée est compté **gagnant** |
| Forward-test / edge | `forward_test.py:186-188`, `oos_tracker._per_trade_returns_pct` (sur `pnl_pct`) | même biais sur l'expectancy qui pilote les poids d'enveloppe |

### Ordre de grandeur

Frais taker OKX configurés : `taker_fee: 0.001` (`config/risk.yaml`). Sur une
enveloppe de slot de 500 € avec un notionnel proche du plafond, chaque entrée
coûte ≈ 0,50 €. Un run à 50 trades porte donc ≈ 25 € de frais d'entrée invisibles
dans `total_pnl`, soit **5 % de l'enveloppe** — largement au-dessus de l'écart
qui sépare un paramétrage gagnant d'un perdant dans les données observées
(`backtest_history.json` : médiane de `total_pnl` sur les runs profitables ≈ 20 €).

### Correction proposée

1. Faire de `net_profit` (= `final_equity − initial_capital`) la grandeur de
   référence exposée par `BacktestResult.to_dict()`, et n'utiliser `total_pnl`
   que comme diagnostic explicitement nommé `total_pnl_hors_frais_entree`.
2. Porter les frais d'entrée dans le `pnl` du trade (`_close_at` : `pnl -=
   position["entry_fees"]`) et retirer la double comptabilisation sur
   `ctx.capital`. Cela rend `win_rate` / `expectancy` / `profit_factor`
   cohérents d'un coup, sans autre changement.
3. Remplacer `res.total_pnl` par `res.net_profit` dans `optimizer_search._eval`
   et dans `opt_scoring.composite_score` / `beats_baseline`.

Test de non-régression à ajouter : `Σ trade["pnl"] == final_equity −
initial_capital` pour tout backtest.

---

## F-02 🔴 Un Sharpe calculé sur 1 à 3 observations

### Constat

`BacktestResult._compute_metrics` (`backtest.py:211-227`) calcule le Sharpe sur
`equity_curve`, qui reçoit **un point par trade clôturé** (`_close_at:771`) — et
non un point par bougie. Aucun plancher sur le nombre d'observations n'est posé :
2 trades ⇒ 2 rendements ⇒ un écart-type quasi nul ⇒ un ratio arbitrairement grand.

Le facteur d'annualisation a bien été corrigé (`returns_per_year`, plafonné aux
bougies/an), mais il ne traite pas la cause : **on ne peut pas estimer un
écart-type sur deux points**.

### Vérification empirique

`data/backtest_history.json` (252 slots, 158 avec au moins un trade) :

```
Sharpe |·| > 10  : 104 / 158  (66 %)
Maximum observé  : 1 014,76   (breakout_opus::1h, 2 trades, PnL 14,69 €)
Autres            : 307,98 (9 trades) · −235,49 (2 tr.) · 230,75 (8 tr.)
Runs à 1-2 trades : 29 / 158
```

Ce n'est pas un héritage : sur la campagne la plus récente (`run_date`
2026-08-07), **14 runs sur 20** dépassent encore |Sharpe| > 10, dont
`opus_omnibus_v11_no_ml::1h` à −215,8 sur 6 trades et `pullback_trend::1h` à
−124,4 sur 4 trades.

Même signature dans `data/oos_tracker.json` : `sim.sharpe` dépasse |10| sur
**119 slots sur 254**, avec un maximum à **4 050,9**.

### Impact

- `composite_score` borne le Sharpe à ±1 après normalisation
  (`opt_scoring.py:94`), donc le classement est protégé — mais
- `beats_baseline` compare `oos_sharpe > b_sharpe` **sans borne**
  (`opt_scoring.py:190`) : un Sharpe de 230 obtenu sur 8 trades bat n'importe
  quel baseline et satisfait à lui seul le critère « amélioration de qualité » ;
- `health_mixin` et l'UI affichent ces valeurs telles quelles ;
- le gate Deflated Sharpe (F-07) reçoit ces valeurs en entrée.

### Correction proposée

```python
_MIN_RETURNS_FOR_SHARPE = 10   # aligné sur MIN_SIGNIFICANT_TRADES

if len(returns) < _MIN_RETURNS_FOR_SHARPE:
    self.sharpe = None          # None, pas 0.0 : « non mesurable » ≠ « nul »
```

et propager `None` (l'UI sait déjà afficher `—`). Alternative complémentaire :
calculer le Sharpe sur les rendements **par bougie** de la courbe d'équité
mark-to-market plutôt que par trade — ce qui règle en même temps F-06.

---

## F-03 🔴 `max_dd_p95` renvoie le meilleur cas au lieu du pire

### Constat

`app/engine/monte_carlo.py:96` :

```python
"max_dd_p95": round(_sf(abs(float(np.percentile(max_dds, 95))), 0.0), 2),
```

`max_dds` est une liste de drawdowns **négatifs** (`dd.min()`, ligne 88). Le
95ᵉ percentile d'une distribution de valeurs négatives est la valeur la **moins**
négative, c'est-à-dire le **drawdown le plus faible** de la simulation. La valeur
absolue ensuite appliquée masque l'inversion : le champ, présenté comme un
quantile de risque à 95 %, publie le scénario le plus favorable.

Le calcul correct est `np.percentile(max_dds, 5)` (queue basse), ou
`np.percentile(np.abs(max_dds), 95)`.

### Vérification empirique

Un p95 de drawdown doit être **≥** au drawdown réellement observé sur la série
d'origine (celle-ci est une permutation parmi d'autres). Dans
`data/oos_tracker.json` :

```
max_dd_p95_pct < |max_drawdown réalisé|  :  145 / 155 enregistrements
```

y compris sur la campagne la plus récente (7/11 le 2026-08-07). Exemples :

| Slot | trades | `max_dd_p95_pct` | DD réalisé |
|---|---|---|---|
| `opus_omnibus_v11_followsetup_no_ml::30m` | 20 | 19,12 | −23,53 |
| `derivatives_reversion::15m` | 36 | 2,30 | −6,03 |
| `trend_rider::4h::ETH/USDC` | 5 | 5,48 | −5,94 |
| `breakout::1h` (juin) | 6 | 0,77 | −1,76 |

L'écart va jusqu'à un facteur 2,6. **Le principal indicateur de risque de
séquence du dépôt sous-estime le risque, de façon systématique.**

### Effet de bord observé : Monte-Carlo dégénéré

Toujours dans `oos_tracker.json`, **145 enregistrements sur 155** ont
`return_p5_pct == return_mean_pct == return_p95_pct` et `prob_profit ∈ {0, 100}`
— la signature exacte d'une permutation sans remise, que la docstring de
`monte_carlo.py` déclare pourtant corrigée. La correction (bootstrap avec remise,
`rng.choice(..., replace=True)`) est bien présente dans le code actuel et les
runs du 2026-08 montrent enfin de la dispersion : **les données persistées
restent majoritairement issues de l'ancien calcul**, et rien ne les invalide ni
ne les date côté UI. Un utilisateur qui consulte le cône Monte-Carlo d'un slot
non re-testé depuis juin lit une bande de largeur nulle avec
`prob_profit = 100 %`.

### Correction proposée

1. `np.percentile(max_dds, 5)` (et retirer le `abs`, ou l'appliquer après).
2. Versionner les enregistrements (`"mc_version": 2`) et ignorer/regénérer les
   anciens côté UI plutôt que de les afficher comme des mesures courantes.
3. Test : sur une série de trades donnée, `max_dd_p95 >= |max_drawdown|` de la
   série non permutée.

---

## F-04 🟠 Emprunt facturé à levier 1 sur le notionnel entier

### Constat

`borrow_cost` (`core/execution.py:26-38`) facture des intérêts composés sur
**tout** le notionnel :

```python
return float(notional) * ((1 + r_period) ** n_periods - 1)
```

Aucune notion de montant réellement emprunté. Or la venue par défaut du dépôt est
`margin-isolated` avec `max_leverage: 1` (`config/venues.yaml`). À levier 1, la
position est intégralement couverte par les fonds propres : **rien n'est
emprunté**, et pourtant l'intégralité du notionnel est facturée à
`borrow_rate_daily: 0.00072` (`config/risk.yaml`), soit ≈ **30 %/an**.

`venue_borrow_rate` (`execution.py:41-68`) neutralise bien le cas `spot` et le
cas `perp`, mais laisse passer `margin` sans regarder le levier.

Le dépôt a déjà identifié et corrigé exactement ce défaut pour les actions
(commentaire `execution.py:48-51` : « chaque trade SBF 120 payait 0,072 %/jour
d'intérêt fictif — ~30 %/an sur un achat comptant »), en basculant la venue
actions en `spot`. Le même défaut subsiste sur le chemin crypto par défaut.

### Ordre de grandeur

Notionnel 500 €, détention 10 jours (typique en 4h/1d) :
`500 × ((1 + 0,00072/24)^240 − 1) ≈ 3,60 €`, à comparer à `2 × 0,50 €` de frais
aller-retour. **Le portage fictif représente ici 3,6× les frais de transaction**,
et 0,72 % du notionnel par trade — plus que l'edge de la plupart des stratégies
mesurées.

Ce coût est appliqué des deux côtés (backtest `_close_at:704`, live
`position_close_mixin.py:233`), donc la parité est préservée, mais **les deux
sont faux ensemble**.

### Correction proposée

```python
def borrow_cost(notional, daily_rate, hours_held, periods_per_day=24,
                own_funds: float | None = None):
    borrowed = notional if own_funds is None else max(0.0, notional - own_funds)
    if borrowed <= 0 or daily_rate <= 0 or hours_held <= 0:
        return 0.0
    ...
```

avec `own_funds = min(notional, env.slot_envelope)` côté backtest et le solde
réellement immobilisé côté live. À défaut, une garde minimale :
`venue_borrow_rate` renvoie 0 quand `venue.max_leverage <= 1`.

⚠️ **Ce correctif change tous les résultats de backtest historiques** (dans le
sens favorable). Il doit être livré seul, avec re-baselining explicite.

---

## F-05 🟠 Aucun plafond de notionnel au niveau venue

### Constat

`RiskLedger.reserve` (`core/risk_ledger.py:70-108`) applique cinq contrôles :

1. notionnel ≤ `env.max_notional` (slot) ;
2. notionnel cumulé du symbole ≤ `env.symbol_max_notional` ;
3. risque cumulé du symbole ≤ `env.symbol_risk_budget` ;
4. risque cumulé de la venue ≤ `env.venue_risk_budget` ;
5. notionnel ≥ `env.min_notional`.

Il manque le pendant notionnel du contrôle 4. `self._venue_notional` est bien
**tenu à jour** (lignes 102, 116, 160) mais **jamais comparé à quoi que ce soit**.
`Envelope.venue_envelope` est renseigné (`risk_envelope.py:127`) et n'est lu
nulle part dans le code de production (vérifié par recherche : seuls des tests le
référencent).

### Effet

`symbol_envelope = venue_capital × max_symbol_exposure_pct` = 1000 × 0,5 = 500 €
par symbole. Avec 5 symboles simultanément en position, le notionnel engagé sur
la venue atteint **2 500 € pour un capital de 1 000 €**, soit un levier effectif
de 2,5× alors que `max_leverage: 1` est déclaré et que l'exécution demande
`params={"leverage": 1}` (`position_open_mixin.py:294`).

Sur un compte margin isolé, cela se traduit soit par des ordres refusés par
l'exchange, soit — si de la marge est disponible — par une exposition réelle non
voulue. Le budget de risque venue (50 €) borne la perte *si les stops tiennent* ;
il ne borne pas l'exposition en cas de gap.

Le dépôt en a conscience : un test s'appelle
`test_two_symbols_at_full_exposure_overflow_the_venue_envelope`
(`tests/test_risk_diagnostics.py:109`) — le débordement est **diagnostiqué**,
pas **empêché**.

### Correction proposée

Ajouter dans `reserve`, entre les contrôles 2 et 3 :

```python
cur_venue_notional = self._venue_notional.get(env.venue, 0.0)
venue_max_notional = env.venue_envelope * max(env.max_leverage, 1.0)
if _exceeds(cur_venue_notional + notional, venue_max_notional):
    return Decision(False, "enveloppe_venue", ...)
```

et ajouter `enveloppe_venue` à `app/core/rejections.REASONS` pour que le motif
soit comptabilisé des deux côtés.

---

## F-06 🟠 Le drawdown ignore les pertes latentes

### Constat

`max_drawdown` est calculé sur `equity_curve` (`backtest.py:211-215`), qui ne
reçoit un point qu'à la **clôture** de chaque trade. Une position qui descend à
−40 % avant de revenir à +2 % ne laisse aucune trace dans le drawdown.

Les données pour le mesurer existent pourtant : `mae` (Maximum Adverse
Excursion) est calculé barre par barre (`backtest.py:862-870`) et agrégé en
`avg_mae`. Il n'est simplement jamais confronté à la courbe d'équité.

### Conséquences en chaîne

- `max_drawdown` sous-estimé ⇒ `dd_factor = max(0, 1 − dd/30)`
  (`opt_scoring.py:84`) trop favorable ⇒ l'optimiseur **préfère** les stratégies
  qui laissent courir les pertes latentes ;
- `calmar = cagr / |max_dd|` (`performance_metrics.py:84-101`) mécaniquement
  surévalué ;
- `max_drawdown == 0` sur 7 runs de `backtest_history.json` **qui ont pourtant
  des trades** — un drawdown nul signifie ici « aucun trade clôturé n'a fait
  baisser l'équité », pas « le portefeuille n'a jamais été en perte » ;
- l'écart avec le live est structurel : le live, lui, valorise
  `capital_display = _paper_base + unrealized` à chaque cycle
  (`balance_sync.py:65`), donc **son** drawdown voit le latent. Les circuit
  breakers `daily_drawdown_limit` / `max_drawdown_global` se déclenchent en live
  sur des mouvements que le backtest n'a jamais simulés.

### Correction proposée

Maintenir une seconde série, `equity_mtm`, mise à jour à chaque barre :
`ctx.capital + unrealized(position, close_arr[i])`, et calculer `max_drawdown`
(et le Sharpe, cf. F-02) dessus. Conserver `equity_curve` pour la compatibilité
d'affichage. Coût : une addition par barre.

---

## F-07 🟠 Deux Deflated Sharpe contradictoires, le plus faible est câblé

### Constat

Le dépôt contient deux implémentations distinctes du même concept :

| | `core/deflated_sharpe.py` | `engine/opt_scoring.py:249` |
|---|---|---|
| Nom | `deflated_sharpe` | `deflated_sharpe_ratio` |
| Retour | un **Sharpe** mis à l'échelle | une **probabilité** ∈ [0,1] |
| E[max SR] | `σ·(√(2 ln N) + γ/(2√(2 ln N)))` | approximation de Bailey & LdP éq. 7 (loi des extrêmes) |
| Non-normalité | non traitée | skew / kurtosis |
| Câblée ? | **oui** (`opt_scoring.beats_baseline:202`) | **non** (morte) |

La version câblée n'est pas la formule de López de Prado — c'est une heuristique
maison dont le facteur `1 − γ²` n'a pas d'assise dans la référence citée. La
version conforme est dans le module de scoring… et n'est appelée nulle part
(commentaire `opt_scoring.py:230-235` : « pas encore câblée »).

### Problème d'étalonnage

`is_deflated_sharpe_significant` est appelé sans `sharpe_std`
(`opt_scoring.py:203-207`), donc `sharpe_std = 1.0`. Avec 100 essais :

```
E[max SR | H0] = 1,0 × (√(2·ln 100) + 0,577/(2·√(2·ln 100))) ≈ 3,13
```

Tout Sharpe OOS inférieur à 3,13 donne un DS **négatif** ⇒ refus. Combiné à F-02
(des Sharpe de 200+ sur 8 trades), le gate **laisse passer exactement les runs
dégénérés et bloque les runs sains**. C'est l'inverse de son objet.

### Correction proposée

1. Supprimer `core/deflated_sharpe.py` et câbler `opt_scoring.deflated_sharpe_ratio`.
2. Estimer `sharpe_std` **empiriquement** : `statistics.stdev([r["oos_sharpe"]
   for r in self.results])`, disponible dans `OptimizerSearchEngine` au moment du
   `_best_result()`.
3. Ne l'appliquer qu'après avoir corrigé F-02 : un gate sur un Sharpe non
   mesurable ne mesure rien.

---

## F-08 🟡 `by_strategy` ne solde pas les frais d'entrée

`_group_metrics` (`backtest.py:438-445`) reconstruit une courbe d'équité par
stratégie :

```python
cap = self.initial_capital
for t in strat_trades:
    cap += t["pnl"]          # ← n'inclut pas entry_fees (cf. F-01)
```

Donc `by_strategy[s]["final_equity"] ≠ result.final_equity` même quand une seule
stratégie tourne (cas de tous les backtests lancés depuis l'UI). Le Sharpe et le
drawdown par stratégie héritent du même biais.

**Correction** : dériver de `net_pnl` du trade une fois F-01 corrigé ; ajouter
une assertion en test pour le cas mono-stratégie.

---

## F-09 🟡 Sortino non standard

`performance_metrics.py:75` :

```python
downside_dev = stdev(downside_returns, xbar=0.0) if len(downside_returns) > 1 ...
```

La déviation downside standard est `√( (1/N) Σ min(r − MAR, 0)² )` — divisée par
le nombre **total** d'observations. Ici la division se fait par `n_negatifs − 1`.
Le dénominateur étant plus petit, la déviation est plus grande et le Sharpe
**sous-estimé** — dans le sens prudent, mais **non comparable** à une valeur
publiée ailleurs, ce qui est le seul intérêt d'un ratio nommé.

`stdev(..., xbar=0.0)` divise en outre par `n−1` alors qu'avec une moyenne
*imposée* (0.0) le diviseur correct est `n`.

---

## F-10 🟡 Sentinelles indiscernables des mesures

| Sentinelle | Lieu | Signification réelle |
|---|---|---|
| `profit_factor = 999.0` | `backtest.py:242` | « aucune perte » (souvent : 1 seul trade) |
| `sortino = 100.0` | `performance_metrics.py:74,78` | « aucun rendement négatif » |
| `calmar = 100.0` | `performance_metrics.py:100` | « drawdown nul » |
| `composite_score = −999.0` | `opt_scoring.py:56` | « moins de 10 trades » |

Ces valeurs traversent l'API, sont persistées (`backtest_history.json` contient
6 entrées à `profit_factor = 999`, dont 3 sur **un seul trade**) et sont
affichées comme des mesures. `beats_baseline` compare `oos_sharpe > b_sharpe`
sans les filtrer.

**Correction** : renvoyer `None` et laisser l'UI afficher `∞` / `—`. `None`
propage naturellement une exclusion des comparaisons ; `999` gagne les tris.

---

## F-11 🟡 `_check_rate()` consomme un jeton avant de savoir si le trade passe

`risk_gate.py:297-303` : `_check_rate` **ajoute** un horodatage à
`self._trade_times` dès qu'il renvoie `True`. Il est appelé depuis `can_trade`
(`risk_gate.py:292`), qui est l'**étape 1 sur 8** de `_try_open_from_signal`
(`position_open_mixin.py:501`).

Un signal refusé plus loin (slot désactivé, poids nul, `stop_dist <= 0`,
notionnel minimum, `reserve` refusée, `_pre_execution_check`, course sur
`pos_key`) a donc déjà **consommé** un jeton du budget
`max_trades_per_minute: 3`. Dans un cycle où 3 signaux sont refusés, le 4ᵉ —
valide — est bloqué par l'anti-spam.

**Correction** : séparer `is_rate_ok()` (lecture pure) de `consume_rate_token()`,
et n'appeler le second qu'après le succès de `_open_position`.

---

## F-12 🟡 Plafond caché à 25 % du capital en live

`balance_sync.py:193-200` :

```python
if self.capital_display < notional * 0.05:      return False
if notional > self.capital_display * 0.25:      return False
```

Deux constantes en dur qui contredisent le modèle d'enveloppes S12 :

- l'enveloppe autorise `slot_envelope × max_leverage` = 500 € pour la config par
  défaut ;
- `_pre_execution_check` refuse tout ce qui dépasse `capital_display × 0.25` =
  250 €.

Résultat : **en live réel**, toute position dimensionnée au plafond de son
enveloppe est refusée — silencieusement (`return False` sans
`self.rejections.record`, cf. `position_open_mixin.py:559-561`), donc sans motif
dans les compteurs ni dans le `signal_log`. Ni le backtest ni le mode paper ne
reproduisent cette contrainte : c'est une rupture de parité **qui ne se
manifeste qu'en argent réel**.

**Correction** : supprimer les deux constantes et s'appuyer sur `env.max_notional`
(déjà appliqué par `compute_size` et `RiskLedger.reserve`), ou les rendre
configurables et les répliquer dans le backtest. Dans tous les cas, enregistrer
un motif de rejet.

---

## F-13 🔵 `alpha_vs_buy_hold` en O(n²)

`performance_metrics.py:164-165` appelle `mean(strat)` et `mean(bench)` **dans**
la compréhension, donc une fois par élément :

```python
cov_sb = sum((s - mean(strat)) * (b - mean(bench)) for s, b in zip(strat, bench)) / (n - 1)
var_b  = sum((b - mean(bench)) ** 2 for b in bench) / (n - 1)
```

Sur une série de 10 000 points cela fait 4 × 10⁸ opérations. La branche n'est
atteinte que si `len(bh_returns) == len(strat_returns)`, ce qui n'arrive
aujourd'hui jamais depuis le backtest (F-01 du fichier `02-BACKTEST.md`), donc
l'impact est latent — mais il se réveillera dès qu'on alignera les deux séries.

**Correction** : hisser `m_s = mean(strat)` / `m_b = mean(bench)` hors des
boucles.

---

## F-14 🔵 Compteurs de rejet accumulés entre deux runs

`Backtester.__init__:573` crée `self.rejections = RejectionCounter()`. `run()` ne
le réinitialise pas. Or `OptimizerSearchEngine._eval` (`optimizer_search.py:274-277`)
appelle **deux fois** `bt.run()` sur la même instance (IS puis OOS) : les rejets
de la passe IS sont comptés dans le résultat OOS.

Idem pour `run_dual_pass` (`backtest.py:134-161`) — celui-ci crée bien deux
`Backtester`, donc il est indemne.

**Correction** : `self.rejections = RejectionCounter()` en tête de `run()`.

---

## Ce qui est solide

Par honnêteté d'audit, les points suivants ont été vérifiés et sont corrects :

- **Source unique des formules monétaires** (`core/execution.py`) réellement
  partagée entre backtest et live — la parité de `close_pnl` tient, y compris
  sur le modèle de venue (`venue_trade_cost`, TTF à l'achat, plancher de
  courtage). C'est la bonne architecture.
- **Sizing par la distance au stop** et refus explicite quand `stop_dist <= 0`
  (`risk_sizer.py:87-91`) : le repli historique sur l'ATR brut, qui multipliait
  le risque par ~2,5, est bien supprimé des deux côtés.
- **Arrondi systématiquement à la baisse** (`_floor_to`) avant confrontation aux
  plafonds : le raisonnement de `risk_sizer.py:26-33` est juste et l'`_FP_EPS`
  de `risk_ledger.py:45` est correctement dimensionné (1e-9 relatif).
- **`RiskLedger` thread-safe** (RLock, réservation atomique avant l'ordre) et
  `update_risk` appelé au trailing pour libérer du budget — le modèle est sain.
- **`funding_cost`** (`trade_economics.py:40-56`) traite correctement le signe :
  un funding négatif est encaissé. Le distinguo perp/margin est juste.
- **`size_impact_cost`** partagé backtest ↔ paper (`execution.py:368-385`) :
  formule cohérente, linéaire en participation, quadratique en notionnel.
