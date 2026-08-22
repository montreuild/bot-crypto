# 99 — Registre

Audit du **2026-08-22** sur `d77e9f3` — Laboratoire : entraînement ML,
optimiseur, backtest.

**10 constats** : 0 P0, 3 P1, 5 P2, 2 P3. **10 CONFIRMÉS par exécution**,
0 plausible. **4 livrés** — toute la cause A.

> Détail et preuves : [`02-CONSTATS.md`](02-CONSTATS.md).
> Causes et solutions globales : [`01-SYNTHESE.md`](01-SYNTHESE.md).

---

## Répartition

| Sévérité | Nombre | Dont reproduits |
|---|---:|---:|
| **P0** — bloquant | 0 | — |
| **P1** — majeur | 3 | 3 |
| **P2** — significatif | 5 | 5 |
| **P3** — mineur | 2 | 2 |

Par cause : **A** contrat typé (4) · **B** décision serveur non remontée (2) ·
**C** vocabulaire (3) · hors cause (2). `LAB-02` relève de A et de C.

---

## Constats

| ID | Sév. | Constat | Fichier | Cause | Statut |
|---|---|---|---|---|---|
| `LAB-01` | **P1** | `/api/candles/stats` rend une liste, le front la type en objet imbriqué : 5 160 lignes de rebut pour 645 datasets | `frontend/src/types/index.ts:242` | A | **livré** |
| `LAB-02` | **P1** | Entraînement poolé inatteignable : `symbols` en chaîne (422) puis `strategy=` au lieu de `recipe=` (400) | `train-recipe-dialog.tsx:181,190` | A+C | **livré** |
| `LAB-04` | **P1** | Presets annonçant 60 essais / ~10 min ; effectif médian 135, max 400, 40/41 stratégies au-dessus | `optimizer/status.ts:6-8` | B | ouvert |
| `LAB-03` | P2 | `startMLTrain` déclare 7 paramètres sur 12 ; le dialogue contourne le type par un `any` | `frontend/src/lib/api.ts:527` | A | **livré** |
| `LAB-05` | P2 | Budget reproportionné, arrêt anticipé et essais en échec tronquent tous le compteur — aucun n'est distingué | `optimizer_search.py:653` | B | ouvert |
| `LAB-07` | P2 | Les 10 recettes et les 14 stratégies n'ont aucun nom commun, affichées l'une sous l'autre sans lien | `ml-view.tsx` | C | ouvert |
| `LAB-08` | P2 | « Prochain retrain » affiche `-154352s` : `timeAgo` calcule un passé | `frontend/src/lib/utils.ts:131` | — | ouvert |
| `LAB-09` | P2 | `completeness` et `gaps` reçus par dataset, jamais affichés ; « Première »/« Dernière » alimentées par `from`/`to` toujours `null` | `ml-view.tsx:99` | A | **livré** |
| `LAB-06` | P3 | « 1/14 entraînés » (axe stratégie) juxtaposé à 3 entrées de registre (axe tf × recette) | `ml-view.tsx` | C | ouvert |
| `LAB-10` | P3 | `filterMl` transmis à deux enfants sur trois : « Optimiseur ML » liste les espaces des 41 stratégies | `optimizer-view.tsx:55` | — | ouvert |

---

## Ce qui a été vérifié et ne pose pas de problème

Consigné parce qu'un audit qui ne dit que ce qui ne va pas laisse croire que
tout le reste a été vu.

- **Aucune route morte.** Les 32 routes `/api/ml`, `/api/optimize`,
  `/api/backtest` sont toutes appelées par le front ; aucun appel ne vise une
  route inexistante.
- **Le backtest expose ses 12 paramètres**, `cost_override`, `realistic_risk`,
  `dual_pass`, `walk_forward` et `monte_carlo` compris.
- **L'optimiseur expose ses 12 paramètres.** Son formulaire est complet ; ce
  qui manque, c'est le retour du serveur, pas l'aller.
- **Le SSE de progression est branché** (`live-progress.tsx:36`) et
  fonctionnel — il lui manque des champs, pas un mécanisme.
- **Le registre de modèles est cohérent** avec ce que le conteneur voit sur
  disque : 3 couples `(tf, recette)` pour 6 artefacts. L'écart avec les 37
  couples de la machine hôte vient du volume monté, pas du code — vérifié dans
  le conteneur avant d'écrire quoi que ce soit.
- **Le contrat `generated.ts` tient** pour les 44 interfaces qu'il couvre, et
  son test compare le fichier entier. Le problème n'est pas qu'il dérive :
  c'est qu'il ne couvre pas ces trois formulaires.
