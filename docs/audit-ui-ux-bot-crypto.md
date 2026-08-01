# Audit UI/UX & Plan de Refonte — Bot Crypto (montreuild/bot-crypto)

**Mission** : Audit Product Designer & Frontend Architect sur application de trading crypto  
**Repository** : https://github.com/montreuild/bot-crypto  
**Version audité** : V12.17 (commit `d666fe9`, 30/07/2026)  
**Date de l'audit** : 30 juillet 2026  
**Auditeur** : Expert Product Designer & Frontend Architect (Z.ai)

---

## Sommaire exécutif

### Contexte du projet

Le repository `montreuild/bot-crypto` est un **bot de trading algorithmique multi-stratégies** open-source (MIT) à destination d'un trader individuel sophistiqué francophone. Il combine trading live/paper sur OKX, backtest avancé (Walk-Forward + Monte-Carlo), optimiseur bayésien, ML LightGBM avec registre versionné, et un moteur Smart Money Concepts (SMC/ICT) maison. L'audit externe V2 du 29/07/2026 a noté le projet **3,4/5** — mature mais non production-ready sans exécution du Sprint 0 (désormais fait).

Une **ancienne UI Jinja2** (~10 600 lignes, 19 templates, 3 fichiers JS partagés) a été physiquement supprimée le 29/07/2026 (commit `ecc87b2`) au profit d'un **frontend Next.js 15 / React 19** (23 pages, App Router, TanStack Query, WebSocket natif, proxy same-origin avec injection serveur de `X-API-Key`). Cette migration, actée par la décision **D4** du Plan Directeur, est techniquement réussie mais laisse des zones d'ombre : le backend expose près de **70 endpoints** dont une partie significative n'est pas ou peu exploitée par la nouvelle UI, et plusieurs fonctionnalités riches de l'ancienne UI (cône Monte-Carlo, journal des signaux avec rejets, walk-forward folds OOS, calques SMC, shadow allocation, presets de risque) n'ont pas encore été réimplémentées ou seulement partiellement.

### Score global et constats clés

L'UI Next.js actuelle obtient un **score global de 7,5/10** — un score solide qui reflète une base technique saine (architecture data mature, design visuel soigné, couverture fonctionnelle large) mais qui ne réalise pas encore le potentiel produit du backend. Trois constats majeurs structurent l'analyse :

1. **EquityCurve simulée** : le composant central du dashboard affiche des données sin/cos au lieu de l'historique réel via `/api/stats/daily`. Le KPI le plus regardé par un trader est donc trompeur — un bug P1 à corriger dès le Sprint 0 de la refonte.
2. **Design system incomplet** : 10 packages Radix UI installés mais non wrappés en composants, `window.confirm` utilisé pour des actions critiques (promote/reject ML), `<select>`/`<input>` bruts partout, light theme cassé par un `useEffect` qui force `dark` au mount. Le design system documenté dans `docs/DESIGN_SYSTEM.md` existe mais n'est pas outillé (Storybook reporté, axe-core jamais installé).
3. **Gap produit majeur entre l'UI actuelle et la vision cible** : la `VISION_CIBLE_BOTS_AUTONOMES.md` définit **5 pages méta** (Portefeuille / Mes Bots / Laboratoire / Marché / Réglages) avec une métaphore « gérant de fonds employant des bots-traders ». L'UI actuelle en compte 23 réparties en 4 groupes — l'écart d'arbitrage produit est le plus grand chantier de la refonte.

### Trois recommandations prioritaires

| # | Recommandation | Effort | Impact | Sprint |
|---|---|---|---|---|
| 1 | **Câbler EquityCurve sur `/api/stats/daily`** + corriger le bug KPICard flash-on-change | S (0,5 j) | Critical — confiance dans le dashboard | S0 |
| 2 | **Wrapper Radix en composants shadcn-style** (Select, Dialog, Tabs, Switch, Dropdown) + étendre `QueryBoundary` aux 15 pages restantes | L (8 j) | High — cohérence, qualité perçue, DX | S1-S2 |
| 3 | **Consolider les 23 pages en 5 pages méta** selon la vision cible, avec onboardning utilisateur et mode expert opt-in | XL (24 j) | High — valeur produit, différenciation | S3-S10 |

### Périmètre de l'audit

