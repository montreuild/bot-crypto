# Plan d'amélioration SMC / ICT V3 — spécification `strategie_smc_ict_OKX_PEA_v4.md` confrontée au bot

**Destinataire : l'agent qui exécutera ce plan.** Ce document dit, pour chacune
des 111 sections de la spécification, ce qui **existe déjà** dans le dépôt (avec
le fichier), ce qui est **à adapter**, ce qui est **à ajouter**, et ce qu'il faut
**refuser d'implémenter tel quel**. Puis il ordonne le travail en lots
mesurables.

Source analysée : `strategie_smc_ict_OKX_PEA_v4.md` (4 925 lignes). ⚠ Le fichier
contient les sections §60–§111 **en double** (lignes 1622–3272 puis 3275–4925,
contenu identique) et porte des marqueurs de citation cassés
(`citeturn0search1`). Ce n'est pas une spec relue : elle doit être traitée
comme une source d'idées, pas comme un contrat.

Documents à lire avant de commencer, dans cet ordre :

1. `docs/STRATEGY_SMC_ML_EDGE.md` §3 quinquies et §4 — la mesure qui conditionne
   tout ce plan.
2. `docs/SPECS_SMC_ICT_ET_ADAPTATIVE.md` — l'analyse d'écart précédente (specs
   v3), dont ce document est la suite.
3. `docs/SMART_MONEY_CONCEPTS.md` — l'état réel du moteur et de la stratégie.

---

> ## ⚑ Verdict du chantier (L0–L10 livrés, plus la suite de §5)
>
> **Quatre mécanismes sur seize valident, sur le plus grand échantillon
> disponible.** Les deux portes de structure de L3 (`no_pullback`, `direction`)
> et les deux mécanismes de tier de L6 gagnent sur les deux fenêtres et les deux
> symboles en 1 h, historique complet — 122 à 154 trades OOS. En 4 h et 1 j,
> aucun ne valide, mais ces cas comptent 11 à 96 trades.
>
> **Ils atténuent, ils ne retournent pas.** `no_pullback` ramène la perte OOS de
> BTC 1 h de −500,6 à −330,6 : une réduction de 34 %, pas un edge. La condition
> de réussite posée en §6 reste donc **non satisfaite** — aucune configuration ne
> passe `beats_baseline` avec un PnL OOS positif.
>
> **Le défaut le plus coûteux du chantier était méthodologique et de mon fait :**
> un plafond de 12 000 barres choisi sans le mesurer, alors que BTC 1 h en compte
> 51 909. Il a rendu fausses trois conclusions — le rejet de `no_pullback`, celui
> de la porte `direction`, et le « 0 sur 16 » d'ensemble — et a fait dégénérer 14
> recalibrations sur 20.
>
> **Acquis techniques, indépendants de la performance :** un défaut de parité
> backtest/live corrigé (neuf stratégies avaient un filtre HTF inerte en
> simulation et actif en production), deux défauts de comptabilité, le verrou des
> sorties partielles ouvert, une convention BOS/MSS/CHoCH unique, sept axes de
> mesure, un plancher de trades unifié, et la règle des deux fenêtres.
>
> Détail et suite : `docs/SUITE_ABLATION_V3.md`.

## 0. Verdict en une page

**Le bot couvre déjà la quasi-totalité de la couche *détection* de la spec.**
1 244 lignes de moteur SMC causal (`app/core/smc*.py`), 282 lignes de
détecteurs ICT (`app/core/ict.py`), 1 616 lignes de stratégie
(`app/strategies/smart_money*.py`), plus un bloc de features SMC pour le ML
(`app/ml/features_smc.py`). Sweeps, FVG, order blocks, breakers, rejection
blocks, BPR, unicorn, CE, IFVG, premium/discount, OTE, PDH/PDL/PWH/PWL, Asian
Range, killzones, Silver Bullet, SMT, Judas, volume profile : tout est là, tout
est causal, tout est testé.

**Ce que la spec apporte réellement de neuf est concentré dans sa partie V3
(§60–§111)** : la *mémoire de structure*. Le bot raisonne aujourd'hui en
`trend ∈ {−1, 0, +1}` (`res["_trend_arr"]`) ; la spec demande une machine à
états qui distingue continuation, pullback, warning et retournement confirmé, et
qui protège les niveaux structurels. Ça n'existe pas — c'est le seul ajout
conceptuel majeur, et il est justifié.

**Mais quatre verrous invalident l'ordre de priorité proposé par la spec :**

| # | Verrou | Preuve | Conséquence |
|---|---|---|---|
| V1 | **Pas de sortie partielle** | `scale_out` / `tp1_percent` : 0 occurrence réelle (`check_scale_in` existe, son symétrique non) | §29, §30, §101 de la spec sont inimplémentables ; une position s'ouvre et se ferme en entier |
| V2 | **Pas de R/R net** | `net_rr` : 0 occurrence. `_build_trade` calcule `rr_final` **brut** ; les frais n'entrent qu'au moment de la clôture (`_close_at`) | §2, §4, §36 — le cœur économique de la spec — n'existent pas |
| V3 | **Backtest mono-position, mono-symbole** | `position = None` scalaire dans `Backtester.run` | §28, §33, §96 (corrélation, budget cluster, positions simultanées) ne sont pas testables |
| V4 | **Les deux stratégies SMC échouent en OOS** | `docs/STRATEGY_SMC_ML_EDGE.md` §3 quinquies : 8/8 cases refusées par `beats_baseline`, Deflated Sharpe = 0.0 partout | Empiler 50 concepts sur une base qui perd n'améliorera rien |

**La conclusion la plus importante de ce plan** : la spec elle-même le dit en
§111 (« PLUS DE CONCEPTS ≠ MEILLEURE STRATÉGIE ») et le dépôt l'a déjà mesuré —
le signal SMC est solide (§2 bis de `docs/ML_ABLATION_SMC.md`, 6 actions
décorrélées), **c'est sa conversion en trades qui échoue**. Le plan est donc
ordonné par *levier mesuré*, pas par ordre de sections :

> **construction du trade (L1–L2) → mémoire de structure (L3–L4) → un seul score
> (L6) → portefeuille (L7) → le reste, seulement s'il est mesuré.**

Un lot ne démarre pas tant que le précédent n'a pas produit son chiffre.

---

## 1. Inventaire vérifié — ce qui existe déjà

### 1.1 Moteur de détection (`app/core/`)

| Fichier | Lignes | Contenu |
|---|---:|---|
| `smc.py` | 88 | façade, ré-exporte tout |
| `smc_structure.py` | 441 | `analyze()` — passe causale O(n) : swings HH/HL/LH/LL, BOS/CHoCH, pools, sweeps, OB, FVG, voids, breakers, rejection blocks |
| `smc_primitives.py` | 133 | `DEFAULTS` (swing_left/right, eq_tol_atr, disp_body_atr, fvg_min_atr…), ATR Wilder, clustering de pools |
| `smc_geometry.py` | 275 | premium/discount + OTE (modes `swing` et `ipda`), trendlines, zigzag, cycle, `liquidity_targets_above/below`, `void_targets_*`, `recent_sweep` |
| `smc_sessions.py` | 281 | `calendar_liquidity_levels` (PDH/PDL/PWH/PWL/PMH), `asian_range_levels`, `killzone_flags`, `session_label`, `htf_trend_series`, `htf_analysis`, `smt_series` |
| `smc_volume.py` | 111 | volume profile (POC/HVN/LVN), canal de régression |
| `ict.py` | 282 | CE, BPR, unicorn, IFVG, measured move, propulsion block, nested OB, projections en écarts-types, `silver_bullet_flags`, `judas_swing`, `smt_divergence`, `align_series` |

Toutes les entités portent `index` / `formed_at` / `swept_at` / `mitigated_at` :
la causalité est structurelle, pas conventionnelle. `tests/test_features_smc.py`
vérifie l'absence de look-ahead par test de préfixe.

### 1.2 Stratégie (`app/strategies/smart_money*.py`, 1 616 lignes)

