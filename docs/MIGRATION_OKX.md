# Migration Binance → OKX (MiCA)

> Contexte : Binance n'obtient pas l'agrément MiCA et restreint/ferme l'accès retail
> en Europe. Le bot bascule sur **OKX** (entité européenne agréée). Le moteur reste
> **multi-exchange via CCXT** : changer d'exchange = changer la config, pas le code.

## TL;DR

```yaml
# config.yaml
exchange:
  name: okx
  api_key: ${OKX_API_KEY}
  api_secret: ${OKX_API_SECRET}
  api_password: ${OKX_API_PASSWORD}   # ⚠ passphrase OKX — 3e credential OBLIGATOIRE
  margin: true                         # margin isolé, quote USDC (comportement conservé)
```

```bash
# .env (jamais versionné, chmod 600)
OKX_API_KEY=...
OKX_API_SECRET=...
OKX_API_PASSWORD=...        # la passphrase définie à la création de la clé API OKX
```

Modèle de marché retenu : **margin isolé**, quote **USDC** — identique au setup Binance
précédent. ~80 % du bot (stratégies, backtest, scanner, OHLCV, tickers, ordres) tourne
sans modification grâce à l'abstraction CCXT.

## Ce qui a changé dans le code

| Zone | Binance | OKX | Fichier |
|---|---|---|---|
| **Credentials** | clé + secret | clé + secret + **passphrase** (`password`) | `exchange.py`, `config.py`, `config.yaml`, `deploy/` |
| **Params margin (ordre)** | `sideEffectType=AUTO_BORROW_REPAY` + `isIsolated` | `tdMode=isolated\|cross` | `exchange.py` (`_margin_params`) |
| **clientOrderId** | `newClientOrderId` / `origClientOrderId` | `clOrdId` | `exchange.py` (`_client_id_field`) |
| **Format de l'id** | `bot-<hex>` (tiret OK) | alphanumérique pur (pas de tiret), ≤32 | `_gen_client_order_id` → `bot<24hex>` (valide partout) |
| **Niveau de marge** | `fetch_{isolated,cross}_margin_account()` → `marginLevel` | `fetch_balance().info.data[0].mgnRatio` | `exchange.py` (`fetch_margin_account`) |
| **Emprunt (dette)** | `userAssets[].borrowed` | `details[].liab` | `exchange.py` (`fetch_balance_detail`) |
| **Solde margin** | `fetch_balance({type: margin})` | `fetch_balance()` (compte unifié) | `exchange.py` |
| **Funding / OI** | symbole `BTC/USDT` | symbole swap `BTC/USDT:USDT` | `derivatives.py` (`_ccxt_swap_symbol`) |
| **Frais** | maker 0.04 % | maker **0.08 %**, taker 0.10 % | `config.yaml` |
| **Emprunt margin** | facturation ~3×/jour | **horaire** (24×/jour) | `config.yaml` (`borrow_periods_per_day: 24`) |

L'abstraction est pilotée par l'`id` ccxt de l'exchange (`RobustExchange._name`). Le code
**Binance reste fonctionnel** : repasser `name: binance` rétablit l'ancien comportement.

## ⚠️ Points de vigilance AVANT le live

1. **Passphrase obligatoire.** Sans `api_password`, tous les appels authentifiés
   échouent. Un warning est émis au démarrage si elle manque sur OKX.
2. **Mode de compte OKX.** Pour le margin, le compte doit être en mode
   *Spot and futures* ou *Multi-currency margin* (pas *Spot* simple). Clé API V5,
   permission **Trade** uniquement (jamais **Withdraw**), restreinte par IP.
