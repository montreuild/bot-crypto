# Audit — Trading live

> Périmètre : `app/live/` (live_trader, position_open/manage/close/restore_mixin,
> balance_sync, slot_lifecycle, auto_opt_mixin, health_mixin, market_hours_mixin,
> ohlcv_cache, signal_pipeline, watchdog), `app/core/exchange.py`,
> `app/core/risk_gate.py`.

---

## Tableau de bord

| # | Sévérité | Titre | Fichier | État au 18/08 |
|---|----------|-------|---------|---------------|
| L-01 | 🔴 Critique | Le stop n'est évalué qu'une fois par cycle, sur le dernier prix | `position_manage_mixin.py:217` | ✅ résolu — high/low de la bougie en formation |
| L-02 | 🔴 Critique | En paper (mode par défaut), aucun stop exchange et aucun stop intrabar | `position_manage_mixin.py:241-243` | ✅ résolu — fill au niveau du stop |
| L-03 | 🟠 Majeur | `fetch_positions()` en spot/margin peut supprimer toutes les positions à la reprise | `position_restore_mixin.py:52-85` | ✅ résolu — fetch seulement en perp |
| L-04 | 🟠 Majeur | La reprise ne rapproche que le SYMBOLE, jamais la taille ni le sens | `position_restore_mixin.py:74-80` | ✅ résolu — désaccord → orphelin |
| L-05 | 🟠 Majeur | `RiskLedger.reserve` sur une clé existante fuit du budget | `risk_ledger.py:98-107` | ✅ résolu — `deja_reserve` |
| L-06 | 🟠 Majeur | Plafond caché à 25 % du capital, sans motif de rejet (cf. F-12) | `balance_sync.py:196` | ✅ résolu |
| L-07 | 🟠 Majeur | Le jeton anti-spam est consommé avant les 7 autres contrôles (cf. F-11) | `risk_gate.py:297` | ✅ résolu — après fill |
| L-08 | 🟡 Moyen | Fenêtre non atomique entre l'ordre exchange et la persistance | `position_open_mixin.py:292-391` | ✅ résolu — persist pending avant l'ordre |
| L-09 | 🟡 Moyen | `notional` non recalculé après le slippage paper | `position_open_mixin.py:313-330` | ✅ résolu — `notional = size * exec_price` |
| L-10 | 🟡 Moyen | Le log d'ouverture affiche un « Sizing = X % » qui n'agit sur rien | `position_open_mixin.py:414-422` | ✅ résolu — `ScoreFactor` |
| L-11 | 🟡 Moyen | `update_daily_stats` reçoit les frais de sortie seuls | `position_close_mixin.py:328` | ✅ résolu — `fees_total` |
| L-12 | 🟡 Moyen | Le frein de volatilité est nommé « ATR BTC » mais alimenté autrement | `risk_gate.py:263` | ✅ résolu — log « ATR » générique |
| L-13 | 🟡 Moyen | Positions mutées hors verrou pendant que l'API les sérialise | `position_manage_mixin.py:53` | ✅ résolu — snapshot sous `_positions_lock` |
| L-14 | 🔵 Mineur | `_pre_execution_check` échoue sans trace dans les compteurs | `position_open_mixin.py:559-561` | ✅ déjà en place — `_record_precheck_reject` |
| L-15 | 🔵 Mineur | Seuil de gap à 2 % en dur | `position_manage_mixin.py:77,84` | ✅ résolu — `trading.gap_threshold` |
| L-16 | 🔵 Mineur | `bars_held` live en horloge murale, backtest en index de bougie | `position_manage_mixin.py:153` | ouvert |

> N-04 (`apply_exit_mode` live) est résolu. Détail : [`14-REVISION-2026-08-18.md`](14-REVISION-2026-08-18.md).

---

## L-01 🔴 Le stop n'existe qu'une fois par minute

### Constat

