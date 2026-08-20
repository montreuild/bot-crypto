# 17 — Confrontation avec l'audit précédent et ses révisions

Sources confrontées : `audit/00-SYNTHESE.md` … `audit/12-ARCHITECTURE-DETTE.md`
(audit du 2026-08-18), `audit/13-` à `20-REVISION-2026-08-18.md`, et le backlog
`audit/2026-08-18-revue-complete/`.

Le rapport `20-REVISION-2026-08-18.md` dresse la liste de ce qui a été
**livré**. J'ai vérifié cette liste ligne à ligne contre le code à `bb94993`.

**Résultat : sur 48 corrections annoncées comme livrées, 39 le sont
effectivement, 6 le sont partiellement, et 3 ont introduit un défaut.**

Le point important n'est pas le décompte : c'est que **les six corrections
partielles partagent toutes la même forme** — le code a été écrit, mais rien ne
le relie à une décision. Aucune n'aurait survécu à un test qui échoue avant le
correctif.

---

## 1. Corrections annoncées qui ont introduit un défaut

### FIN-01 de la révision → mes `FIN-01` et `FIN-02`

> **Annoncé** : « `_close_at` lit `entry_fees` (jamais pollué par les jambes). »

Le diagnostic était juste : `position["fees"]` accumule les frais des jambes,
et les retrancher du PnL les comptait deux fois. La correction bascule
`_close_at` sur `position["entry_fees"]`.

**Ce que la révision n'a pas vu** : `entry_fees` est figé à l'ouverture et
n'est jamais incrémenté au pyramidage, et le champ journalisé `"fees"` est
**écrasé** par la même ligne. La correction règle le double comptage et crée
deux nouveaux écarts :

| Effet | Mesure |
|---|---|
| Frais des jambes et pyramidages absents du reporting | −11 % (jambes) à −23,6 % (jambes + 2 pyramidages) |
| Frais d'entrée des pyramidages non retranchés du PnL | Σ`pnl` − courbe d'équité = +0,4634 |

Détail dans `04-MOTEUR-FINANCIER.md`.

**Ce qui rend ce cas instructif** : la révision annonce au point voisin
**FIN-03** — « `borrow_cost` accumulé, plus écrasé à la clôture ». Le même
défaut d'écrasement a donc été identifié et corrigé pour `borrow_cost`
(`position_lifecycle.py:78`), et laissé en place pour `fees`
(`:77`), une ligne au-dessus. Les deux lignes sont adjacentes.

---

### BT-02 de la révision → mon `BT-01`

> **Annoncé** : « WF hérite `backtest.realistic_risk`. »

Techniquement exact : le walk-forward lit désormais la clé de configuration.

**Ce que la révision n'a pas vérifié** : la clé n'existe ni dans `config.yaml`
ni dans `config/*.yaml`. `realistic_risk` valait `True` en dur ; il vaut
maintenant `False`. Et `_run_baseline` (`auto_optimizer.py:329`) conserve
`realistic_risk=True`.

Le gate d'auto-apply compare donc un baseline **avec** circuit breakers à un
walk-forward **sans**. Rendre un réglage configurable sans poser sa valeur
équivaut à changer sa valeur par défaut.

---

### DATA-03 de la révision → mes `DAT-01` et `PERF-01`

> **Annoncé** : « Écarts OHLCV pré-calculés (diff vectorisé). »

La vectorisation est bien là et le gain sur les faux trous de week-end est
spectaculaire (**1 402 → 47** sur `AC.PA` 1d, données réelles).

**Ce que la révision ne mentionne pas** : le même commit ajoute
`allowed = max(allowed, cal.max_gap_seconds(ts, expected_secs))`. Cette ligne
porte le seuil de détection de 1,5×tf à 3×tf sur les marchés 24/7 et **masque
15 trous réels sur `BTC_USDC` 1h**. Elle réintroduit précisément ce que le
commentaire supprimé au même endroit mettait en garde de faire.

Le gain de performance annoncé est par ailleurs inversé : la mesure A/B donne
**×1,75 plus lent** globalement, jusqu'à ×5,53. La garde censée éviter le
chemin calendaire (`cal is None`) n'est jamais vraie.

Détails dans `09-DONNEES.md` et `13-PERFORMANCE.md`.

---

