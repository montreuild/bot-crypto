# 11 — Frontend

Delta important : 60 fichiers TS/TSX touchés. Trois mouvements de fond —
l'éclatement des types, l'extraction de composants partagés
(`SymbolSearch`, `StrategyPicker`, `Tooltip`), et l'arrivée d'une vraie
couverture de tests unitaires.

**Toutes les vérifications automatiques passent :**

| Vérification | Résultat |
|---|---|
| `tsc --noEmit` | 0 erreur sur 34 115 lignes |
| `eslint .` | 0 erreur |
| `vitest run` | 190 tests / 20 fichiers, tous verts (17,6 s) |

Aucun défaut de correctness trouvé côté frontend. Les constats de ce rapport
portent sur la structure et les garde-fous ; l'accessibilité est traitée
séparément dans `12-UI-UX-ACCESSIBILITE.md`.

---

## 1. Éclatement des types — bien mené

`frontend/src/types/index.ts` passe de **1 112 à 16 lignes**, réparti en :

| Fichier | Lignes | Rôle |
|---|---:|---|
| `types/generated.ts` | 477 | Contrats dérivés de `app/api/schemas.py` |
| `types/views.ts` | 578 | Types de vue |
| `types/ui.ts` | 199 | Types purement UI |

La séparation « contrat serveur » / « type de vue » / « type UI » est la bonne
frontière : elle rend visible ce qui doit être régénéré et ce qui est libre.

**Réserve** — le garde-fou qui protège cette dérivation ne compare que les noms
d'interfaces, pas leurs champs. Un champ ajouté côté Pydantic sans
régénération passe inaperçu. C'est traité comme `API-02` dans
`10-BACKEND-API.md` : le correctif est côté test Python.

---

## 2. Couverture de tests — progrès réel

Six fichiers de test créés dans le delta :

| Fichier | Lignes |
|---|---:|
| `lib/__tests__/api.test.ts` | 131 |
| `lib/__tests__/utils.test.ts` | 125 |
| `hooks/__tests__/use-api.test.tsx` | 78 |
| `components/ui/__tests__/symbol-search.test.tsx` | 66 |
| `components/ui/__tests__/strategy-picker.test.tsx` | 46 |
| `lib/__tests__/backtest-metrics.test.ts` | 50 |

Le total passe à 190 tests. `lib/api.ts` et `hooks/use-api.ts` — les deux
fichiers les plus utilisés du frontend (`apiFetch` a 101 arêtes entrantes) —
étaient jusque-là sans test unitaire.

À noter, `0dea56a test(fe): mutationFn reçoit le contexte TanStack` : un test
qui corrige sa propre hypothèse sur la signature de TanStack Query plutôt que
d'ajuster le code. C'est la bonne façon de procéder.

---

## FE-01 — Deux composants de vue restent au-delà de 680 lignes (P3, CONFIRMÉ)

Traité en détail comme `ARCH-02` dans `02-ARCHITECTURE.md`.

`smart-replay-view.tsx` (744 lignes, sortance 237) et `backtest-results.tsx`
(681 lignes) concentrent la complexité. Le delta a retouché le second
(+179/−165) sans le découper, et a dû corriger au passage
`b555674 fix(ui): hooks avant early return dans backtest-results` — un bug de
règle des hooks, typique de ces tailles.

Le mouvement inverse est pourtant engagé ailleurs :
`use-smart-graph-chart.ts` perd 63 lignes au profit de
`smart-graph-helpers.ts` (+15, testé). C'est le modèle à reproduire.

**Effort** : 4 h par composant.

---

## FE-02 — La reconnexion WebSocket redemande un jeton à chaque tentative (P3, CONFIRMÉ)

**Fichier** : `frontend/src/lib/ws-provider.tsx:92-103`.

```typescript
void (async () => {
  const ticket = await fetchWsTicket();
  …
  const ws = new WebSocket(wsUrlWithTicket(WS_URL, ticket));
```

`connect()` fait un `POST /api/ws/ticket` avant chaque tentative, y compris à
chaque essai de reconnexion automatique après `onclose`.

C'est **fonctionnellement correct** — un jeton est à usage unique et vit 30 s,
il en faut donc bien un neuf à chaque handshake. Le coût est un aller-retour
REST supplémentaire par tentative.

**Scénario** — backend indisponible : chaque cycle de reconnexion fait un POST
qui échoue, puis une tentative WebSocket qui échoue. Le compteur
`reconnectAttempts` applique bien un recul progressif, donc il n'y a pas
d'emballement. Pas de sortie fausse.

**Vérification** — lecture du code ; `fetchWsTicket` renvoie `null` en cas
d'échec (`ws-provider.tsx:36-39`) et `wsUrlWithTicket` retombe alors sur l'URL
nue (`:46`), qui sera refusée en 4403. Le comportement dégradé est propre.

Signalé pour mémoire : si le nombre de connexions simultanées augmente, ce
POST devient un point de charge à surveiller.

---

## FE-03 — Le simulateur de coûts et le panneau de validation ont été refondus (CONFIRMÉ — amélioration)

**Fichiers** : `components/cards/cost-simulator-panel.tsx` (+48/−70),
`components/cards/optimizer-validate-panel.tsx` (+62/−70),
`components/optimizer/optimizer-config-form.tsx` (+87/−124).

Les trois perdent des lignes en gagnant des fonctionnalités — signe d'une
refonte, pas d'un empilement. `bb94993` corrige dans la foulée le typage de
`OptimizeJob.result.initial_capital`, que le panneau de validation consommait.

Aucun défaut relevé.

---

## Ce qui a été vérifié sans rien trouver

- **Formatage fr-FR** — `lib/utils.ts` (+77/−11) centralise le formatage des
  nombres et des devises (`1897580 fix(ui): UX-02`). Couvert par
  `lib/__tests__/utils.test.ts` (125 lignes, nouveau).
- **`use-api.ts`** — 753 lignes, le plus gros fichier du frontend, mais c'est
  une collection de hooks indépendants : la taille n'y traduit pas une
  complexité couplée. Désormais testé.
- **`chart-fullscreen.tsx`** — corrigé dans le delta (+13/−16) pour le mode
  plein écran de la courbe d'équité. `tsc` et `vitest` verts.
- **Composants partagés** — `SymbolSearch` (273 lignes) et `StrategyPicker`
  (113) sont maintenant utilisés partout, sauf `Optimizer Symbols` par choix
  explicite (`b9973b6`). L'unification supprime une source de divergence de
  comportement entre onglets.
- **Tests e2e et visuels** — trois jobs CI distincts existent et sont câblés :
  `e2e` (smoke tests de chargement), `a11y` (axe sur 20 pages), `visual`
  (`visual.spec.ts`, retouché dans le delta). Le filet est complet ; sa limite
  est traitée dans `12-UI-UX-ACCESSIBILITE.md`.
