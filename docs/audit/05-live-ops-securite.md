# Audit — Live trading, Ops & Sécurité

> Audit post-refonte « configs par symbole » (2026-07-11). Chaque item est
> autonome : un agent peut l'exécuter avec la seule directive ci-dessous.
> Format : Priorité P1 (critique) → P3 (confort) ; Effort S/M/L.
> ⚠ OPS-01 est une **conséquence directe de la refonte per-symbole** : à
> traiter en premier.

### [OPS-01] Migration slot per-symbole : `manual_active` et `slot_budgets` orphelins en silence
- Priorité: P1 | Effort: M | Fichiers: config.yaml:126-133,146-161 ; app/engine/opt_persistence.py:244-311 ; app/live/capital_allocator.py:98-129,146-199 ; app/live/slot_lifecycle.py:54,86,139-142
- Problème: `get_active_strategies_per_tf()` attache désormais toujours un `symbol` à chaque entrée active → `slot_key` à 3 parties partout. Or config.yaml persiste encore des clés à 2 parties : `capital_allocator.slot_budgets` (7 entrées, ex. `supertrend_macd::15m`) et `lifecycle.manual_active` (15 entrées). Le matching est strict (`in`/`==`) — ces clés ne correspondent plus à aucun slot réel. Résultat : les 15 bots que l'utilisateur croit forcés en ACTIF ne le sont plus, et les 7 budgets personnalisés sont ignorés (partage égal), sans aucun log.
- Directive: Dans `SlotLifecycleManager.__init__`/`CapitalAllocator.__init__`, ajouter une passe de compatibilité : une clé 2-parties `strategy::tf` sans match exact s'applique à tous les slots de préfixe `strategy::tf::` (+ warning listant les clés migrées). Ajouter un test de non-régression chargeant config.yaml réel et vérifiant 0 clé orpheline.
- Acceptation: au démarrage, un log liste 0 clé orpheline (ou les migre) ; `GET /api/bots` montre `manual_active=true` pour les bots historiquement forcés.

### [OPS-02] API web exposée sans authentification forte (config réelle)
- Priorité: P1 | Effort: S | Fichiers: config.yaml:174-178 ; app/api/helpers.py:48-63 ; app/core/config.py:269-281
- Problème: config.yaml a `web.host: 0.0.0.0` et `web.api_key: ''`. `verify_api_key` retombe sur un filtre « IP = localhost », dépendant d'un reverse proxy bien configuré. `load_config()` n'émet qu'un warning — le démarrage n'est jamais bloqué. Tous les endpoints mutants (`/api/bot/start|stop`, `/api/config/*`, `/api/risk/reset-halt`) sont protégés uniquement par ce filtre fragile.
- Directive: Dans `load_config`, transformer le cas `web.host in ("0.0.0.0","::") and not web.api_key` en `raise ValueError(...)` sauf override explicite (`ALLOW_INSECURE_WEB=1`). Renseigner `web.api_key` avant tout déploiement réseau.
- Acceptation: lancer `cli.py` avec host 0.0.0.0 sans api_key lève une erreur au chargement.

### [OPS-03] Watchdog dead-man jamais démarré en production
- Priorité: P1 | Effort: S | Fichiers: deploy/crypto-bot.service ; app/live/watchdog.py:127-160 ; app/live/live_trader.py:911-935
- Problème: `_heartbeat()` écrit `data/heartbeat.json`, mais c'est `app/live/watchdog.py::run()` (process séparé) qui doit lire ce battement et armer le kill-switch en cas de gel. Le seul unit systemd du dépôt ne lance que `cli.py` ; rien ne démarre le watchdog. Un thread principal figé (deadlock réseau) n'est jamais détecté.
- Directive: Ajouter `deploy/crypto-bot-watchdog.service` (`ExecStart=.../python -m app.live.watchdog`, `Restart=always`) et documenter son activation.
- Acceptation: `systemctl status crypto-bot-watchdog` actif ; supprimer `data/heartbeat.json` déclenche `data/KILL_SWITCH` dans les `timeout_s` configurés.