## 2. Corrections annoncées mais seulement à moitié câblées

### OPT-03 : « `beats_baseline` refuse un DD +25 % » → mon `OPT-02`

Le refus existe. Il dépend de l'argument `oos_dd`. `auto_optimizer.py:657` le
transmet ; **`app/api/routes/optimizer.py:349` ne le transmet pas**.

Or le code de cette route note lui-même que le chemin manuel est le plus
fréquent, `auto_apply` étant désactivé par défaut. Le garde-fou couvre le
chemin le moins emprunté. Mesuré : la route manuelle accepte un drawdown OOS de
80 % contre un baseline à 10 %.

### OPT-06 : « Win-rate seul insuffisant » → mon `OPT-01`

La règle voulue est « Sharpe **ou** expectancy **ou** profit factor ». Elle est
morte des deux côtés : `_run_baseline` ne produit ni `profit_factor` ni
`expectancy`, et aucun des deux appelants ne passe `oos_pf` / `oos_expectancy`.

La règle effective est « Sharpe uniquement » — plus stricte que voulu. Un
paramétrage qui sextuple le PnL et double le profit factor est refusé pour
0,01 point de Sharpe.

### ML-01 : « IC Hanley–McNeil ; borne basse < 0,50 bloque » → mon `ML-01`

La borne est calculée et le verdict `block` est produit. Mais à l'unique site
d'appel (`app/ml/policy.py:321`), le verdict n'alimente qu'un `logger.warning`
et des métadonnées de registre. **Il ne modifie jamais `gate.decision`** : le
modèle est publié.

Ce garde-fou ne bloque rien. La révision commente d'ailleurs le cas comme
« rare » et renvoie l'opérateur au réglage de `auc_floor` — mais `auc_floor` et
le test d'intervalle de confiance ne sont pas substituables : le second dépend
de `n_oos_samples`, qu'aucun réglage d'`auc_floor` ne compense.

Second point non vu : la borne suppose `n1 = n0 = n/2` alors que `n` est un
nombre de barres. Sur labels déséquilibrés, le garde-fou est systématiquement
trop permissif (6 scénarios sur 7 mesurés). Détail dans `08-ML.md`.

### API-01 / FE-03 : « `generated.ts` = modèles publics de `schemas.py` » → mon `API-02`

Le générateur et le test existent. Le test ne vérifie que la **présence des
noms d'interfaces**, jamais leurs champs. Un champ ajouté côté Pydantic sans
régénération passe le test, passe `tsc`, et fait diverger le contrat en
silence. Le risque est concret : `app/api/schemas.py` gagne 476 lignes dans ce
delta.

### ARCH-04 : « mypy `app/core` + `app/engine` bloquant, 0 `ignore_errors` » → mon `CI-02`

**La configuration est bien conforme** : `mypy.ini` ne contient plus aucun
`ignore_errors` (la seule occurrence du mot est un commentaire), et le job CI
couvre bien `app/core` et `app/engine`.

Mais le gate est **rouge sur HEAD** : `app/core/database.py:336` produit une
erreur `arg-type`. Le garde-fou a donc été établi, puis cassé par un commit
ultérieur du même delta, sans que rien ne le signale.

---

## 3. Corrections annoncées et confirmées

Vérifiées une par une, conformes à l'annonce :