Le cycle live tourne toutes les `scan_interval` secondes (`config/risk.yaml` :
**60 s**). Dans `_manage_position`, la décision de sortie repose sur une seule
observation :

```python
price = ticker.get("last", pos["entry"])          # ligne 61
...
if trailing.is_triggered(price, new_stop, pos["side"]):   # ligne 217
    self._close_position(pos_id, price, ...)
```

Il n'y a **aucune lecture du plus-bas / plus-haut** de l'intervalle. Une mèche
qui traverse le stop entre deux cycles n'est jamais vue ; la position est fermée
au cycle suivant, au prix qui se présente.

Le backtest fait l'inverse : `stop_hit = c_low <= stop` (`backtest.py:879`),
c'est-à-dire une détection **intrabar exhaustive**.

### Ampleur

En 1h, une bougie contient 60 cycles ; le backtest voit le plus-bas de la
bougie, le live voit 60 échantillons ponctuels. Sur BTC, un écart de 0,3–0,8 %
entre le plus-bas d'une minute et sa clôture est banal.

Le sens du biais est constant : **le backtest sort toujours au stop, le live sort
toujours au-delà**. Cet écart est exactement ce que `test_backtest_live_parity`
ne peut pas voir, puisqu'il compare des formules, pas des cadences
d'échantillonnage.

### Atténuation existante — et sa limite

En **live réel**, les stops exchange (`exchange_stop_orders: true`) posent un
`STOP_LOSS_LIMIT` (ou un OCO OKX) au niveau du stop logiciel, donc l'exécution
réelle est correcte. Le stop logiciel devient un filet de rattrapage.

Mais ce chemin est désactivé en paper (cf. L-02), et il pose un
`stopPrice + prix limite` à `stop × (1 − 0,005)`
(`position_manage_mixin.py:258-262`) : sur une chute rapide de plus de 0,5 %, le
limit n'est pas rempli et la position reste ouverte, sans que rien ne le
détecte avant le cycle suivant.

### Correction proposée

1. Ne pas décider sur `ticker["last"]` mais sur le plus-bas/plus-haut de la
   dernière bougie en formation, disponible dans `OHLCVCache` :
   ```python
   lo, hi = self.ohlcv_cache.get_forming_range(symbol, pos_tf)
   probe  = lo if pos["side"] == "long" else hi
   if trailing.is_triggered(probe, new_stop, pos["side"]): ...
   ```
   (`app/live/ohlcv_cache.py` gère déjà la bougie en formation —
   cf. `tests/test_ohlcv_forming.py`.)
2. Documenter et **mesurer** l'écart : journaliser
   `slippage_vs_stop = |exec_price − stop|` à chaque sortie sur stop, et
   l'exposer dans `by_exit_reason`. Sans cette mesure, l'écart backtest/live
   reste invisible.

---

## L-02 🔴 Le mode paper n'a aucune protection de stop

`_exchange_stops_enabled` (`position_manage_mixin.py:241-243`) :

```python
return (not self.cfg["trading"].get("paper_mode")
        and bool(self.cfg["trading"].get("exchange_stop_orders", True)))
```

`paper_mode: true` est le **défaut** (`config/risk.yaml`). En paper, donc :

- aucun ordre stop côté exchange (normal : il n'y a pas d'ordre du tout) ;
- mais aussi **aucune simulation** de ce que ferait un stop exchange.

Le PnL paper est donc calculé avec la sortie tardive de L-01, alors que le
live réel aurait été rempli au niveau du stop. **Le paper trading est plus
pessimiste que le live réel sur ce point précis, et le backtest est plus
optimiste que les deux.** Trois modèles d'exécution différents pour la même
stratégie, dont aucun ne peut servir de référence aux deux autres.

C'est particulièrement gênant parce que le paper est le mode qui alimente
`DailyStats.equity_close`, donc `_restore_paper_base`, donc l'équité du
`RiskGate`, donc les circuit breakers.

