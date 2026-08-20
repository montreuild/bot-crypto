# 07 — Live et exécution

Delta : `app/live/protocols.py` (nouveau, 101 lignes),
`app/live/position_open_mixin.py` (+37/−19),
`app/live/position_manage_mixin.py` (+33/−12),
`app/live/position_close_mixin.py` (+13/−9),
`app/live/position_restore_mixin.py` (+10/−7).

Le delta durcit deux points sensibles — la détection d'ordre non exécuté et le
remplacement d'un stop exchange — et bascule les valeurs par défaut du mode
papier dans le sens sûr. Un seul défaut relevé, sur le message envoyé à
l'opérateur.

---

## LIVE-01 — L'alerte de stop non remplacé annonce l'inverse du risque réel (P2, CONFIRMÉ)

**Fichier** : `app/live/position_manage_mixin.py:395-406`.

### Le code

```python
except RuntimeError as e:
    logger.error(f"[StopExchange] Trailing {pos.get('symbol')} : annulation "
                 f"échouée ({e}) — l'ancien stop reste en place")
    if hasattr(self, "notif"):
        self.notif.send(
            f"⚠️ *Stop exchange non remplacé* `{pos.get('symbol')}` : {e}\n"
            f"L'ancien stop reste en place (niveau plus prudent).",
            async_=True,
        )
    return None
```

Ce chemin est atteint quand le trailing veut **resserrer** le stop et que
l'annulation de l'ordre précédent échoue.

Un trailing stop ne se déplace que dans un sens : vers le haut pour un long,
vers le bas pour un short. L'ancien stop est donc, par construction, **plus
éloigné du prix** que celui qu'on n'a pas pu poser. Il laisse plus de place à
la perte.

Le message dit à l'opérateur l'exact contraire : « niveau plus prudent ».

### Scénario d'échec

Position longue, entrée 100, stop initial 96. Le trailing calcule un nouveau
stop à 104 (profit verrouillé). `cancel_order` échoue — l'exchange est
injoignable. L'ancien stop à 96 reste actif. L'opérateur reçoit :

```
⚠️ Stop exchange non remplacé BTC/USDC : cancel_stop_failed:12345
L'ancien stop reste en place (niveau plus prudent).
```

Il lit « plus prudent » et n'intervient pas. La position est en réalité exposée
à 8 points de perte au lieu de 0, et le profit verrouillé ne l'est pas.

### Vérification

**CONFIRMÉ** par lecture : le chemin n'est atteint que depuis
`_update_exchange_stop`, appelé par le trailing, dont la propriété de
monotonie est vérifiée par `tests/test_partial_exits.py::test_le_trailing_structurel_ne_recule_jamais`.
Le sens du déplacement est donc établi.

### Correctif proposé

Inverser le message et le hisser au niveau d'alerte qu'il mérite :

```
⚠️ Stop exchange NON RESSERRÉ `{symbole}` : {e}
L'ancien stop ({ancien}) reste actif — plus éloigné que le stop visé ({vise}).
Exposition supérieure à celle attendue : vérifier manuellement.
```

**Effort** : 20 min.

### Délégation IA

> Dans `app/live/position_manage_mixin.py`, le message envoyé à l'opérateur
> quand le remplacement d'un stop exchange échoue affirme que « l'ancien stop
> reste en place (niveau plus prudent) ». C'est faux : ce chemin n'est atteint
> que lorsque le trailing veut RESSERRER le stop, donc l'ancien est plus
> éloigné du prix et l'exposition est plus grande que prévu.
> Corriger le message pour qu'il indique le vrai sens du risque, et y faire
> figurer l'ancien niveau et le niveau visé. Faire de même pour le
> `logger.error` juste au-dessus.
> Test : vérifier que le texte de notification contient le niveau visé et le
> niveau resté actif.

---

## LIVE-02 — Un ordre au statut inconnu est traité comme exécuté (P2, PLAUSIBLE)

**Fichier** : `app/live/position_open_mixin.py:77-90`.

```python
def _order_failed(order: dict | None) -> bool:
    if order is None:
        return True
    status = str(order.get("status") or "").lower()
    if status in _REJECTED_ORDER_STATUSES:
        return True
    filled = float(order.get("filled") or 0)
    if filled > 0:
        return False
    if status in ("closed", "filled"):
        return False
    if status in ("open", "new", "pending", "unfilled"):
        return True
    return False            # ← statut inconnu ET filled == 0
```

Le repli final renvoie `False` — « pas d'échec » — pour un ordre dont le statut
n'est reconnu par aucune liste **et** dont la quantité exécutée est nulle.

### Scénario d'échec

Un connecteur renvoie `{"id": "…", "status": "PARTIALLY_CANCELED", "filled": 0}`.
Le statut n'est dans aucune des trois listes, `filled` vaut 0 : `_order_failed`
renvoie `False`. L'appelant considère l'ordre exécuté et enregistre une
position au prix
`order.get("price") or order.get("average") or price`
(`position_manage_mixin.py:478`). Le bot suit une position qui n'existe pas
côté exchange.

