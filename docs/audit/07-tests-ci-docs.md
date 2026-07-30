# Audit — Tests, CI & documentation

> Audit post-refonte « configs par symbole » (2026-07-11). Chaque item est
> autonome : un agent peut l'exécuter avec la seule directive ci-dessous.
> Format : Priorité P1 (critique) → P3 (confort) ; Effort S/M/L.

### [TEST-01] Absence totale de CI workflows
- Priorité: P1 | Effort: S | Fichiers: .github/workflows (création)
- Problème: Aucun workflow GitHub Actions. 470 tests pytest existent mais ne s'exécutent jamais au commit — aucune barrière avant merge sur main. Risque de fusion de code cassé.
- Directive: Créer `.github/workflows/test.yml` déclenché sur PR et push vers main : setup Python 3.11+, `pip install -r requirements.txt`, `python -m pytest tests/ -q --tb=short`, plus un job lint (`python -m pyflakes app/` en attendant TEST-05/ruff). Attention : certains tests peuvent dépendre de `data/ohlcv` versionné — vérifier qu'ils passent sur un clone frais, sinon les marquer et les exclure du workflow (cf TEST-06).
- Acceptation: Workflow visible dans GitHub Actions, tests verts sur un push.

### [TEST-02] Module LiveTrader jamais instancié dans les tests — ✅ RÉALISÉ (2026-07-13)
- Priorité: P1 | Effort: M | Fichiers: app/live/live_trader.py:40-150, tests/
- Problème: `app.live.live_trader.LiveTrader` (le cœur du trading) n'est jamais importé dans tests/ (grep = 0). Les tests e2e ne l'exercent que via cli.py — pas d'unittests isolés. Toute régression du live passe inaperçue.
- Directive: Ajouter `tests/test_live_trader.py` avec fixtures de mock exchange : tests d'instanciation et chemins clés (init, _build_active_per_tf, reload_active_strategies, status, _restore_open_positions). Minimum 5 tests.
- Acceptation: `pytest tests/test_live_trader.py -v` exécute 5+ tests, tous PASSED.
- **Réalisation** : `tests/test_live_trader.py`, **12 tests** (mini. 5 demandé) construisant une vraie instance `LiveTrader` avec `MockExchange` (fetch_ticker/fetch_positions/fetch_balance en mémoire, zéro réseau) + DB sqlite jetable (`tmp_path`, `init_db`) :
  - instanciation (`TestInstantiation` ×2) : construction sans réseau, et vérification que `_load_all_strategies()` charge bien `PARAM_SPACES ∪ strategies.enabled` (~45 stratégies) et pas seulement la liste `enabled` — comportement réel découvert en inspectant le code, pas supposé.
  - `_build_active_per_tf` (×2) : fallback sur `strategies.enabled` (score 0.0) quand `optimizer_results` est vide ; bascule sur le score OOS réel quand `optimizer_results` est renseigné.
  - `reload_active_strategies`/`reload_strategies` (×2) : rechargement depuis un nouvel `optimizer_results` ; et **découverte empirique** que `reload_strategies(enabled)` ne recharge pas de nouveaux modules (déjà tous chargés par `_load_all_strategies`) mais **retire** ceux hors de la liste — testé sur le chemin réellement emprunté (vérifié par exécution directe avant d'écrire l'assertion), pas sur une hypothèse.
  - `status` (×2) : forme du dict retourné, et garde-fou `not callable(type(trader).status)` — c'est une `@property` côté `HealthMixin`, une régression vers une méthode casserait silencieusement les routes API qui la lisent sans parenthèses.
  - `_restore_open_positions` (×3) : restaure une position persistée (`persist_open_position` + `session_scope`), rejette une position à prix d'entrée invalide (0.0), no-op sur DB vide.
  - `stop()` (×1) : chemin sans position ouverte, ne lève pas.
  - Vérifié : `pytest tests/test_live_trader.py -v` → 12 passed ; suite complète 549 passed (537 + 12, aucune régression, aucun état global partagé entre tests grâce à `tmp_path`/instance par test).