**Correction** : en paper, simuler l'exécution du stop exchange — détecter le
franchissement sur la bougie en formation et remplir au niveau du stop (plus le
slippage paper). Cela aligne paper et live réel, et laisse l'écart avec le
backtest se limiter au slippage.

---

## L-03 🟠 La reprise peut effacer toutes les positions

`_restore_open_positions` (`position_restore_mixin.py:52-85`) :

```python
ex_positions = self.exchange.fetch_positions() or []
exchange_symbols_with_pos = set()
for ep in ex_positions:
    contracts = float(ep.get("contracts") or ep.get("size") or 0)
    if contracts > 0:
        exchange_symbols_with_pos.add(ep.get("symbol", ""))
...
if (exchange_symbols_with_pos is not None and symbol not in exchange_symbols_with_pos):
    delete_open_position(_sess, pos_id)      # ← suppression définitive
```

`fetch_positions()` est l'API **dérivés** de ccxt. Sur OKX, elle renvoie les
positions de swap/futures. La venue par défaut du dépôt est
`margin-isolated` (`market_type: margin`, `config/venues.yaml`) : les positions
margin spot y sont représentées comme des **soldes empruntés**, pas comme des
`positions` au sens ccxt.

Si `fetch_positions()` renvoie une liste vide (et non une exception), le code
conclut « aucune position sur l'exchange » et **supprime de la base toutes les
positions ouvertes**, définitivement, au démarrage. Le bot repart avec un
portefeuille vide alors que le capital est engagé sur l'exchange, et plus rien
ne surveille les stops.

La branche `except` couvre bien le cas d'erreur (toutes les positions sont
alors restaurées, ligne 63-67) — mais pas le cas « la méthode répond
correctement quelque chose qui ne veut rien dire pour cette venue ».

**Correction** :

```python
# N'appliquer la détection de fantômes que si l'on sait la faire pour cette venue.
if venue.market_type == "perp":
    ...fetch_positions()
elif venue.market_type in ("spot", "margin"):
    ...fetch_balance()  # solde de l'actif de base > 0 ?
else:
    exchange_symbols_with_pos = None   # on ne sait pas → on restaure
```

Et, dans tous les cas, **ne pas supprimer** : marquer la position
`status="orphaned"` et notifier. Une suppression est irréversible ; un
rapprochement raté est une hypothèse, pas un fait.

---

## L-04 🟠 La reprise ne rapproche que le symbole

Même bloc : la détection de fantôme se fait sur `symbol ∈
exchange_symbols_with_pos`. Ni la **taille**, ni le **sens**, ni le nombre de
positions ne sont comparés.

Deux slots sur `BTC/USDC` (par exemple `trend::1h` et `breakout::4h`) : si
l'exchange porte une seule position BTC, les **deux** sont restaurées. Le bot
gère alors 2× la taille réelle : les deux ordres de clôture partiront, le second
échouera (ou pire, ouvrira une position inverse).

`_verify_restored_position` existe et vérifie entry/taille — mais uniquement si
`pos.get("order_id")` est renseigné et hors paper (ligne 102-104). Un
`order_id` vide (ordre passé avant la migration, ou venue `can_execute: false`)
saute la vérification.

**Correction** : rapprocher la **somme des tailles** par symbole et par sens
entre la base et l'exchange, et refuser de démarrer (plutôt que de deviner) si
l'écart dépasse une tolérance. Un désaccord de position est un incident, pas un
détail de reprise.

---

## L-05 🟠 Une réservation en double fuit du budget

`RiskLedger.reserve` (`risk_ledger.py:98-107`) :

```python
self._positions[pos_key] = _Reservation(...)          # écrase sans regarder
self._venue_notional[env.venue] += notional
self._symbol_notional[sym_key]  += notional
...
```

Si `pos_key` existe déjà, l'ancienne `_Reservation` est **remplacée**, alors que
ses montants ont déjà été ajoutés aux six agrégats. `release(pos_key)` ne
soustraira ensuite qu'une fois : la différence reste engagée pour toujours,
jusqu'au redémarrage.