5 setups (`_SETUP_CHECKERS`) : `SWEEP_REVERSAL`, `CALENDAR_SWEEP`, `OB_RETEST`,
`BREAKER_RETEST`, `BPR_REVERSAL`. Score par confluence additive (base 0.50,
plafond 1.0), seuil `min_score` par TF, filtre/bonus SMT, reclassement Silver
Bullet, `min_rr` / `min_gain_pct`, ciblage par poches de liquidité et voids
(`_build_trade`), `size_by_confluence`, time-stop conditionnel, trailing
optionnel. Sélection de setups déjà arbitrée par ablation OOS
(`strategies/smart_money.yaml`).

### 1.3 Exécution, risque, coûts

- `app/core/execution.py` — source unique des formules : `trade_fees`,
  `borrow_cost`, `venue_trade_cost`, `size_impact_cost`, `cost_model`,
  `quantize_size/price`.
- `config/venues.yaml` — 4 venues : `spot`, `margin-isolated`, `perp-hedge-okx`
  (`market_type: perp`), `euronext-paper` (actions, `fractional: false`,
  data-only).
- `app/core/risk_envelope.py` — enveloppes emboîtées venue → symbole → slot,
  double plafond notionnel/risque.
- `app/core/risk_sizer.py` + `risk_curve.py` — sizing par risque, dé-risquage en
  drawdown (×0.75 > 5 %, ×0.5 > 10 %).
- `app/core/risk_gate.py` + `app/engine/backtest_risk_gate.py` — kill-switch,
  pertes consécutives, DD journalier par slot, max trades/jour, volatility brake.
  Le backtest les rejoue en mode `realistic_risk`.
- `app/core/market_calendar.py` — sessions XPAR, jours fériés, adaptateur
  `exchange_calendars`.

### 1.4 Mesure

`BacktestResult` expose `by_strategy`, `by_setup`, `by_module` (§65 livré en
#218), MFE/MAE par trade, `exit_reason`, `rejections` (taxonomie partagée
live/backtest), diagnostics par barre. `WalkForwardAnalyzer`, `MonteCarlo`,
`deflated_sharpe_ratio`, `overfitting_ratio`, `beats_baseline`,
`composite_score`, `regime_stress_test`, `forward_test`. Scripts de mesure
dédiés dans `scripts/measure_*.py`.

---

## 2. Correspondance §1–§111

Légende : **✅ existe** · **🔧 à adapter** · **➕ à ajouter** · **⛔ à refuser ou
reporter** (justification en §3).

### 2.1 Partie V2 (§1–§59)

| § | Sujet | État | Où / quoi faire |
|---|---|:--:|---|
| 1 | Profils OKX / PEA | 🔧 | `config/venues.yaml` + `config/risk.yaml` portent déjà la dimension venue. Les **valeurs** (score 80/88, RR 2.5/3.0, risque 0.5 %/0.75 %) ne sont pas transposables telles quelles → §3.1 |
| 2 | Tout calculé NET | ➕ | **Verrou V2.** Voir lot L2 |
| 3 | Frais configurables | ✅ | `config/venues.yaml` + `trading.taker_fee/maker_fee/spread_pct/borrow_rate_daily`, `execution.cost_model()` |
| 4 | Economic Edge (gain ≥ 5× coûts) | ➕ | Gate à ajouter dans L2. Remplace avantageusement `min_gain_pct` (seuil absolu 0,4 %) par un seuil **relatif aux coûts réels de la venue** |
| 5 | Structure HTF bull/bear/neutral | ✅ | `htf_trend_series()`, `res["bias"]` |
| 6 | Timeframes | 🔧 | Le bot **rééchantillonne** (`htf_trend_series(mult)`), il ne charge pas 3 TF simultanés. `df_htf` est accepté par `score()` mais **non utilisé** par `smart_money`, et **jamais passé** par le backtest (0 occurrence dans `app/engine/backtest.py`). Voir L5 |
| 7 | Swing detection + latence | ✅ | `swing_left/right` configurables, confirmation retardée déjà respectée |
| 8 | External / Internal structure | 🔧 | `htf_analysis()` donne l'externe, `analyze()` l'interne — mais rien ne relie formellement les deux. Traité par L3 |
| 9 | Liquidité (SSL/BSL, PD/PW/PM, session, Asian) | ✅ | `liquidity_pools`, `calendar_liquidity_levels`, `asian_range_levels` |
| 10 | Equal high/low, tolérance ATR | ✅ | `eq_tol_atr` (défaut 0.25 ; la spec propose 0.10 → paramètre, à balayer) |
| 11 | Liquidity sweep avec réintégration | ✅ | `sweeps[].rejected` |
| 12 | Displacement (corps ≥ ATR, ratio, RVOL) | 🔧 | `disp_body_atr` existe. Manquent `body/range` et la position de clôture → L4 (§84) |
| 13 | MSS / BOS | 🔧 | `structure_events` porte BOS/CHoCH. **MSS n'existe pas comme concept distinct** (1 occurrence, dans un commentaire). Convention à fixer en L3 |
| 14 | Fair Value Gap | ✅ | `fvgs` + `fvg_min_atr` |
| 15 | FVG quality + mitigation_pct | 🔧 | `mitigated_at` / `filled_at` existent, `mitigation_pct` continu non → L4 (§85) |
| 16 | Order Block | ✅ | `order_blocks` + `broke_structure` + `strength` |
| 17 | Premium / Discount | ✅ | `premium_discount_at()` |
| 18 | OTE | ✅ | `ote_low` / `ote_high` / `in_ote` |
| 19 | Draw on Liquidity | ✅ | `liquidity_targets_above/below()` |
| 20 | Scoring /100 | 🔧 | `_score_setup` a son barème sur [0,1], **optimisé et mesuré**. Ne pas le remplacer → §3.2, traité en L6 |
| 21–22 | Setups LONG / SHORT | ✅ | `SWEEP_REVERSAL` + `OB_RETEST` couvrent la séquence |
| 23 | Stop = sweep ± 0.10 ATR, refus si > 4 ATR | 🔧 | `sl_buffer_atr` (0.25) existe. **Le plafond `4 × ATR` n'existe pas** → ajout trivial dans `_build_trade`, à mesurer |
| 24 | Position sizing | ✅ | `risk_sizer.py`, parité backtest/live verrouillée par `tests/test_backtest_live_parity.py` |
| 25–26 | Risque OKX / PEA | 🔧 | Sémantique différente : le bot risque un % de **l'enveloppe du slot**, pas de l'équité globale (`config/risk.yaml`, `profiles`) → §3.1 |
| 27 | Perpetuals + funding | ➕ | Venue `perp-hedge-okx` déclarée, mais **le funding n'entre pas dans le PnL** : 0 occurrence de `funding` dans `execution.py` et `backtest.py`. Les perps sont facturés au `borrow_rate_daily`, ce qui n'est pas le bon modèle → L2 |
| 28 | Corrélation crypto / budget cluster | ➕ | `correlation_matrix.py` mesure la corrélation **entre bots a posteriori**, pas un budget de risque cluster à l'entrée → L7 |
| 29 | TP1 / TP2 / Runner | ➕ | **Verrou V1** → L1 |
| 30 | Trailing structurel (SL sous dernier HL) | ➕ | `trailing.py` fait du trailing ATR multi-phases avec option `use_swing`, mais pas « derrière le dernier HL/LH structurel » → L1 |
| 31 | Time stop | ✅ | `time_stop_bars` + `ts_profit_r` (time-stop conditionnel sur MFE) |
| 32 | Anti-FOMO | 🔧 | Partiellement : le retest est requis par construction dans `OB_RETEST`. La règle « distance depuis POI > 1R → refus » et « cible déjà consommée » n'existent pas → L4 |
| 33 | Anti-overtrading | ✅ | `BacktestRiskGate` : pertes consécutives, pause, max trades/jour, DD slot |
| 33b | Une tentative par liquidity event | ➕ | → L6 (§97/§98) |
| 34 | Daily / weekly loss limits | ✅ | `daily_drawdown_limit`, `slot_daily_dd_limit`, `max_drawdown_global`, `equity_kill_switch_dd` |
| 35 | PEA long-only | 🔧 | `euronext-paper` a `allow_short: false` et `market_type: spot`. Le reste (score 88, RR 3) → §3.1 |
| 36 | PEA frais | ✅ | Modèle de coûts par venue |
| 37 | Filtre indice / secteur | ➕ | **Aucune donnée indice/secteur dans le dépôt.** Dépendance externe → L9 |
| 38 | PEA volatilité (ATR ≥ 1 %) | ➕ | Filtre simple, `ATR_pct` disponible → L9 |
| 39 | PEA univers | ✅ | `data/universe/sbf120.yaml` + `scripts/check_universe.py` |
| 40 | Earnings / macro | ➕ | 0 occurrence de `earnings`. Dépendance externe → L9 |
| 41 | Killzones | ✅ | `killzone_flags()`, `KILLZONES` |
| 42 | Asian Range | ✅ | `asian_range_levels()` — livré en #219 |
| 43 | SMT | ✅ | `smt_series()` + `smt_filter`/`smt_bonus` |
| 44 | Silver Bullet | ✅ | `silver_bullet_flags()` + reclassement `module` — livré en #219 |
| 45 | Power of Three / AMD | ✅ | Bonus `amd_*` dans `smart_money_params` |
| 46 | Indicateurs classiques secondaires | ✅ | Politique déjà appliquée (aucun indicateur ne déclenche seul) |
| 47 | Fonctions Python | 🔧 | La liste est un vœu d'API, pas une contrainte. Ne pas renommer l'existant pour y coller → §3.3 |
| 48 | Data model `Setup` | 🔧 | Le contrat actuel est le dict `signal` (`BaseStrategy.score`). L'enrichir, pas le remplacer → L6 |
| 49 | Métriques de backtest | ✅ | Toutes présentes sauf `Fees Paid`/`Funding Paid`/`Slippage` agrégés au niveau résultat → petit ajout en L0 |
| 50 | Walk forward | ✅ | `WalkForwardAnalyzer`, `split_is_oos` |
| 51 | Monte Carlo | ✅ | `MonteCarlo` |
| 52 | Paper trading | ✅ | `paper_mode`, `forward_test.py`, lifecycle shadow |
| 53 | Critères de validation | 🔧 | Les critères de la spec (PF > 1.2, ≥ 300 trades) sont **plus faibles** que `beats_baseline` + Deflated Sharpe du dépôt. Ne pas régresser → §3.4 |
| 54 | State machine d'exécution | 🔧 | Implicite dans la boucle live. Ne pas réifier pour le plaisir → §3.3 |
| 55 | Configuration finale | 🔧 | Le dépôt a un découpage config strict (une section = un fichier, cf. `config.yaml`). Injecter le bloc `strategy:` de la spec tel quel casserait `_validate_*` → écrire dans `strategies/*.yaml` |
| 56 | Résumé opérationnel | — | Narratif |
| 57 | Interdictions | ✅ | Toutes déjà tenues, sauf « funding ignoré » (V2) et « positions corrélées » (V3) |
| 58 | Priorité d'implémentation | ⛔ | Cet ordre est celui d'un projet vierge. Il est remplacé par §4 de ce plan |
| 59 | Principe directeur | ✅ | C'est déjà la séquence de `SWEEP_REVERSAL` |

