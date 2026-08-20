# 15 — Tests et CI

Toutes les commandes ci-dessous ont été exécutées sur `bb94993` (HEAD), depuis
l'environnement du projet (`.venv`, Python 3.14.6), en reproduisant **les
commandes exactes du workflow** `.github/workflows/ci.yml`.

| Gate CI | Commande reproduite | Résultat |
|---|---|---|
| `lint` | `ruff check .` (ruff 0.15.8, version épinglée en CI) | **ROUGE — 3 erreurs** |
| `mypy` | `mypy app/core app/engine app/api/ws_tickets.py app/live/protocols.py app/ml/overfitting_gate.py` | **ROUGE — 1 erreur** |
| `test` | `pytest tests/ -q --tb=short -m "not slow" --cov=app --cov-fail-under=64` (commande CI exacte) | vert — 2 126 passés, 27 ignorés, 19 déselectionnés ; **couverture 67,10 %** (188 s) |
| `frontend` / lint | `npm run lint` | vert |
| `frontend` / type-check | `npm run type-check` | vert |
| `frontend` / test | `vitest run` | vert — 190 passés / 20 fichiers |

**Deux des six gates sont rouges sur HEAD.**

---

## CI-01 — Le job `lint` échoue sur HEAD (P1, CONFIRMÉ)

**Fichier** : `tests/test_audit_a02_ml_data.py:69`, `:86`, `:103`.
**Fichier ajouté par le delta** (+51 lignes).

```
tests\test_audit_a02_ml_data.py:69:5:  I001 [*] Import block is un-sorted or un-formatted
tests\test_audit_a02_ml_data.py:86:5:  I001 [*] Import block is un-sorted or un-formatted
tests\test_audit_a02_ml_data.py:103:5: I001 [*] Import block is un-sorted or un-formatted
Found 3 errors.
[*] 3 fixable with the `--fix` option.
```

Les trois occurrences sont des blocs d'import **à l'intérieur de fonctions**
(colonne 5), non triés.

**Scénario d'échec** — `.github/workflows/ci.yml:20` lance `ruff check .` sur
tout le dépôt. Le job `lint` sort en code 1 : **toute PR ouverte depuis HEAD
part avec la CI rouge**, indépendamment de son contenu.

**Vérification** — **CONFIRMÉ**, avec la version de ruff épinglée par la CI
(`ruff 0.15.8`, identique en local), et la commande exacte `ruff check .`.

**Correctif** — `ruff check --fix tests/test_audit_a02_ml_data.py`. Les trois
erreurs sont marquées corrigeables automatiquement.

**Effort** : 2 min.

**Délégation IA** —
> `ruff check .` échoue sur HEAD avec 3 erreurs I001 dans
> `tests/test_audit_a02_ml_data.py` (blocs d'import internes aux fonctions, non
> triés). Lancer `ruff check --fix tests/test_audit_a02_ml_data.py`, puis
> vérifier que `ruff check .` sort en code 0 et que
> `pytest tests/test_audit_a02_ml_data.py -q` reste vert.

---

## CI-02 — Le job `mypy` échoue sur HEAD (P1, CONFIRMÉ)

**Fichier** : `app/core/database.py:336`.
**Fichier modifié par le delta** (+18/−14).

```
app\core\database.py:336: error: Argument 1 to "parse_pos_key" has incompatible
type "Column[str] | str"; expected "str"  [arg-type]
            _sym, _strat, tf = parse_pos_key(r.id or "")
```

`r` est une ligne SQLAlchemy ; `r.id` est typé `Column[str] | str` par les
stubs. `r.id or ""` ne réduit pas le type pour mypy, alors que `parse_pos_key`
exige un `str`.

**Scénario d'échec** — `.github/workflows/ci.yml:34` lance mypy sur un
périmètre qui **inclut `app/core`**. Le job sort en code 1. Même conséquence
que CI-01 : toute PR issue de HEAD est rouge.

**Vérification** — **CONFIRMÉ** avec la commande exacte du workflow.
`Found 1 error in 1 file (checked 110 source files)`.

**Correctif** — `parse_pos_key(str(r.id or ""))`, ou une annotation locale.

