# Audit — Tests, CI & documentation

> Audit post-refonte « configs par symbole » (2026-07-11). Chaque item est
> autonome : un agent peut l'exécuter avec la seule directive ci-dessous.
> Format : Priorité P1 (critique) → P3 (confort) ; Effort S/M/L.

### [TEST-01] Absence totale de CI workflows
- Priorité: P1 | Effort: S | Fichiers: .github/workflows (création)
- Problème: Aucun workflow GitHub Actions. 470 tests pytest existent mais ne s'exécutent jamais au commit — aucune barrière avant merge sur main. Risque de fusion de code cassé.
- Directive: Créer `.github/workflows/test.yml` déclenché sur PR et push vers main : setup Python 3.11+, `pip install -r requirements.txt`, `python -m pytest tests/ -q --tb=short`, plus un job lint (`python -m pyflakes app/` en attendant TEST-05/ruff). Attention : certains tests peuvent dépendre de `data/ohlcv` versionné — vérifier qu'ils passent sur un clone frais, sinon les marquer et les exclure du workflow (cf TEST-06).
- Acceptation: Workflow visible dans GitHub Actions, tests verts sur un push.

### [TEST-02] Module LiveTrader jamais instancié dans les tests
- Priorité: P1 | Effort: M | Fichiers: app/live/live_trader.py:40-150, tests/
- Problème: `app.live.live_trader.LiveTrader` (le cœur du trading) n'est jamais importé dans tests/ (grep = 0). Les tests e2e ne l'exercent que via cli.py — pas d'unittests isolés. Toute régression du live passe inaperçue.
- Directive: Ajouter `tests/test_live_trader.py` avec fixtures de mock exchange : tests d'instanciation et chemins clés (init, _build_active_per_tf, reload_active_strategies, status, _restore_open_positions). Minimum 5 tests.
- Acceptation: `pytest tests/test_live_trader.py -v` exécute 5+ tests, tous PASSED.

### [TEST-03] Toutes les routes FastAPI non testées via TestClient
- Priorité: P1 | Effort: L | Fichiers: app/api/routes/*.py (12 fichiers), tests/
- Problème: Les routes (backtest, bot, config, data, derivatives, ml, optimizer, portfolio, replay, scanner, trades) ne sont pas testées avec le TestClient FastAPI. Risque d'erreurs 500, routes cassées, paramètres ignorés (le parsing slot_key 3-parties vient d'ailleurs de casser silencieusement 2 routes lors de la refonte).
- Directive: Créer `tests/test_api_routes.py` avec `from starlette.testclient import TestClient`. Pour chaque route majeure : cas nominal + cas d'erreur. Minimum : data_status, data_refetch (400 sans symbole), scanner fast_analysis (400 données insuffisantes), portfolio bots (symbol exposé), strategy_performance (slot 2 et 3 parties).
- Acceptation: 8+ tests d'API verts, couvrant GET/POST nominaux et gestion d'erreurs.

### [TEST-04] Absence de configuration flake8 / mypy
- Priorité: P2 | Effort: S | Fichiers: .flake8 (création), mypy.ini (création), requirements.txt
- Problème: `flake8` et `mypy` sont dans requirements.txt mais aucun fichier de config n'existe. Style incohérent, aucun type-check exécuté.
- Directive: Créer `.flake8` (max-line-length=100, exclude __pycache__/venv, extend-ignore E501 si besoin) et `mypy.ini` (python_version, ignore_missing_imports=True, démarrage en mode non-strict). Brancher dans la CI (TEST-01).
- Acceptation: `flake8 app/` sans erreur (ou baseline documentée) ; `mypy app/` termine.

### [TEST-05] Pas de ruff (linter moderne unifié)
- Priorité: P2 | Effort: M | Fichiers: ruff.toml (création), requirements.txt
- Problème: flake8+black installés mais pas ruff : pas d'import sorting, détection unused lente, deux outils au lieu d'un.
- Directive: Ajouter `ruff` à requirements.txt. Créer `ruff.toml` : line-length=100, select = ["F","W","E","I"], exclude venv. Lancer `ruff check app/ --fix` (autofix imports), vérifier `pytest -q` après. Remplacer pyflakes/flake8 par ruff dans la CI.
- Acceptation: `ruff check app/` en <2s, 0 erreur après fix, 470+ tests verts.

### [TEST-06] Tests lents / dépendants des données versionnées
- Priorité: P2 | Effort: M | Fichiers: tests/test_feature_store_integration.py, tests/test_consensus_precompute.py, tout test lisant data/ohlcv
- Problème: Plusieurs tests >1s ; certains dépendent des parquets `data/ohlcv` versionnés (fragiles sur clone sans data, et gros checkout CI).
- Directive: `pytest --durations=10` ; remplacer les lectures data/ par des fixtures polars synthétiques quand possible ; marquer les tests restants `@pytest.mark.slow` (enregistrer le marker dans pytest.ini) et permettre `pytest -m "not slow"` pour la boucle dev + CI rapide.
- Acceptation: `pytest -m "not slow" tests/` < 5s ; markers déclarés sans warning pytest.

### [TEST-07] Composants live sans tests isolés (position_mixin, slot_lifecycle, balance_sync)
- Priorité: P2 | Effort: M | Fichiers: app/live/position_mixin.py, app/live/slot_lifecycle.py, app/live/balance_sync.py
- Problème: Couverts seulement par les tests d'intégration phase*/e2e — pas d'unittests isolés du cycle de vie de position ni de la sync de balance. Une régression de clôture de position passe inaperçue.
- Directive: Créer `tests/test_position_lifecycle.py` (ouverture→gestion→fermeture, mock exchange) et `tests/test_balance_sync.py` (sync paper/spot/margin). Minimum 3 tests par fichier.
- Acceptation: 6+ tests verts couvrant open/manage/close et les 3 modes de sync.