### 2.2 Partie V3 (§60–§111) — l'apport réel

| § | Sujet | État | Où / quoi faire |
|---|---|:--:|---|
| 60 | 12 états de structure | ➕ | **Cœur du plan.** `res["bias"]["trend"]` est un entier ternaire → L3 |
| 61 | Convention BOS / MSS / CHoCH | ➕ | À figer une fois pour toutes dans un module unique → L3 |
| 62–63 | Transitions bull↔bear en 2 temps (warning → confirmed) | ➕ | → L3 |
| 64 | Protected high / low | ➕ | 0 occurrence. Concept structurant, peu coûteux → L3 |
| 65 | Inducement / IDM | 🔧 | `require_inducement` existe comme **flag de filtre** dans `smart_money.yaml`, sans détecteur dédié. À promouvoir en entité → L4 |
| 66 | IRL / ERL | ➕ | 0 occurrence. Distinction utile pour le ciblage → L4 |
| 67 | Dealing range explicite | 🔧 | `premium_discount_at(mode="swing"|"ipda")` fait le travail mais n'expose pas `dealing_range_{high,low,mid}` ni le choix de contexte → L4 |
| 68 | Narrative engine | 🔧 | À réduire à un **libellé dérivé** de l'état structure + liquidité, pas un moteur → §3.2 |
| 69 | Narrative score (7 blocs) | ⛔→🔧 | N'ajouter **aucun** second barème. Décomposer le score unique en sous-blocs mesurables → L6 |
| 70 | Sequence score | ⛔→🔧 | Idem — c'est le même score vu autrement |
| 71 | Tiers A/B/C/D | ➕ | Utile : donne une **échelle de risque** discrète (×1.00 / ×0.85 / ×0.65 / ×0.50) branchable sur `size_factor` existant → L6 |
| 72 | `sequence_id` / `sequence_type` | ➕ | 0 occurrence. Prérequis de la déduplication ET des stats par modèle → L6 |
| 73 | Failed reversal | ➕ | Le cas le plus intéressant à tester de toute la spec (continuation après échec de retournement) → L3, mesuré en L8 |
| 74 | Reversal confirmation score | 🔧 | Sous-bloc du score unique, pas un barème à part → L6 |
| 75 | Regime engine (6 états) | 🔧 | `classify_regime()` existe (4 états, ADX/MM). **Ne pas fusionner : choisir.** Mesuré en L5 |
| 76 | Volatility regime par **percentile** ATR | ➕ | 0 occurrence de percentile ATR. Remplace avantageusement les seuils absolus (`atr_volatile_threshold: 3.0`) → L5 |
| 77 | Liquidity hierarchy | ➕ | 0 occurrence. Le ciblage actuel prend la **première** cible qui passe le RR, sans notion de qualité → L4 |
| 78 | Target quality score | ➕ | → L4 |
| 79 | Expected target value (proba × reward) | ➕ | **Excellente idée, mais la proba doit être *mesurée*, pas postulée.** Voir §3.5 → L4 puis L8 |
| 80 | Entry refinement 15m → 5m | 🔧 | Nécessite le multi-TF réel (§6) → L5, optionnel |
| 81 | MTF alignment score | ➕ | → L5 |
| 82 | Pullback ≠ reversal | ➕ | Conséquence directe de L3 |
| 83 | Liquidity raid quality | ➕ | Mesures disponibles (profondeur, mèche/corps, vitesse de reclaim, volume) → L4 |
| 84 | Displacement quality | 🔧 | Enrichir `disp_body_atr` → L4 |
| 85 | FVG hierarchy | ➕ | Marquer le FVG **né du displacement qui a causé le MSS** → L4 |
| 86 | Order block quality | 🔧 | `strength`/`broke_structure` existent, à étendre → L4 |
| 87 | Breaker blocks | ✅ | `breakers` + setup `BREAKER_RETEST` (**désactivé** : −163 USDC / 220 trades en 4h) |
| 88 | Balanced price range | ✅ | `ict.balanced_price_ranges` + setup `BPR_REVERSAL` (désactivé, pas d'edge stable) |
| 89 | Session narrative | 🔧 | `session_label()` existe ; la narration Asian→London→NY est un libellé → L4, faible priorité |
| 90 | PDH/PDL/PWH/PWL | ✅ | `calendar_liquidity_levels()` |
| 91 | Daily / weekly / monthly open | ➕ | 0 occurrence. Trivial à ajouter dans `smc_sessions` → L4 |
| 92 | News / event risk | ➕ | → L9 |
| 93 | Execution quality score | 🔧 | Les briques existent (`spread_pct`, `size_impact_cost`, volume) mais aucun score ni refus sur spread → L2 |
| 94 | Adaptive position sizing multiplicatif | 🔧 | 4 des 5 facteurs existent déjà (`size_factor` par confluence, `_risk_multiplier(dd)`, `volatility_brake_factor`). Manquent corrélation et exécution → L7 |
| 95 | Drawdown adaptation | ✅ | `risk_curve.py` (paliers différents de la spec — paramétrer, pas réécrire) |
| 96 | Correlation engine | ➕ | → L7 |
| 97 | Setup deduplication / `market_event_id` | ➕ | → L6 |
| 98 | Cooldown intelligent (événementiel) | 🔧 | `reentry_cooldown_bars` est purement temporel → L6 |
| 99 | Trade journal automatique | 🔧 | Le dict position couvre ~60 % des champs. Compléter → L0 |
| 100 | MFE / MAE | ✅ | Calculés par barre dans `_manage_open_position` |
| 101 | Exit analytics (comparaison des systèmes de sortie) | ➕ | **Le levier n° 1 identifié par la mesure précédente** → L0 + L1 |
| 102 | Feature ablation | 🔧 | Le mécanisme existe (`scripts/measure_smc_ablation.py`, flags `use_*`). À généraliser aux nouveaux modules → L8 |
| 103 | Robustesse par régime | ✅ | `regime_stress_test.py` |
| 104 | Ne pas sur-optimiser | ✅ | `overfitting_ratio`, Deflated Sharpe, `audit_param_space.py` |
| 105 | Walk-forward renforcé | ✅ | `split_is_oos`, `_oos_trade_window_bars` |
| 106 | Modèle de décision (21 questions) | 🔧 | Bonne **checklist de test**, mauvaise architecture de code → §3.3 |
| 107 | Architecture V3 | 🔧 | Compatible avec les couches existantes (cf. `ARCHITECTURE.md` §Couches). Ne pas réorganiser le dépôt |
| 108 | State machine V3 | ➕ | = §60, → L3 |
| 109 | Raisonnement narratif | 🔧 | Se matérialise par le champ `reason` déjà présent, enrichi en L3 |
| 110 | Priorités V3 | ✅ | Cohérentes avec ce plan, sauf l'ordre — voir §4 |
| 111 | Philosophie (modulaire et mesurable) | ✅ | C'est déjà la règle du dépôt |

---

## 3. Critique — ce qu'il ne faut PAS faire

### 3.1 Ne pas transposer les seuils chiffrés de §1, §25, §26, §35

La spec donne « score ≥ 80 », « risque 0.50 % », « R/R ≥ 2.5 ». Trois raisons de
refuser la transposition littérale :

1. **Échelle différente.** `_score_setup` produit un score sur `[0, 1]` (base
   0.50, confluences additives). « 80 » n'a pas de sens dessus. Les valeurs
   actuelles (`min_score: 0.70` en 4h) viennent d'une optimisation OOS ; les
   écraser détruirait une mesure.
2. **Sémantique du risque différente.** Le dépôt risque un % de **l'enveloppe du
   slot** (`risk_envelope.py`, `profiles: {prudent: 0.01, normal: 0.025}`), pas
   de l'équité globale. « 0.75 % max par trade » de la spec et « 2.5 % du slot »
   du dépôt ne se comparent pas directement.
3. **Le R/R minimal actuel est brut ; celui de la spec est net.** Fixer
   `min_rr: 2.5` avant d'avoir implémenté le R/R net (L2) donnerait un seuil
   ni brut ni net.

**À faire à la place** : exposer ces seuils dans `param_space`, les balayer, et
laisser l'optimiseur + `beats_baseline` trancher. Les profils OKX/PEA deviennent
deux jeux de `optimizer_results` par venue, pas deux constantes.

### 3.2 Ne pas créer six systèmes de score

La spec propose §20 (score /100), §69 (narrative score), §70 (sequence score),
§74 (reversal confirmation score), §78 (target quality), §81 (MTF alignment).
Six barèmes, ~40 poids, tous à calibrer. C'est exactement ce que §104 de la même
spec interdit, et ce qui produit l'`overfitting_ratio` saturé déjà observé.

**À faire à la place** : **un** score, décomposé en sous-blocs nommés, chacun
activable et mesurable séparément (L6). Le total reste sur `[0, 1]`. Chaque
sous-bloc doit prouver son apport par ablation (L8) ou disparaître.

Même remarque pour le « Narrative Engine » (§68) : c'est un **libellé dérivé**
de l'état de structure et de la liquidité ciblée, pas un moteur. Il alimente le
champ `reason` du signal et le journal — il ne décide rien.

### 3.3 Ne pas réécrire l'architecture pour coller à §47, §54, §106, §107

`§47` liste 30 fonctions Python. La plupart existent sous d'autres noms
(`detect_liquidity_sweep` → `analyze()["sweeps"]`). Renommer casserait
`app/ml/features_smc.py`, `app/api/services/scanner_service.py`, l'overlay du
scanner et les tests. **La façade `app/core/smc.py` existe précisément pour que
les noms historiques restent stables.**

`§54`/`§108` demandent une state machine d'exécution explicite. Le dépôt a déjà
une boucle live composée en mixins (`ARCHITECTURE.md` §Composition du
LiveTrader) et un `SlotLifecycleManager`. Réifier `IDLE → SCAN → … → COOLDOWN`
ajouterait une couche sans changer une décision.

`§106` (21 questions) est en revanche une **excellente checklist de test
d'acceptation** : à transformer en `tests/test_smc_decision_checklist.py`, un
test par question critique.

### 3.4 Ne pas régresser sur les critères de validation (§53)

La spec demande « PF > 1.2, expectancy > 0, ≥ 300 trades ». Le dépôt applique
déjà `beats_baseline` (≥ 10 trades OOS, PnL OOS positif, mieux que les défauts,
+ un critère de qualité) **et** le Deflated Sharpe corrigé du test multiple
(López de Prado). Ce dernier est strictement plus exigeant. **Aucun lot de ce
plan ne doit assouplir ces gates.**

### 3.5 §79 (expected target value) : la probabilité doit être mesurée

`expected_target_value = target_probability × net_reward` est la meilleure idée
de la spec pour le ciblage. Mais §78 propose de fixer la probabilité par un
barème arbitraire (« HTF external +30, Previous Week +25… »). Poser une
probabilité à la main puis maximiser dessus, c'est optimiser une croyance.

**À faire** : en L4, implémenter le mécanisme avec une probabilité **estimée sur
l'historique** — pour chaque type de pool, la fréquence historique d'atteinte
avant invalidation du setup, mesurée en walk-forward (donc jamais sur la fenêtre
de test). Tant que cette mesure n'existe pas, `target_probability = 1.0` pour
tous les types (comportement actuel), et le mécanisme reste inerte mais câblé.

### 3.6 §37, §40, §92 (indice/secteur, earnings, macro) : dépendance de données

Aucune de ces données n'est dans le dépôt. Elles impliquent un fournisseur
externe, un cache, une politique de fraîcheur et un mode dégradé. **Ne pas
implémenter le filtre avant d'avoir la donnée** : un filtre earnings qui ne
connaît aucune date ne fait rien, mais donne l'illusion d'une protection. L9
traite la donnée d'abord, le filtre ensuite.

### 3.7 §27 : le funding est un vrai trou, pas un détail

`config/venues.yaml` déclare `perp-hedge-okx` en `market_type: perp`, facturé au
`borrow_rate_daily` (0.072 %/jour). Ce n'est pas le modèle des perpetuals : le
funding est payé/reçu toutes les 8 h, il change de signe, et
`app/core/derivatives.py` sait déjà le récupérer (`fetch_funding`) — mais
uniquement pour en faire des **features ML**. Tout backtest perp actuel a un
coût de portage faux. À corriger en L2 avant toute mesure sur perp.

---

## 4. Plan de travail

Chaque lot = une PR. Règle d'or : **un lot ne démarre pas tant que le précédent
n'a pas produit son chiffre**, consigné dans `CHANGELOG.md` et dans le document
de mesure du lot.

### L0 — Instrumentation : savoir pourquoi les trades meurent ✅ LIVRÉ

> **Résultat : le diagnostic supposé était faux dans 3 cas sur 4.** Sur 1 h et
> ETH 4 h, 66–71 % des trades meurent sur leur stop initial après un MFE médian
> de 0,34–0,49 R — ils ne décollent jamais, donc le problème est l'entrée ou la
> cible, pas la sortie. La cible n'est touchée que 28–36 % du temps pour un TP
> demandé 2 à 5 fois supérieur au MFE médian. Seul BTC 4 h (seul cas avec
> `use_trailing`) montre le défaut supposé. **Conséquence : L1 reste justifié
> mais ne réparera pas les cas 1 h — c'est L3/L4 qui les visent.**
> Détail : `docs/MESURE_GEOMETRIE_SORTIE.md`.

*Prérequis de tout le reste. `docs/STRATEGY_SMC_ML_EDGE.md` §4 le demande
explicitement et personne ne l'a fait.*

**Objectif** : rendre lisible la construction du trade avant de la changer.

**Travaux**

1. Compléter le journal de trade (§99) — champs manquants dans le dict position
   de `Backtester._try_enter` / `_close_at` : `session`, `htf_bias`,
   `structure_state` (après L3), `liquidity_swept` (type + niveau), `mss`/`bos`,
   `fvg`/`ob`/`idm`, `pd_zone`, `gross_rr`, `net_rr` (après L2),
   `sequence_type` (après L6), `funding_paid`, `slippage_paid`.
2. Agréger au niveau `BacktestResult` : `fees_paid`, `funding_paid`,
   `slippage_paid`, `gross_profit`, `net_profit` (§49).
3. Ajouter `by_exit_reason` à côté de `by_setup`/`by_module`
   (`BacktestResult._compute_extended_metrics`, même patron que #218).
4. Écrire `scripts/measure_exit_geometry.py` : pour `smart_money` et
   `smc_ml_edge`, sur BTC + ETH × {1h, 4h}, produire la distribution des
   `exit_reason`, la distribution MFE/MAE par `exit_reason`, et le tableau
   « MFE atteint vs TP demandé ».

**Critère d'acceptation** : le rapport répond par un chiffre à « les positions
meurent-elles sur stop, sur trailing ou sur expiration ? » et « le MFE médian
dépasse-t-il le TP ? ». Aucun changement de comportement — les backtests
existants doivent être **bit-identiques** (test de non-régression).

**Livrable** : `docs/MESURE_GEOMETRIE_SORTIE.md`.

---

### L1 — Sorties partielles + trailing structurel (verrou V1) ✅ LIVRÉ

> **Résultat : le tout-ou-rien actuel est le pire des quatre systèmes, dans les
> quatre cas testés.** `partiel_struct` (TP1 1 R + TP2 poche + runner derrière
> le dernier pivot) gagne 3 fois sur 4 et divise le drawdown par deux sur
> ETH 1 h. Mais **aucun système ne rend la stratégie rentable** : le meilleur
> absolu vaut −0,33 % OOS. La géométrie de sortie valait 2 à 7 points de PnL,
> pas le signe. Laissé **off par défaut** — améliorer un système perdant n'est
> pas une raison de le promouvoir. Détail : `docs/MESURE_SYSTEMES_DE_SORTIE.md`.
>
> Effet de bord chiffré : BTC 4 h affiche PF 1,147 pour un PnL net de −0,33 % —
> les frais des fills supplémentaires mangent l'edge brut. Argument direct
> pour L2.

*Le chantier architectural n° 1, déjà identifié par
`docs/SPECS_SMC_ICT_ET_ADAPTATIVE.md` §1 et toujours ouvert.*

**Objectif** : TP1 / TP2 / runner (§29) et stop derrière le dernier HL/LH (§30).

**Travaux**

1. **Modèle de position fractionnable.** `position["size"]` devient
   `position["legs"] = [{size, tp, status}]` avec `size` maintenu comme somme
   pour compatibilité. `check_scale_in` fournit le patron symétrique à suivre
   (`_manage_open_position`, `app/live/position_manage_mixin.py`).
2. **`_close_partial_at(ctx, position, i, exec_price, fraction, reason)`** —
   dérivé de `_close_at` : frais au prorata, PnL réalisé partiel, équité mise à
   jour, pas d'`append` au journal tant que la position n'est pas close ; le
   trade final porte `exits: [{bar, price, fraction, reason}]`.
3. **R multiple par trade** recalculé sur la somme pondérée des jambes — c'est
   ce que consomment `composite_score` et `MonteCarlo`, donc à vérifier avec
   soin.
4. **Contrat stratégie** : `signal["exits"] = [{"r": 1.0, "fraction": 0.25},
   {"target": "liquidity_htf", "fraction": 0.25}]`, runner = reste. Défaut
   `None` → comportement actuel strictement inchangé.
5. **Trailing structurel** : nouveau mode dans `TrailingStopManager`
   (`mode="structure"`) qui place le stop sous le dernier HL confirmé (long) /
   au-dessus du dernier LH (short), en réutilisant `res["_all_swings"]`.
   Break-even + coûts après TP1 (§30).
6. **Live** : `app/live/position_manage_mixin.py` et `position_close_mixin.py`
   doivent gérer la clôture partielle réelle. `tests/test_backtest_live_parity.py`
   doit être étendu, pas contourné.
7. **Base de données** : migration du schéma trades pour `exits` (JSON) —
   cf. `app/core/database.py`.

**Critère d'acceptation** : `scripts/measure_exit_systems.py` compare sur BTC+ETH
× {1h, 4h}, à réglages égaux et sur la même découpe IS/OOS, quatre systèmes de
sortie (§101) : TP fixe / TP+runner / trailing ATR / trailing structurel. Le
résultat est publié même s'il est négatif.

**Risque** : c'est le lot qui touche le plus de code partagé. Aucune autre
modification de comportement ne doit y être glissée.

**Livrable** : `docs/MESURE_SYSTEMES_DE_SORTIE.md`.

---

### L2 — Moteur de coûts et R/R net (verrou V2)

**Objectif** : §2, §4, §23, §27, §36, §93 — aucune décision d'entrée sans coût.

**Travaux**

1. **`app/core/trade_economics.py`** (nouveau, pur, sans I/O) :
   ```python
   def round_trip_cost(entry, size, venue, cost_model, *, hours_held_est, funding_rate_est) -> Costs
   def net_rr(entry, stop, target, size, costs) -> float
   def economic_edge_ok(expected_gross, costs, multiple) -> bool
   ```
   Réutilise `execution.trade_fees`, `borrow_cost`, `size_impact_cost` — pas de
   formule dupliquée.
2. **Funding perp** : `execution.py` apprend un `funding_cost(notional,
   funding_rate, hours_held, periods=3/jour)`, alimenté par
   `derivatives.DerivativesStore.align_to_ohlcv()` (série `funding_rate` déjà
   disponible et causale). Facturé dans `_close_at` **uniquement** pour
   `venue.market_type == "perp"`. `borrow_cost` reste pour `margin`.
3. **Gate d'entrée** dans `_build_trade` : remplacer `min_gain_pct` (seuil
   absolu) par `economic_edge_ok(expected_gross, costs, multiple=p["cost_multiple"])`
   et le filtre `min_rr` brut par `net_rr >= p["min_net_rr"]`. Les deux anciens
   paramètres restent lisibles pour ne pas invalider les `optimizer_results`
   existants, mais deviennent des replis.
4. **Plafond de stop** (§23) : refus si `stop_distance > p["max_stop_atr"] × ATR`
   (défaut 4.0, `None` = off).
5. **Execution score** (§93) : refus si `spread_pct > p["max_spread"]` ; malus de
   score sur `size_impact_cost` relatif au gain attendu. Pour les actions,
   pénalité supplémentaire sur volume médian faible.

**Critère d'acceptation** :
- un test prouve que `net_rr` calculé à l'entrée coïncide, à 1e-6 près, avec le
  R réalisé d'un trade qui touche exactement son TP (`tests/test_net_rr_parity.py`) ;
- un backtest perp avant/après montre l'écart de PnL dû au funding — chiffre
  publié ;
- ré-optimisation de `smart_money` avec le gate net, comparée à l'ancienne sur
  la même découpe.

---

### L3 — Structure Engine séquentiel (l'apport réel de la spec) ✅ LIVRÉ

> **⚑ Verdict révisé.** Les deux portes **valident** sur l'historique complet en
> 1 h (`no_pullback` : +108/+170 sur BTC, +170/+146 sur ETH ; `direction` :
> +0/+65 et +101/+61). Le rejet initial reposait sur 12 000 barres et 49 trades
> OOS. Elles restent **off par défaut** : leur activation est une décision de
> trading, et elles réduisent la perte sans la retourner.
>
> **Acquis durable** : la convention BOS/MSS/CHoCH existe enfin, une seule fois,
> et L4/L6 s'y adossent.
>
> Le postulat de §62 (« entrer en WARNING est pire ») n'est pas testable : 3 à 5
> trades par compartiment. Reporté à L8, avec plus de symboles.
>
> **Règle nouvelle pour la suite du plan :** toute règle dérivée d'une lecture de
> résultats doit être vérifiée sur la fenêtre qui n'a pas servi à la former.
> Détail : `docs/MOTEUR_STRUCTURE_SEQUENTIEL.md`.

**Objectif** : §60–§64, §73, §82, §108 — la mémoire de structure.

**Travaux**

1. **`app/core/smc_state.py`** (nouveau) — consomme `analyze()`, n'y touche pas :
   ```python
   STATES = ("UNKNOWN","RANGING","BULLISH","BULLISH_PULLBACK","BULLISH_WARNING",
             "BEARISH","BEARISH_PULLBACK","BEARISH_WARNING",
             "REVERSAL_BULLISH_PENDING","REVERSAL_BEARISH_PENDING",
             "BULLISH_CONFIRMED","BEARISH_CONFIRMED")

   def structure_states(res, params) -> dict:
       """Série d'états par barre + protected_high/low par barre + événements
       MSS/BOS/CHoCH qualifiés. Causal : l'état à la barre i n'utilise que
       les entités dont confirm_index <= i."""
   ```
2. **Convention interne unique** (§61), documentée en tête du module et **seule
   référence du dépôt** :
   - **BOS** = clôture au-delà du dernier swing **dans le sens** de la structure
     → continuation.
   - **MSS** = sweep de liquidité **puis** displacement **puis** cassure du
     dernier LH (bull) / HL (bear) → changement interne, produit un *warning*.
   - **CHoCH** = première cassure contraire sans displacement suffisant →
     *avertissement précoce*, ne change pas l'état.
   `structure_events` de `analyze()` continue d'émettre BOS/CHoCH comme
   aujourd'hui (aucune régression) ; `smc_state` les **qualifie**.
3. **Cassure valide vs faible** (§60.3) : clôture au-delà **et** displacement
   ≥ `disp_body_atr` × ATR. Une mèche seule ne change jamais l'état.
4. **Protected high / low** (§64) : dernier HL ayant permis un nouveau HH (et
   miroir), exposé par barre.
5. **Failed reversal** (§73) : état `FAILED_BEARISH_REVERSAL` /
   `FAILED_BULLISH_REVERSAL` quand un MSS contraire ne produit pas de LH/HL puis
   se fait invalider par un BOS dans le sens initial.
6. **Branchement stratégie** : `smart_money` reçoit `structure_state` dans
   `_SignalCtx`. Nouveau paramètre `structure_gate` (défaut **off**) qui
   n'autorise l'entrée que dans certains états. Off = comportement actuel.

**Contraintes non négociables**

- **Causalité** : `tests/test_smc_state_causal.py` sur le patron de
  `tests/test_features_smc.py` — la série calculée sur `df[:k]` doit être
  identique au préfixe de la série calculée sur `df` complet, pour 20 valeurs de
  `k`.
- **Performance** : passe unique O(n), pas de recalcul par barre. Mesurer avec
  `scripts/` avant/après sur 50 000 barres.

**Critère d'acceptation** : `by_structure_state` dans le backtest montre le taux
de réussite par état d'entrée. Question à laquelle il faut répondre par un
chiffre : **entrer en `WARNING` est-il pire qu'entrer en `CONFIRMED` ?** Si
l'écart est nul, `structure_gate` reste off et on le dit.

**Livrable** : `docs/MOTEUR_STRUCTURE_SEQUENTIEL.md`.

---

### L4 — Liquidité hiérarchisée et qualité des zones ✅ LIVRÉ

> **Résultat : §77 est contredit.** Le seul compartiment à échantillon
> exploitable (`SWING`, 19–28 trades) est le rang le PLUS BAS de la hiérarchie,
> et c'est le meilleur — sur BTC 4 h il bat `PREV_WEEK` d'un facteur 16. Les
> classes nobles comptent 1 à 7 trades. `target_mode: expected_value` gagne sur
> une fenêtre et perd sur l'autre dans les quatre cas : **rejeté**.
> `max_stop_atr` (§23) ne mord pas — résultats identiques au bit près sur 3/4.
>
> §3.5 de ce plan avait prévu le mécanisme ; c'est maintenant mesuré. La voie
> ouverte reste l'estimation des fréquences en walk-forward (L8) : `proba` est
> déjà un paramètre de `meilleure_cible`. Les onze fonctions de qualité restent
> des entrées candidates du score de L6 — leur échec porte sur le CIBLAGE.
> Détail : `docs/MESURE_HIERARCHIE_LIQUIDITE.md`.

**Objectif** : §12, §15, §32, §65–§67, §77–§79, §83–§86, §91.

**Travaux**

1. **`liquidity_class`** sur chaque pool (§77) : `HTF_EXTERNAL` > `PREV_WEEK` >
   `PREV_DAY` > `SESSION` > `SWING` > `INTERNAL`. Champ ajouté dans
   `smc_structure.analyze()` et `smc_sessions.calendar_liquidity_levels()`.
2. **IRL / ERL** (§66) et **dealing range explicite** (§67) : `smc_geometry`
   expose `dealing_range(res, i, mode)` → `{high, low, mid, source}` avec
   `mode ∈ {htf, session, swing}`, et le signal porte `internal_target` /
   `external_target`.
3. **Inducement** (§65) : détecteur `inducement_pools(res, i)` — petit
   swing interne entre le POI et le prix, non balayé. Le flag
   `require_inducement` existant s'y branche enfin.
4. **Qualité mesurée** : `raid_quality` (§83 — profondeur au-delà du niveau,
   ratio mèche/corps, vitesse de reclaim, volume), `displacement_quality`
   (§84 — body/ATR, range/ATR, position de clôture, structure cassée),
   `fvg_rank` (§85 — le FVG né du displacement causant le MSS reçoit le rang
   max), `ob_quality` (§86). Toutes bornées `[0, 1]`, toutes causales.
5. **Ciblage** (§78, §79) : `_build_trade` ne prend plus la *première* cible qui
   passe le RR, mais celle qui maximise `expected_target_value =
   target_probability × net_reward`. `target_probability` vaut 1.0 par type tant
   que L8 ne l'a pas estimée (cf. §3.5) — le classement se fait alors sur
   `liquidity_class` puis `net_rr`.
6. **Anti-FOMO** (§32) : refus si la distance entre le prix et le POI dépasse
   `p["max_poi_distance_r"]` × R, ou si la cible visée a déjà été consommée.
7. **Daily / weekly / monthly open** (§91) : ajout dans `smc_sessions`, exposé
   comme niveau de contexte (bonus de score, jamais de déclenchement).
8. **`mitigation_pct`** continu (§15) sur les FVG, refus au-delà de
   `p["fvg_max_mitigation"]` (défaut 0.80).

**Critère d'acceptation** : ablation module par module (patron
`scripts/measure_smc_ablation.py`). Chaque brique de qualité qui n'améliore ni
le PF OOS ni le Sharpe OOS est **désactivée par défaut** et documentée comme
telle — comme l'ont été `BREAKER_RETEST` et `BPR_REVERSAL`.