**Effort** : 5 min.

**Délégation IA** —
> `python -m mypy app/core app/engine app/api/ws_tickets.py app/live/protocols.py app/ml/overfitting_gate.py`
> (la commande du job `mypy` de la CI) échoue sur `app/core/database.py:336` :
> `r.id` est typé `Column[str] | str` et `parse_pos_key` attend un `str`.
> Corriger par une conversion explicite. Vérifier que la commande sort en
> code 0 et que `pytest -q` reste à 2 142 passés.

---

## TEST-01 — La suite ne couvre pas la conservation des coûts (P1, CONFIRMÉ)

**Fichier** : `tests/test_partial_exits.py:196-215`.

Le fichier porte explicitement une intention de **conservation** — sa docstring
annonce « la somme des jambes plus le reliquat redonne le PnL du trade ». Les
tests écrits vérifient :

- `test_les_jambes_sont_realisees_et_tracees` — nombre et raison des jambes ;
- `test_la_taille_restante_est_le_runner` — conservation des **tailles** ;
- `test_le_pnl_du_trade_agrege_les_jambes_et_le_reliquat` — inégalité
  `t["pnl"] > somme des jambes` ;
- `test_l_equite_finale_reste_coherente` — cadence de la courbe d'équité.

**Aucun ne vérifie une égalité sur les frais ni sur le capital.** C'est
précisément pourquoi `FIN-01` et `FIN-02` sont passés : la suite est verte
(2 142 passés) alors que `t["fees"]` sous-estime les coûts de 11 % à 24 % et
que `Σ t["pnl"]` diverge de la courbe d'équité dès qu'il y a pyramidage.

**Scénario d'échec** — un correctif futur qui déplacerait encore la
comptabilité des frais ne serait détecté par aucun test.

**Vérification** — **CONFIRMÉ** : la suite complète passe sur un code où les
deux défauts sont reproduits par ailleurs.

**Correctif proposé** — deux tests d'invariant :

1. **conservation du capital** —
   `capital_initial + Σ t["pnl"] == equity_curve[-1]` à 1e-6 près, sur une
   stratégie avec jambes **et** pyramidage ;
2. **conservation des frais** — instrumenter
   `app.engine.position_lifecycle._close_pnl` et `Backtester._fees` pour
   totaliser les frais réellement prélevés, et vérifier l'égalité avec
   `Σ t["fees"]`.

Les deux doivent échouer sur le code actuel.

**Effort** : 2 h.

