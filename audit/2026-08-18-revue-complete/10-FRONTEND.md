# 10 — Frontend (Next.js 15 / React 19 / TypeScript)

Périmètre : `frontend/src` — 162 fichiers, ~32 700 lignes TS/TSX.
Stack : Next.js 15.5.22, React 19, TanStack Query 5 + Table 8, lightweight-charts 4,
Recharts 2, Radix UI, Tailwind 3, Zod 3, Vitest 2, Playwright + axe-core.

**Jugement d'ensemble.** Le code TypeScript est propre : `tsc --noEmit` passe **sans une
seule erreur** sur 32 700 lignes, et le recours aux échappatoires est faible — 12 `as any`
et 50 occurrences tous motifs confondus (`any`, `@ts-ignore`, `eslint-disable`), soit
1 pour 650 lignes. C'est bien tenu. Le problème n'est pas la qualité du code écrit, c'est
**ce qui n'est pas testé** : 4,84 % de couverture, avec la totalité des vues, des hooks et
de la couche d'accès API à zéro.

---

## FE-01 — 4,84 % de couverture, et rien de ce qui porte la logique n'est couvert

**Sévérité P1 · CONFIRMÉ (mesure)**

`npx vitest run --coverage` sur le worktree :

```
Test Files  10 passed (10)
Tests      126 passed (126)
All files   |  4.84 % stmts  |  57.56 % branch  |  20.22 % funcs  |  4.84 % lines
 components |     0 %        |      0 %         |     0 %         |     0 %
```

Les seuls fichiers couverts :

| Fichier | Lignes |
|---|---:|
| `ui/badge.tsx` | 100 % |
| `ui/button.tsx` | 100 % |
| `ui/card.tsx` | 100 % |
| `ui/data-table.tsx` | 97,7 % |
| `lib/schemas.ts` | 100 % |
| `lib/utils.ts` | 24 % |

Tout le reste est à **0 %**, y compris :

| Fichier | Lignes | Rôle |
|---|---:|---|
| `hooks/use-api.ts` | 745 | **toute** la couche d'accès aux données |
| `lib/api.ts` | 650 | client HTTP, gestion d'erreurs, authentification |
| `components/views/*.tsx` | ~6 000 | les 14 vues métier |
| `hooks/use-smart-graph-chart.ts` | 629 | logique de graphe |

L'asymétrie avec le backend est frappante et **institutionnalisée dans la CI** :

```yaml
# .gitlab-ci.yml
test:      pytest … --cov=app --cov-fail-under=25     ← seuil imposé
frontend:  npm run lint && npm run type-check && npm test && npm run build   ← aucun seuil
```

Le backend a un plancher de couverture ; le frontend n'en a pas. `npm test` passe avec
126 tests sur des boutons.

**Ce que ça coûte concrètement** : `use-api.ts` et `lib/api.ts` transforment les réponses
du serveur avant affichage. Une erreur de transformation — un champ renommé, un `null`
traité comme `0`, une unité en pourcentage prise pour une fraction — produit un chiffre
faux dans une interface de trading, et **aucun test ne peut l'attraper**. Le seul filet
est Zod (`lib/schemas.ts`, 139 validateurs, couvert à 100 %), qui vérifie la forme et non
la sémantique.

**Correction, par ordre de rendement** :
1. `lib/api.ts` + `hooks/use-api.ts` avec MSW (mock du réseau) — c'est là que vit le
   risque de justesse, et c'est testable sans DOM.
2. Un seuil `--coverage.thresholds.lines=30` dans la CI frontend, monté par paliers.
3. Les 4 vues sans état d'erreur (FE-02) une fois celles-ci corrigées.

**Effort** : 3 à 4 jours pour atteindre 30 % avec les bons fichiers.

---

## FE-02 — Quatre vues sur quatorze n'ont ni état de chargement ni état d'erreur

**Sévérité P2 · CONFIRMÉ (recensement)**

Occurrences de `isLoading`/`isPending` et de `isError`/`error` par vue :

| Vue | chargement | erreur |
|---|---:|---:|
| `config-view.tsx` | **0** | **0** |
| `smart-graph-tables.tsx` | **0** | **0** |
| `backtest-results.tsx` | **0** | 1 |
| `compare-view.tsx` | **0** | 17 |
| `scanner-view.tsx` | 1 | 3 |
| `optimizer-view.tsx` | 2 | 2 |
| `ml-view.tsx` | 2 | 2 |
| *(les 7 autres)* | 3 à 12 | 4 à 17 |