### [TEST-03] Toutes les routes FastAPI non testées via TestClient — ✅ RÉALISÉ (2026-07-13)
- Priorité: P1 | Effort: L | Fichiers: app/api/routes/*.py (12 fichiers), tests/
- Problème: Les routes (backtest, bot, config, data, derivatives, ml, optimizer, portfolio, replay, scanner, trades) ne sont pas testées avec le TestClient FastAPI. Risque d'erreurs 500, routes cassées, paramètres ignorés (le parsing slot_key 3-parties vient d'ailleurs de casser silencieusement 2 routes lors de la refonte).
- Directive: Créer `tests/test_api_routes.py` avec `from starlette.testclient import TestClient`. Pour chaque route majeure : cas nominal + cas d'erreur. Minimum : data_status, data_refetch (400 sans symbole), scanner fast_analysis (400 données insuffisantes), portfolio bots (symbol exposé), strategy_performance (slot 2 et 3 parties).
- Acceptation: 8+ tests d'API verts, couvrant GET/POST nominaux et gestion d'erreurs.
- **Réalisation** : `tests/test_api_routes.py`, **10 tests** (mini. 8 demandé) sur les 5 routes citées, plus un test d'auth :
  - Obstacle non anticipé par la directive : `verify_api_key` bloque par défaut toute requête « non locale » (aucune clé API configurée) — or `TestClient` n'a pas l'IP `127.0.0.1`, donc **tous** les appels auraient dû échouer en 403 sans traitement particulier. Résolu via `app.dependency_overrides[verify_api_key] = lambda: None` (mécanisme FastAPI standard pour ce cas), appliqué par une fixture `client` avec teardown (`dependency_overrides.pop`) pour ne pas fuiter entre tests.
  - `data_status` (200, structure `datasets`) ; `data_refetch` sans symbole ni `scanner.symbols` configuré (400) ; `scanner/fast_analysis` sur un symbole sans cache (400, message « insuffisantes ») ; `portfolio` sans trader (200, forme par défaut) et **avec** un vrai `LiveTrader` attaché à `state.trader` (200, `bots[0]["symbol"] == "BTC/USDC"` — confirme l'exposition du symbole) ; `strategy_performance` en 2 parties (`trend_rider::4h`) et 3 parties (`trend_rider::4h::BTC/USDC`, `/` encodé en `%2F` dans l'URL, `:path` converter) — les deux renvoient `strategy`/`tf`/`slot_key` correctement décomposés ; format invalide → 400 ; `SessionLocal` absent → 503 (garde-fou testé séparément).
  - **Test d'auth dédié** (`test_protected_route_rejects_unauthenticated_non_local_request`) qui n'utilise PAS le contournement, pour vérifier que le bypass de test ne masque pas une vraie régression de la couche auth : un `TestClient` brut reçoit bien 403 sur une route protégée.
  - État global (`app.dependency_overrides`, `app.api.state.cfg/trader/SessionLocal`) manipulé exclusivement via la fixture `client` (teardown systématique) et `monkeypatch` (restauration automatique par test) — pas d'effet de bord d'un test sur les suivants, vérifié en relançant la suite complète après ce fichier.
  - Vérifié : `pytest tests/test_api_routes.py -v` → 10 passed ; suite complète 559 passed (549 + 10, aucune régression).

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

### [TEST-07] Composants live sans tests isolés (position_mixin, slot_lifecycle, balance_sync) — ✅ RÉALISÉ (2026-07-13)
- Priorité: P2 | Effort: M | Fichiers: app/live/position_mixin.py, app/live/slot_lifecycle.py, app/live/balance_sync.py
- Problème: Couverts seulement par les tests d'intégration phase*/e2e — pas d'unittests isolés du cycle de vie de position ni de la sync de balance. Une régression de clôture de position passe inaperçue.
- Directive: Créer `tests/test_position_lifecycle.py` (ouverture→gestion→fermeture, mock exchange) et `tests/test_balance_sync.py` (sync paper/spot/margin). Minimum 3 tests par fichier.
- Acceptation: 6+ tests verts couvrant open/manage/close et les 3 modes de sync.
- **Réalisation** : **17 tests** au total (mini. 6 demandé), sur une vraie instance `LiveTrader` (MockExchange en mémoire + DB sqlite jetable), périmètre `position_mixin.py`/`balance_sync.py` (`slot_lifecycle.py` a son propre test dédié pré-existant, `tests/test_phase2_lifecycle_alloc.py` — non dupliqué ici).
  - `tests/test_position_lifecycle.py` (7 tests) : `_try_open_from_signal` (création réussie avec les bons champs stop/side/strategy ; bloqué par `risk.halted` ; bloqué par `max_positions`) ; `_manage_position` (prix stable → reste ouverte ; gap sous le stop → clôture forcée, PnL négatif persisté en base) ; `_close_position` (PnL positif crédité au capital paper + trade persisté ; `pos_id` inconnu → no-op sans lever).
  - `tests/test_balance_sync.py` (10 tests) : `_sync_paper_balance` (base seule, PnL non réalisé long ajouté, position `_reserved` ignorée) ; `_sync_spot_balance` (cash − emprunt, valeur de marché d'une position longue incluse) ; `_sync_margin_account` (met à jour `_margin_level` ; niveau critique → `risk.halted=True`) ; `_pre_execution_check` (capital suffisant/insuffisant, notionnel déjà verrouillé par une autre position pris en compte).
  - Piège découvert et neutralisé : `_manage_position` appelle inconditionnellement `ohlcv_cache.get()` pour `check_early_exit`/`check_scale_in` si la stratégie expose ces hooks (`trend_rider` les a) — sans `fetch_ohlcv` sur le mock, l'appel exchange échoue proprement (dégradation gracieuse déjà présente dans le code, aucune écriture disque sur échec — vérifié en lisant `CandleStore.fetch`). `fetch_ohlcv` stub ajouté au mock (retourne `[]`) pour un test silencieux plutôt que de compter sur le warning catché.
  - Vérifié : `pytest tests/test_position_lifecycle.py tests/test_balance_sync.py -v` → 17 passed ; suite complète 576 passed (559 + 17, aucune régression).

### [TEST-08] Schéma `optimizer_results[strat][tf][symbol]` non documenté dans le code — ✅ RÉALISÉ (2026-07-13)
- Priorité: P2 | Effort: S | Fichiers: app/core/config.py, app/live/utils.py (resolve_strategy_params)
- Problème: Le CHANGELOG détaille le nouveau schéma par symbole mais aucun docstring du code source ne définit la structure exacte (types, clés optionnelles, migration héritée → BTC/USDC). Onboarding difficile.
- Directive: Ajouter dans app/core/config.py (chargement strategies/*.yaml) un docstring avec le schéma EXACT : `{'smart_money': {'4h': {'BTC/USDC': {'run_date','oos_score','params'}}}}` + la règle de rétro-compat (entrée sans symbole = BTC/USDC, ne s'applique pas aux autres symboles). Croiser avec le docstring de resolve_strategy_params.
- Acceptation: docstring présent avec schéma et exemple ; référencé depuis ARCHITECTURE.md.
- **Réalisation** : fichier corrigé — `resolve_strategy_params` a déménagé dans `app/core/param_resolution.py` lors de V4-B (ARCH-02), plus dans `app/live/utils.py` (ré-export de compatibilité seulement) ; ce module possédait déjà d'excellents docstrings détaillés (`_select_symbol_entry`, `_is_legacy_tf_entry`, `resolve_strategy_params`) décrivant la règle de rétro-compat exacte. Le vrai trou était `_load_strategy_configs` dans `app/core/config.py`, dont le docstring montrait encore l'ancien schéma à 2 niveaux (`{tf: {run_date, oos_score, params}}`, sans dimension symbole). Étendu avec le schéma exact à 3 niveaux, l'exemple demandé (`smart_money/4h/BTC-ETH`), la règle de rétro-compat, et un renvoi croisé vers `param_resolution.py` + la nouvelle section ARCHITECTURE.md. Vérifié : `python3 -c "import app.core.config"` sans erreur ; 576 tests verts (docstring seul, aucun changement de comportement).

### [TEST-09] CHANGELOG [Non publié] excessivement long (~750 lignes) — ✅ RÉALISÉ (2026-07-13)
- Priorité: P3 | Effort: M | Fichiers: CHANGELOG.md
- Problème: La section [Non publié] accumule bugfixes + features + refactors sans découpage de version — impossible d'extraire une release.
- Directive: Découper en versions datées (regrouper par jalon : moteur SMC, indicateurs, trend_rider, vizion, configs par symbole), garder [Non publié] court. Optionnel : script `scripts/release.py` qui fige [Non publié] sous un tag.
- Acceptation: [Non publié] ≤ 150 lignes ; l'historique découpé reste intégral.
- **Réalisation** : le bloc faisait en réalité **817 lignes** (30 sous-sections `###`), pas ~750. Découpé en **10 versions datées** (`12.8.0` → `12.17.0`, à la suite de `12.7.0` déjà publiée), par jalon comme demandé (configs par symbole, SMT, données OHLCV+Vizion+ICT, trend_rider, smart_money×indicateurs, trailing/time-stop, moteur SMC+Smart graph/replay, performance+optimiseur en 3 groupes). Dates réelles tirées de `git log --follow -- CHANGELOG.md` (date du dernier commit de chaque jalon), pas inventées. `[Non publié]` : 3 lignes.
  - Fait **par script** (`/tmp/.../split_changelog.py`, jetable) plutôt qu'à la main : découpe le fichier aux 30 frontières `### ` exactes (vérifiées ligne par ligne avant écriture), regroupe, insère les en-têtes `## [x.y.z] - date` + séparateurs `---` (convention déjà présente entre les autres versions du fichier), sans jamais réécrire le texte des sous-sections — élimine tout risque de coquille de recopie sur 817 lignes.
  - Vérifié : chacune des 30 sous-sections retrouvée **exactement une fois, verbatim**, dans le nouveau fichier (script de contrôle) ; la portion `## [12.7.0]` et tout ce qui suit est **byte-identique** à l'original (comparaison Python) ; `git diff --stat` confirme **42 insertions, 0 suppression** — aucune ligne de contenu original modifiée ou perdue, seuls les nouveaux en-têtes/séparateurs sont des ajouts purs. 576 tests verts (fichier non exécutable, aucune régression possible).
  - Script `scripts/release.py` (optionnel dans la directive) non fait — non nécessaire une fois le rattrapage effectué, et un futur épisode de dérive se traiterait de la même façon.

### [TEST-10] ARCHITECTURE.md ne documente pas le flux live — ✅ RÉALISÉ (2026-07-13)
- Priorité: P3 | Effort: S | Fichiers: ARCHITECTURE.md, app/live/live_trader.py
- Problème: ARCHITECTURE.md existe (14 Ko) mais ne couvre pas le flux live (LiveTrader loop → OHLCVCache → SignalPipeline → PositionMixin → BalanceSync → exchange), ni les slots par symbole ni le capital allocator.
- Directive: Ajouter une section « Live Trading Loop » : diagramme ASCII du flux, description des slots `strategy::tf::symbol`, du cycle de vie (candidat/essai/actif/retiré) et de l'allocation de capital.
- Acceptation: section présente, relue, cohérente avec le code actuel.
- **Réalisation** : nouvelle section `## 🔴 Live Trading Loop` (entre « Flux de données » et « Modules clés »), 4 sous-sections : composition des 4 mixins (V4-J) + composés (OHLCVCache/SignalPipeline/CapitalAllocator) ; diagramme ASCII détaillé de `_cycle()` (gestion positions avec ses 5 branches de sortie, pipeline signaux, gating complet de `_try_open_from_signal`, synchro capital, rééquilibrage, + tâches planifiées hors-cycle) ; slots `strategy::tf::symbol` (formats `build_slot_key`/`build_pos_key`, ordres différents, règle héritée) ; cycle de vie des bots (diagramme ASCII candidat→essai→actif→retiré avec les conditions de transition exactes, `edge_min_trades`/`fidelity_min_fills`/`plancher_budget_pct` vérifiés dans `slot_lifecycle.py` avant d'être documentés) ; allocation de capital (3 modes, shadow allocation, rééquilibrage, persistance). Pointeurs croisés ajoutés depuis l'ancien diagramme sommaire de « Flux de données » et depuis l'entrée `app/live/live_trader.py` de « Modules clés » (tous deux restaient corrects mais incomplets — pas réécrits pour limiter le diff, juste reliés à la nouvelle section détaillée). Chaque affirmation (noms de méthodes, champs de config, seuils par défaut) vérifiée par lecture directe du code source avant rédaction, pas supposée. Relu pour cohérence avec le code actuel ; 576 tests verts (doc uniquement).

### [TEST-11] Stratégies : ~13/53 testées
- Priorité: P3 | Effort: L | Fichiers: app/strategies/*.py, tests/
- Problème: ~40 stratégies sans test (opus_omnibus_v7-12, scoring_statistique v1-5…). NOTE : à exécuter APRÈS le tri du code mort (cf. 02-code-mort.md) — inutile d'écrire des tests pour des stratégies qui seront archivées.
- Directive: Après archivage, ajouter un test smoke paramétré : pour chaque stratégie restante, instancier + `score()` sur 300 bougies synthétiques (pas de crash, dict conforme {score, side, name}). Marker `@pytest.mark.strategy_smoke`.
- Acceptation: 1 test smoke par stratégie conservée, tous verts.

### [TEST-12] Reproductibilité de l'environnement — ✅ RÉALISÉ (2026-07-13)
- Priorité: P3 | Effort: S | Fichiers: requirements.txt
- Problème: Versions partiellement épinglées ; installer sur une machine vierge n'est pas garanti reproductible (ccxt/polars évoluent vite et cassent des APIs).
- Directive: Épingler toutes les versions (pip freeze trié pour les deps directes), commenter la date de lock, tester `pip install -r requirements.txt` dans un venv vierge + `pytest -q`.
- Acceptation: installation vierge complète sans erreur, tests verts.
- **Réalisation** : constat après lecture — le fichier avait DÉJÀ toutes ses dépendances directes épinglées en `==` (probablement corrigé entre l'écriture de l'audit le 2026-07-11 et maintenant). Le vrai travail restant : **vérifier** que ces pins installent réellement ensemble sans conflit sur une machine vierge (jamais testé), pas les deviner depuis l'environnement de ce conteneur (qui s'est avéré ne PAS avoir été provisionné depuis ce fichier — plusieurs paquets requis par requirements.txt, ex. `psutil`/`optuna`/`black`/`mypy`/`pytest-cov`, étaient absents, et les versions présentes avaient dérivé). « pip freeze trié » depuis cet environnement ad hoc aurait donc figé un état jamais réellement testé contre ce dépôt.
  - Créé un **vrai venv Python 3.12 vierge** (`python3.12 -m venv`, conforme à l'en-tête du fichier « Python 3.12 REQUIRED »), `pip install -r requirements.txt` : **succès sans aucun conflit de résolution**, chaque paquet installé exactement à la version épinglée. `pytest -q` dans ce même venv : **576 passed**. Les deux critères d'acceptation sont donc remplis avec les pins **existants**, sans changer un seul numéro de version (aucune preuve qu'une version soit cassée — les changer aurait été un risque non justifié).
  - Ajouté le commentaire de **date de lock** manquant (2026-07-13, avec le résumé de la vérification) dans l'en-tête du fichier, et une note explicite sur le choix de ne pas figer les sous-dépendances transitives (pas de lockfile de type `pip-tools`/`poetry.lock` dans ce projet — hors scope de cet item, effort L pour un gain marginal tant que l'install directe reste reproductible comme vérifié ici).
  - Venv de test (850 Mo) supprimé après vérification — pas conservé dans le dépôt ni le scratchpad.