3. **Sémantique du margin level (re-tunée).** Binance `marginLevel` (actif/passif,
   liquidation ≈ 1.05) ≠ OKX. Sur OKX, le code calcule lui-même
   `marginLevel = adjEq / mmr` (équité ajustée USD / maintenance margin requirement
   USD) lu dans `fetch_balance().info.data[0]` — un **ratio décimal sans ambiguïté
   d'échelle** : liquidation forcée à ≈ **1.0**, alerte native OKX à **3.0** (300 %),
   plus c'est haut plus c'est sûr. On évite ainsi de parser le champ brut `mgnRatio`
   (fraction vs pourcentage selon le mode de compte). Les seuils ont été recalibrés :

   | Seuil | Binance (avant) | **OKX (après)** | Effet |
   |---|---|---|---|
   | `margin_level_alert` | 1.5 | **3.0** | notification (aligné sur l'alerte 300 % d'OKX) |
   | `margin_level_critical` | 1.2 | **1.5** | HALT des nouvelles entrées (≈ 50 % de marge au-dessus de la liquidation) |
   | Liquidation exchange | ≈ 1.05 | ≈ 1.0 | gérée par OKX (backstop ultime) |

   Ce sont des **points de départ prudents**. Observez le ratio réel de votre compte
   en paper / début de live et ajustez : un compte sain affiche typiquement un ratio
   bien > 5. Sans positions margin (`mmr = 0`), le ratio vaut 999 (aucun risque).
4. **MiCA & accès margin retail.** L'entité OKX Europe peut restreindre le margin /
   les dérivés au retail. Si l'accès margin est refusé : repasser en spot pur
   (`margin: false`, `margin_mode: null`, `max_leverage: 1`).
5. **Stops exchange (OKX = ordres algo).** Sur OKX les stops sont des *algo orders*
   (trigger) ; ils n'apparaissent pas dans `fetch_open_orders` standard. La pose et
   l'annulation passent par `create_order(stopPrice=...)` (géré par ccxt), mais
   l'adoption d'un stop existant après crash (`_adopt_or_place_exchange_stop`) peut
   nécessiter un `params={'ordType':'trigger'}` selon la version ccxt — à valider sur
   un premier trade réel. Dégradation gracieuse : le stop logiciel reste actif.
6. **Frais & taux d'emprunt.** Vérifiez votre palier VIP réel et les taux d'emprunt
   par devise sur okx.com ; les valeurs de `config.yaml` sont des estimations
   conservatrices.

## Données de dérivés (signaux)

`DerivativesStore` agrège des signaux directionnels (funding, OI, long/short, taker) —
désormais **100 % OKX**, sans aucune dépendance à Binance :

- **funding & open interest** : via l'instance OKX (ccxt), symbole swap `BTC/USDT:USDT`.
- **long/short ratio & taker ratio** : via les endpoints **publics** OKX `rubik/stat`
  (`/api/v5/rubik/stat/contracts/long-short-account-ratio` et `.../taker-volume`,
  param `ccy`, périodes `1H`/`1D`). Le ratio taker buy/sell est calculé depuis
  `taker-volume` (`buyVol / sellVol`). Plus robuste en UE/MiCA, où `fapi.binance.com`
  peut être géo-bloqué.
- Dégradation gracieuse inchangée : en cas d'échec réseau, les colonnes sont absentes
  et les stratégies retombent sur leur logique OHLCV pure.

## Procédure de bascule recommandée

1. Garder `paper_mode: true`, lancer 1–2 semaines sur OKX, vérifier l'écart paper vs
   backtest (< 5 %) et que l'OHLCV/tickers OKX se chargent bien (quote USDC).
2. Créer la clé API OKX (Trade + passphrase + IP), renseigner le `.env`.
3. Re-tuner les seuils `margin_level_*` sur la sémantique mgnRatio OKX (ou passer en
   spot pur si le margin retail est bloqué sous MiCA).
4. Passage live avec capital réduit (≈ 10 %) et `risk_per_trade: 0.005` pendant
   2 semaines, en surveillant : positions BDD vs OKX, stops, PnL vs relevé OKX.
5. (Optionnel) Re-lancer l'optimiseur : les frais OKX (maker 0.08 %) changent
   légèrement l'économie des stratégies tunées sous les frais Binance.
