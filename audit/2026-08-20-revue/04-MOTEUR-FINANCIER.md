# 04 — Moteur financier (coûts, PnL, risque)

Domaine le plus touché par le delta :
`app/engine/position_lifecycle.py` (+161/−138, réécriture du cycle de vie),
`app/engine/backtest_result.py` (+147/−72), `app/core/risk/` (paquet neuf,
1 524 lignes).

Le refactor de `position_lifecycle.py` (commit `fec34ed`) découpe
`_manage_open_position` — qui faisait 190 lignes — en quatre fonctions :
`_evaluer_sorties`, `_appliquer_jambes`, `_mettre_a_jour_trailing`, plus
l'orchestrateur. Le découpage est propre et l'ordre des opérations est
préservé (sorties → jambes → trailing → pyramidage). **Mais il a déplacé la
comptabilité des frais, et c'est là que sont les deux constats P1.**

---

## FIN-01 — Les frais des jambes partielles et des scale-in disparaissent du reporting (P1, CONFIRMÉ)

**Fichier** : `app/engine/position_lifecycle.py:65` et `:77`.
**Introduit par** : `fec34ed` (dans le delta).

### Le code

```python
# app/engine/position_lifecycle.py:65
entry_fees = float(position.get("entry_fees", position.get("fees", 0.0)) or 0.0)
fees = entry_fees + fees          # fees = frais de la sortie finale seulement
...
"fees": round(fees, 6),           # ligne 77 — ÉCRASE l'accumulateur
```

Avant le delta la ligne 65 était `entry_fees = float(position.get("fees", 0.0) or 0.0)`.

`position["fees"]` est un **accumulateur** : il est incrémenté à chaque jambe
partielle (`position_lifecycle.py:167`) et à chaque pyramidage
(`position_lifecycle.py:460`). Depuis le delta, `_close_at` ne le lit plus —
il lit `position["entry_fees"]`, qui est figé à l'ouverture
(`position_lifecycle.py:677`) et n'est jamais mis à jour. La ligne 77 réécrit
ensuite `position["fees"]` avec `entry_fees_initiaux + frais_de_sortie_finale`.

Tous les frais intermédiaires sont perdus.

### Scénario d'échec

Stratégie long avec deux cibles partielles (TP1 25 %, TP2 25 %), 400 barres
haussières, `taker_fee=0.001`, `maker_fee=0.0004`, `spread_pct=0.0005`.

| Grandeur | Jambes seules | Jambes + 2 pyramidages |
|---|---:|---:|
| `position["fees"]` accumulé juste avant `_close_at` | 1,142823 | 1,606289 |
| dont `entry_fees` initiaux | 0,950000 | 0,950000 |
| **Frais intermédiaires effacés** | **0,192823** | **0,656289** |
| `t["fees"]` finalement journalisé | 1,555363 | 2,129178 |
| Frais réellement prélevés | 1,748186 | 2,785467 |
| **Sous-estimation** | **−11,0 %** | **−23,6 %** |

`total_fees` de `BacktestResult` (`app/engine/backtest_result.py:314`) somme ce
champ : **le coût total affiché est sous-estimé de 11 % à 24 %** dès qu'une
stratégie utilise des sorties partielles ou du pyramidage. Toutes les
spécifications SMC/ICT du dépôt sont bâties sur `TP1 25 % → TP2 25 % → runner`,
donc le cas est nominal, pas marginal.

### Vérification

**CONFIRMÉ.** Deux reproductions indépendantes :

1. instrumentation de `_close_pnl` et `Backtester._fees` pour totaliser les
   frais réellement prélevés, comparés à `t["fees"]` ;
2. interception de `_close_at` pour capturer `position["fees"]` juste avant
   l'écrasement — c'est la mesure décisive, elle isole l'écrasement sans
   dépendre d'une reconstruction.

Les deux donnent le même chiffre. C'est une **régression du delta** : avant
`fec34ed`, `t["fees"]` était correct.

### Correctif proposé

Ligne 65, dissocier les deux rôles :

