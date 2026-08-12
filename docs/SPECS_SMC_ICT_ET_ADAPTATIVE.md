# Deux spécifications confrontées au bot — ce qui existe, ce qui manque

Analyse de `strategie_smc_ict_actions_crypto.md` (72 sections) et
`Stratégie multi-actifs adaptative` (80 sections) contre le code réel.

**Conclusion en une phrase :** le bot couvre déjà l'essentiel de la *détection*
des deux spécifications — c'est la **gestion de position** (sorties partielles)
et l'**orchestration portefeuille** (classement, corrélation, force relative)
qui manquent, et ces deux manques sont communs aux deux documents.

---

## 1. Le verrou commun : pas de sortie partielle

Les deux spécifications sont bâties autour du même schéma :

```
TP1 → sortir 25 %   |   TP2 → sortir 25 %   |   runner 50 % en trailing
```

Le moteur de backtest **ne sait pas sortir partiellement**. Vérifié :

| capacité | état |
|---|---|
| `partial_fill` (taux de remplissage à l'ENTRÉE) | existe — mais sans rapport |
| `check_scale_in` (pyramidage, ajout sur position gagnante) | existe |
| **scale-out / sortie fractionnée** | **absent (0 occurrence)** |
| `tp1_percent` / `tp2_percent` | **absent (0 occurrence)** |

Une position s'ouvre et se ferme **en entier**. Tant que ce n'est pas levé,
aucune des deux stratégies ne peut être implémentée fidèlement : on peut
seulement approximer le runner par un trailing sur la position entière, ce qui
change complètement le profil de risque et rend les backtests non comparables
aux spécifications.

**C'est le chantier prérequis n° 1**, et il est architectural : il touche
`Backtester._try_exit`, la structure `position`, le journal de trades et le
calcul du R multiple. Le pyramidage (`check_scale_in`) fournit le patron
symétrique à suivre.

---

## 2. Spécification SMC / ICT

### 2.1 Ce qui existe déjà — et c'est beaucoup

Le dépôt contient **1 244 lignes de moteur SMC** (`app/core/smc*.py`) et
**1 616 lignes de stratégie** (`app/strategies/smart_money*.py`).

| Section de la spec | Dans le bot |
|---|---|
| §6 Détection des swings (pivots) | `analyze()` → `swings` avec `swing_left/right` configurables |
| §5 §7 HH/HL/LH/LL, structure | `swings[].label`, `structure_line` |
| §8 HTF bias | `htf_analysis()`, `htf_trend_series(mult=4)` — HTF par **rééchantillonnage**, pas par chargement séparé |
| §9 §10 BOS / MSS-CHoCH | `structure_events` (`kind` BOS/CHoCH, `direction`) |
| §11 §12 Liquidity pools, equal H/L | `liquidity_pools` (`eq_tol_atr` configurable) |
| §13 Score de liquidité par type | `calendar_liquidity_levels()` — PDH/PWH/PMH |
| §14 Liquidity sweep | `sweeps`, `recent_sweep()` |
| §15 §16 Displacement | `disp_body_atr` dans `smart_money_params` |
| §17–19 FVG + mitigation | `fvgs` avec `mitigated_at` / `filled_at` |
| §20 §21 Order Blocks | `order_blocks` (`created_at`, `invalidated_at`, `broke_structure`) |
| §22 Breaker Block | `breakers` + setup `BREAKER_RETEST` |
| §23 Premium / Discount | `premium_discount_at()` (modes swing et IPDA) |
| §24 OTE | `premium_discount_at()` → `ote_low`, `ote_high`, `in_ote` |
| §25 Draw on Liquidity | `liquidity_targets_above/below()` |
| §29 Killzones / sessions | `killzone_flags()`, `session_label()` |
| §33 SMT Divergence | `smt_series()` + bonus dans `_score_setup` |
| §34 Score /100 + seuil | `_score_setup` avec `min_score` |
| §40 R/R minimum | `min_rr` dans `PARAM_SPACE` |
| §44 Time stop | `max_hold_bars` / `exit_after_bars` |
| §47 Anti-surtrading | `BacktestRiskGate` : pertes consécutives, max trades/jour, DD journalier, pause/halt |
| §51 Position sizing | `app/core/risk_sizer.py`, `risk_envelope` |
| §59 Anti-look-ahead | vérifié par test de préfixe (`tests/test_features_smc.py`) |
| §60 Frais / slippage | `taker_fee`, `maker_fee`, `spread_pct`, coûts d'emprunt |
| §62 Métriques | Sharpe, Sortino, Calmar, CAGR, PF, expectancy, alpha vs B&H |
| §63 Walk-forward | `split_is_oos()` (65/35), `overfitting_ratio`, Deflated Sharpe |
| §64 Monte Carlo | panneau dédié dans le frontend |

Les 5 setups de `smart_money` (`SWEEP_REVERSAL`, `CALENDAR_SWEEP`,
`OB_RETEST`, `BREAKER_RETEST`, `BPR_REVERSAL`) couvrent le cœur du §26/§28, et
son YAML documente déjà une **ablation setup par setup**.

### 2.2 Ce qui manque

| Section | Manque | Difficulté |
|---|---|---|
| §41 §42 | **TP1/TP2/runner** + trailing structurel (stop derrière dernier HL/LH) | **élevée** — voir §1 |
| §30 | **Asian Range** (high/low/mid + sweep) | faible |
| §31 | **Silver Bullet** comme module séparé | moyenne |
| §54 | **Filtre news / earnings** | moyenne (dépend d'une source de données) |
| §49 §50 | **Risque portefeuille inter-actifs + clusters de corrélation** | élevée |
| §65 | **Statistiques séparées par module** (SMC Core / ICT Session / ICT Advanced) | faible |

Présents mais **partiels** : IFVG (existe comme bonus de confluence
`_inv_fvg_add`, pas comme point d'entrée à part entière) et AMD (4 fichiers,
comme bonus, pas comme module à statistiques propres).

### 2.3 Ce qui doit être adapté plutôt qu'ajouté

- **Le scoring.** La spec §34 pondère HTF 15 / liquidité 15 / sweep 15 /
  displacement 10 / MSS 15 / POI 10 / P-D 10 / draw 5 / session 5.
  `_score_setup` a ses propres poids, déjà optimisés. Réécrire le barème pour
  coller à la spec **casserait des réglages mesurés** — mieux vaut l'exposer en
  `param_space` et comparer les deux barèmes par ablation.
- **Le multi-timeframe.** La spec veut Daily → 4H → 1H → 15m chargés
  séparément ; le bot **rééchantillonne** depuis le TF de base
  (`htf_trend_series(mult)`). C'est plus simple et sans risque de
  désalignement, mais ne donne qu'**un** niveau HTF à la fois. Passer à
  Daily+4H+1H simultanés demande soit des `mult` empilés, soit un vrai
  chargement multi-TF.
- **Le seuil `MIN_SCORE = 80`** de la spec est à recalibrer : les scores de
  `_score_setup` ne sont pas sur la même échelle.

---

## 3. Spécification multi-actifs adaptative

### 3.1 Ce qui existe

| Section | Dans le bot |
|---|---|
| §5 Indicateurs (EMA/RSI/MACD/ATR/ADX/ROC/BB/volume) | les 437 colonnes de `v4_polars@1` les contiennent toutes |
| §6 Market regime | `classify_regime()` — 4 états |
| §14 Régime de volatilité | `ATR_pct`, `BB_width_rank100` |
| §17 §18 §21 Breakout / Pullback | stratégies **déjà existantes** : `breakout.py`, `breakout_opus.py`, `pullback_trend.py` |
| §25 §26 Stop structurel + ATR | `use_swing`, `atr_stop_mult`, buffers |
| §27–30 Position sizing, volatility adjustment | `risk_sizer`, `volatility_brake_factor` |
| §40 Time stop | `exit_after_bars` |
| §45–48 Cooldown, pertes consécutives, daily loss limit | `BacktestRiskGate` / `RiskGate` |
| §49 Drawdown protection | `max_drawdown_global`, `daily_drawdown_limit` |
| §62 §63 Frais / slippage | complet |
| §64 Backtest sans look-ahead | signal à N, ordre à N+1 |
| §71 Walk-forward | `split_is_oos` |

### 3.2 Ce qui manque

| Section | Manque | Note |
|---|---|---|
| §12 | **Force relative vs benchmark** (action vs indice, token vs BTC) | **absent** — brique centrale de la spec |
| §7 | **Market Score global** (index EMA200, breadth, momentum marché) | **absent** |
| §7 | **Breadth** (% de titres au-dessus de leur MM) | quasi absent |
| §8 §56 | **Classement des actifs** puis sélection des meilleurs | `scanner_service.py` scanne, mais ne classe pas pour allouer |
| §31 §32 | **Corrélation entre positions**, clusters, max positions par classe | quasi absent |
| §34 | TP1/TP2/runner | voir §1 |
| §41 | **Exécution réaliste sur gap** (ouverture au lieu du stop) | à vérifier côté actions |
| §43 | Filtre earnings | absent |

### 3.3 Ce qui doit être adapté

- **Le régime.** `classify_regime` produit `RANGE / TREND_UP / TREND_DN /
  CHOPPY` à partir d'ADX, alignement de MM, DI et pente SMA20. La spec veut
  `BULL / STRONG_BULL / NEUTRAL / BEAR` à partir d'EMA200 et de sa pente. Les
  deux sont raisonnables et **ne se recouvrent pas** — il faut choisir, pas
  fusionner. Mesuré par ailleurs : donner le régime en feature explicite au ML
  n'apporte rien (`docs/ML_ABLATION_SMC.md` §3), ce qui ne présage rien de son
  utilité comme **filtre de décision**, qui est un autre usage.
- **Breakout et pullback existent déjà comme stratégies séparées.** La spec en
  fait deux setups d'un moteur unique avec un score commun. Les fusionner
  demanderait de réconcilier trois `param_space` déjà optimisés.
- **L'architecture est par slot (symbole × TF), pas par portefeuille.** Les
  §31–33 (risque agrégé, corrélation, max positions par classe) supposent un
  allocateur au-dessus des stratégies. `risk_envelope` fournit l'enveloppe par
  bot ; l'étage portefeuille inter-bots reste à écrire.

---

## 4. Recommandation

**Ne pas écrire deux nouvelles stratégies tout de suite.** Dans l'ordre :

1. **Sorties partielles dans le moteur** (§1). Sans elles, les deux specs sont
   inimplémentables fidèlement, et c'est aussi le levier le plus probable pour
   la conversion signal → trade : le dossier `smc_ml_edge` a montré que le
   problème n'est pas la détection mais la sortie.
2. **Force relative + Market Score** (spec adaptative §7 §12). Deux briques
   isolées, testables séparément, et qui manquent aux deux specs pour filtrer.
3. **Asian Range + statistiques par module** (spec SMC §30 §65). Peu coûteux,
   et rend les setups comparables entre eux.
4. **Alors seulement**, une stratégie `smc_ict_core` non-ML suivant la spec —
   qui sera à ce moment-là surtout un **assemblage** de briques existantes,
   avec un barème de score à comparer par ablation contre celui de
   `smart_money`.

Le point à garder en tête : `smart_money` implémente déjà le cœur du §26/§28 et
**perd de l'argent en OOS** (`docs/STRATEGY_SMC_ML_EDGE.md` §3 quinquies). Une
seconde stratégie SMC non-ML qui reprendrait la même détection sans changer la
gestion de position n'a pas de raison de faire mieux.
