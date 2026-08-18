# 11 — UI / UX et accessibilité

Périmètre : `frontend/src/app`, `frontend/src/components`, `frontend/e2e`.

**Jugement d'ensemble.** L'ergonomie est traitée avec une rigueur qu'on ne voit pas
souvent : l'interface distingue explicitement « pas de valeur », « valeur non
significative » et « valeur infinie » (`MetricValue`), les tests d'accessibilité couvrent
chaque onglet Radix séparément parce que Radix ne monte que l'onglet actif, et le contraste
du thème clair a été porté à WCAG AA dans la fenêtre auditée. Le défaut principal est
d'un tout autre genre : **l'interface affiche des dollars pour des positions libellées en
euros**.

---

## UX-01 — Tous les montants sont affichés en dollars, quelle que soit la devise réelle

**Sévérité P1 · CONFIRMÉ (lecture)**

`frontend/src/lib/utils.ts:21-29` :

```typescript
export function formatUSD(value: number, opts = {}): string {
  const formatter = new Intl.NumberFormat('en-US', {
    style: 'currency', currency: 'USD',       // ← en dur
    ...
  });
  return formatter.format(value);
}
```

C'est **la seule** fonction de formatage monétaire du frontend (`grep currency` sur
`src/lib` et `src/components/ui` : une seule occurrence).

Or le backend a une devise **par venue**, et le sait :

- `Venue.quote_currency` (`app/core/bot_identity.py`), défaut `"USDC"` ;
- `Envelope.currency` (`app/core/risk_envelope.py:23`) ;
- `cost_model()["quote_currency"]` (`app/core/execution.py:401`), transmis dans **chaque**
  résultat de backtest (`backtest_result.py:446`).

L'information voyage jusqu'au client. Elle n'est simplement jamais lue.

**Conséquence** : les venues actions ajoutées par G2 — SBF 120, `.PA`, `.AS`, avec taxe de
transaction française et calendrier XPAR — cotent en **euros**. Le portefeuille, le PnL,
les frais et les enveloppes de risque de ces positions s'affichent avec un `$`. Un
utilisateur qui lit `$1,234.56` sur une position BNP.PA lit une valeur exacte avec une
unité fausse. Sur une interface qui sert à décider d'engager de l'argent, l'unité fait
partie du chiffre.

Le nom de la fonction, `formatUSD`, aggrave le cas : il rend le défaut invisible à la
relecture — un appel à `formatUSD(pnl)` a l'air correct partout.

**Correction** :

```typescript
export function formatMoney(value: number, currency = 'USDC', opts = {}): string
```

en propageant `cost_model.quote_currency` (backtest) et `envelope.currency` (portefeuille,
risque) jusqu'aux composants d'affichage. Conserver `formatUSD` comme alias déprécié le
temps de la migration.

