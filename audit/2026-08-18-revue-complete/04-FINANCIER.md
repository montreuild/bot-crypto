# 04 — Moteur financier : coûts, PnL, frais, emprunt, risque

Périmètre : `app/core/execution.py`, `app/core/trade_economics.py`,
`app/engine/position_lifecycle.py`, `app/core/risk_ledger.py`, `app/core/risk_sizer.py`,
`app/core/risk_envelope.py`, `app/core/risk_curve.py`.

**Jugement d'ensemble.** L'architecture des coûts est bonne, et meilleure que la moyenne
de ce qu'on voit sur ce type de dépôt : une source unique de formules monétaires
(`execution.py`) partagée backtest ↔ live, un modèle de venue qui distingue spot / margin /
perp, un funding discret séparé de l'emprunt composé, un registre de risque atomique sous
verrou. Les défauts trouvés ne sont pas des erreurs de conception — ce sont des **fuites
d'agrégation** : de l'argent correctement calculé, puis mal reporté ou mal recompté à
l'étage au-dessus. Deux d'entre eux (FIN-01, FIN-02) faussent des chiffres que
l'utilisateur lit et sur lesquels l'optimiseur décide.

---

## FIN-01 — `total_pnl` ne vaut pas la variation d'équité dès qu'un trade a des jambes partielles

**Sévérité P1 · CONFIRMÉ (reproduit)**

`app/engine/position_lifecycle.py:54`

```python
entry_fees = float(position.get("fees", 0.0) or 0.0)
```

`_close_at` lit `position["fees"]` en croyant y trouver **les seuls frais d'entrée**.
C'était vrai avant les sorties partielles. Ça ne l'est plus : `_close_partial_at`
(`position_lifecycle.py:156`) fait grossir cette même clé à chaque jambe —

```python
position["fees"] = round(position.get("fees", 0.0) + fees, 8)   # fees = frais de SORTIE de la jambe
```

Ligne 64, le PnL journalisé retranche donc les frais de sortie des jambes **une seconde
fois** — ils ont déjà été déduits par `_close_pnl` à l'intérieur de `_realized_pnl`.

### Reproduction

Harnais : `tests/test_partial_exits.py` (série haussière déterministe, stratégie
`_AvecJambes` : TP1 25 % à 0,5R, TP2 25 % à 1R, runner 50 %) — mêmes `_cfg`, mêmes données.

```
===== SANS jambes partielles (témoin) =====
  total_pnl                : 259.896800
  net_profit (équité)      : 259.896800
  ÉCART                    : 0.000000        → invariant OK

===== AVEC jambes partielles =====
  jambes                   : 2
  total_pnl                : 136.146200
  net_profit (équité)      : 136.339000
  ÉCART                    : -0.192800
  frais de sortie des jambes : 0.192823      → l'écart EST cette somme
```

### Pourquoi c'est un P1 et pas un arrondi

1. **Le code affirme le contraire.** `backtest_result.py:390-392` :
   « *F-01 : alias explicite — depuis que le pnl de trade porte les frais d'entrée,
   total_pnl == net_profit* ». L'invariant revendiqué est faux, et rien ne le teste :
   `tests/test_partial_exits.py::test_l_equite_finale_reste_coherente` vérifie
   `net_profit == final_equity - initial_capital`, ce qui est vrai **par définition**
   (`backtest_result.py:236`) et ne peut donc rien détecter.

2. **La contamination est large.** Tout ce qui dérive de `t["pnl"]` est biaisé à la
   baisse pour les trades fractionnés, et **seulement pour eux** :
   `win_rate`, `profit_factor`, `expectancy`, `avg_win` / `avg_loss`, `by_strategy`,
   `by_setup`, `by_module`, `by_exit_reason`, les courbes d'équité et Sharpe par
   stratégie (`_group_metrics`, `backtest_result.py:311-343`). En face,
   `max_drawdown` se lit sur `equity_mtm`, qui est juste. Deux familles de métriques
   du même rapport ne décrivent plus le même trade.

