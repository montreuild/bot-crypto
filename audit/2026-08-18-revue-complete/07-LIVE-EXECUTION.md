# 07 — Trading live et exécution

Périmètre : `app/live/*` (4 781 lignes), `app/core/exchange.py`,
`app/core/provider_router.py`, `app/core/bot_identity.py`.

**Jugement d'ensemble.** C'est la partie du dépôt où le soin apporté se voit le plus.
`create_order` est **idempotent** : un `clientOrderId` est généré avant l'envoi et, après
un timeout réseau, l'ordre est recherché par cet identifiant avant tout retry
(`exchange.py:225-234`). C'est la bonne réponse au problème le plus dangereux d'un bot de
trading, et elle est rarement implémentée correctement. Les venues sans exécution branchée
(`can_execute: false`) sont interceptées au plus près du trade et pas seulement dans le
routeur, avec une justification explicite du choix. L'échec de pose du stop exchange
notifie l'opérateur au lieu de passer inaperçu.

Deux défauts restent, tous deux dans la même zone : **la fenêtre entre deux ordres**.

---

## LIVE-01 — Le remplacement du stop exchange peut laisser deux stops vivants sur la même position

**Sévérité P1 · CONFIRMÉ (lecture)**

`_update_exchange_stop` (`position_manage_mixin.py:379-388`) procède en deux temps :

```python
filled = self._cancel_exchange_stop(pos)
if filled is not None:
    return filled
self._place_exchange_stop(pos)
```

Et `_cancel_exchange_stop` (`position_manage_mixin.py:359-377`) retire l'identifiant
**avant** tout appel réseau, puis avale l'échec :

```python
oid = pos.pop("stop_order_id", None)        # ← retiré AVANT l'appel API
if not oid:
    return None
try:
    o = self.exchange.fetch_order(oid, pos["symbol"]) or {}
    ...
    if status not in ("canceled", "cancelled", "expired", "rejected"):
        self.exchange.cancel_order(oid, pos["symbol"])
except Exception as e:
    logger.warning(f"[StopExchange] Annulation stop {pos['symbol']} KO : {e}")
return None                                  # ← indiscernable d'un succès
```

### Scénario d'échec

1. Le trailing remonte le stop → `_update_exchange_stop`.
2. `cancel_order` échoue définitivement (le décorateur `@with_retry` a déjà consommé ses
   4 tentatives sur ~30 s — c'est donc une panne réelle, pas un aléa).
3. L'exception est journalisée en `warning` et la fonction rend `None` — **la même valeur
   qu'un succès**.
4. `pos["stop_order_id"]` a déjà été supprimé à l'étape 0 : l'ordre reste **vivant sur
   l'exchange et n'est plus référencé nulle part**.
5. `_place_exchange_stop` pose un second stop et écrit le nouvel identifiant.

La position porte désormais **deux ordres de vente déclenchables** pour une seule
quantité détenue. À la clôture, `_close_position` annule celui qu'il connaît ; l'orphelin
survit. S'il se déclenche ensuite, il vend une position qui n'existe plus — c'est-à-dire
qu'il **ouvre un short non désiré** sur une venue margin, ou échoue avec un solde
insuffisant sur du spot.

### Ce qui prouve que le danger est connu

`_adopt_or_place_exchange_stop` (`position_manage_mixin.py:391-394`) existe précisément
pour ça, et le dit :

> *À la restauration : adopte un stop déjà ouvert sur l'exchange pour ce symbole (évite
> les stops dupliqués qui vendraient deux fois), sinon en pose un nouveau.*

La protection a été écrite pour le chemin de **restauration après redémarrage**, et non
pour le chemin de **remontée du trailing** — qui est pourtant emprunté à chaque bougie, sur
chaque position, donc des milliers de fois plus souvent.

### Correction

`_cancel_exchange_stop` doit distinguer trois issues au lieu de deux : annulé,
déjà exécuté, **échec d'annulation**. Sur échec :

```python
except Exception as e:
    pos["stop_order_id"] = oid        # on le garde : l'ordre est peut-être vivant
    logger.error(...)
    raise ExchangeStopCancelError(oid) from e
```

et `_update_exchange_stop` renonce à poser le nouveau stop (le stop logiciel prend le
relais, l'ancien stop exchange reste en place à un niveau plus prudent — ce qui est le
comportement sûr), avec notification à l'opérateur comme le fait déjà
`_place_exchange_stop`.

**Effort** : ~15 lignes + un test de simulation d'échec d'annulation. ~3 h.

---

## LIVE-02 — Un ordre accepté mais non rempli crée une position fantôme

**Sévérité P1 · PLAUSIBLE (lecture)**

`_order_failed` (`position_open_mixin.py:70-80`) ne regarde que le statut :

```python
_REJECTED_ORDER_STATUSES = frozenset({"rejected", "canceled", "cancelled", "expired"})

def _order_failed(order: dict | None) -> bool:
    if order is None:
        return True
    return str(order.get("status") or "").lower() in _REJECTED_ORDER_STATUSES
```

