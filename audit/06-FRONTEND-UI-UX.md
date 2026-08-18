# Audit — Frontend, UI et UX

> Périmètre : `frontend/src/` — 122 composants `.tsx` + 31 modules `.ts`,
> ≈ 33 000 lignes. Next.js 15.5.22 / React 19 / TanStack Query 5 / Radix /
> Tailwind / Recharts + lightweight-charts / Zod.

---

## Tableau de bord

| # | Sévérité | Titre | Fichier |
|---|----------|-------|---------|
| U-01 | 🟠 Majeur | Le système i18n n'est branché nulle part | `lib/i18n.tsx` |
| U-02 | 🟠 Majeur | L'UI présente comme des mesures des valeurs sentinelles et des Sharpe absurdes | `cards/kpi-cards.tsx`, `views/optimizer-view.tsx` |
| U-03 | 🟠 Majeur | Sondage permanent : ≈ 40 requêtes/min par onglet, en plus du WebSocket | `hooks/use-api.ts` |
| U-04 | 🟠 Majeur | 98 composants sur 122 sont `'use client'` — le SSR ne sert à rien | ✅ atténué — `dynamic()` lab + portfolio |
| U-05 | 🟡 Moyen | 212 usages de `any` / `as any` : les types du backend ne protègent rien | 40 fichiers |
| U-06 | 🟡 Moyen | ~39 champs de saisie sans étiquette associée | ✅ atténué — lab/settings ont Label/aria-label |
| U-07 | 🟡 Moyen | `dangerouslySetInnerHTML` sur du texte venant du backend | `cards/optimizer-warnings.tsx:50` |
| U-08 | 🟡 Moyen | Quatre composants de plus de 700 lignes, jusqu'à 1 558 | `views/optimizer-view.tsx` |
| U-09 | 🟡 Moyen | `key={i}` sur des listes qui changent d'ordre | ✅ atténué — clés stables sur listes métier |
| U-10 | 🟡 Moyen | Le mode expert a deux sources de vérité qui peuvent diverger | `app/lab/page.tsx:139-145` |
| U-11 | 🔵 Mineur | `lang="fr"` figé sur `<html>` | `app/layout.tsx` |
| U-12 | 🔵 Mineur | Le nom des onglets vit dans l'URL sans être validé | `app/lab/page.tsx`, `app/market/page.tsx` |

> **18/08** — libellés honnêtes, pas une refonte UI : walk-forward annoncé
> comme **stabilité** (`kind`, `avg_fold_pnl`) ; optimiseur affiche `val_*`
> et `gate_source` (holdout vs sélection). U-02 atténué côté backend
> (F-02 / F-10 : plus de Sharpe ±1000 ni sentinelles 999).
> Voir [`14-REVISION-2026-08-18.md`](14-REVISION-2026-08-18.md).

---

## U-01 🟠 L'internationalisation est du code mort

`frontend/src/lib/i18n.tsx` (97 lignes) fournit :

- un type `Locale = 'fr' | 'en'` ;
- deux dictionnaires complets (FR et EN) ;
- une persistance `localStorage` (`crypto-bot-locale`) ;
- une détection automatique via `navigator.language` ;
- un `I18nProvider` et un hook `useI18n` avec repli sans contexte.

Recensement des consommateurs sur tout `frontend/src` :

```
$ grep -rln "useI18n" --include=*.tsx frontend/src
frontend/src/lib/i18n.tsx
```

**Un seul fichier : lui-même.** `I18nProvider` n'est pas monté dans
`components/providers.tsx`, aucun composant n'appelle `t()`, et toutes les
chaînes sont écrites en français en dur dans le JSX
(`'Plage de dates invalide'`, `'Backtest terminé'`, `'Win rate'`…).

Le dictionnaire contient pourtant `'settings.language': 'Langue'`, ce qui
indique qu'un sélecteur de langue était prévu dans les réglages — il n'existe
pas non plus.