---

### L5 — Régime, volatilité et multi-timeframe ✅ LIVRÉ (partiel)

> **⚠ Correction de ce plan.** Le point 3 ci-dessous affirmait qu'« aucune
> stratégie n'utilise `df_htf` ». **C'est faux** : neuf le font
> (`breakout`, `breakout_filtreHor`, `fear_momentum`, `gemini_trend_follow`,
> `multi_tf_sr`, `pullback_trend`, `supertrend_macd`, `trend`, `tvr_trend`).
> La divergence n'était donc pas latente mais **active** : `htf_trend(None)`
> renvoie 0, donc leur filtre HTF était inerte en backtest et actif en live.
>
> **Corrigé par le repli de rééchantillonnage** (option b), pas par le passage
> de `df_htf` au backtest — deux édits au lieu de trente-quatre, et l'invariant
> anti-fuite reste structurel. ⚠ Les `optimizer_results` de ces neuf stratégies
> ont été mesurés avec un filtre inerte : **à recalibrer**.
>
> Livré aussi : `atr_percentile` (§76) et `mtf_alignment` (§81 §82).
> **Non livré :** la comparaison des deux moteurs de régime (point 2) et
> l'entry refinement 5 m (point 5), reportés — le régime est un axe de mesure
> pour L8, pas un mécanisme à trancher avant.

