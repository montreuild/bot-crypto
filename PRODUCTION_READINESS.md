# 🚦 Vérification Livetrading — Préparation à la Production

Revue fonctionnelle et technique complète du chemin de livetrading
(`cli.py` → `LiveTrader` → `SignalPipeline` → `PositionMixin` → `RobustExchange`),
réalisée en vue d'un passage en production. Date : 2026-06-10, mis à jour le
2026-06-11 (module d'exécution commun + parité backtest↔live).

**Verdict : le bot est sain en paper mode. Pour le live réel, les correctifs
critiques ci-dessous ont été appliqués ; il reste des prérequis de
configuration et une période de validation à respecter avant d'engager du
capital (voir checklist en fin de document).**

---

## ✅ Corrigé dans ce commit

| # | Problème | Correctif |
|---|----------|-----------|
| 1 | **Stop-loss purement logiciel** : si le bot crash ou perd le réseau, les positions restaient sans aucune protection côté exchange (risque de perte illimitée / liquidation en margin). | Stop **STOP_LOSS_LIMIT posé sur l'exchange** en miroir du stop logiciel à chaque ouverture (`_place_exchange_stop`), **remplacé** quand le trailing remonte le stop, **annulé** à la clôture. Si le stop exchange a déjà exécuté (bot down), la position est soldée localement **sans second ordre** (pas de double vente). À la restauration après crash, le stop existant est **adopté** (pas de doublon) ou reposé. Opt-out : `trading.exchange_stop_orders: false`. |
| 2 | **Prix d'exécution à la clôture** : `order.get("price") or exit_price` — les ordres market ne renvoient souvent pas de prix immédiat → PnL calculé sur le prix ticker pré-exécution. | `fetch_order()` de secours à la clôture (comme à l'ouverture) pour lire le prix moyen réellement exécuté. |
| 3 | **Partial fills ignorés à l'ouverture** : la position était trackée avec la taille demandée même si l'exchange n'en remplissait qu'une partie (stops/PnL faux). | La taille trackée est alignée sur `order["filled"]` si < 98 % de la taille demandée (live uniquement), avec warning. |
| 4 | **Margin level critique = simple notification asynchrone** : le bot continuait à ouvrir des positions jusqu'à la liquidation (OKX : ratio adjEq/mmr, liquidation ≈ 1.0). | Seuil `margin_level_critical` (défaut 1.5) → **HALT immédiat du trading** (`risk.halted`) + notification **synchrone**. `margin_level_alert` (3.0) reste une alerte simple. |
| 5 | **Entrées `_reserved` (réservation atomique de slot) itérées comme de vraies positions** dans `status`, `_sync_paper_balance`, `_send_status_report`, `stop()` → KeyError potentiel / lignes fantômes dans l'API. | Filtrage systématique de `_reserved` dans tous les chemins d'itération. |
| 6 | **Incohérences de configuration silencieuses** (margin + max_leverage=1, paper + margin). | Warnings explicites au chargement de la config (`app/core/config.py`). |
| 7 | (commit précédent) **Bug polars épinglé 1.0.0** : les z-scores dérivés (`funding_z`, `lsr_z`…) échouaient silencieusement en live (`min_samples` inexistant). | Détection de la signature `min_periods`/`min_samples` à l'import. |

Le mécanisme de **pyramidage** ajouté pour Snowball passe par les mêmes
garde-fous que les entrées (risk.can_trade → sizing RiskManager → budget slot →
pre_execution_check) et replace le stop exchange après chaque ajout.

**Mise à jour 2026-06-11** — les formules monétaires (frais, coût d'emprunt
composé, PnL net) sont désormais **partagées entre le backtest et le live**
via `app/core/execution.py`, avec un test de parité
(`tests/test_execution_parity.py` : même trade ⇒ même PnL net par les deux
chemins). Conséquence pratique : les résultats de backtest/paper sont
directement comparables au live sur le plan des coûts — un écart observé en
production viendra du marché (slippage réel, fills partiels, taux d'emprunt
variables), pas des formules.

---

## 🔴 Prérequis restants avant le live réel (configuration / décision)