3. **`by_exit_leg` absorbe toute l'erreur au même endroit.** Le poste « runner » est
   calculé **par différence** (`backtest_result.py:170-172`) : `pnl_du_trade − Σ pnl_des_jambes`.
   L'erreur étant entièrement dans `pnl_du_trade`, elle atterrit intégralement sur le
   runner. Or `by_exit_leg` existe précisément pour répondre à « le reliquat paie-t-il
   les jambes prises tôt ? » — la seule ligne du tableau qui porte le biais est celle
   qu'on interroge.

4. **Ça grandit avec ce qu'on veut mesurer.** L'écart vaut `Σ frais_sortie_jambes`,
   donc proportionnel au nombre de jambes et au taux de frais. Ici, jambes en `maker`
   (0,04 %) : 0,19 sur 136. En `taker` (0,1 %) avec 4 jambes, c'est 2,5× plus. Le mode
   `tp1_tp2_runner` est *par construction* celui qui déclenche le plus de jambes : le
   biais est maximal sur le mode qu'on cherche à évaluer.

### Correction

Le champ propre existe déjà. `_try_enter` pose `position["entry_fees"]`
(`position_lifecycle.py:654`) et **aucune jambe partielle n'y touche**. Une ligne :

```python
# position_lifecycle.py:54
entry_fees = float(position.get("entry_fees", position.get("fees", 0.0)) or 0.0)
```

Le repli sur `"fees"` couvre les dicts de position construits à la main par les tests.

**Effort** : 1 ligne + 1 test d'invariant (`total_pnl == net_profit`, avec et sans jambes)
— celui qui manque aujourd'hui. ~30 min.

---

## FIN-02 — Les sorties « early exit » et « time exit » ne paient ni spread ni frais taker

**Sévérité P1 · CONFIRMÉ (mesuré)**

`app/engine/position_lifecycle.py:262` et `:266`

```python
self._close_at(ctx, position, i, c_close, early_exit_reason, maker=True)
self._close_at(ctx, position, i, c_close, "exit_after_bars", maker=True)
```

Trois décisions cumulées, toutes favorables :
- prix d'exécution = **le close exact** de la barre, sans spread ;
- `ref_price=None` → `slip_exit = 0` (`position_lifecycle.py:51`) : le coût n'est même pas
  journalisé comme slippage ;
- `maker=True` → frais maker alors qu'une sortie décidée par le signal est un ordre au
  marché.

### Ce qui prouve que c'est un oubli et non un choix

Le même fichier applique la règle inverse pour la clôture de fin de série
(`backtest.py:576-577`) :

```python
_eod_exec = _eod * (1 - self.spread_pct) if _side == "long" else _eod * (1 + self.spread_pct)
self._close_at(ctx, _pos, len(df) - 1, _eod_exec, "end_of_data", maker=False, ...)
```

commenté « *B-10 : une liquidation forcée est taker, avec spread — pas un maker gratuit* ».
Le raisonnement vaut mot pour mot pour une sortie sur signal. Les stops et TP, eux,
passent bien par `_fill_at_level` qui applique le spread (`position_lifecycle.py:185-186`).
Seules ces deux voies de sortie y échappent.

### Mesure

```
exit_reason        : exit_after_bars
prix de sortie     : 420.858993
close de la barre  : 420.858993
écart              : 4.5e-07          → aucun spread appliqué
notional           : 950.00
slippage_cost      : 0.474762         → l'entrée seule ; la sortie n'a rien coûté
coût NON facturé   : spread 0.4750 + écart taker/maker 0.5700 = 1.0450
```