Le chemin est atteignable : dans `_try_open_from_signal`, `reserve` (étape 6,
ligne 553) précède la vérification atomique `pos_key in self.open_positions`
(étape 8, ligne 564). Deux appels concurrents pour le même `pos_key` — deux
signaux du même slot sur le même symbole dans le même cycle, ou un scan direct
(`_scan_symbol_strategy`) concurrent de la boucle — réservent tous les deux.

Le second est ensuite refusé à l'étape 8 et appelle `release` — qui libère la
réservation, mais une seule fois sur deux ajouts.

**Correction** :

```python
if pos_key in self._positions:
    return Decision(False, "reservation_existante", f"{pos_key} déjà réservé")
```

Deux lignes, et l'invariant redevient vrai. Un test de concurrence
(`tests/test_risk_thread_safety.py` existe déjà) devrait couvrir le cas.

---

## L-06/L-07 — voir [`01-FINANCIER.md`](01-FINANCIER.md)

- **L-06** = F-12 : plafond `notional > capital_display × 0.25` en dur, qui
  contredit le modèle d'enveloppes et n'existe qu'en live réel.
- **L-07** = F-11 : `can_trade` consomme un jeton anti-spam avant les sept
  autres contrôles.

Les deux ont le même effet de bord : un refus **silencieux**, sans motif dans
`self.rejections` ni dans `signal_log`. Le dépôt a pourtant investi dans un
vocabulaire de motifs partagé backtest/live (`app/core/rejections.py`) pour
pouvoir expliquer les divergences ; ces deux chemins y échappent.

---

## L-08 🟡 Fenêtre non atomique entre l'ordre et la persistance

`_open_position` (`position_open_mixin.py:292-391`) exécute dans l'ordre :

1. `self._execute_order(...)` — **l'ordre part sur l'exchange** ;
2. lecture du prix exécuté, éventuel `fetch_order` ;
3. slippage paper, calcul des frais, débit du capital ;
4. `self.open_positions[pos_key] = pos` (sous verrou) ;
5. `self.risk.register_open(pos)` ;
6. `persist_open_position(_sess, pos)` — **la base connaît la position**.

