# 16 — Dette technique

Le dépôt est dans un état sain pour sa taille (63 125 lignes Python,
34 115 lignes TS/TSX). La dette mesurable est **faible et localisée** ; le
delta l'a réduite plus qu'il ne l'a augmentée.

---

## 1. Indicateurs mesurés

| Indicateur | Valeur | Lecture |
|---|---:|---|
| `TODO` / `FIXME` / `HACK` / `XXX` dans `app/` | **3** | Remarquablement bas pour 227 modules |
| Erreurs mypy hors périmètre CI | 347 / 56 fichiers | Le vrai poste de dette |
| Fichiers Python > 700 lignes | 11 | Concentrés sur `candle_store`, `auto_optimizer`, stratégies |
| Composants TSX > 600 lignes | 4 | `smart-replay-view`, `backtest-results`, `bots/page`, `smart-graph-view` |
| `datetime.utcnow()` (déprécié) | 10 occurrences | Suppression annoncée par CPython |
| Avertissements pytest | 14 (13 `DeprecationWarning`) | Tous liés à `utcnow()` et Starlette |
| Modules à double identité | 15 shims | Créés par le delta (voir `ARCH-01`) |

---

## DETTE-01 — `datetime.utcnow()` est déprécié et voué à disparaître (P2, CONFIRMÉ)

**Fichiers** : `app/engine/opt_persistence.py:72`, `:153`, `:208` ;
`app/ml/model_registry.py:447`, `:526`, `:607` ; `app/ml/trainer.py:417` ;
`app/strategies/opus_omnibus_v11.py:428` ; et 2 autres.

```
DeprecationWarning: datetime.datetime.utcnow() is deprecated and scheduled for
removal in a future version. Use timezone-aware objects to represent datetimes
in UTC: datetime.datetime.now(datetime.UTC).
```

Treize des quatorze avertissements de la suite de tests viennent de là.

**Scénario d'échec** — à la suppression effective dans une version future de
CPython, `app/engine/opt_persistence.py` et `app/ml/model_registry.py` lèveront
`AttributeError` **à l'écriture des métadonnées d'optimisation et de registre
de modèles** : les chemins de persistance, précisément.

Un second risque existe dès aujourd'hui : `utcnow()` renvoie un datetime
**naïf**. `app/ml/trainer.py:417` calcule
`(datetime.utcnow() - train_end_dt).total_seconds()` — si `train_end_dt` était
un jour construit comme *aware*, la soustraction lèverait `TypeError`. Le code
fonctionne parce que les deux côtés sont naïfs, par coïncidence de
construction.

**Vérification** — **CONFIRMÉ** : 10 occurrences comptées dans `app/`, et les
avertissements sont effectivement émis par la suite (visibles dans la sortie
pytest).

**Correctif proposé** — remplacer par `datetime.now(timezone.utc)`. Attention :
la nouvelle forme renvoie un datetime *aware*, donc `.isoformat()` produit
`…+00:00` au lieu de `…` — les trois sites qui concatènent un `"Z"` littéral
(`opt_persistence.py:208`, `model_registry.py:447`, `:526`, `:607`) doivent
être ajustés pour ne pas produire `+00:00Z`.

**Effort** : 2 h, dont l'essentiel en vérification du format sérialisé.

**Délégation IA** —
> Remplacer les 10 `datetime.utcnow()` de `app/` par
> `datetime.now(timezone.utc)`. Attention au format sérialisé : la nouvelle
> forme est *aware*, donc `.isoformat()` ajoute déjà `+00:00`. Les sites qui
> concatènent un `"Z"` littéral (`app/engine/opt_persistence.py:208`,
> `app/ml/model_registry.py:447`, `:526`, `:607`) produiraient `+00:00Z` :
> utiliser `.strftime("%Y-%m-%dT%H:%M:%SZ")` ou retirer le `"Z"`.
> Vérifier aussi `app/ml/trainer.py:417`, où le résultat est soustrait à
> `train_end_dt` : les deux opérandes doivent être du même type (naïf ou
> aware), sinon `TypeError`.
> Critère d'acceptation : `pytest -q` reste à 2 142 passés **et** le nombre
> de `DeprecationWarning` tombe de 13 à 0 pour `utcnow`.

---

## DETTE-02 — 347 erreurs mypy hors du périmètre CI (P2, CONFIRMÉ)

Traité en détail comme `TEST-02` dans `15-TESTS-CI.md`.