### [TEST-08] Schéma `optimizer_results[strat][tf][symbol]` non documenté dans le code
- Priorité: P2 | Effort: S | Fichiers: app/core/config.py, app/live/utils.py (resolve_strategy_params)
- Problème: Le CHANGELOG détaille le nouveau schéma par symbole mais aucun docstring du code source ne définit la structure exacte (types, clés optionnelles, migration héritée → BTC/USDC). Onboarding difficile.
- Directive: Ajouter dans app/core/config.py (chargement strategies/*.yaml) un docstring avec le schéma EXACT : `{'smart_money': {'4h': {'BTC/USDC': {'run_date','oos_score','params'}}}}` + la règle de rétro-compat (entrée sans symbole = BTC/USDC, ne s'applique pas aux autres symboles). Croiser avec le docstring de resolve_strategy_params.
- Acceptation: docstring présent avec schéma et exemple ; référencé depuis ARCHITECTURE.md.

### [TEST-09] CHANGELOG [Non publié] excessivement long (~750 lignes)
- Priorité: P3 | Effort: M | Fichiers: CHANGELOG.md
- Problème: La section [Non publié] accumule bugfixes + features + refactors sans découpage de version — impossible d'extraire une release.
- Directive: Découper en versions datées (regrouper par jalon : moteur SMC, indicateurs, trend_rider, vizion, configs par symbole), garder [Non publié] court. Optionnel : script `scripts/release.py` qui fige [Non publié] sous un tag.
- Acceptation: [Non publié] ≤ 150 lignes ; l'historique découpé reste intégral.

### [TEST-10] ARCHITECTURE.md ne documente pas le flux live
- Priorité: P3 | Effort: S | Fichiers: ARCHITECTURE.md, app/live/live_trader.py
- Problème: ARCHITECTURE.md existe (14 Ko) mais ne couvre pas le flux live (LiveTrader loop → OHLCVCache → SignalPipeline → PositionMixin → BalanceSync → exchange), ni les slots par symbole ni le capital allocator.
- Directive: Ajouter une section « Live Trading Loop » : diagramme ASCII du flux, description des slots `strategy::tf::symbol`, du cycle de vie (candidat/essai/actif/retiré) et de l'allocation de capital.
- Acceptation: section présente, relue, cohérente avec le code actuel.

### [TEST-11] Stratégies : ~13/53 testées
- Priorité: P3 | Effort: L | Fichiers: app/strategies/*.py, tests/
- Problème: ~40 stratégies sans test (opus_omnibus_v7-12, scoring_statistique v1-5…). NOTE : à exécuter APRÈS le tri du code mort (cf. 02-code-mort.md) — inutile d'écrire des tests pour des stratégies qui seront archivées.
- Directive: Après archivage, ajouter un test smoke paramétré : pour chaque stratégie restante, instancier + `score()` sur 300 bougies synthétiques (pas de crash, dict conforme {score, side, name}). Marker `@pytest.mark.strategy_smoke`.
- Acceptation: 1 test smoke par stratégie conservée, tous verts.

### [TEST-12] Reproductibilité de l'environnement
- Priorité: P3 | Effort: S | Fichiers: requirements.txt
- Problème: Versions partiellement épinglées ; installer sur une machine vierge n'est pas garanti reproductible (ccxt/polars évoluent vite et cassent des APIs).
- Directive: Épingler toutes les versions (pip freeze trié pour les deps directes), commenter la date de lock, tester `pip install -r requirements.txt` dans un venv vierge + `pytest -q`.
- Acceptation: installation vierge complète sans erreur, tests verts.