**Décision à prendre** : soit supprimer le module (et le mentionner dans le
changelog pour éviter qu'un contributeur le croie fonctionnel), soit le brancher.
La seconde option représente environ 400 chaînes à extraire ; ce n'est pas une
tâche de fin de sprint. En l'état, le module donne l'impression fausse que
l'application est multilingue.

---

## U-02 🟠 L'UI affiche des nombres qui ne veulent rien dire

Le frontend rend fidèlement ce que le backend lui envoie. Le problème est que
ce qu'il envoie comporte, sans marqueur distinctif :

| Valeur | Source | Ce que l'utilisateur lit |
|---|---|---|
| `sharpe: 1014.763` | `backtest_history.json` (2 trades) | « Sharpe 1014,76 » |
| `sharpe: -215.816` | run du 2026-08-07 (6 trades) | « Sharpe -215,82 » |
| `profit_factor: 999` | sentinelle « aucune perte » | « PF 999 » |
| `sortino: 100.0` | sentinelle « aucun rendement négatif » | « Sortino 100 » |
| `calmar: 100.0` | sentinelle « drawdown nul » | « Calmar 100 » |
| `max_dd_p95_pct: 0.77` | percentile inversé (F-03) | « pire drawdown à 95 % : 0,77 % » alors que le drawdown observé est 1,76 % |
| `return_p5 == return_p95` | Monte-Carlo dégénéré (F-03) | un cône de largeur nulle avec « probabilité de profit : 100 % » |

Sur 158 runs de l'historique, **104 affichent un Sharpe supérieur à 10**. Un
tableau de bord qui affiche un Sharpe de 1 014 ne trompe personne longtemps —
mais il érode la confiance dans *tous* les chiffres affichés, y compris les
bons.

La responsabilité est backend (cf. F-02, F-03, F-10), mais le frontend peut et
doit se défendre :

```tsx
// components/ui/metric.tsx
export function MetricValue({ value, nObs, minObs = 10, format }: Props) {
  if (value == null) return <span className="text-dim">—</span>;
  if (nObs != null && nObs < minObs)
    return <Tooltip content={`${nObs} observations — non significatif`}>
             <span className="text-dim">n/a</span>
           </Tooltip>;
  if (value >= 999) return <span title="aucune perte enregistrée">∞</span>;
  return <span>{format(value)}</span>;
}
```

Le composant `cards/significant-bots-table.tsx` montre que la notion de
significativité existe déjà côté UI ; il faut la généraliser aux cartes de KPI.

---

## U-03 🟠 Le sondage sature le backend

`hooks/use-api.ts` déclare des `refetchInterval` sur presque toutes les
requêtes :

| Hook | Intervalle | Req/min |
|---|---|---|
| `useBotStatus` | 3 s | 20 |
| `usePortfolio` | 5 s | 12 |
| `useHealth` | 10 s | 6 |
| `useBots` | 10 s | 6 |
| `useTrades` | 15 s | 4 |
| `useDailyStats`, `useFeesBreakdown` | 60 s | 2 |
| autres (`ml-recipes-list`, `opportunities-widget`, …) | 60–180 s | ~2 |

Soit **≈ 50 requêtes par minute et par onglet**, avant même de compter les
sondages spécifiques aux pages ouvertes.

Trois conséquences :

1. **Le seau de rate limiting** est de 300 req/min pour *tout le monde* derrière
   un proxy (cf. A-06) : six onglets suffisent à provoquer des 429 généralisés.
2. **Chaque `/api/status` déclenche `_serialize_position`**, qui appelle
   `_safe_ticker` par position — donc un appel réseau exchange par position
   toutes les 3 secondes (cf. A-04).
3. **Un WebSocket existe déjà** (`lib/ws-provider.tsx`, `app/api/routes/ws.py`)
   et publie `trade_opened` / `trade_closed`. Le commentaire de `useBotStatus`
   dit « temps réel via polling, complété par WS » — c'est l'inverse de ce qu'il
   faudrait : le WS devrait être la source et le sondage le filet.

**Corrections** :

- porter `refetchInterval` de `useBotStatus` à 15–30 s et invalider la clé
  `['status']` sur événement WS ;
- ajouter `refetchIntervalInBackground: false` (défaut de TanStack Query, mais
  à confirmer) et `enabled: !document.hidden` — un onglet en arrière-plan
  n'a pas besoin de sonder ;
- retirer l'appel exchange de `_serialize_position` côté backend.

---

## U-04 🟠 Presque tout est client-side

**98 fichiers sur 122** portent la directive `'use client'`. Les pages elles-mêmes
(`app/lab/page.tsx`, `app/bots/page.tsx`, `app/portfolio/page.tsx`…) sont des
composants clients.

Conséquences :

- le rendu serveur de Next.js ne produit qu'une coquille ; tout le contenu
  arrive après hydratation et après le premier aller-retour API ;
- le bundle JS embarque Recharts **et** lightweight-charts **et** framer-motion
  **et** l'intégralité des vues, y compris `optimizer-view.tsx` (1 558 lignes)
  chargé même quand l'utilisateur reste sur `/portfolio` ;
- aucun `next/dynamic` n'est utilisé pour scinder les vues lourdes (vérifié).

Le choix est défendable pour une application de trading temps réel : tout est
dynamique, rien n'est cachable. Mais il faut alors en tirer les conséquences —
découpage de bundle par route, chargement paresseux des bibliothèques de
graphiques — plutôt que de payer le coût sans le bénéfice.

**Correction à faible coût** :

```tsx
const OptimizerView = dynamic(() => import('@/components/views/optimizer-view'),
                              { loading: () => <Skeleton /> });
```

sur les quatre vues > 700 lignes, plus `lightweight-charts` et `recharts` en
import dynamique dans leurs composants respectifs.

---

## U-05 🟡 212 échappatoires de typage

```
$ grep -rn ": any\b|as any|@ts-ignore|@ts-expect-error" frontend/src | wc -l
212
```

répartis sur une quarantaine de fichiers, dont les pages principales :

```
app/bots/page.tsx:44   String((bot as any).timeframe || (bot as any).tf || '')
app/bots/page.tsx:60   (e as any).mean_pct ?? (e as any).avg_return_pct
app/data/page.tsx:36   function flattenDatasets(data: any): DatasetRow[]
app/lab/page.tsx:844   function Verdict({ result }: { result: any })
```

Le motif `(bot as any).timeframe || (bot as any).tf` est révélateur : il traduit
une **incertitude sur le contrat backend** (`timeframe` ou `tf` ?), résolue à
l'exécution plutôt que dans les types. `types/index.ts` fait 1 015 lignes et
`lib/schemas.ts` 581 lignes de schémas Zod — l'investissement de typage existe,
mais il est court-circuité aux endroits qui comptent.

Point positif : `lib/api.ts` valide les réponses avec Zod **sans bloquer** —
un écart est journalisé et la donnée brute passe. C'est le bon arbitrage pour
un tableau de bord. Il faudrait toutefois que ces écarts soient **visibles**
(compteur dans le bandeau de statut) et non seulement en console.

**Correction** : générer les types depuis l'OpenAPI de FastAPI
(`/api/openapi.json` est exposé hors production) avec `openapi-typescript`.
Cela supprime la classe entière de divergences `tf`/`timeframe`.

---

## U-06 🟡 Champs de saisie sans étiquette

```
<input …>   : 61
<Label …>   : 25
htmlFor=    : 22
aria-*      : 134
```

Environ **39 champs** n'ont donc ni `<Label htmlFor>` ni `aria-label` explicite.
Un lecteur d'écran annonce « champ de saisie » sans dire de quoi il s'agit ; la
saisie au clavier ne bénéficie pas non plus de l'agrandissement de la zone
cliquable qu'apporte un `<label>` associé.

Points positifs à porter au crédit du dépôt :

- **aucun `<img>`** dans tout le frontend (donc aucun problème d'`alt`) — les
  icônes passent par `lucide-react`, qui est correctement traité par les
  lecteurs d'écran quand il est en `aria-hidden` ;
- 134 attributs `aria-*` : l'accessibilité n'est pas ignorée ;
- `ErrorState` porte `role="alert"` (`ui/query-state.tsx`) — le bon rôle au bon
  endroit ;
- **une suite Playwright d'accessibilité existe** (`e2e/tests/a11y.spec.ts`
  avec `@axe-core/playwright`, script `npm run test:a11y`).

Il faut donc surtout **faire tourner** cette suite en CI et traiter ses
retours : l'outillage est déjà là.

---

## U-07 🟡 `dangerouslySetInnerHTML` sur du texte serveur

`components/cards/optimizer-warnings.tsx:50` :

```tsx
dangerouslySetInnerHTML={{
  __html: w.text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
}}
```

Les avertissements sont construits localement dans ce même fichier, à partir de
valeurs numériques (`oosScore`) — le risque immédiat est donc nul.

Mais le motif est fragile : si un jour un avertissement inclut le nom d'une
stratégie, d'un symbole ou un message d'erreur du backend, l'injection devient
possible. Un nom de stratégie provient d'un fichier YAML éditable via
`/api/config/strategies`.

**Correction** : remplacer par un rendu de fragments.

```tsx
{w.text.split(/\*\*(.+?)\*\*/).map((part, i) =>
  i % 2 ? <strong key={i}>{part}</strong> : part)}
```

Le second usage (`app/layout.tsx`, script de thème inline) est légitime : c'est
la technique standard pour éviter le flash de thème, et le contenu est une
constante littérale.

---

## U-08 🟡 Composants trop gros pour être relus

```
1558  views/optimizer-view.tsx
1487  app/lab/page.tsx
1426  views/smart-graph-view.tsx
 734  views/smart-replay-view.tsx
 677  app/bots/page.tsx
 603  views/compare-view.tsx
 572  views/derivatives-view.tsx
 555  app/settings/page.tsx
```

`app/lab/page.tsx` contient à lui seul les onglets Backtest, Optimiseur, ML,
Replay et Comparatif, plus des sous-composants (`BacktestTab`, `Verdict`…). Le
fichier mélange l'état local (une vingtaine de `useState`), l'orchestration des
requêtes, la logique métier (`Verdict`) et le rendu.

L'effet pratique le plus mesurable : **tout changement d'état dans un onglet
re-rend l'intégralité de la page**, y compris les graphiques des autres onglets
s'ils sont montés. Il y a 97 `useMemo`/`useCallback` dans le frontend, ce qui
indique qu'on a déjà lutté contre le problème au cas par cas.

**Correction** : un fichier par onglet, chargés par `next/dynamic` (cf. U-04).
Le découpage est mécanique et sans risque fonctionnel.

---

## U-09 🟡 `key={i}` sur des listes ordonnées

19 emplacements, dont plusieurs sur des données qui changent d'ordre ou de
longueur :

```
components/cards/activity-feed.tsx:43        flux d'activité (préfixe)
components/tables/trades-table.tsx:345       liste de trades
components/charts/walk-forward-table.tsx:143 lignes de folds
components/views/smart-graph-view.tsx:1282   4 tableaux distincts
app/lab/page.tsx:1134
```

Sur un flux où les nouveaux éléments arrivent **en tête** (`activity-feed`,
`signals-feed`), l'index est le pire choix possible : React réutilise le DOM de
l'élément 0 pour un contenu différent, ce qui casse l'animation, le focus et
l'état interne (par exemple une ligne dépliée dans `trades-table`).

**Correction** : utiliser l'identifiant métier (`trade.id`, `signal.time +
signal.symbol`, `fold.index`).

---

## U-10 🟡 Deux sources de vérité pour le mode expert

`app/lab/page.tsx:139-145` :

```tsx
setLocalExpert(localStorage.getItem('expert_mode') === 'true');
const expertMode = presetsQuery.data ? !!presetsQuery.data.expert_mode : localExpert;
...
localStorage.setItem('expert_mode', String(checked));
```

Le mode expert existe :

- côté serveur, dans `config/ops.yaml` (`ui.expert_mode: true`), modifiable par
  `POST /api/portfolio/expert-mode` (`routes/portfolio.py:529`) ;
- côté client, dans `localStorage`.

Le client écrit **les deux** mais lit la valeur serveur en priorité *si la
requête a répondu*. Pendant le chargement, il affiche la valeur locale. Sur deux
navigateurs différents, l'utilisateur voit deux états initiaux différents qui
convergent après le premier aller-retour — un basculement visible de l'interface.

`app/settings/page.tsx:238` fait la même double écriture.

**Correction** : une seule source (le serveur), avec `localStorage` comme cache
d'affichage optimiste explicitement marqué comme tel, ou pas de cache du tout.

---

## U-11/U-12 (mineurs)

- **U-11** : `<html lang="fr">` est figé dans `app/layout.tsx`. Même si l'i18n
  était branché (U-01), l'attribut resterait `fr` — ce qui fait lire les textes
  anglais avec une prononciation française par les synthèses vocales.
- **U-12** : les onglets sont pilotés par `?tab=` (`/lab?tab=optimizer`,
  `/market?tab=smartgraph`), et les redirections héritées du backend pointent
  dessus (`api/main.py:334-346`). La valeur n'est pas validée contre une liste
  fermée côté client : `?tab=nimportequoi` produit un état sans onglet actif
  plutôt qu'un repli sur le premier.

---

## Ce qui est solide

Le frontend est la partie la mieux tenue du dépôt.

- **`QueryBoundary` / `useStickyError`** (`ui/query-state.tsx`) : le
  raisonnement est exact et rarement fait correctement. Le problème identifié —
  un `refetchInterval` court remet `error` à `null` pendant la nouvelle
  tentative, donc l'erreur clignote ou n'apparaît jamais — est un vrai piège de
  TanStack Query, et la solution (mémoriser l'échec jusqu'au prochain succès)
  est la bonne. Le titre de page est rendu dans les trois états, donc une page
  a toujours un `h1` même backend éteint.
- **Le proxy same-origin** (`app/api/[...path]/route.ts`) : injection de
  `X-API-Key` côté serveur (jamais dans le bundle), suppression du CORS,
  gestion des en-têtes hop-by-hop, normalisation du 503 backend en
  `ApiError(status: 0)`. C'est propre et le raisonnement est écrit.
- **Timeout de 15 s sur chaque requête** avec `AbortSignal.timeout` : le mode
  de panne visé (SYN filtré, `fetch` pendu jusqu'au timeout TCP de l'OS) est
  réel et rarement traité.
- **Validation Zod non bloquante** : valider sans casser l'UI est le bon
  arbitrage pour un tableau de bord ; l'écart est journalisé.
- **Script de thème inline** avant le rendu, sans classe `dark` figée sur
  `<html>` : le flash de thème est évité correctement, y compris au re-render.
- **Outillage de test complet et configuré** : Vitest + Testing Library avec
  **seuils de couverture à 60 %** effectivement déclarés
  (`vitest.config.ts`), Playwright avec `a11y.spec.ts` (axe-core),
  `pages.spec.ts`, `visual.spec.ts` (avec instantanés) et
  `qw-backtest.spec.ts`. Peu de dépôts de cette taille en ont autant.
- **Palette CSS par variables** avec thème clair par défaut et bascule sombre,
  `color-scheme` correctement posé sur `html` — les contrôles natifs suivent le
  thème.
- **`api-status-banner.tsx` / `health-banner.tsx` / `halt-banner.tsx`** : les
  états dégradés du backend ont une place réservée dans l'interface, ce qui
  évite le « ça tourne dans le vide » classique.