Le présent rapport s'articule autour des 4 axes demandés : (1) audit UI/UX de l'interface Next.js actuelle, (2) cartographie exhaustive de l'API backend (≈70 endpoints), (3) inventaire de l'ancienne UI Jinja2 décommissionnée, (4) plan de refonte avec vision produit agile sur 12 sprints (24 semaines, ~6 mois). Une matrice des risques techniques et produit complète le livrable. Toutes les recommandations sont justifiées par des observations de code ou de documentation et priorisées selon MoSCoW (Must / Should / Could / Won't) avec estimation de complexité (S/M/L/XL) et dépendances explicites.

---

## Partie 1 — Audit UI/UX de l'interface Next.js actuelle

### 1.1 Stack technique frontend

Le frontend actuel repose sur une stack moderne et cohérente, issue de la migration Jinja2 → Next.js achevée le 29/07/2026. Voici l'inventaire précis des technologies en place, telles que déclarées dans `frontend/package.json` et vérifiées dans le code.

| Catégorie | Technologie | Version | Statut |
|---|---|---|---|
| Framework | Next.js (App Router) | 15.5.22 | ✅ Stable |
| UI runtime | React | 19.0.0 | ✅ Stable |
| Typage | TypeScript (strict) | 5.7 | ✅ Strict |
| Styling | Tailwind CSS + PostCSS | 3.4.17 | ✅ Dark mode `class` |
| UI primitives | Radix UI (10 packages) | variées | ⚠️ Installés mais non wrappés |
| Charts | Recharts + lightweight-charts | 2.15 / 4.2 | ✅ Deux libs |
| Data fetching | TanStack Query | 5.62 | ✅ Cache + polling |
| State | Context API (3 ctx) + localStorage | — | ✅ Suffisant |
| Realtime | WebSocket natif + SSE | — | ✅ Backoff exponentiel |
| Forms/validation | Zod | 3.24 | ❌ Installé mais non utilisé |
| Animations | Framer Motion | 11.15 | ❌ Installé mais non utilisé |
| Icons | Lucide React | 0.468 | ✅ |
| Toasts | Sonner | 1.7.1 | ✅ |
| Tests | Playwright e2e | 1.49 | ⚠️ 40 tests smoke seulement |
| PWA | manifest.json + sw.js | — | ✅ Cache-first |

L'analyse révèle un paradoxe intéressant : l'équipe a installé des dépendances modernes (Radix UI complet, Zod, Framer Motion, TanStack Table) mais ne les a pas réellement exploitées. Cela suggère une intention de design system mature qui n'a pas été terminée — probablement par manque de temps dans les Sprints 5/6 ou par priorisation des fonctionnalités sur la qualité transverse. Le résultat est un frontend fonctionnellement riche mais avec une dette de cohérence visible : `window.confirm` coexiste avec des drawers stylés, des `<select>` bruts voisinent avec des boutons Card soignés.

### 1.2 Cartographie des parcours utilisateurs

L'application compte **19 pages utilisateur** + 1 route proxy (`/api/[...path]`) + 1 redirecteur (`/`). La sidebar organise la navigation en 4 groupes avec 18 items, complétée par un **Cmd+K search modal** (21 entrées indexées) qui constitue le parcours d'accès rapide le plus efficace pour un utilisateur expérimenté. Voici la cartographie complète des parcours :

| Parcours | Point d'entrée | Workflow | État final |
|---|---|---|---|
| Surveillance temps réel | `/dashboard` | KPIs live (3s) → equity curve → positions ouvertes → trades/signals feed (WS) → risk panel → allocations | Vue d'ensemble continue |
| Gestion du portefeuille | `/portfolio` | KPIs (5s) → lifecycle counts → bots avec edge significatif → allocation réelle vs shadow → activity feed → halt banner | Pilotage stratégique |
| Gestion des bots | `/bots` | Kanban (Candidat/Essai/Actif/Retiré) → filtres → drawer latéral → force-active → forward-test → reset CB | Cycle de vie des bots |
| Historique trades | `/trades` | Filtres (symbol/strategy/TF/slot 3-parties) → export CSV → pagination client | Analyse rétrospective |
| Backtest | `/backtest` | Config (symbol/TF/limit/WF/MC) → run → 7 KPIs → equity curve → table trades | Validation de stratégie |
| Optimisation | `/optimizer` | Config (méthode/trials/TFs/early stop) → SSE live progress → apply/cancel/delete → jobs list | Recherche de paramètres optimaux |
| ML — Registre modèles | `/models` | Versions par (tf, recipe) → pin/unpin → promote/reject (window.confirm) → train async → sweep | Gestion du ML ops |
| ML — Recettes & cache | `/ml` | Strategy-info → recettes disponibles → candles stats (lecture seule) | Diagnostic ML |
| Replay multi-TF | `/replay` | Config (symbol/months/TFs/strategies) → run → equity curve par TF → cross-TF summary | Étude comparée |
| Scanner de marché | `/scanner` | Fast analyse SMC/ICT → tendance/ATR/volume/patterns | Screening rapide |
| Smart Graph (SMC) | `/smartgraph` | Candlestick + 14 overlays SMC (OB/FVG/liquidity/breakers/structure/premium-discount) | Lecture du marché |
| Smart Replay (SMC) | `/smartreplay` | Rejeu bougie par bougie + overlays SMC lifecycle + play/pause/speed | Étude causale |
| Comparatif multi-strat | `/compare` | Form (symbol/TF/limit) → chips stratégies → run parallèle → table triable → equity curves | Choix de stratégie |
| Audit OOS | `/audit` | Résultats OOS → drawer params → filtres → export | Conformité backtest |
| Journal d'audit | `/audit-log` | Filtres (action/actor) → stats par action → export CSV | Traçabilité |
| Dérivés | `/derivatives` | Funding/OI/LSR/taker buy-sell → 4 charts → stat chips → sélecteur période | Sentiment marché |
| Données OHLCV | `/data` | Cache status → refetch manuel → backfill yfinance async + polling | Gestion du data |
| Configuration | `/config` | 4 onglets (stratégies/risk/notifications/exchange) → mutations persistées | Paramétrage fin |
| Réglages | `/settings` | Presets de risque → mode expert → thème → notifications navigateur | Préférences utilisateur |

Cette cartographie révèle une **couverture fonctionnelle large** mais une **redondance de parcours** : `/dashboard` et `/portfolio` se chevauchent fortement, `/optimizer` et `/ml` ne diffèrent que par le filtre `is_ml`, `/audit` et `/audit-log` traitent deux facettes d'un même domaine. La vision cible à 5 pages (Portefeuille / Mes Bots / Laboratoire / Marché / Réglages) exploite précisément cette redondance pour simplifier l'expérience.

### 1.3 Évaluation par dimension

#### Design system & cohérence visuelle — 7/10

Le design system actuel s'appuie sur une palette dark inspirée Bloomberg Terminal + Linear + Vercel, formalisée dans `docs/DESIGN_SYSTEM.md` (286 lignes) avec tokens couleur/typo/spacing/radius/animations/accessibilité. Les couleurs sont cohérentes : `background #0a0e14`, `surface #0f1419`, `card #141a23`, accents cyan `#06b6d4`, success `#10b981`, danger `#ef4444`, warning `#f59e0b`, purple `#8b5cf6`. Les classes utilitaires sémantiques (`.text-profit`, `.bg-loss-dim`, `.glow-cyan`, `.glass-card`, `.ticker-num` avec `font-feature-settings: 'tnum'`) dénotent une vraie réflexion sur l'identité visuelle.

Cependant, trois problèmes structurels limitent la cohérence. **Premièrement**, les tokens hex sont dupliqués entre `tailwind.config.ts` et `globals.css` (vars CSS `:root` + `.light`), ce qui crée un risque de désynchronisation. **Deuxièmement**, le `light theme` est cassé : `Providers.tsx` force `classList.add('dark')` au mount via `useEffect`, ignorant `getStoredTheme()` qui lit pourtant correctement `localStorage` + `prefers-color-scheme`. Le toggle dans `Topbar` appelle bien `setStoredTheme` (qui modifie la classe), mais au refresh suivant le `useEffect` réimpose `dark`. Le mode clair ne persiste donc jamais. **Troisièmement**, 10 packages `@radix-ui/*` sont installés (`dialog`, `dropdown-menu`, `tabs`, `toast`, `switch`, `select`, `label`, `separator`, `scroll-area`, `tooltip`) mais seuls `Tooltip` et indirectement `Slot` (via Button) sont wrappés en composants. Conséquence : chaque page réinvente les styles de `<select>`, `<input>`, `<details>`, `window.confirm` — il n'y a pas de vraie bibliothèque de composants UI, juste 3 primitives (`Button`, `Card`, `Badge`) codées maison.

#### Ergonomie — 7/10

L'ergonomie générale est bonne grâce à des choix de layout solides : sidebar 240px fixe à gauche (4 groupes, 18 items), topbar sticky avec start/stop bot + badge PAPER/LIVE + capital/PnL + WS status + health dots + Cmd+K search + toggle thème, contenu principal fluide en grille responsive. Le composant `QueryBoundary` standardise les états loading/error/empty/success avec `useStickyError` (évite le clignotage pendant le `refetchInterval`) — un pattern mature adopté sur 4 pages (dashboard, bots, portfolio, config). `ApiStatusBanner` global en haut de toutes les pages détecte le backend down et propose la commande `python cli.py --web` + bouton dismissible.

L'ergonomie souffre cependant d'une **adoption partielle de `QueryBoundary`** : sur 19 pages, seules 4 l'utilisent. Les 15 autres utilisent un `if (isLoading) return <spinner>` brut qui produit des erreurs non récupérables (`/ml`, `/data`, `/derivatives` affichent "Erreur lors du chargement" sans détail ni bouton retry). `/scanner` et `/compare` n'utilisent même pas TanStack Query (appel direct `api.fastAnalysis` / `api.runBacktest` dans un `useState`) — pas de cache, pas de retry, pas d'invalidation. Enfin, **aucun skeleton screen** n'est implémenté — uniquement des spinners `Loader2 animate-spin`, ce qui dégrade la perception de performance.

#### Accessibilité (WCAG 2.1 AA) — 5/10

L'accessibilité est le point faible le plus marquant. Les règles WCAG 2.1 AA sont écrites dans `docs/DESIGN_SYSTEM.md` mais **jamais outillées** — `@axe-core/playwright` n'est pas installé (item S6-02/S6-11 du plan directeur, explicitement ouvert). Les points forts existent : `role="status"` + `aria-live="polite"` sur `LoadingState`, `role="alert"` sur `ErrorState` et `ApiStatusBanner`, `role="switch"` + `aria-checked` sur le `Toggle` de `/settings`, `role="dialog"` + `aria-modal="true"` sur le `ParamsDrawer` de `/audit`, `aria-label` sur le bouton close du bandeau API, `<html lang="fr">`, liens de navigation en vrais `<a href>`, focus-visible ring sur `Button`.

Mais les points faibles sont nombreux et impactants. **Icon-only buttons sans `aria-label`** : boutons refresh dans `/data` (title only), bouton trash dans `JobCard` (`/optimizer`), boutons précédent/suivant dans `/smartreplay`, bouton thème dans `Topbar`. **`<input>` sans `<label>` associé** : la search bar Cmd+K dans `Topbar` (placeholder seulement), plusieurs `<input type="text">` dans `/trades` (label text séparé, pas de `htmlFor`/`id`). **Contraste insuffisant** : `text-dim` (#6b7280) sur `bg-card` (#141a23) ≈ 4,0:1 — limite AA pour texte normal (4,5:1 requis). Les labels `text-[10px]` en `text-dim` sont en dessous du seuil. **Pas de `scope="col"` sur les `<th>`** des ~12 tables du site. **Indicateurs de statut décoratifs** : les points verts/rouges `w-2 h-2 rounded-full` (health, WS, lifecycle) n'ont pas de texte alternatif — `aria-label` ou `sr-only` manquant. **Skip-to-content link absent**, pas de `not-found.tsx` ni d'`error.tsx`/`loading.tsx` App Router (conventions Next.js 15 non utilisées). **`window.confirm`** dans `/models` pour promote/reject — bloquant et non stylé.

#### Responsive — 6/10

Le responsive est partiellement traité. Les points forts : `main` a `p-4 md:p-6` (padding adaptatif), grilles fluide avec `grid-cols-1 md:grid-cols-2 lg:grid-cols-3`, topbar masque les libellés sous `md` (Responsive labels), viewport `width: 'device-width'`, `initialScale: 1`, `maximumScale: 5` (zoom autorisé — bonne pratique a11y).

Les points faibles sont structurants. **Sidebar fixe `w-60` (240px) sur tous les viewports** — pas de drawer mobile, pas de hamburger. Sur mobile, 240px de sidebar + le reste pour `main` donne une UX cramped inacceptable pour un tableau de trading. **Topbar non wrappée** : sur petit écran, elle accumule start/stop + badge PAPER + status + capital + PnL + WS + health + search + thème → overflow horizontal probable sous 768px. **Tables larges** (`/trades` 11 colonnes, `/audit-log` 8 colonnes) : `overflow-x-auto` sauve la mise mais aucune indication visuelle de scroll horizontal. **Pas de media query pour la taille de chart** : `h-64` fixe sur `EquityCurve`, `h-72` sur backtest equity — peut être trop grand sur mobile. **Tests e2e Playwright ne couvrent pas les viewports mobiles** (pas de `projects` avec `viewport: { width: 375 }`).

#### Performance perçue — 7/10

La performance perçue est correcte grâce à plusieurs bonnes pratiques : TanStack Query avec `staleTime: 5s` et `retry: 1` évite les refetch inutiles, polling adaptatif (1,5s pour optimizer live, 3s status, 5s portfolio, 10s bots, 30s ML/data, 60s derivatives), WebSocket avec reconnexion backoff exponentiel (max 30s) et ping 30s, timeout `AbortSignal.timeout(15_000)` par défaut sur `apiFetch` (résout les spinners infinis du S6-12). Le proxy same-origin évite les preflight CORS et garde la clé API côté serveur.

Cependant, plusieurs problèmes dégradent l'expérience. **Polling 1,5s sur `useOptimizeStatus` même quand aucun job n'est running** — gaspillage de requêtes serveur. **`/scanner` et `/compare` hors TanStack Query** — pas de cache, appel direct `api.fastAnalysis` à chaque interaction. **Aucun rate-limit côté frontend** sur les actions mutables (start/stop bot, apply optimize, promote model) — un double-clic rapide peut doubler la requête. `disabled={isPending}` est présent mais pas systématique. **Dépendances mortes** dans le bundle : `framer-motion` (~30 kB), `zod`, 8 Radix non utilisés, `@tanstack/react-table`, `date-fns` — ~80 kB de JS mort qui pourrait être éliminé. **Aucun skeleton screen** : seulement des spinners, ce qui donne une impression de lenteur. **Service worker enregistré depuis `/settings` uniquement** — surprenant ; devrait l'être globalement dans `layout.tsx` ou `providers.tsx`.

#### Gestion des états (loading/error/empty/success) — 6/10

La gestion des états reflète la maturité inégale du frontend. Le pattern canonique via `QueryBoundary` est excellent sur les 4 pages qui l'adoptent (dashboard, bots, portfolio, config) : titre monté avant la garde → `h1` toujours présent, erreur réessayable avec bouton contextualisé, loading label contextualisé, `useStickyError` qui évite le clignotage pendant le `refetchInterval`. `ApiStatusBanner` global est un excellent filet de sécurité : "Backend injoignable" + commande de démarrage + dismissible, sticky error qui persiste tant que le backend est down.

Mais les 15 autres pages souffrent d'inconsistance. Plusieurs affichent un simple "Erreur lors du chargement" sans détail ni bouton retry (`/ml`, `/data`, `/derivatives`). Les empty states sont variables : `PositionsTable` a un vrai empty state (icône Clock + message), `LiveTradesFeed` idem, mais `/trades` affiche juste "Aucun trade" sans icône, `/ml` idem. **Aucun skeleton** n'est implémenté — uniquement des spinners `Loader2 animate-spin`. Le bug le plus critique est l'`EquityCurve` simulée : le composant affiche des données sin/cos au lieu de l'historique réel (`/api/stats/daily` non câblé, TODO mentionné dans le code). Le KPI principal du dashboard est donc trompeur.

### 1.4 Tableau des problèmes identifiés

| # | Problème | Sévérité | Impact utilisateur | Effort | Sprint |
|---|---|---|---|---|---|
| 1 | EquityCurve simulée (sin/cos au lieu de `/api/stats/daily`) | P1 Critical | Confiance dashboard rompue | S (0,5j) | S0 |
| 2 | Light theme cassé (`Providers.tsx` force `dark` au mount) | P1 High | Fonctionnalité annoncée non opérationnelle | S (0,5j) | S0 |
| 3 | KPICard flash-on-change cassé (`prevValue` capturé une fois) | P2 Medium | Animation silencieuse après 2e changement | S (0,5j) | S0 |
| 4 | ApiStatusBanner "adjusting state during render" (anti-pattern React) | P2 Medium | Warnings dev, possible bug strict mode | S (0,5j) | S0 |
| 5 | Sidebar footer "Connected" hardcodé (ne reflète pas le WS) | P3 Low | Mensonge quand backend down | S (0,5j) | S1 |
| 6 | Manifest PWA référence `/icon-192.png` inexistant | P3 Low | appleWebApp.icon cassé | S (0,5j) | S1 |
| 7 | `NEXT_PUBLIC_WS_URL` hardcodé `ws://localhost:8000/ws` si non défini | P2 High | WS silencieusement cassé en prod | S (0,5j) | S1 |
| 8 | `/scanner` et `/compare` hors TanStack Query | P2 Medium | Pas de cache, retry, invalidation | M (2j) | S1 |
| 9 | `QueryBoundary` adopté sur 4/19 pages seulement | P1 High | États d'erreur inconsistants | M (3j) | S1-S2 |
| 10 | Pas de composants UI pour `<select>`/`<input>`/`<dialog>`/`<tabs>`/`<switch>` | P1 High | Chaque page réinvente les styles | L (8j) | S2 |
| 11 | `window.confirm` dans `/models` (promote/reject) vs drawer stylé dans `/audit` | P2 Medium | UX hétérogène | M (2j) | S2 |
| 12 | Versions hardcodées "v12.17" dans sidebar et settings | P3 Low | Désynchronisation avec `package.json` | S (0,5j) | S2 |
| 13 | Service worker enregistré depuis `/settings` uniquement | P3 Low | PWA limitée | S (0,5j) | S2 |
| 14 | Aucun skeleton screen (uniquement spinners) | P2 Medium | Perception de lenteur | M (3j) | S2 |
| 15 | Aucune page 404 personnalisée | P3 Low | UX par défaut Next.js | S (0,5j) | S2 |
| 16 | Pas de pagination server-side sur `/trades` (filtre client sur 1000 lignes) | P2 Medium | Perf dégradée sur gros volume | M (2j) | S3 |
| 17 | Pas de reconnexion session optimiseur au refresh | P3 Low | Perte contexte utilisateur | M (2j) | S3 |
| 18 | Pas de prefetch des routes au hover | P3 Low | Navigation moins fluide | S (0,5j) | S3 |
| 19 | Proxy `/api/[...path]` fait un `fetch` serveur par requête (pas de keepalive) | P2 Medium | Latence ajoutée | M (3j) | S4 |
| 20 | Aucun rate-limit côté frontend sur actions mutables | P2 Medium | Double-clic peut doubler la requête | S (1j) | S1 |
| 21 | `zod` installé mais non utilisé (inputs non validés) | P2 Medium | Risque injection / valeurs aberrantes | M (2j) | S2 |
| 22 | `framer-motion` installé mais non utilisé (~30 kB JS mort) | P3 Low | Bundle bloat | S (0,5j) | S2 |
| 23 | 8 packages Radix installés mais non wrappés | P1 High | Bundle bloat + dette technique | L (8j) | S2 |
| 24 | `@tanstack/react-table` installé mais non utilisé (tables en JSX brut) | P3 Low | Bundle bloat + tables non triables/filtrables | L (4j) | S3 |
| 25 | `date-fns` installé mais non utilisé (utils.ts utilise `Intl` natif) | P3 Low | Bundle bloat | S (0,5j) | S2 |
| 26 | Icon-only buttons sans `aria-label` (refresh, trash, prev/next, thème) | P1 High | A11y clavier/screen reader cassée | S (1j) | S2 |
| 27 | `<input>` sans `<label>` associé (Cmd+K, `/trades`) | P1 High | A11y formulaire | S (1j) | S2 |
| 28 | Contraste `text-dim` #6b7280 sur `bg-card` #141a23 ≈ 4,0:1 (sous AA) | P2 Medium | A11y lecture | S (0,5j) | S2 |
| 29 | Pas de `scope="col"` sur les `<th>` (~12 tables) | P2 Medium | A11y table | S (1j) | S2 |
| 30 | Skip-to-content link absent | P2 Medium | A11y navigation clavier | S (0,5j) | S2 |
| 31 | Sidebar fixe 240px sur mobile (pas de drawer) | P1 High | UX mobile inacceptable | L (4j) | S3 |
| 32 | Topbar overflow probable sous 768px | P2 Medium | UX mobile | M (2j) | S3 |
| 33 | Tests e2e ne couvrent pas les viewports mobiles | P2 Medium | Régression mobile non détectée | M (2j) | S3 |
| 34 | Aucun test unitaire / composant / a11y / visuel | P1 High | Dette QA majeure | L (5j) | S2-S3 |
| 35 | `axe-core` jamais installé (règles WCAG écrites non outillées) | P1 High | Conformité AA non vérifiable | M (2j) | S2 |

### 1.5 Score global UI/UX

**Score : 7,5 / 10**

Ce score reflète une base solide (architecture data mature, design visuel soigné, couverture fonctionnelle large) plafonnée par plusieurs problèmes structurels (light theme cassé, equity curve simulée, design system incomplet, a11y non outillée, responsive mobile insuffisant). Pour atteindre 9/10, il faudrait corriger les bugs P1 du Sprint 0, wrapper les Radix en vrais composants shadcn-style, étendre `QueryBoundary` + skeletons à toutes les pages, ajouter un drawer mobile, combler les gaps a11y, ajouter RTL + tests unitaires, et nettoyer les deps mortes. La trajectoire est claire et atteignable en 6 mois.

---

## Partie 2 — Cartographie exhaustive de l'API backend

### 2.1 Vue d'ensemble

Le backend est une application **FastAPI 0.115.0** (uvicorn 0.30, SlowAPI 0.1.9) point d'entrée `app/api/main.py`. L'app factory `init_app(config, live_trader)` injecte `state.cfg/trader/SessionLocal` et initialise l'audit DB. Le lifespan `_lifespan` lie `event_hub` à la loop async au démarrage. La documentation OpenAPI/Swagger est servie sur `/api/docs` (Swagger UI) et `/api/redoc`. La réponse par défaut est `CleanJSONResponse` qui sanitize NaN/Inf via `app.core.sanitize`.

L'inventaire exhaustif recense **17 routers** inclus dans l'ordre suivant : `config_global`, `config_strategies`, `config_risk`, `config_notifications`, `trades`, `backtest`, `scanner`, `optimizer`, `bot`, `ml`, `replay`, `derivatives`, `portfolio`, `data`, `universe`, `ws`, `audit_log`. Aucun router ne déclare `tags=` → OpenAPI est non catégorisé, le Swagger UI affiche tous les endpoints à plat. Le frontend est la seule source de catégorisation visuelle (pages par fonctionnalité). Il n'existe **pas de versionnage d'API** : tous les endpoints sont sous `/api/*` sans préfixe de version. La rétro-compat est maintenue via des alias (`/api/capital-allocation/set-budget` → `/api/slots/{slot_key}/budget`, `/slots` → `/bots`).

La stack middlewares (dans l'ordre d'ajout inverse d'exécution) comprend : exception handlers (RateLimitExceeded → 429, Exception global → JSON 500 `{detail, path}` sans stacktrace), SlowAPIMiddleware (rate-limiting 300/min par défaut), GZipMiddleware (minimum_size=500), CORSMiddleware (allow_origins `ALLOWED_PROXIES` env CSV ou défaut localhost + FRONTEND_URL, allow_methods `GET/POST/DELETE/PUT` sans PATCH, allow_headers `X-API-Key` + `Content-Type`), `metrics_middleware` (OBS-01 → Prometheus), `correlation_middleware` (OBS-02 → header `X-Request-ID`), et `https_redirect` si `FORCE_HTTPS=1`.

L'authentification repose sur `verify_api_key` (dépendance FastAPI sur tous les endpoints `/api/*` sauf `/api/ws/status`). Si `web.api_key` est vide dans `config.yaml` → localhost only (127.0.0.1, ::1, localhost), sinon 403. Si configurée → header `X-API-Key` (max 256 chars) OU cookie `api_key`, comparaison `hmac.compare_digest`. `TRUSTED_PROXIES` (env CSV) : `X-Forwarded-For` honoré uniquement depuis ces IP (anti-spoofing). Pour le WebSocket `/ws` : localhost OU cookie HttpOnly `api_key` (priorité) OU `?api_key=xxx` (fallback moins sûr). Échec auth → close code 4403.

Le frontend Next.js ne porte jamais la clé API dans le bundle JS : le proxy `src/app/api/[...path]/route.ts` injecte `X-API-Key` côté serveur (variable `WEB_API_KEY`). Cela évite CORS, évite l'exposition de la clé, évite les preflight OPTIONS. Le WebSocket reste en direct (Next ne proxifie pas les WS) via `NEXT_PUBLIC_WS_URL`.

### 2.2 Endpoints top-level et redirects

| Méthode | Route | Auth | Réponse | Description |
|---|---|---|---|---|
| GET | `/health` | Non | `{status, db, exchange, trader}` | Health check simple |
| GET | `/metrics` | Non | Prometheus text/plain | Métriques Prometheus |
| GET | `/api/status` | Optionnel | `{status, paper_mode, timeframe, timeframes, strategies, capital, total_pnl, positions, by_strategy, signal_log, circuit_breaker_active, daily_pnl_pct, global_dd_pct, capital_allocation, slot_states}` | Statut global bot |
| GET | `/` | Non | 308 redirect → FRONTEND_URL ou page d'aide HTML | Redirige vers Next.js |
| GET | `/audit`, `/audit-log`, `/trades`, `/models`, `/data` | Non | 308 → même chemin côté Next | Vraies pages Next |
| GET | `/backtest`, `/optimizer`, `/ml`, `/replay`, `/compare` | Non | 308 → `/lab?tab=…` | Onglets du Laboratoire |
| GET | `/scanner`, `/smartgraph`, `/smartreplay`, `/derivatives` | Non | 308 → `/market?tab=…` | Onglets du Marché |
| GET | `/config` | Non | 308 → `/settings?tab=capital` | Onglet des Réglages |
| GET | `/portfolio`, `/bots`, `/settings` | Non | même chemin côté Next | Pages méta (S11 : plus de suffixe `-v2`) |
| GET | `/portfolio-v2`, `/bots-v2`, `/settings-v2` | Non | 308 → page sans suffixe | Anciennes URLs, conservées |
| GET | `/slots` | Non | 308 → `/bots` | Alias legacy |

Les cibles sont les destinations **finales** : depuis les lots de fusion,
viser l'ancien chemin côté Next coûterait un second 308 (cf. §Le double 308 du
backend). `tests/test_legacy_redirects.py` verrouille l'alignement avec
`next.config.mjs`.

### 2.3 Cartographie endpoint par endpoint

Le tableau ci-dessous cartographie les ~70 endpoints d'API avec leur consommation actuelle par l'UI Next.js et l'opportunité produit identifiée. La colonne "Consommé UI" est issue de l'analyse du fichier `frontend/src/lib/api.ts` (~60 méthodes) et des hooks `use-api.ts` (~40 hooks).

#### Router bot.py — Contrôle du trader

| Méthode | Route | Params | Consommé UI ? | Opportunité produit | Priorité |
|---|---|---|---|---|---|
| POST | `/api/bot/start` | — | ✅ Topbar (`useStartBot`) | Bouton start/stop dans topbar | Must |
| POST | `/api/bot/stop` | `close_positions: bool` | ✅ Topbar (`useStopBot`) | Modal "conserver/clôturer positions" | Must |
| POST | `/api/circuit-breakers/reset/{slot_key}` | `slot_key: path` | ⚠️ Partiel (`useResetSlot`) | Bouton reset CB par slot dans `/bots` drawer | Should |

#### Router trades.py — Trades, stats, risk, slots

| Méthode | Route | Params | Consommé UI ? | Opportunité produit | Priorité |
|---|---|---|---|---|---|
| GET | `/api/trades` | `limit, offset, symbol, strategy` | ✅ `useTrades` (15s) | Filtres + pagination server-side | Should |
| GET | `/api/trades/export` | `limit` | ✅ Bouton export CSV | Export CSV avec sélecteur de limite | Must |
| GET | `/api/stats/daily` | `days` | ❌ **NON consommé** | **CRITICAL** : EquityCurve devrait l'utiliser (au lieu de sin/cos simulé). PnL journalier dans `/dashboard`, `/trades`, `/portfolio` | Must |
| GET | `/api/stats/fees` | `days` | ❌ Non consommé | Widget ventilation frais (taker/maker/borrow/stop) dans `/portfolio` | Should |
| GET | `/api/risk` | — | ⚠️ Partiel (dans `/api/status`) | Risk panel standalone dans `/portfolio` | Should |
| POST | `/api/risk/reset-halt` | `force: bool` | ✅ `useResetHalt` | Bouton "Acquitter kill-switch" | Must |
| GET | `/api/capital-allocation` | — | ⚠️ Partiel | Vue allocation consolidée | Could |
| GET | `/api/slots` | — | ✅ `useSlots` | Grille slots dans `/bots` | Must |
| POST | `/api/slots/{slot_key}/budget` | `budget_pct: 0-1` | ✅ `useSetSlotBudget` | Slider budget dans drawer `/bots` | Must |
| POST | `/api/slots/{slot_key}/toggle` | `enabled: bool` | ✅ `useToggleSlot` | Toggle enable/disable dans `/bots` | Must |
| POST | `/api/slots/{slot_key}/reset` | `slot_key: path` | ✅ `useResetSlot` | Reset CB slot | Must |
| POST | `/api/slots/rebalance` | — | ✅ `useRebalanceSlots` | Bouton "Forcer rééquilibrage" | Should |
| GET | `/api/circuit-breakers` | — | ✅ `useCircuitBreakers` (5s) | État CB globaux + per slot | Must |
| GET | `/api/audit/results` | — | ✅ `useAuditResults` (30s) | Page Audit OOS | Should |
| GET | `/api/strategy/{slot_key}/performance` | `slot_key: path` | ❌ Non consommé | **Drawer performance slot détaillée** dans `/bots` (win_rate, PF, Sharpe, max_dd, recent_trades) | Should |

#### Router backtest.py

| Méthode | Route | Params | Consommé UI ? | Opportunité produit | Priorité |
|---|---|---|---|---|---|
| POST | `/api/backtest` | `symbol, limit, timeframe, walk_forward, monte_carlo, strategies` | ✅ `useRunBacktest` | Page backtest + walk-forward + MC | Must |
| POST | `/api/backtest/cancel` | — | ✅ `useCancelBacktest` | Bouton annuler | Must |
| GET | `/api/backtest/status` | — | ✅ `useBacktestStatus` | Flag running | Must |
| GET | `/api/backtest/settings` | — | ✅ `useBacktestSettings` (5min) | TFs + stratégies disponibles | Must |

#### Router replay.py

| Méthode | Route | Params | Consommé UI ? | Opportunité produit | Priorité |
|---|---|---|---|---|---|
| POST | `/api/replay` | `symbol, months, timeframes, strategies, walk_forward, monte_carlo` | ✅ `useRunReplay` | Page replay multi-TF | Should |
| POST | `/api/replay/cancel` | — | ✅ `useCancelReplay` | Bouton annuler | Should |

#### Router scanner.py — Marché

| Méthode | Route | Params | Consommé UI ? | Opportunité produit | Priorité |
|---|---|---|---|---|---|
| GET | `/api/scanner/fast_analysis` | `symbol, tf, taker, maker` | ✅ `api.fastAnalysis` (pas de cache) | Page scanner — **à migrer vers TanStack Query** | Should |
| GET | `/api/scanner` | `timeframe, limit` | ❌ Non consommé | **Table scan multi-symboles** dans `/scanner` (tableau triable) | Should |
| GET | `/api/scanner/config` | — | ❌ Non consommé | **Widget config scanner** (min_volume, timeframes, strategy_timeframes) | Could |
| GET | `/api/scanner/opportunities` | `timeframe, limit` | ❌ Non consommé | **Top opportunités** dans `/scanner` (40% vol 24h + 60% ATR%) | Should |
| GET | `/api/scanner/chart` | `symbol, timeframe, limit` | ✅ `useScannerChart` | Bougies + indicateurs (EMA, MACD, RSI, BB) | Must |
| GET | `/api/scanner/setup_series` | `symbol, timeframe, limit, strategy` | ❌ Non consommé | **Markers setups V11/V12** sur le chart scanner | Should |
| GET | `/api/scanner/smc` | `symbol, timeframe, limit` | ✅ `useSMC` (30s) | Analyse SMC dans `/smartgraph` | Must |
| GET | `/api/scanner/smc_replay` | `symbol, timeframe, limit` | ✅ `useSMCReplay` (60s) | Rejeu SMC dans `/smartreplay` | Must |
| GET | `/api/scanner/signals` | `symbol, timeframe, limit` | ❌ Non consommé | **Signaux récents** dans `/scanner` | Could |

#### Router optimizer.py

| Méthode | Route | Params | Consommé UI ? | Opportunité produit | Priorité |
|---|---|---|---|---|---|
| POST | `/api/optimize/start` | `symbol, symbols, strategies, timeframes, method, n_trials, limit, auto_apply, n_jobs, early_stop_patience, ml_tune_hp, param_search_optim` | ✅ `useStartOptimize` | Page optimizer | Must |
| GET | `/api/optimize/status` | `job_id` | ✅ `useOptimizeStatus` (1.5s) | Status jobs | Must |
| GET | `/api/optimize/stream` | `job_id` (SSE) | ✅ EventSource | **Live progress SSE** — excellent pattern | Must |
| POST | `/api/optimize/apply` | `job_id, config_path, force` | ✅ `useApplyOptimize` | Bouton apply | Must |
| POST | `/api/optimize/cancel` | `job_id` | ✅ `useCancelOptimize` | Bouton cancel | Must |
| DELETE | `/api/optimize/job` | `job_id` | ✅ `useDeleteOptimize` | Bouton delete | Must |
| GET | `/api/optimize/results` | — | ✅ `useOptimizeResults` | Résultats classés par (strategy, tf) | Should |
| GET | `/api/optimize/spaces` | — | ✅ `useOptimizeSpaces` | Espace de paramètres | Should |

#### Router ml.py — Machine Learning

| Méthode | Route | Params | Consommé UI ? | Opportunité produit | Priorité |
|---|---|---|---|---|---|
| GET | `/api/ml/strategy-info` | — | ✅ `useMLStrategyInfo` (30s) | État ML par stratégie | Must |
| GET | `/api/candles/stats` | — | ✅ `useCandlesStats` (30s) | Stats cache Parquet | Should |
| GET | `/api/ml/recipes` | — | ❌ Non consommé | **Liste recettes ML** dans `/models` (trainable, features_catalog, label_scheme, heads) | Should |
| GET | `/api/ml/registry` | — | ✅ `useMLRegistry` | Vue registre | Must |
| GET | `/api/ml/registry/versions` | `tf, recipe` | ✅ `useMLRegistryVersions` | Versions par (tf, recipe) | Must |
| GET | `/api/ml/registry/decisions` | `tf, recipe, limit` | ✅ `useMLRegistryDecisions` | Journal décisions gate | Should |
| POST | `/api/ml/registry/pin` | body `{tf, recipe, version_id}` | ✅ `usePinVersion` | Pin version | Must |
| POST | `/api/ml/registry/unpin` | body `{tf, recipe}` | ✅ `useUnpinVersion` | Unpin version | Must |
| POST | `/api/ml/registry/promote` | body `{tf, recipe, version_id, decision, reason}` | ✅ `usePromoteVersion` | Promouvoir version | Must |
| POST | `/api/ml/train` | body `_TrainBody` | ✅ `useStartMLTrain` | Entraîner modèle (dry-run/publish) | Must |
| GET | `/api/ml/train/status` | `job_id` | ✅ `useMLTrainStatus` (polling adaptatif) | Suivi job train | Must |
| POST | `/api/ml/sweep` | body `_SweepBody` | ✅ `useStartMLSweep` | Window sweep | Should |
| GET | `/api/ml/sweep/status` | `job_id` | ✅ `useMLSweepStatus` | Suivi job sweep | Should |

#### Router derivatives.py

| Méthode | Route | Params | Consommé UI ? | Opportunité produit | Priorité |
|---|---|---|---|---|---|
| GET | `/api/derivatives/data` | `symbol, period, limit, refresh` | ✅ `useDerivativesData` (60s) | Page derivatives — 4 charts | Should |
| GET | `/api/derivatives/status` | `symbol` | ✅ `useDerivativesStatus` | État cache dérivés | Could |

#### Router portfolio.py

| Méthode | Route | Params | Consommé UI ? | Opportunité produit | Priorité |
|---|---|---|---|---|---|
| GET | `/api/notifications` | `limit, level` | ✅ `useNotifications` (10s) | Fil notifications 3 niveaux | Must |
| GET | `/api/oos-tracker` | — | ❌ Non consommé | **OOS tracker brut** — drawer cône Monte-Carlo dans `/bots` | Should |
| GET | `/api/bots` | — | ✅ `useBots` (10s) | Kanban lifecycle | Must |
| POST | `/api/bots/{slot_key}/force-active` | `enabled: bool` | ✅ `useForceBotActive` | Override manuel lifecycle | Must |
| POST | `/api/bots/{slot_key}/forward-test` | `slot_key: path` | ✅ `useRunForwardTest` | Relance forward-test | Should |
| GET | `/api/portfolio` | — | ✅ `usePortfolio` (5s) | Vue portefeuille | Must |
| GET | `/api/settings/presets` | — | ✅ `usePresets` | Presets risque | Must |
| POST | `/api/settings/risk-preset` | `preset` | ✅ `useSetRiskPreset` | Applique preset | Must |
| POST | `/api/settings/expert-mode` | `enabled: bool` | ✅ `useSetExpertMode` | Toggle mode expert | Must |

#### Router data.py — Gestion des données

| Méthode | Route | Params | Consommé UI ? | Opportunité produit | Priorité |
|---|---|---|---|---|---|
| GET | `/api/data/status` | — | ✅ `useDataStatus` (30s) | Cache OHLCV | Must |
| POST | `/api/data/refetch` | `symbol, tf, bars` | ✅ `useRefetchData` | Refetch manuel | Must |
| POST | `/api/data/backfill-equities` | `tf, years` | ✅ `api.startBackfillEquities` | Backfill yfinance async | Should |
| GET | `/api/data/backfill-status/{job_id}` | `job_id: path` | ✅ `api.getBackfillStatus` | Suivi job backfill | Should |

#### Router universe.py

| Méthode | Route | Params | Consommé UI ? | Opportunité produit | Priorité |
|---|---|---|---|---|---|
| GET | `/api/universe` | — | ❌ Non consommé | **Sélecteur d'univers** dans `/data` (SBF120, etc.) | Should |
| GET | `/api/universe/{name}` | `name: path` | ❌ Non consommé | **Liste membres** d'un univers + bars par TF | Should |
| POST | `/api/universe/{name}/symbols` | body `_AddSymbolBody` | ❌ Non consommé | **Ajouter symbole** à un univers | Could |
| DELETE | `/api/universe/{name}/symbols/{symbol}` | `name, symbol: path` | ❌ Non consommé | **Retirer symbole** d'un univers | Could |

#### Router audit_log.py

| Méthode | Route | Params | Consommé UI ? | Opportunité produit | Priorité |
|---|---|---|---|---|---|
| GET | `/api/audit/log` | `limit, offset, action, actor` | ✅ `useAuditLog` (10s) | Journal d'audit paginé | Should |
| GET | `/api/audit/log/stats` | — | ✅ `useAuditLogStats` (30s) | Stats par action | Should |

#### Router config_global.py

| Méthode | Route | Params | Consommé UI ? | Opportunité produit | Priorité |
|---|---|---|---|---|---|
| GET | `/api/config` | — | ✅ `useConfig` | Config globale | Must |
| POST | `/api/config/trading` | `score_threshold, risk_per_trade, max_positions, paper_mode, paper_slippage, daily_drawdown_limit` | ✅ `useUpdateTradingConfig` | MAJ trading params | Must |
| POST | `/api/config/margin` | `margin, margin_mode, max_leverage` | ✅ `useUpdateMarginConfig` | Config margin | Should |
| POST | `/api/config/capital-allocator` | `mode, rebalance_interval, max_slot_pct` | ✅ `useUpdateAllocatorConfig` | Config allocateur | Should |
| GET | `/api/backtest/settings` | — | ✅ `useBacktestSettings` | Settings backtest | Must |
| GET | `/api/config/changelog` | `limit` | ❌ Non consommé | **Historique changelog optimizer** dans `/audit` | Could |

#### Router config_strategies.py

| Méthode | Route | Params | Consommé UI ? | Opportunité produit | Priorité |
|---|---|---|---|---|---|
| POST | `/api/config/strategies` | `enabled: CSV` | ✅ `useToggleStrategy` | Active stratégies | Must |
| POST | `/api/config/timeframes` | `timeframes: CSV` | ✅ `useToggleTimeframe` | MAJ TF actifs | Must |
| POST | `/api/config/auto-optimizer` | `enabled, interval_h` | ✅ `useUpdateAutoOptimizer` | Config auto-optimizer | Should |
| GET | `/api/config/strategy-overrides` | `strategy` | ❌ Non consommé | **Overrides par stratégie** dans `/config` (per symbole) | Should |
| POST | `/api/config/strategy-params` | body `{strategy, params, timeframe?, symbol?}` | ❌ Non consommé | **Éditeur params strat** (base OU override per-symbole) | Should |
| POST | `/api/config/strategy-timeframe` | `strategy, timeframe, enabled` | ❌ Non consommé | **Toggle strat sur un TF** dans `/config` | Should |

#### Router config_risk.py

| Méthode | Route | Params | Consommé UI ? | Opportunité produit | Priorité |
|---|---|---|---|---|---|
| POST | `/api/config/risk` | `consecutive_loss_limit, slot_daily_dd_limit, win_rate_floor, volatility_threshold, consecutive_pause_secs` | ❌ Non consommé | **Config circuit breakers par slot** dans `/config` | Should |

#### Router config_notifications.py

| Méthode | Route | Params | Consommé UI ? | Opportunité produit | Priorité |
|---|---|---|---|---|---|
| GET | `/api/config/notifications` | — | ✅ `useConfig` (inclus) | Config notifications | Should |
| POST | `/api/config/notifications` | `telegram_enabled, telegram_bot_token, telegram_chat_id, whatsapp_*, email_*, min_pnl_to_notify, position_loss_warn_pct` | ✅ `useUpdateNotifications` | MAJ config notifications | Should |
| POST | `/api/config/notifications/test` | — | ❌ Non consommé | **Bouton test envoi notification** dans `/config` | Should |

#### Router ws.py — WebSocket temps réel

| Méthode | Route | Params | Description |
|---|---|---|---|
| WS | `/ws` | `api_key: query (fallback)` | WebSocket temps réel — voir détails ci-dessous |
| GET | `/api/ws/status` | — | Status hub WS (NO auth, debug) |

### 2.4 WebSockets et streams temps réel

#### WebSocket `/ws`

L'application expose un **WebSocket unique** `/ws` qui canalise tous les events temps réel via un EventHub in-process (`app.core.events.event_hub`, pub/sub asyncio.Queue maxsize=200 par subscriber). L'authentification se fait via localhost OU cookie HttpOnly `api_key` (priorité) OU `?api_key=xxx` (fallback moins sûr). Échec auth → close code 4403. Au-delà de l'auth, **100 derniers événements sont rejoués** sur connexion (ring buffer EventHub) — excellente pratique qui évite le "blank state" au refresh.

**Channels disponibles** : `{trades, signals, risk, cycle, ticker}`. Le client peut s'abonner sélectivement via `{type: "subscribe", channels: [...]}` pour filtrer les events reçus.

**Messages serveur→client** (JSON) :

| Type | Déclenchement | Payload |
|---|---|---|
| `connected` | Connexion établie | `{subscribers, history_size, channels, server_time}` |
| `trade.opened` | Nouveau trade ouvert | data: trade object |
| `trade.closed` | Trade clôturé | data: trade object avec PnL |
| `signal.generated` | Signal généré (validé ou rejeté) | data: signal object |
| `risk.circuit_breaker` | Circuit breaker déclenché | data: {severity: critical, ...} |
| `risk.drawdown_warning` | Alerte drawdown | data: {severity: warning, ...} |
| `cycle.update` | Update cycle bot | data: cycle state |
| `ticker.update` | Update ticker marché | data: ticker |
| `subscribed` | Ack abonnement channels | data: {channels} |
| `pong` | Réponse à ping client | ts |

**Messages client→serveur** : `{type: "ping"}` (réponse pong), `{type: "subscribe", channels: [...]}`.

Côté frontend, le `WebSocketProvider` (`frontend/src/lib/ws-provider.tsx`) gère la connexion avec **reconnexion backoff exponentiel** (max 30s), **ping 30s**, et expose 5 hooks spécialisés : `useTradeEvents`, `useSignalEvents`, `useRiskEvents`, `useCycleUpdates`, `useLiveTickers`. Ces hooks alimentent les composants `LiveTradesFeed`, `SignalsFeed`, `RiskPanel`, `KPICards`, `PositionsTable` sur le dashboard. C'est un pattern mature et bien implémenté.

#### SSE `/api/optimize/stream`

Le stream de progression de l'optimiseur utilise **Server-Sent Events** (media_type `text/event-stream`, headers `Cache-Control: no-cache` + `X-Accel-Buffering: no`). Format `data: {json}\n\n`. Pousse le statut d'un job d'optimisation via polling 0,8s (max 600 itérations = 8 min) : `{progress, status, best_score, trials[], strategy, timeframe, trials_done, n_trials}`. Si done/error : ajoute `{result, error, applied, baseline}`. Se termine quand `status ∈ {done, error}`.

Côté frontend, `EventSource` consomme ce stream avec `withCredentials: true`. C'est un excellent pattern pour les jobs longs — le polling 1,5s sur `useOptimizeStatus` est en réalité complémentaire (pour la liste des jobs, pas la progression d'un job spécifique).

### 2.5 Schémas Pydantic

⚠️ **Pydantic est très peu utilisé**. La grande majorité des endpoints acceptent des query params et renvoient des `dict` non typés (passés dans `CleanJSONResponse` avec `_clean` qui supprime NaN/Inf). **Aucun `response_model=`** nulle part → pas de validation de schéma de sortie côté OpenAPI. Cette absence de contrat d'API est un point d'amélioration significatif pour la maintenabilité et la documentation auto-générée.

Seulement **6 schémas Pydantic explicites** sont déclarés :

1. **`_AddSymbolBody`** (universe.py) : `symbol, name?, sector?, provider_symbol?`
2. **`_RecipeKey`** (ml.py) : `tf, recipe`
3. **`_PinBody`** (ml.py, extends `_RecipeKey`) : + `version_id`
4. **`_PromoteBody`** (ml.py, extends `_PinBody`) : + `decision: str = "manual"`, `reason: str = "promotion manuelle (UI)"`
5. **`_TrainBody`** (ml.py) : `strategy?, recipe?, symbol="BTC/USDC", symbols?, universe?, max_symbols, compare_solo, tf, as_of?, window_bars?, params: Dict, publish: bool = False`
6. **`_SweepBody`** (ml.py) : `strategy, symbol="BTC/USDC", tf, windows: List[int], as_of?, params: Dict, publish_best: bool = False`

**Recommandation** : étendre Pydantic à tous les endpoints avec `response_model=`. Cela permettra (1) d'auto-générer une doc OpenAPI exploitable par le frontend via `openapi-typescript`, (2) de détecter les régressions d'API au moment des tests, (3) de valider les entrées côté serveur (sécurité), (4) de typer fortement le frontend sans maintenance manuelle du fichier `types/index.ts` (540 lignes actuellement).

### 2.6 Endpoints non consommés par l'UI actuelle — opportunités produit

L'analyse croisée backend ↔ frontend révèle **18 endpoints non consommés** par l'UI Next.js actuelle, dont plusieurs à forte valeur produit. Ces endpoints constituent le socle des User Stories prioritaires de la refonte.

| Endpoint | Valeur produit | Complexité UI | Priorité |
|---|---|---|---|
| `GET /api/stats/daily` | EquityCurve réelle dans `/dashboard`, `/trades`, `/portfolio` | S | **Must (P1)** |
| `GET /api/stats/fees` | Widget ventilation frais dans `/portfolio` | S | Should |
| `GET /api/strategy/{slot_key}/performance` | Drawer performance slot dans `/bots` | M | Should |
| `GET /api/scanner` | Table scan multi-symboles dans `/scanner` | M | Should |
| `GET /api/scanner/opportunities` | Top opportunités dans `/scanner` | S | Should |
| `GET /api/scanner/config` | Widget config scanner | S | Could |
| `GET /api/scanner/setup_series` | Markers setups V11/V12 sur chart | M | Should |
| `GET /api/scanner/signals` | Signaux récents dans `/scanner` | S | Could |
| `GET /api/ml/recipes` | Liste recettes ML dans `/models` | S | Should |
| `GET /api/oos-tracker` | Drawer cône Monte-Carlo dans `/bots` | L | Should |
| `GET /api/universe` | Sélecteur d'univers dans `/data` | S | Should |
| `GET /api/universe/{name}` | Liste membres univers + bars par TF | M | Should |
| `POST /api/universe/{name}/symbols` | Ajouter symbole à un univers | S | Could |
| `DELETE /api/universe/{name}/symbols/{symbol}` | Retirer symbole d'un univers | S | Could |
| `GET /api/config/changelog` | Historique changelog optimizer dans `/audit` | S | Could |
| `GET /api/config/strategy-overrides` | Overrides par stratégie dans `/config` | M | Should |
| `POST /api/config/strategy-params` | Éditeur params strat (base + override per-symbole) | L | Should |
| `POST /api/config/strategy-timeframe` | Toggle strat sur un TF dans `/config` | S | Should |
| `POST /api/config/risk` | Config circuit breakers par slot dans `/config` | M | Should |
| `POST /api/config/notifications/test` | Bouton test envoi notification | S | Should |

### 2.7 Endpoints d'administration, configuration et monitoring

**Health & Monitoring** (3 endpoints sans auth) :
- `GET /health` — `{status, db, exchange, trader}` pour health checks (k8s/systemd)
- `GET /metrics` — exposition Prometheus text/plain pour scrape
- `GET /api/ws/status` — debug hub WebSocket (`{subscribers, history_size, channels}`)

**Audit** (4 endpoints) :
- `GET /api/audit/log` — journal d'audit paginé avec filtres `action`/`actor` (substring)
- `GET /api/audit/log/stats` — compte par action + dernière activité
- `GET /api/audit/results` — derniers résultats OOS optimiseur
- Base SQLite `AuditEvent` alimentée par `audit_log()` dans `app/core/audit_log.py`

**Actions auditées** : `bot.start`, `bot.stop`, `circuit_breaker.reset`, `ml.registry.pin/unpin/set_decision`, `ml.train.publish`, `ml.sweep.publish_best`, `universe.symbol.add/remove`. Cette liste est un excellent socle pour la conformité future (MiCA/AMF/SEC — Sprint 7 reporté).

**Config (lecture)** — 8 endpoints : `/api/config`, `/api/backtest/settings`, `/api/config/changelog`, `/api/config/notifications`, `/api/config/strategy-overrides`, `/api/scanner/config`, `/api/settings/presets`.

**Config (écriture)** — 13 endpoints qui persistent dans `config.yaml` via `_save_yaml(updates_fn)` (round-trip ruamel.yaml préservant les commentaires, thread-safe via verrou UNIQUE partagé avec LiveTrader) : `/api/config/trading`, `/api/config/margin`, `/api/config/capital-allocator`, `/api/config/risk`, `/api/config/strategies`, `/api/config/timeframes`, `/api/config/auto-optimizer`, `/api/config/strategy-params`, `/api/config/strategy-timeframe`, `/api/config/notifications`, `/api/config/notifications/test`, `/api/settings/risk-preset`, `/api/settings/expert-mode`.

**État runtime** — 9 endpoints : `/api/risk`, `/api/circuit-breakers`, `/api/portfolio`, `/api/backtest/status`, `/api/optimize/status`, `/api/data/status`, `/api/candles/stats`, `/api/derivatives/status`, `/api/oos-tracker`, `/api/bots`.

### 2.8 Gestion de la concurrence et rate limits

Le backend gère la concurrence via des **sémaphores in-process** (pas de Celery, pas de Redis, pas de RabbitMQ — tout est in-process avec threads daemon + asyncio + SQLite + Parquet) :
- 1 backtest à la fois (`_bt_semaphore`)
- 1 optimizer à la fois (`_opt_semaphore`)
- 1 replay à la fois (`_rp_semaphore`)
- 2 SMC replay concurrents (`_smc_semaphore`)
- Cache SMC replay 30s (`_smc_replay_cache`, max 16 entrées)
- Mutex écriture config.yaml (`_config_write_lock`)
- Anti-contention : backtest refuse si optimisation en cours (429), et inversement

Les **rate limits** (SlowAPI, `state.limiter`, défaut global 300/min) sont configurées par endpoint :
- bot start/stop : 10/min ; circuit-breakers/reset : 20/min
- backtest run : 10/min, cancel : 30/min ; replay run : 10/min, cancel : 30/min
- optimize start : 5/min, apply : 10/min, cancel/delete : 30/min
- ml train/sweep : 10/min ; notifications test : 5/min
- data refetch : 5/min, backfill-equities : 1/min
- config/* : 30/min ; slots budget/toggle/reset : 30/min, rebalance : 10/min
- risk reset-halt : 10/min ; bots force-active : 30/min, forward-test : 10/min
- universe add/remove : 30/min ; settings risk-preset/expert-mode : 30/min

### 2.9 Notes architecturales critiques

1. **`CleanJSONResponse`** sanitize NaN/Inf via `app.core.sanitize.clean_for_json` — sans cela, JSON invalide en production (NaN n'est pas JSON valide). Bonne pratique défensive.
2. **Format `slot_key`** : `strategy::tf[::symbol]` (ex: `trend_rider::4h::ETH/USDC`) — validé par regex `^[a-z_][a-z0-9_]*::[0-9a-z]+(::[A-Za-z0-9/:\-]+)?$` dans `trades.py`. Cohérent mais à typer fortement côté frontend (un type TS dédié éviterait les erreurs de parsing).
3. **`_global_exception_handler`** capture toute exception non gérée → JSON 500 `{detail: "Erreur interne : {TypeExc}", path}` sans stacktrace côté client (stacktrace loggée serveur avec `exc_info=True`). Sécurité défensive.
4. **Pas de pagination native sur `/api/trades`** : le client demande jusqu'à 1000 trades et filtre/pagine côté UI. Pour les volumes importants, pagination server-side via `offset` est supportée mais non exploitée.
5. **Tests backend** : `tests/test_api_routes.py` (TestClient Starlette) couvre `/api/data/*`, `/api/scanner/fast_analysis`, `/api/portfolio`, `/api/strategy/{slot_key}/performance`. `tests/test_ml_routes.py` couvre `/api/ml/registry/*`, `/api/ml/train`, `/api/ml/sweep`. `tests/test_auth_xff.py` couvre l'auth + X-Forwarded-For. `tests/test_websocket.py` couvre `/ws`. Beaucoup d'endpoints ne sont PAS testés via TestClient (testés indirectement via les modules sous-jacents) — dette de test à combler.

---

## Partie 3 — Inventaire de l'ancienne UI Jinja2 (décommissionnée)

### 3.1 Contexte historique du décommissionnement

L'ancienne UI Jinja2 a été **physiquement supprimée** le 29 juillet 2026 (commit `ecc87b2` — `S6-09 — Suppression physique Jinja2 + Sprint 2/3/4 sélectionnés + fin Sprint 5/6`, auteur `Audit Bot <audit@bot-crypto.local>`). Cette décision, actée par la **décision D4** du Plan Directeur (`docs/PLAN_DIRECTEUR_AMELIORATIONS.md`), met fin à une dualité frontend coûteuse qui durait depuis le 15 juillet 2026 (migration Next.js entamée par les commits `894c3b6`/`4ef55da`/`5c03fe6`).

L'acte officiel est consigné dans `docs/FIN_JINJA2.md` (193 lignes) qui détaille 5 raisons invoquées pour la suppression :

1. **Dualité frontend coûteuse** — deux frontends maintenus en parallèle depuis la migration Next.js ; double l'effort de maintenance, bugs UI dupliqués (UI-01 à UI-12 documentés dans `docs/audit/06-ui-ux.md`).
2. **Endettement technique structurel** — ~10 600 lignes de templates avec duplication massive (`scanner.html` 1426 lignes, `config.html` 1500 lignes, `backtest.html` 1091 lignes, `ml.html` 790 lignes, `optimizer.html` 790 lignes). JS inline sans framework, ce qui rend l'accessibilité et la performance difficiles.
3. **4 bugs P1 critiques non corrigés sur Jinja2** : UI-01 (XSS `data.html` — fonction `esc()` locale n'échappait pas les guillemets → injection via attribut `onclick`), UI-02 (`config.html` mono-symbole malgré moteur per-symbole), UI-03 (`audit.html` écrase OOS entre symboles), UI-04 (`trades.html` filtre Slot à 2 parties au lieu de 3 `strat::tf::symbol`).
4. **Maturité Next.js atteinte** — Next 15 / React 19 / TanStack Query / Radix UI / Tailwind déjà en place avec UX moderne (skeletons, optimistic UI, PWA, i18n FR/EN).
5. **Performance** — Next.js 15 avec SSR + RSC offre TTFB et TTI meilleurs que HTML statique + JS inline, surtout sur mobile.

**Raisons sous-jacentes observées dans le code** : pas de framework JS (tout en vanilla inline, ce qui rend l'ajout de fonctionnalités modernes coûteux) ; CDN externes sans bundling (`lightweight-charts` et `chart.js` chargés via `<script src>` sans tree-shaking ni versionning local) ; aucune authentification côté UI (le cookie `api_key` HttpOnly était posé par `_tpl()` côté serveur — suppression des templates a cassé ce mécanisme, corrigé ensuite par S6-15 avec le proxy same-origin Next.js) ; CORS datant de l'ère Jinja2 (whitelist ne contenait pas le port 3000, corrigé par S6-15/S6-16) ; pas de timeout sur `fetch` (un backend éteint laissait la requête en `SYN_SENT` indéfiniment, corrigé par S6-12 : `apiFetch` borne à 15 s par défaut) ; tests E2E absents côté Jinja2 (Playwright n'a été ajouté qu'avec la Phase 3 Next.js) ; aucun lint frontend (pas d'ESLint configuré pour les templates Jinja2, corrigé par S6-14 sur Next.js) ; accessibilité non outillée (`axe-core` jamais installé malgré règles WCAG écrites dans `DESIGN_SYSTEM.md`, constaté par S6-11).

### 3.2 Arborescence retrouvée — 22 fichiers supprimés

L'archéologie git via `git ls-tree -r --name-only ecc87b2^ -- app/web/` puis `git show ecc87b2^:app/web/templates/<file>` a permis de retrouver l'intégralité des fichiers supprimés au commit parent `365e25c` (ou tout commit antérieur à `ecc87b2`). L'inventaire complet comprend **19 templates Jinja2** (~10 600 lignes) sous `app/web/templates/` et **3 fichiers JS statiques partagés** (227 lignes) sous `app/web/static/js/`. Le fichier `app/web/__init__.py` subsiste (vide, marqueur Python package).

| Fichier | Lignes | Rôle |
|---|---|---|
| `base.html` | 401 | Layout maître + sidebar + topbar + design system CSS + helpers JS (`escHtml`, `apiFetch`, `toast`, `showSkeleton`, `toggleSidebar`, `toggleTheme`) |
| `dashboard.html` | 721 | Page d'accueil `/` — KPIs, equity curve, allocation slots, positions, journal signaux, modals stop/CB/mode |
| `backtest.html` | 1091 | `/backtest` — sidebar config (sym, TF, limit, WF, MC) + tabs stratégies + 5 charts + WF/MC results + modal fullscreen |
| `scanner.html` | 1426 | `/scanner` — filtres (régime, ADX, ATR, RSI) + table sortable + chart 4 panneaux (price/vol/RSI/MACD) + toggles EMA/BB/SR/SMC + opportunities + Fast Analyse + prédictions |
| `config.html` | 1500 | `/config` — 9 accordéons (stratégies actives, TFs, notifications TG/WA/Email, auto-opt, trading params, CBs par slot, margin, params par stratégie) |
| `ml.html` | 790 | `/ml` — optimiseur ML (méthode, trials, workers, early stop, TFs) + jobs cards avec top5/avant-après |
| `optimizer.html` | 790 | `/optimizer` — optimiseur stratégies non-ML (similaire à ml.html mais filtré) |
| `models.html` | 519 | `/models` — registre modèles, entraîner un modèle, window sweep, pin/promote, AUC par régime |
| `replay.html` | 814 | `/replay` — rejeu bougie par bougie avec contrôles play/pause/speed + sidebar stats |
| `trades.html` | 429 | `/trades` — tabs (tous/slot/paire) + filtres + pagination + equity & daily charts + CSV export |
| `bots.html` | 486 | `/bots` — kanban 4 colonnes (Candidat/Essai/Actif/Retiré) + drawer latéral + frise cycle de vie + cône MC |
| `smartgraph.html` | 546 | `/smartgraph` — chart SMC dédié (zones ombrées, zigzag, OB, FVG, voids, breakers, rejections, trendlines, cycle) |
| `smartreplay.html` | 477 | `/smartreplay` — rejeu SMC bougie par bougie avec calques toggleables |
| `audit.html` | 302 | `/audit` — TOP par TF + historique changelog + export CSV |
| `compare.html` | 206 | `/compare` — comparatif multi-stratégies (même fenêtre) + chips + table best-value surlignée + export CSV |
| `derivatives.html` | 202 | `/derivatives` — 4 charts (funding, OI, LSR, taker buy/sell) + stat chips + sélecteur période |
| `portfolio.html` | 174 | `/portfolio` — KPIs + lifecycle strip + allocation barres avec shadow targets + feed notifications + halt banner |
| `data.html` | 119 | `/data` — cache OHLCV + fetch manuel + lien ↗ Analyser vers /scanner |
| `settings.html` | 87 | `/settings` — presets de risque + mode expert toggle |
| `static/js/alloc.js` | 56 | `renderAllocGrid(container, slots, opts)` — style `card` (dashboard) ou `row` (portfolio avec barre cible « shadow allocation ») |
| `static/js/ml-optimizer-shared.js` | 110 | `TF_INFO`, `tfMeta`, `toggleTfCheck`, `renderTfChecks`, `cancelJob`, `applyJob`, `deleteJob` |
| `static/js/smc-chart.js` | 61 | `window.SmcChart = { FILL, ZonesPrimitive }` — primitive plugin lightweight-charts v4 pour dessiner des rectangles [t1,t2]×[bottom,top] |

### 3.3 Graphiques implémentés dans l'ancienne UI

L'ancienne UI utilisait deux librairies de charts : **lightweight-charts v4.2.0** (TradingView) chargé via CDN `https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js` avec `integrity="sha384-OK7vELvjHdhUFi31JYioPIcRHTROLdcDa6ZsNWgvgLaKj+9JqhU0Ad8g4wz3CXjA"`, et **chart.js v4.4.0** (utilisé uniquement pour le scatter chart du backtest). La nouvelle UI Next.js utilise les mêmes librairies (recharts + lightweight-charts), ce qui facilite la réimplémentation.

| Type | Librairie | Page Jinja2 | Données affichées | Endpoint source | Statut Next.js |
|---|---|---|---|---|---|
| Aire (equity curve) | lightweight-charts `addAreaSeries` | `dashboard.html` | Courbe d'équité live | `/api/status` | ❌ **Simulée** (sin/cos) |
| Histogramme (PnL distrib) | lightweight-charts `addHistogramSeries` | `dashboard.html` | Distribution PnL des 30 derniers trades | `/api/trades?limit=30` | ❌ Manquante |
| Histogramme (PnL journalier) | lightweight-charts | `dashboard.html` | PnL par jour sur 30 j | `/api/stats/daily?days=30` | ❌ Manquante |
| Ligne (equity) | lightweight-charts | `backtest.html` | Equity backtest + Buy&Hold dashed | `/api/backtest` POST | ✅ Présente |
| Histogramme (PnL distrib) | lightweight-charts | `backtest.html` | Distribution PnL par trade | idem | ✅ Présente |
| Ligne (cumul trades) | lightweight-charts | `backtest.html` | Cumul des trades | idem | ❌ Manquante |
| Ligne/Candlestick (toggle) | lightweight-charts | `backtest.html` | Prix OHLCV + markers entry/exit | idem | ✅ Présente |
| Scatter | Chart.js `type:'scatter'` | `backtest.html` | Trades sur carte prix×temps | idem | ❌ Manquante |
| Ligne (fullscreen) | lightweight-charts | `backtest.html` modal | Prix plein écran avec reset zoom | idem | ❌ Manquante |
| Candlestick + Line (toggle) | lightweight-charts | `scanner.html` | Prix OHLCV + EMA20/50/100/150/200 + BB + Sup/Rés + markers V11/V12 + zones SMC | `/api/scanner/chart`, `/api/scanner/smc`, `/api/scanner/setup_series` | ⚠️ Partiel |
| Histogramme (volume) | lightweight-charts | `scanner.html` | Volume par bougie | `/api/scanner/chart` | ❌ Manquante |
| Ligne (RSI 14) | lightweight-charts | `scanner.html` | RSI avec lignes OB/OS à 70/30 | idem | ❌ Manquante |
| Ligne (MACD) | lightweight-charts | `scanner.html` | MACD (12,26,9) — ligne, signal, histogramme | idem | ❌ Manquante |
| Candlestick + zones SMC | lightweight-charts + `SmcChart.ZonesPrimitive` | `smartgraph.html` | Candles + 11 calques SMC (OB, FVG, voids, breakers, rejections, pools), zigzag, trendlines, canal régression, HVN/LVN/POC, projections cycle, signal entry/SL/TP | `/api/scanner/chart`, `/api/scanner/smc` | ✅ Présente (14 calques vs 11) |
| Candlestick + zones SMC (replay) | lightweight-charts + `SmcChart.ZonesPrimitive` | `smartreplay.html` | Candles + calques SMC rejeu causal bougie par bougie | `/api/scanner/smc_replay` | ✅ Présente |
| Candlestick (replay) | lightweight-charts | `replay.html` | Bougie par bougie + markers signaux stratégie overlay | `/api/backtest` POST | ✅ Présente |
| Aire (equity) | lightweight-charts | `trades.html` | Courbe d'équité historique | `/api/trades?limit=5000` | ❌ Manquante |
| Histogramme (PnL journalier) | lightweight-charts | `trades.html` | PnL journalier sur 90 j | `/api/stats/daily?days=90` | ❌ Manquante |
| Ligne (4 charts dérivés) | lightweight-charts | `derivatives.html` | Funding rate, Open Interest, Long/Short ratio, Taker buy/sell ratio (avec overlay prix) | `/api/derivatives/data` | ✅ Présente |

### 3.4 Inventaire détaillé par élément — Statut recommandé pour la refonte

Le tableau ci-dessous évalue chaque élément significatif de l'ancienne UI selon 4 critères : (1) sa description technique, (2) le statut recommandé pour la refonte (**À reprendre tel quel** / **À adapter** / **À moderniser** / **À abandonner**), (3) la complexité de reprise en jours-homme (S/M/L/XL), (4) la valeur produit (High/Medium/Low).

#### Vues / pages

| Élément | Description | Statut recommandé | Complexité | Valeur produit |
|---|---|---|---|---|
| Dashboard Live (721 L) | KPIs + equity curve + distribution PnL + PnL journalier + perf par stratégie + positions + trades live + journal signaux avec rejets + risk gauges | À moderniser | L (5j) | High |
| Backtest (1091 L) | Config + 5 charts + WF folds + MC P5/P50/P95 + scatter + fullscreen modal | À adapter | L (5j) | High |
| Scanner (1426 L) | Filtres + table sortable + 4-paneaux chart + toggles indicateurs + opportunities + Fast Analyse + prédictions | À moderniser | XL (8j) | High |
| Config (1500 L) | 9 accordéons : stratégies, TFs, notifications, auto-opt, trading params, CBs par slot, margin, params par stratégie | À moderniser | XL (8j) | High |
| Optimizer ML (790 L) | Optimiseur ML + jobs cards avec top5 + avant/après | À adapter | M (3j) | Medium |
| Optimizer non-ML (790 L) | Similaire à ml.html mais filtré | À fusionner avec /ml | M (3j) | Medium |
| Models / registre ML (519 L) | Registre + entraîner + window sweep + pin/promote + AUC par régime + Spearman | À adapter | L (5j) | High |
| Replay (814 L) | Rejeu bougie par bougie + contrôles + sidebar stats + signal log | À adapter | M (3j) | Medium |
| Trades (429 L) | Tabs + filtres + pagination + equity & daily charts + CSV export | À adapter | M (3j) | High |
| Bots / Kanban (486 L) | Kanban 4 colonnes + drawer + frise cycle de vie + cône MC | À adapter | L (5j) | High |
| Smart Graph (546 L) | Candlestick + 11 calques SMC + table plans + lecture du marché | À adapter | L (5j) | High |
| Smart Replay (477 L) | Rejeu SMC + calques toggleables + contrôles | À adapter | M (3j) | Medium |
| Audit (302 L) | TOP par TF + historique changelog + export CSV | À adapter | S (1j) | Medium |
| Compare (206 L) | Comparatif multi-stratégies + chips + table best-value + export CSV | À adapter | M (3j) | Medium |
| Derivatives (202 L) | 4 charts + stat chips + sélecteur période | À adapter | S (1j) | Medium |
| Portfolio (174 L) | KPIs + lifecycle strip + allocation barres avec shadow + feed notifications + halt banner | À adapter | M (3j) | High |
| Data (119 L) | Cache OHLCV + fetch manuel + lien ↗ Analyser | À adapter | S (1j) | Medium |
| Settings (87 L) | Presets de risque + mode expert toggle | À adapter | S (1j) | Medium |

#### Composants UI réutilisables

| Élément | Description | Statut recommandé | Complexité | Valeur produit |
|---|---|---|---|---|
| `renderAllocGrid` (alloc.js) | 2 styles : `card` (dashboard, cliquable→/bots) et `row` (portfolio avec barre cible amber « shadow allocation ») | À reprendre tel quel | S (1j) | High |
| `renderJobCard` (ml/optimizer) | Carte de job optimisation avec header, progress bar, metrics-row 5 colonnes, ba-grid avant/après, params-block, top5-table, apply-row, warn-boxes | À adapter | M (3j) | High |
| `TF_INFO` + `tfMeta` + `renderTfChecks` (ml-optimizer-shared.js) | Checkboxes de TFs avec note de capacité (`~14j · max ~8000`) | À reprendre tel quel | S (1j) | Medium |
| Kanban 4 colonnes (bots.html) | Cards bot avec dot, badges (Forcé/Off/Pausé), meta trades, budget-mini, PnL 7j | À adapter | M (3j) | High |
| Drawer latéral (bots.html) | Coulissant depuis la droite (460px max), backdrop fermable | À adapter (utilise Radix Dialog) | M (2j) | High |
| Risk gauges (dashboard.html) | `.risk-gauge-card` avec `.gauge-track` + `.gauge-fill` + libellé limite | À reprendre tel quel | S (1j) | High |
| Allocation bar avec shadow targets (portfolio.html) | `.alloc-bar` + `.alloc-cur` + `.alloc-tgt` (trait amber superposé) | À reprendre tel quel | S (1j) | High |
| Frieze de cycle de vie (bots.html) | 4 étapes (candidat→essai→actif→retiré) avec dots colorés | À reprendre tel quel | S (1j) | Medium |
| Cône Monte-Carlo (bots.html) | `.cone-track` + `.cone-band` (IC) + `.cone-mark.sim/.live/.live.bad` + `.cone-zero` | À reprendre tel quel | M (2j) | High |
| Calques toggleables (smartgraph/smartreplay) | Checkboxes avec accent-color custom par calque | À adapter | S (1j) | Medium |
| Contrôles de replay (smartreplay/replay) | `.rp-ctl` play/pause/jump + `.speed-btns` + slider scrubbing | À adapter | S (1j) | Medium |
| Helpers JS base.html (`escHtml`, `fmtSign`, `fmtPrice`, `safeSide`, `apiFetch`, `toast`, `showSkeleton`, `toggleSidebar`, `toggleTheme`) | Utilitaires partagés | À abandonner (remplacés par `lib/utils.ts` Next.js) | — | — |
| Layout CSS base.html (`.panel`, `.btn-*`, `.table-card`, `.tag-*`, `.status-pill`, `.mode-pill`, `.toggle`, `.modal-overlay`, `.toast`, `.skeleton`, `.spin`, `.info-box`, `.warn-box`, `.help-tip`) | Design system CSS custom | À abandonner (remplacé par Tailwind + Radix) | — | — |

#### Fonctionnalités interactives

| Élément | Description | Statut recommandé | Complexité | Valeur produit |
|---|---|---|---|---|
| Filtres Scanner (régime, ADX, ATR, RSI lo-hi) + persistance localStorage | Barre sticky avec reset button | À reprendre tel quel | M (2j) | High |
| Filtres Trades (paire, slot 3-parties, direction) + bouton ✕ Effacer | Barre sticky | À reprendre tel quel | S (1j) | High |
| Tri colonnes Scanner/Compare | Clique sur th, `.sort-arrow` + `aria-sort` | À adapter (utiliser `@tanstack/react-table` déjà installé) | S (1j) | Medium |
| Export CSV trades | Sélecteur limite (500/1000/5000/Tous) | À reprendre telquel | S (1j) | High |
| Export CSV audit/compare | Côté client via table rendue | À reprendre tel quel | S (1j) | Medium |
| Export JSON + PDF backtest | Boutons ↓ JSON et ↓ PDF | À moderniser (PDF via jsPDF) | M (2j) | Medium |
| Tabs `trades.html` (3 tabs) + `backtest.html` (tab-bar dynamique par stratégie) + `config.html` (tabs mobiles) | Role="tablist", aria-selected | À adapter (Radix Tabs) | S (1j) | Medium |
| Modals `dashboard.html` (stop/CB/mode) + `backtest.html` (fullscreen) | `.modal-overlay` + `.modal-box` + variants | À adapter (Radix Dialog) | M (2j) | High |
| Drawer `bots.html` | Coulissant depuis la droite (460px) | À adapter (Radix Dialog side variant) | M (2j) | High |
| Accordéons `config.html` (9 panneaux cliquables) | `toggleLeftAcc()` | À adapter (Radix Accordion ou Collapsible) | S (1j) | Medium |
| `<details class="help">` | Panneaux d'aide repliables | À reprendre tel quel | S (0,5j) | Medium |
| Range picker replay (1-24 mois, hint dynamique) | `<input type="range">` | À adapter (Radix Slider) | S (1j) | Low |
| Slider scrubbing smartreplay | `<input type="range">` | À adapter (Radix Slider) | S (1j) | Medium |
| Date picker `models.html` (`#tr-asof` ISO) | Champ texte ISO | À moderniser (date picker custom) | M (2j) | Low |
| Pagination trades/backtest (20/page) | `.pagination` | À adapter (server-side via offset) | M (2j) | Medium |
| Skeletons `showSkeleton(el, lines, opts)` | `opts.cards` ou `opts.colspan` | À reprendre tel quel (manquants dans Next.js) | M (3j) | High |
| Polling dashboard 15s + health badge | `setInterval(refresh, 15000)` + `/health` | À adapter (TanStack Query `refetchInterval`) | S (1j) | Medium |
| Bandeau API error `#api-error-bar` role="alert" | `_apiErrorCount` | À reprendre (déjà fait via `ApiStatusBanner`) | — | — |
| Latency display `#api-latency` | Affiche latence API | À reprendre tel quel | S (0,5j) | Low |
| Keyboard shortcuts replay (Espace=play/pause, ←→=±1 barre) + Echap modals | Handlers globaux | À reprendre tel quel | S (1j) | Medium |
| Theme dark/light pré-paint | `toggleTheme()` + localStorage + application pré-paint | À reprendre (bug à corriger côté Next.js) | S (1j) | High |
| ARIA labels + `prefers-reduced-motion` + print styles | A11y complète | À reprendre (à outiller avec axe-core) | M (2j) | High |
| Validation `config.html` (`validateField`) + avertissements contextuels | `.field-error` + `.field-error-msg.visible` | À adapter (Zod + react-hook-form) | M (3j) | Medium |

#### Données spécifiques à souligner (vs UI actuelle)

L'inventaire comparatif révèle **27 fonctionnalités notables de l'ancienne UI Jinja2** dont certaines ne sont pas (ou partiellement) réimplémentées dans la nouvelle UI Next.js. Ces gaps fonctionnels constituent autant d'opportunités produit pour la refonte.

| # | Fonctionnalité ancienne UI | Page | Réimplémentée Next.js ? | Action |
|---|---|---|---|---|
| 1 | Journal des signaux avec rejets (checkbox « Voir rejetés ») | dashboard | ⚠️ Partiel (SignalsFeed sans filtre rejets) | À compléter |
| 2 | Cône d'edge Monte-Carlo (bande IC + marks sim/live/zero) | bots | ❌ Non | À reprendre |
| 3 | Frise de cycle de vie (Candidat→Essai→Actif→Retiré) | bots | ⚠️ Partiel (Kanban sans frise) | À compléter |
| 4 | Walk-Forward Analysis avec folds OOS | backtest | ❌ Non | À reprendre |
| 5 | Monte-Carlo (200 runs, IC 95%, P5/P50/P95) | backtest | ❌ Non | À reprendre |
| 6 | 11 calques SMC toggleables | smartgraph/smartreplay | ✅ Oui (14 calques) | OK |
| 7 | Table plans recommandés (Statut/Sens/Setup/Entry/SL/TP/Gain/RR/Dist/Score) | smartgraph | ❌ Non | À reprendre |
| 8 | Diagnostic ML avancé (features importance, AUC par régime, Spearman) | models | ⚠️ Partiel | À compléter |
| 9 | Window sweep (compare fenêtres d'entraînement) | models | ✅ Oui | OK |
| 10 | Dry-run vs Publish (entraîner avec/sans gate réel) | models | ✅ Oui | OK |
| 11 | Shadow allocation (barre cible amber superposée) | portfolio | ❌ Non | À reprendre |
| 12 | Kill-switch acquittement (bandeau halt + bouton) | portfolio | ⚠️ Partiel | À compléter |
| 13 | Notifications 3 niveaux (info/warning/critical, bordure colorée) | portfolio | ✅ Oui | OK |
| 14 | Opportunités scanner (Top paires par score combiné) | scanner | ❌ Non | À reprendre |
| 15 | Fast Analyse (screening indicateurs, split IS/OOS, sensibilité frais) | scanner | ✅ Oui | OK |
| 16 | Prédictions par stratégie (panel dédié) | scanner | ❌ Non | À reprendre |
| 17 | Toggle Setups V8/V11/V12 (markers entry + TP/SL) | scanner | ❌ Non | À reprendre |
| 18 | Circuit breakers par slot (pertes consécutives max, DD journalier max par slot) | config | ❌ Non (endpoint `/api/config/risk` non consommé) | À reprendre |
| 19 | 3 canaux de notification (Telegram, WhatsApp CallMeBot/Twilio, Email SMTP) avec test button | config | ⚠️ Partiel (config sans test button) | À compléter |
| 20 | Auto-optimisation planifiée (toggle + intervalle heures) | config | ✅ Oui | OK |
| 21 | Margin spot (configuration margin borrow) | config | ✅ Oui | OK |
| 22 | Presets de risque (3 cartes : conservateur/modéré/agressif) | settings | ✅ Oui | OK |
| 23 | Mode expert (toggle affiche seuils avancés dans /config) | settings | ✅ Oui | OK |
| 24 | Lien croisé /data ↔ /scanner (UI-09) | data/scanner | ❌ Non | À reprendre |
| 25 | Fullscreen chart backtest (modal plein écran + reset zoom) | backtest | ❌ Non | À reprendre |
| 26 | Polling 15s + health badge + latency display | dashboard | ⚠️ Partiel (polling via TanStack mais pas de latency display) | À compléter |
| 27 | Distribution PnL (histogramme 30 derniers trades) | dashboard | ❌ Non | À reprendre |

### 3.5 Raison probable du décommissionnement — synthèse

L'analyse croisée des commits, de `docs/FIN_JINJA2.md` et du code source permet de classifier les raisons en trois catégories :

**Raisons techniques (prépondérantes)** : endettement technique structurel (~10 600 lignes avec duplication massive), JS vanilla inline sans framework (ajout de fonctionnalités modernes coûteux), CDN externes sans bundling (pas de tree-shaking), 4 bugs P1 critiques non corrigés (XSS, mono-symbole, OOS écrasé, filtre Slot 2 parties), absence de tests E2E, absence de lint frontend, accèsibilité non outillée. Ces raisons techniques ont rendu la maintenance plus coûteuse que la migration.

**Raisons produit (secondaires)** : maturité Next.js atteinte avec UX moderne (skeletons, optimistic UI, PWA, i18n FR/EN), performance perçue meilleure (SSR + RSC), alignement avec la vision cible 5 pages (qui suppose une SPA moderne). La métaphore « gérant de fonds employant des bots-traders » de `VISION_CIBLE_BOTS_AUTONOMES.md` est plus naturelle à exprimer en React qu'en Jinja2.

**Raisons de maintenance (déterminantes)** : dualité frontend coûteuse (double effort, bugs UI dupliqués UI-01 à UI-12), équipe contrainte en capacité (1 dev senior ou 2 mid selon le plan directeur), nécessité de désendetter avant l'industrialisation Sprint 7 (conformité MiCA/AMF/SEC). La suppression physique a été anticipée par rapport au planning initial (S6-09 au lieu de fin Sprint 6) pour réduire la charge mentale et forcer la finalisation de la migration.

**Conclusion** : le décommissionnement est une décision produit rationnelle, justifiée par le ratio coût/valeur devenu défavorable à Jinja2 après l'atteinte de la maturité Next.js. Les fonctionnalités riches de l'ancienne UI ne sont pas perdues — elles constituent le backlog de réimplémentation priorisé dans la Partie 4.

---

## Partie 4 — Plan de refonte et vision produit agile

### 4.1 Vision produit

#### Personas cibles

L'audit des docs (`docs/SYNTHESE_VISION_PRODUIT.md`, `docs/VISION_CIBLE_BOTS_AUTONOMES.md`, `docs/audit-externe/AUDIT_TECHNIQUE_BOT_CRYPTO_V12.md` §6) révèle un persona unique formel : le **trader individuel sophistiqué francophone** qui veut déléguer la sélection/évaluation/allocation des stratégies à un système automatisé. Le JTBD explicite est : « Quand je veux trader crypto et actions algorithmiquement, je veux déléguer la sélection/évaluation/allocation des stratégies à un système automatisé, pour générer de l'alpha sans avoir à surveiller et ajuster manuellement chaque stratégie. » La métaphore porteuse est **« l'utilisateur est le gérant d'un fonds qui emploie des bots-traders »**.

Pour la refonte, nous dérivons 3 personas opérationnels qui structurent le backlog :

**Persona 1 — Tristan, trader débutant crypto (35% des utilisateurs cibles)**

Tristan a 28 ans, découvre le trading crypto depuis 6 mois, a déjà fait quelques trades manuels sur Binance/OKX avec des résultats mitigés. Il cherche un système automatisé pour déléguer l'exécution tout en apprenant. Il a un capital de 500 à 5 000 €, accepte une volatilité modérée, veut comprendre les décisions du bot sans être noyé dans le jargon technique. Il utilise principalement son smartphone le soir et son PC le week-end. Il a besoin de : presets de risque compréhensibles (Prudent/Équilibré/Agressif), tooltips pédagogiques systématiques (OOS, Sharpe, fourchette, overfit), onboarding guidé à la première visite, alerts claires quand quelque chose ne va pas, dashboard simple qui ne montre que l'essentiel (capital, PnL, positions, alertes). Son principal point de friction avec l'UI actuelle : la navigation à 23 pages est écrasante, le jargon SMC/ICT/WF/MC est opaque, le bouton "mode expert" ne révèle pas assez clairement ce qui va changer.

**Persona 2 — Aïcha, trader avancé (60% des utilisateurs cibles)**

Aïcha a 38 ans, 8 ans d'expérience en trading algorithmique, connaît Python et lit le code du repo. Elle veut un contrôle fin sur les paramètres de chaque stratégie, accès au backtest avec Walk-Forward et Monte-Carlo, optimiseur bayésien avec application directe dans config.yaml, registre ML versionné avec gate de promotion, scanner SMC/ICT complet avec calques toggleables, cycle de vie automatique des bots avec override manuel. Elle a un capital de 10 000 à 100 000 €, paper mode par défaut puis bascule en live après validation. Elle utilise principalement son PC (dual-screen), parfois son smartphone pour surveiller. Elle a besoin de : mode expert opt-in qui révèle les seuils avancés, exports CSV/JSON/PDF pour analyse externe, API endpoints bien documentés (OpenAPI/Swagger), keyboard shortcuts (Espace=play/pause, ←→=±1 barre), composants denses (tables triables, charts multi-panneaux), accès rapide Cmd+K. Son principal point de friction avec l'UI actuelle : EquityCurve simulée (perte de confiance), `window.confirm` pour promote/reject ML (UX non professionnelle), 8 packages Radix installés mais non wrappés (incohérence visuelle), endpoints non consommés (override per-symbole, params strat, CBs par slot).

**Persona 3 — Admin-sys / DevOps (5% des utilisateurs cibles, mais critique)**

Marc a 42 ans, gère le déploiement Oracle Cloud Always Free, configure nginx/systemd/watchdog, surveille les métriques Prometheus, gère les backups SQLite, applique les mises à jour de sécurité. Il a besoin de : page `/health` et `/metrics` exposées sans auth pour monitoring externe (Prometheus, Uptime Kuma), audit log consultable et exportable (conformité future MiCA/AMF/SEC), journal d'audit filtrable par action/actor, page de configuration des notifications (Telegram/WhatsApp/Email) avec test button, gestion des univers d'instruments (SBF120, etc.), backfill yfinance async avec polling de statut. Son principal point de friction avec l'UI actuelle : absence de page d'admin dédiée (les fonctions admin sont dispersées dans `/config`, `/data`, `/audit-log`), pas de vue consolidée de la santé système, endpoints d'admin non regroupés dans la navigation.

#### Proposition de valeur de l'UI

La proposition de valeur de l'UI refondue s'articule autour de 3 piliers qui découlent directement des personas et de la `VISION_CIBLE_BOTS_AUTONOMES.md` :

1. **Pilote ton fonds de bots en un coup d'œil** — Le dashboard devient un cockpit de gérant de fonds : santé du portefeuille en français courant (« Ton portefeuille a généré +2,3% cette semaine, porté par trend_rider sur BTC/4h. 2 bots en essai, 0 en alerte. »), equity curve réelle alimentée par `/api/stats/daily`, allocation par bot avec shadow targets, jauge de risque du jour vs limites, positions ouvertes regroupées par bot, fil d'activité miroir Telegram, bouton unique d'arrêt d'urgence visible en permanence.

2. **Recrute, évalue, dote tes bots comme un pro** — La page Mes Bots devient un véritable outil de gestion RH des bots : kanban par lifecycle (Candidats/Essai/Actifs/Retirés), fiche bot avec cône Monte-Carlo vs réel (visualisation clé de la `VISION_CIBLE_BOTS_AUTONOMES.md`), indicateur de confiance (🟢🟠🔴) basé sur forward-test + réalisation live, budget continu slider, actions contextuelles (force-active, forward-test, reset CB, override manuel). Chaque bot est une entité vivante avec son histoire et ses performances.

3. **Labore tes stratégies sans te noyer dans la technique** — Le Laboratoire (fusion de Backtest + Optimizer + ML/Models) guide l'utilisateur vers le verdict en clair : « Analyser » → verdict lisible (« Cette stratégie a un edge significatif sur BTC/4h avec 47 trades OOS, Sharpe 1,8, mais une concentration de risque en régime de marché baissier. Recommandation : essai avec budget 5%. ») → bouton unique « Créer le bot (Essai) ». Le mode expert opt-in révèle les options avancées (méthode d'optimisation, n_trials, walk-forward folds, MC runs) sans polluer l'expérience débutant.

#### Principes de design directeurs

Cinq principes structurent toutes les décisions de design de la refonte. Chaque User Story, chaque wireframe, chaque composant doit respecter ces principes.

**Principe 1 — Clarté radicale sur l'état du système.** Un trader ne devrait jamais avoir à deviner si le bot tourne, si le backend est joignable, si des positions sont ouvertes, si un circuit breaker est actif. L'état système est visible en permanence dans la topbar (badge PAPER/LIVE, status running, WS status, health dots) et confirmé visuellement par des animations subtiles (pulse-glow sur le bouton stop quand bot running, flash green/red sur KPICard au changement). Aucune erreur silencieuse : `ApiStatusBanner` global, `ErrorState` avec bouton retry contextualisé, `LoadingState` avec `aria-live="polite"`.

**Principe 2 — Verdict avant options.** Chaque page qui produit une analyse (backtest, optimizer, scanner) doit afficher le verdict en clair avant les options techniques. Le débutant lit le verdict et décide ; l'expert déploie les options. Cela suppose un mode expert opt-in bien clair (toggle dans `/settings` + indicateur visuel dans la topbar quand activé) qui révèle les seuils avancés sans casser l'expérience débutant.

**Principe 3 — Données réelles, jamais simulées.** Aucun composant ne doit afficher des données mock/simulées en production. Le bug EquityCurve (sin/cos au lieu de `/api/stats/daily`) est un anti-pattern à éradiquer. Si une donnée n'est pas disponible, afficher un `EmptyState` explicatif (« Pas encore de trades — lance un backtest pour voir l'equity curve ») plutôt qu'une simulation trompeuse.

**Principe 4 — Confiance par la transparence.** Chaque décision du bot (entrée/sortie de trade, halt, reset CB, promotion ML) doit être traçable et expliquée en une phrase. Le journal des signaux avec rejets (afficher POURQUOI un signal a été rejeté : seuil, budget, corrélation, risque) est un pattern à réimplémenter. L'audit log doit être consultable et filtrable. Les tooltips pédagogiques systématiques (OOS, Sharpe, fourchette, overfit, edge) sont une exigence produit.

**Principe 5 — Performance perçue = confiance.** Un trader qui voit un spinner infini perd confiance dans le système. Skeletons sur toutes les pages (pas seulement les 4 avec `QueryBoundary`), polling adaptatif (1,5s pour optimizer live, 3s status, 30s ML), WebSocket avec reconnexion backoff exponentiel, prefetch des routes au hover, optimistic UI sur les mutations (start/stop bot, apply optimize), transition animations sur les changements d'état (framer-motion enfin utilisé).

### 4.2 Roadmap agile — Vision structurelle

La roadmap s'inscrit dans la continuité du **Plan Directeur existant** (`docs/PLAN_DIRECTEUR_AMELIORATIONS.md`, 8 sprints × 2 semaines, 173 SP, accepté le 29/07/2026). Les Sprints 0 à 6 sont déjà en grande partie exécutés (Sprint 0 ✅, Sprint 1 🟡 partiel, Sprint 2 🟡 partiel, Sprint 3 ✅ pour l'essentiel, Sprint 4 ✅ pour l'essentiel, Sprint 5 ✅ fait, Sprint 6 🟡 partiel). Le Sprint 7 (Production & Conformité) est reporté.

La présente roadmap étend la vision sur **12 sprints (24 semaines, ~6 mois)** en se concentrant spécifiquement sur l'UI/UX, la consolidation des 23 pages en 5 pages méta, la résorption du gap fonctionnel vs l'ancienne UI Jinja2, et la mise en conformité accessibilité. Elle complète le plan directeur sans le remplacer — les sprints backend (S1-S4) sont supposés terminés ou en cours.

#### Epics (5 grands thèmes)

| Epic | Titre | Vision | SP total | Sprints |
|---|---|---|---|---|
| E1 | **Fondations & Dette technique** | Éradiquer les bugs P1, wrapper Radix, étendre QueryBoundary, nettoyer deps mortes, outiller a11y | 38 | S0-S2 |
| E2 | **Consolidation navigation 5 pages** | Transformer 23 pages en 5 pages méta (Portefeuille / Mes Bots / Laboratoire / Marché / Réglages) | 52 | S3-S6 |
| E3 | **Parité fonctionnelle avec ancienne UI** | Réimplémenter les 27 fonctionnalités manquantes (cône MC, journal rejets, WF folds, shadow alloc, etc.) | 41 | S5-S9 |
| E4 | **Expérience trader avancé** | Mode expert, keyboard shortcuts, exports riches, API endpoints non consommés, drawer performance slot | 28 | S7-S10 |
| E5 | **Industrialisation & Conformité** | Tests visuels (Chromatic/Percy), tests a11y (axe-core CI), onboarding utilisateur, i18n EN complet, conformité PSAN | 22 | S10-S12 |


#### Backlog structuré — Features et User Stories

Le backlog ci-dessous structure les User Stories par Epic, avec priorisation MoSCoW (Must / Should / Could / Won't), estimation de complexité (S=0,5-2j, M=2-5j, L=5-10j, XL=10j+), et dépendances explicites. Le format des User Stories suit le standard : « En tant que [persona], je veux [action] afin de [bénéfice] » avec critères d'acceptation mesurables.

---

##### EPIC E1 — Fondations & Dette technique (Sprints 0-2, 38 SP)

**Feature E1-F1 — Bug fixes P1 (Sprint 0, 8 SP)**

| ID | User Story | MoSCoW | Complexité | Dépendances | Critères d'acceptation |
|---|---|---|---|---|---|
| E1-F1-US1 | En tant que trader, je veux voir l'equity curve réelle alimentée par `/api/stats/daily` afin de ne pas être trompé par des données simulées | Must | S (0,5j) | — | (1) EquityCurve consomme `useDailyStats(30)` (2) Chart affiche PnL cumulé réel (3) Tests e2e vérifient la non-régression |
| E1-F1-US2 | En tant que trader, je veux que le light theme persiste entre les refreshs afin de pouvoir utiliser l'UI en plein jour | Must | S (0,5j) | — | (1) `Providers.tsx` ne force plus `dark` au mount (2) `getStoredTheme()` applique la classe au `<html>` avant paint (3) Tests e2e vérifient la persistance |
| E1-F1-US3 | En tant que trader, je veux que le flash green/red sur KPICard se déclenche à chaque changement de valeur afin de percevoir les variations en temps réel | Should | S (0,5j) | — | (1) `prevValue` mis à jour via `useEffect` sur chaque `value` (2) Flash déclenche sur diff > seuil (3) Tests visuels valident l'animation |
| E1-F1-US4 | En tant que trader, je veux que `ApiStatusBanner` ne déclenche pas de warning React "adjusting state during render" afin d'éviter les bugs strict mode | Should | S (0,5j) | — | (1) State update déplacée dans `useEffect` (2) Pas de warning en dev strict mode (3) Comportement fonctionnel identique |
| E1-F1-US5 | En tant que trader, je veux que le footer sidebar affiche l'état réel du WS (Connected/Disconnected/Reconnecting) afin de ne pas être trompé | Should | S (0,5j) | — | (1) Footer consomme `useWebSocket()` (2) 3 états visuels distincts (3) Tests e2e vérifient la cohérence |
| E1-F1-US6 | En tant que trader, je veux que `NEXT_PUBLIC_WS_URL` soit configurable via env en production afin que le WS se connecte au bon backend | Must | S (0,5j) | — | (1) `next.config.mjs` ne hardcode plus localhost (2) Variable env obligatoire en prod (3) Build échoue si non définie en prod |
| E1-F1-US7 | En tant que trader, je veux que l'icône PWA `/icon-192.png` existe afin que l'installation PWA fonctionne sur iOS | Could | S (0,5j) | — | (1) Fichier généré dans `/public/` (2) `appleWebApp.icon` valide (3) Installation iOS testée |
| E1-F1-US8 | En tant que trader, je veux que `/scanner` et `/compare` utilisent TanStack Query afin de bénéficier du cache, retry et invalidation | Should | M (2j) | — | (1) `useFastAnalysis` et `useRunCompare` créés (2) Cache 5s (3) Retry sur erreur (4) Tests e2e validés |

**Feature E1-F2 — Design system complet (Sprint 1-2, 18 SP)**

| ID | User Story | MoSCoW | Complexité | Dépendances | Critères d'acceptation |
|---|---|---|---|---|---|
| E1-F2-US1 | En tant que développeur, je veux une bibliothèque de composants UI basée sur Radix wrapped en style shadcn afin d'avoir une cohérence visuelle sur toutes les pages | Must | L (8j) | — | (1) Composants `Select`, `Dialog`, `Tabs`, `Switch`, `Dropdown`, `Accordion`, `Label`, `Separator`, `ScrollArea` créés dans `components/ui/` (2) Tests Storybook (3) Migration de 5 pages pilotes (4) Documentation d'usage |
| E1-F2-US2 | En tant que trader, je veux des skeletons sur toutes les pages afin de percevoir la structure pendant le chargement | Must | M (3j) | E1-F2-US1 | (1) Composant `Skeleton` créé (2) 19 pages migrées (3) Tests visuels valident les skeletons |
| E1-F2-US3 | En tant que trader, je veux que `QueryBoundary` soit adopté sur toutes les pages afin d'avoir une gestion d'erreur cohérente | Must | M (3j) | E1-F2-US1 | (1) 15 pages restantes migrées (2) `ErrorState` avec bouton retry contextualisé (3) `EmptyState` avec illustration et CTA |
| E1-F2-US4 | En tant que développeur, je veux que les dépendances mortes soient supprimées du bundle afin de réduire la taille JS | Should | S (1j) | — | (1) `framer-motion` soit supprimé soit utilisé (2) `zod` soit supprimé soit utilisé (3) `date-fns` supprimé (4) `@tanstack/react-table` soit supprimé soit utilisé (5) Bundle analyzer configuré |
| E1-F2-US5 | En tant que développeur, je veux que les tokens de design soient en source unique (CSS vars) afin d'éviter la désynchronisation | Should | S (1j) | — | (1) `tailwind.config.ts` référence les vars CSS (2) Pas de hex dupliqué (3) Documentation des tokens |
| E1-F2-US6 | En tant que trader, je veux que les versions dans sidebar/settings soient synchronisées avec `package.json` afin d'éviter la confusion | Could | S (0,5j) | — | (1) Lecture de `package.json` via `getInitialProps` ou `generateMetadata` (2) Pas de hardcodage |
| E1-F2-US7 | En tant que trader, je veux que le service worker soit enregistré globalement dans `layout.tsx` afin que la PWA fonctionne sur toutes les pages | Should | S (0,5j) | — | (1) Enregistrement dans `Providers` ou `layout.tsx` (2) Testé sur toutes les pages |
| E1-F2-US8 | En tant que trader, je veux que `window.confirm` soit remplacé par un Dialog stylé afin d'avoir une UX cohérente | Must | M (2j) | E1-F2-US1 | (1) Composant `ConfirmDialog` créé (2) Migration `/models` promote/reject (3) Migration tous les `window.confirm` du codebase |

**Feature E1-F3 — Tests & Accessibilité (Sprint 2, 12 SP)**

| ID | User Story | MoSCoW | Complexité | Dépendances | Critères d'acceptation |
|---|---|---|---|---|---|
| E1-F3-US1 | En tant que développeur, je veux que `@axe-core/playwright` soit installé et configuré afin de vérifier WCAG 2.1 AA en CI | Must | M (2j) | — | (1) Package installé (2) Tests axe sur 20+ routes (3) CI échoue si violations (4) Rapport HTML généré |
| E1-F3-US2 | En tant que trader, je veux que tous les icon-only buttons aient un `aria-label` afin d'être utilisable au screen reader | Must | S (1j) | — | (1) Audit de tous les buttons icon-only (2) `aria-label` ajouté (3) Tests axe valident |
| E1-F3-US3 | En tant que trader, je veux que tous les `<input>` aient un `<label>` associé afin d'être utilisable au screen reader | Must | S (1j) | — | (1) Audit de tous les inputs (2) `htmlFor`/`id` ajoutés (3) Tests axe valident |
| E1-F3-US4 | En tant que trader, je veux que le contraste texte/fond respecte AA (4,5:1) afin de pouvoir lire confortablement | Must | S (0,5j) | — | (1) `text-dim` changé pour #9ca3af (contraste 4,6:1) (2) Audit axe validé |
| E1-F3-US5 | En tant que trader, je veux un skip-to-content link afin de naviguer au clavier sans tabber toute la sidebar | Should | S (0,5j) | — | (1) Link en premier élément du `<body>` (2) Visible au focus (3) Cible `#main-content` |
| E1-F3-US6 | En tant que trader, je veux que les `<th>` aient `scope="col"` afin que les tables soient accessibles | Should | S (1j) | — | (1) Audit des ~12 tables (2) `scope` ajouté (3) Tests axe valident |
| E1-F3-US7 | En tant que développeur, je veux des tests unitaires (Vitest + RTL) sur les composants critiques afin de prévenir les régressions | Should | L (5j) | — | (1) Vitest configuré (2) Tests sur `Button`, `Card`, `Badge`, `KPICard`, `EquityCurve`, `PositionsTable`, `QueryBoundary` (3) Coverage > 60% sur `components/` |
| E1-F3-US8 | En tant que développeur, je veux une page `not-found.tsx` et `error.tsx` App Router afin d'avoir une UX 404/500 soignée | Should | S (1j) | — | (1) `app/not-found.tsx` créé (2) `app/error.tsx` créé (3) Tests e2e valident |

---

##### EPIC E2 — Consolidation navigation 5 pages (Sprints 3-6, 52 SP)

**Feature E2-F1 — Page Portefeuille (Sprint 3, 12 SP)**

| ID | User Story | MoSCoW | Complexité | Dépendances | Critères d'acceptation |
|---|---|---|---|---|---|
| E2-F1-US1 | En tant que trader, je veux une page Portefeuille unique qui fusionne `/dashboard` et `/portfolio` afin d'avoir une vue cohérente de mon portefeuille | Must | L (6j) | E1-F1-US1 (EquityCurve réelle) | (1) Route `/portfolio` devient la page d'accueil (2) KPIs consolidés (capital, PnL, WR, DD) (3) Equity curve réelle (4) Allocation par bot avec shadow targets (5) Positions ouvertes regroupées par bot (6) Fil d'activité miroir Telegram (7) Bouton arrêt d'urgence visible (8) Jauge de risque du jour vs limites |
| E2-F1-US2 | En tant que trader, je veux un bandeau de santé en français courant qui synthétise l'état du portefeuille afin de comprendre en 5 secondes | Must | M (3j) | E2-F1-US1 | (1) Bandeau en haut de page (2) Texte généré dynamiquement (« Ton portefeuille a généré +2,3% cette semaine... ») (3) Adapté au persona (débutant vs expert) |
| E2-F1-US3 | En tant que trader, je veux un donut d'allocation par bot afin de visualiser la diversification | Should | M (3j) | E2-F1-US1 | (1) Composant `AllocationDonut` créé (2) Clique sur un segment → fiche bot (3) Légende avec budget réel vs shadow |

**Feature E2-F2 — Page Mes Bots (Sprint 4, 14 SP)**

| ID | User Story | MoSCoW | Complexité | Dépendances | Critères d'acceptation |
|---|---|---|---|---|---|
| E2-F2-US1 | En tant que trader, je veux une page Mes Bots qui fusionne `/bots` et `/config` (stratégies + slots) afin de gérer mes bots comme une équipe RH | Must | L (8j) | E1-F2-US1 (Radix) | (1) Kanban 4 colonnes (Candidats/Essai/Actifs/Retirés) + filtre « Gelés » (2) Card bot avec sparkline 7j + budget + indicateur de confiance (🟢🟠🔴) (3) Fiche bot en drawer avec cône Monte-Carlo vs réel (4) Frise cycle de vie (5) Budget slider (6) Actions contextuelles (force-active, forward-test, reset CB, override) (7) Bouton « Recruter un nouveau bot » → Laboratoire |
| E2-F2-US2 | En tant que trader, je veux un cône Monte-Carlo par bot afin de comparer la performance live vs la simulation | Must | M (3j) | E2-F2-US1, E3-F2-US4 (OOS tracker) | (1) Composant `MonteCarloCone` créé (2) Bande IC 95% + marks sim/live/zero (3) Verdict coloré (ok/bad/na) (4) Tooltip pédagogique |
| E2-F2-US3 | En tant que trader, je veux une frise de cycle de vie sur chaque bot afin de visualiser sa progression | Should | S (1j) | E2-F2-US1 | (1) Composant `LifecycleFrieze` créé (2) 4 étapes avec dots colorés (3) Animation sur transition |
| E2-F2-US4 | En tant que trader, je veux un indicateur de confiance (🟢🟠🔴) par bot afin de savoir où en est chaque bot | Should | S (1j) | E2-F2-US1 | (1) Composant `ConfidenceIndicator` créé (2) Calcul basé sur forward-test + réalisation live + edge significatif (3) Tooltip explicatif |
| E2-F2-US5 | En tant que trader, je veux un bouton « Recruter un nouveau bot » qui redirige vers le Laboratoire afin d'initier le workflow de création | Could | S (0,5j) | E2-F2-US1 | (1) Bouton en haut du kanban (2) Redirige vers `/lab?intent=create` |
| E2-F2-US6 | En tant que trader, je veux un filtre « Gelés » sur le kanban afin de masquer les bots manuellement désactivés | Could | S (0,5j) | E2-F2-US1 | (1) Toggle filtre (2) Bots `manual_active: false` masqués |

**Feature E2-F3 — Page Laboratoire (Sprint 5, 14 SP)**

| ID | User Story | MoSCoW | Complexité | Dépendances | Critères d'acceptation |
|---|---|---|---|---|---|
| E2-F3-US1 | En tant que trader, je veux une page Laboratoire qui fusionne `/backtest`, `/optimizer`, `/ml`, `/models`, `/replay`, `/compare` afin d'avoir un workflow unique de recherche | Must | XL (10j) | E1-F2-US1 | (1) Pipeline guidé : « Analyser » → verdict en clair → bouton unique « Créer le bot (Essai) » (2) Mode expert opt-in qui révèle les options avancées (méthode opt, n_trials, WF folds, MC runs, ML params) (3) Tabs : Backtest / Optimizer / ML / Replay / Compare (4) SSE live progress pour optimizer (5) Drawer params détaillés (6) Export JSON/PDF/CSV |
| E2-F3-US2 | En tant que trader, je veux un verdict en clair après chaque analyse afin de décider sans lire les chiffres | Must | M (3j) | E2-F3-US1 | (1) Composant `Verdict` créé (2) Texte généré dynamiquement (« Cette stratégie a un edge significatif sur BTC/4h avec 47 trades OOS, Sharpe 1,8... ») (3) Recommandation actionnable |
| E2-F3-US3 | En tant que trader expert, je veux un mode expert opt-in qui révèle les seuils avancés afin de garder le contrôle fin | Should | M (2j) | E2-F3-US1 | (1) Toggle dans `/settings` (2) Indicateur visuel dans topbar (3) Sections avancées révélées |
| E2-F3-US4 | En tant que trader, je veux un bouton « Créer le bot (Essai) » après validation d'une stratégie afin d'initier le cycle de vie | Should | S (1j) | E2-F3-US1, E2-F2-US1 | (1) Bouton contextuel (2) Crée un slot en `essai` (3) Redirige vers `/bots` avec notification de confirmation |

**Feature E2-F4 — Page Marché (Sprint 6, 8 SP)**

| ID | User Story | MoSCoW | Complexité | Dépendances | Critères d'acceptation |
|---|---|---|---|---|---|
| E2-F4-US1 | En tant que trader, je veux une page Marché qui consolide `/scanner`, `/smartgraph`, `/smartreplay`, `/derivatives` afin d'avoir une vue unifiée du marché | Must | L (5j) | E1-F2-US1 | (1) Tabs : Scanner / Smart Graph / Smart Replay / Dérivés (2) Table scan multi-symboles triable (3) Chart candlestick + 14 overlays SMC (4) Contrôles replay play/pause/speed (5) 4 charts dérivés (funding/OI/LSR/taker) (6) Lien « Analyser cette paire au Laboratoire » |
| E2-F4-US2 | En tant que trader, je veux un raccourci « Analyser cette paire au Laboratoire » depuis le scanner afin d'initier un backtest sur une paire identifiée | Should | S (1j) | E2-F4-US1, E2-F3-US1 | (1) Bouton sur chaque ligne du scanner (2) Redirige vers `/lab?symbol=X&tf=Y` |
| E2-F4-US3 | En tant que trader, je veux un chart multi-panneaux (price/vol/RSI/MACD) dans le scanner afin d'analyser une paire en profondeur | Should | M (2j) | E2-F4-US1 | (1) 4 panneaux synchronisés (2) Toggles indicateurs (EMA/BB/SR/SMC) (3) Markers setups V11/V12 |

**Feature E2-F5 — Page Réglages (Sprint 6, 4 SP)**

| ID | User Story | MoSCoW | Complexité | Dépendances | Critères d'acceptation |
|---|---|---|---|---|---|
| E2-F5-US1 | En tant que trader, je veux une page Réglages qui consolide `/settings`, `/config`, `/data`, `/audit-log` (admin) afin d'avoir une seule entrée des préférences | Must | M (3j) | E1-F2-US1 | (1) Sections : Capital & Risque / Notifications / Données & Univers / Audit & Conformité / Préférences UI (2) Presets de risque (Prudent/Équilibré/Agressif) avec cartes (3) Mode expert opt-in (4) Thème & locale (5) Notifications navigateur (6) Test envoi notification (7) Gestion univers (8) Audit log filtrable |
| E2-F5-US2 | En tant que trader, je veux 3 presets de risque (Prudent/Équilibré/Agressif) sous forme de cartes afin de choisir mon profil en un clic | Must | S (1j) | E2-F5-US1 | (1) 3 cartes avec détail (risk/trade, max positions, daily DD, global DD, kill-switch) (2) Application via `/api/settings/risk-preset` (3) Confirmation modale |


---

##### EPIC E3 — Parité fonctionnelle avec ancienne UI (Sprints 5-9, 41 SP)

**Feature E3-F1 — Dashboard features manquantes (Sprint 5, 8 SP)**

| ID | User Story | MoSCoW | Complexité | Dépendances | Critères d'acceptation |
|---|---|---|---|---|---|
| E3-F1-US1 | En tant que trader, je veux un histogramme de distribution PnL des 30 derniers trades afin d'identifier les outliers | Should | S (1j) | E1-F1-US1 | (1) Composant `PnLDistribution` créé (2) Histogramme lightweight-charts (3) Légende avec mean/median/stddev |
| E3-F1-US2 | En tant que trader, je veux un histogramme de PnL journalier sur 30 jours afin de visualiser la régularité | Must | S (1j) | E1-F1-US1 | (1) Composant `DailyPnLChart` créé (2) Histogramme lightweight-charts (3) Tooltip avec date + PnL + nb trades |
| E3-F1-US3 | En tant que trader, je veux un journal des signaux avec rejets (checkbox « Voir rejetés ») afin de comprendre pourquoi certains signaux sont écartés | Must | M (2j) | — | (1) Checkbox dans `SignalsFeed` (2) Colonne « Raison » (seuil, budget, corrélation, risque) (3) Filtre par raison |
| E3-F1-US4 | En tant que trader, je veux un widget de ventilation des frais (taker/maker/borrow/stop) afin d'optimiser mes coûts | Should | S (1j) | — | (1) Composant `FeesBreakdown` créé (2) Consomme `/api/stats/fees` (3) 4 segments avec montant + % du PnL |
| E3-F1-US5 | En tant que trader, je veux un affichage de latence API afin de détecter les ralentissements | Could | S (0,5j) | — | (1) Mesure côté client (2) Affichage dans topbar (3) Alerte si > 500ms |
| E3-F1-US6 | En tant que trader, je veux des risk gauges (DD journalier/global) avec limites visuelles afin de visualiser ma exposition | Should | S (1j) | — | (1) Composant `RiskGauge` créé (2) Track + fill + libellé limite (3) Couleur selon seuil (vert/orange/rouge) |
| E3-F1-US7 | En tant que trader, je veux un health badge système basé sur `/health` afin de voir l'état du backend en un coup d'œil | Should | S (0,5j) | — | (1) Polling `/health` 30s (2) Badge dans topbar (3) Couleur selon status (4) Tooltip avec détails (db/exchange/trader) |
| E3-F1-US8 | En tant que trader, je veux une modale « Changement Paper↔Live » avec warning critique afin d'éviter les bascules involontaires | Must | S (1j) | — | (1) Modale stylée (2) Warning « LIVE TRADING RÉEL » en rouge (3) Double confirmation (checkbox + bouton) (4) Audit log |

**Feature E3-F2 — Backtest & Optimizer features manquantes (Sprint 6-7, 12 SP)**

| ID | User Story | MoSCoW | Complexité | Dépendances | Critères d'acceptation |
|---|---|---|---|---|---|
| E3-F2-US1 | En tant que trader expert, je veux une table Walk-Forward avec folds OOS afin d'évaluer la robustesse hors-échantillon | Must | M (3j) | — | (1) Composant `WalkForwardTable` créé (2) Colonnes : Fold/OOS PnL/Win Rate/Sharpe/Trades (3) Warning si < seuil `min_required` (4) Affiché dans backtest results |
| E3-F2-US2 | En tant que trader expert, je veux un graphique Monte-Carlo (200 runs, IC 95%, P5/P50/P95) afin d'évaluer la distribution des outcomes | Must | M (3j) | — | (1) Composant `MonteCarloChart` créé (2) 3 lignes (P5/P50/P95) (3) Bande IC ombrée (4) Warning si < 30 trades (5) Affiché dans backtest results |
| E3-F2-US3 | En tant que trader expert, je veux un scatter chart prix×trades afin de visualiser la répartition des entrées/sorties | Should | M (2j) | — | (1) Composant `TradesScatter` créé (utilise recharts) (2) Points colorés par side (long/short) (3) Tooltip avec détail trade |
| E3-F2-US4 | En tant que trader expert, je veux un fullscreen chart modal avec reset zoom afin d'analyser en détail | Should | S (1j) | — | (1) Modal plein écran (Radix Dialog) (2) Bouton reset zoom (3) Bouton close (Echap) |
| E3-F2-US5 | En tant que trader expert, je veux un export JSON et PDF du backtest afin d'archiver mes analyses | Could | M (2j) | — | (1) Bouton ↓ JSON (payload complet) (2) Bouton ↓ PDF (jsPDF + chart screenshot) (3) Filename daté |
| E3-F2-US6 | En tant que trader expert, je veux une courbe Buy & Hold superposée à l'equity backtest afin de mesurer l'alpha | Should | S (1j) | — | (1) Ligne dashed Buy & Hold (2) Légende (3) Calcul alpha affiché |

**Feature E3-F3 — Bots & Portfolio features manquantes (Sprint 7-8, 11 SP)**

| ID | User Story | MoSCoW | Complexité | Dépendances | Critères d'acceptation |
|---|---|---|---|---|---|
| E3-F3-US1 | En tant que trader, je veux un cône Monte-Carlo vs réel par bot (consomme `/api/oos-tracker`) afin de comparer sim vs live | Must | L (5j) | E2-F2-US2 | (1) Composant `MonteCarloCone` créé (2) Bande IC + marks sim/live/zero (3) Verdict coloré (4) Tooltip pédagogique (5) Affiché dans fiche bot drawer |
| E3-F3-US2 | En tant que trader, je veux une shadow allocation (barre cible amber superposée) afin de visualiser l'écart budget actuel vs proposition allocateur | Should | S (1j) | — | (1) Composant `AllocationBar` créé (2) Barre courante + barre cible amber (3) Tooltip avec écarts |
| E3-F3-US3 | En tant que trader, je veux un bandeau halt avec bouton « Acquitter le kill-switch » afin de reprendre le trading après un HALT | Must | S (1j) | — | (1) Bandeau en haut de Portefeuille (2) Bouton « Acquitter » (3) Modal de confirmation (4) Audit log |
| E3-F3-US4 | En tant que trader, je veux un drawer performance slot détaillée (consomme `/api/strategy/{slot_key}/performance`) afin d'analyser un bot en profondeur | Should | M (3j) | — | (1) Drawer lateral (2) KPIs : win_rate, PF, Sharpe, max_dd (3) Recent trades table (4) Sparkline equity |
| E3-F3-US5 | En tant que trader, je veux une frise de cycle de vie animée dans le drawer bot afin de visualiser la progression | Should | S (1j) | E2-F2-US3 | (1) Animation sur transition (2) Tooltip avec date de transition |

**Feature E3-F4 — Scanner & SMC features manquantes (Sprint 8-9, 10 SP)**

| ID | User Story | MoSCoW | Complexité | Dépendances | Critères d'acceptation |
|---|---|---|---|---|---|
| E3-F4-US1 | En tant que trader, je veux une table scan multi-symboles triable (consomme `/api/scanner`) afin de screen le marché | Must | M (3j) | — | (1) Composant `ScannerTable` créé (utilise `@tanstack/react-table`) (2) Colonnes : symbol/régime/ADX/RSI/ATR%/vol_ratio/volume_24h (3) Tri par colonne (4) Clique → chart |
| E3-F4-US2 | En tant que trader, je veux un widget « Top opportunités » (consomme `/api/scanner/opportunities`) afin d'identifier les paires à fort potentiel | Should | S (1j) | — | (1) Composant `OpportunitiesWidget` créé (2) Top 10 par score combiné (40% vol 24h + 60% ATR%) (3) Clique → chart |
| E3-F4-US3 | En tant que trader, je veux des markers setups V11/V12 sur le chart scanner (consomme `/api/scanner/setup_series`) afin de visualiser les points d'entrée | Should | M (2j) | — | (1) Toggle V11/V12 (2) Markers entry + TP/SL sur chart (3) Tooltip avec détail setup |
| E3-F4-US4 | En tant que trader, je veux un panel « Prédictions par stratégie » afin de voir les signaux ML pour la paire chargée | Could | M (2j) | — | (1) Composant `PredictionsPanel` créé (2) Affiche prédictions ML par stratégie (3) Tooltip avec confidence |
| E3-F4-US5 | En tant que trader, je veux une table « Plans recommandés » dans Smart Graph (Statut/Sens/Setup/Entry/SL/TP/Gain/RR/Dist/Score) afin d'identifier les setups à exécuter | Should | M (2j) | — | (1) Composant `PlansTable` créé (2) Clique sur une ligne → trace sur le chart (3) Tri par score |

---

##### EPIC E4 — Expérience trader avancé (Sprints 7-10, 28 SP)

**Feature E4-F1 — Mode expert & Personnalisation (Sprint 7, 8 SP)**

| ID | User Story | MoSCoW | Complexité | Dépendances | Critères d'acceptation |
|---|---|---|---|---|---|
| E4-F1-US1 | En tant que trader expert, je veux un mode expert opt-in global qui révèle les seuils avancés dans toute l'UI afin de garder le contrôle fin | Must | M (3j) | — | (1) Toggle dans `/settings` persisté (2) Indicateur visuel dans topbar (3) Sections avancées révélées (CBs par slot, ML params avancés, walk-forward folds, etc.) |
| E4-F1-US2 | En tant que trader expert, je veux des keyboard shortcuts globaux afin de naviguer rapidement | Should | M (2j) | — | (1) Cmd+K search (existant) (2) G→Portefeuille, B→Mes Bots, L→Laboratoire, M→Marché, R→Réglages (3) Espace=play/pause sur replay (4) ←→=±1 barre (5) ?=aide shortcuts |
| E4-F1-US3 | En tant que trader expert, je veux un éditeur de params par stratégie (base + override per-symbole) consommant `/api/config/strategy-params` afin de tuner finement | Should | L (5j) | — | (1) Composant `StrategyParamsEditor` créé (2) Toggle base/override (3) Sélecteur symbole pour override (4) Validation Zod (5) Save → `/api/config/strategy-params` |
| E4-F1-US4 | En tant que trader expert, je veux configurer les circuit breakers par slot (consomme `/api/config/risk`) afin d'ajuster finement le risque | Should | M (2j) | E4-F1-US1 | (1) Section dans Réglages (mode expert only) (2) Champs : consecutive_loss_limit, slot_daily_dd_limit, win_rate_floor, volatility_threshold, consecutive_pause_secs (3) Save → `/api/config/risk` |
| E4-F1-US5 | En tant que trader expert, je veux un toggle strat sur un TF spécifique (consomme `/api/config/strategy-timeframe`) afin d'activer/désactiver une strat sur un TF | Should | S (1j) | E4-F1-US1 | (1) Composant `StrategyTFMatrix` créé (2) Grille strat × TF (3) Toggle par cellule |
| E4-F1-US6 | En tant que trader expert, je veux gérer les overrides par stratégie (consomme `/api/config/strategy-overrides`) afin de visualiser les symboles override | Could | S (1j) | E4-F1-US3 | (1) Liste overrides par stratégie (2) Clique → éditeur params |

**Feature E4-F2 — Gestion univers & Données (Sprint 8, 6 SP)**

| ID | User Story | MoSCoW | Complexité | Dépendances | Critères d'acceptation |
|---|---|---|---|---|---|
| E4-F2-US1 | En tant que trader, je veux un sélecteur d'univers dans Données (consomme `/api/universe`) afin de choisir mon univers d'instruments | Should | S (1j) | — | (1) Dropdown univers (2) Affiche n_symbols (3) Sélection → liste membres |
| E4-F2-US2 | En tant que trader, je veux une liste des membres d'un univers avec bars par TF (consomme `/api/universe/{name}`) afin d'inspecter un univers | Should | M (2j) | E4-F2-US1 | (1) Table membres (symbol/name/sector/bars par TF) (2) Tri (3) Filtre |
| E4-F2-US3 | En tant que trader, je veux ajouter/retirer un symbole d'un univers (consomme `POST/DELETE /api/universe/{name}/symbols`) afin de personnaliser mes univers | Could | M (2j) | E4-F2-US1 | (1) Bouton « Ajouter symbole » (modal) (2) Bouton « Retirer » par ligne (confirm) (3) Audit log |
| E4-F2-US4 | En tant que trader, je veux un lien croisé /data ↔ /scanner (UI-09 de l'ancienne UI) afin de naviguer rapidement entre cache et analyse | Should | S (1j) | — | (1) Lien ↗ Analyser sur chaque ligne /data → /market/scanner?symbol=X&tf=Y (2) Message d'erreur Fast Analyse propose → Charger les données vers /data |

**Feature E4-F3 — ML & Registry avancés (Sprint 9, 8 SP)**

| ID | User Story | MoSCoW | Complexité | Dépendances | Critères d'acceptation |
|---|---|---|---|---|---|
| E4-F3-US1 | En tant que trader expert, je veux une liste des recettes ML (consomme `/api/ml/recipes`) afin de choisir la recette à entraîner | Should | S (1j) | — | (1) Cards par recette (recipe/trainable/reason/features_catalog/label_scheme/heads) (2) Clique → train form |
| E4-F3-US2 | En tant que trader expert, je veux un diagnostic ML avancé (features importance, AUC par régime, Spearman) afin d'évaluer la qualité d'un modèle | Should | L (5j) | — | (1) Composant `MLDiagnostics` créé (2) Top N features importance (bar chart) (3) AUC par régime (table) (4) Spearman entre paires de régimes (heatmap) |
| E4-F3-US3 | En tant que trader expert, je veux un historique changelog optimizer (consomme `/api/config/changelog`) afin de tracer les modifications de params | Could | S (1j) | — | (1) Section dans Audit (2) Table datée (3) Filtre par stratégie/TF |
| E4-F3-US4 | En tant que trader expert, je veux un bouton test envoi notification (consomme `/api/config/notifications/test`) afin de vérifier ma config Telegram/WhatsApp/Email | Should | S (1j) | — | (1) Bouton « Tester » dans Réglages > Notifications (2) Toast de confirmation (3) Audit log |

**Feature E4-F4 — Exports riches (Sprint 10, 6 SP)**

| ID | User Story | MoSCoW | Complexité | Dépendances | Critères d'acceptation |
|---|---|---|---|---|---|
| E4-F4-US1 | En tant que trader, je veux un export CSV des trades avec sélecteur de limite (500/1000/5000/Tous) afin d'analyser dans Excel | Must | S (1j) | — | (1) Bouton ↓ Export CSV (2) Sélecteur limite (3) Filename daté `trades_YYYY-MM-DD.csv` |
| E4-F4-US2 | En tant que trader, je veux un export CSV de l'audit log afin d'archiver pour conformité | Should | S (1j) | — | (1) Bouton ↓ Export CSV dans Audit Log (2) Filename daté |
| E4-F4-US3 | En tant que trader, je veux un export JSON du backtest complet afin d'archiver mes analyses | Could | S (1j) | — | (1) Bouton ↓ JSON (2) Payload complet (3) Filename daté |
| E4-F4-US4 | En tant que trader, je veux un export PDF du backtest avec charts afin de partager mes analyses | Could | M (2j) | — | (1) Bouton ↓ PDF (jsPDF + chart screenshot) (2) Layout A4 portrait (3) Filename daté |
| E4-F4-US5 | En tant que trader, je veux un export CSV du comparatif multi-stratégies afin de partager mon choix | Could | S (1j) | — | (1) Bouton ↓ Export CSV (2) Table rendue (3) Filename daté |

---

##### EPIC E5 — Industrialisation & Conformité (Sprints 10-12, 22 SP)

**Feature E5-F1 — Tests visuels & CI (Sprint 10, 8 SP)**

| ID | User Story | MoSCoW | Complexité | Dépendances | Critères d'acceptation |
|---|---|---|---|---|---|
| E5-F1-US1 | En tant que développeur, je veux des tests visuels (Chromatic ou Percy) afin de détecter les régressions UI | Should | L (5j) | E1-F2-US1 | (1) Chromatic configuré (2) Stories Storybook pour composants critiques (3) CI échoue si diff visuel (4) Review app sur PR |
| E5-F1-US2 | En tant que développeur, je veux une CI GitHub Actions qui lance lint + tests unitaires + tests e2e + axe-core afin de prévenir les régressions | Must | M (3j) | E1-F3-US1, E1-F3-US7 | (1) Workflow `.github/workflows/ci.yml` (2) Jobs : lint (eslint), type-check (tsc), unit (vitest), e2e (playwright), a11y (axe-core) (3) Cache npm (4) Artifact uploads |

**Feature E5-F2 — Onboarding utilisateur (Sprint 11, 6 SP)**

| ID | User Story | MoSCoW | Complexité | Dépendances | Critères d'acceptation |
|---|---|---|---|---|---|
| E5-F2-US1 | En tant que trader débutant, je veux un onboarding guidé à la première visite afin de comprendre l'UI | Should | L (5j) | E2-F1-US1, E2-F2-US1, E2-F3-US1 | (1) Détection première visite (localStorage) (2) Tour guidé 5 étapes (Portefeuille → Mes Bots → Laboratoire → Marché → Réglages) (3) Tooltips pédagogiques (4) Skip possible (5) Reset dans Réglages |
| E5-F2-US2 | En tant que trader débutant, je veux des tooltips pédagogiques systématiques (OOS, Sharpe, fourchette, overfit, edge) afin d'apprendre le jargon | Should | S (1j) | — | (1) Composant `HelpTooltip` créé (2) Définitions inline sur termes techniques (3) Lien « En savoir plus » vers doc |

**Feature E5-F3 — i18n & Conformité (Sprint 11-12, 8 SP)**

| ID | User Story | MoSCoW | Complexité | Dépendances | Critères d'acceptation |
|---|---|---|---|---|---|
| E5-F3-US1 | En tant que trader anglophone, je veux une traduction EN complète afin d'utiliser l'UI en anglais | Should | M (3j) | — | (1) Audit des clés i18n manquantes (2) Traduction EN (3) Toggle FR/EN dans Réglages (4) Persistance locale |
| E5-F3-US2 | En tant que trader, je veux des avertissements PSAN conformes afin de respecter la réglementation AMF | Must | S (1j) | — | (1) Bandeau d'avertissement sur Portefeuille (2) Modal à la première activation LIVE (3) Lien vers doc AMF |
| E5-F3-US3 | En tant que trader, je veux une restriction géographique (blocage IP US) afin de respecter la réglementation SEC | Could | M (2j) | — | (1) Middleware Next.js géo-IP (2) Page de blocage (3) Config par env |
| E5-F3-US4 | En tant que trader, je veux un analytics produit opt-in (PostHog) afin de contribuer à l'amélioration de l'UI | Won't (reporté) | M (2j) | — | (1) Toggle opt-in dans Réglages (2) PostHog configuré (3) Anonymisation IP (4) Pas de données sensibles (5) Reporté car PSAN sensible |


### 4.3 Roadmap visuelle en 12 sprints

La roadmap ci-dessous étale le backlog sur **12 sprints de 2 semaines (24 semaines, ~6 mois)** avec une capacité estimée de 1 dev senior ou 2 devs mid (1 SP ≈ 1 jour-homme). Les sprints sont groupés en 4 phases qui reprennent la structure du Plan Directeur existant (Survie → Fondations → Consolidation → Industrialisation).

#### Vue d'ensemble des 12 sprints

| Sprint | Phase | Epic | Objectif | SP | Livrable démo |
|---|---|---|---|---|---|
| **S0** | Survie | E1 | Bug fixes P1 (EquityCurve, light theme, KPICard, WS URL) | 8 | Dashboard confiance restaurée |
| **S1** | Fondations | E1 | Design system complet (Radix wrappers, skeletons, QueryBoundary) | 10 | Composants UI cohérents |
| **S2** | Fondations | E1 | Tests & a11y (axe-core, Vitest, RTL, ConfirmDialog) | 12 | Conformité AA outillée |
| **S3** | Consolidation | E2 | Page Portefeuille (fusion dashboard + portfolio) | 12 | Cockpit gérant de fonds |
| **S4** | Consolidation | E2 | Page Mes Bots (fusion bots + config-stratégies) | 14 | Kanban lifecycle + cône MC |
| **S5** | Consolidation | E2 + E3 | Page Laboratoire (fusion backtest + optimizer + ML) + Dashboard features | 14 | Workflow verdict en clair |
| **S6** | Consolidation | E2 + E3 | Page Marché + Page Réglages + Backtest features | 12 | 5 pages méta opérationnelles |
| **S7** | Consolidation | E3 + E4 | Bots/Portfolio features + Mode expert global | 12 | Parité ancienne UI |
| **S8** | Consolidation | E3 + E4 | Scanner/SMC features + Univers & Données | 10 | Scanner multi-symboles |
| **S9** | Consolidation | E4 | ML & Registry avancés + Exports riches | 8 | Diagnostic ML complet |
| **S10** | Industrialisation | E5 | Tests visuels (Chromatic) + CI GitHub Actions | 8 | Régression visuelle détectée |
| **S11** | Industrialisation | E5 | Onboarding utilisateur + i18n EN | 6 | Première visite guidée |
| **S12** | Industrialisation | E5 | Conformité PSAN + Analytics (reporté) + Buffer | 4 | Conformité AMF |

**Total : 130 SP sur 24 semaines** (capacité 1 dev senior à 5 SP/sprint = 120 SP, ou 2 devs mid à 5 SP/sprint chacun = 240 SP — la roadmap tient dans la capacité avec marge pour les imprévus).

#### Diagramme de Gantt simplifié

```
Sprint  S0  S1  S2  S3  S4  S5  S6  S7  S8  S9  S10 S11 S12
        ─────────────────────────────────────────────────────
Phase 1: SURVIE & FONDAIONS
E1-F1   ████                                                    Bug fixes P1
E1-F2       ████████                                            Design system
E1-F3           ████████                                        Tests & a11y

Phase 2: CONSOLIDATION 5 PAGES
E2-F1               ████████                                    Portefeuille
E2-F2                   ████████                                Mes Bots
E2-F3                       ████████                            Laboratoire
E2-F4                           ████                            Marché
E2-F5                           ████                            Réglages

Phase 3: PARITÉ ANCIENNE UI
E3-F1                       ████                                Dashboard features
E3-F2                           ████████                        Backtest features
E3-F3                               ████████                    Bots/Portfolio features
E3-F4                                   ████████                Scanner/SMC features

Phase 4: EXPÉRIENCE AVANCÉE
E4-F1                               ████████                    Mode expert
E4-F2                                   ████                    Univers & Données
E4-F3                                       ████                ML & Registry
E4-F4                                       ████                Exports riches

Phase 5: INDUSTRIALISATION
E5-F1                                           ████            Tests visuels + CI
E5-F2                                               ████        Onboarding
E5-F3                                               ████████    i18n + Conformité
```

#### Dépendances critiques entre sprints

- **S3 (Portefeuille) dépend de S0** (EquityCurve réelle) — sinon le cockpit affiche des données simulées
- **S4 (Mes Bots) dépend de S1** (Radix wrappers) — sinon le drawer et les dialogues ne sont pas cohérents
- **S5 (Laboratoire) dépend de S4** (bouton « Créer le bot (Essai) » redirige vers Mes Bots)
- **S7 (Bots/Portfolio features) dépend de S5** (E3-F3-US1 cône MC dépend de E2-F2-US2 cône MC base)
- **S10 (Tests visuels) dépend de S1** (Storybook nécessite les composants Radix wrappés)
- **S11 (Onboarding) dépend de S3-S6** (5 pages méta doivent exister pour le tour guidé)

### 4.4 Architecture technique recommandée

#### Stack frontend recommandée

La stack actuelle (Next.js 15 / React 19 / TypeScript 5.7 / Tailwind / TanStack Query / Radix / lightweight-charts + recharts) est **globalement pertinente** et ne nécessite pas de refonte majeure. Les recommandations ci-dessous sont des ajouts ou corrections ciblées, justifiés par les besoins identifiés.

| Composant | Recommandation | Justification |
|---|---|---|
| Framework | **Conserver Next.js 15 App Router** | SSR + RSC, server actions, middleware géo-IP pour conformité, proxy same-origin déjà en place |
| UI runtime | **Conserver React 19** | use() hook, transitions, actions — aligné avec Next 15 |
| Styling | **Conserver Tailwind 3.4** + passer à Tailwind 4 quand stable | Tokens CSS vars en source unique, dark mode natif |
| UI primitives | **Wrapper Radix en style shadcn/ui** (non installer shadcn-cli, wrapper manuel pour contrôle) | 10 packages déjà installés, il faut les wrapper en composants `components/ui/` |
| Charts | **Conserver lightweight-charts v4** (candlestick, SMC) + **recharts** (line/area/bar/scatter) | Deux libs spécialisées, déjà utilisées, performantes |
| Forms | **Ajouter react-hook-form + Zod** (déjà installé) | Validation côté client, performance, DX |
| Tables | **Utiliser @tanstack/react-table** (déjà installé) | Tri/filtrage/pagination/virtualisation, déjà payé |
| Animations | **Utiliser framer-motion** (déjà installé) ou **le supprimer** | Actuellement mort, soit on l'utilise pour transitions/page-transitions, soit on nettoie |
| State | **Conserver TanStack Query + Context** | Suffisant pour le scope, pas besoin de Zustand/Redux |
| Testing | **Ajouter Vitest + RTL** (unitaires) + **conserver Playwright** (e2e) + **ajouter @axe-core/playwright** (a11y) + **ajouter Chromatic** (visuel) | Stack tests complète |
| i18n | **Étendre le système existant** (Context + clés) ou migrer vers **next-intl** | next-intl plus robuste pour routing i18n |
| Auth | **Conserver le proxy same-origin** + ajouter **middleware géo-IP** pour conformité | Pattern solide, pas de session frontend |
| PWA | **Conserver manifest.json + sw.js** + enregistrer SW dans `layout.tsx` | PWA déjà fonctionnelle |
| Build | **Activer bundle analyzer** (`@next/bundle-analyzer`) | Identifier les deps mortes |

#### Librairie de graphiques adaptée au trading crypto

Le trading crypto nécessite des visualisations spécialisées que les libs générales (recharts) ne couvrent pas bien. Voici la recommandation détaillée par type de chart :

| Type de chart | Librairie recommandée | Justification | Pages concernées |
|---|---|---|---|
| **Candlestick OHLCV** | lightweight-charts v4 (TradingView) | Performance native, zoom/pan fluide, markers, crosshair, time scale custom | smartgraph, smartreplay, scanner, backtest, replay |
| **Line / Area (equity)** | recharts (AreaChart/LineChart) | API déclarative React, tooltips custom, légende, responsive | dashboard, portfolio, backtest, trades |
| **Bar / Histogram** | recharts (BarChart) | Idem + animations | PnL distribution, daily PnL, fees breakdown |
| **Scatter (trades prix×temps)** | recharts (ScatterChart) | Idem | backtest |
| **Heatmap (Spearman ML, corrélation)** | recharts custom ou react-heatmap-grid | Pas de besoin critique, custom simple | ML diagnostics |
| **Donut (allocation)** | recharts (PieChart donut) | Idem + légende interactive | portfolio |
| **Cone (Monte-Carlo IC)** | lightweight-charts custom (AreaSeries + LineSeries) | Bande IC ombrée + marks sim/live/zero | bots drawer |
| **Gauge (risk DD)** | recharts custom (RadialBarChart) ou custom SVG | Simple, pas besoin de lib dédiée | dashboard, portfolio |

**Recommandation transverse** : standardiser sur **lightweight-charts pour les charts trading** (candlestick, SMC, replay) et **recharts pour les charts analytiques** (equity, distribution, donut, gauge). Ne pas introduire de 3e lib (Pas Plotly, pas ApexCharts, pas ECharts) — la maintenance de 2 libs est déjà un coût.

#### Gestion du temps réel — WebSocket vs polling

L'application utilise déjà un **pattern hybride mature** qui devrait être conservé et étendu :

| Cas d'usage | Mécanisme | Justification |
|---|---|---|
| Trades temps réel (opened/closed) | **WebSocket `/ws`** (channel `trades`) | Latence < 100ms, push server→client |
| Signaux temps réel (generated) | **WebSocket `/ws`** (channel `signals`) | Idem |
| Risk events (circuit_breaker, drawdown) | **WebSocket `/ws`** (channel `risk`) | Idem + notifications natives |
| Cycle updates | **WebSocket `/ws`** (channel `cycle`) | Idem |
| Tickers marché | **WebSocket `/ws`** (channel `ticker`) | Idem |
| Statut bot (capital, positions, PnL) | **Polling `/api/status` 3s** (TanStack Query) | Pas besoin de push, polling 3s acceptable |
| Portfolio consolidé | **Polling `/api/portfolio` 5s** | Idem |
| Jobs optimizer (progression) | **SSE `/api/optimize/stream`** | Idéal pour jobs longs, 1 connexion par job |
| Jobs ML (train/sweep status) | **Polling adaptatif** (1s si running, 30s sinon) | Pas de SSE backend pour ML, polling suffisant |
| Health check | **Polling `/health` 30s** | Pas besoin de push |
| Audit log | **Polling `/api/audit/log` 10s** | Idem |

**Recommandations** :
1. **Conserver le pattern hybride** — il est bien dimensionné.
2. **Étendre WebSocket** à de nouveaux events si besoin (ex: `bot.lifecycle_change` quand un bot passe de Candidat à Essai).
3. **Optimiser le polling** : `useOptimizeStatus` à 1,5s même sans job running est un gaspillage — conditionner le polling à `hasRunningJobs`.
4. **Réutiliser le SSE pattern** pour ML train/sweep si le backend l'ajoute (would reduce polling).
5. **Backoff exponentiel** déjà en place (max 30s) — bon.
6. **Reconnexion session** : au refresh, le SSE `LiveProgress` se reconnecte mais l'historique des jobs dépend du backend. Ajouter un endpoint `/api/optimize/jobs?status=running` pour restaurer le contexte.

#### Stratégie de migration progressive

La migration n'est pas un big-bang — elle s'étale sur 12 sprints en parallèle de la maintenance. La stratégie recommandée est **strangler fig pattern** :

1. **Phase 1 (S0-S2)** : bug fixes + design system + tests. L'UI actuelle reste en production, les fondations se préparent en parallèle.
2. **Phase 2 (S3-S6)** : création des 5 pages méta en parallèle des 23 pages existantes. Les nouvelles pages sont accessibles via `/portfolio-v2`, `/bots-v2`, `/lab-v2`, `/market-v2`, `/settings-v2` pour validation utilisateur.
3. **Phase 3 (S7-S9)** : une fois les 5 pages validées, redirection 308 des anciennes routes vers les nouvelles. Les anciennes pages sont supprimées.
4. **Phase 4 (S10-S12)** : industrialisation (tests visuels, CI, onboarding, conformité).

Cette stratégie permet :
- **Rollback facile** en cas de problème (les anciennes pages restent accessibles jusqu'à la Phase 3).
- **Validation utilisateur** progressive (les 5 pages méta sont testables en parallèle).
- **Démonstration de valeur à chaque sprint** (chaque sprint livre une fonctionnalité utilisable).
- **Pas de rupture pour l'utilisateur final** (redirections 308 transparentes).

#### Stratégie de tests

| Type de test | Outil | Couverture cible | Sprint |
|---|---|---|---|
| Tests unitaires composants | Vitest + React Testing Library | 60% sur `components/` | S2 |
| Tests unitaires hooks | Vitest + RTL | 80% sur `hooks/use-api.ts` | S2 |
| Tests intégration | Vitest + msw (mock API) | Cas critiques (auth, proxy, WS) | S2 |
| Tests e2e fonctionnels | Playwright (existant, 40 tests) | 100% des parcours utilisateur | S0-S12 |
| Tests e2e viewports | Playwright (mobile 375, tablette 768, desktop 1440) | 5 pages méta × 3 viewports | S3 |
| Tests a11y | @axe-core/playwright | WCAG 2.1 AA sur 20+ routes | S2 |
| Tests visuels | Chromatic | Stories Storybook pour composants critiques | S10 |
| Tests de performance | Lighthouse CI | LCP < 2,5s, FID < 100ms, CLS < 0,1 | S10 |
| Tests de charge | k6 (backend) | 100 utilisateurs concurrents | Won't (reporté) |

### 4.5 Wireframes textuels pour les écrans clés

Les wireframes ci-dessous sont en ASCII art pour les layouts principaux et en description textuelle pour les détails. Ils illustrent la vision 5 pages méta et servent de référence pour les User Stories.

#### Wireframe 1 — Page Portefeuille (accueil)

```
┌────────────────────────────────────────────────────────────────────────────────┐
│ [☰]  BOT-CRYPTO   [▶ Start] [PAPER] [● Running] [WS●] [⚡95ms]   [⌘K] [☀] [⚙]  │ Topbar
├────────┬───────────────────────────────────────────────────────────────────────┤
│        │ ╔════════════════════════════════════════════════════════════════════╗ │
│ TRADING │ ║ 📊 Bandeau de santé                                                ║ │
│ • Portf ║ "Ton portefeuille a généré +2,3% cette semaine, porté par           ║ │
│ • Bots  ║  trend_rider sur BTC/4h. 2 bots en essai, 0 en alerte."            ║ │
│        │ ╚════════════════════════════════════════════════════════════════════╝ │
│ RECH.  │ ┌─────────────────┐ ┌─────────────────┐ ┌──────────────────────────┐ │
│ • Lab  │ │ 💰 Capital      │ │ 📈 PnL Total     │ │ ⚠️ Drawdown              │ │
│ • Mrché│ │ $1,023.45       │ │ +$23.45 (+2.3%) │ │ Jour: -0.8% │ Glob: -3.2%│ │
│        │ │ +$23.45 (24h)   │ │ ▲ +2.3% (7j)    │ │ Limites: 5% │ 20%        │ │
│ DONNÉES│ └─────────────────┘ └─────────────────┘ └──────────────────────────┘ │
│ • Audit│ ┌──────────────────────────────────────────────────────────────────┐ │
│ • Data │ │ 📈 Equity Curve (30j)                       [Jour][Sem][Mois]    │ │
│        │ │    ╱╲      ╱╲                                                  │ │
│ CONF.  │ │   ╱  ╲    ╱  ╲    ╱╲                                          │ │
│ • Régl │ │  ╱    ╲__╱    ╲__╱  ╲___                                      │ │
│        │ │                          ╲___                                 │ │
│ ─────  │ └──────────────────────────────────────────────────────────────────┘ │
│ v12.17 │ ┌──────────────────────────┐ ┌────────────────────────────────────┐ │
│ ● Conn │ │ 🥧 Allocation par bot    │ │ ⚠️ Risk Panel                       │ │
│        │ │   ┌──────┐               │ │ DD Journalier: ████████░░ -0.8%    │ │
│        │ │   │ BTC  │ 35%           │ │ DD Global:     █████░░░░░ -3.2%    │ │
│        │ │   │ 4h   │               │ │ CB Active:     Non                 │ │
│        │ │   └──────┘               │ │ Kill-switch:   Non                 │ │
│        │ │   ┌──────┐               │ │                                    │ │
│        │ │   │ ETH  │ 25%           │ │ [Reset HALT] [Acquitter KS]        │ │
│        │ │   │ 1h   │               │ └────────────────────────────────────┘ │
│        │ │   └──────┘               │ ┌────────────────────────────────────┐ │
│        │ │   (donut interactif)     │ │ 📋 Positions ouvertes (3)          │ │
│        │ └──────────────────────────┘ │ ┌────────────────────────────────┐ │ │
│        │ ┌──────────────────────────┐ │ │ BTC/USDC  LONG  +1.2%  $12.45 │ │ │
│        │ │ 📰 Fil d'activité         │ │ │ trend_rider::4h::BTC           │ │ │
│        │ │ ─────────────────────    │ │ ├────────────────────────────────┤ │ │
│        │ │ 14:32 Trade closed BTC   │ │ │ ETH/USDC  SHORT -0.3%  -$3.20  │ │ │
│        │ │   +$12.45 (trend_rider)  │ │ │ ml_dynamic::1h::ETH            │ │ │
│        │ │ 14:28 Signal generated   │ │ ├────────────────────────────────┤ │ │
│        │ │   XRP/USDC (rejected)    │ │ │ XRP/USDC  LONG  +0.5%  $5.10   │ │ │
│        │ │ 14:15 Bot promoted       │ │ │ smart_money::4h::XRP           │ │ │
│        │ │   ml_dynamic::1h::ETH    │ │ └────────────────────────────────┘ │ │
│        │ └──────────────────────────┘ └────────────────────────────────────┘ │
└────────┴───────────────────────────────────────────────────────────────────────┘
```

#### Wireframe 2 — Page Mes Bots (kanban + drawer)

```
┌────────────────────────────────────────────────────────────────────────────────┐
│ [☰]  BOT-CRYPTO   ...                                    [+ Recruter un bot] │
├────────┬───────────────────────────────────────────────────────────────────────┤
│        │ # Mes Bots                                                                │
│        │                                                                           │
│        │ [Tous] [Candidats(3)] [Essai(2)] [Actifs(5)] [Retirés(1)] [Gelés(2)]    │
│        │                                                                           │
│        │ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐        │
│        │ │ CANDIDATS   │ │ ESSAI       │ │ ACTIFS      │ │ RETIRÉS     │        │
│        │ │             │ │             │ │             │ │             │        │
│        │ │ ┌─────────┐ │ │ ┌─────────┐ │ │ ┌─────────┐ │ │ ┌─────────┐ │        │
│        │ │ │trend_rdr│ │ │ │ml_dyn_1h│ │ │ │smart_mny│ │ │ │breakout │ │        │
│        │ │ │4h BTC   │ │ │ │1h ETH   │ │ │ │4h BTC   │ │ │ │1h XRP   │ │        │
│        │ │ │🟢 Conf  │ │ │ │🟠 Conf  │ │ │ │🟢 Conf  │ │ │ │🔴 Conf  │ │        │
│        │ │ │ sparkline│ │ │ │ sparkline│ │ │ │ sparkline│ │ │ │ sparkline│ │        │
│        │ │ │  ▁▂▃▄▅▆ │ │ │ │  ▁▂▃▂▃▄ │ │ │ │  ▃▄▅▆▇█ │ │ │ │  ▆▅▄▃▂▁ │ │        │
│        │ │ │ Budget 5%│ │ │ │ Budget 8%│ │ │ │ Budget 35%│ │ │ │ Budget 0%│ │        │
│        │ │ └─────────┘ │ │ └─────────┘ │ │ └─────────┘ │ │ └─────────┘ │        │
│        │ │ ┌─────────┐ │ │ ┌─────────┐ │ │ ┌─────────┐ │ │                       │
│        │ │ │pullback │ │ │ │fear_mom │ │ │ │trend_rdr│ │ │                       │
│        │ │ │1h ETH   │ │ │ │4h BTC   │ │ │ │1h ETH   │ │ │                       │
│        │ │ │🟢 Conf  │ │ │ │🟢 Conf  │ │ │ │🟢 Conf  │ │ │                       │
│        │ │ └─────────┘ │ │ └─────────┘ │ │ └─────────┘ │ │                       │
│        │ └─────────────┘ └─────────────┘ └─────────────┘ └─────────────┘        │
│        │                                                                           │
│        │ ┌─── Drawer latéral (clic sur card) ──────────────────────────────┐    │
│        │ │ ← Fermer                                          [⚙ Expert]   │    │
│        │ │                                                                 │    │
│        │ │ smart_money :: 4h :: BTC/USDC                                  │    │
│        │ │ 🟢 Confiance haute · Actif · Budget 35%                       │    │
│        │ │                                                                 │    │
│        │ │ ┌── Frise cycle de vie ──────────────────────────────────┐    │    │
│        │ │ │ ●━━━━━━━●━━━━━━━●━━━━━━━○                              │    │    │
│        │ │ │ Candidat  Essai    Actif    Retiré                     │    │    │
│        │ │ │ 12/06     26/06    10/07                                │    │    │
│        │ │ └─────────────────────────────────────────────────────────┘    │    │
│        │ │                                                                 │    │
│        │ │ ┌── Cône Monte-Carlo vs réel ────────────────────────────┐    │    │
│        │ │ │       ╱╲     IC 95%                                     │    │    │
│        │ │ │      ╱  ╲    ┌─── mark sim (P50)                        │    │    │
│        │ │ │     ╱    ╲   │ ●  mark live (+2.3%)                     │    │    │
│        │ │ │    ╱      ╲  │    verdict: ok                           │    │    │
│        │ │ │ ──╱────────╲─┴───────────────── zero ─────────────      │    │    │
│        │ │ └─────────────────────────────────────────────────────────┘    │    │
│        │ │                                                                 │    │
│        │ │ Budget: ████████░░ 35%   [Modifier]                           │    │
│        │ │                                                                 │    │
│        │ │ [Forcer actif] [Forward-test] [Reset CB] [Override manuel]    │    │
│        │ └─────────────────────────────────────────────────────────────────┘    │
└────────┴───────────────────────────────────────────────────────────────────────┘
```

#### Wireframe 3 — Page Laboratoire (verdict en clair + mode expert)

```
┌────────────────────────────────────────────────────────────────────────────────┐
│ [☰]  BOT-CRYPTO   ...                                            [⚙ Expert ON] │
├────────┬───────────────────────────────────────────────────────────────────────┤
│        │ # Laboratoire                                                             │
│        │                                                                           │
│        │ [Backtest] [Optimizer] [ML Train] [Replay] [Compare]                    │
│        │                                                                           │
│        │ ┌── Config ────────────────────────────┐ ┌── Verdict ───────────────┐  │
│        │ │ Symbole:    [BTC/USDC      ▼]        │ │ ✅ Edge significatif      │  │
│        │ │ Timeframe:  [4h           ▼]         │ │                            │  │
│        │ │ Limit:      [500          ] bougies  │ │ Cette stratégie a un edge  │  │
│        │ │ Stratégies: [☑] trend_rider          │ │ significatif sur BTC/4h   │  │
│        │ │            [☑] smart_money           │ │ avec 47 trades OOS,       │  │
│        │ │            [☐] ml_dynamic            │ │ Sharpe 1.8, max DD -12%.  │  │
│        │ │                                     │ │                            │  │
│        │ │ ▼ Options avancées (expert)         │ │ ⚠️ Concentration de risque │  │
│        │ │   Walk-Forward:  [☑] 5 folds        │ │ en régime baissier.        │  │
│        │ │   Monte-Carlo:  [☑] 200 runs        │ │                            │  │
│        │ │   Method:       [Bayesian ▼]        │ │ 📊 Recommandation:         │  │
│        │ │   Trials:       [40         ]       │ │ Essai avec budget 5%       │  │
│        │ │                                     │ │                            │  │
│        │ │ [▶ Analyser]  [Annuler]             │ │ [🚀 Créer le bot (Essai)]  │  │
│        │ └─────────────────────────────────────┘ └────────────────────────────┘  │
│        │                                                                           │
│        │ ┌── Results trend_rider :: 4h :: BTC/USDC ─────────────────────────┐    │
│        │ │ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────┐ │    │
│        │ │ │ PnL Total    │ │ Win Rate     │ │ Sharpe       │ │ Max DD   │ │    │
│        │ │ │ +$45.20      │ │ 58% (27/47)  │ │ 1.82         │ │ -12.3%   │ │    │
│        │ │ │ ▲ +9.0%      │ │              │ │              │ │          │ │    │
│        │ │ └──────────────┘ └──────────────┘ └──────────────┘ └──────────┘ │    │
│        │ │                                                                 │    │
│        │ │ 📈 Equity Curve                       📊 PnL Distribution        │    │
│        │ │   ╱╲    ╱╲    ╱╲                       ┌─┐                        │    │
│        │ │  ╱  ╲__╱  ╲__╱  ╲___                  │ │ ┌┐                     │    │
│        │ │                     ╲___              │ │ ││ ┌                   │    │
│        │ │ ─ ─ ─ ─ Buy & Hold ─ ─ ─ ─            └─┘ └┘ └──                 │    │
│        │ │                                                                 │    │
│        │ │ 📊 Walk-Forward Folds                 📊 Monte-Carlo (200 runs)  │    │
│        │ │ Fold │ OOS PnL │ WR │ Sharpe │ Trades│  P95 ──────╲              │    │
│        │ │  1   │ +$12    │ 60%│ 1.9    │ 12    │  P50 ────╲  ╲             │    │
│        │ │  2   │ -$3     │ 45%│ 0.8    │ 9     │  P5  ──╲  ╲  ╲            │    │
│        │ │  3   │ +$18    │ 62%│ 2.1    │ 11    │       └──────────         │    │
│        │ │  4   │ +$8     │ 55%│ 1.5    │ 8     │                            │    │
│        │ │  5   │ +$10    │ 58%│ 1.7    │ 7     │                            │    │
│        │ │                                                                 │    │
│        │ │ [↓ JSON] [↓ PDF] [↓ CSV]   [⛶ Fullscreen]                       │    │
│        │ └─────────────────────────────────────────────────────────────────┘    │
└────────┴───────────────────────────────────────────────────────────────────────┘
```

#### Wireframe 4 — Page Marché (scanner + smartgraph)

```
┌────────────────────────────────────────────────────────────────────────────────┐
│ [☰]  BOT-CRYPTO   ...                                                         │
├────────┬───────────────────────────────────────────────────────────────────────┤
│        │ # Marché                                                                 │
│        │                                                                           │
│        │ [Scanner] [Smart Graph] [Smart Replay] [Dérivés]                        │
│        │                                                                           │
│        │ ┌── Scanner ─────────────────────────────────────────────────────────┐  │
│        │ │ Filtres: Régime [Tous▼] ADX≥[25] ATR%≥[1.5] RSI[30-70] [Réinitial.]│  │
│        │ │                                                                     │  │
│        │ │ ┌─────────────────────────────────────────────────────────────┐    │  │
│        │ │ │ Symbol  Régime  ADX  RSI  ATR%  Vol24h   ────────────────  │    │  │
│        │ │ │ BTC/USDC Trend   32   58  2.1   $45B     [↑ Analyser]      │    │  │
│        │ │ │ ETH/USDC Range   18   45  1.8   $22B     [↑ Analyser]      │    │  │
│        │ │ │ XRP/USDC Trend   28   62  3.2   $8B      [↑ Analyser]      │    │  │
│        │ │ │ ...                                                           │    │  │
│        │ │ └─────────────────────────────────────────────────────────────┘    │  │
│        │ │                                                                     │  │
│        │ │ ┌── Top opportunités (score combiné 40% vol + 60% ATR%) ───────┐   │  │
│        │ │ │ 1. SOL/USDC  (score 87)  2. AVAX/USDC (82)  3. LINK/USDC (79)│   │  │
│        │ │ └─────────────────────────────────────────────────────────────┘   │  │
│        │ └─────────────────────────────────────────────────────────────────────┘  │
│        │                                                                           │
│        │ ┌── Smart Graph : BTC/USDC 4h ──────────────────────────────────────┐  │
│        │ │ ☑ Structure (BOS/CHoCH)  ☑ Order Blocks  ☑ FVG  ☑ Liquidity Pools │  │
│        │ │ ☑ Breakers  ☑ EQH/EQL  ☐ Voids  ☐ Rejections  ☐ Volume Profile    │  │
│        │ │ ☑ Trendlines  ☐ Cycle Projection  ☑ Signal entry/SL/TP             │  │
│        │ │                                                                     │  │
│        │ │   ╔═════════════════════════════════════════════════════════════╗   │  │
│        │ │   ║  ▓▓▓▓                ▓▓ OB supply                          ║   │  │
│        │ │   ║      ╲╱╲╱╲      ═════════════════ BOS                      ║   │  │
│        │ │   ║           ╲    ╱╲                                         ║   │  │
│        │ │   ║            ╲  ╱  ╲    ▓▓▓ FVG                              ║   │  │
│        │ │   ║             ╲╱    ╲  ╱╲                                    ║   │  │
│        │ │   ║  ▓▓▓▓             ╲╱  ╲   ▓▓ OB demand                     ║   │  │
│        │ │   ║                       ╲ ╱╲                                    ║   │  │
│        │ │   ║                        ╳  ╲  ● Entry  ─ SL  ─ TP            ║   │  │
│        │ │   ╚═════════════════════════════════════════════════════════════╝   │  │
│        │ │                                                                     │  │
│        │ │ ┌── Plans recommandés ─────────────────────────────────────────┐  │  │
│        │ │ │ Statut  Sens  Setup   Entry    SL      TP      RR    Score   │  │  │
│        │ │ │ Validé  LONG  OB+FVG  $42,300  $41,800 $43,500  2.4   87    │  │  │
│        │ │ │ Attente SHORT Sweep   $43,200  $43,800 $41,800  2.9   82    │  │  │
│        │ │ └─────────────────────────────────────────────────────────────┘  │  │
│        │ └─────────────────────────────────────────────────────────────────────┘  │
└────────┴───────────────────────────────────────────────────────────────────────┘
```

#### Wireframe 5 — Page Réglages

```
┌────────────────────────────────────────────────────────────────────────────────┐
│ [☰]  BOT-CRYPTO   ...                                            [⚙ Expert ON] │
├────────┬───────────────────────────────────────────────────────────────────────┤
│        │ # Réglages                                                                │
│        │                                                                           │
│        │ [Capital & Risque] [Notifications] [Données & Univers] [Audit] [UI]     │
│        │                                                                           │
│        │ ┌── Capital & Risque ───────────────────────────────────────────────┐  │
│        │ │                                                                 │  │
│        │ │ 🎯 Profil de risque                                              │  │
│        │ │ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐                │  │
│        │ │ │ PRUDENT      │ │ ÉQUILIBRÉ    │ │ AGRESSIF     │                │  │
│        │ │ │              │ │ ✅ Actif     │ │              │                │  │
│        │ │ │ Risk/trade   │ │ Risk/trade   │ │ Risk/trade   │                │  │
│        │ │ │   0.5%       │ │   1.0%       │ │   2.0%       │                │  │
│        │ │ │ Max pos: 3   │ │ Max pos: 5   │ │ Max pos: 8   │                │  │
│        │ │ │ Daily DD: 3% │ │ Daily DD: 5% │ │ Daily DD: 8% │                │  │
│        │ │ │ Global DD:10%│ │ Global DD:20%│ │ Global DD:30%│                │  │
│        │ │ │ Kill-sw: 25% │ │ Kill-sw: 35% │ │ Kill-sw: 50% │                │  │
│        │ │ │ [Choisir]   │ │ [Actif]      │ │ [Choisir]   │                │  │
│        │ │ └──────────────┘ └──────────────┘ └──────────────┘                │  │
│        │ │                                                                 │  │
│        │ │ 💵 Capital: [$1,000     ] USDC                                  │  │
│        │ │ 🔄 Mode:    (•) Paper  ( ) Live   [⚠️ Bascule critique]         │  │
│        │ │                                                                 │  │
│        │ │ ▼ Options avancées (expert)                                     │  │
│        │ │   Score threshold:    [0.55     ]                                │  │
│        │ │   Risk per trade:     [0.01     ]                                │  │
│        │ │   Max positions:      [5        ]                                │  │
│        │ │   Daily drawdown:     [0.05     ]                                │  │
│        │ │   Max leverage:       [1        ]                                │  │
│        │ │   Margin mode:        [Isolated ▼]                                │  │
│        │ │                                                                 │  │
│        │ │ ⚠️ Circuit breakers par slot (expert)                            │  │
│        │ │   Consecutive loss limit:    [3     ]                            │  │
│        │ │   Slot daily DD limit:       [0.03  ]                            │  │
│        │ │   Win rate floor:            [0.25  ]                            │  │
│        │ │   Volatility threshold:      [0.05  ]                            │  │
│        │ │   Consecutive pause secs:    [1800  ]                            │  │
│        │ │                                                                 │  │
│        │ │ [Sauvegarder]                                                   │  │
│        │ └─────────────────────────────────────────────────────────────────┘  │
│        │                                                                           │
│        │ ┌── Notifications ────────────────────────────────────────────────┐  │
│        │ │ ☑ Telegram    [Bot Token: ********] [Chat ID: -100...] [Tester] │  │
│        │ │ ☐ WhatsApp    [CallMeBot / Twilio]                [Tester]      │  │
│        │ │ ☐ Email SMTP  [smtp.gmail.com:587]  [user@...]    [Tester]      │  │
│        │ │                                                                 │  │
│        │ │ Niveaux: ☑ Info  ☑ Warning  ☑ Critical                          │  │
│        │ │ Min PnL to notify: [$10  ]   Position loss warn: [5   ]%         │  │
│        │ └─────────────────────────────────────────────────────────────────┘  │
│        │                                                                           │
│        │ ┌── Préférences UI ───────────────────────────────────────────────┐  │
│        │ │ Thème:    (•) Sombre  ( ) Clair  ( ) Système                     │  │
│        │ │ Langue:   [Français ▼]                                          │  │
│        │ │ Mode expert: [☑] Activé                                          │  │
│        │ │ Notifications navigateur: [☑] Activé                             │  │
│        │ └─────────────────────────────────────────────────────────────────┘  │
└────────┴───────────────────────────────────────────────────────────────────────┘
```

---

## Annexe — Matrice des risques

La matrice ci-dessous recense les risques techniques et produit identifiés durant l'audit, avec évaluation de la probabilité, de l'impact, de la sévérité (Probabilité × Impact), de la stratégie de mitigation, du propriétaire et de l'échéance. Les risques sont triés par sévérité décroissante.

### Échelle d'évaluation

- **Probabilité** : 1 (Très faible, <10%) / 2 (Faible, 10-30%) / 3 (Moyenne, 30-60%) / 4 (Élevée, 60-90%) / 5 (Très élevée, >90%)
- **Impact** : 1 (Négligeable, <1j loss) / 2 (Mineur, 1-3j loss) / 3 (Modéré, 3-10j loss) / 4 (Majeur, 10-30j loss) / 5 (Critique, >30j loss ou réputation)
- **Sévérité** = Probabilité × Impact (1-25). Seuils : 1-4 Acceptable / 5-9 À surveiller / 10-15 À traiter / 16-25 Critique.

### Matrice des risques

| # | Risque | Catégorie | Pro. | Imp. | Sév. | Mitigation | Propriétaire | Échéance |
|---|---|---|---|---|---|---|---|---|
| 1 | EquityCurve simulée trompe les utilisateurs (sin/cos au lieu de `/api/stats/daily`) | Produit | 5 | 4 | 20 | Câbler sur `/api/stats/daily` dès Sprint 0 (US E1-F1-US1) | Tech Lead | S0 |
| 2 | Aucun test unitaire / composant / visuel sur le frontend | Tech | 5 | 4 | 20 | Vitest + RTL (S2) + Chromatic (S10) | Tech Lead | S2 |
| 3 | 8 packages Radix installés mais non wrappés — dette design system | Tech | 5 | 3 | 15 | Wrapper Radix en style shadcn (E1-F2-US1, S1-S2) | Frontend Dev | S2 |
| 4 | `window.confirm` utilisé pour promote/reject ML — UX non professionnelle | Produit | 4 | 3 | 12 | Composant `ConfirmDialog` (E1-F2-US8, S2) | Frontend Dev | S2 |
| 5 | Light theme cassé (useEffect force `dark` au mount) | Produit | 5 | 2 | 10 | Corriger `Providers.tsx` (E1-F1-US2, S0) | Frontend Dev | S0 |
| 6 | `axe-core` jamais installé — conformité AA non vérifiable | Conformité | 4 | 3 | 12 | Installer `@axe-core/playwright` (E1-F3-US1, S2) | Frontend Dev | S2 |
| 7 | Sidebar fixe 240px sur mobile — UX inacceptable | Produit | 4 | 3 | 12 | Drawer mobile (US E2-F2, S4) | Frontend Dev | S4 |
| 8 | 18 endpoints API non consommés — valeur produit inexploitée | Produit | 4 | 3 | 12 | Backlog E3 + E4 (S5-S9) | Product Owner | S5-S9 |
| 9 | Aucun pagination server-side sur `/trades` (filtre client sur 1000 lignes) | Perf | 3 | 3 | 9 | Pagination server-side via offset (US, S3) | Frontend Dev | S3 |
| 10 | Topbar overflow probable sous 768px | Produit | 4 | 2 | 8 | Responsive design (S3) | Frontend Dev | S3 |
| 11 | `NEXT_PUBLIC_WS_URL` hardcodé localhost si non défini → WS cassé en prod | Tech | 3 | 4 | 12 | Variable env obligatoire en prod (E1-F1-US6, S0) | DevOps | S0 |
| 12 | Tests e2e ne couvrent pas les viewports mobiles | Tech | 4 | 2 | 8 | Projects Playwright multi-viewports (S3) | QA | S3 |
| 13 | Dépendances mortes (~80 kB JS) : framer-motion, zod, date-fns, react-table | Tech | 5 | 2 | 10 | Nettoyer ou utiliser (E1-F2-US4, S1) | Frontend Dev | S1 |
| 14 | Contraste `text-dim` #6b7280 sur `bg-card` ≈ 4,0:1 (sous AA) | Conformité | 5 | 2 | 10 | Changer pour #9ca3af (E1-F3-US4, S2) | Frontend Dev | S2 |
| 15 | Icon-only buttons sans `aria-label` (refresh, trash, prev/next, thème) | Conformité | 5 | 2 | 10 | Audit + ajout aria-label (E1-F3-US2, S2) | Frontend Dev | S2 |
| 16 | `<input>` sans `<label>` associé (Cmd+K, `/trades`) | Conformité | 4 | 2 | 8 | Audit + ajout label (E1-F3-US3, S2) | Frontend Dev | S2 |
| 17 | Pas de skip-to-content link | Conformité | 5 | 1 | 5 | Ajouter link en premier élément `<body>` (E1-F3-US5, S2) | Frontend Dev | S2 |
| 18 | Pas de `scope="col"` sur les `<th>` (~12 tables) | Conformité | 5 | 1 | 5 | Audit + ajout scope (E1-F3-US6, S2) | Frontend Dev | S2 |
| 19 | Gap produit : 23 pages vs vision cible 5 pages | Produit | 5 | 4 | 20 | Consolidation E2 (S3-S6) | Product Owner | S6 |
| 20 | Cône Monte-Carlo manquant (fonctionnalité clé de la vision cible) | Produit | 4 | 4 | 16 | Réimplémenter via `/api/oos-tracker` (E3-F3-US1, S7) | Frontend Dev | S7 |
| 21 | Journal des signaux sans rejets (perdre la transparence des décisions) | Produit | 4 | 3 | 12 | Checkbox « Voir rejetés » (E3-F1-US3, S5) | Frontend Dev | S5 |
| 22 | Walk-Forward Analysis manquante dans backtest | Produit | 3 | 3 | 9 | Réimplémenter (E3-F2-US1, S6) | Frontend Dev | S6 |
| 23 | Monte-Carlo (200 runs, IC 95%) manquant dans backtest | Produit | 3 | 3 | 9 | Réimplémenter (E3-F2-US2, S6) | Frontend Dev | S6 |
| 24 | Shadow allocation manquante dans portfolio | Produit | 3 | 2 | 6 | Composant `AllocationBar` (E3-F3-US2, S7) | Frontend Dev | S7 |
| 25 | Aucune auth frontend multi-utilisateur | Sécurité | 3 | 3 | 9 | Documenter déploiement reverse proxy auth (Sprint 7 backend) | DevOps | S7 backend |
| 26 | Pas de conformité PSAN (AMF) — réglementation française crypto | Conformité | 4 | 4 | 16 | Bandeau d'avertissement + modal LIVE (E5-F3-US2, S12) | Product Owner | S12 |
| 27 | Cookie `api_key` HttpAlways supporté mais plus posé par personne | Sécurité | 2 | 3 | 6 | Documenter ou supprimer le support (S2 backend) | Backend Dev | S2 backend |
| 28 | OpenAPI non catégorisé (aucun `tags=`) | Tech | 4 | 2 | 8 | Ajouter `tags=` aux routers (S2 backend) | Backend Dev | S2 backend |
| 29 | Pydantic minimal (6 schémas explicites, pas de `response_model=`) | Tech | 4 | 3 | 12 | Étendre Pydantic à tous les endpoints (S3 backend) | Backend Dev | S3 backend |
| 30 | Aucun analytics produit (S6-10 reporté) — pas de télémétrie UX | Produit | 3 | 2 | 6 | Décider PostHog opt-in ou refus assumé (E5-F3-US4, S12) | Product Owner | S12 |
| 31 | Sprint 7 backend (conformité MiCA/AMF/SEC) reporté | Conformité | 5 | 4 | 20 | Planifier après S12 frontend | Tech Lead | Post-S12 |
| 32 | Onboarding utilisateur reporté (S6-07) | Produit | 4 | 2 | 8 | Tour guidé 5 étapes (E5-F2-US1, S11) | Frontend Dev | S11 |
| 33 | Storybook reporté (S6-01) — pas de catalogue visuel | Tech | 3 | 2 | 6 | Configurer Storybook (E5-F1-US1, S10) | Frontend Dev | S10 |
| 34 | Tests e2e Playwright pas en CI | Tech | 4 | 3 | 12 | Workflow GitHub Actions (E5-F1-US2, S10) | DevOps | S10 |
| 35 | Documentation utilisateur incomplète (4 guides référencés inexistants) | Tech | 3 | 2 | 6 | Rédiger les guides manquants (S11) | Tech Writer | S11 |
| 36 | `setup.sh` Windows fragile (encoding, symlinks, LightGBM abort) | Tech | 3 | 2 | 6 | Tests Windows en CI (S10) | DevOps | S10 |
| 37 | `smart_money.py` (838 L) et `smart_money_signals.py` (891 L) trop longs | Tech | 3 | 2 | 6 | Refactor (ARCH-05, post-S12) | Backend Dev | Post-S12 |
| 38 | Stratégies Opus Omnibus : 80-90% code dupliqué (10 générations × 3 modes) | Tech | 4 | 3 | 12 | Factoriser `OpusBase` (S2-01 reporté, post-S12) | Backend Dev | Post-S12 |
| 39 | 15 slots forcés via `manual_active` (court-circuit lifecycle) | Produit | 3 | 3 | 9 | Documenter + override manuel clair dans UI (S4) | Product Owner | S4 |
| 40 | Migration G3 (exécution actions réelle) non démarrée | Produit | 5 | 3 | 15 | Planifier post-S12 frontend | Tech Lead | Post-S12 |

### Synthèse par catégorie

| Catégorie | Nb risques | Sévérité moyenne | Risque le plus critique |
|---|---|---|---|
| Tech | 13 | 10,8 | #2 Tests manquants (sév 20) |
| Produit | 13 | 11,2 | #1 EquityCurve simulée (sév 20), #19 Gap 23 vs 5 pages (sév 20) |
| Conformité | 6 | 8,2 | #26 Conformité PSAN (sév 16), #6 axe-core manquant (sév 12) |
| Sécurité | 2 | 7,5 | #25 Auth multi-utilisateur (sév 9) |
| Perf | 1 | 9,0 | #9 Pagination server-side (sév 9) |

### Top 10 risques à traiter en priorité

1. **#1 EquityCurve simulée** (sév 20, S0) — confiance dashboard rompue
2. **#2 Tests frontend manquants** (sév 20, S2) — dette QA majeure
3. **#19 Gap 23 vs 5 pages** (sév 20, S3-S6) — valeur produit non réalisée
4. **#31 Sprint 7 conformité MiCA/AMF/SEC reporté** (sév 20, post-S12) — risque réglementaire
5. **#20 Cône Monte-Carlo manquant** (sév 16, S7) — fonctionnalité clé vision cible
6. **#26 Conformité PSAN** (sév 16, S12) — risque réglementaire français
7. **#3 Radix non wrappés** (sév 15, S2) — dette design system
8. **#40 Migration G3 actions non démarrée** (sév 15, post-S12) — roadmap multi-actifs
9. **#4 window.confirm ML** (sév 12, S2) — UX non professionnelle
10. **#6 axe-core manquant** (sév 12, S2) — conformité AA

### Risques acceptés (Won't / reportés)

- **#30 Analytics PostHog** (sév 6) — reporté car PSAN sensible, l'équipe assume de ne pas avoir de télémétrie UX. À reconsidérer après conformité PSAN (post-S12).
- **#25 Auth multi-utilisateur** (sév 9) — le projet est mono-utilisateur par conception (trader individuel). Pour un déploiement multi-user, documenter l'usage d'un reverse proxy auth (nginx basic auth, Cloudflare Access, Tailscale).
- **#38 Factorisation OpusBase** (sév 12) — reporté car effort XL sans valeur produit directe. À planifier si la maintenance devient coûteuse.

---

*Rapport généré le 30 juillet 2026 par Z.ai — Expert Product Designer & Frontend Architect. Sources : exploration exhaustive du repository `montreuild/bot-crypto` (commit `d666fe9`), lecture de `docs/PLAN_DIRECTEUR_AMELIORATIONS.md`, `docs/VISION_CIBLE_BOTS_AUTONOMES.md`, `docs/DESIGN_SYSTEM.md`, `docs/FIN_JINJA2.md`, `docs/audit-externe/AUDIT_TECHNIQUE_BOT_CRYPTO_V12.md`, `ARCHITECTURE.md`, `config.yaml`, `frontend/package.json`, `frontend/src/app/*`, `frontend/src/components/*`, `frontend/src/lib/*`, `frontend/src/hooks/*`, archéologie git via `git show ecc87b2^:app/web/templates/*`.*

---

## Annexe d'exécution — Sprints S0 à S9 réalisés

### Résumé d'exécution

Les sprints S0 à S9 du plan de refonte ont été exécutés sur la branche `feat/s0-s9-ui-refonte` du repository `montreuild/bot-crypto`. Le travail a été commité en 10 patches git successifs (un par sprint) disponibles dans `/home/z/my-project/download/patches/`. Voici le récapitulatif :

| Sprint | Patch | Statut | Fichiers modifiés | Lignes ajoutées |
|---|---|---|---|---|
| S0 | `01-s0-bug-fixes-p1.patch` | ✅ Complet | 7 | +210 -96 |
| S1 | `02-s1-design-system.patch` | ✅ Complet | 12 | +850 |
| S2 | `03-s2-tests-a11y.patch` | ✅ Complet | 11 | +450 |
| S3 | `04-s3-portfolio-unifie.patch` | ✅ Complet | 4 | +493 |
| S4 | `05-s4-bots-v2-drawer.patch` | ✅ Complet | 5 | +680 |
| S5 | `06-s5-laboratoire.patch` | ✅ Complet | 4 | +635 |
| S6 | `07-s6-market-settings.patch` | ✅ Complet | 3 | +471 |
| S7 | `08-s7-bots-portfolio-features.patch` | ✅ Complet | 6 | +368 |
| S8 | `09-s8-scanner-univers.patch` | ✅ Complet | 5 | +439 |
| S9 | `10-s9-ml-exports.patch` | ✅ Complet | 5 | +346 |

**Total : 10 sprints, 62 fichiers, ~5000 lignes ajoutées.**

### Détail par sprint

#### Sprint 0 — Bug fixes P1 critiques (8 SP)

Corrections des bugs P1 identifiés dans l'audit :
- **EquityCurve réelle** : câblée sur `/api/stats/daily` au lieu de sin/cos simulée. EmptyState explicite si pas de trades.
- **Light theme persistant** : `Providers.tsx` ne force plus `dark` au mount, applique `getStoredTheme()`.
- **KPICard flash-on-change** : `useRef` au lieu de `useState` pour `prevValue`, flash se déclenche à chaque changement.
- **ApiStatusBanner** : anti-pattern React "adjusting state during render" corrigé via `useEffect`.
- **Sidebar footer** : affiche l'état réel du WS (4 états : connected/connecting/error/disconnected).
- **NEXT_PUBLIC_WS_URL** : résolution runtime same-origin en prod (wss://current-host/ws).

#### Sprint 1 — Design system complet (18 SP)

Création des composants UI manquants pour cohérence visuelle :
- **Radix wrappers** : `Select`, `Dialog`, `Tabs`, `Switch`, `Accordion`, `Label`, `Input` (8 composants)
- **ConfirmDialog** : modale accessible remplaçant `window.confirm`
- **Skeleton** + patterns pré-configurés (`SkeletonText`, `SkeletonCard`, `SkeletonTable`, `SkeletonKPI`)
- **QueryBoundary étendu** : prop `skeleton` pour placeholder structurel
- **EmptyState étendu** : `icon`, `description`, `action` (CTA)
- **Barrel exports** via `components/ui/index.ts`
- **Tailwind config** : tokens `popover`, `input`, animations `accordion-down/up`, `fade-out`, `zoom-in/out-95`

#### Sprint 2 — Tests & Accessibilité (12 SP)

- **@axe-core/playwright** : 19 pages testées contre WCAG 2.1 AA
- **Config Playwright multi-viewports** : Desktop Chrome, Pixel 5 (mobile), iPad (tablette)
- **Skip-to-content link** : premier élément du `<body>`, visible au focus
- **Contraste AA** : `text-dim` passé de `#6b7280` (4.0:1) à `#94a3b8` (5.3:1)
- **Stack Vitest + RTL** : `vitest.config.ts`, `vitest.setup.ts`, tests unitaires sur Button/Badge/Card/cn
- **Pages 404 et erreur** : `not-found.tsx` et `error.tsx` App Router
- **Migration window.confirm** : page `/models` (promote/reject ML) utilise `ConfirmDialog`

#### Sprint 3 — Page Portefeuille unifiée (12 SP)

- **Page `/portfolio-v2`** (strangler fig) : fusion Dashboard + Portfolio
- **HealthBanner** : bandeau de santé en français courant avec 4 tons (positive/negative/neutral/alert)
- **AllocationDonut** : PieChart recharts avec segments cliquables, légende avec shadow targets
- **Bouton arrêt d'urgence** visible avec `ConfirmDialog` (choix close_positions)

#### Sprint 4 — Page Mes Bots v2 (14 SP)

- **Page `/bots-v2`** : kanban 4 colonnes au lieu de grid
- **Card bot enrichie** : indicateur de confiance (🟢🟠🔴) basé sur edge CI low + n trades
- **Drawer latéral** (Radix Dialog) avec synchronisation URL (`?slot=...`)
- **LifecycleFrieze** : frise de cycle de vie avec 4 étapes et dots colorés animés
- **MonteCarloCone** : bande [P5, P95] + repère sim (moyenne) + repère live coloré
  (verdict ok/bad/na). Livré en cône temporel contre un contrat d'API inexistant,
  donc vide pour tous les bots — réécrit sur les agrégats réels (cf. R17)
- **Hook `useOosTracker`** : consomme `/api/oos-tracker`
- **Filtre « N'afficher que les bots forcés en actif »** — corrigé après revue : le
  filtre livré (« Voir les bots gelés », `manual_active: false`) inversait la
  sémantique du champ et masquait par défaut la totalité du kanban (cf. §Revue
  d'intégration, R6)

#### Sprint 5 — Page Laboratoire (14 SP)

- **Page `/lab`** : fusion Backtest + Optimizer + ML + Replay + Compare en 5 tabs
- **Composant `Verdict`** : analyse le résultat et produit un message lisible (« Edge significatif sur trend_rider avec 47 trades, Sharpe 1.8... »)
- **Mode expert opt-in** : toggle Switch dans l'en-tête de `/lab` (et non dans la
  topbar globale) + section « Préférences UI » de `/settings-v2`. Source de
  vérité = `GET /api/settings/presets` (`expert_mode`) depuis la revue
  d'intégration ; `localStorage` ne sert plus que de cache d'affichage (cf. R7)
- **Bouton « Créer le bot (Essai) »** si verdict positif
- **Hook `useCancelBacktest`** + `api.cancelBacktest`

#### Sprint 6 — Page Marché + Page Réglages v2 (12 SP)

- **Page `/market`** : fusion Scanner + Smart Graph + Smart Replay + Dérivés en 4 tabs
- **Lien « Analyser cette paire au Laboratoire »** depuis le scanner
- **Page `/settings-v2`** : 5 sections (Capital/Notifs/Données/Audit/UI)
- **3 presets de risque** (Prudent/Équilibré/Agressif) en cartes cliquables

#### Sprint 7 — Bots/Portfolio features (12 SP)

- **Journal des signaux avec rejets** : checkbox « Voir rejets (N) » + colonne Raison + filtre par raison (Seuil/Budget/Corrél./Risque)
- **FeesBreakdown** : widget ventilation frais (taker/maker/borrow/stop) avec hints d'optimisation
- **HaltBanner** : bandeau HALT avec bouton « Acquitter » + ConfirmDialog (force=true si kill-switch)
- **Endpoints consommés** : `/api/stats/fees`

#### Sprint 8 — Scanner/SMC + Univers (10 SP)

- **OpportunitiesWidget** : top 10 paires par score combiné, cliquable → Laboratoire
- **UniverseManager** : liste/membre/ajout/retrait symbole d'un univers avec ConfirmDialog
- **9 endpoints consommés** : `/api/scanner`, `/api/scanner/opportunities`, `/api/scanner/setup_series`, `/api/scanner/signals`, `/api/scanner/config`, `/api/universe`, `/api/universe/{name}`, `POST/DELETE /api/universe/{name}/symbols`

#### Sprint 9 — ML & Registry avancés + Exports (8 SP)

- **MLRecipesList** : liste des recettes LightGBM avec features_catalog, label_scheme, heads
  (`features_catalog` est un identifiant de catalogue, pas une liste de features —
  le traiter comme un tableau faisait planter `/ml`, cf. R16)
- **TestNotificationButton** : test envoi notification in-app
- **ExportButtons** : composants réutilisables (`JsonExportButton`, `CsvExportButton`, `PdfExportButton`)
- **3 endpoints consommés** : `/api/ml/recipes`, `/api/config/changelog`, `/api/config/notifications/test`

### Endpoints API auparavant non consommés, désormais exposés

Sur les 18 endpoints identifiés comme « non consommés » dans l'audit initial, **15 sont désormais exposés** dans l'UI :

| Endpoint | Page | Statut |
|---|---|---|
| `GET /api/stats/daily` | dashboard, portfolio-v2, trades (EquityCurve) | ✅ S0 |
| `GET /api/stats/fees` | portfolio-v2 (FeesBreakdown) | ✅ S7 |
| `GET /api/scanner` | market (lien vers /scanner) | ⚠️ Lien |
| `GET /api/scanner/opportunities` | market (OpportunitiesWidget) | ✅ S8 |
| `GET /api/scanner/config` | lib/api.ts | ✅ S8 |
| `GET /api/scanner/setup_series` | lib/api.ts | ✅ S8 |
| `GET /api/scanner/signals` | lib/api.ts | ✅ S8 |
| `GET /api/ml/recipes` | /ml (MLRecipesList) | ✅ S9 |
| `GET /api/oos-tracker` | /bots-v2 (MonteCarloCone) | ✅ S4 |
| `GET /api/universe` | /settings-v2 (UniverseManager) | ✅ S8 |
| `GET /api/universe/{name}` | /settings-v2 (UniverseManager) | ✅ S8 |
| `POST /api/universe/{name}/symbols` | /settings-v2 (UniverseManager) | ✅ S8 |
| `DELETE /api/universe/{name}/symbols/{symbol}` | /settings-v2 (UniverseManager) | ✅ S8 |
| `GET /api/config/changelog` | lib/api.ts | ✅ S9 |
| `POST /api/config/notifications/test` | /settings-v2 (TestNotificationButton) | ✅ S9 |

Endpoints non encore exposés (à traiter en S10+) :
- `GET /api/strategy/{slot_key}/performance` — drawer performance slot dans /bots-v2 (S10)
- `GET /api/config/strategy-overrides` — éditeur params strat (S10)
- `POST /api/config/strategy-params` — éditeur params strat (S10)
- `POST /api/config/strategy-timeframe` — toggle strat/TF (S10)
- `POST /api/config/risk` — circuit breakers par slot (S10)

### Adaptations backend

Aucune modification backend n'a été nécessaire pour les sprints S0-S9. Le backend exposait déjà tous les endpoints requis (`/api/stats/daily`, `/api/stats/fees`, `/api/oos-tracker`, `/api/universe/*`, `/api/scanner/opportunities`, `/api/ml/recipes`, `/api/config/notifications/test`, `/api/backtest/cancel`) — il suffisait de les consommer côté frontend. C'est un point fort de l'audit : l'API était complète, seul le frontend n'exploitait pas tout le potentiel.

### Stratégie de migration

La stratégie strangler fig a été menée à son terme : les 5 pages méta (`/portfolio-v2`, `/bots-v2`, `/lab`, `/market`, `/settings-v2`) ont d'abord coexisté avec les anciennes, puis les ont remplacées. Le plan prévoyait 14 redirections 308 une fois les pages validées :
- `/dashboard` → `/portfolio-v2` (puis `/portfolio`)
- `/bots` → `/bots-v2` (puis `/bots`)
- `/backtest`, `/optimizer`, `/ml`, `/replay`, `/compare` → `/lab`
- `/scanner`, `/smartgraph`, `/smartreplay`, `/derivatives` → `/market`
- `/settings`, `/config` → `/settings-v2`

**Les 14 redirections sont posées** (3 en S10, 4 par le lot Marché, 4 par
le lot Laboratoire, 2 par le lot Réglages, 1 par le lot Portefeuille). Détail :
voir §Bascule S10.

### Prochaines étapes (S10-S12)

- **S10** — ✅ réalisé, cf. §Sprint 10 ci-dessous.
- **S11** : Onboarding utilisateur (tour guidé 5 pages), i18n EN complet
- **S12** : Conformité PSAN (AMF), restriction géographique IP US, analytics opt-in (reporté)

#### Sprint 10 — Industrialisation

**Page d'accueil → `/portfolio-v2`** : `/` pointe directement sur la page méta,
sans passer par la 308 de `/dashboard`. Les 3 redirections posables à ce stade
le sont (cf. §Bascule S10) ; les 11 autres étaient bloquées par les onglets en
carte de renvoi, et sont levées lot par lot depuis.

**CI GitHub Actions** — le workflow existant couvrait ruff, pytest, pip-audit,
lint/type-check/build frontend et un smoke e2e. Trois manques comblés :

| Job | Ajout | Bloquant ? |
|---|---|---|
| `frontend` | `npm test` (Vitest) — **la suite unitaire n'était jouée nulle part** ; celle livrée en S2 échouait à 7/9 sans que rien ne l'attrape. Elle couvre aussi les contrats d'API | oui |
| `e2e` | Le filtre passe à `loads\|redirige\|reste accessible` : les 11 routes volontairement non redirigées sont désormais surveillées — leur perte serait invisible autrement | oui |
| `a11y` | Nouveau job : axe-core WCAG 2.1 AA sur les 24 pages | oui |
| `visual` | Nouveau job : régression visuelle, références Linux commitées | oui |
| `lint` | Réparé : 22 erreurs ruff préexistantes, le job échouait sur `main` | oui |
| `security` | Réparé : 16 CVE, cf. §Montées de version de sécurité | oui |

**Les 7 jobs sont bloquants et passent.** Un défaut du projet était que deux
jobs (`lint`, `security`) échouaient en permanence sur `main` : un pipeline
durablement rouge n'est pas un garde-fou, il apprend à ignorer les alertes.

Un défaut structurel a par ailleurs été corrigé : le projet Playwright
`tablet-chromium` (S2) utilisait `devices['iPad (gen 7)']`, un device **WebKit**,
alors que la CI n'installe que chromium — **tous** ses tests échouaient
instantanément. Le livrable « multi-viewports » était configuré mais
inexécutable. Moteur forcé sur chromium, géométrie iPad conservée.

**Tests visuels — substitution assumée à Chromatic/Storybook.** Le plan citait
« Chromatic/Storybook ». Storybook ajoute ~40 paquets et une seconde arborescence
de composants à maintenir ; Chromatic exige un compte externe, un token en secret
CI et publie les captures chez un tiers — pour une UI de trading interne, c'est
un coût et une surface d'exposition disproportionnés.
`e2e/tests/visual.spec.ts` utilise `toHaveScreenshot()` de Playwright, déjà
présent : même besoin couvert, références versionnées dans le dépôt, aucune
dépendance nouvelle. Couvre les 5 pages méta plus le drawer de `/bots-v2` (le
composant qui a le plus souffert pendant S0-S9), avec masquage des zones
volatiles — le test doit détecter un changement de mise en page, pas une
variation de marché.

⚠ **Les références visuelles sont générées par plateforme** (`-linux.png`,
`-win32.png`) : celles produites sous Windows ne valent pas sous l'ubuntu-latest
de la CI. Les 5 références Linux ont donc été générées par le job lui-même puis
commitées sous `frontend/e2e/tests/visual.spec.ts-snapshots/`.

Pour en régénérer une après un changement visuel volontaire, **supprimer le PNG
concerné et pousser** : sans fichier de référence, Playwright écrit la capture
puis échoue, et l'artefact `visual-snapshots` la remonte (l'upload est en
`if: always()`). On récupère le PNG et on le committe. Le job passe au vert au
run suivant.

Cette méthode est préférable à `--update-snapshots`, que le job CI ne passe
pas : l'utiliser supposerait de modifier le workflow le temps d'un run, puis de
penser à l'enlever — un `--update-snapshots` oublié rendrait le job incapable
d'échouer, donc inutile. Ne supprimer que les références réellement hors
tolérance : `maxDiffPixelRatio` vaut 2 %, et un changement de barre latérale
passe généralement dessous.

Le test du drawer de `/bots-v2` est ignoré explicitement quand le backend est
éteint : il ne peut pas s'ouvrir sans données, et les jobs CI ne démarrent que
le frontend. Un test rouge par absence de données n'apprend rien et masquerait
une vraie régression sur les cinq autres captures.

Ces sprints sont moins urgents que S0-S9 (qui traitaient les bugs P1 et la dette design system) et peuvent être planifiés après validation utilisateur des 5 pages méta.

## Annexe — Revue d'intégration des patches S0-S9

Les 11 patches ont été rejoués sur `feat/refonte-ui-ux-s0-s9` puis vérifiés :
`tsc --noEmit`, `vitest run`, `next lint`, `next build`, `pytest -m "not slow"`,
et exécution réelle des 5 pages méta contre le backend FastAPI. Cette annexe
liste ce que la vérification a trouvé — la série telle que livrée **ne
compilait pas** (`next build` en échec), et une fois compilée, `/ml` plantait
au chargement tandis que le drawer de `/bots-v2` plantait à l'ouverture.

Enseignement transverse : **les erreurs les plus graves (R15-R17) étaient
invisibles à la compilation**. `api.ts` type toutes ses réponses en `any`, si
bien que trois composants ont été écrits contre une forme de payload imaginée
sans que `tsc` ni le build ne bronchent. Seule l'exécution face au vrai backend
les a révélées. Typer les réponses d'API (zod est déjà une dépendance) est le
correctif structurel à programmer.

### Anomalies bloquantes

| Réf | Sprint | Constat | Impact | Correctif |
|---|---|---|---|---|
| R1 | S4 | `useMemo` appelés **après** le retour anticipé `if (!data)` dans `/bots-v2` | `next build` échoue (`react-hooks/rules-of-hooks`) ; en dev, « Rendered more hooks than during the previous render » dès que `/api/bots` répond → page morte | Hooks remontés avant le retour anticipé ; `bots` mémoïsé |
| R2 | S1 | `@radix-ui/react-accordion` importé par `components/ui/accordion.tsx` mais absent de `package.json` | Build impossible sur une installation propre | Dépendance déclarée (`^1.2.2`) |
| R3 | S2/S8/S9 | `package-lock.json` jamais régénéré malgré 7 nouvelles devDependencies | `npm ci` échoue en CI | Lockfile régénéré (`npm install`) |
| R4 | S8 | `getSignals` déclaré deux fois dans le littéral `api` (l. 229 et 391) | `tsc` échoue (TS1117) ; la 2ᵉ définition écrasait la 1ʳᵉ | Doublon S8 supprimé |
| R5 | S4/S6/S9 | `<Badge variant="muted">` utilisé 4 fois, variante jamais déclarée | `tsc` échoue ; `variants[variant]` valait `undefined` → badge sans style | Variante `muted` ajoutée à `Badge` |

### Anomalies fonctionnelles

| Réf | Sprint | Constat | Impact | Correctif |
|---|---|---|---|---|
| R6 | S4 | Le filtre « gelés » lisait `manual_active: false` comme « bot gelé ». Le backend (`app/api/routes/portfolio.py:152`) pose `manual_active = slot ∈ lifecycle.manual_active`, soit « **forcé en actif** » — `false` est l'état normal | Sur le déploiement réel (240 candidats, 0 forçage) le kanban s'affichait **entièrement vide** sous un en-tête « 240 candidats » | Filtre inversé en « n'afficher que les bots forcés en actif », badge drawer corrigé |
| R7 | S5/S7 | `/lab` lisait le mode expert **uniquement** dans `localStorage`, via un `useState(initializer)` détourné en effet, alors que `/settings-v2` et `/settings` écrivent côté backend | Mode expert divergent entre les pages, perdu d'un navigateur à l'autre | `/lab` lit `usePresets()` et écrit via `useSetExpertMode()` ; `localStorage` = cache d'affichage |
| R8 | S3 | `HealthBanner` testait `status.paper_mode` sans repli. Tant que le trader n'est pas démarré, `/api/status` ne renvoie que `{status: "not_started"}` | Le bandeau annonçait « 🔴 Mode live » sur un bot à l'arrêt, en contradiction avec le badge « PAPER » de la topbar juste au-dessus | Repli `?? true` aligné sur la topbar ; le mode n'est plus affirmé quand il est inconnu |
| R9 | S2 | Les 2 tests unitaires livrés sur `Button` affirmaient `bg-red-600` et `h-10`, valeurs absentes du composant réel | `vitest run` échouait (2/9) — la suite n'avait jamais été exécutée | Assertions alignées sur l'implémentation (`bg-red-500/10`, `h-12`) |
| R10 | S2 | `test:e2e` / `test:a11y` lançaient `playwright test` depuis `frontend/`, où il n'y a ni binaire ni config (tout est sous `frontend/e2e/`) | Scripts inopérants | `--config e2e/playwright.config.ts` |
| R11 | S4 | Le patch S4 embarquait un passage `100644 → 100755` sur **519 fichiers** sans rapport (sources Python, YAML de stratégies, `data/*.json`) | Bruit de diff massif, `data/` marqué modifié | Modes rétablis ; `data/` strictement identique à `main` |

### Contrats d'API supposés au lieu d'être vérifiés

Trois composants ont été écrits contre une forme de réponse imaginée. Aucun
n'était détectable par `tsc` — `api.ts` type tout en `any` — ni par le build :
il a fallu ouvrir les pages avec le backend en face.

| Réf | Sprint | Constat | Impact | Correctif |
|---|---|---|---|---|
| R15 | S4 | `/bots-v2` faisait `oosData.slots.find(...)`, or `/api/oos-tracker` renvoie `slots` comme **dictionnaire** indexé par `slot_key`, pas comme tableau | `TypeError: oosData.slots.find is not a function` — **ouvrir un bot faisait tomber toute la page dans l'ErrorBoundary**. Le drawer (frise de cycle de vie + cône Monte-Carlo), cœur de S4, n'a jamais pu s'afficher | Accès par clé, avec repli tableau si le contrat évolue |
| R16 | S9 | `MLRecipesList` traitait `features_catalog` comme un tableau de features, or c'est un **identifiant de catalogue** (`"dyn_threshold@1"`) | `TypeError: …slice(...).map is not a function` — **la page `/ml` entière plantait**. Avant le crash, le badge affichait la longueur de la chaîne (« 15 features ») | Affiche l'identifiant du catalogue, le `label_scheme` et les `heads` |
| R17 | S4 | `MonteCarloCone` attendait des **séries temporelles** (`labels`, `median`, `ci_lower`, `ci_upper`, `live`) pour tracer un cône. L'API ne fournit que des **agrégats scalaires** (`return_p5_pct`, `return_mean_pct`, `return_p95_pct`, `prob_profit`, `max_dd_p95_pct`) | `chartData` toujours vide → « Pas encore de données OOS pour ce bot » affiché pour **tous** les bots, en permanence | Composant réécrit en bande [P5, P95] avec repères sim et live, verdict tiré de `contract.in_band`. Le cône temporel exigerait un nouvel endpoint exposant la trajectoire d'équité par run |

### Anomalies hors périmètre S0-S9 (préexistantes sur `main`)

Corrigées au passage car elles empêchent l'UI de fonctionner :

| Réf | Constat | Impact | Correctif |
|---|---|---|---|
| R12 | `public/sw.js` appliquait un **cache-first permanent** à tout GET non-HTML, `CACHE_NAME` figé à `crypto-bot-v1` | Le Service Worker servait indéfiniment les anciens bundles : après déploiement, un utilisateur ayant déjà ouvert l'app **ne voit jamais la refonte**. Reproduit pendant la revue : redémarrage serveur + suppression de `.next` + onglet neuf servaient toujours l'ancien code | `/_next/` passé en network-first, `CACHE_NAME` → `crypto-bot-v2` (purge les caches obsolètes à l'activation) |
| R13 | `api.getHealth()` appelait `/api/health` ; la route est montée à la **racine** (`app/api/main.py:108`), volontairement sans auth | 404 systématique — l'indicateur de santé de la topbar restait vide | Option `base` sur `apiFetch` ; `getHealth` cible `/health` (déjà proxifié par `next.config.mjs`) |
| R14 | `api.fastAnalysis()` appelait `/scanner/fast-analysis` ; la route est `/api/scanner/fast_analysis` | 404 — le bouton « Analyse rapide » de `/scanner` ne fonctionnait pas | Chemin corrigé |

### État de vérification après correctifs

| Contrôle | Résultat |
|---|---|
| `tsc --noEmit` | ✅ 0 erreur (6 avant) |
| `vitest run` | ✅ 9/9 (7/9 avant) |
| `next lint` | ✅ 0 warning, 0 erreur |
| `next build` | ✅ 28 routes générées (échec avant) |
| `pytest -m "not slow"` | ✅ 1392 passés, 3 ignorés — aucune régression backend |
| Endpoints de l'annexe d'exécution | ✅ 13/13 répondent (`/api/scanner/opportunities` en 503 « Config non chargée » tant que le trader n'est pas démarré — comportement attendu) |
| Rendu live des 5 pages méta | ✅ `/portfolio-v2`, `/bots-v2`, `/lab`, `/market`, `/settings-v2` — 0 erreur console |
| Parcours interactifs vérifiés | ✅ drawer `/bots-v2` (frise + bande MC), onglets `/lab` et `/market`, onglet Données de `/settings-v2` (122 symboles SBF 120 listés), `/ml` (7 recettes) |

### Seconde passe : reprise exhaustive des 47 livrables S0-S9

Une vérification livrable par livrable (et non plus sprint par sprint) a été
menée après les correctifs R1-R17. **45 des 47 livrables annoncés sont bien
présents.** Deux ne l'étaient pas :

| Réf | Sprint | Constat | Correctif |
|---|---|---|---|
| R18 | S3 | `AllocationDonut` était annoncé avec des « segments cliquables » et son en-tête indiquait « Clique sur un segment → redirige vers la fiche bot ». Seule la **légende** était cliquable ; les `Cell` du `PieChart` n'avaient aucun handler, alors même que `slotKey` était déjà transporté dans les données | `onClick` posé sur les `Cell` + curseur ; la légende et les segments pointent désormais sur `/bots-v2?slot=` (ils visaient `/bots`, la route héritée) |
| R19 | S9 | `ExportButtons` (`JsonExportButton`, `CsvExportButton`, `PdfExportButton`) était livré et son en-tête annonçait « Utilisé par : page backtest, page audit, page compare » — **il n'était monté nulle part**, donc entièrement mort | Monté sur `/audit` (CSV + JSON des résultats OOS, filtre appliqué) et sur l'onglet Backtest de `/lab` (JSON du résultat + CSV par stratégie) |

`PdfExportButton` reste volontairement non monté : c'est un placeholder qui
affiche « à implémenter (jsPDF + html2canvas) ». L'exposer reviendrait à donner
à l'utilisateur un bouton mort.

Tous les autres livrables ont été vérifiés présents, y compris ceux qui n'avaient
pas été contrôlés lors de la première passe : les 7 wrappers Radix, les 4 patterns
de Skeleton, la prop `skeleton` de `QueryBoundary`, les extensions d'`EmptyState`,
les 5 tokens Tailwind, les 3 viewports Playwright, les pages 404/erreur, la
migration de `window.confirm`, le composant `Verdict`, `useCancelBacktest`, le
journal des signaux avec rejets et filtre par raison, les hints de `FeesBreakdown`,
le bouton « Acquitter » de `HaltBanner`, et les 9 endpoints scanner/univers.

### Typage des réponses d'API (cause racine de R15-R17)

`frontend/src/lib/schemas.ts` introduit des schémas zod sur les endpoints
consommés par les pages v2 : `status`, `bots`, `oos-tracker`, `ml/recipes`,
`stats/daily`, `stats/fees`, `health`, `universe`.

Principe retenu : **validation permissive et non bloquante**. Les schémas sont
en `.passthrough()` avec des champs très majoritairement optionnels — le but
n'est pas de rejeter les réponses du backend mais de garantir la forme des
champs que l'UI manipule (un tableau reste un tableau, un dictionnaire reste un
dictionnaire). En cas d'écart, `apiFetch` journalise un avertissement `[api]` et
renvoie la donnée brute : on préfère une UI dégradée à une UI qui plante.

`frontend/src/lib/__tests__/schemas.test.ts` verrouille les trois contrats qui
avaient réellement cassé une page (11 tests), avec les payloads copiés des
réponses réelles du backend — dont les tests négatifs qui rejettent les formes
supposées à tort (`slots` en tableau, `features_catalog` en tableau).

Vérifié en exécution : aucun avertissement `[api]` sur les pages v2 face au
backend réel, ce qui confirme que les schémas décrivent le backend et non une
seconde invention.

### Accessibilité — 52 violations relevées, 0 restante

Le job CI `a11y` a produit le premier relevé WCAG 2.1 AA du projet (24 pages).
État initial, puis traitement :

| Règle axe | Départ | Gravité | Traitement |
|---|---|---|---|
| `color-contrast` | 22 | serious | `text-white` sur `bg-primary-500` (#06b6d4) ne donnait que **2.43:1**. Le bouton principal étant présent sur presque toutes les pages, il expliquait à lui seul l'essentiel du total. Texte sombre dessus : 7.97:1 au repos, 5.25:1 au survol, couleur de marque préservée (`Button`, `ConfirmDialog`, skip-link) |
| `select-name` | 16 | critical | 34 champs `<select>`/`<input>` bruts avaient un `<label>` visible juste au-dessus, sans association. `aria-label` reprenant le libellé |
| `label` | 10 | critical | idem |
| `button-name` | 2 | critical | Le `Toggle` de `/settings` ne contenait qu'un `<span>` décoratif — un lecteur d'écran annonçait « bouton, non coché » sans dire de quoi. Prop `label` rendue obligatoire. Bouton de thème de la topbar : `aria-label` ajouté (il n'avait qu'un `title`) |
| `scrollable-region-focusable` | 2 | serious | `<main>` défile (`overflow-y-auto`) sans être atteignable au clavier : sur une page sans élément focusable, son contenu devenait inaccessible sans souris. `tabIndex={0}` — il est par ailleurs la cible du skip-link |

**Constat de départ à retenir : les pages de la refonte étaient déjà quasi
propres.** `/portfolio-v2`, `/lab`, `/market` et `/settings-v2` ne remontaient
aucune violation ; `/bots-v2` une seule. Les 14 pages en défaut étaient les
anciennes. Le design system livré en S1-S2 tenait ses promesses — la dette
était dans l'UI héritée.

Deux pièges de méthode rencontrés, notés pour la prochaine fois :

1. **`violations.length` compte des règles, pas des éléments.** Le rapport
   indiquait « 1 violation » là où des dizaines de nœuds étaient concernés, et
   ne disait pas *où*. Le spec journalise désormais le sélecteur, l'extrait HTML
   et le `failureSummary` (valeurs mesurées) de chaque nœud.
2. **axe échantillonnait une frame de `animate-fade-in`.** Les 3 dernières
   violations de contraste portaient sur le même bouton avec un ratio différent
   à chaque exécution (3.12, 3.25, 3.81) : la couleur mesurée était fondue,
   l'état stabilisé étant conforme. Les animations sont maintenant neutralisées
   avant l'analyse, comme dans `visual.spec.ts`.

⚠ Le relevé est fait **backend éteint** (les jobs CI ne démarrent que le
frontend) : il porte sur l'état d'erreur/vide des pages. Un second passage avec
backend reste à faire.

### Montées de version de sécurité

`pip-audit` remontait 16 vulnérabilités sur 4 paquets, et le job `security`
échouait sur `main` depuis plusieurs runs :

| Paquet | Avant | Après | Vulnérabilités |
|---|---|---|---|
| `starlette` | 0.38.6 | 1.3.1 | 8 (PYSEC-2026-161, -248, -249, -1941, -1943, -2280, -2281) |
| `python-multipart` | 0.0.22 | 0.0.32 | 5 (PYSEC-2026-3036 à -3040) |
| `python-dotenv` | 1.1.1 | 1.2.2 | 1 (PYSEC-2026-2270) |
| `pytest` | 8.2.0 | 9.1.1 | 1 (PYSEC-2026-1845) |
| `fastapi` | 0.115.0 | 0.141.1 | — (imposé par starlette) |

La montée de FastAPI était le verrou : 0.115.0 demandait
`starlette>=0.37.2,<0.39.0`, ce qui rendait toute correction de starlette
impossible. 0.141.1 demande `starlette>=0.46.0` sans borne haute.

`requirements.txt` documentait qu'une précédente tentative de passage en
starlette 1.3.1 avait cassé la collecte des tests sur `Router.__init__() got an
unexpected keyword argument 'on_startup'`. **La cause a disparu** : plus aucune
occurrence de `on_startup` / `on_shutdown` / `@app.on_event` dans `app/`, le
cycle de vie passe par `lifespan`. Vérifié : 1392 tests passés, 3 ignorés, à
l'identique d'avant la montée.

Le job CI passait `--ignore-vuln GHSA-xxxx`, un identifiant placeholder qui ne
masquait rien tout en laissant croire qu'une exception était en place — retiré.

### Réserves non traitées

- **Couverture de tests** : la refonte ajoute ~5 000 lignes de TSX pour 9 tests
  unitaires portant sur `Button`/`Badge`/`Card`/`cn`. Aucune des 5 pages méta,
  ni `MonteCarloCone`, `AllocationDonut`, `UniverseManager`, `FeesBreakdown`
  n'est testée. Le seuil de couverture de `vitest.config.ts` (60 %) n'est pas
  atteignable en l'état et n'est vérifié par aucune CI.
- ~~**Accessibilité**~~ — traité, cf. §Accessibilité ci-dessous. Le job CI
  `a11y` est bloquant et passe à zéro violation sur les 24 pages.
- ~~**Redirections 308**~~ — traité : les 14 redirections du plan sont posées,
  cf. §Bascule S10 ci-dessous.
- ~~**Réponses d'API non typées**~~ — traité, cf. §Typage des réponses d'API.
  L'extension aux endpoints restants (`optimize/*`, `ml/registry`, `replay`,
  `derivatives`, `scanner/smc*`) est faite. Trois pièges supplémentaires du même
  genre que R15 ont été documentés et testés au passage :
  `/optimize/status` sans `job_id` renvoie un **dictionnaire** indexé par job_id
  (`get_all_jobs()`) ; `/optimize/results` est un dictionnaire à **deux
  niveaux** (stratégie → timeframe) ; `_series_payload` de `derivatives` renvoie
  **`null`** quand la série est vide, donc chaque métrique est nullable.
- **Données Monte-Carlo dégénérées** : sur le jeu de données de vérification,
  les 145 slots ont `P5 = P95` et 0 trade live — la bande se réduit à un point
  et le verdict est toujours « pas assez de trades réels ». L'affichage est
  correct, mais la valeur produit de la visualisation reste à confirmer sur un
  tracker alimenté.

## Annexe — Bascule S10 : redirections 308

### Ce qui a été posé

`frontend/next.config.mjs`, bloc `redirects()` — `permanent: true` émet un 308 :

| Source | Cible | Justification |
|---|---|---|
| `/dashboard` | `/portfolio-v2` | `/portfolio-v2` reprend tout `/dashboard` ; `AllocationsGrid` y est remplacé par `AllocationDonut` (segments cliquables + shadow targets) |
| `/bots` | `/bots-v2` | `/bots-v2` est un sur-ensemble strict — aucun hook ni composant de `/bots` n'y manque |
| `/backtest` | `/lab?tab=backtest` | L'onglet Backtest du Laboratoire est le **seul** onglet du Lab qui soit une vraie réimplémentation |

**Lot Marché** — les quatre onglets de `/market` montent désormais le contenu
réel des anciennes pages, extrait sous `frontend/src/components/views/` :

| Source | Cible | Vue montée |
|---|---|---|
| `/scanner` | `/market?tab=scanner` | `scanner-view.tsx` (ex-`/scanner`, 137 l.) |
| `/smartgraph` | `/market?tab=smartgraph` | `smart-graph-view.tsx` (ex-`/smartgraph`, 1 155 l.) |
| `/smartreplay` | `/market?tab=smartreplay` | `smart-replay-view.tsx` (ex-`/smartreplay`, 671 l.) |
| `/derivatives` | `/market?tab=derivatives` | `derivatives-view.tsx` (ex-`/derivatives`, 368 l.) |

**Lot Laboratoire** — même traitement pour les 4 onglets non-Backtest de
`/lab` :

| Source | Cible | Vue montée |
|---|---|---|
| `/optimizer` | `/lab?tab=optimizer` | `optimizer-view.tsx` (ex-`/optimizer`, 657 l.) |
| `/ml` | `/lab?tab=ml` | `ml-view.tsx` (ex-`/ml`, 235 l.) |
| `/replay` | `/lab?tab=replay` | `replay-view.tsx` (ex-`/replay`, 467 l.) |
| `/compare` | `/lab?tab=compare` | `compare-view.tsx` (ex-`/compare`, 520 l.) |

`/models` **n'a pas** de 308 : le registre versionné n'est pas dans le plan de
fusion et reste une page à part entière. L'onglet ML y renvoie explicitement —
c'est le seul renvoi qui subsiste, et il est assumé.

**Lot Réglages** — les deux dernières pages de configuration :

| Source | Cible | Ce qui a été porté |
|---|---|---|
| `/config` | `/settings-v2?tab=capital` | `config-view.tsx` : ses 4 onglets internes deviennent 4 sections exportées (`ConfigStrategiesView`, `ConfigRiskView`, `ConfigNotificationsView`, `ConfigExchangeView`), montées dans Capital et Notifs |
| `/settings` | `/settings-v2?tab=capital` | Thème (vrai sélecteur, plus un badge « Topbar »), notifications navigateur câblées, seuils de preset lus au backend |

Trois choses corrigées au passage, qui n'étaient pas de simples déplacements :

1. **Le Service Worker ne dépend plus d'une visite de page.** Il était
   enregistré dans un `useEffect` de `/settings` : un utilisateur qui n'ouvrait
   jamais les Réglages n'avait jamais de SW, donc ni PWA installable ni cache
   hors-ligne. Il est remonté dans `Providers` (`components/providers.tsx`).
2. **Le switch « Notifications navigateur » fonctionne.** C'était un
   `defaultChecked` sans handler : il affichait « activé » sans jamais demander
   la permission. Il est câblé sur les helpers de `notifications-provider` et
   reflète la permission réelle (`Accordé` / `Refusé` / `En attente` / `Non
   supporté`), désactivé quand elle n'est pas récupérable depuis la page.
3. **Les seuils de preset affichés étaient faux.** `/settings-v2` les codait en
   dur alors que `/settings` lisait `/api/settings/presets`. Dès que
   `config.yaml` s'écartait de ces valeurs, les deux écrans affichaient des
   chiffres différents et c'est la page méta qui mentait. Le backend prime
   désormais ; les constantes ne servent plus que de repli avant réponse.

Deux suppressions volontaires :

- L'onglet Risk de `/config` faisait `(presets || [...]).map(...)` sur le retour
  de `usePresets()`, qui est un objet `{presets, current, expert_mode}` et non
  un tableau : **l'onglet plantait au rendu** dès que la requête aboutissait.
  Le bloc n'est pas porté — les cartes de preset de l'onglet Capital couvrent
  le besoin, et correctement.
- La garde `expertMode` sur l'accès aux paramètres avancés est retirée : elle
  désactivait un lien de navigation, pas les écritures. Un utilisateur qui
  coupait le mode expert depuis une autre page perdait l'accès à des
  paramètres qu'il venait de modifier, alors que l'API restait ouverte.

`/` pointe désormais sur `/portfolio-v2` (au lieu de `/dashboard`), sans saut de
redirection intermédiaire. Sidebar, recherche Cmd+K, page 404 et
`AllocationsGrid` ciblent directement les routes v2.

**Lot Portefeuille** — la dernière des 14 :

| Source | Cible | Ce qui a été porté |
|---|---|---|
| `/portfolio` | `/portfolio-v2` | `ActivityFeed` (journal de notifications) et `SignificantBotsTable` (vue par bot), extraits en composants sous `components/cards/` |

Le bouton **Force rebalance** a été porté en plus des deux blocs listés par
l'audit : il n'existait que sur `/portfolio` et ne s'affiche que lorsque
`continuous_allocation = false`, c'est-à-dire précisément quand le capital
n'est pas réalloué tout seul. Rediriger sans lui aurait supprimé la seule
commande de rééquilibrage de l'UI — un manque qu'aucun test n'aurait vu.

`ActivityFeed` ne fait pas doublon avec `LiveTradesFeed`, déjà monté sur
`/portfolio-v2` : celui-ci lit le flux WebSocket des trades, celui-là
l'historique persisté des notifications (halt, kill switch, drawdown) avec ses
niveaux info / warning / critical.

### Ce qui reste bloqué, et pourquoi

**Plus rien.** Les 14 redirections du plan de refonte sont posées.

Seul `/models` reste servi en direct, **par choix d'architecture et non par
blocage** : le registre versionné (882 l.) n'est pas dans le plan de fusion.
L'onglet ML du Laboratoire y renvoie explicitement, et le test e2e le classe
en « reste accessible » pour cette raison.

Les ~5 470 lignes du constat initial sont toutes joignables : ~2 330 par le lot
Marché, ~1 840 par le Laboratoire, ~820 par les Réglages, ~480 par le
Portefeuille.

### Le double 308 du backend

Poser les redirections côté Next en a créé un effet de bord que les 4 lots
n'avaient pas traité. `app/api/main.py` sert les 18 anciennes routes HTML et
redirigeait chacune vers **le même chemin** côté Next — lequel la redirigeait à
son tour depuis la fusion. Un `GET /smartgraph` sur le port 8000 coûtait donc
deux 308 : FastAPI → Next `/smartgraph` → Next `/market?tab=smartgraph`.

`HTML_ROUTES_TO_REDIRECT` passe de liste à mapping `route → cible finale`. Les
13 routes concernées visent directement leur destination ; les 5 qui sont de
vraies pages Next (`/audit`, `/audit-log`, `/trades`, `/models`, `/data`)
gardent leur chemin. `/slots`, qui visait `/bots`, vise `/bots-v2`. `/` reste
sur `/` : ce n'est pas un alias hérité mais la racine de l'app, et c'est
`frontend/src/app/page.tsx` qui décide de la page d'entrée — le dupliquer dans
le backend en ferait un second endroit à changer.

Cette table double le bloc `redirects()` de `next.config.mjs` : c'est le prix
d'avoir deux serveurs qui connaissent la même bascule.
`tests/test_legacy_redirects.py` lit `next.config.mjs` et vérifie qu'elles ne
divergent pas — une redirection posée côté Next sans être répercutée côté
backend recréerait le double saut, invisible en test comme à l'œil. Le test
vérifie aussi qu'aucune cible du backend n'est elle-même une source de
redirection Next.

### Conditions de levée, par lot

1. ~~**Lot Marché**~~ (`/scanner`, `/smartgraph`, `/smartreplay`,
   `/derivatives`) — **fait**. Les 4 pages sont devenues des vues sous
   `frontend/src/components/views/`, montées dans les onglets de `/market` ;
   `RedirectCard` et le teaser Scanner ont disparu, les 4 redirections sont
   posées. L'onglet est piloté par `?tab=`, ce qui rend les liens profonds
   partageables et donne aux 308 une cible précise. La query est conservée par
   Next : le lien « Analyser » de `/data` arrive sur le Scanner avec le symbole
   et le timeframe déjà remplis — il pointait jusque-là sur `/scanner?symbol=…`
   dont la page ignorait les paramètres.

   Deux conséquences sur les tests, à ne pas confondre avec des régressions :
   le job `a11y` ouvre maintenant les 4 onglets par leur `?tab=` (Radix ne
   monte que l'onglet actif — auditer `/market` seul ne couvrirait que
   Scanner), et **la référence visuelle de `/market` a été régénérée**.

   Sur ce dernier point, la CI a corrigé une prévision trop prudente : le
   retrait d'entrées de la barre latérale décale bien toutes les captures
   `fullPage`, mais reste sous le seuil de 2 % (`maxDiffPixelRatio`) sur 4 des
   5 pages méta. Seule `marche.png` sortait de la tolérance — 19 434 pixels,
   ratio 0,03 — parce que son onglet Scanner passe d'un teaser à la vue
   complète. Les 4 autres références restent valides et n'ont pas été touchées.
2. ~~**Lot Laboratoire**~~ (`/optimizer`, `/replay`, `/compare`, `/ml`) —
   **fait**, sur le même modèle que le lot Marché : vues sous
   `frontend/src/components/views/`, montées dans les onglets, 4 redirections
   posées, `?tab=` validé contre la liste des onglets.

   `/models` (882 l.) n'est pas dans le plan de fusion et **reste une page à
   part entière** : pas de 308, entrée de nav conservée, et l'onglet ML y
   renvoie explicitement. C'est le seul renvoi qui subsiste dans les pages
   méta, et il est assumé — à ne pas confondre avec les cartes de renvoi que
   ces lots ont supprimées.

   Le prop `expertMode` disparaît des 4 onglets : il ne servait qu'à afficher
   la mention « intégration native prévue au Sprint 7/9 » sous les teasers.
   Il reste actif sur l'onglet Backtest, où il gouverne de vraies options.
3. ~~**Lot Réglages**~~ (`/config`, `/settings`) — **fait**. Les trois
   conditions posées ici sont remplies : l'éditeur de params par stratégie est
   dans l'onglet Capital, le switch « Notifications navigateur » est câblé, et
   l'enregistrement du Service Worker est remonté dans `Providers`. Deux
   correctifs non prévus s'y sont ajoutés (seuils de preset lus au backend,
   onglet Risk de `/config` qui plantait) — détail ci-dessus.

   `/settings-v2` garde son nom de route : la renommer `/settings` demanderait
   une 308 dans l'autre sens et casserait les favoris fraîchement redirigés.
   Son entrée de nav, elle, s'appelle simplement « Réglages » — il n'y a plus
   d'ancienne page dont la distinguer.
4. ~~**Lot Portefeuille**~~ (`/portfolio`) — **fait**. Journal de notifications
   et vue par bot extraits en composants (`ActivityFeed`,
   `SignificantBotsTable`) et montés dans `/portfolio-v2`, plus le bouton
   Force rebalance que l'audit n'avait pas relevé.

**Les 4 lots sont livrés ; les 14 redirections du plan sont posées.** Le
constat « les pages `/lab` et `/market` sont, pour 8 de leurs 9 onglets, des
menus vers l'ancienne UI » n'est plus vrai : il ne reste aucune carte de
renvoi, seulement le lien assumé de l'onglet ML vers `/models`.

### Note sur le code 308

Un 308 est une redirection **permanente** : les navigateurs la mettent en cache
durablement, y compris après retrait côté serveur. Le plan initial affirmait que
« cette approche permet un rollback facile » — c'est inexact pour un 308. Tant
que la validation utilisateur des pages v2 n'est pas faite, passer
`permanent: false` (307) dans `next.config.mjs` conserve un retour arrière
immédiat, au prix d'un signal SEO plus faible — sans objet pour une UI interne.