1. **Choisir UN mode et rendre la config cohérente** — actuellement
   `paper_mode: true` + `exchange.margin: true` + `max_leverage: 1` :
   - *Spot pur (recommandé pour démarrer)* : `paper_mode: false`,
     `exchange.margin: false`, `margin_mode: null`, `max_leverage: 1`.
   - *Margin isolé* : `paper_mode: false`, `margin: true`,
     `margin_mode: isolated`, `max_leverage: 2–3` max, et surveiller
     `margin_level` quotidiennement.
2. **Frais réels** : `taker_fee: 0.001` / `maker_fee: 0.0008` correspondent au
   palier standard OKX (taker 0.10 % / maker 0.08 %). Vérifiez votre palier
   VIP réel ; en margin, le `borrow_rate_daily: 0.00072` est une estimation —
   OKX applique des taux d'emprunt **horaires** variables par devise
   (`borrow_periods_per_day: 24`). Le PnL margin sera approximatif (cf. backlog).
3. **Sécurité API** :
   - définir `web.api_key` (sinon l'API n'est accessible qu'en localhost, mais
     ne comptez pas dessus derrière un reverse proxy) — un warning explicite
     est désormais émis au démarrage si `web.host: 0.0.0.0` sans clé ;
     génération : `python -c "import secrets; print(secrets.token_urlsafe(32))"` ;
   - clés exchange **uniquement** via variables d'env (`${OKX_API_KEY}`,
     `${OKX_API_SECRET}`, `${OKX_API_PASSWORD}` — déjà le cas) ; clés OKX
     restreintes par IP, droit *Trade* seul (sans retrait) ;
   - HTTPS (`FORCE_HTTPS=1` + reverse proxy TLS) et CORS adapté au domaine
     via la variable d'env `ALLOWED_ORIGINS` (liste séparée par des virgules,
     ex. `ALLOWED_ORIGINS=https://bot.mondomaine.com` — défaut : localhost).
   - **`X-Forwarded-For`** n'est désormais honoré que si la connexion provient
     d'un proxy déclaré dans `TRUSTED_PROXIES` (IP séparées par des virgules,
     ex. `TRUSTED_PROXIES=127.0.0.1`). **Derrière un reverse proxy, définissez
     cette variable** avec l'IP du proxy ; sinon le header est ignoré
     (anti-spoofing : un client distant ne peut plus se faire passer pour
     localhost et contourner l'auth quand `web.api_key` est vide).
4. **Supervision** : activer Telegram (`notifications.telegram_enabled`),
   le service systemd avec `Restart=on-failure` (déjà dans `deploy/`), et le
   healthcheck `/health` dans un monitoring externe (UptimeRobot ou cron).

---

## 🟠 Backlog recommandé (non bloquant pour un démarrage spot prudent)

| Priorité | Sujet | Détail |
|---|---|---|
| ~~Haute~~ ✅ fait | **Idempotence des ordres** | `create_order` attache un `newClientOrderId` stable entre tentatives ; après un timeout réseau, l'ordre est recherché par `origClientOrderId` et réutilisé s'il existe (pas de doublon). Tests : `test_order_idempotency.py`. |
| ~~Haute~~ ✅ fait | **Réconciliation des frais/emprunts réels** | Après chaque clôture live, frais du fill (fetch_my_trades) et intérêts margin réels remplacent les estimations dans le PnL/BDD ; warning si écart > 5 %. Opt-out `trading.reconcile_real_costs`. Tests : `test_reconcile_costs.py`. |
| ~~Haute~~ ✅ fait | **Gate Deflated Sharpe au naissance** | `app/core/deflated_sharpe.py` (López de Prado 2014) câblé dans `beats_baseline()` (opt_scoring.py). Corrige le biais de sélection multiple : une stratégie optimisée avec 50 essais et Sharpe 0.3 est refusée à l'apply. Activable via `optimizer.deflated_sharpe_gate` (défaut `true`). Tests : `test_deflated_sharpe_gate.py` (12 tests). |
| ~~Haute~~ ✅ fait | **Overfitting gate ML** | `app/ml/overfitting_gate.validate_model_quality` câblé dans `policy.maybe_refresh`. Enrichit les diagnostics avec un `level` (block/warn/good/strong) exposé dans `ArtifactRef.to_dict().overfitting_gate` et affiché via `OverfittingGateBadge` dans `/models`. |
| ~~Haute~~ ✅ fait | **Backtest realistic_risk** | `app/engine/backtest_risk_gate.py` réplique les 6 circuit breakers du `RiskGate` live en backtest (pertes consécutives, DD journalier par slot, trades/jour, DD journalier global, DD depuis le pic, volatility brake). Lit les mêmes clés de config que le gate live (`trading.daily_drawdown_limit`, `trading.max_drawdown_global`, `risk.*`) — sans quoi backtest et live ne se comparent pas. Opt-in via `realistic_risk=True`. Tests : `test_backtest_risk_gate.py` (21 tests). |
| Moyenne | **Locks `CapitalAllocator`/`RiskManager`** | `register_open/close` modifient les budgets sans verrou ; risque de course faible (la boucle est mono-thread) mais réel avec les threads d'auto-optimisation. |
| Moyenne | **Circuit-breaker réseau global** | Les erreurs réseau consécutives déclenchent un reset de session TCP (exchange.py) mais jamais un halt : ajouter un halt temporaire après ~10 min d'échecs continus. |
| ~~Moyenne~~ ✅ fait | **Vérification entry/size à la restauration** | `_verify_restored_position` croise la position BDD avec `fetch_order(pos["order_id"])` : entry corrigé si écart > 0,1 %, taille si écart > 2 %. |
| Basse | **Slippage paper proportionnel à la taille** | Le slippage paper fixe (0,1 %) sous-estime les gros ordres sur paires illiquides. |
| Basse | **Timeout scoring pipeline (5 s)** | Avec beaucoup de symboles×TF×stratégies, des scores peuvent être silencieusement abandonnés ; rendre le timeout configurable. |

---

## ✅ Points vérifiés et jugés sains

- **Boucle principale** : reprise après coupure réseau (`_recover_after_gap`)
  avec clôture des stops franchis pendant le gap ; purge mémoire périodique.
- **Restauration après crash** : positions rechargées depuis la BDD, positions
  fantômes écartées (vérification `fetch_positions`), stops déjà franchis
  signalés, trailing réinitialisé depuis le stop sauvegardé.
- **Risk management** : circuit breakers global (DD journalier/global) et par
  slot (pertes consécutives, DD slot, win-rate floor, max trades/jour),
  volatility brake ATR, kill sur équité négative.
- **Ouverture atomique** : réservation de slot sous verrou + rollback si
  l'ordre échoue (la réservation est correctement supprimée par le `except`).
- **Sécurité API** : auth `X-API-Key` en `hmac.compare_digest`, fallback
  localhost-only sans clé, rate-limit 60/min, `/api/config` redacte les
  sections `exchange` et `notifications`, validation des noms de stratégies
  (anti-injection de module), whitelist d'exchanges.
- **Cohérence backtest/live des paramètres** : `resolve_strategy_params` est
  la source unique des params (base YAML + overlay optimizer) des deux côtés.

---

## 📋 Checklist Go/No-Go

- [ ] Config mise en mode cible (spot pur OU margin assumé) — voir §Prérequis 1
- [ ] `web.api_key` défini, HTTPS actif, CORS restreint au domaine
- [ ] Clés OKX : restriction IP + droit *Trade* seul (pas de retrait) + passphrase
- [ ] Notifications Telegram testées (`POST /api/config/notifications/test`)
- [ ] `exchange_stop_orders: true` vérifié sur un premier trade réel (le stop
      apparaît dans les ordres ouverts OKX — NB : OKX = ordres algo/trigger)
- [ ] 2 semaines de paper mode sur les symboles/TF cibles, écart paper vs
      backtest < 5 %
- [ ] Démarrage live avec capital réduit (ex. 10 % du capital cible) et
      `risk_per_trade: 0.005` pendant 2 semaines
- [ ] Vérification quotidienne : margin level, positions BDD vs exchange,
      stops orphelins, PnL vs relevé OKX
- [x] Backup automatique de `trades.db` et `config.yaml`/`strategies/`
      (SEC-05 : `deploy/backup.sh` + cron, cf. `DEPLOY.md` §9.1)

---

*Document généré lors de la revue de production. À mettre à jour à chaque
évolution du chemin d'exécution live.*