| Réf révision | Vérification faite |
|---|---|
| **FIN-03** `borrow_cost` accumulé | `position_lifecycle.py:78` accumule bien |
| **FIN-04** `ledger.update_risk` après trailing | `:293-295`, présent |
| **FIN-06** Pyramidage × courbe DD × frein volatilité | `:400-408`, présent |
| **FIN-07** `ledger.resize` après jambe | `:179-187`, présent |
| **FIN-08** Point mort via `venue_trade_cost` | `:230-240`, présent |
| **BT-01** Fold OOS préfixé du warmup | Vérifié **sans fuite** : `warmup ≥ 210 = WARMUP` |
| **BT-03** `n_folds_failed` / `erreurs` | `walk_forward.py:112-118`, exploité par le gate |
| **BT-10** `is_nested: true` | `walk_forward.py:139`, présent |
| **LIVE-01** Échec d'annulation relance l'id | `position_manage_mixin.py:381`, présent |
| **LIVE-02** `_order_failed` refuse open/new/pending | `position_open_mixin.py:87-88`, présent |
| **LIVE-04** `.get("paper_mode", True)` | 6 occurrences vérifiées |
| **ML-02** `chrono_split` via `default_purge_embargo` | Vérifié **strictement plus sûr** |
| **ML-03** `fit_trace` causal | `app/ml/fit_trace.py` + test, présent |
| **ML-04** Train / calib / eval séparés | `splitting.py::val_eval_cut`, présent |
| **SEC-02** `?api_key=` retiré, jeton éphémère | Vérifié, 192 bits, usage unique, 30 s |
| **ARCH-02** Découpage de `_manage_open_position` | 4 fonctions, ordre des opérations préservé |
| **ARCH-03** Paquets `smc/` et `risk/` | Présents ; dette de double identité (`ARCH-01`) |
| **API-03** Bornes `Field` sur les enveloppes | Présentes + testées (`test_venue_envelope_bounds`) |
| **TEST-01** `--cov-fail-under=64` | Présent dans **les deux** pipelines |
| **TEST-02** Plancher CI frontend | `vitest.config.ts` : statements 5, branches 20, functions 10 |
| **UX-02** Formatage `fr-FR` | Centralisé dans `lib/utils.ts` + testé |
| **PERF-05** Index par barre dans `prepare_for_backtest` | Présent + `test_perf05_smart_money.py` |

---

## 4. Points restés ouverts, confirmés encore ouverts

Le `§2` de `20-REVISION-2026-08-18.md` liste ce qui reste. Vérifié :

| Réf | État à `bb94993` |
|---|---|
| **FE-01 / TEST-02** — objectif 30 % de couverture frontend | Toujours ouvert ; plancher à 5 % en place, 190 tests |
| **API-04 / TEST-04** — `scanner_service.py` peu couvert | Toujours 778 lignes, non traité dans ce delta |
| **U-05** — `as any` résiduels | Non traité ; `tsc` reste vert |
| **DATA-01** — feature store sur 1 symbole | Confirmé : `data/features/` ne contient que `BTC_USDC` (413 Mo) |
| **X-02 / X-06** — doublons de stratégies | Reportés par consigne, non traités |

Ces points sont correctement suivis : rien n'a été annoncé livré à tort.

---

## 5. Ce que la révision précédente a bien anticipé

Le `§3` de `20-REVISION-2026-08-18.md` porte un avertissement que mes mesures
confirment :

> « FIN-01 / FIN-02 / BT-01 changent les chiffres des backtests existants.
> Revalider les paramètres déjà retenus par l'optimiseur avant de les
> réappliquer. »

C'est exact, et l'ampleur est maintenant chiffrée : les coûts journalisés
sous-estiment de 11 % à 24 %, et le PnL par trade diverge de la courbe
d'équité dès qu'il y a pyramidage. **La revalidation annoncée ne doit pas être
faite avant les correctifs `FIN-01` et `FIN-02`**, sinon elle figerait des
paramètres choisis sur des coûts faux.

---

## 6. Enseignement de méthode

Six corrections annoncées livrées sont inertes, et trois ont introduit un
défaut. Aucune n'était mal conçue : dans les neuf cas, le code exprime la bonne
intention.

Ce qui manque est unique et identique partout : **aucune de ces corrections
n'a été accompagnée d'un test qui échoue avant le correctif.**

- Un test « le PnL des trades égale la variation de capital » aurait attrapé
  `FIN-01` et `FIN-02` ;
- un test « un candidat qui améliore le profit factor est accepté » aurait
  attrapé `OPT-01` ;
- un test « la route apply refuse un DD dégradé de +25 % » aurait attrapé
  `OPT-02` ;
- un test « un modèle `block` n'est pas publié » aurait attrapé `ML-01` ;
- un test « une série 1h à laquelle on retire une barre produit un trou »
  aurait attrapé `DAT-01` ;
- un test « le `realistic_risk` du baseline égale celui des folds » aurait
  attrapé `BT-01`.

Chacun tient en une dizaine de lignes. C'est le critère d'acceptation que j'ai
posé dans **chaque** fiche de délégation de cet audit : le test doit échouer
sur le code actuel avant que le correctif ne soit écrit.

C'est la seule recommandation de ce rapport qui vaille pour tous les autres.