```python
frais_cumules = float(position.get("fees", 0.0) or 0.0)      # entrée + jambes + scale-in
entry_fees    = float(position.get("entry_fees", 0.0) or 0.0)
fees = frais_cumules + fees        # ligne 66 : total réel
```

et conserver `entry_fees` uniquement pour la ligne `"pnl"` (voir FIN-02).

**Effort** : 30 min correctif + 1 h de test.

### Délégation IA

> Dans `app/engine/position_lifecycle.py::_close_at`, séparer deux notions
> aujourd'hui confondues : `position["fees"]` est l'accumulateur de TOUS les
> frais (entrée, jambes partielles, pyramidages), `position["entry_fees"]` est
> le seul montant d'entrée initial. Le champ journalisé `"fees"` doit valoir
> `accumulé + frais de sortie finale` ; le champ `"pnl"` doit retrancher les
> frais d'entrée **y compris ceux des pyramidages** (cf. FIN-02).
> Ajouter dans `tests/test_partial_exits.py` un test de conservation des coûts :
> instrumenter `app.engine.position_lifecycle._close_pnl` et
> `Backtester._fees`, faire tourner une stratégie à 2 jambes + 2 pyramidages,
> et vérifier `sum(t["fees"]) == frais réellement prélevés` à 1e-6 près.
> Ce test doit échouer sur le code actuel.

---

## FIN-02 — La somme des PnL de trades diverge de la courbe d'équité dès qu'il y a pyramidage (P1, CONFIRMÉ)

**Fichier** : `app/engine/position_lifecycle.py:75`.

### Le code

```python
"pnl": round(pnl + realized - entry_fees, 6),
```

`entry_fees` vaut les frais d'entrée **initiaux**. Or chaque pyramidage prélève
ses propres frais d'entrée sur le capital
(`position_lifecycle.py:450` : `ctx.capital -= add_fees`) sans jamais mettre à
jour `entry_fees`. Ces `add_fees` ne sont donc retranchés nulle part du PnL
journalisé.

### Scénario d'échec

Même série que FIN-01, avec `check_scale_in` renvoyant `size_factor=0.5` deux
fois :

```
capital initial + Σ t["pnl"]  = 1 245,447125
capital final (courbe d'équité) = 1 244,983700
ÉCART                           = +0,463425
```

Sans pyramidage, l'écart est de `+0,000018` (arrondi) : la conservation est
exacte. **L'écart est exactement la somme des frais d'entrée des pyramidages.**

Conséquence : le PnL par trade, le win-rate, le profit factor et l'expectancy —
tous calculés depuis `t["pnl"]` — sont **optimistes**, alors que la courbe
d'équité, elle, est juste. Deux vérités coexistent dans le même
`BacktestResult`.

L'ampleur est ici de 0,19 % du PnL du trade, avec deux pyramidages et des frais
de 10 bps. Elle croît linéairement avec le nombre de pyramidages et le niveau
de frais.

### Nuance importante

Le delta a **corrigé** un défaut plus gros au même endroit. Avant `fec34ed`,
`entry_fees` valait l'accumulateur complet : les frais de sortie des jambes
partielles étaient retranchés **deux fois** (une fois nettés dans
`_realized_pnl`, une fois via `entry_fees`). Le solde du delta est donc
positif ; il reste ce résidu.

### Vérification

**CONFIRMÉ** — invariant de conservation du capital mesuré avec et sans
pyramidage, sur la même série.

### Correctif proposé

Tenir un accumulateur dédié `position["entry_fees"]` mis à jour au pyramidage :

```python
# position_lifecycle.py, bloc de scale-in (~ligne 460)
position["entry_fees"] = round(position.get("entry_fees", 0.0) + add_fees, 6)
```

Une ligne. Elle rétablit l'invariant.

**Effort** : 15 min correctif + 45 min de test.

### Délégation IA

