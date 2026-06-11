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
| 2 | **Prix d'exécution à la clôture** : `order.get("price") or exit_price` — les ordres market Binance ne renvoient souvent pas de prix immédiat → PnL calculé sur le prix ticker pré-exécution. | `fetch_order()` de secours à la clôture (comme à l'ouverture) pour lire le prix moyen réellement exécuté. |
| 3 | **Partial fills ignorés à l'ouverture** : la position était trackée avec la taille demandée même si l'exchange n'en remplissait qu'une partie (stops/PnL faux). | La taille trackée est alignée sur `order["filled"]` si < 98 % de la taille demandée (live uniquement), avec warning. |
| 4 | **Margin level critique = simple notification asynchrone** : le bot continuait à ouvrir des positions jusqu'à la liquidation (Binance liquide ≈ 1.05). | Nouveau seuil `margin_level_critical` (défaut 1.2) → **HALT immédiat du trading** (`risk.halted`) + notification **synchrone**. `margin_level_alert` (1.5) reste une alerte simple. |
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
2. **Frais réels** : `taker_fee: 0.001` est correct pour le spot Binance sans
   BNB. Vérifiez votre palier VIP/BNB réel ; en margin, le
   `borrow_rate_daily: 0.00072` est une estimation — Binance applique des taux
   horaires variables par paire. Le PnL margin sera approximatif (cf. backlog).
3. **Sécurité API** :
   - définir `web.api_key` (sinon l'API n'est accessible qu'en localhost, mais
     ne comptez pas dessus derrière un reverse proxy) ;
   - clés exchange **uniquement** via variables d'env (`${BINANCE_API_KEY}` —
     déjà le cas) ; clés Binance restreintes par IP, sans droit de retrait ;
   - HTTPS (`FORCE_HTTPS=1` + reverse proxy TLS) et CORS adapté au domaine.
4. **Supervision** : activer Telegram (`notifications.telegram_enabled`),
   le service systemd avec `Restart=on-failure` (déjà dans `deploy/`), et le
   healthcheck `/health` dans un monitoring externe (UptimeRobot ou cron).

---

## 🟠 Backlog recommandé (non bloquant pour un démarrage spot prudent)

| Priorité | Sujet | Détail |
|---|---|---|
| Haute | **Idempotence des ordres** | En cas de timeout réseau sur `create_order`, un retry peut dupliquer l'ordre. Utiliser un `newClientOrderId` déterministe et vérifier son existence avant retry. |
| Haute | **Réconciliation des frais/emprunts réels** | Après clôture, lire `fetch_my_trades` pour remplacer frais et borrow_cost estimés par les valeurs réelles (écart actuel ~0,01–0,1 %/trade en margin). |
| Moyenne | **Locks `CapitalAllocator`/`RiskManager`** | `register_open/close` modifient les budgets sans verrou ; risque de course faible (la boucle est mono-thread) mais réel avec les threads d'auto-optimisation. |
| Moyenne | **Circuit-breaker réseau global** | Les erreurs réseau consécutives déclenchent un reset de session TCP (exchange.py) mais jamais un halt : ajouter un halt temporaire après ~10 min d'échecs continus. |
| Moyenne | **Vérification entry/size à la restauration** | `_restore_open_positions` vérifie l'existence de la position côté exchange, pas la cohérence taille/prix : croiser avec `fetch_order(pos["order_id"])`. |
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
- [ ] Clés Binance : restriction IP + pas de droit de retrait
- [ ] Notifications Telegram testées (`POST /api/config/notifications/test`)
- [ ] `exchange_stop_orders: true` vérifié sur un premier trade réel (le stop
      apparaît dans les ordres ouverts Binance)
- [ ] 2 semaines de paper mode sur les symboles/TF cibles, écart paper vs
      backtest < 5 %
- [ ] Démarrage live avec capital réduit (ex. 10 % du capital cible) et
      `risk_per_trade: 0.005` pendant 2 semaines
- [ ] Vérification quotidienne : margin level, positions BDD vs exchange,
      stops orphelins, PnL vs relevé Binance
- [ ] Backup automatique de `trades.db` et `config.yaml`/`strategies/`

---

*Document généré lors de la revue de production. À mettre à jour à chaque
évolution du chemin d'exécution live.*
