# Audit — Architecture & simplification

> Audit post-refonte « configs par symbole » (2026-07-11). Chaque item est
> autonome : un agent peut l'exécuter avec la seule directive ci-dessous.
> Format : Priorité P1 (critique) → P3 (confort) ; Effort S/M/L.
> Règle transverse du repo : tout changement touchant le trading doit être
> **byte-identique** quand la feature est off (empreinte de régression), et la
> suite `python -m pytest -q` doit rester verte.

### [ARCH-01] Divergence de résolution des params live vs backtest (risque de parité)
- Priorité: P1 | Effort: M | Fichiers: app/live/utils.py:48-70 (_merge_params), app/live/utils.py:73,110-158 (resolve_strategy_params/_GLOBAL_PARAM_KEYS), app/live/signal_pipeline.py:151-154, app/engine/backtest.py:594
- Problème : `resolve_strategy_params()` filtre explicitement `_GLOBAL_PARAM_KEYS` (score_threshold, risk_per_trade, capital, max_positions, taker_fee, maker_fee…) avant d'écraser les params de base — utilisé par `Backtester.run()` et scanner.py. `_merge_params()`, utilisé uniquement par `SignalPipeline._score_symbol_slot` côté live, écrase TOUTES les clés de l'overlay sans ce filtre. Un `optimizer_results[...].params` contenant par erreur une clé globale serait bloqué en backtest mais appliqué en live.
- Directive : faire de `_merge_params` un wrapper qui délègue le filtrage à la même logique que `resolve_strategy_params` (ou faire consommer par `signal_pipeline.py` directement `resolve_strategy_params(cfg, tf, symbol)` au lieu de `_merge_params(base, entry["params"])`). Ne pas changer la signature publique de `SignalPipeline.collect()`.
- Acceptation : nouveau test unitaire injectant une clé de `_GLOBAL_PARAM_KEYS` dans un entry optimisé et vérifiant qu'elle n'apparaît pas dans les params mergés côté live ; `pytest tests/test_scoring_alignment.py tests/test_execution_parity.py tests/test_resolve_symbol_params.py` verts.