**Objectif** : §6, §75, §76, §80, §81.

**Travaux**

1. **ATR percentile** (§76) — `indicators_core` : `atr_percentile(df, n, lookback)`
   causal. Deux seuils absolus s'en nourrissent aujourd'hui et devraient devenir
   relatifs : `risk.volatility_threshold` (0.05), entrée du
   `volatility_brake_factor` (`app/core/risk_gate.py:266`,
   `app/engine/backtest_risk_gate.py:384`), et `atr_volatile_threshold` (3.0),
   entrée de la classification de régime (`app/core/indicators_market.py:72`).
   Un seuil absolu d'ATR% ne signifie pas la même chose sur BTC 2018 et sur une
   action du SBF 120 — c'est précisément l'argument de §76. Les deux formes
   coexistent le temps de la mesure.
2. **Régime** (§75) : **ne pas fusionner** avec `classify_regime()`. Mesurer les
   deux comme filtre de décision (et non comme feature ML — `docs/ML_ABLATION_SMC.md`
   §3 a déjà montré que la feature n'apporte rien, ce qui ne présage pas de
   l'usage filtre). Le perdant est supprimé.
3. **Multi-timeframe réel** (§6, §81) — **décision : rééchantillonnage, et
   retrait de `df_htf` du contrat.**

   *Le problème.* `df_htf` est accepté par `BaseStrategy.score()` et **passé par
   le live** (`app/live/signal_pipeline.py:115`,
   `app/live/position_open_mixin.py:580`) mais **jamais par le backtest** (0
   occurrence dans `app/engine/backtest.py`). Aucune stratégie ne l'utilise
   aujourd'hui — `smart_money` ne fait que le déclarer
   (`smart_money_plans.py:13`). C'est donc une divergence backtest/live
   **latente** : la première stratégie qui s'en servira aura deux comportements
   sans que rien n'échoue.

   *La décision.* Généraliser `_htf_buckets` à **une liste de cibles HTF**, et
   supprimer `df_htf` de la signature de `score()`. Trois raisons, par ordre
   d'importance :

   1. **L'invariant anti-fuite devient structurel au lieu d'être déclaratif.**
      `_htf_buckets` mappe déjà chaque barre LTF sur le dernier bucket HTF
      *entièrement clôturé* (`searchsorted(bucket_end, epoch + ltf_sec)`) : il
      n'existe aucun chemin de code donnant accès à un bucket non clos. Avec un
      chargement séparé, la jointure d'une série 4h sur un index 1h peut fuiter
      jusqu'à 3 h de futur si elle s'aligne sur le timestamp d'ouverture au lieu
      de la clôture. `ict.align_series` sait le faire correctement, mais c'est
      une discipline à ré-appliquer à chaque site d'appel, indéfiniment.
   2. **Le chargement séparé contamine toute la couche engine.** Elle est
      uniformément « une df en entrée » : `Backtester.run(df, …)`,
      `WalkForwardAnalyzer.run(df, …)`, `run_dual_pass(engine, cfg, df, …)`. Le
      Backtester ne charge pas de données — on l'alimente — et l'optimiseur en
      crée un par essai. Y injecter N frames (ou un callback de fetch) se
      propage à `walk_forward`, `forward_test`, `opt_workers` et aux routes
      `backtest`/`replay`. Le retrait de `df_htf`, lui, est un diff mécanique :
      34 fichiers de stratégie, une ligne chacun, aucune logique touchée.
   3. **Une seule source de données** : deux jeux OHLCV ne peuvent pas diverger
      (snapshot, comblement de trous, convention de clôture).

   *Forme à implémenter.* Pas « empiler des `mult` » — c'est le fallback.
   `_htf_buckets` accepte déjà une cible nommée via `htf_sec_map[ltf_sec]`
   (alignée sur `_HTF_MAP` d'`app/live/utils`). La généralisation est donc :

   ```python
   def htf_buckets_multi(df, htf_targets: list[int], params) -> dict[int, tuple]
   # une passe O(n) par cible ; aucun concept ni invariant nouveau
   ```
   `mtf_alignment_score` (§81) consomme la sortie ; la règle §82 (un TF bas
   contraire = *pullback*, jamais annulation du biais HTF) s'applique dessus.

   *Le coût réel de cette décision, à traiter dans le lot.* `bucket =
   epoch // htf_sec` découpe sur l'horloge **UTC**. Exact en crypto ; **faux sur
   actions** : un « 4h » Euronext découpé à 12:00 UTC coupe la séance en deux et
   fabrique une bougie qu'aucune place n'a publiée. La réponse n'est pas le
   chargement séparé, c'est des **bornes session-aware** dans `_htf_buckets`,
   en réutilisant `SessionCalendar` (`app/core/market_calendar.py`) qui connaît
   déjà XPAR. Tant que ce n'est pas fait, **interdire les cibles HTF
   intra-journalières sur les venues `asset_class: equity`** (le bucket
   journalier, lui, reste correct pour Paris — toute la séance tombe dans la
   même journée UTC).

   *Contrôle de fidélité, à faire une fois.* `CandleStore` stocke par
   `(symbol, timeframe)` : comparer le 4h rééchantillonné depuis le 1h au 4h
   natif est runnable aujourd'hui. `scripts/check_htf_resampling.py` — écart
   sur `high`/`low`/`close` par bucket, sur BTC et sur une action SBF 120.
   S'il est nul en crypto et non nul sur action, c'est exactement le symptôme
   des bornes de session ci-dessus, et ça le prouve au lieu de le supposer.
4. **`mtf_alignment_score`** (§81) : score continu, avec la règle §82 — un TF bas
   contraire vaut **pullback**, jamais annulation du biais HTF.
5. **Entry refinement 5m** (§80) : **reporté**. Dépend de (a), et le gain
   théorique (SL plus serré) est exactement ce que L0/L1 vont mesurer comme
   risque (sortie prématurée).

---

### L6 — Un score, une séquence, un événement

**Objectif** : §20, §48, §69–§72, §74, §97, §98, §71.

**Travaux**

1. **Score unique décomposé.** `_score_setup` conserve son échelle `[0, 1]` et
   son barème actuel comme **référence**, mais expose sa décomposition :
   ```python
   signal["score_breakdown"] = {
       "htf": .., "liquidity": .., "sweep": .., "displacement": ..,
       "structure": .., "poi": .., "premium_discount": .., "draw": ..,
       "timing": .., "cost": ..,
   }
   ```
   Un second barème `scoring_profile: "spec_v4"` (poids de §20/§69/§70) est
   ajouté **en option** dans `param_space`, et les deux sont comparés par
   ablation. Le perdant est supprimé. **Aucun barème n'est ajouté sans être
   comparé.**
2. **`sequence_id` / `sequence_type`** (§72) : UUID par séquence structurelle,
   `sequence_type ∈ {CONTINUATION, REVERSAL, EARLY_REVERSAL, FAILED_REVERSAL}`
   dérivé de L3. Porté par le signal, journalisé, agrégé
   (`BacktestResult.by_sequence_type`).
3. **Tiers de setup** (§71) : `tier ∈ {A, B, C, D}` dérivé de la séquence et du
   score. `D` → pas de trade. `C` → `size_factor` réduit. Se branche sur le
   `size_factor` **existant**, pas sur un nouveau mécanisme.
4. **`market_event_id` et déduplication** (§97) : un sweep + son displacement
   forment un événement ; tous les signaux qui en découlent partagent l'id. Règle
   « 1 événement → 1 trade primaire ».
5. **Cooldown intelligent** (§98) : `reentry_cooldown_bars` devient un
   **plancher**, levé par un nouvel événement de liquidité, un nouveau BOS ou une
   nouvelle session — jamais par le simple écoulement du temps seul.

**Critère d'acceptation** : `by_sequence_type` et `by_tier` publiés. Si
`FAILED_REVERSAL` (§73) affiche un edge, c'est le résultat le plus intéressant de
tout le plan et il mérite son propre document.

---

### L7 — Risque adaptatif et portefeuille (verrou V3)

**Objectif** : §28, §33, §94, §95, §96.

**Travaux**

1. **Sizing multiplicatif explicite** (§94) — `risk_sizer.compute_risk()` rend
   sa décomposition auditable :
   `final = base × score_factor × volatility_factor × correlation_factor ×
   drawdown_factor × execution_factor`. Quatre facteurs sur six existent déjà
   (`size_by_confluence`, `volatility_brake_factor`, `_risk_multiplier(dd)`,
   et le tier de L6) : le travail est de les **nommer et les exposer**, pas de
   les réinventer.
2. **Clusters de corrélation** (§28, §96) : `app/core/risk_clusters.py` —
   budget de risque par cluster (BTC/ETH/alts L1/alts DeFi ; pour les actions :
   secteur), alimenté par `correlation_matrix.py` sur fenêtre glissante. Refus
   ou réduction si le budget cluster est consommé.
3. **Backtest multi-position** : c'est le sous-chantier lourd. Deux niveaux :
   - **niveau 1 (suffisant pour mesurer)** : un `PortfolioBacktester` qui rejoue
     N backtests mono-symbole déjà produits et applique **a posteriori** les
     contraintes de portefeuille (positions simultanées, budget cluster, DD
     global) en refusant les trades qui n'auraient pas pu être pris. Peu
     invasif, immédiatement mesurable.
   - **niveau 2 (fidèle)** : boucle multi-symbole synchronisée sur l'horloge.
     À n'entreprendre que si le niveau 1 montre que la contrainte de portefeuille
     change matériellement le résultat.

   **Commencer par le niveau 1.**

---

### L8 — Protocole de mesure

**Objectif** : §49–§53, §79 (estimation de probabilité), §101–§105.

**Travaux**

1. **Harnais d'ablation généralisé** : `scripts/measure_smc_ablation.py` étendu à
   tous les flags introduits par L3–L6, avec une seule commande produisant le
   tableau « module retiré → Δ PF OOS, Δ Sharpe OOS, Δ trades ».
2. **Estimation de `target_probability`** (§79) : fréquence historique
   d'atteinte par `liquidity_class`, estimée **en walk-forward** (fenêtre
   d'estimation strictement antérieure à la fenêtre de test).
3. **Robustesse par régime** (§103) : `regime_stress_test.py` étendu aux
   nouveaux états de structure.
4. **Rapport de validation** : un script unique produisant, pour une stratégie ×
   symbole × TF, le verdict `beats_baseline` + Deflated Sharpe + Monte Carlo +
   walk-forward, dans un format comparable d'un lot à l'autre.

**Règle** : tout module introduit par L3–L7 qui ne survit pas à l'ablation est
**désactivé par défaut** et documenté comme tel. La spec l'exige (§102), le dépôt
le pratique déjà.

---

### L9 — Actions / PEA

**Objectif** : §35–§40, §92. **À faire en dernier**, pour trois raisons : la
venue est data-only, la donnée manque, et l'échantillon de trades sera petit.

**Travaux**

1. **Donnée d'abord** : fournisseur pour (a) le calendrier de résultats,
   (b) l'appartenance sectorielle, (c) les niveaux indice (CAC/SBF). Cache, TTL,
   mode dégradé explicite. `app/core/providers.py` / `yfinance_provider.py` sont
   les points d'entrée.
2. **Filtres ensuite** : blackout earnings (§40), bonus indice/secteur (§37),
   plancher de volatilité `ATR(14) ≥ 1 %` (§38), fenêtre macro pour la crypto
   (§92).
3. **Exécution sur gap** : vérifier que le backtest actions modélise l'ouverture
   au lieu du stop lors d'un gap (`market_calendar` gère les sessions ; le
   remplissage au prix du stop est optimiste sur actions).
4. **Profil PEA** : jeu de paramètres dédié dans `strategies/smart_money.yaml`
   sous la venue `euronext-paper` — plus sélectif, long-only (déjà imposé par
   `allow_short: false`), R/R net supérieur. **Valeurs à trouver par
   optimisation, pas à recopier de la spec.**

---

### L10 — Modules à laisser en veille

`SMT` (§43), `Silver Bullet` (§44), `AMD` (§45), `Breaker` (§87), `BPR` (§88),
`Judas`, `Unicorn`, `IFVG` : **tous déjà implémentés**, tous désactivés par
défaut, plusieurs déjà mesurés comme négatifs. La spec elle-même (§110) demande
qu'ils restent optionnels. **Aucun travail à faire dessus** tant que L0–L8 n'ont
pas rendu leur verdict — sauf les repasser dans l'harnais d'ablation de L8 une
fois que la construction du trade aura changé (leur valeur a pu changer avec
elle).

---

## 5. Règles d'exécution pour l'agent

1. **Une PR par lot**, avec son entrée `CHANGELOG.md` et son document de mesure.
   Intégration en **merge commit ou rebase, jamais `--squash`**.
2. **Séparer les correctifs de justesse des paramètres de trading.** Un lot qui
   corrige un calcul ET change un seuil n'est pas relisable : deux commits, ou
   deux PR.
3. **Causalité prouvée, pas affirmée.** Tout nouveau calcul par barre a son test
   de préfixe (patron : `tests/test_features_smc.py`).
4. **Parité backtest ↔ live.** Toute modification du sizing, des coûts ou de la
   gestion de position doit laisser passer `tests/test_backtest_live_parity.py`
   et `tests/test_execution_parity.py` — les étendre, jamais les contourner.
5. **Défauts inchangés.** Chaque nouveau paramètre a un défaut qui reproduit le
   comportement actuel à l'identique. Un backtest lancé avant et après un lot,
   sans toucher la config, doit donner **exactement** le même résultat. Ce test
   de non-régression fait partie du lot.
6. **Ne pas invalider `optimizer_results`.** Les jeux de paramètres mesurés dans
   `strategies/*.yaml` sont des données, pas du code : renommer un paramètre
   impose de migrer les résultats ou de documenter leur péremption.
7. **Réutiliser l'outillage du dépôt** : `split_is_oos`, `composite_score`,
   `beats_baseline`, `deflated_sharpe_ratio`, `Rejections`, `scripts/measure_*`.
   Ne pas recoder un backtest ni un découpage IS/OOS.
8. **Publier les résultats négatifs.** C'est la pratique établie du dépôt
   (`docs/STRATEGY_SMC_ML_EDGE.md` §3 quinquies) et la seule qui rende les
   documents fiables.
9. **Vérifier en exécutant l'application**, pas seulement les tests : un module
   peut être vert en test et inerte en production (Docker, cf.
   `docs/DOCKER.md`).

---

## 6. Critères d'acceptation globaux

Le plan est réussi si, à la fin de L8 :

1. Un backtest `smart_money` (ou son successeur) passe `beats_baseline` **et**
   affiche un Deflated Sharpe > 0 sur au moins **deux symboles décorrélés** et
   **deux timeframes**, sur la découpe OOS standard du dépôt.
2. La question « pourquoi les positions sortent-elles avant la cible ? » a une
   réponse chiffrée et un correctif mesuré.
3. Chaque module SMC/ICT activé par défaut a démontré son apport par ablation ;
   les autres sont off et documentés.
4. `net_rr`, `funding` et `execution quality` entrent dans la décision d'entrée,
   et le R/R annoncé à l'entrée coïncide avec le R réalisé.
5. `structure_state` et `sequence_type` sont journalisés, et leurs statistiques
   par état sont publiées.

Si le point 1 échoue après L8, **la conclusion à publier est que la stratégie SMC
règles-seules n'a pas d'edge exploitable dans cet espace de paramètres** — pas
qu'il faut ajouter un module de plus. C'est déjà la direction que pointent les
mesures existantes, et un plan qui n'envisage pas son propre échec ne mesure
rien.

---

## 7. Ce que ce plan ne traite pas

- L'exécution réelle sur PEA (chantier G3 du `docs/PLAN_DIRECTEUR_MULTI_ACTIFS.md`) :
  `euronext-paper` reste `can_execute: false`.
- Le ML (`smc_ml_edge`, catalogue `v5_smc@1`) : il consomme les mêmes briques et
  bénéficiera de L1–L4, mais son cycle de vie est traité par
  `docs/CONCEPTION_CYCLE_DE_VIE_ML.md`.
- L'UI : les nouveaux champs (`structure_state`, `sequence_type`, `net_rr`,
  `by_*`) devront être exposés, mais c'est un lot de suite, pas un prérequis.
- La sécurité et l'ops, couverts par les plans directeurs existants.