> Dans `app/engine/position_lifecycle.py`, bloc de pyramidage (là où
> `position["fees"]` est incrémenté de `add_fees`), incrémenter aussi
> `position["entry_fees"]` du même montant : ce sont des frais d'ENTRÉE, et
> `_close_at` s'en sert pour retrancher les frais d'entrée du PnL journalisé.
> Ajouter un test qui vérifie l'invariant de conservation
> `capital_initial + sum(t["pnl"]) == equity_curve[-1]` à 1e-6 près, sur une
> stratégie avec pyramidage. Ce test doit échouer sur le code actuel avec un
> écart égal à la somme des `add_fees`.

---

## FIN-03 — Le point mort après jambe utilise le modèle de coûts de la venue (P3, CONFIRMÉ — amélioration)

**Fichier** : `app/engine/position_lifecycle.py:230-240`.

Le calcul du stop au point mort après une sortie partielle est passé de
`cout = 2 * self.taker_fee + self.spread_pct` à un appel réel à
`venue_trade_cost` pour l'entrée et la sortie, ramené au notionnel.

C'est un **gain de justesse** : les venues à commission fixe ou à plancher
(actions, TTF) n'étaient pas modélisées par la formule proportionnelle. Aucun
défaut relevé. Signalé pour mémoire, le point mort étant un paramètre sensible.

**Vérification** — lecture du code et de `app/core/execution.py::venue_trade_cost`.

---

## FIN-04 — Le pyramidage passe désormais par la courbe de risque et le frein de volatilité (P3, CONFIRMÉ — amélioration)

**Fichier** : `app/engine/position_lifecycle.py:400-408`.

Le sizing d'un pyramidage était `base × risk / stop_dist × size_factor ×
partial_fill`. Il applique maintenant en plus `_risk_multiplier(dd)` (courbe de
dé-risquage en drawdown) et `gate.volatility_brake_factor`.

Cela aligne le pyramidage sur le sizing d'entrée. Changement de comportement
volontaire, cohérent, et qui va dans le sens prudent. Aucun défaut relevé —
mais c'est **un changement de paramétrage de trading**, pas un correctif de
correctness : il modifie les résultats de tous les backtests de stratégies qui
pyramident. À valider par l'utilisateur avant merge.

---

## FIN-05 — `RiskLedger` est désormais mis à jour aux jambes et au trailing (P3, CONFIRMÉ — amélioration)

**Fichiers** : `app/engine/position_lifecycle.py:179-187` (`_ledger.resize`
après une jambe), `:293-295` (`_ledger.update_risk` après remontée du stop).

Le registre de risque suivait le risque **réservé à l'entrée** et ne le
révisait qu'à la clôture. Il suit maintenant la taille réelle et la distance au
stop réelle. Le budget de risque libéré par une jambe ou par une remontée de
stop redevient donc disponible, ce qui est correct.

Aucun défaut relevé.

---

## Ce qui a été vérifié sans rien trouver

- **Ordre des opérations du refactor** — `_evaluer_sorties` → `_appliquer_jambes`
  → `_mettre_a_jour_trailing` → pyramidage reproduit exactement l'ordre
  d'avant. La priorité conservative stop > TP est préservée
  (`position_lifecycle.py:255-262`).
- **Sorties anticipée et temporelle** — elles passent de
  `(prix=close, maker=True, ref_price=None)` à
  `(prix=close∓spread, maker=False, ref_price=close)`. Le spread est appliqué
  au prix **et** enregistré dans `slippage_cost`, mais `slippage_cost` est
  purement descriptif : il n'est pas retranché du capital
  (`position_lifecycle.py:88`). **Pas de double comptage.** Changement volontaire
  vers plus de conservatisme (frais taker au lieu de maker).
- **`_close_partial_at`** — le notionnel est bien mis au prorata de la taille
  sortie (`:145`), et `size_initial` sert de base aux fractions : les jambes ne
  dérivent pas.
- **Paquet `app/core/risk/`** — les 8 modules déplacés sont identiques à leur
  version d'origine (diff limité aux imports). Aucun état module mutable
  rebindable, donc pas de divergence de comportement liée au découpage
  (voir `ARCH-01`).