Sa docstring annonce pourtant l'objectif exact :

> *poursuivre comme si l'ordre avait réussi créerait une position fantôme (trackée par le
> bot, absente de l'exchange).*

Un ordre au statut `"open"` avec `filled: 0` — carnet vide, marché suspendu, ordre
market converti en limit non touché — **n'est pas détecté**. La suite du flux le traite
comme un succès :

```python
exec_price = order.get("price") or order.get("average") or price   # ← repli sur le ticker
...
filled = float(order.get("filled") or 0)
if 0 < filled < size * 0.98:                                       # ← 0 ne satisfait pas 0 < filled
    size = filled
```

Le garde de remplissage partiel est écrit `0 < filled`, donc **le cas `filled == 0` lui
échappe** : la taille n'est pas ajustée. Le bot enregistre alors une position de taille
pleine, au prix du ticker, la persiste en base, réserve le risque au `RiskLedger` et pose
un stop exchange — pour une exposition réelle **nulle**.

Le stop, lui, existe bel et bien : il vendra une quantité que le bot ne détient pas.

### Corrections

Deux, indépendantes :

1. `_order_failed` doit aussi refuser un ordre non rempli :
   ```python
   if str(order.get("status") or "").lower() in _REJECTED_ORDER_STATUSES:
       return True
   return float(order.get("filled") or 0) <= 0 and str(order.get("status","")).lower() != "closed"
   ```
   (la clause sur `"closed"` préserve le stub paper de `exchange.py:201-203`, qui rend
   `status="closed"` sans champ `filled`).
2. Le garde de remplissage partiel doit s'écrire `filled < size * 0.98` sans le
   `0 < filled`, une fois le cas nul traité en amont.

**Effort** : ~8 lignes + 2 tests. ~2 h.

---

## LIVE-03 — L'ajustement au remplissage réel ne s'applique jamais en paper

**Sévérité P2 · PLAUSIBLE (lecture)**

Le bloc de correction de taille est dans la branche `else` du test paper
(`position_open_mixin.py:~437`) : en paper, la taille reste celle demandée et seul le
slippage est appliqué. C'est cohérent — un fill paper est toujours complet — mais cela
signifie que **le chemin de correction de taille n'est jamais exercé en paper**, donc
jamais exercé par un utilisateur avant sa première session réelle. C'est le pire endroit
pour placer du code non éprouvé.

`partial_fill_pct: 0.95` existe côté backtest (`execution.py:425`) pour modéliser ce
phénomène ; le paper trading, lui, remplit toujours à 100 %. **Le paper est donc plus
optimiste que le backtest** sur ce point précis, alors qu'il est censé en être le
prolongement réaliste.

**Correction** : appliquer `partial_fill_pct` au fill paper, ce qui aligne les trois
chemins et fait passer le code de correction sous test.

---

## LIVE-04 — Le drapeau `paper_mode` a un défaut sûr à trois endroits et pas aux vingt autres