### [OPS-04] Notifications externes toutes désactivées par défaut
- Priorité: P1 | Effort: S | Fichiers: config.yaml:182-214 ; app/core/notifications.py:52-95,155-166 ; app/live/balance_sync.py:130-151
- Problème: `telegram/whatsapp/email_enabled: false` → `Notifier.send()` n'alimente que le flux UI et les logs. Combiné à OPS-03 : un margin level critique (HALT réel) ou un mismatch de réconciliation >5 % ne préviennent l'opérateur que s'il regarde le dashboard.
- Directive: Configurer au moins un canal (Telegram recommandé) avant tout passage en réel ; ajouter dans `load_config` un garde-fou : si `paper_mode=false` et aucun canal actif → warning CRITIQUE ou blocage.
- Acceptation: au moins un canal `*_enabled=true` avant démarrage en `paper_mode=false`, sinon refus/alerte CRITICAL.

### [OPS-05] Endpoints GET non protégés malgré une clé API configurée
- Priorité: P2 | Effort: S | Fichiers: app/api/routes/config.py:360-361,385-386 ; app/api/routes/optimizer.py:145-146,156-157,264-265
- Problème: `GET /api/backtest/settings`, `/api/config/changelog`, `/api/optimize/status`, `/api/optimize/stream`, `/api/optimize/results` n'ont pas `Depends(verify_api_key)` — accessibles sans clé, ils exposent capital, risk_per_trade, strategy_params et l'historique des optimisations.
- Directive: Ajouter `dependencies=[Depends(verify_api_key)]` sur ces 5 routes (ou documenter l'exception en commentaire si raison produit).
- Acceptation: requête sans `X-API-Key` sur ces 5 routes → 403 quand `web.api_key` est configurée.

### [OPS-06] Fetch incrémental CandleStore plafonné à 10 pages — données obsolètes après coupure longue
- Priorité: P2 | Effort: M | Fichiers: app/core/candle_store.py:179-213 ; app/live/ohlcv_cache.py:149-225
- Problème: `_fetch_incremental` plafonne à 10 pages × 1000 bougies et avance séquentiellement depuis le dernier `time` en cache. Après coupure prolongée sur TF fin, `OHLCVCache.get()` renvoie `tail(fetch_limit)` contenant des bougies du passé pendant le rattrapage : les stratégies scorent sur données périmées alors que le prix d'exécution est courant — désynchronisation signal/exécution.
- Directive: Dans `CandleStore.fetch()`, si l'écart `df_cached.max(time)` vs now dépasse un seuil (ex. 2×TF) après fetch : retourner None pour le live tant que le rattrapage n'est pas terminé, OU fetch direct des N dernières bougies (`since=None`) en priorité + backfill en tâche de fond.
- Acceptation: test simulant un gap de 20 000 bougies 1m : `OHLCVCache.get()` ne renvoie un DataFrame exploitable qu'une fois le dernier timestamp < tf_ms de now().

### [OPS-07] CandleStore : verrou en mémoire seulement, écriture non atomique
- Priorité: P2 | Effort: M | Fichiers: app/core/candle_store.py:33-44,340-345
- Problème: `_get_file_lock` = `threading.Lock` par chemin, en mémoire du process — aucune protection inter-process. Or `cli.py --backtest/--optimize` (2e process) lit/écrit les mêmes parquets pendant que le live tourne. `_save()` fait `write_parquet` direct sans fichier temporaire + `os.replace` → parquet tronqué possible (perte de données malgré le try/except de `_load`).
- Directive: Appliquer le motif d'écriture atomique de `watchdog.write_heartbeat` (`write_parquet` vers `{path}.tmp` puis `os.replace`) dans `CandleStore._save` ; documenter/interdire l'exécution simultanée CLI+live sur le même base_dir (ou `fcntl.flock`).
- Acceptation: backtest CLI en parallèle du live ne produit jamais de parquet illisible (test d'écriture concurrente simulée).

### [OPS-08] Pas de mécanisme de migration de schéma SQLite
- Priorité: P2 | Effort: M | Fichiers: app/core/database.py:145-166
- Problème: `init_db()` = `create_all` seulement (ne modifie jamais les tables existantes). Aucun Alembic ni ALTER TABLE. Toute future colonne sur `Trade`/`OpenPosition` échouera silencieusement sur une base existante.
- Directive: Introduire une fonction `_migrate_schema(engine)` (compare `PRAGMA table_info` aux colonnes attendues, exécute des `ALTER TABLE ... ADD COLUMN` idempotents) appelée après create_all — ou Alembic si l'on préfère l'outillage standard.
- Acceptation: ajouter une colonne test à `Trade`, relancer sur une trades.db existante : colonne créée sans erreur.

### [OPS-09] Index composites manquants pour les requêtes forward-test/lifecycle
- Priorité: P2 | Effort: S | Fichiers: app/core/database.py:42-47,289-342
- Problème: `get_closed_trades_for_slot`/`get_slot_live_stats` filtrent sur `strategy + timeframe + status LIKE + time >=` mais aucun index ne couvre `(strategy, timeframe, time)` — scan quasi complet à mesure que trades.db grossit (pas de purge).
- Directive: Ajouter `Index("ix_trades_strategy_tf_time", "strategy", "timeframe", "time")` à `Trade.__table_args__` + migration (cf OPS-08).
- Acceptation: `EXPLAIN QUERY PLAN` montre l'usage du nouvel index au lieu d'un SCAN.

### [OPS-10] CapitalAllocator muté sans verrou entre cycle principal et thread lifecycle
- Priorité: P2 | Effort: M | Fichiers: app/live/live_trader.py:962-1017,176-183 ; app/live/capital_allocator.py (aucun threading.Lock)
- Problème: `_lifecycle_thread` (daemon) appelle `apply_continuous_allocation()`/`compute_shadow_allocation()` qui écrivent `budget_pct` pendant que le thread principal appelle `can_allocate`/`register_open`/`register_close` sur les mêmes `SlotBudget` — aucune synchronisation → mises à jour perdues possibles sur `used_notional`/`budget_pct`.
- Directive: Ajouter un `threading.RLock` interne à `CapitalAllocator`, acquis dans toutes les méthodes lisant/écrivant `self._slots` (register_open/close, can_allocate, rebuild_slots, _apply_mode, apply_continuous_allocation, _rebalance).
- Acceptation: test de charge (thread lifecycle en boucle serrée + ouvertures/fermetures) sans incohérence entre `sum(used_notional)` et le notionnel réellement ouvert.

### [OPS-11] Config réelle : deux incohérences margin non bloquantes
- Priorité: P2 | Effort: S | Fichiers: config.yaml:6,27,41,52 ; app/core/config.py:251-267
- Problème: `margin: true` + `max_leverage: 1` (coût d'emprunt sans bénéfice) ET `margin: true` + `paper_mode: true` (coûts simulés ≠ réels → paper non représentatif). Deux warnings jamais bloquants.
- Directive: Trancher l'intention (spot pur `margin: false` tant que paper, ou `max_leverage > 1`) ; transformer ces warnings en erreurs quand `paper_mode: false`.
- Acceptation: config.yaml sans warning margin ; réintroduits avec `paper_mode: false` → chargement en échec.

### [OPS-12] `/health` ne détecte pas un thread de trading figé
- Priorité: P3 | Effort: S | Fichiers: app/api/main.py:135-145
- Problème: `health_check()` retourne `trader.running`, booléen posé à True par `start()` — un thread bloqué (deadlock) laisse `running=True` : `/health` répond « ok » à un monitoring externe alors que le bot ne scanne plus.
- Directive: Enrichir `/health` avec `heartbeat_age()` (watchdog.py) : si age > timeout_s → `status: "degraded"` + champ `heartbeat_age_s`.
- Acceptation: geler le heartbeat → `/health` bascule sur "degraded" après timeout_s.

### [OPS-13] Tolérance de budget +5 % par slot cumulable sur l'exposition agrégée
- Priorité: P3 | Effort: S | Fichiers: app/live/capital_allocator.py:239-254
- Problème: `can_allocate()` tolère +5 % par slot individuellement — avec N slots au plafond simultanément, l'exposition totale dépasse 100 % sans garde-fou agrégé.
- Directive: Ajouter dans `can_allocate()` un plafond agrégé strict `sum(used_notional)/capital ≤ 1.05` en plus du contrôle par slot.
- Acceptation: scénario 5 slots à 20 % chacun tous à +5 % simultanés → rejeté par le garde-fou agrégé.

### [OPS-14] Écritures non atomiques save_trade + update_daily_stats à la clôture
- Priorité: P3 | Effort: S | Fichiers: app/live/position_mixin.py:1010-1016
- Problème: `_close_position()` fait deux commits distincts (trade puis stats journalières) — un crash entre les deux laisse les agrégats désynchronisés du trade enregistré.
- Directive: Regrouper les deux écritures dans une transaction unique (un seul commit final, rollback commun).
- Acceptation: test avec échec mocké de `update_daily_stats` après `save_trade` → rollback complet (ou les deux persistés ensemble).
