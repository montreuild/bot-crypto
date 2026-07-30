# Audit — Backtester, Optimiseur & Risque

> Audit post-refonte « configs par symbole » (2026-07-11). Chaque item est
> autonome : un agent peut l'exécuter avec la seule directive ci-dessous.
> Format : Priorité P1 (critique) → P3 (confort) ; Effort S/M/L.
> ⚠ BT-01 est une **conséquence directe de la refonte per-symbole** (la route
> d'apply manuelle n'a pas été migrée) : à traiter en premier.

### [BT-01] `/api/optimize/apply` écrase les configs des autres symboles (corruption cross-symbole)
- Priorité: P1 | Effort: S | Fichiers: app/api/routes/optimizer.py:198-236 ; app/engine/opt_persistence.py:122-198 (branche else 174-178) ; app/live/utils.py:85-107
- Problème: `optimizer_apply()` appelle `apply_best_params(...)` **sans** passer `symbol`, alors que le job stocke bien `symbol`. Quand `symbol=None`, la branche else exécute `opt[timeframe] = entry` : si `opt[timeframe]` était un mapping par symbole `{"BTC/USDC": {...}, "ETH/USDC": {...}}`, il est intégralement remplacé par une entrée plate — qui redevient « héritée » (= BTC/USDC seulement). Les configs ETH/SOL disparaissent silencieusement et des params optimisés pour un autre symbole sont mal attribués. Le chemin auto (`_run_one_job`) est correct ; seule la route API manuelle est fautive.
- Directive: Dans `optimizer_apply`, lire `symbol = job.get("symbol")` et le transmettre à `apply_best_params(..., symbol=symbol)`.
- Acceptation: Test : lancer deux jobs (BTC puis ETH) sur la même stratégie/TF, appliquer les deux via `/api/optimize/apply`, vérifier que `optimizer_results[tf]["BTC/USDC"]` ET `["ETH/USDC"]` restent intacts. Ajouter à tests/test_symbol_slots.py.