**0,11 % du notionnel par trade** (5 bps de spread + 6 bps d'écart taker/maker), non
facturé et non journalisé.

### Portée

Ce n'est pas un cas marginal. Le champ `exit_after_bars` est le mode de sortie des
stratégies de mesure de signal (style « rapport V4 » : sortie à la clôture de la barre
suivante, sans SL/TP) — **pour elles, 100 % des sorties passent par ce chemin**. Une
stratégie dont l'espérance par trade est de 30 à 60 bps voit son edge surévalué de
11 bps, soit **18 à 37 % de son espérance**. L'optimiseur, qui classe sur des métriques
dérivées de ce PnL, préfère donc structurellement les paramétrages qui sortent au temps.

### Correction

Aligner sur le chemin EOD :

```python
_px = c_close * (1 - self.spread_pct) if side == "long" else c_close * (1 + self.spread_pct)
self._close_at(ctx, position, i, _px, exit_reason, maker=False, ref_price=c_close)
```

⚠ **Correctif de justesse, pas réglage de trading** : il change le PnL de tout backtest
existant utilisant ces sorties, donc les paramètres déjà retenus par l'optimiseur sont à
réévaluer après application. À isoler dans sa propre branche.

**Effort** : 2 lignes + mise à jour des baselines de test. ~2 h avec la revalidation.

---

## FIN-03 — Le coût d'emprunt des jambes partielles est écrasé au moment de la clôture

**Sévérité P2 · PLAUSIBLE (lecture)**

`_close_partial_at` accumule (`position_lifecycle.py:157`) :

```python
position["borrow_cost"] = round(position.get("borrow_cost", 0.0) + borrow, 8)
```

`_close_at` **écrase** (`position_lifecycle.py:67`) :

```python
"borrow_cost": round(borrow, 6),      # ← seulement la jambe finale
```

Les deux voisines immédiates font pourtant l'inverse (lignes 82-83) :

```python
"slippage_cost": round(position.get("slippage_cost", 0.0) + slip_exit, 6),
"funding_cost":  round(position.get("funding_cost", 0.0), 6),
```

Conséquence : `total_borrow_cost` (`backtest_result.py:228`) sous-déclare l'emprunt des
jambes. `ctx.capital` est juste — c'est un défaut de **journalisation**, pas de PnL. Il
fausse l'analyse « quelle part du PnL l'emprunt mange-t-il ? », qui est la raison d'être
de cet agrégat.

**Correction** : `round(position.get("borrow_cost", 0.0) + borrow, 6)`. 1 ligne.

---

## FIN-04 — Le backtest ne dit jamais au registre de risque que le stop a monté

**Sévérité P1 · CONFIRMÉ (recherche exhaustive des appelants)**

`RiskLedger.update_risk` existe pour ça, et sa docstring le dit
(`risk_ledger.py:136-141`) : « *Appelé quand le trailing remonte le stop : le risque réel
diminue et libère du budget pour un autre bot* ».

Appelants réels :

```
app/live/position_manage_mixin.py:215     ← live
app/live/position_open_mixin.py:617       ← live
(tests)
```

**Aucun dans `app/engine/`.** `_manage_open_position` met à jour `position["stop"]`
(`position_lifecycle.py:354`) et s'arrête là. Le risque réservé au registre reste figé à
sa valeur d'ouverture pendant toute la vie du trade en backtest, alors qu'il décroît en
live.

**Conséquence** : à budget de risque symbole/venue contraignant, le live autorise une
seconde position là où le backtest la refuse. Les deux chemins n'allouent pas le même
capital dans le temps — sur une grandeur que `tests/test_execution_parity.py` prétend
verrouiller. Aucun test ne couvre ce cas.

**Correction** : après la ligne 354,

```python
if _ledger is not None and (_pk := position.get("_pos_key")):
    _ledger.update_risk(_pk, abs(entry - new_stop) * position["size"])
```

**Effort** : ~4 lignes + un test de parité d'allocation. ~2 h.

---

## FIN-05 — `_slot_notional` et `_slot_risk` sont tenus à jour mais jamais confrontés à un plafond

**Sévérité P2 · CONFIRMÉ (recherche exhaustive)**

`RiskLedger` maintient six agrégats. Cinq sont comparés à une limite dans `reserve()`.
Les deux du niveau **slot** ne le sont jamais :

```
risk_ledger.py:67-68    déclaration
risk_ledger.py:119-120  incrément dans reserve()
risk_ledger.py:133-134  décrément dans release()
risk_ledger.py:152,172,175  ajustement dans update_risk() / resize()
risk_ledger.py:185-186  lecture dans engaged()
                        → aucune comparaison
```

`reserve()` confronte bien `env.max_notional`, mais au notionnel de **la seule
transaction en cours** (`risk_ledger.py:84`), jamais au cumul déjà engagé sur le slot.

C'est **exactement** le défaut déjà corrigé un niveau au-dessus, et le commentaire du
correctif est encore là (`risk_ledger.py:91-92`) :

> *F-05 : `_venue_notional` était tenu à jour mais jamais comparé. Sans ça, N symboles à
> plein levier dépassent max_leverage déclaré.*

Le même raisonnement s'applique au slot et n'y a pas été appliqué.

**Chemin d'exploitation** : le pyramidage. Chaque incrément réserve sous une clé distincte
(`_inc_key = f"{_pk}:scale:{n}"`, `position_lifecycle.py:415`) avec la même `Envelope`.
Chaque incrément passe seul sous `env.max_notional`, et rien n'additionne. Le registre
n'empêche donc pas une position pyramidée de dépasser l'enveloppe de son slot.

**Ce qui limite la portée aujourd'hui** : le backtest se protège par un calcul local
(`room = _base × levier − position["notional"]`, `position_lifecycle.py:404`), et le live
par les garde-fous de `position_manage_mixin`. Le registre — qui est censé être *le*
garant — ne garantit rien à ce niveau. C'est un P2 par absence d'appelant fautif
aujourd'hui, pas par innocuité.

**Correction** : deux comparaisons symétriques de celles du niveau venue, avant
l'enregistrement.

---

## FIN-06 — Le pyramidage ignore la courbe de dé-risquage et le frein de volatilité

**Sévérité P2 · PLAUSIBLE (lecture)**

Une entrée neuve subit deux réductions conjoncturelles (`position_lifecycle.py:544-547`) :

```python
risk_amount = base * ctx.risk * size_factor * _risk_multiplier(dd)   # courbe de drawdown
if _gate is not None:
    risk_amount *= _gate.volatility_brake_factor                      # frein de volatilité
```

Le renfort, lui (`position_lifecycle.py:401`) :

```python
add_size = _base * ctx.risk / stop_dist * sf * self.partial_fill      # ni l'un, ni l'autre
```

En drawdown > 10 %, `risk_multiplier` renvoie 0,5 : les entrées neuves sont divisées par
deux, **les renforts restent à taille pleine**. Le mécanisme de protection est contourné
au moment précis où il devrait mordre — et il l'est en ajoutant du risque sur une position
déjà ouverte, donc déjà corrélée à la perte en cours.

`RiskSizer.compute_size` (`risk_sizer.py:96-97`) applique bien les deux facteurs : le
chemin de référence existe, le pyramidage du backtest ne l'emprunte pas.

**Correction** : réutiliser `RiskSizer.compute_size` au lieu de recalculer, ou à défaut
appliquer les deux facteurs. **Effort** : ~3 lignes.

---

## FIN-07 — Après une jambe partielle, le registre continue de réserver 100 % du trade

**Sévérité P2 · PLAUSIBLE (lecture)**

`_close_partial_at` réduit `position["size"]` et `position["notional"]`
(`position_lifecycle.py:154-155`) mais n'appelle ni `ledger.resize` ni `ledger.update_risk`.
Après TP1 (25 % sortis), le registre réserve toujours le notionnel et le risque d'origine
jusqu'à la clôture finale.

Même famille que FIN-04 : le registre décrit un état qui n'existe plus. Effet pratique :
sur-réservation temporaire qui bloque d'autres bots du même symbole. Le live fait
`resize` sur ce chemin (`position_manage_mixin.py:517`) — encore un écart backtest/live.

**Correction** : `_ledger.resize(_pk, risk=..., notional=position["notional"])` en fin de
`_close_partial_at`. ~3 lignes.

---

## FIN-08 — Le « point mort frais compris » ignore le modèle de coûts de la venue

**Sévérité P2 · PLAUSIBLE (lecture)**

`position_lifecycle.py:311` :

```python
cout = 2 * self.taker_fee + self.spread_pct
be = entry * (1 + cout) if side == "long" else entry * (1 - cout)
```

Trois composantes de `venue_trade_cost` sont ignorées : `fee_fixed` (commission fixe par
ordre), `fee_min` (plancher de courtage), `transaction_tax_pct` (TTF française, due à
l'achat). Sur une venue actions — le cas que `execution.py:150-158` a explicitement
introduit — ce « point mort » laisse le trade perdant après un aller-retour.

Le calcul contourne aussi `fee_pct` : une venue déclarant un taux propre n'est pas prise
en compte.

**Correction** : dériver le seuil de `venue_trade_cost(entry, size, ...)` des deux côtés
plutôt que d'un taux en dur. ~6 lignes.

---

## FIN-09 — `round_trip_cost` estime les frais de sortie au prix d'entrée

**Sévérité P2 · PLAUSIBLE (lecture)**

`app/core/trade_economics.py:76-79` :

```python
f_in  = venue_trade_cost(entree, taille, fee_rate, side=side, venue=venue, is_entry=True)
f_out = venue_trade_cost(entree, taille, fee_rate, side=side, venue=venue, is_entry=False)
#                        ^^^^^^ le prix d'ENTRÉE, pour la jambe de SORTIE
```

Les frais étant proportionnels au notionnel, la sortie d'un long gagnant coûte plus que
son entrée. Le module reçoit pourtant la cible (`net_rr` a `cible` en paramètre, ligne 94)
— l'information est disponible et n'est pas utilisée.

Biais **systématiquement du même signe** : sous-estimation du coût, donc `net_rr` optimiste
et `economic_edge_ok` trop permissif. Magnitude ≈ `fee_rate × (cible − entrée) × taille` :
à R/R 2 avec un stop à 2 %, le coût de sortie est sous-estimé de ~4 % — quelques dixièmes
de bps. Petit, mais il pousse toujours dans le sens de prendre le trade, et c'est un filtre
d'entrée.

**Correction** : passer le prix de la jambe de sortie. ~2 lignes.

---

## FIN-10 — `close_pnl` sans venue facture l'emprunt sur la totalité du notionnel

**Sévérité P3 · CONFIRMÉ (aucun appelant fautif)**

`app/core/execution.py:141-142` :

```python
if venue is None:
    borrowed = float(notional)      # y compris pour un long à levier 1
```

Un long couvert par les fonds propres ne devrait rien emprunter — c'est ce que
`borrowed_notional` calcule correctement juste en dessous quand une venue est fournie.

Les 4 appelants passent tous une venue (`position_lifecycle.py:40,143`,
`position_close_mixin.py:241`, `position_manage_mixin.py:473`), et le backtest résout
toujours la sienne (`backtest.py:251`). **Aucun coût faux aujourd'hui.**

Reste que le défaut par défaut va dans le mauvais sens : un futur appelant qui omet la
venue paiera un intérêt fictif silencieusement. C'est exactement le scénario que S11 a
corrigé pour les actions (« *chaque trade SBF 120 payait 0,072 %/jour d'intérêt
fictif* », `execution.py:79-82`). Le repli mériterait d'être `borrowed_notional(notional,
side, max_leverage=1.0)`.

---

## FIN-11 — La courbe de dé-risquage est une marche d'escalier

**Sévérité P3 · Observation de conception**

`app/core/risk_curve.py:17-20` : ×0,5 au-delà de 10 %, ×0,75 au-delà de 5 %, ×1 sinon.

À 9,99 % de drawdown le risque est plein ; à 10,01 % il est réduit d'un quart
supplémentaire. Deux conséquences :

1. Un paramétrage qui frôle un seuil produit des backtests instables — une bougie
   d'écart change le sizing de tous les trades suivants. C'est un facteur de variance que
   l'optimiseur interprétera comme du signal.
2. La discontinuité est identique en live, donc au moins la parité est respectée.

Ce n'est pas un défaut, c'est un choix — mais un choix à mesurer : une rampe linéaire
entre 5 % et 15 % supprimerait la sensibilité au seuil sans changer la politique. À
trancher par une mesure, pas par un principe.

---

## Ce qui a été vérifié et tenu

À signaler, parce qu'un audit qui ne liste que les défauts donne une image fausse :

- **Parité des formules monétaires** — `execution.py` est bien la source unique.
  Les 4 sites de clôture (2 backtest, 2 live) passent tous par `close_pnl` avec la venue.
- **Funding vs emprunt** — correctement disjoints : `venue_borrow_rate` renvoie 0 sur une
  venue perp (`execution.py:91-92`), et `funding_cost` modélise des périodes discrètes
  signées, encaissables par un short (`trade_economics.py:40-56`). Le signe est porté par
  le sens de la position (`backtest.py:722`).
- **Fill au gap** — `_fill_at_level` remplit à l'ouverture quand la bougie a déjà franchi
  le niveau, dans les deux sens, stop comme TP (`position_lifecycle.py:170-187`).
- **Priorité du stop** — le stop l'emporte toujours sur le TP en ambiguïté intrabar, et
  l'ambiguïté est *comptée* (`tp_sl_ambiguous_bars`) au lieu d'être ignorée.
- **Aucun usage de `float` là où `Decimal` s'imposerait** : le dépôt ne manipule pas de
  soldes comptables en base, seulement des grandeurs de marché — `float` est le bon choix
  ici, et l'absence de `Decimal` n'est pas un défaut.
- **`RiskLedger.reserve` est atomique** sous `RLock`, refuse la double réservation d'une
  même clé (L-05), et n'accorde aucune tolérance de dépassement (`_FP_EPS = 1e-9`).

---

## Récapitulatif

| ID | Sévérité | Preuve | Constat | Effort |
|---|---|---|---|---|
| FIN-01 | **P1** | CONFIRMÉ | `total_pnl` ≠ équité avec jambes partielles | 30 min |
| FIN-02 | **P1** | CONFIRMÉ | Sorties early/time sans spread ni taker | 2 h |
| FIN-04 | **P1** | CONFIRMÉ | Backtest n'appelle jamais `update_risk` | 2 h |
| FIN-03 | P2 | PLAUSIBLE | `borrow_cost` des jambes écrasé | 15 min |
| FIN-05 | P2 | CONFIRMÉ | Plafond slot jamais appliqué au cumul | 1 h |
| FIN-06 | P2 | PLAUSIBLE | Pyramidage hors courbe de drawdown | 30 min |
| FIN-07 | P2 | PLAUSIBLE | Registre non redimensionné après jambe | 30 min |
| FIN-08 | P2 | PLAUSIBLE | Point mort hors modèle de venue | 1 h |
| FIN-09 | P2 | PLAUSIBLE | Frais de sortie estimés au prix d'entrée | 15 min |
| FIN-10 | P3 | CONFIRMÉ | Repli `venue=None` facture tout le notionnel | 15 min |
| FIN-11 | P3 | — | Courbe de risque en marches | à mesurer |