`config-view.tsx` (340 lignes) est celle qui compte le plus : c'est la vue qui **écrit la
configuration de trading**. Sans état d'erreur, un `POST /api/config/*` qui échoue — 403
sur clé API expirée, 422 sur valeur hors domaine, 500 — ne produit aucun retour visible.
L'utilisateur croit avoir enregistré un paramètre de risque qui ne l'est pas.

`compare-view.tsx` est le cas inverse et instructif : 17 occurrences de gestion d'erreur,
zéro état de chargement. Sur une vue de comparaison qui déclenche plusieurs backtests, un
écran figé sans indicateur est indiscernable d'un écran cassé.

**Correction** : `sonner` (déjà en dépendance) pour les erreurs de mutation, et le
squelette de chargement déjà utilisé par les autres vues. ~2 h par vue.

---

## FE-03 — 1 462 lignes de types écrites à la main contre une API sans contrat

**Sévérité P1 · Dérivé d'API-01**

`src/types/index.ts` (1 462 lignes) et `src/lib/schemas.ts` (585 lignes de Zod) décrivent
la forme des réponses du serveur. Côté serveur, **zéro `response_model`** (cf. API-01).
Le contrat n'existe donc qu'en un seul exemplaire, du mauvais côté du réseau, maintenu à
la main.

La fenêtre auditée en donne la démonstration : quatre commits successifs des 17 et 18/08
(`9df222f`, `9913bd2`, `601f43f`, `e85ccf3` — « *types FastAnalyse (kind, n)* »,
« *U-05 types restants* », « *U-05 contrats restants* ») ne font que **réaligner à la main
des types qui avaient dérivé**. Ce travail se répétera à chaque évolution du backend.

Zod est le bon choix pour la validation au bord — mais il valide contre une définition
qui a été *recopiée*, pas *dérivée*. Générer les types depuis l'OpenAPI transforme ces
2 000 lignes en artefact de build et fait échouer la compilation, plutôt que l'exécution,
quand le serveur change.

---

## FE-04 — Six fichiers utilisent des hooks sans directive `'use client'`

**Sévérité P3 · CONFIRMÉ (recensement)**

```
components/cards/backtest-progress.tsx
components/tables/trades-table.tsx
components/ui/data-table.tsx
hooks/use-backtest-session.ts
hooks/use-replay-engine.ts
hooks/use-replay-keyboard.ts
```

Ce n'est **pas un bug aujourd'hui** : dans l'App Router, la directive n'est requise qu'à
la frontière, et ces modules sont importés depuis des composants clients qui la portent.
La compilation passe, l'application fonctionne.

C'est une fragilité : le jour où l'un d'eux est importé depuis un composant serveur — ce
que rien n'empêche — l'erreur survient au build avec un message peu explicite. Poser la
directive sur tout module qui appelle un hook rend la contrainte locale et vérifiable.

---

## Ce qui a été vérifié et tenu

- **`tsc --noEmit` : 0 erreur** sur 32 700 lignes. Le typage tient.
- **Recours minimal aux échappatoires** — 12 `as any`, 50 occurrences toutes catégories
  confondues. Pour un frontend de cette taille branché sur une API non typée, c'est
  remarquablement discipliné.
- **Validation au bord avec Zod** — 139 validateurs dans `lib/schemas.ts`, testés à 100 %.
  L'API n'a pas de contrat, mais le client refuse au moins les payloads malformés.
- **Authentification côté serveur Next** — la clé API est posée en cookie
  `HttpOnly; SameSite=Lax; Secure` par le proxy de route
  (`src/app/api/[...path]/route.ts:111`), donc invisible au JavaScript de la page et
  protégée contre le CSRF inter-site. C'est la bonne construction, et le commentaire
  l'explique correctement.
- **Découpage récent** — la fenêtre auditée a séparé `backtest-view` / `backtest-results`,
  `smart-graph-view` / `smart-graph-tables` / `smart-graph-helpers`, et extrait quatre
  composants d'optimiseur. Le plus gros fichier restant est `types/index.ts`, un fichier
  de déclarations — c'est le bon endroit pour de la taille.
- **`vitest` : 126 tests, 10 fichiers, tous passés.** Les tests qui existent sont bons ;
  ils ne sont simplement pas là où le risque est.

---

## Récapitulatif

| ID | Sévérité | Preuve | Constat | Effort |
|---|---|---|---|---|
| FE-01 | **P1** | CONFIRMÉ | 4,84 % de couverture, API et vues à 0 % | 3-4 j |
| FE-03 | **P1** | Dérivé | 2 000 lignes de contrat recopié à la main | avec API-01 |
| FE-02 | P2 | CONFIRMÉ | 4 vues sans état d'erreur, dont celle qui écrit la config | 1 j |
| FE-04 | P3 | CONFIRMÉ | Hooks sans `'use client'` — fragilité de build | 15 min |