### [BT-02] MonteCarlo.run() : permutation sans remise → IC sur l'équité finale dégénéré
- Priorité: P1 | Effort: S | Fichiers: app/engine/backtest.py:963-995 (ligne 979 `rng.permutation(pnls)`) ; app/core/oos_tracker.py:219-234
- Problème: `rng.permutation` ne fait que réordonner les PnL — la somme est invariante : `final_equity_mean/p5/p95` retournent tous LA MÊME valeur et `prob_profit` ne peut valoir que 0 ou 100. Seuls `max_dd_p95` et `prob_ruin_10pct` (dépendants de l'ordre) sont valides. Le défaut se propage à `oos_tracker` qui présente p5/p95 comme une fourchette. Aucun test ne couvre MonteCarlo.
- Directive: Séparer les deux usages : garder la permutation pour `max_dd_p95`/`prob_ruin` (risque de séquence) ; ajouter un bootstrap avec remise (`rng.choice(pnls, size=len(pnls), replace=True)`) pour la distribution d'équité finale. Nommer clairement (`order_risk.*` vs `sampling_risk.*`).
- Acceptation: `tests/test_monte_carlo.py` : avec des PnL non tous égaux, `final_equity_p5 != final_equity_p95` après fix (test qui échoue avant, passe après).

### [BT-03] max_notional_pct : défaut divergent Backtester (0.50) vs RiskManager live (0.20)
- Priorité: P1 | Effort: S | Fichiers: app/engine/backtest.py:230 ; app/core/risk.py:57 ; config.yaml:92-95 ; app/core/config.py:37-40
- Problème: les deux lisent `cfg["backtest"]["max_notional_pct"]` avec des replis différents (50 % vs 20 %) — et la clé n'existe ni dans config.yaml ni dans DEFAULTS. Le backtest plafonne à 50 % du capital pendant que le live plafonne à 20 % : un paramétrage validé en backtest est structurellement irréproductible en live (tailles ×2,5).
- Directive: Ajouter `max_notional_pct` dans `DEFAULTS["backtest"]` avec UNE valeur canonique (0.20, alignée risk manager) ; faire lire cette clé par `Backtester.__init__` sans défaut divergent ; documenter dans config.yaml.
- Acceptation: assert sur DEFAULTS ; backtest de non-régression (byte-identique si aucun trade ne dépassait 20 %, sinon écart documenté sur un run de référence).

### [BT-04] Application manuelle des résultats d'optimisation sans aucun garde-fou qualité
- Priorité: P1 | Effort: M | Fichiers: app/api/routes/optimizer.py:198-236 ; app/engine/auto_optimizer.py:481-501 (_beats_baseline)
- Problème: l'auto-apply exige `_beats_baseline()` (≥3 trades OOS, PnL OOS > 0 et > baseline, amélioration WR/Sharpe). Le bouton « Appliquer » de l'UI ne fait AUCUNE de ces vérifications : il applique dès que le job est done, même avec PnL OOS négatif ou 1-2 trades.
- Directive: Extraire `_beats_baseline` en fonction pure dans opt_scoring.py ; l'appeler dans `optimizer_apply` — si échec, HTTP 409 avec le détail (PnL/WR/Sharpe vs baseline). Flag `force=true` pour override explicite.
- Acceptation: POST apply sur un job `best_oos_pnl <= 0` ou `< 3 trades` → 409, YAML non modifié.

### [BT-05] Espace de recherche (jusqu'à ~1,7×10¹¹ combos) vs n_trials=40
- Priorité: P1 | Effort: M | Fichiers: app/engine/optimizer.py:257,291 ; app/api/routes/optimizer.py:25,46 ; app/strategies/opus_omnibus_v11_followsetup_no_ml.py:184 (22 params) ; app/strategies/smart_money.py:102 (27 params)
- Problème: produit cartésien des param_space : 1,76×10¹¹ combos (opus v11_followsetup_no_ml), 1,5×10¹⁰ (smart_money)… couverture explorée ~10⁻¹⁰ avec n_trials=40 → risque élevé de surapprentissage par sélection multiple (TODO déjà noté dans auto_optimizer.py:481-488, jamais corrigé).
- Directive: (1) log/metric au démarrage de chaque StrategyOptimizer : taille du param_space vs n_trials, warning si couverture < 1e-4. (2) réduire les param_space des stratégies >1e8 combos (basculer les paramètres à faible impact en fixed_params) ou scaler n_trials en log. (3) créer `scripts/audit_param_space.py` listant chaque stratégie avec combos et n_trials effectif.
- Acceptation: script d'audit exécutable ; revue refusant un param_space au-delà du seuil sans justification.

### [BT-06] Seuils de significativité trop bas et incohérents (min_trades=2, fidelity_min_fills=2, baseline=3)
- Priorité: P1 | Effort: S | Fichiers: app/engine/opt_scoring.py:18,37 ; app/engine/auto_optimizer.py:489-501 ; app/live/slot_lifecycle.py:46-52,104-111
- Problème: composite_score traite ≥2 trades comme significatif ; `_beats_baseline` exige 3 ; le lifecycle promeut un bot en ACTIF avec 2 fills live. Trois seuils (2, 3, 10 recommandé) sans justification statistique commune.
- Directive: constante partagée `MIN_SIGNIFICANT_TRADES` (ex. 10) dans un module commun ; l'utiliser pour la décision d'apply et la promotion lifecycle ; réserver 2-3 au filtre anti-dégénérescence (documenté comme tel).
- Acceptation: seuils documentés en un seul endroit avec justification (marge binomiale) ; test : aucun bot ACTIF sous le seuil retenu.

### [BT-07] Walk-Forward jamais utilisé dans la boucle d'auto-apply
- Priorité: P2 | Effort: M | Fichiers: app/engine/backtest.py:886-959 (WalkForwardAnalyzer) ; app/engine/auto_optimizer.py:481-488 (TODO)
- Problème: le WalkForwardAnalyzer (5 folds) n'est câblé que derrière un flag manuel des routes backtest/replay et du CLI. Le pipeline qui décide l'apply live ne fait qu'UN split IS/OOS — un paramétrage peut battre la baseline sur un seul split et être auto-appliqué.
- Directive: après sélection de best_params, passe de validation (activée par défaut pour l'auto-apply) : WalkForwardAnalyzer avec params FIGÉS (pas de re-optimisation par fold) sur df_full ; exiger `consistency >= X%` en plus de `_beats_baseline()`.
- Acceptation: test d'intégration : best_params avec consistency < seuil refusés même s'ils passent _beats_baseline ; raison de refus loguée.

### [BT-08] Conventions IS/OOS incohérentes entre modules
- Priorité: P2 | Effort: M | Fichiers: app/engine/auto_optimizer.py:320-323,658-660,703-706 (WARMUP=210 ×3, split 0.65 en dur) ; app/engine/optimizer.py:82-108 (_OOS_FRACTION=0.35) ; app/engine/backtest.py:892-899 (WARMUP=220) ; app/core/oos_tracker.py:41-54 (_WARMUP_BARS=250)
- Problème: ≥4 conventions coexistent sans constante partagée : warmup 210 (dupliqué 3×), 220, 250 ; fraction OOS 0.35 définie dans optimizer.py mais 0.65 en dur dans auto_optimizer.py — divergences silencieuses possibles.
- Directive: créer `app/core/is_oos.py` : `WARMUP_BARS_DEFAULT`, `OOS_FRACTION_DEFAULT=0.35`, `split_is_oos(df, warmup, oos_fraction) -> (df_is, df_oos, split_idx)`. Migrer les 3 sites d'auto_optimizer + required_total_bars. Documenter pourquoi WalkForward (folds) et oos_tracker (fenêtre glissante) restent des cas distincts, mais leur faire importer la même constante warmup.
- Acceptation: un seul site pour le warmup/fraction ; run d'optimisation avant/après → indices de coupure byte-identiques.

### [BT-09] Le backtest ne réduit jamais le risque en drawdown, contrairement au live
- Priorité: P2 | Effort: M | Fichiers: app/engine/backtest.py:507,656 ; app/core/risk.py:417-425
- Problème: le Backtester fixe `risk_per_trade` une fois pour tout le run ; le live (`compute_risk`) réduit ×0.75 si DD>5 %, ×0.5 si DD>10 %. Le backtest simule des tailles pleines en pleine séquence de pertes → les métriques DD/Sharpe de sélection ne reflètent pas le comportement réel.
- Directive: factoriser `risk_multiplier(dd_pct)` dans un module partagé (app/core/risk_curve.py) importé par les deux ; suivre peak_equity dans ctx et appliquer le multiplicateur dans `_try_enter`.
- Acceptation: byte-identique sur une période sans DD>5 % ; écart documenté avant/après sur une période à DD>10 %.

### [BT-10] Modèle de coûts d'exécution statique — aucune dépendance à la taille
- Priorité: P2 | Effort: M | Fichiers: app/engine/backtest.py:224-230 ; app/core/execution.py:21-38
- Problème: `spread_pct` et `partial_fill_pct` constants pour tous les trades quel que soit le notionnel — un trade à 50 % du capital subit le même slippage qu'un trade à 1 %. Les stratégies à fort size_factor/pyramidage paraissent plus viables qu'en réalité.
- Directive: modèle de slippage croissant avec la taille relative (`spread_effective = spread_pct × (1 + k·notional/volume_moyen)` ou paliers), configurable `backtest.slippage_model` (défaut = comportement actuel).
- Acceptation: modèle off → byte-identique ; écart PnL/Sharpe documenté avec modèle actif sur une stratégie à fort notionnel.

### [BT-11] Pas de plafond d'exposition cumulée sur actifs corrélés (BTC/ETH ~0.8)
- Priorité: P2 | Effort: M | Fichiers: app/live/capital_allocator.py:277-314 (check_correlation), 18-22
- Problème: garde-fous = ratio directionnel global (≥75 % même sens) + plafond PAR symbole (25 %). Aucun plafond de groupe corrélé : BTC 25 % + ETH 25 % longs = 50 % d'exposition sur des actifs qui bougent ensemble, sans veto.
- Directive: config `correlated_groups: [["BTC/USDC","ETH/USDC"]]` (ou corrélation glissante des rendements 1h) ; dans check_correlation, exposition cumulée du groupe vs `max_correlated_group_exposure_pct` (35-40 %).
- Acceptation: test : BTC à 24 % puis ETH portant le groupe au-delà du plafond → refusé même si chaque symbole < 25 %.

### [BT-12] Route API `/api/optimize/start` reste mono-symbole
- Priorité: P2 | Effort: S | Fichiers: app/api/routes/optimizer.py:19-31,73-93,111-120 ; app/live/live_trader.py:848-869 ; optimize_runner.py:163
- Problème: seul le scheduler interne boucle sur `scanner.symbols`. La route API (utilisée par optimizer.html) et optimize_runner.py n'acceptent qu'un symbole — l'opérateur doit répéter l'appel par symbole (risque d'oubli/incohérence).
- Directive: paramètre optionnel `symbols` (liste CSV) sur `optimizer_start()`, bouclant comme `_auto_opt_thread` (extraire la logique partagée `run_multi_symbol_optimization(cfg, symbols, ...)` importée par les deux). Rétrocompatible.
- Acceptation: `POST /api/optimize/start?symbols=BTC/USDC,ETH/USDC` crée des jobs `strategy@tf@symbol` pour les deux ; l'appel mono-symbole existant inchangé.

### [BT-13] Ambiguïté TP/SL intrabar résolue par convention non mesurée
- Priorité: P3 | Effort: S | Fichiers: app/engine/backtest.py:356-391 (_manage_open_position)
- Problème: quand TP et SL sont touchés dans la même barre, le stop l'emporte toujours (`if tp_val is not None and not stop_hit`). Choix « conservateur » documenté mais jamais mesuré (fraction de trades concernés ? biais PnL ?).
- Directive: compteur diagnostique `diag["tp_sl_ambiguous_bars"]` (calculer les deux conditions indépendamment SANS changer la décision), exposé dans BacktestResult.diagnostics ; publier la fraction sur un run de référence.
- Acceptation: métrique publiée ; si >5 %, ticket de suivi pour convention alternative.