Entre 1 et 6, un crash laisse une position **réelle sur l'exchange, inconnue de
la base**. Au redémarrage, `_restore_open_positions` ne la voit pas (elle n'est
pas en base) et rien ne la reprend : position non surveillée, sans stop logiciel,
sans stop exchange (posé seulement à l'étape 7, ligne 397).

L'appelant gère bien l'exception (`position_open_mixin.py:573-577` : retrait de
`open_positions` et `ledger.release`), mais un crash process ne lève pas
d'exception.

**Correction** : écrire une **intention** en base avant l'ordre
(`status="pending"`, `client_order_id` généré localement), puis la confirmer.
Au démarrage, rapprocher les intentions `pending` avec `fetch_open_orders` /
`fetch_my_trades` sur le `client_order_id`. C'est le seul motif qui rend la
reprise déterministe.

Note : `test_order_idempotency.py` existe (93 lignes) — l'idempotence est
partiellement traitée côté ordre, pas côté persistance.

---

## L-09 🟡 `notional` non recalculé après le slippage paper

`position_open_mixin.py:313-330` :

```python
if self.cfg["trading"].get("paper_mode"):
    slip = self._paper_slippage_fraction(symbol, tf, notional)
    exec_price *= (1 + slip) if signal["side"] == "long" else (1 - slip)
else:
    ...
    size = filled; notional = size * exec_price        # ← recalculé ici seulement
```

En paper, `notional` reste `size × price_avant_slippage` alors que
`pos["entry"] = exec_price` (après slippage). L'invariant
`notional == size × entry` est donc rompu dans le mode par défaut.

`notional` sert ensuite à :

- `close_pnl(notional=pos["notional"])` → coût d'emprunt (`position_close_mixin.py:235`) ;
- `pnl_pct = pnl / pos["notional"] * 100` (ligne 274) — la grandeur même que
  `oos_tracker._per_trade_returns_pct` utilise pour mesurer l'edge et donc
  allouer les enveloppes ;
- `_pre_execution_check` : `locked = sum(p["notional"] ...)`.

L'écart vaut `paper_slippage` (0,1 % par défaut), donc l'expectancy mesurée est
biaisée de ~0,1 % relatif. Petit, mais systématique et dans le sens favorable
pour les longs.

**Correction** : `notional = size * exec_price` après le slippage, dans les deux
branches.

---

## L-10 🟡 Le log annonce un dimensionnement qui n'existe plus

`position_open_mixin.py:414-422` :

```python
score_factor = round(0.5 + 0.5 * (signal.get("score", 0) - strat_threshold)
                     / max(1.0 - strat_threshold, 1e-9), 2)
logger.info(f"[OPEN] ... | Sizing={score_factor * 100:.0f}% | Size={size:.6f} ...")
```

Ce facteur de score n'entre dans **aucun** calcul : `compute_size`
(`risk_sizer.py:96-102`) n'utilise que `env.slot_risk_amount`, `size_factor`,
le frein de volatilité et la courbe de drawdown. La docstring de `compute_size`
dit d'ailleurs explicitement que « le facteur de score interne
(`0.5 + 0.5 × …`) est supprimé ».

La ligne de log a survécu à la suppression. Un opérateur qui lit
« Sizing=73 % » face à une taille qu'il trouve élevée cherchera au mauvais
endroit.

**Correction** : supprimer la ligne, ou afficher les vrais facteurs
(`volatility_brake_factor`, `_drawdown_multiplier()`, `size_factor`).

---

## L-11 🟡 Les statistiques journalières sous-déclarent les frais

`position_close_mixin.py:323-329` :

```python
save_trade(session, trade)                       # trade["fees"] = fees_total ✅
update_daily_stats(session, ..., pnl, pnl > 0, fees, self.capital_display)
                                                 #             ↑ fees de SORTIE seuls
```

`fees_total` (entrée + jambes partielles + sortie) est calculé ligne 286 et
utilisé pour le trade, mais `update_daily_stats` reçoit `fees`, la valeur locale
retournée par `close_pnl` — c'est-à-dire les seuls frais de sortie.

`DailyStats.fees` alimente la ventilation des frais de l'UI
(`useFeesBreakdown`, `cards/fees-breakdown.tsx`). Elle sous-déclare donc d'un
facteur ~2.

---

## L-12 🟡 Le frein de volatilité ne mesure pas ce que son nom dit

`RiskGate.update_volatility(btc_atr_pct)` (`risk_gate.py:263`) : le paramètre,
le log (« ATR BTC ») et le seuil (`volatility_threshold: 0.05`, commenté
« 5% ATR BTC ») désignent un indicateur **global de marché**.

En live, l'appelant est `self.ohlcv_cache.update_volatility_brake()`
(`live_trader.py:375`) — à vérifier, mais le commentaire du cycle dit bien
« ATR BTC/USDC 1h ».

En backtest (`realistic_risk`), l'appelant est
`self._risk_gate.update_volatility(float(atr_arr[i]) / _px)`
(`backtest.py:1546-1549`) : c'est l'ATR **du symbole backtesté**, pas celui de
BTC. Sur une action Euronext ou un altcoin, ce n'est pas la même grandeur et le
seuil de 5 % n'a pas la même signification.

**Correction** : soit renommer en `update_market_volatility(atr_pct, source)`
et documenter que la source diffère, soit rendre la référence explicite et la
partager (BTC dans les deux cas).

---

## L-13 🟡 Mutation de positions hors verrou

`_manage_position` lit `self.open_positions.get(pos_id)` **sans** `_positions_lock`
(ligne 53) puis mute le dict en place : `pos["stop"] = new_stop` (ligne 169),
`pos["_trailing"] = trailing` (158), `cibles.remove(cible)` (114),
`pos["reason"] = ...` (144).

En parallèle, les threads de l'API sérialisent ces mêmes dicts
(`_serialize_position`, `position_close_mixin.py:367`) via `/api/status`, sondé
toutes les 3 s par le frontend (`use-api.ts:17`).

En CPython, l'écriture d'une clé de dict est atomique au niveau du GIL, donc pas
de corruption mémoire — mais une lecture peut voir un `stop` déjà remonté avec
une `size` pas encore réduite après une sortie partielle. Les valeurs affichées
sont alors incohérentes entre elles.

**Correction** : `with self._positions_lock:` autour de la lecture initiale et
copie défensive dans `_serialize_position`.

---

## L-14 à L-16 (mineurs)

- **L-14** : `if not self._pre_execution_check(...): self.ledger.release(pos_key);
  return False` (`position_open_mixin.py:559-561`) — aucun
  `self.rejections.record(...)`, aucune entrée dans `signal_log`. C'est le seul
  des huit points de refus du chemin d'ouverture à ne rien tracer, alors que le
  bloc documente « un seul point de refus, un seul motif compté ».
- **L-15** : seuil de gap `stop × 0.98` / `stop × 1.02` en dur
  (`position_manage_mixin.py:77, 84`). Sur un actif à faible volatilité, 2 %
  est un gouffre ; sur un altcoin, c'est du bruit.
- **L-16** : `bars_held = (time.time() − open_time) / tf_secs` (ligne 153)
  contre `bars_held = i − position["bar"]` en backtest (`backtest.py:702`).
  Sur `euronext-paper` (`calendar: XPAR`, séance 09:00–17:30), une position
  gardée du vendredi soir au lundi matin compte 62 « barres » en live contre 0
  en backtest. Cela pilote `grace_bars` et `breakeven_r` du trailing : les deux
  chemins n'appliquent pas la même phase au même moment.

---

## Ce qui est solide

- **`_order_failed` / `_order_fail_reason`** (`position_open_mixin.py:67-88`) :
  un `None` ou un statut rejeté est traité comme un échec, avec remise en gestion
  de la position en cas d'échec de clôture (`position_close_mixin.py:186-206`) et
  notification synchrone. C'est exactement le bon réflexe : ne jamais faire
  disparaître une position dont on n'a pas la preuve qu'elle est fermée.
- **Réconciliation des coûts réels** (`_reconcile_close_costs`) : frais du fill
  via `fetch_my_trades`, intérêts via `fetch_borrow_interest`, avec repli sur
  l'estimation et alerte au-delà de 5 % d'écart. Le refus de convertir une devise
  de frais tierce (OKB) plutôt que d'inventer un taux est le bon arbitrage.
- **Watchdog dead-man en process séparé** (`app/live/watchdog.py`) avec
  heartbeat écrit atomiquement (`os.replace`) et kill-switch fichier : le
  raisonnement « un process ne peut pas se surveiller lui-même » est juste et
  la mise en œuvre est minimale et correcte.
- **Kill-switch d'équité persistant et sticky** (`risk_gate.py:189-215`) :
  non levable sans `force=True`, persisté en base. Bon garde-fou catastrophe.
- **Maintenance isolée de la boucle** (`live_trader.py:270-281`) : une erreur
  d'auto-optimisation ou de cycle de vie ne peut pas arrêter le trading. La
  reprise après coupure réseau (`_recover_after_gap`, > 300 s) est prévue.
- **OCO natif OKX** avec repli stop-limit simple, et adoption des stops
  existants à la reprise (`_adopt_or_place_exchange_stop`) : la protection
  exchange est pensée jusqu'au redémarrage.
- **`RiskLedger.update_risk` appelé quand le trailing remonte le stop**
  (`position_manage_mixin.py:173-174`) : le budget de risque libéré profite
  immédiatement aux autres slots. C'est une vraie finesse du modèle.