Résumé : le job CI couvre 110 fichiers sur 227. `app/live` (qui manipule le
capital), `app/ml`, `app/api` et `app/strategies` ne sont pas type-vérifiés.
`check_untyped_defs` est désactivé globalement, sauf pour six modules — le
corps de la plupart des fonctions n'est donc pas analysé (142 notes
`annotation-unchecked`).

C'est le poste de dette dominant du dépôt. **Ce n'est pas une régression du
delta**, qui a au contraire étendu le périmètre à `app/core` et `app/engine`.

**Effort** : 3 à 5 jours, à découper par paquet, en commençant par `app/live`.

---

## DETTE-03 — Double identité de module pour le risque et le SMC (P2, CONFIRMÉ)

Traité en détail comme `ARCH-01` dans `02-ARCHITECTURE.md`.

15 shims créés par le delta, 68 sites d'import sur l'ancien chemin contre 35
sur le nouveau. Dette **latente** : aucun état mutable rebindable ni
`monkeypatch` ne la rend active aujourd'hui.

**Effort** : 2 h, remplacement mécanique.

---

## DETTE-04 — Onze fichiers Python dépassent 700 lignes (P3, CONFIRMÉ)

| Fichier | Lignes |
|---|---:|
| `app/core/candle_store.py` | 1 087 |
| `app/strategies/ml_dynamic_threshold.py` | 1 048 |
| `app/engine/auto_optimizer.py` | 966 |
| `app/strategies/scoring_statistique_opus_v4.py` | 908 |
| `app/engine/optimizer_search.py` | 889 |
| `app/strategies/scoring_statistique_opus_v5.py` | 867 |
| `app/strategies/opus_omnibus_v11_followsetup.py` | 794 |
| `app/engine/backtest.py` | 780 |
| `app/api/services/scanner_service.py` | 778 |
| `app/strategies/opus_omnibus_v11.py` | 734 |
| `app/engine/position_lifecycle.py` | 707 |

Les fichiers de `app/strategies` ne sont pas un problème : une stratégie est
une unité cohérente, et sa longueur vient de sa logique métier.

Les trois à surveiller sont `candle_store.py` (1 087 lignes, dont le point
d'entrée `fetch` avec 146 arêtes), `auto_optimizer.py` (966, qui porte trois
constats de ce rapport) et `position_lifecycle.py` (707, qui en porte deux).

Le delta a montré la bonne méthode sur ce dernier : `_manage_open_position`,
qui faisait 190 lignes, a été découpé en quatre fonctions nommées. Le résultat
est nettement plus lisible — même si le découpage a déplacé la comptabilité des
frais et introduit `FIN-01` et `FIN-02`.

**Effort** : 1 à 2 jours par fichier. À ne pas lancer sans les tests
d'invariant du lot `TEST-01` : c'est exactement ce qui a manqué ici.

---

## DETTE-05 — Deux pipelines CI aux critères divergents (P3, CONFIRMÉ)

**Fichiers** : `.github/workflows/ci.yml:49`, `.gitlab-ci.yml`.

`.gitlab-ci.yml` impose `--cov-fail-under=64` sur pytest ; le workflow GitHub
ne pose aucun seuil de couverture. `.gitlab-ci.yml` se décrit lui-même comme
un miroir du premier, mais il ne l'est pas.

**Scénario d'échec** — un changement fait chuter la couverture sous 64 % :
GitHub reste vert, GitLab devient rouge. Si les deux sont branchés sur le même
dépôt, l'incohérence se lit comme une CI instable.

**Correctif** — aligner les deux, ou retirer le pipeline GitLab s'il n'est pas
utilisé.

**Effort** : 30 min.

---

## Ce qui va bien

- **3 `TODO`/`FIXME` dans 227 modules.** C'est exceptionnellement bas. Le
  dépôt ne repousse pas ses dettes dans des commentaires : il les traite ou les
  documente dans `audit/`.
- **Aucune dépendance de `app/` vers `scripts/` ou `research/`.** Vérifié
  (voir `00-METHODE-ET-PERIMETRE.md`). La séparation code de production /
  outillage de recherche est réelle.
- **Suppression assumée de scikit-learn.** `IsotonicRegression` réimplémentée
  en PAV natif, AUC par rang de Mann-Whitney. Une dépendance lourde en moins,
  sans reliquat d'import.
- **Effort de test du delta.** 16 fichiers de test Python créés ou étendus,
  6 côté frontend. La couverture progresse sur les modules qui en manquaient le
  plus (`lib/api.ts`, `hooks/use-api.ts`).
- **Frontend sans dette de typage.** `tsc --noEmit` et `eslint` sont verts sur
  34 115 lignes — un contraste net avec les 347 erreurs mypy côté Python.