### [ARCH-02] Couche app/engine dépend de app/live (inversion de dépendance)
- Priorité: P1 | Effort: L | Fichiers: app/engine/opt_persistence.py:149,238,256, app/engine/opt_workers.py:165, app/engine/backtest.py:13, app/strategies/smart_money.py:70, app/strategies/vizion.py:33
- Problème : `resolve_strategy_params`, `DEFAULT_CONFIG_SYMBOL`, `_select_symbol_entry`, `_is_legacy_tf_entry`, `_HTF_MAP` sont définis dans app/live/utils.py (couche « live ») mais importés par app/engine et app/strategies. Le docstring d'utils.py admet lui-même « évitant les imports circulaires » — placement de contournement, pas de choix architectural.
- Directive : créer app/core/param_resolution.py regroupant `_is_nan_like`, `_clean_param_dict`, `_merge_params`, `DEFAULT_CONFIG_SYMBOL`, `_is_legacy_tf_entry`, `_select_symbol_entry`, `resolve_strategy_params`, `_GLOBAL_PARAM_KEYS` ; déplacer aussi `_HTF_MAP` vers app/core/timeframes.py (cf ARCH-08). Remplacer dans app/live/utils.py par des ré-exports le temps de migrer tous les call sites, puis supprimer les ré-exports.
- Acceptation : `grep -rn "from app.live" app/engine app/strategies app/core` retourne 0 résultat ; suite de tests complète verte (comportement byte-identique, seul le chemin d'import change).

### [ARCH-03] get_active_strategies_per_tf réimplémente sa propre sélection tf/symbole
- Priorité: P1 | Effort: M | Fichiers: app/engine/opt_persistence.py:244-290, app/live/live_trader.py:218 (_build_active_per_tf)
- Problème : `get_active_strategies_per_tf()` reconstruit une boucle candidates/MIN_VIABLE_SCORE/top_n directement sur `cfg["optimizer_results"]` au lieu de déléguer à `resolve_strategy_params`/`_select_symbol_entry` pour la sélection par symbole ; troisième chemin de résolution parallèle de la même précédence « strategy_params < optimizer_results[strat][tf][symbol] ».
- Directive : factoriser la sélection candidate-par-symbole dans une fonction unique réutilisée par `get_active_strategies_per_tf` et `resolve_strategy_params`, en gardant `get_active_strategies_per_tf` responsable uniquement du tri/top_n/seuil OOS.
- Acceptation : tests/test_symbol_slots.py et tests/test_phase1_bot_unit.py verts ; test de non-régression comparant `_active_per_tf` avant/après sur une config fixture.

### [ARCH-04] live_trader.py importe app.api (couche live → couche api)
- Priorité: P2 | Effort: M | Fichiers: app/live/live_trader.py:576-591 (_persist_allocator_budgets)
- Problème : `_persist_allocator_budgets` fait `from app.api import state as _api_state` puis `from app.api.routes.config import _save_yaml` — le module live dépend d'un helper privé d'un fichier de routes FastAPI, et lit `state.cfg` directement. Même inversion qu'ARCH-02 mais vers le haut de la pile (api).
- Directive : déplacer `_save_yaml` (et toute logique d'écriture YAML partagée) d'app/api/routes/config.py vers app/core/yaml_io.py (déjà existant) ; faire pointer live_trader.py et routes/config.py vers cette fonction core ; injecter le chemin de config au lieu de lire `app.api.state`.
- Acceptation : `grep -rn "from app.api" app/live` retourne 0 résultat ; tests/test_allocator_persistence.py vert.

### [ARCH-05] Clé de slot reconstruite manuellement au lieu d'utiliser bot_identity._slot_key/parse_slot_key
- Priorité: P2 | Effort: M | Fichiers: app/core/bot_identity.py:200-215 (helpers canoniques), app/live/live_trader.py:457,555-556,975, app/live/capital_allocator.py:111, app/live/signal_pipeline.py:126-128, app/core/oos_tracker.py:279-280
- Problème : au moins 9 sites construisent le format `"{strategy}::{tf}::{symbol}"` ou `"{symbol}::{strategy}::{tf}"` via des f-strings ad hoc au lieu des helpers canoniques. Deux ordres de champs coexistent (slot_key vs pos_key) sans garde-fou commun.
- Directive : ajouter `_pos_key(symbol, strategy, tf)` à côté de `_slot_key` dans app/core/bot_identity.py, remplacer chaque construction manuelle listée par un appel à ces helpers.
- Acceptation : `grep -rEn 'f"\{[a-z_.]+\}::\{' app/live app/core` ne retourne plus que les définitions dans bot_identity.py ; tests/test_symbol_slots.py, tests/test_capital_allocator.py verts.

### [ARCH-06] Fichier-dieu app/live/live_trader.py (1235 lignes)
- Priorité: P2 | Effort: L | Fichiers: app/live/live_trader.py:40, lignes 814-1075 (auto-opt/reopt), 911-1160 (heartbeat/dead-man/status)
- Problème : LiveTrader cumule cycle de trading, scheduling auto-optimizer (`_maybe_auto_optimize`, `_auto_opt_thread`, `_trigger_reopt`, `_on_opt_applied`), forward-test, heartbeat/dead-man switch et reporting — le pattern PositionMixin/BalanceSyncMixin existe déjà et n'est pas étendu à ces blocs.
- Directive : extraire lignes 814-1075 vers app/live/auto_opt_mixin.py (classe `AutoOptMixin`), lignes 879-937+1105-1235 vers app/live/health_mixin.py (classe `HealthMixin`) ; faire hériter `LiveTrader(PositionMixin, BalanceSyncMixin, AutoOptMixin, HealthMixin)`.
- Acceptation : live_trader.py < 500 lignes ; tests/test_phase1_bot_unit.py, tests/test_phase3_resilience.py, tests/test_e2e_trading.py verts sans modification.

### [ARCH-07] Fichier-dieu app/api/routes/scanner.py (992 lignes) — logique métier dans les routes
- Priorité: P2 | Effort: L | Fichiers: app/api/routes/scanner.py:251-341 (_setup_series_v8), 342-480 (_setup_series_v11), 519-754 (scanner_smc), 756-895 (scanner_smc_replay)
- Problème : trois fonctions privées de calcul (~90/~140/~236 lignes) sont mêlées aux handlers FastAPI, sans couche service — impossible à tester unitairement sans monter les routes.
- Directive : créer app/api/services/scanner_service.py et y déplacer `_atr_at`, `_tp_sl`, `_setup_series_v8`, `_setup_series_v11`, et la logique de `scanner_smc`/`scanner_smc_replay` ; les routes ne font plus que parser les query params et appeler le service.
- Acceptation : scanner.py < 300 lignes ; réponse JSON de `/api/scanner/smc` byte-identique sur un jeu de données fixe avant/après.

### [ARCH-08] Mapping HTF/TF-secondes dupliqué dans 4 endroits
- Priorité: P2 | Effort: M | Fichiers: app/live/utils.py:165-179 (_HTF_MAP), app/live/position_mixin.py:36-37 (_TF_SECS), app/live/ohlcv_cache.py:38,44 (deux maps), app/strategies/vizion.py:37-41 (_TF_SEC + _HTF_SEC_MAP recalculé)
- Problème : `_HTF_MAP` est déclaré « source unique de vérité » mais vizion.py recalcule localement `_HTF_SEC_MAP`, tandis que position_mixin.py et ohlcv_cache.py définissent leurs propres tables tf→secondes indépendantes. smart_money.py et core/smc.py référencent aussi `_HTF_MAP` depuis une couche qui ne devrait pas dépendre d'app.live.
- Directive : créer app/core/timeframes.py avec `TF_SECONDS` canonique + `HTF_MAP` + `tf_to_seconds()` ; migrer position_mixin.py, vizion.py, smart_money.py, core/smc.py vers cet import ; renommer explicitement la map de ohlcv_cache.py si son usage (polling) diffère sémantiquement.
- Acceptation : un seul littéral par concept dans tout le repo ; tests/test_vizion.py, tests/test_ohlcv_forming.py verts.

### [ARCH-09] core/oos_tracker.py dépend de app.engine (violation core→engine)
- Priorité: P2 | Effort: M | Fichiers: app/core/oos_tracker.py:196-197 (_forward_test_slot)
- Problème : `_forward_test_slot()` importe `app.engine.engine.Engine` et `app.engine.backtest.Backtester/MonteCarlo` à l'intérieur de la fonction — app/core dépend d'app/engine (import scopé = contournement de cycle).
- Directive : déplacer `_forward_test_slot` (et les fonctions à usage exclusif forward-test) vers app/engine/forward_test.py ; ne garder dans oos_tracker.py que les fonctions purement analytiques (contrat MC, comparaison bandes).
- Acceptation : `grep -rn "from app.engine" app/core` retourne 0 résultat ; tests forward-test verts.

### [ARCH-10] Constantes de frais dupliquées (taker/maker fee)
- Priorité: P2 | Effort: S | Fichiers: app/core/config.py:33 (DEFAULTS canonique 0.001/0.0004), app/api/routes/config.py:374-375, app/api/routes/scanner.py:22, app/core/fast_analysis.py:118, app/live/position_mixin.py:332,753,936
- Problème : les défauts `taker_fee=0.001`/`maker_fee=0.0004` sont recopiés à 6+ endroits (défauts de fonction et fallback `cfg.get(..., 0.001)`) — un changement du défaut canonique n'impacterait pas ces sites (PnL/backtest incohérents avec la config réelle).
- Directive : exporter `DEFAULT_TAKER_FEE`/`DEFAULT_MAKER_FEE` depuis app/core/config.py et remplacer tous les littéraux `0.001`/`0.0004` de fallback de frais par ces constantes.
- Acceptation : `grep -rn '0\.001\|0\.0004' app/core app/live app/api` ne montre plus que la définition canonique et les tests ; tests/test_pnl_borrow_cost.py, tests/test_reconcile_costs.py verts.

### [ARCH-11] DEFAULT_CONFIG_SYMBOL défini une fois mais 25 littéraux "BTC/USDC" indépendants
- Priorité: P3 | Effort: S | Fichiers: app/live/utils.py:82, 16 fichiers dont app/api/routes/scanner.py (5 défauts de paramètre), app/engine/{auto_optimizer,backtest,optimizer,scanner}.py, app/core/config.py:45
- Problème : la constante existe mais 25 occurrences brutes de `"BTC/USDC"` restent codées en dur comme valeur par défaut — renommer le symbole de référence exigerait de toucher 16 fichiers.
- Directive : après ARCH-02, importer `DEFAULT_CONFIG_SYMBOL` partout où un défaut de symbole est nécessaire dans app/api et app/engine ; ne garder le littéral que dans la définition et dans app/core/config.py:45 (liste scanner = config, pas défaut).
- Acceptation : occurrences littérales de "BTC/USDC" hors définition/config/tests divisées par ≥3 ; tests verts.

### [ARCH-12] État global mutable app/api/state.py importé par 14 modules
- Priorité: P3 | Effort: L | Fichiers: app/api/state.py (cfg, trader, SessionLocal, 7 locks/semaphores/caches module-level), 13 fichiers app/api/routes/* + app/live/live_trader.py
- Problème : variables de module mutées via `global` et lues directement par 14 fichiers, y compris live_trader.py (cf ARCH-04). Empêche le test isolé des routes sans monkeypatch, et interdit plusieurs instances de bot par process.
- Directive (incrémentale) : encapsuler dans une dataclass `AppState`, exposer via `app.state` FastAPI + `Depends(get_app_state)` pour les routes nouvellement modifiées ; prioritairement, supprimer la dépendance de live_trader.py à `app.api.state` (résolu par ARCH-04).
- Acceptation : live_trader.py ne référence plus `app.api.state` ; tests d'API existants verts sans changement de comportement observable.

### [ARCH-13] get_store()/get_feature_store() : singleton dupliqué + chemins data/ codés en dur
- Priorité: P3 | Effort: S | Fichiers: app/core/candle_store.py:361-372, app/core/feature_store.py:383-396, app/core/derivatives.py:101
- Problème : pattern double-checked-locking singleton recopié à l'identique dans candle_store.py et feature_store.py ; chemins `"data/ohlcv"`, `"data/features"`, `"data/derivatives"` codés en dur chacun dans leur fichier.
- Directive : factoriser un helper générique `_lazy_singleton(factory)` dans app/core ; définir `DATA_ROOT` dans app/core/config.py et faire dériver les 3 chemins par défaut.
- Acceptation : tests/test_feature_store.py, tests/test_feature_store_integration.py verts ; un seul point de définition pour la racine `data/`.

### [ARCH-14] Fichiers-dieux app/core/smc.py (1083 l.) et app/strategies/smart_money.py (1178 l.)
- Priorité: P3 | Effort: L | Fichiers: app/core/smc.py:95-500 (analyze), 780-848 (volume_profile), 849-953 (killzone/session/smt_series), app/strategies/smart_money.py:93-1177 (Strategy monolithique : _signal_at ~330 lignes, _build_trade ~130 lignes)
- Problème : smc.py mélange structure de marché, profil de volume et sessions/killzones ; smart_money.py concentre cache d'analyse, scoring, trade plans et construction de signaux dans une classe.
- Directive : scinder smc.py en smc_structure.py (analyze/_zigzag/_trendlines/premium-discount), smc_volume.py (volume_profile/regression_channel), smc_sessions.py (killzone_flags/session_label/smt_series) — smc.py devient façade ré-exportant l'API publique (aucun call site cassé) ; dans smart_money.py, extraire `_signal_at`/`_build_trade` (665-1122) vers smart_money_signals.py.
- Acceptation : chaque nouveau module < 450 lignes ; tests/test_smc.py vert avec sorties byte-identiques sur les fixtures existantes.

---

**Note transverse (preuve)** : 144 imports scopés `from app.` à l'intérieur de fonctions (dont 19 dans scanner.py, 18 dans live_trader.py, 14 dans api/routes/optimizer.py) — usage systématique du contournement de cycle. ARCH-01/02/04/05/09 en sont les instances les plus risquées (parité live/backtest d'abord, maintenabilité ensuite).