**Effort** : ~1 journée (la valeur est déjà dans les payloads ; c'est du câblage).

---

## UX-02 — Interface en français, nombres au format anglo-saxon

**Sévérité P2 · CONFIRMÉ (lecture)**

`src/app/layout.tsx:73` : `<html lang="fr">`. Les quatre fonctions de formatage
(`src/lib/utils.ts`) utilisent `Intl.NumberFormat('en-US')`.

Résultat affiché dans une interface entièrement francophone :

| Affiché | Attendu en `fr-FR` |
|---|---|
| `1,234.56` | `1 234,56` |
| `$1,234.56` | `1 234,56 $` |
| `12.5%` | `12,5 %` |

Ce n'est pas cosmétique sur des chiffres financiers : un lecteur francophone lit
`1,234` comme « un virgule deux trois quatre ». Sur une taille de position ou un
drawdown, l'ambiguïté est réelle — et elle porte sur trois ordres de grandeur.

`lang="fr"` déclare aussi la langue aux lecteurs d'écran, qui énonceront donc des nombres
au format anglais avec une prononciation française.

**Correction** : `Intl.NumberFormat('fr-FR', …)` dans les quatre fonctions, et
`toLocaleString('fr-FR')` pour les dates. Les baselines visuelles Playwright
(`e2e/tests/visual.spec.ts-snapshots`) seront à régénérer. ~2 h.

---

## UX-03 — La vue qui écrit la configuration de trading ne signale aucune erreur

**Sévérité P2 · CONFIRMÉ (recensement) — voir FE-02**

`components/views/config-view.tsx` (340 lignes) : zéro occurrence de `isLoading`,
`isPending`, `isError` ou `error`.

Cette vue pilote `POST /api/config/trading`, `/api/config/risk`,
`/api/config/strategy-params`. Un échec — 403 (clé expirée), 422 (valeur refusée par le
schéma), 500 — ne produit **aucun retour visible**. L'utilisateur repart en croyant avoir
modifié un paramètre de risque.

C'est le pire endroit possible pour un échec silencieux : les paramètres écrits ici
gouvernent le sizing et l'exécution. Le décalage entre l'état affiché et l'état réel ne
se manifestera qu'au trade suivant.

`sonner` est déjà en dépendance et utilisé ailleurs. ~2 h.

---

## UX-04 — Les états d'erreur des vues de calcul long ne distinguent pas « en cours » de « bloqué »

**Sévérité P2 · PLAUSIBLE (lecture)**

`compare-view.tsx` : 17 occurrences de gestion d'erreur, **zéro** état de chargement.
`backtest-results.tsx` : 1 erreur, zéro chargement.

Ces deux vues affichent le résultat d'opérations qui prennent des dizaines de secondes à
plusieurs minutes (backtests, comparaisons multi-stratégies). Sans indicateur, un écran
vide pendant 90 secondes est indiscernable d'un écran cassé — et l'utilisateur relance,
ce qui empile les jobs côté serveur.

Le backend fournit pourtant ce qu'il faut : `/api/backtest/status`,
`/api/optimize/stream` (SSE), et un composant `live-progress.tsx` a été créé dans la
fenêtre auditée pour l'optimiseur. Le motif existe, il n'est pas généralisé.

---

## Ce qui a été vérifié et tenu

- **`MetricValue`** (`components/ui/metric-value.tsx`) — trois états distincts pour une
  métrique :
  - `null` / `NaN` → `—` ;
  - moins de `minObs` observations → `n/a` avec l'infobulle « *n observations — non
    significatif* » ;
  - valeur ≥ sentinelle → `∞` avec « *aucune perte / drawdown mesurable* ».

  C'est le pendant exact, côté interface, du travail fait côté backend (`sharpe = None`
  sous 10 trades, `profit_factor = None` sans perte). Le contrat « non mesurable ≠ nul »
  traverse toute la pile. C'est rare et ça mérite d'être dit.
- **182 occurrences** du placeholder `—` dans les composants : l'absence de valeur est
  traitée systématiquement, pas au cas par cas.
- **Accessibilité automatisée** — `e2e/tests/a11y.spec.ts` avec `@axe-core/playwright`,
  échec sur violation `critical`/`serious`/`moderate`, WCAG 2.1 AA.
- **Couverture a11y par onglet** — le fichier ouvre `/market?tab=scanner`,
  `?tab=smartgraph`, `?tab=smartreplay`, `?tab=derivatives` séparément, avec la raison :
  *« Radix ne monte QUE l'onglet actif : auditer /market seul ne couvrirait que le premier
  onglet »*. C'est exactement le piège dans lequel tombent la plupart des suites a11y.
- **Les pages issues de la refonte ont été explicitement ajoutées** à la liste auditée,
  avec le motif : *« elles concentrent l'essentiel du nouveau code (kanban, drawer, tabs,
  donut, dialogues) : ce sont précisément celles dont l'accessibilité n'avait jamais été
  vérifiée »*. Le raisonnement est le bon.
- **Contraste WCAG AA du thème clair** corrigé dans la fenêtre auditée (`2fa8052`), avec
  régénération des baselines visuelles Linux (`74f056e`).
- **155 attributs `aria-*` / `role`** dans les composants, en plus de la sémantique Radix.
- **Tests visuels de non-régression** — `visual.spec.ts` avec instantanés versionnés.

---

## Récapitulatif

| ID | Sévérité | Preuve | Constat | Effort |
|---|---|---|---|---|
| UX-01 | **P1** | CONFIRMÉ | Tout est affiché en `$` malgré `quote_currency` par venue | 1 j |
| UX-02 | P2 | CONFIRMÉ | `lang="fr"` mais nombres en `en-US` | 2 h |
| UX-03 | P2 | CONFIRMÉ | La vue de configuration n'affiche aucune erreur | 2 h |
| UX-04 | P2 | PLAUSIBLE | Pas d'indicateur sur les vues de calcul long | 4 h |