**Délégation IA** —
> Ajouter dans `tests/test_partial_exits.py` deux tests d'invariant comptable,
> sur une stratégie déclarant à la fois des `exits` partiels et un
> `check_scale_in` :
> 1. `capital_initial + sum(t["pnl"] for t in res.trades) == res.equity_curve[-1]`
>    à 1e-6 près ;
> 2. la somme des `t["fees"]` égale les frais réellement prélevés, mesurés en
>    instrumentant `app.engine.position_lifecycle._close_pnl` et
>    `app.engine.backtest.Backtester._fees`.
> Ces deux tests DOIVENT échouer sur le code actuel (écarts respectifs : les
> frais d'entrée des pyramidages, et les frais des jambes + pyramidages).
> Ils constituent le critère d'acceptation de FIN-01 et FIN-02.

---

## TEST-02 — Le périmètre mypy couvre moins de la moitié de `app/` (P2, CONFIRMÉ)

**Fichiers** : `.github/workflows/ci.yml:34`, `mypy.ini:1-12`.

Le job CI type-vérifie `app/core`, `app/engine`, plus trois fichiers isolés :
**110 fichiers sur 227**. Ne sont pas couverts : `app/ml`, `app/live` (sauf
`protocols.py`), `app/api` (sauf `ws_tickets.py`), `app/strategies`.

Par ailleurs `mypy.ini:5` pose `check_untyped_defs = False` globalement. Seuls
**six modules** le réactivent (`sanitize`, `ohlcv_gaps`, `overfitting_gate`,
`ws_tickets`, `protocols`, `backtest_result`). Pour tout le reste, **le corps
des fonctions non annotées n'est pas analysé du tout** — ce que mypy signale
lui-même par 142 notes `[annotation-unchecked]`.

Mesure du périmètre non couvert : `mypy app/` complet remonte **347 erreurs
dans 56 fichiers**.

| Code | Occurrences |
|---|---:|
| `attr-defined` | 188 |
| `annotation-unchecked` (note) | 142 |
| `arg-type` | 62 |
| `assignment` | 23 |
| `union-attr` | 14 |
| `misc` | 12 |
| `var-annotated` | 11 |
| `index` | 11 |
| `has-type` | 11 |
| `override` | 5 |

**Scénario d'échec** — un `attr-defined` dans `app/live` ou `app/ml` — un
attribut mal nommé sur un objet de position ou de modèle — passe la CI et ne
se révèle qu'à l'exécution, en live.

**Vérification** — **CONFIRMÉ** : les deux commandes ont été exécutées et
comparées (110 fichiers vérifiés en CI contre 227 dans `app/`).

Ce constat n'est **pas** une régression du delta : le delta a au contraire
étendu le périmètre (`ARCH-04` de l'audit précédent a levé
`ignore_errors` sur `app/core` et `app/engine`). Il est signalé pour cadrer
l'effort restant.

**Correctif proposé** — traiter par lots, en commençant par `app/live`
(risque le plus direct sur le capital), puis `app/ml`, puis `app/api`. Ajouter
chaque module au job CI une fois propre, pour empêcher les retours en arrière.

**Effort** : 3 à 5 jours pour les 347 erreurs. À découper par paquet.

**Délégation IA** —
> Étendre le périmètre mypy module par module, en commençant par `app/live`.
> Pour chaque lot : lancer `python -m mypy app/live`, corriger les erreurs
> **sans changer le comportement à l'exécution** (annotations, `cast`,
> resserrement de types — pas de `# type: ignore` sauf justification en
> commentaire), puis ajouter le chemin à la commande du job `mypy` de
> `.github/workflows/ci.yml`. Critère d'acceptation par lot : `pytest -q` reste
> à 2 142 passés et la commande CI sort en code 0.

---

## Ce qui a été vérifié sans rien trouver

- **Couverture apportée par le delta** — 16 fichiers de test créés ou étendus,
  dont `test_backtest_dual_run.py`, `test_ml03_fit_causality.py` (130 lignes),
  `test_perf05_smart_money.py` (113), `test_revue_2026_08_18.py` (123),
  `test_candle_store_backfill.py` (77), `test_openapi_contracts.py` (49). Côté
  frontend, 6 fichiers de test nouveaux (`api.test.ts` 131 lignes,
  `utils.test.ts` 125, `use-api.test.tsx` 78, `symbol-search.test.tsx` 66,
  `strategy-picker.test.tsx` 46). L'effort de test du delta est réel et
  substantiel.
- **`pip-audit`** — le job `security` de la CI lance `pip-audit` sur
  `requirements.txt` et `requirements-dev.txt`. Le mécanisme est en place ; je
  ne l'ai pas exécuté (accès réseau à la base d'avis requis).
- **Jobs frontend** — `lint`, `type-check`, `test:coverage` et `build` sont
  tous câblés (`ci.yml:85-98`). Les trois premiers ont été reproduits et sont
  verts.
- **Marqueur `slow`** — la CI lance `-m "not slow"`, soit 19 tests
  déselectionnés. J'ai exécuté les deux : la suite complète (2 142 passés) et
  le sous-ensemble CI (2 126 passés). Les deux sont verts.
- **Seuil de couverture** — `--cov-fail-under=64` est présent dans **les deux**
  pipelines (`.github/workflows/ci.yml:49-50`, commande pytest sur deux
  lignes ; et `.gitlab-ci.yml:36`). Le gate passe : **67,10 %** de couverture
  mesurée sur `app/` (29 533 instructions, 9 716 non couvertes), soit 3 points
  de marge. Le pipeline GitLab est bien le miroir qu'il annonce être.
- **Actions Node 24** — `38516b6` met à jour les actions et corrige l'export
  nommé de `eslint.config.mjs`. `npm run lint` est vert.