### Vérification

**PLAUSIBLE** — établi par lecture du code. **Non reproduit** : je n'ai pas
d'exchange renvoyant un tel statut sous la main, et je n'ai pas construit de
faux connecteur pour le provoquer.

**Ce n'est pas une régression du delta** : la version précédente
(`status in _REJECTED_ORDER_STATUSES`) avait déjà ce comportement pour un
statut inconnu. Le delta a en revanche **corrigé** le cas voisin — un ordre
`open`/`new` non rempli est désormais bien traité comme un échec, alors qu'il
passait pour un succès avant.

### Correctif proposé

Inverser le repli : ce qui n'est pas reconnu comme rempli est un échec.

```python
return True   # statut inconnu et rien de rempli → prudence
```

C'est le sens sûr : refuser d'ouvrir une position dont on n'a pas la preuve
qu'elle existe. À accompagner d'un log listant le statut inconnu, pour enrichir
les listes au fil des connecteurs rencontrés.

**Effort** : 30 min + revue des connecteurs utilisés.

### Délégation IA

> Dans `app/live/position_open_mixin.py::_order_failed`, le repli final renvoie
> `False` (« pas d'échec ») pour un ordre dont le statut n'est reconnu par
> aucune liste et dont `filled` vaut 0 : le bot enregistre alors une position
> possiblement inexistante. Inverser ce repli en `True` et journaliser le
> statut inconnu en `warning`, pour pouvoir compléter les listes.
> Vérifier que `pytest -q` reste vert et, si un test échoue, l'examiner :
> il documente peut-être un statut légitime à ajouter à
> `("closed", "filled")` plutôt qu'une régression.

---

## LIVE-03 — L'annulation de stop échouée ne perd plus l'identifiant (CONFIRMÉ — correction réelle)

**Fichier** : `app/live/position_manage_mixin.py:361-386`.

```python
oid = pos.pop("stop_order_id", None)
…
except Exception as e:
    pos["stop_order_id"] = oid          # restauration
    logger.error(…)
    raise RuntimeError(f"cancel_stop_failed:{oid}") from e
```

Auparavant, l'identifiant était retiré du dict en tête de fonction et
l'exception simplement journalisée en `warning`. En cas d'échec, le bot perdait
la trace d'un ordre stop **toujours actif côté exchange** : il croyait n'avoir
aucun stop, et pouvait en poser un second. Deux stops pour une position.

La restauration de l'identifiant et la remontée en `RuntimeError` corrigent les
deux problèmes. Correction de fond.

---

## LIVE-04 — Le mode papier devient la valeur par défaut sûre (CONFIRMÉ — amélioration)

**Fichier** : `app/live/position_manage_mixin.py:263`, `:288`, `:479`, `:497`,
`:623`, `:632`.

Six occurrences passent de `cfg["trading"].get("paper_mode")` à
`cfg["trading"].get("paper_mode", True)`.

Une configuration où la clé manque basculait donc en **mode réel**. Elle
bascule désormais en mode papier. Le repli va dans le sens qui ne peut pas
coûter d'argent, y compris pour `_exchange_stops_enabled`, qui cesse de poser
des ordres sur l'exchange quand la clé est absente.

---

## LIVE-05 — Les protocoles de typage sont introduits (CONFIRMÉ — amélioration)

**Fichier** : `app/live/protocols.py` (nouveau, 101 lignes).

`LiveHost` et `LifecycleHost` déclarent le contrat que les mixins attendent de
leur hôte. `PositionLifecycleMixin` et `PositionManageMixin` en héritent, ce
qui rend vérifiables des attributs jusque-là seulement supposés
(`self.cfg`, `self.exchange`, `self.notif`…).

Le module est l'un des six inscrits au périmètre `check_untyped_defs` de
`mypy.ini`, et il figure dans la commande CI. C'est la bonne façon de rendre
un mixin analysable.

---

## Ce qui a été vérifié sans rien trouver

- **`app/live/live_trader.py`** — 3ᵉ point de passage du graphe
  (betweenness 0,0076). Le delta n'y touche qu'une ligne
  (`e9467e4`, protection contre l'absence de `_best_auc_per_tf`).
- **`balance_sync.py`, `watchdog.py`, `market_hours_mixin.py`** — retouches de
  typage uniquement (`float = None` → `float | None`). Aucun effet à
  l'exécution.
- **Verrou de capital** — les mutations de `_paper_base` et `capital_display`
  restent toutes sous `with self._capital_lock`, y compris dans les chemins
  modifiés (`:494-500`, `:629-635`).
- **`app/live` hors périmètre mypy** — seul `protocols.py` est vérifié en CI.
  Les 5 mixins de position, qui manipulent le capital, ne le sont pas. Voir
  `TEST-02` dans `15-TESTS-CI.md` : c'est le lot que je recommande de traiter
  en premier.