**Sévérité P3 · CONFIRMÉ (aucun chemin exploitable aujourd'hui)**

Trois sites lisent le drapeau avec un défaut **sûr** :

```
app/core/exchange.py:377      cfg["trading"].get("paper_mode", True)
app/live/health_mixin.py:243  cfg["trading"].get("paper_mode", True)
app/core/config.py:625        cfg["trading"].get("paper_mode", True)
```

Une vingtaine d'autres — **tous ceux qui décident d'envoyer un ordre réel** — le lisent
sans défaut :

```
position_open_mixin.py:317,330,358    position_close_mixin.py:210,225,246,254
position_manage_mixin.py:262,287,458,476,602,611
position_restore_mixin.py:55,89,121,161    balance_sync.py:31,150,182
```

`.get("paper_mode")` sur une clé absente rend `None`, donc falsy, donc **la branche
d'exécution réelle**. Le défaut est sûr là où il ne compte pas et ouvert là où il compte.

**Aujourd'hui, aucun chemin d'exploitation** : `DEFAULTS` pose `"paper_mode": True`
(`config.py:71`) et la boucle de `setdefault` (`config.py:539-543`) garantit la clé après
`load_config`. C'est pour ça que ce n'est pas un P1.

Reste que la sécurité repose entièrement sur une invariante externe non exprimée là où
la décision se prend. Un `cfg` construit à la main — dans un test, un script, un futur
worker — bascule vingt sites en mode réel d'un coup, silencieusement. Le coût de la
robustesse est de vingt caractères.

**Correction** : ajouter `, True` partout. Optionnellement, une propriété
`self.paper` sur `LiveTrader`, calculée une fois, pour n'avoir plus qu'un seul site.

---

## LIVE-05 — Le log d'ouverture affiche un `ScoreFactor` qui ne module plus rien

**Sévérité P3 · CONFIRMÉ (lecture croisée)**

`position_open_mixin.py:~425` calcule et journalise :

```python
score_factor = round(0.5 + 0.5 * (signal.get("score", 0) - strat_threshold)
                     / max(1.0 - strat_threshold, 1e-9), 2)
logger.info(f"[OPEN] ... | ScoreFactor={score_factor * 100:.0f}% | Size={size:.6f} ...")
```

Or `RiskSizer.compute_size` dit explicitement (`risk_sizer.py:79-81`) :

> *Le facteur de score interne (`0.5 + 0.5 × …`) est supprimé : il modulait la taille sans
> être répliqué par le backtest, donc cassait la parité.*

La formule affichée est **exactement** celle qui a été retirée du sizing. L'opérateur lit
donc, à côté de la taille réelle, un pourcentage qui n'y a plus contribué. Sur un log
d'ouverture de position, c'est un mensonge par juxtaposition — le genre d'affichage qui
fait perdre une heure le jour où on cherche pourquoi une taille ne correspond pas.

**Correction** : retirer le champ, ou le renommer `ScoreNorm` en indiquant qu'il est
informatif.

---

## LIVE-06 — Huit blocs `except: pass` sur des chemins de position

**Sévérité P2 · CONFIRMÉ (recensement)**

```
position_close_mixin.py:140, 377
position_open_mixin.py:311, 348, 466, 608
position_restore_mixin.py:104
health_mixin.py:190 (continue)
```

Certains sont légitimes et bien placés — `position_open_mixin.py:608` entoure une
publication WebSocket, explicitement « jamais critique ». D'autres le sont moins :
`position_open_mixin.py:311` avale l'échec de `delete_open_position` **sur le chemin de
rollback d'un ordre refusé**, c'est-à-dire au moment précis où une position fantôme est
en train d'être nettoyée. Si le nettoyage échoue, personne ne le saura, et la position
restera en base — exactement l'état que le rollback devait empêcher.

Sur les 125 `except Exception` de `app/`, 89 sont suivis d'un `pass` : le ratio global est
élevé mais la plupart sont dans des chemins de diagnostic. Le sous-ensemble à traiter est
celui listé ci-dessus.

**Correction** : sur les chemins de position, remplacer `pass` par un `logger.error` avec
le contexte, et pour le rollback, une notification opérateur.

---

## Ce qui a été vérifié et tenu

- **Idempotence des ordres** — `clientOrderId` généré avant l'envoi, ordre recherché par
  cet identifiant après un timeout réseau et **réutilisé** au lieu d'être redemandé
  (`exchange.py:197-234`). C'est le point le plus risqué d'un bot de trading, et il est
  traité correctement.
- **Reconnexion de session** — reset TCP après `RESET_AFTER_ERRORS` erreurs consécutives,
  compteur remis à zéro sur succès.
- **Retry différencié** — 4 essais / ~30 s sur les ordres et l'OHLCV, 2 essais / ~1,5 s sur
  les tickers (`exchange.py:27-28`). Le compromis latence/robustesse est explicite et
  cohérent avec la criticité de chaque appel.
- **Venue data-only** — le garde `can_execute` est posé **au plus près du trade**
  (`position_open_mixin.py:238`) et pas seulement dans `ProviderRouter`, avec une
  justification qui tient : un test ou un script peut injecter un exchange directement.
  La notification remplace alors le « position ouverte » habituel, qui laisserait croire à
  un fill réel (`position_open_mixin.py:~455`).
- **Échec de pose du stop exchange** — journalisé en `error` **et** notifié à l'opérateur,
  avec le message qui dit ce qui reste protégé (`position_manage_mixin.py:346-357`).
- **Détection du stop déjà exécuté** — `_cancel_exchange_stop` rend l'ordre quand il a
  été rempli pendant que le bot ne regardait pas, et les appelants clôturent localement
  sans envoyer un second ordre.
- **Réconciliation des coûts réels** — `_reconcile_close_costs` remplace les estimations
  par les frais et intérêts réellement facturés hors paper
  (`position_close_mixin.py:246-250`).
- **Verrouillage** — `_positions_lock` et `_capital_lock` séparés, et les threads de fond
  (auto-opt, forward-test, lifecycle) sont tous `daemon=True`.
- **Comptage en bougies, pas en horloge** — `bars_held_from_ohlcv` (`position_manage_mixin.py:45-57`)
  refuse de compter un week-end XPAR comme 62 barres 1 h. Détail juste, souvent manqué.

---

## Récapitulatif

| ID | Sévérité | Preuve | Constat | Effort |
|---|---|---|---|---|
| LIVE-01 | **P1** | CONFIRMÉ | Échec d'annulation ⇒ deux stops vivants | 3 h |
| LIVE-02 | **P1** | PLAUSIBLE | Ordre accepté non rempli ⇒ position fantôme | 2 h |
| LIVE-03 | P2 | PLAUSIBLE | Correction de taille jamais exercée en paper | 2 h |
| LIVE-06 | P2 | CONFIRMÉ | `except: pass` sur le rollback d'ouverture | 2 h |
| LIVE-04 | P3 | CONFIRMÉ | `paper_mode` sans défaut sûr sur 20 sites | 30 min |
| LIVE-05 | P3 | CONFIRMÉ | `ScoreFactor` affiché mais plus appliqué | 10 min |
