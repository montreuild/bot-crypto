# Spécifications de rattrapage — Pages Lab (Backtest / Optimizer / Replay / ML / Compare) Next.js

**Repository cible** : `https://github.com/montreuild/bot-crypto`
**Branche de référence** : `main` (état au 07/08/2026)
**Auteur** : Audit externe produit/frontend
**Périmètre** : 5 onglets du `/lab` Next.js — Backtest, Optimizer, Replay, ML, Compare
**Source de référence** : templates Jinja2 supprimés en commit `ecc87b2` (S6-09), récupérés via `git show ecc87b2~1:app/web/templates/*.html`

---

## Table des matières

1. [Contexte et méthodologie](#1-contexte-et-méthodologie)
2. [Conventions de spécification](#2-conventions-de-spécification)
3. [Backtest — BT-001 à BT-018](#3-backtest)
4. [Optimizer — OPT-001 à OPT-013](#4-optimizer)
5. [Replay — RPL-001 à RPL-009](#5-replay)
6. [ML — ML-001 à ML-007](#6-ml)
7. [Compare — CMP-001 à CMP-005](#7-compare)
8. [Synthèse transverse](#8-synthèse-transverse)
9. [Plan de migration et non-régression](#9-plan-de-migration-et-non-régression)
10. [Planning suggéré (6 sprints × 2 semaines)](#10-planning-suggéré-6-sprints--2-semaines)
11. [Annexe A — Extraits de code Jinja2 de référence](#annexe-a--extraits-de-code-jinja2-de-référence)
12. [Annexe B — Index des composants React à créer/modifier](#annexe-b--index-des-composants-react-à-créermodifier)

---

## 1. Contexte et méthodologie

### 1.1 Constat de l'utilisateur

> « L'interface de backtest et d'optimisation en Jinja2 étaient mieux. »

Le passage Jinja2 → Next.js (S6-09, commit `ecc87b2`) a livré une base saine (TanStack Query, types partagés, composants UI Radix, accessibilité clavier, e2e Playwright), mais a **perdu un niveau de détail, de précision et de personnalisation** que les templates Jinja2 avaient construit sur ~10 600 lignes de HTML/CSS/JS. Ce document spécifie comment rattraper cet écart **sans régresser les acquis du Next.js** (CostModelCard, Verdict, StudyVsLiveCard, SSE, schémas Zod, multi-symboles, etc.).

### 1.2 Méthodologie d'analyse

1. **Récupération des templates Jinja2 supprimés** via `git show ecc87b2~1:app/web/templates/{backtest,optimizer,replay,ml,compare,base}.html`. Les fichiers restaurés sont dans `/home/z/my-project/analysis/jinja2-originals/` (4 092 lignes cumulées).
2. **Lecture intégrale des pages Next.js actuelles** :
   - `frontend/src/app/lab/page.tsx` — **depuis #244** : shell (~175 L) +
     `next/dynamic` par onglet. Backtest dans `components/views/backtest-view.tsx`.
     (À la rédaction : 781 L, onglet Backtest inline + lazy-load des 4 autres vues.)
   - `frontend/src/components/views/{optimizer,compare,replay,ml}-view.tsx`
   - `frontend/src/components/charts/*` et `frontend/src/components/cards/*`
   - `frontend/src/lib/api.ts` et `frontend/src/types/index.ts`
3. **Gap matrix par feature** : pour chaque fonctionnalité Jinja2, marquage ✅ / ⚠️ / ❌ côté Next.js.
4. **Sévérité** : CRITIQUE (bloquant pour usage sérieux) / HIGH (impact usage quotidien) / MEDIUM (qualité de vie) / LOW (cosmétique).
5. **Effort en story points** : 1 / 2 / 3 / 5 / 8 / 13. Sprint de rattachement parmi 6 sprints de 2 semaines.

### 1.3 Périmètre

5 features couvertes : **Backtest**, **Optimizer**, **Replay**, **ML**, **Compare**. Au total **52 spécifications** détaillées + **9 patterns transverses** + **1 plan de migration**.

### 1.4 État actuel du repo (vérifié au 07/08/2026)

| Indicateur | État |
|---|---|
| CI | 🟢 verte (`ruff` + `pytest -m "not slow"`) |
| Tests frontend | 🟡 1 fichier de tests unitaires + 4 fichiers e2e Playwright |
| Couverture Lab Next.js | 🟡 Visuel (snapshots sur 5 pages) — pas de tests de comportement |
| `lightweight-charts` en dépendance | ✅ Présent (utilisé par `smart-replay-view.tsx` et `smart-graph-view.tsx`) |
| `recharts` en dépendance | ✅ Présent (utilisés par `BacktestEquityChart`, `MonteCarloPanel`, `TradesScatter`, `compare-view`) |
| TanStack Query | ✅ Présent (mutations + queries avec `refetchInterval`) |
| Schémas Zod | ✅ Présents dans `frontend/src/lib/schemas.ts` |

---

## 2. Conventions de spécification

Chaque spécification suit le modèle suivant :

```
### <ID> — <Titre court>

**User story.** En tant que <rôle>, je veux <action>, afin de <bénéfice>.

**Priorité** : CRITIQUE / HIGH / MEDIUM / LOW
**Effort** : <SP> (1/2/3/5/8/13)
**Sprint** : S1 / S2 / S3 / S4 / S5 / S6
**Fichiers impactés** :
- À créer : <chemins>
- À modifier : <chemins>
**Endpoints API consommés** : <liste>
**Référence Jinja2** : `<fichier>:<lignes>` (cf. Annexe A pour extrait)

#### Description fonctionnelle
<description structurée par blocs>

#### Composants React
- `<NomComposant>` (à créer) — props : `<prop>: <type>`
- `<NomComposant>` (à modifier) — ajout prop `<prop>: <type>`

#### État et store
- Hook `useXxx` — ajouter clé `<clé>: <type>`
- Query/mutation TanStack — `<queryKey>`

#### Interactions
- Clic / clavier / drag / scroll — comportement attendu

#### Critères d'acceptation
- [ ] <critère 1>
- [ ] <critère 2>
- [ ] <critère 3>

#### Non-régression
- [ ] <test existant à conserver>
- [ ] <snapshot Playwright à mettre à jour>
- [ ] <feature flag / plan rollback>

#### Plan de migration
1. <étape 1>
2. <étape 2>
```

**Légende des symboles** :
- ✅ présent en Next.js | ⚠️ partiel | ❌ absent
- 🆕 acquis Next.js à préserver | 🔄 à adapter | ➕ à créer

---

## 3. Backtest

La page Backtest est actuellement inline dans `frontend/src/app/lab/page.tsx` (composants `BacktestTab` l. 218-422 et `BacktestResults` l. 543-748). Elle partage l'onglet `/lab` avec 4 autres vues lazy-loadées.

### 3.0 Layout cible

**Description structurée par blocs** :
- **Zone 1 — Sidebar config (250 px, sticky, scrollable Y)** : symbole, timeframe, nombre de bougies (avec quick presets + hint dynamique), stratégie checkboxes (avec badge `● actif`), validation (WF + MC + dual_pass sous mode expert), boutons Lancer/Annuler, boutons export JSON/PDF (désactivés tant qu'aucun résultat), zone message + log panel.
- **Zone 2 — Zone principale (scrollable)** : verdict en clair (existant), CostModelCard (existant), tableau comparatif si >1 stratégie, tabs par stratégie, puis par stratégie : graphique prix+signaux, KPI grid (9 métriques), 4 sous-graphiques (equity, PnL/trade, PnL cumulé, score×pnl), panneau WF, panneau MC, panneau ML si applicable, panneau Diagnostics, tableau des trades, panneau stats agrégées.
- **Responsive** : sous 860 px, la sidebar passe au-dessus (1 colonne).

### BT-001 — Graphique Prix + Signaux avec markers entrée/sortie et stop lines

**User story.** En tant que trader, je veux voir le graphique des prix en chandelier avec mes entrées (▲ Long / ▼ Short) et sorties (● gain/perte) marquées dessus, ainsi que le stop initial (dashed) et le stop trailing (solid), afin de comprendre visuellement où et pourquoi ma stratégie a tradé.

**Priorité** : CRITIQUE
**Effort** : 8
**Sprint** : S1
**Fichiers impactés** :
- À créer : `frontend/src/components/charts/price-signals-chart.tsx`
- À modifier : `frontend/src/app/lab/page.tsx` (intégration dans `BacktestResults`)
**Endpoints API consommés** : `POST /api/backtest` (champ `trades[].stop_trail` déjà renvoyé par le backend)
**Référence Jinja2** : `backtest.html:668-755` (fonction `buildPrice`) — voir Annexe A.1

#### Description fonctionnelle

**Bloc 1 — Conteneur chart (300 px de haut, full-width)** :
- Bibliothèque : `lightweight-charts` v4.2.0 (déjà en dépendance via `smart-replay-view`).
- Type de chart : candlestick par défaut, toggle line/candles via bouton en haut à droite du chart.
- Couleurs : up `#22c55e`, down `#ef4444`, wick `#64748b`, fond transparent.
- Bouton « Plein écran » (icône `Maximize2`) à droite du toggle — ouvre une `Dialog` Radix avec le chart en pleine hauteur (calc(100vh - 100px)), boutons « Reset zoom » + « Fermer », fermeture par Escape.

**Bloc 2 — Légende en bas du chart** :
- Ligne 1 : `▲ Entrée Long` (vert), `▼ Entrée Short` (rouge), `● Sortie gain` (vert clair), `● Sortie perte` (rouge clair).
- Ligne 2 : `┄┄ Stop initial` (amber dashed), `── Stop trailing` (purple solid).

**Bloc 3 — Markers entrée** :
- Position : `belowBar` pour Long, `aboveBar` pour Short.
- Forme : `arrowUp` (Long), `arrowDown` (Short).
- Couleur : vert `#22c55e` (Long), rouge `#ef4444` (Short).
- Texte : setup abrégé. Mapping à reproduire exactement :
  - `SIGNAL UP` → `↑SIG`
  - `SHORT TD HIGH` → `↓TDH`
  - `BREAKOUT_LONG` → `↑BRK`
  - `BREAKOUT_SHORT` → `↓BRK`
  - `MOMENTUM_BLITZ_LONG` → `↑MBL`
  - `MOMENTUM_BLITZ_SHORT` → `↓MBS`
  - Pour les autres setups : 6 premiers caractères en majuscules, préfixés `↑` (Long) ou `↓` (Short).
- Si `t.setup` absent : texte `↑E` / `↓E` (Entrée).

**Bloc 4 — Markers sortie** :
- Position : inverse de l'entrée (`aboveBar` pour Long, `belowBar` pour Short).
- Forme : `circle`.
- Couleur : vert clair `#4ade80` si `pnl > 0`, rouge clair `#f87171` sinon.
- Texte : raison abrégée. Mapping à reproduire :
  - `take_profit` → `🎯TP`
  - `stop_loss` → `🛑SL`
  - `trailing_stop` → `⤵TS`
  - `exit_after_bars` → `⏱T`
  - `end_of_data` → `⏹FIN`
  - `p_dir_inversion` → `INV`
  - `regime_exit_TD` → `TD`
  - `manual_close` → `✋MAN`
  - Tout autre : 3 premiers caractères majuscules.

**Bloc 5 — Stop lines par trade** :
- **Stop initial** : ligne horizontale `PriceLine` amber `#f59e0b` `lineStyle: Dashed` `lineWidth: 1`, valeur = `t.stop_initial` (ou `t.entry - t.atr_at_entry * t.sl_atr_mult` si non direct). Étendue : de la barre d'entrée à la barre du premier update du trailing (ou à la sortie si pas de trailing).
- **Stop trailing** : série de segments reconstruite depuis `t.stop_trail` (array de `{bar, stop}`). Affichage : `Series` de type `Line` purple `#a855f7` `lineStyle: Solid` `lineWidth: 2`, étendue du premier point `stop_trail[0].bar` à la barre de sortie. Si `stop_trail` vide ou absent, ne pas tracer le trailing (le stop initial reste jusqu'à la sortie).

**Bloc 6 — Comportement zoom/pan** :
- Zoom molette, pan drag (natif LightweightCharts).
- Bouton « Reset zoom » dans la toolbar du chart (et dans le mode plein écran).
- Crosshair avec tooltip OHLCV au survol (date, O, H, L, C, V — déjà implémenté dans `smart-replay-view.tsx:482-487`, à factoriser).

#### Composants React

- `<PriceSignalsChart>` (à créer) — props :
  ```typescript
  interface PriceSignalsChartProps {
    candles: Candle[];           // { time, open, high, low, close, volume }
    trades: BacktestTrade[];     // trades d'UNE stratégie (pas multi-strat)
    chartType?: 'candles' | 'line';
    height?: number;             // défaut 300
    onChartTypeChange?: (t: 'candles' | 'line') => void;
  }
  ```
- `<ChartFullscreen>` (à modifier) — accepter un `children` render-prop au lieu d'être câblé à `BacktestEquityChart`.

#### État et store

- État local du composant : `chartType` (persisté en `localStorage` clé `bt.chartType`).
- État partagé via `useState` dans `BacktestResults` : `expandedStrategy` (la stratégie dont les détails sont affichés — voir BT-007).

#### Interactions

- Clic sur un marker entrée → scroll vers la ligne correspondante dans le tableau des trades (BT-003) + highlight pendant 2 s.
- Clic sur un marker sortie → idem.
- Hover sur une stop line → tooltip `Trade #N · stop initial @ $X · trailing actif depuis bar M`.
- Double-clic sur le chart → reset zoom.
- Échappement → ferme le modal plein écran.

#### Critères d'acceptation

- [ ] Au moins 1 trade par stratégie est visible avec marker entrée + sortie + stop lines.
- [ ] Le toggle line/candles persiste entre runs (localStorage).
- [ ] Le plein écran s'ouvre avec Escape pour fermer.
- [ ] Si `trades` vide : chart s'affiche quand même (juste les bougies) avec message « Aucun trade pour cette stratégie » en overlay.
- [ ] Si `stop_trail` absent : stop initial seul tracé, pas d'erreur.
- [ ] Le composant est réutilisable pour Replay (BT passe un `candles` slice au lieu de toutes les bougies).

#### Non-régression

- [ ] `frontend/e2e/tests/visual.spec.ts` snapshot `laboratoire-chromium-linux.png` à regénérer.
- [ ] `frontend/src/components/charts/backtest-equity-chart.tsx` inchangé (pas touché par cette spec).
- [ ] Test `test_backtest.py` backend : vérifier que `stop_trail` est bien sérialisé dans la réponse (le backend le renvoie déjà, vérifié dans `app/engine/backtest.py`).

#### Plan de migration

1. Créer le composant `<PriceSignalsChart>` isolé, sans intégration.
2. Ajouter un feature flag `LAB_PRICE_CHART_ENABLED` (env var `NEXT_PUBLIC_LAB_PRICE_CHART`) default `false`.
3. Quand flag on : render `<PriceSignalsChart>` au-dessus de `TradesScatter` (les deux visibles temporairement).
4. Après validation visuelle : retirer `TradesScatter` ou le garder en mode opt-in.
5. Supprimer le flag après 1 sprint sans régression.

---

### BT-002 — Tableau des trades (sortable, paginé, filtrable, expandable)

**User story.** En tant que trader, je veux une table détaillée de chaque trade avec filtres (Long/Short/Win/Loss), tri par colonne, pagination, et lignes expandables affichant raison du signal, conditions, indicateurs au signal, sizing et détails position, afin de debugguer chaque trade individuellement.

**Priorité** : CRITIQUE
**Effort** : 8
**Sprint** : S1
**Fichiers impactés** :
- À créer : `frontend/src/components/tables/trades-table.tsx` (composant réutilisable)
- À créer : `frontend/src/components/tables/trades-table-row-detail.tsx`
- À modifier : `frontend/src/app/lab/page.tsx` (intégration dans `BacktestResults` par stratégie)
**Endpoints API consommés** : aucun (données déjà dans la réponse `POST /api/backtest`)
**Référence Jinja2** : `backtest.html:770-981` (fonction `buildTrades` + `detail`) — voir Annexe A.2

#### Description fonctionnelle

**Bloc 1 — Header de table** :
- Ligne de filtres (chips toggleable) : `Tous` / `Long` / `Short` / `✅ Gains` / `🔴 Pertes` (compteur entre parenthèses).
- Bouton `CSV` (icône Download) à droite.
- Compteur `N trades` à gauche.

**Bloc 2 — Tableau** (14 colonnes) :
| # | Dir. | Setup | Entrée (date) | Prix E. | Prix S. | Dur. | Sortie | Score | PnL | % PnL | Frais | ⌄ |
- `#` : index du trade (1-based).
- `Dir.` : badge `LONG` (vert) / `SHORT` (rouge).
- `Setup` : texte abrégé (caché si aucun trade n'a de setup).
- `Entrée` : date `YYYY-MM-DD HH:mm`.
- `Prix E.` / `Prix S.` : 6 décimales (ou 2 si > 1000).
- `Dur.` : nombre de bars + conversion `(Xd Yh)`.
- `Sortie` : badge coloré (mapping exit_reason — voir BT-001 bloc 4).
- `Score` : mini barre de progression 0-1, couleur dégradé rouge→amber→vert.
- `PnL` : signé, couleur vert/rouge, `$` prefix.
- `% PnL` : signé, couleur vert/rouge, `%` suffix.
- `Frais` : positif, `$` prefix, gris.
- `⌄` : bouton expand (chevron).

**Bloc 3 — Tri** :
- Clic sur en-tête de colonne → tri asc/desc, indicateur `▲`/`▼` à côté du label.
- Colonnes triables : `#`, `Entrée`, `Prix E.`, `Prix S.`, `Dur.`, `Score`, `PnL`, `% PnL`, `Frais`.
- Tri persisté par stratégie (state local par tab).

**Bloc 4 — Pagination** :
- 20 trades par page.
- Footer : `Page X de Y` + boutons `◀ Précédent` / `Suivant ▶`.
- Si ≤ 20 trades, footer masqué.

**Bloc 5 — Ligne expandable** (2 colonnes, grid `grid-cols-2 gap-4`) :

**Colonne gauche — Signal** :
- `Raison du signal` : texte libre depuis `t.signal_reason` (ou `t.setup`).
- `Conditions` : liste de checks ✓/✗ depuis `t.conditions` (array de `{label, passed}`).
- `Indicateurs au signal` : tableau key/value, 6 décimales, depuis `t.indicators` (object). Filtrer les clés trop longues (raccourcir à 30 chars).

**Colonne droite — Sortie & Sizing** :
- `Raison de sortie` : `t.exit_reason` (texte complet, pas l'abbr).
- `Setup V7` : `t.setup_v7` si présent.
- `Régime entrée` : `t.regime_lbl` (badge coloré : Trend / Range / Volatile).
- `TP fixé` : `t.tp_price` (ou `t.tp_atr_mult × ATR`).
- `SL (×ATR)` : `t.sl_atr_mult`.
- `TP (×ATR)` : `t.tp_atr_mult`.
- `exit_after_bars` : `t.exit_after_bars`.
- `size_factor` : `t.size_factor`.
- `disable_trailing` : badge `Actif` (vert) / `Désactivé` (rouge).
- `Notionnel` : `$X`.
- `Taille` : `t.size` (en unités de la paire).
- `Stop initial` : `$X`.
- `Bar entrée/sortie` : `t.entry_bar` / `t.exit_bar`.
- `Statut` : `Fermé` (vert) / `Ouvert` (amber).

#### Composants React

- `<TradesTable>` (à créer) — props :
  ```typescript
  interface TradesTableProps {
    trades: BacktestTrade[];
    pageSize?: number;             // défaut 20
    showSetupColumn?: boolean;     // true si au moins 1 trade a un setup
    onRowClick?: (trade: BacktestTrade) => void;
  }
  ```
- `<TradesTableRowDetail>` (à créer) — props :
  ```typescript
  interface RowDetailProps {
    trade: BacktestTrade;
  }
  ```

#### État et store

- État local : `filters` (`Set<'long' | 'short' | 'win' | 'loss'>`), `sortBy` (`column + asc/desc`), `page` (`number`), `expandedRowId` (`string | null`).
- Hook `useTradesExport` (existant) à étendre : ajouter `exportTradesCSV(trades, meta)` qui sérialise 19 colonnes (voir BT-018).

#### Interactions

- Clic sur chip de filtre → toggle dans le Set, reset page à 1.
- Clic sur en-tête de colonne → toggle asc/desc, reset page à 1.
- Clic sur `⌄` → toggle expandedRowId (une seule ligne expandée à la fois).
- Clic n'importe où sur la ligne (hors `⌄`) → appel `onRowClick` (utilisé par BT-001 pour scroll-into-view du chart, et par BT-009 pour le scroll-into-view des stops).

#### Critères d'acceptation

- [ ] 100 trades s'affichent en 5 pages de 20.
- [ ] Filtre `Long` masque tous les shorts.
- [ ] Tri par `PnL desc` met le trade le plus profitable en haut.
- [ ] Expand d'une ligne affiche raison + conditions + indicateurs + sizing.
- [ ] Si `t.conditions` absent, masquer la sous-section (pas de liste vide).
- [ ] Si `t.indicators` vide, afficher « N/A ».

#### Non-régression

- [ ] `frontend/src/components/charts/trades-scatter.tsx` peut être conservé en complément (vue « scatter ») ou remplacé — décider après validation.
- [ ] Test e2e `frontend/e2e/tests/pages.spec.ts` : ajouter un test « click sur un trade dans la table expand la ligne ».

#### Plan de migration

1. Créer `<TradesTable>` isolé avec données mock.
2. Intégrer dans `BacktestResults` en dessous de `TradesScatter` (les deux visibles temporairement, feature flag `LAB_TRADES_TABLE_ENABLED`).
3. Après validation : rendre `TradesScatter` opt-in via toggle dans la toolbar (par défaut table).
4. Supprimer le flag après 1 sprint.

---

### BT-003 — Panneau Diagnostics (recherche de signaux)

**User story.** En tant que trader, je veux voir des statistiques détaillées sur la recherche de signaux (barres totales, % en position, signaux acceptés vs rejetés, max bars en position), afin de comprendre pourquoi ma stratégie ne trade pas ou trade trop.

**Priorité** : CRITIQUE
**Effort** : 5
**Sprint** : S1
**Fichiers impactés** :
- À créer : `frontend/src/components/cards/diagnostics-panel.tsx`
- À modifier : `frontend/src/app/lab/page.tsx` (intégration)
- À modifier : `frontend/src/types/index.ts` (ajouter type `BacktestDiagnostics`)
- À vérifier : backend `app/engine/backtest.py` (champ `diagnostics` déjà renvoyé ? si non, à ajouter)
**Endpoints API consommés** : `POST /api/backtest` (champ `diagnostics` à ajouter si absent)
**Référence Jinja2** : `backtest.html:582-597` (fonction `diagHTML`) — voir Annexe A.3

#### Description fonctionnelle

**Bloc 1 — KPI grid (9 métriques, grid 3×3)** :
| Barres totales | % en position | % recherche signal |
| Signaux acceptés | Trades ouverts | Rejets notional |
| Rejets ATR≤0 | Max barres en pos. | Max barres sans sig. |

- `Barres totales` : `diagnostics.bars_total`.
- `% en position` : `diagnostics.bars_in_position / bars_total * 100`, formaté `XX.X%`.
- `% recherche signal` : `100 - bars_in_position / bars_total * 100`.
- `Signaux acceptés` : `diagnostics.signals_accepted`.
- `Trades ouverts` : `diagnostics.trades_opened`.
- `Rejets notional` : `diagnostics.rejections.notional`.
- `Rejets ATR≤0` : `diagnostics.rejections.atr_le_zero`.
- `Max barres en pos.` : `diagnostics.max_bars_in_position`.
- `Max barres sans sig.` : `diagnostics.max_bars_without_signal`.

**Bloc 2 — Warnings contextuels** (3 conditions, encarts ambre) :
- Si `signals_accepted == 0` : « Aucun signal accepté — vérifiez les filtres ADX, le score_threshold et les conditions de setup. »
- Si `max_bars_in_position > 500` : « Position stuck > 500 barres — vérifiez `disable_trailing` et `exit_after_bars` dans le YAML de la stratégie. »
- Si `signals_accepted > 0 && trades_opened == 0` : « Signaux acceptés mais 0 trade ouvert — rejets notional < 1 USDC, vérifiez `risk_per_trade` et `capital`. »

**Bloc 3 — Tableau per-strategy** (si multi-stratégies) :
| Stratégie | Évalué | side=none | Proposé | < seuil | ≥ seuil | Erreurs |
- `Évalué` : nombre de bougies évaluées par la stratégie.
- `side=none` : signaux neutres (pas de direction).
- `Proposé` : signaux avec direction.
- `< seuil` : signaux filtrés par `score_threshold`.
- `≥ seuil` : signaux passés le seuil.
- `Erreurs` : exceptions levées pendant l'évaluation.

**Bloc 4 — Note explicative** (footer, muted) :
- Sémantique de chaque colonne en 1 phrase (tooltip au survol du `?` à côté du header).

#### Composants React

- `<DiagnosticsPanel>` (à créer) — props :
  ```typescript
  interface DiagnosticsPanelProps {
    diagnostics: BacktestDiagnostics;
    perStrategy?: Array<{
      strategy: string;
      evaluated: number;
      side_none: number;
      proposed: number;
      below_threshold: number;
      above_threshold: number;
      errors: number;
    }>;
  }
  ```

#### État et store

- Aucun état local (composant pur).
- Type `BacktestDiagnostics` à ajouter dans `frontend/src/types/index.ts`.

#### Interactions

- Hover sur `?` à côté d'un header de colonne → tooltip explicatif.

#### Critères d'acceptation

- [ ] Si `bars_total == 0` : panneau masqué.
- [ ] Les 3 warnings s'affichent indépendamment (peuvent coexister).
- [ ] Le tableau per-strategy est triable par `Évalué` / `Proposé` / `Erreurs`.

#### Non-régression

- [ ] Vérifier backend : champ `diagnostics` déjà renvoyé par `/api/backtest` ? Si non, ajouter dans `app/engine/backtest.py` (coté Python, collecter depuis `Engine.run`).
- [ ] Test backend `tests/test_backtest.py` : ajouter assertion sur la présence de `diagnostics`.

#### Plan de migration

1. Vérifier/corriger le backend pour exposer `diagnostics`.
2. Ajouter le type dans `types/index.ts`.
3. Créer le composant.
4. Intégrer en bas de `BacktestResults` (après le tableau des trades).

---

### BT-004 — Sync serveur + persistance session

**User story.** En tant que trader, je veux que mon UI détecte si un backtest tourne déjà côté serveur (autre onglet, post-reload) et restaure mon dernier résultat après un reload accidentel, afin de ne pas perdre mon travail ni lancer des backtests en double.

**Priorité** : CRITIQUE
**Effort** : 5
**Sprint** : S1
**Fichiers impactés** :
- À créer : `frontend/src/hooks/use-backtest-status.ts`
- À modifier : `frontend/src/hooks/use-api.ts` (ajouter `useBacktestStatus`)
- À modifier : `frontend/src/app/lab/page.tsx` (intégration)
**Endpoints API consommés** : `GET /api/backtest/status` (toutes les 5 s)
**Référence Jinja2** : `backtest.html:277-297` (`_syncBacktestRunState`) + `backtest.html:323-335` (sessionStorage) — voir Annexe A.4

#### Description fonctionnelle

**Bloc 1 — Polling status serveur** :
- Hook `useBacktestStatus` : query TanStack `GET /api/backtest/status`, `refetchInterval: 5000` (5 s).
- Si `status.running == true` : bouton « Analyser » désactivé + message ambre « ⏳ Un backtest est déjà en cours côté serveur (lancé à HH:MM:SS) — [Annuler] ».
- Le lien `[Annuler]` déclenche `useCancelBacktest` (existant).
- Si `status.running == false` : bouton « Analyser » réactivé.

**Bloc 2 — Persistance session** :
- À la réception d'un résultat, sérialiser dans `sessionStorage` clé `bt_data` :
  ```typescript
  {
    result: BacktestResult,
    config: { symbol, timeframe, limit, strategies, walk_forward, monte_carlo, dual_pass },
    timestamp: number,
  }
  ```
- Au mount de `BacktestTab`, lire `bt_data`. Si présent et `< 30 minutes` :
  - Restaurer la config dans les inputs.
  - Restaurer le résultat dans `BacktestResults`.
  - Toast info « ↩ Résultat restauré · N bougies · N strat · lancé il y a X min ».
- Si présent mais `> 30 minutes` : supprimer (stale).
- Bouton « Effacer » dans la toolbar pour vider `bt_data` manuellement.

#### Composants React

- `<BacktestRunningBanner>` (à créer) — props : `{ runningSince: string | null, onCancel: () => void }`.

#### État et store

- Hook `useBacktestStatus` : `{ running: boolean, startedAt: string | null }`.
- Hook `useBacktestSession` : `{ save, load, clear }` (3 fonctions wrapper autour de `sessionStorage`).

#### Interactions

- Au mount : `load()` silencieusement.
- À chaque résultat reçu : `save(result, config)`.
- Au clic bouton « Effacer » : `clear()` + reset état local.

#### Critères d'acceptation

- [ ] Lancer un backtest dans un onglet → ouvrir un 2e onglet → le bouton est désactivé dans le 2e onglet.
- [ ] Reload de la page pendant un run → message « backtest en cours » persiste.
- [ ] Reload après un run terminé → résultat restauré si < 30 min.
- [ ] Bouton « Effacer » vide la session.

#### Non-régression

- [ ] Vérifier backend : `GET /api/backtest/status` existe (probable, à confirmer dans `app/api/routes/backtest.py`).
- [ ] Tests e2e : pas d'impact (Playwright n'utilise pas sessionStorage cross-sessions).

#### Plan de migration

1. Créer les hooks isolés.
2. Intégrer `useBacktestStatus` dans `BacktestTab`.
3. Intégrer `useBacktestSession` (save/load).
4. Ajouter le bouton « Effacer ».

---

### BT-005 — Barre de progression + ETA + log horodaté

**User story.** En tant que trader, je veux voir la progression de mon backtest en temps réel (barre animée, ETA estimé, log horodaté), afin de savoir si je dois attendre 10 s ou 5 min et détecter un freeze.

**Priorité** : HIGH
**Effort** : 5
**Sprint** : S1
**Fichiers impactés** :
- À créer : `frontend/src/components/cards/backtest-progress.tsx`
- À modifier : `frontend/src/app/lab/page.tsx`
- À vérifier backend : `POST /api/backtest` actuellement synchrone — envisager `POST /api/backtest/async` + `GET /api/backtest/stream` (SSE) OU rester synchrone mais générer les logs côté client (comme Jinja2).
**Endpoints API consommés** : `POST /api/backtest` (synchrone) OU `GET /api/backtest/stream` (à créer si SSE)
**Référence Jinja2** : `backtest.html:163-168` (HTML) + `backtest.html:373-398` (JS `progInterval` + steps simulés) — voir Annexe A.5

#### Description fonctionnelle

**Bloc 1 — Card de progression** (remplace le spinner actuel) :
- Barre de progression animée (gradient cyan→purple, transition width 0.5 s).
- Texte `XX%` à droite de la barre.
- Texte ETA `~Xs restant` ou `~Xmin restant` (en dessous, mono font).
- Bouton « Annuler » à droite.

**Bloc 2 — Log panel** (sous la barre, max-height 200 px, scroll auto en bas) :
- Lignes horodatées `HH:MM:SS · <niveau> · <message>`.
- Niveaux : `run` (cyan), `ok` (vert), `warn` (amber), `err` (rouge).
- Steps simulés (côté client, basés sur le temps écoulé) :
  - `0s` — `Connexion exchange & initialisation…`
  - `~1s` — `Téléchargement des données OHLCV…`
  - `~3s` — `Données reçues — calcul des indicateurs…`
  - `~5s` — `Exécution <strat>…` (une ligne par stratégie)
  - `~7s` — `Calcul des métriques & statistiques…`
  - `~9s` — `Finalisation des résultats de base…`
  - Si Walk-Forward : `Walk-Forward : découpage des folds…` + `↳ WF fold X/5 · <strat>…`
  - Si Monte-Carlo : `Monte-Carlo : simulation des scénarios…` + `↳ MC runs X-Y / 200…`
  - `~12s` — `Assemblage final & sérialisation JSON…`
- À la réception : ligne `ok` `✓ N bougies · date_range · N strat · Xs` + une ligne par stratégie (trades, WR, PnL signé).

**Bloc 3 — ETA algorithm** :
- À T+5 s : ETA = `temps_écoulé / progression_estimée`.
- Progression estimée : 50 % à mi-temps des steps simulés, 90 % à la fin des steps, 100 % à la réception.
- Si pas de réception après 2× l'ETA initial : warning « Le backtest prend plus de temps que prévu — envisagez d'annuler. ».

#### Composants React

- `<BacktestProgress>` (à créer) — props :
  ```typescript
  interface BacktestProgressProps {
    startedAt: number;
    strategies: string[];
    walkForward: boolean;
    monteCarlo: boolean;
    onCancel: () => void;
    onComplete: (result: BacktestResult) => void;
  }
  ```

#### État et store

- État local : `logs: LogEntry[]`, `progress: number` (0-100), `eta: string | null`.
- Effet : `setInterval` 400 ms qui met à jour `progress` + `eta` + pousse les logs simulés.
- Cleanup : `clearInterval` à l'unmount ou à la complétion.

#### Interactions

- Scroll auto en bas du log panel quand une nouvelle ligne arrive (si l'utilisateur n'a pas scroll up manuellement — détecter via `scrollTop + clientHeight >= scrollHeight - 50`).

#### Critères d'acceptation

- [ ] Barre animée dès le lancement.
- [ ] ETA affiché à partir de T+5 s.
- [ ] Logs simulés apparaissent dans l'ordre chronologique.
- [ ] À la réception : logs réels remplacent les simulés (dernière ligne verte).
- [ ] Annulation interrompt les timers.

#### Non-régression

- [ ] Le hook existant `useRunBacktest` reste fonctionnel (utilisé en interne par `BacktestProgress`).
- [ ] Si l'utilisateur ferme l'onglet pendant un run : le backtest continue côté serveur (BT-004 le détectera au prochain mount).

#### Plan de migration

1. Créer `<BacktestProgress>` isolé.
2. Remplacer le spinner actuel par `<BacktestProgress>` quand `isLoading == true`.
3. Conserver le toast sonner « Backtest terminé » en complément (pas en remplacement).
4. Optionnel (S2+) : migrer vers SSE backend si les logs simulés ne suffisent pas pour les très longs backtests (ML retrain).

---

### BT-006 — KPIs manquants (Expectancy, Buy & Hold, Alpha, Equity Finale)

**User story.** En tant que trader, je veux voir les 9 KPIs complets par stratégie (PnL Net + %, Win Rate + n, Sharpe, Max DD, Expectancy, Profit Factor, Equity Finale, Buy & Hold + %, Alpha), afin d'évaluer correctement l'edge vs le market benchmark.

**Priorité** : HIGH
**Effort** : 3
**Sprint** : S1
**Fichiers impactés** :
- À modifier : `frontend/src/app/lab/page.tsx` (section KPI grid l. 636-676)
- À vérifier : `frontend/src/types/index.ts` (champs `expectancy`, `buy_hold_return`, `alpha`, `equity_final` déjà présents ?)
- À vérifier backend : `app/engine/backtest.py` `BacktestResult` renvoie ces champs.
**Endpoints API consommés** : aucun (champs déjà dans la réponse)
**Référence Jinja2** : `backtest.html:539-549` — voir Annexe A.6

#### Description fonctionnelle

**Bloc 1 — KPI grid 3×3 par stratégie** :
| PnL Net (+ %) | Win Rate (+ n trades) | Sharpe (⚠ si <30 trades) |
| Max DD | Expectancy | Profit Factor |
| Equity Finale (+ capital init) | Buy & Hold (+ %) | Alpha (vs B&H) |

- `PnL Net` : `$X (+Y%)` — couleur vert/rouge selon signe.
- `Win Rate` : `XX.X% (N trades)` — ambre si < 30 trades.
- `Sharpe` : `X.XX` — suffixe `⚠ Non significatif` en ambre si `n_trades < 30`.
- `Max DD` : `-XX.X%` — rouge si < -20%, ambre si < -10%, vert sinon.
- `Expectancy` : `$X.XX` — signé.
- `Profit Factor` : `X.XX` — vert si > 1.5, ambre si > 1.0, rouge sinon.
- `Equity Finale` : `$X (capital init: $Y)` — vert si > capital init.
- `Buy & Hold` : `$X (+Y%)` — affiché en muted (c'est le benchmark, pas l'edge).
- `Alpha` : `+X.X%` — vert si > 0, rouge sinon.

#### Composants React

- Modifier `<KpiCard>` (existant) ou créer `<BacktestKpiGrid>` dédié.

#### Critères d'acceptation

- [ ] Les 9 KPIs s'affichent.
- [ ] Si `expectancy` absent (anciens backtests) : afficher « N/A » plutôt que crash.
- [ ] Le warning « < 30 trades » apparaît bien.

#### Non-régression

- [ ] Vérifier que le `Verdict` (l. 451-539) utilise les mêmes 9 KPIs pour son calcul d'edge (sinon le mettre à jour).

#### Plan de migration

Direct : modifier le JSX de la KPI grid. Pas de feature flag nécessaire.

---

### BT-007 — Tableau comparatif multi-stratégies + tabs par stratégie

**User story.** En tant que trader, je veux un tableau côte-à-côte des métriques principales de toutes mes stratégies (avec best value surlignée), puis des tabs pour switcher entre stratégies, afin de comparer rapidement et zoomer sur une stratégie précise.

**Priorité** : HIGH
**Effort** : 5
**Sprint** : S1
**Fichiers impactés** :
- À modifier : `frontend/src/app/lab/page.tsx` (structure de `BacktestResults`)
- À créer : `frontend/src/components/cards/strategy-comparison-table.tsx`
- À utiliser : `frontend/src/components/ui/tabs.tsx` (existant)
**Endpoints API consommés** : aucun
**Référence Jinja2** : `backtest.html:458-466` (tab bar) + `backtest.html:757-768` (`buildCmp`) — voir Annexe A.7

#### Description fonctionnelle

**Bloc 1 — Tableau comparatif** (si > 1 stratégie) :
- 10 colonnes : Trades, Win Rate, PnL net, Max DD, Sharpe, Expectancy, Profit Factor, Avg Win, Avg Loss, Alpha.
- Une ligne par stratégie.
- Best value par colonne surlignée en vert + marqueur `✦`.
- Coloration conditionnelle : PnL/Expectancy vert/rouge, Max DD ambre.

**Bloc 2 — Tabs par stratégie** (au-dessus des résultats détaillés) :
- Un tab par stratégie, label `<nom> (N trades)`.
- Couleur persistée par stratégie via palette (cyan, purple, amber, rose, emerald, blue).
- Seule la stratégie active rend son chart prix, KPI grid, sous-graphiques, table trades, etc.

#### Composants React

- `<StrategyComparisonTable>` (à créer) — props :
  ```typescript
  interface StrategyComparisonTableProps {
    strategies: BacktestStrategyResult[];
    bestHighlights?: boolean;  // défaut true
  }
  ```

#### État et store

- État partagé dans `BacktestResults` : `activeStrategy: string` (index ou nom).
- Palette de couleurs : constante `STRAT_PALETTE = ['#06b6d4', '#a855f7', '#f59e0b', '#ec4899', '#10b981', '#3b82f6']` partagée entre la table, les tabs, et le chart (pour colorer les markers entrée par stratégie).

#### Interactions

- Clic sur un tab → `setActiveStrategy(name)`.
- Clic sur une ligne du tableau comparatif → `setActiveStrategy(line.strategy)` + scroll vers les détails.

#### Critères d'acceptation

- [ ] Avec 5 stratégies : tableau 5 lignes, best value surlignée.
- [ ] Clic sur tab change la stratégie affichée.
- [ ] Les couleurs sont stables entre re-renders.

#### Non-régression

- [ ] Le composant `Verdict` actuel prend la meilleure stratégie par PnL — vérifier qu'il utilise bien la stratégie active du tab ou la meilleure globale (à décider : recommandé = meilleure globale).

#### Plan de migration

1. Extraire la logique « par stratégie » du JSX inline actuel vers un sous-composant `<StrategyDetails>`.
2. Ajouter les tabs au-dessus.
3. Ajouter le tableau comparatif en haut des résultats.

---

### BT-008 — Stats agrégées (Long/Short, WR, Avg Win/Loss, par setup, par raison)

**User story.** En tant que trader, je veux voir les statistiques agrégées de mes trades (répartition Long/Short, win rate par côté, avg win/loss, performance par setup, performance par raison de sortie), afin d'identifier quels setups fonctionnent et lesquels perdent.

**Priorité** : HIGH
**Effort** : 3
**Sprint** : S1
**Fichiers impactés** :
- À créer : `frontend/src/components/cards/trades-stats-panel.tsx`
- À modifier : `frontend/src/app/lab/page.tsx`
**Endpoints API consommés** : aucun (calcul côté client depuis `trades`)
**Référence Jinja2** : `backtest.html:861-926` (tableaux par setup + par raison + chips stats) — voir Annexe A.8

#### Description fonctionnelle

**Bloc 1 — Chips de stats** (6 chips inline) :
- `Long: N` / `Short: N` / `WR Long: XX%` / `WR Short: XX%` / `Avg Win: $X` / `Avg Loss: $X`.

**Bloc 2 — Tableau « Par setup »** (si au moins 1 trade a `setup`) :
| Setup | N | WR% | PnL total |
- Tri par PnL total décroissant.
- Couleur PnL vert/rouge.

**Bloc 3 — Tableau « Par raison de sortie »** :
| Sortie | N | WR% | PnL total |
- Tri par N décroissant.
- Badge raison (mapping exit_reason — voir BT-001 bloc 4).

#### Composants React

- `<TradesStatsPanel>` (à créer) — props : `{ trades: BacktestTrade[] }`. Calcul des agrégats via `useMemo`.

#### Critères d'acceptation

- [ ] Chips affichés même si 0 trade (N=0, WR=0%).
- [ ] Tableau « Par setup » masqué si aucun trade n'a de setup.
- [ ] Tableau « Par raison » toujours affiché (exit_reason toujours présent).

#### Non-régression

- [ ] Aucun impact.

#### Plan de migration

Direct.

---

### BT-009 — Export CSV trades (19 colonnes)

**User story.** En tant que trader, je veux exporter CSV avec 19 colonnes détaillées par trade (id, side, setup, entry/exit time, prix, duration, exit_reason, score, pnl, pnl_pct, fees, sl_atr_mult, tp_atr_mult, exit_after_bars, size_factor, regime_lbl, bearish_excess), afin d'analyser mes trades dans Excel ou un autre outil.

**Priorité** : HIGH
**Effort** : 2
**Sprint** : S1
**Fichiers impactés** :
- À modifier : `frontend/src/components/ui/export-buttons.tsx` (étendre `CsvExportButton` ou créer `TradesCsvExportButton`)
- À créer : `frontend/src/lib/trades-csv.ts` (util de sérialisation)
**Endpoints API consommés** : aucun
**Référence Jinja2** : `backtest.html:1048-1075` (`exportCSV`) — voir Annexe A.9

#### Description fonctionnelle

**CSV headers (19 colonnes)** :
```
id,side,setup,entry_time,exit_time,entry,exit,duration_bars,exit_reason,score,pnl,pnl_pct,fees,sl_atr_mult,tp_atr_mult,exit_after_bars,size_factor,regime_lbl,bearish_excess
```

**Format** :
- BOM UTF-8 en début de fichier (Excel FR).
- Séparateur `,`.
- Échappement `"` autour des valeurs contenant `,` ou `"`.
- Timestamps au format ISO 8601 UTC.
- Décimales : 6 pour prix, 4 pour PnL, 2 pour % .

**Nom de fichier** : `trades_<sym>_<tf>_<strat>_<YYYYMMDDHHmm>.csv`.

#### Composants React

- `<TradesCsvExportButton>` (à créer) — props : `{ trades: BacktestTrade[], meta: { symbol, timeframe, strategy } }`.

#### Critères d'acceptation

- [ ] CSV s'ouvre proprement dans Excel FR (pas de corruption d'accents).
- [ ] 19 colonnes présentes même si valeurs nulles (vide, pas de colonne manquante).
- [ ] Nom de fichier respecté.

#### Non-régression

- [ ] Le `CsvExportButton` actuel (KPIs agrégés) reste fonctionnel — renommer en `KpisCsvExportButton` pour clarifier.

#### Plan de migration

Direct.

---

### BT-010 — Panneau ML spécifique (AUC, features, lookahead, proba_up, warning 0 trades)

**User story.** En tant que trader, je veux un panneau dédié aux stratégies ML affichant AUC cross-val, nombre de features, lookahead, proba haussière moyenne, avec un warning si 0 trade généré, afin de diagnostiquer les problèmes de modèle.

**Priorité** : MEDIUM
**Effort** : 3
**Sprint** : S2
**Fichiers impactés** :
- À créer : `frontend/src/components/cards/ml-backtest-panel.tsx`
- À modifier : `frontend/src/app/lab/page.tsx`
- À vérifier backend : `app/engine/backtest.py` renvoie `ml_info` (dict) pour les stratégies `ml_*`.
**Endpoints API consommés** : `POST /api/backtest` (champ `ml_info` à vérifier/ajouter)
**Référence Jinja2** : `backtest.html:575-580` (`mlHTML`) — voir Annexe A.10

#### Description fonctionnelle

**Bloc 1 — KPIs ML (4)** :
| AUC cross-val | Nb features | Lookahead | Proba haussière moy. |
- `AUC cross-val` : `0.XXX` — vert si ≥ 0.6, ambre si ≥ 0.5, rouge sinon.
- `Nb features` : entier.
- `Lookahead` : entier (barres).
- `Proba haussière moy.` : `0.XXX`.

**Bloc 2 — Warning 0 trades** :
- Si `n_trades == 0` et `strategy.startsWith('ml_')` : encart ambre « ⚠ Aucun signal ML généré — le modèle n'a pas pu produire de trades. Causes possibles : données insuffisantes, filtre ADX trop restrictif, seuil de probabilité trop élevé. Essayez avec ≥ 2000 bougies. »

**Bloc 3 — Note info** :
- « Le modèle est réentraîné périodiquement selon `retrain_every`. Dernier entraînement : `<date>`. Prochain : `<date>`. »

#### Composants React

- `<MLBacktestPanel>` (à créer) — props : `{ mlInfo: BacktestMLInfo, nTrades: number, strategy: string }`.

#### Critères d'acceptation

- [ ] Panneau masqué pour les stratégies non-ML.
- [ ] Warning 0 trades uniquement pour `ml_*`.

#### Non-régression

- [ ] Vérifier backend : `ml_info` déjà renvoyé ? Si non, ajouter.

#### Plan de migration

Backend d'abord, puis frontend.

---

### BT-011 — Threshold warning + sample size warning

**User story.** En tant que trader, je veux être averti si mon `score_threshold` est en dessous du seuil recommandé pour la stratégie, et si mon échantillon de trades est trop petit pour tirer des conclusions, afin de ne pas sur-interpréter des résultats non significatifs.

**Priorité** : MEDIUM
**Effort** : 2
**Sprint** : S2
**Fichiers impactés** :
- À créer : `frontend/src/lib/strat-thresholds.ts` (constante `STRAT_THRESHOLDS`)
- À modifier : `frontend/src/app/lab/page.tsx`
**Endpoints API consommés** : `GET /api/backtest/settings` (pour `score_threshold` courant)
**Référence Jinja2** : `backtest.html:223` (`STRAT_THRESHOLD`) + `backtest.html:535-537` — voir Annexe A.11

#### Description fonctionnelle

**Bloc 1 — Constante des seuils recommandés** :
```typescript
export const STRAT_THRESHOLDS: Record<string, number> = {
  fear_momentum: 0.72,
  pullback_trend: 0.65,
  // ... à compléter depuis config
};
```

**Bloc 2 — Threshold warning** :
- Si `current_score_threshold < STRAT_THRESHOLDS[strategy]` : encart bleu « `score_threshold` actuel (X.XX) est en dessous du seuil recommandé (Y.YY) pour <strategy>. [Ajuster dans Config →] ».

**Bloc 3 — Sample size warning** :
- Si `n_trades < 30` : encart ambre « ⚠ N trades — échantillon trop petit pour des conclusions statistiques fiables. »

#### Critères d'acceptation

- [ ] Les 2 warnings peuvent coexister.
- [ ] Le lien « Ajuster dans Config » redirige vers `/settings?tab=strategies&strategy=<name>`.

#### Plan de migration

Direct.

---

### BT-012 — Hint dynamique limit↔durée + quick presets

**User story.** En tant que trader, je veux que le hint sous le champ « Nombre de bougies » convertisse automatiquement en durée selon le TF (1000 bougies en 1h = ~42 jours), et des boutons presets 500/2k/5k/8k, afin de choisir rapidement une fenêtre de backtest pertinente.

**Priorité** : MEDIUM
**Effort** : 2
**Sprint** : S2
**Fichiers impactés** :
- À modifier : `frontend/src/app/lab/page.tsx` (input limit l. 313-322)
- À créer : `frontend/src/lib/limit-hint.ts` (util de conversion)
**Référence Jinja2** : `backtest.html:126-131` (presets) + `backtest.html:196-206` (`updateLimHint`) — voir Annexe A.12

#### Description fonctionnelle

**Bloc 1 — Hint dynamique** :
- Calcul : `limit × TF_minutes / 60` heures, puis conversion :
  - `< 24h` : `≈ Xh de données`
  - `< 30 jours` : `≈ Xj de données` (ambre)
  - `≥ 90 jours` : `≈ Xj (~X mois) de données` (vert)
  - `≥ 365 jours` : `≈ Xj (~X an) de données` (vert)

**Bloc 2 — Presets** :
- 4 boutons inline : `500` / `2k` / `5k` / `8k` (cliquables, set la value).

#### Critères d'acceptation

- [ ] Hint se met à jour au changement de TF ou de limit.
- [ ] Couleurs respectées.

#### Plan de migration

Direct.

---

### BT-013 — Validation regex du symbole côté client

**Priorité** : MEDIUM | **Effort** : 1 | **Sprint** : S2

**User story.** En tant que trader, je veux que mon symbole soit validé en temps réel (regex `^[A-Z0-9]+/[A-Z0-9]+$` pour crypto, ou `^[A-Z0-9.]+$` pour actions), afin d'éviter un round-trip serveur inutile.

**Référence Jinja2** : `backtest.html:221, 358-362`.

**Description** : Input `Symbole` avec `pattern` HTML5 + validation visuelle (bordure rouge + message si invalide). Bouton « Analyser » désactivé si invalide.

**Composants** : modifier l'input existant, ajouter prop `error` au `<Input>` (existant).

**Critères** : [ ] `BTC/USDC` valide. [ ] `btc` invalide + message « Format attendu : BASE/QUOTE (ex: BTC/USDC) ou ticker action (ex: AAPL) ». [ ] Bouton désactivé si invalide.

---

### BT-014 — Stratégies marquées « ● actif » si enabled en config

**Priorité** : MEDIUM | **Effort** : 1 | **Sprint** : S2

**User story.** En tant que trader, je veux voir quelles stratégies sont activées dans ma config actuelle, afin de backtester les bonnes.

**Référence Jinja2** : `backtest.html:307-318`.

**Description** : À côté de chaque checkbox de stratégie, un badge `● actif` (vert) si la stratégie est dans `config.strategies.enabled`. Récupérer via `GET /api/config`.

**Composants** : modifier la liste de checkboxes existante.

**Critères** : [ ] Badge `● actif` visible. [ ] Si `enabled` vide : aucun badge.

---

### BT-015 — Tabs par stratégie persistantes en couleur

**Priorité** : LOW | **Effort** : 1 | **Sprint** : S2

(Couvert par BT-007 — palette `STRAT_PALETTE` partagée.)

---

### BT-016 — Toggle line/candles sur le chart prix

**Priorité** : LOW | **Effort** : 1 | **Sprint** : S2

(Couvert par BT-001 bloc 1.)

---

### BT-017 — Badges exit_reason colorés (dans tableau trades)

**Priorité** : LOW | **Effort** : 1 | **Sprint** : S2

(Couvert par BT-002 bloc 2 + mapping BT-001 bloc 4.)

---

### BT-018 — Markers setup abrégés (↑SIG, ↓TDH)

**Priorité** : LOW | **Effort** : 1 | **Sprint** : S2

(Couvert par BT-001 bloc 3.)

---

---

## 4. Optimizer

Page actuelle : `frontend/src/components/views/optimizer-view.tsx` (724 l.).

### 4.0 Layout cible

**Description structurée par blocs** :
- **Zone 1 — Panneau config gauche (300 px, sticky)** : stratégies (avec badge compatibilité TF), paire, méthode, trials, workers, early stopping, param search optim, timeframes, preview matrix, bougies par TF (avec hint IS/OOS), auto-apply, bouton Lancer.
- **Zone 2 — Zone jobs droite (scrollable)** : feedback post-lancement, groupes par statut (En cours/Erreurs/Annulés/Terminés), bouton « Tout ouvrir/Réduire tout », job cards collapsibles.

### OPT-001 — Before/After table dans les job cards

**User story.** En tant que trader, je veux voir côte-à-côte les métriques avant et après optimisation (Trades, PnL, Sharpe, WR, Drawdown, Alpha), afin de mesurer le gain réel apporté par l'optimisation.

**Priorité** : CRITIQUE
**Effort** : 5
**Sprint** : S2
**Fichiers impactés** :
- À modifier : `frontend/src/components/views/optimizer-view.tsx` (composant `JobCard` l. 172-308)
- À créer : `frontend/src/components/cards/before-after-grid.tsx`
- À vérifier : `frontend/src/types/index.ts` (champs `baseline` et `after` dans `OptimizeJobResult`)
**Endpoints API consommés** : `GET /api/optimize/status` (champ `result.baseline` et `result.after`)
**Référence Jinja2** : `optimizer.html:648-671` — voir Annexe A.13

#### Description fonctionnelle

**Bloc 1 — Grid 2 colonnes (Avant | Après)** :

| Métrique | Avant optimisation | Après optimisation (OOS) | Δ |
|---|---|---|---|
| Trades | N | N | ±N |
| PnL | $X | $Y | ±$Z |
| Sharpe | X.XX | Y.YY | ±Z.ZZ |
| Win Rate | XX% | YY% | ±Z% |
| Drawdown | -XX% | -YY% | ±Z% |
| Alpha | +X% | +Y% | ±Z% |

- Header colonne « Avant » : sous-titre `(source: <baseline_source>)` ex: `backtest OOS`.
- Header colonne « Après » : sous-titre `(OOS)`.
- Colonne `Δ` : couleur vert/rouge selon signe, badge `↑`/`↓`.

#### Composants React

- `<BeforeAfterGrid>` (à créer) — props :
  ```typescript
  interface BeforeAfterGridProps {
    baseline: { trades: number; pnl: number; sharpe: number; win_rate: number; max_drawdown: number; alpha: number; };
    after: { trades: number; pnl: number; sharpe: number; win_rate: number; max_drawdown: number; alpha: number; };
    baselineSource?: string;
  }
  ```

#### Critères d'acceptation

- [ ] Si `baseline` absent : encart muted « Pas de baseline disponible ».
- [ ] Delta calculé même si `after` est nul (compare à 0).
- [ ] Couleurs cohérentes avec la KPI grid backtest.

#### Non-régression

- [ ] Vérifier backend : `result.baseline` déjà renvoyé ? Si non, ajouter dans `opt_persistence.py` (`optimizer_applier.py` a été supprimé — code mort).

#### Plan de migration

1. Vérifier backend (champ `baseline`).
2. Créer le composant isolé.
3. Intégrer dans `JobCard` entre la metrics grid et le best params block.

---

### OPT-002 — Top-5 trials table + best params block

**User story.** En tant que trader, je veux voir le top-5 des trials (IS Score, OOS Score, Final, OOS PnL, WR, DD, Overfit) avec la ligne #1 surlignée 🏆, ainsi que les best params formatés (key: value), afin de juger la robustesse du top-5 et inspecter les valeurs optimisées.

**Priorité** : CRITIQUE
**Effort** : 5
**Sprint** : S2
**Fichiers impactés** :
- À modifier : `frontend/src/components/views/optimizer-view.tsx` (`JobCard`)
- À créer : `frontend/src/components/tables/top-trials-table.tsx`
- À créer : `frontend/src/components/cards/best-params-block.tsx`
**Endpoints API consommés** : `GET /api/optimize/status` (champ `result.top_trials` et `result.best_params`)
**Référence Jinja2** : `optimizer.html:673-698` (best params + top-5) — voir Annexe A.14

#### Description fonctionnelle

**Bloc 1 — Best params block** (au-dessus du top-5) :
- Liste verticale `key: value` en mono font.
- Couleurs : `key` cyan, `value` vert, séparateur `:` muted.
- Formatage des valeurs : 6 décimales pour floats, entier pour ints.
- Si > 10 params : scroll vertical max-height 200 px.

**Bloc 2 — Top-5 trials table** :
| # | IS Score | OOS Score | Final | OOS PnL | OOS WR | OOS DD | Overfit |
- Ligne #1 : background vert léger + `🏆` à gauche.
- Tri impossible (déjà trié par `Final` desc côté backend).
- Couleurs : OOS PnL vert/rouge, Overfit ambre si > 2.

#### Composants React

- `<TopTrialsTable>` (à créer) — props : `{ trials: OptimizeTrial[] }`.
- `<BestParamsBlock>` (à créer) — props : `{ params: Record<string, number | string> }`.

#### Critères d'acceptation

- [ ] Si `top_trials` vide : masquer le bloc.
- [ ] Si `best_params` vide : masquer le bloc.

#### Non-régression

- [ ] Vérifier backend : `top_trials` déjà renvoyé ?

#### Plan de migration

1. Vérifier backend.
2. Créer les composants.
3. Intégrer dans `JobCard` après `<BeforeAfterGrid>`.

---

### OPT-003 — Métrique Overfit + 3 warnings (overfit >2, trades OOS <3, score OOS < -0.05)

**User story.** En tant que trader, je veux être averti si mon optimisation a surappris (overfit > 2), si l'échantillon OOS est trop petit (< 3 trades), ou si le score OOS est négatif au point d'exclure la stratégie du live (< -0.05), afin de ne pas appliquer des params dangereux.

**Priorité** : CRITIQUE
**Effort** : 3
**Sprint** : S2
**Fichiers impactés** :
- À modifier : `frontend/src/components/views/optimizer-view.tsx` (`JobCard`)
- À créer : `frontend/src/components/cards/optimizer-warnings.tsx`
**Endpoints API consommés** : `GET /api/optimize/status` (champs `result.overfit`, `result.oos_score`, `result.oos_trades`)
**Référence Jinja2** : `optimizer.html:712-723` — voir Annexe A.15

#### Description fonctionnelle

**Bloc 1 — Métrique Overfit dans la metrics grid** :
- Cellule `Overfit` : valeur `X.XX`.
- Badge `⚠` (ambre) si > 2, `✓ OK` (vert) sinon.
- Tooltip au survol : « Ratio IS/OOS. Un ratio > 2 indique que la performance IS est 2× supérieure à l'OOS — signe de surapprentissage. »

**Bloc 2 — Warnings** (encarts ambre en dessous de la metrics grid) :
- Si `overfit > 2` : « ⚠ Overfit détecté (X.XX) — la performance IS est largement supérieure à l'OOS. Les params optimisés risquent de ne pas se généraliser. »
- Si `oos_trades < 3` : « ⚠ Seulement N trades sur la période OOS — échantillon insuffisant pour valider l'edge. »
- Si `oos_score < -0.05` : « 🚫 Score OOS < -0.05 — stratégie **exclue du live trading** même si vous appliquez les params. »

#### Composants React

- `<OptimizerWarnings>` (à créer) — props : `{ overfit: number; oosTrades: number; oosScore: number }`.

#### Critères d'acceptation

- [ ] Les 3 warnings peuvent coexister.
- [ ] Si `overfit` est null : pas de warning.

#### Non-régression

- [ ] Vérifier backend : `overfit`, `oos_trades`, `oos_score` déjà renvoyés ?

#### Plan de migration

1. Vérifier backend.
2. Créer le composant.
3. Intégrer dans `JobCard`.

---

### OPT-004 — Champs manquants : early_stopping, limit par TF, ml_tune_hp

**User story.** En tant que trader, je veux pouvoir configurer l'early stopping (arrêt après N trials sans amélioration), le nombre de bougies par TF, et pour le ML le two-phase HP tuning, afin d'avoir le même niveau de contrôle qu'en Jinja2.

**Priorité** : HIGH
**Effort** : 3
**Sprint** : S2
**Fichiers impactés** :
- À modifier : `frontend/src/components/views/optimizer-view.tsx` (config panel)
- À modifier : `frontend/src/hooks/use-api.ts` (vérifier que `useStartOptimize` accepte déjà ces champs — confirmé par subagent, il les accepte mais l'UI ne les expose pas)
**Endpoints API consommés** : `POST /api/optimize/start` (champs `early_stopping`, `limit_per_tf`, `ml_tune_hp`)
**Référence Jinja2** : `optimizer.html:137` (early stopping) + `optimizer.html:147-156` (limit par TF) ; `ml.html:144` (ml_tune_hp)

#### Description fonctionnelle

**Bloc 1 — Early stopping** :
- Input number (0-50), défaut 0.
- Hint : « Arrêt si pas d'amélioration après N trials (0 = off) ».

**Bloc 2 — Bougies par TF** :
- Input number (0-8000), défaut 0 (= auto).
- Hint IS/OOS dynamique (cf. OPT-005).

**Bloc 3 — ml_tune_hp** (visible seulement si une stratégie ML est sélectionnée) :
- Checkbox « Régler aussi les hyperparamètres d'entraînement (two-phase, plus lent) ».

#### Critères d'acceptation

- [ ] Les 3 champs sont envoyés dans le payload `POST /api/optimize/start`.
- [ ] `ml_tune_hp` masqué si aucune stratégie ML sélectionnée.

#### Non-régression

- [ ] Vérifier que le backend `app/api/routes/optimizer.py` accepte ces champs (probable, vu que le hook les envoie déjà).

#### Plan de migration

Direct.

---

### OPT-005 — Hint IS/OOS dynamique par TF coché

**User story.** En tant que trader, je veux voir pour chaque TF coché la répartition IS/OOS en bougies et en jours (ex: « 1h → IS 5200 (~108j) · OOS 2800 (~58j) »), afin de choisir une fenêtre suffisante.

**Priorité** : HIGH
**Effort** : 2
**Sprint** : S2
**Fichiers impactés** :
- À créer : `frontend/src/lib/is-oos-hint.ts`
- À modifier : `frontend/src/components/views/optimizer-view.tsx`
**Référence Jinja2** : `optimizer.html:223-263` (`updateLimHint`)

#### Description fonctionnelle

- Pour chaque TF coché, calculer :
  - `IS = limit_per_tf × 0.65` (convention 65/35).
  - `OOS = limit_per_tf × 0.35`.
  - Conversion en jours selon TF.
- Affichage : `<TF> → IS <N> (~<days>j) · OOS <M> (~<days>j)`.
- Warning ambre si `limit_per_tf × TF_minutes > plafond_OKX` (8 000 bougies max en 1h = ~333j).

#### Critères d'acceptation

- [ ] Hint se met à jour au changement de TF ou de limit.
- [ ] Warning plafond OKX visible.

#### Plan de migration

Direct.

---

### OPT-006 — ETA estimé sur la progress bar

**User story.** En tant que trader, je veux voir un ETA estimé sur la progress bar de l'optimizer, afin de savoir quand mon optimisation sera terminée.

**Priorité** : HIGH
**Effort** : 2
**Sprint** : S2
**Fichiers impactés** :
- À modifier : `frontend/src/components/views/optimizer-view.tsx` (composant `LiveProgress` l. 58-115)
**Endpoints API consommés** : `GET /api/optimize/stream` (SSE — ajouter champ `eta_seconds` côté backend, OU calculer côté client)
**Référence Jinja2** : `optimizer.html:607-618` (`_estimateETA`)

#### Description fonctionnelle

- ETA calculé côté client : `(trials_total - trials_done) × avg_trial_duration`.
- `avg_trial_duration` = moyenne glissante des 5 derniers trials.
- À T+5 s : warmup, ETA = null.
- À T+10 s : ETA calculé, affiché `~Xs restant` ou `~Xmin restant`.

#### Critères d'acceptation

- [ ] ETA null pendant les 5 premières secondes.
- [ ] ETA se raffine au fur et à mesure.

#### Plan de migration

Direct.

---

### OPT-007 — Groupes par statut + bouton « Tout ouvrir/Réduire tout » + job cards collapsibles

**User story.** En tant que trader, je veux voir mes jobs groupés par statut (En cours / Erreurs / Annulés / Terminés) avec un toggle pour tout ouvrir/réduire, et chaque job card collapsible, afin de gérer 20+ jobs sans noyer l'écran.

**Priorité** : HIGH
**Effort** : 5
**Sprint** : S2
**Fichiers impactés** :
- À modifier : `frontend/src/components/views/optimizer-view.tsx` (jobs grid l. 689-720 + JobCard l. 172-308)
**Référence Jinja2** : `optimizer.html:564-596` (groupes + toggle) — voir Annexe A.16

#### Description fonctionnelle

**Bloc 1 — Groupes par statut** :
- 4 sections : `En cours (N)` (cyan), `Erreurs (N)` (rouge), `Annulés (N)` (ambre), `Terminés (N)` (vert).
- Header collapsible (clic pour masquer/afficher le groupe).
- Si un groupe est vide, le masquer.

**Bloc 2 — Bouton « Tout ouvrir / Réduire tout »** :
- En haut de la zone jobs.
- Toggle tous les jobs `Terminés` (les `En cours` restent expanded).

**Bloc 3 — Job cards collapsibles** :
- Header toujours visible (stratégie, statut, PnL, score, bouton ⌄).
- Détails (metrics, before/after, best params, top-5, apply) masqués par défaut pour les `Terminés`.
- Auto-expand pour les `En cours` et les `Terminés` nouvellement terminés (via `useEffect` sur `status` change).

#### Critères d'acceptation

- [ ] 20 jobs Terminés : groupe collapsed par défaut, 1 clic ouvre tout.
- [ ] Job en cours : auto-expanded.
- [ ] Job qui passe de running à done : reste expanded.

#### Plan de migration

1. Extraire `JobCard` dans un composant dédié si pas déjà fait.
2. Ajouter état `collapsed` (default true pour done, false pour running).
3. Wrapper la grid dans un `<JobGroup>` par statut.

---

### OPT-008 — Feedback post-lancement (bougies fetch par TF, skipped combinations)

**User story.** En tant que trader, je veux voir le feedback détaillé après lancement (bougies récupérées par TF, combinaisons ignorées avec raison), afin de comprendre ce qui a été lancé.

**Priorité** : MEDIUM
**Effort** : 2
**Sprint** : S3
**Fichiers impactés** :
- À modifier : `frontend/src/components/views/optimizer-view.tsx`
**Référence Jinja2** : `optimizer.html:452-504` (`startOpt`)

#### Description fonctionnelle

**Bloc 1 — Loading immédiat** :
- Dès le clic sur « Lancer », card ambre en haut de la zone jobs : spinner + « Récupération des bougies en cours… ».

**Bloc 2 — Feedback post-réponse** :
- Card verte `✓ N job(s) lancé(s)` avec :
  - Liste fetch par TF : `1h: 5200 bougies` (+ `(max OKX)` si plafonné).
  - Liste skipped : `⚠ N combinaison(s) ignorée(s) : strategy@tf — reason`.

#### Critères d'acceptation

- [ ] Loading visible < 100 ms après le clic.
- [ ] Skipped combinations affichées avec raison.

#### Plan de migration

Direct.

---

### OPT-009 — Symbol libre (input texte)

**User story.** En tant que trader, je veux pouvoir saisir n'importe quel symbole (crypto OU action SBF 120), afin de ne pas être limité aux 5 hardcodés.

**Priorité** : MEDIUM
**Effort** : 2
**Sprint** : S3
**Fichiers impactés** :
- À modifier : `frontend/src/components/views/optimizer-view.tsx` (l. 40, 502-519 — remplacer les 5 chips hardcodés par un input multi)
**Référence Jinja2** : `optimizer.html:117` (`#opt-symbol`)

#### Description fonctionnelle

- Input texte libre + bouton « Ajouter » (Enter ajoute).
- Chips des symboles ajoutés (supprimables via X).
- Validation regex (cf. BT-013).
- Persistance en `localStorage` clé `opt.symbols` (5 derniers utilisés).

#### Critères d'acceptation

- [ ] `BTC/USDC` ajoutable.
- [ ] `AIR.PA` ajoutable (action).
- [ ] Symbole invalide refusé avec message.

#### Plan de migration

Direct.

---

### OPT-010 — Note « Globaux non-optimisés »

**Priorité** : MEDIUM | **Effort** : 1 | **Sprint** : S3

**User story.** En tant que trader, je veux être rassuré sur le fait que l'optimiseur ne touche pas aux paramètres globaux (score_threshold, risk_per_trade, capital, timeframe, paper_mode), afin de comprendre le périmètre d'action de l'optimisation.

**Référence Jinja2** : `optimizer.html:401-410`.

**Description** : Encart bleu en bas du panneau config : « Les paramètres globaux ne sont jamais modifiés par l'optimiseur : `score_threshold`, `risk_per_trade`, `capital`, `timeframe`, `paper_mode`. »

**Critères** : [ ] Encart visible sous la preview matrix.

---

### OPT-011 — Badges compatibilité TF par stratégie

**Priorité** : MEDIUM | **Effort** : 2 | **Sprint** : S3

**User story.** En tant que trader, je veux voir à côté de chaque stratégie quels TFs sont recommandés (cyan) vs non-recommandés mais sélectionnés (ambre ⚠), afin d'éviter de lancer une optimisation sur un TF inadapté.

**Référence Jinja2** : `optimizer.html:267-285` (`renderStratChecks`).

**Description** : À côté de chaque chip de stratégie, badges `<TF>` en cyan pour les recommandés, `<TF>⚠` en ambre pour les non-recommandés mais sélectionnés.

**Composants** : modifier la liste de chips existante.

**Critères** : [ ] Recommandations chargées depuis `GET /api/optimize/spaces` (champ `recommended_tfs` par stratégie).

---

### OPT-012 — Layout 2 colonnes (config sticky)

**Priorité** : LOW | **Effort** : 2 | **Sprint** : S3

**User story.** En tant que trader, je veux que la config reste visible (sticky) pendant que je scrolle les jobs, afin de ne pas avoir à remonter pour relancer.

**Description** : Grid `grid-template-columns: 300px 1fr`. Config sticky `position: sticky; top: 56px`. Responsive : 1 colonne sous 860 px.

**Critères** : [ ] Config reste visible pendant le scroll des jobs.

---

### OPT-013 — n_jobs select guidé (1/2/4/-1)

**Priorité** : LOW | **Effort** : 1 | **Sprint** : S3

**User story.** En tant que trader, je veux un select guidé pour `n_jobs` (1 / 2 / 4 ARM A1 / -1 = tous CPU), afin de ne pas avoir à connaître le nombre de CPUs.

**Description** : Remplacer l'input number par un `<Select>` avec 4 options prédéfinies. Tooltip explicatif sur l'option ARM A1.

**Critères** : [ ] 4 options disponibles. [ ] Option `-1` libellée « Tous les CPUs ».

---

---

## 5. Replay

⚠ **Note critique** : La vue Next.js actuelle (`replay-view.tsx`, 476 l.) n'est PAS un replay interactif bougie-par-bougie — c'est un batch multi-TF runner. Le feature entier a changé de nature. Le batch multi-TF est utile et doit être conservé (renommé « Multi-TF Batch »), mais le replay interactif Jinja2 doit être recréé.

Page actuelle : `frontend/src/components/views/replay-view.tsx` (476 l.).
Page Jinja2 de référence : `replay.html` (814 l.) — voir Annexe A.17 pour le moteur complet.

### 5.0 Layout cible — Replay interactif

**Description structurée par blocs** :
- **Zone 1 — Sidebar gauche (240 px, sticky, full height)** : paire, timeframe (mono), range slider Mois (1-24) avec hint bougies, select stratégie overlay, bouton Charger/Annuler, log panel horodaté, stats panel live (Bougies, Période, Trades, WR, PnL).
- **Zone 2 — Chart area (pleine hauteur, full-width restant)** : candlestick LightweightCharts plein écran, badge OHLCV top-left (mis à jour au survol crosshair), badge position top-right « current / total ».
- **Zone 3 — Playback controls (barre en bas du chart)** : progress bar scrubbable + 7 boutons transport (Début / -10 / -1 / Play-Pause / +1 / +10 / Fin) + 7 boutons vitesse (0.5× / 1× / 2× / 5× / 10× / 20× / MAX) + compteur position.
- **Zone 4 — Signal log (en bas, max-height 110 px)** : une ligne par sortie de trade (time, dir, exit price, PnL).

### 5.1 Découpage stratégique — Conserver le batch multi-TF, recréer le replay

**Décision produit recommandée** :
1. Renommer la vue actuelle `replay-view.tsx` en `multi-tf-batch-view.tsx` (feature distincte, utile).
2. Créer un nouveau `replay-view.tsx` avec le replay interactif.
3. Dans le tab bar du Lab, ajouter 2 onglets : « Replay » et « Multi-TF Batch ».

### RPL-001 — Replay interactif bougie-par-bougie (chart + playback + 7 vitesses)

**User story.** En tant que trader, je veux rejouer le marché bougie par bougie avec contrôle playback (play/pause/step ±1/±10/seek), 7 vitesses (0.5× à MAX), et progress bar scrubbable, afin d'observer visuellement le comportement de ma stratégie en temps réel sur l'historique.

**Priorité** : CRITIQUE
**Effort** : 13
**Sprint** : S3
**Fichiers impactés** :
- À créer : `frontend/src/components/views/replay-view.tsx` (nouveau, remplace l'actuel renommé)
- À créer : `frontend/src/components/charts/replay-candlestick-chart.tsx`
- À créer : `frontend/src/components/controls/playback-controls.tsx`
- À créer : `frontend/src/hooks/use-replay-engine.ts`
- À renommer : `frontend/src/components/views/replay-view.tsx` → `multi-tf-batch-view.tsx`
- À modifier : `frontend/src/app/lab/page.tsx` (ajouter un 6e onglet)
**Endpoints API consommés** : `POST /api/replay` (à vérifier — endpoint existant pour charger les bougies + calculer les trades d'une stratégie sur la fenêtre)
**Référence Jinja2** : `replay.html:542-689` (moteur `tick` / `step` / `seekTo` / `setSpeed`) — voir Annexe A.17

#### Description fonctionnelle

**Bloc 1 — Chart candlestick plein écran** :
- LightweightCharts v4.2.0, candlestick series, hauteur = `calc(100vh - 56px - 80px - 110px)` (topbar - playback - signal log).
- Couleurs up/down vert/rouge, wick muted.
- Crosshair avec tooltip OHLCV au survol (déjà implémenté dans `smart-replay-view.tsx`, à factoriser dans un hook `useCrosshairTooltip`).
- **OHLCV info badge** top-left du chart : `O X.XXXX H X.XXXX L X.XXXX C X.XXXX V XXX` (mis à jour au survol crosshair).
- **Position badge** top-right : `current / total` (ex: `1234 / 4320`).

**Bloc 2 — Progress bar scrubbable** :
- Largeur 100 %, hauteur 8 px, thumb draggable.
- Clic n'importe où sur la barre → seek direct à cette position.
- Drag du thumb → scrub continu.
- Touch support (mobile/tablette).

**Bloc 3 — 7 boutons transport** (inline) :
| ⏮ | ◀◀ | ◀ | ▶/⏸ | ▶ | ▶▶ | ⏭ |
- `⏮` Début (Home) : seek 0.
- `◀◀` -10 : step -10.
- `◀` -1 : step -1.
- `▶/⏸` Play/Pause (Space) : toggle lecture.
- `▶` +1 : step +1.
- `▶▶` +10 : step +10.
- `⏭` Fin (End) : seek total.

**Bloc 4 — 7 boutons vitesse** (inline, à droite des transport) :
| 0.5× | 1× | 2× | 5× | 10× | 20× | MAX |
- Vitesse active surlignée.
- `MAX` : batch 100 bougies par frame via `requestAnimationFrame` (instantané visuel).

**Bloc 5 — Compteur position** :
- À droite des vitesses : `X / Y` (FR locale : `1 234 / 4 320`).

#### Composants React

- `<ReplayCandlestickChart>` (à créer) — props :
  ```typescript
  interface ReplayChartProps {
    candles: Candle[];
    currentPosition: number;
    onCrosshairMove?: (candle: Candle | null) => void;
    onSeek?: (pos: number) => void;
  }
  ```
- `<PlaybackControls>` (à créer) — props :
  ```typescript
  interface PlaybackControlsProps {
    position: number;
    total: number;
    isPlaying: boolean;
    speed: ReplaySpeed;
    onPlayPause: () => void;
    onStep: (delta: number) => void;
    onSeek: (pos: number) => void;
    onSpeedChange: (s: ReplaySpeed) => void;
  }
  type ReplaySpeed = 0.5 | 1 | 2 | 5 | 10 | 20 | 'MAX';
  ```

#### État et store

- Hook `useReplayEngine` :
  ```typescript
  interface ReplayState {
    candles: Candle[];
    trades: BacktestTrade[];
    position: number;
    isPlaying: boolean;
    speed: ReplaySpeed;
    accumulatedStats: { trades: number; wins: number; pnl: number };
  }
  ```
- Effet : `useEffect` qui démarre un `requestAnimationFrame` loop si `isPlaying && speed !== 'MAX'`. Pour `speed === 'MAX'`, batch 100 bougies par frame.
- À chaque `tick()` : `series.update(candles[position])` (incrémental, pas de reset), pousse les markers entrée/sortie dont la bar ≤ position, met à jour les stats accumulées.
- `step(n)` : avancer/reculer de n bougies. Recul = reconstruction depuis 0 (LightweightCharts ne supporte pas le `undo`).
- `seekTo(pos)` : reconstruction complète (bougies jusqu'à pos + markers jusqu'à pos + reset stats puis re-accumulate).

#### Interactions

- Space → play/pause.
- ←/→ → ±1 bougie (Shift = ±10).
- Home/End → début/fin.
- 1/2/5/0 → vitesses 1×/2×/5×/MAX.
- Désactivé si focus dans un input/select (`document.activeElement.tagName` check).

#### Critères d'acceptation

- [ ] 4 000 bougies se chargent en < 2 s.
- [ ] Play en 1× défile à 1 bougie/seconde.
- [ ] Play en MAX atteint la fin en < 5 s.
- [ ] Seek à 50 % affiche correctement bougies + markers + stats.
- [ ] Step -10 recule de 10 bougies (reconstruction invisible).
- [ ] Raccourcis clavier fonctionnels hors inputs.

#### Non-régression

- [ ] `smart-replay-view.tsx` (SMC replay) reste fonctionnel — il a son propre moteur, à harmoniser après.
- [ ] Tests e2e : snapshot `laboratoire-chromium-linux.png` à regénérer (nouvel onglet).

#### Plan de migration

1. Créer `useReplayEngine` isolé avec données mock.
2. Créer `<ReplayCandlestickChart>` + `<PlaybackControls>`.
3. Intégrer dans un nouveau `replay-view.tsx` (l'ancien est renommé `multi-tf-batch-view.tsx`).
4. Ajouter un 6e onglet dans `lab/page.tsx`.
5. Feature flag `LAB_REPLAY_V2_ENABLED` pour rollout progressif.

---

### RPL-002 — Strategy overlay markers (▲▼ entrées, ✓/✗ sorties)

**User story.** En tant que trader, je veux voir les entrées (▲ Long / ▼ Short) et sorties (✓ gain / ✗ perte) de ma stratégie s'afficher en temps réel sur le chart pendant le replay, afin de visualiser la séquence des trades.

**Priorité** : CRITIQUE
**Effort** : 5
**Sprint** : S3
**Fichiers impactés** :
- À modifier : `frontend/src/components/charts/replay-candlestick-chart.tsx`
- À modifier : `frontend/src/hooks/use-replay-engine.ts`
**Référence Jinja2** : `replay.html:501-532` (`barToEntry`/`barToExit`) + `replay.html:752-780` (markers) — voir Annexe A.17

#### Description fonctionnelle

- À chaque `tick()`, le moteur cherche dans `barToEntry[position]` et `barToExit[position]` (index pré-calculé au chargement).
- Si entrée trouvée : `series.setMarkers([...existing, entryMarker])`.
  - `entryMarker` : `arrowUp` (Long, vert, belowBar, texte `▲`) ou `arrowDown` (Short, rouge, aboveBar, texte `▼`).
- Si sortie trouvée : ajout `exitMarker`.
  - `exitMarker` : `circle` (vert clair si gain, rouge clair si perte, texte `✓` ou `✗`).
- Tri chronologique des markers (exigence LightweightCharts).

#### Critères d'acceptation

- [ ] Markers s'ajoutent au fur et à mesure du replay.
- [ ] Seek arrière重建 correctement les markers.
- [ ] Pas de doublon de markers.

#### Plan de migration

Intégré à RPL-001.

---

### RPL-003 — Signal log temps réel

**User story.** En tant que trader, je veux voir en bas du chart un log temps réel des trades qui se clôturent pendant le replay (time, dir, exit price, PnL), afin de suivre l'activité sans quitter le chart des yeux.

**Priorité** : CRITIQUE
**Effort** : 3
**Sprint** : S3
**Fichiers impactés** :
- À créer : `frontend/src/components/cards/replay-signal-log.tsx`
- À modifier : `frontend/src/components/views/replay-view.tsx`
**Référence Jinja2** : `replay.html:235-238` + `replay.html:783-800` (`addSignalLog`)

#### Description fonctionnelle

- Panel en bas du chart, max-height 110 px, scroll auto vers le haut (plus récent en tête).
- Une ligne par sortie de trade : `HH:MM:SS · <LONG|SHORT> · exit $X.XXXX · PnL ±$Y`.
- Couleur : `LONG` vert, `SHORT` rouge, `PnL` vert/rouge selon signe.

#### Composants React

- `<ReplaySignalLog>` (à créer) — props : `{ entries: SignalLogEntry[] }`.

#### Critères d'acceptation

- [ ] Entrées s'ajoutent en tête au fur et à mesure.
- [ ] Seek arrière vide le log puis reconstruit.

#### Plan de migration

Intégré à RPL-001.

---

### RPL-004 — Live accumulating stats (trades/wins/pnl au fil du replay)

**User story.** En tant que trader, je veux voir les stats (trades, win rate, PnL) s'accumuler en temps réel pendant le replay, afin de voir comment la performance se construit bougie par bougie.

**Priorité** : CRITIQUE
**Effort** : 2
**Sprint** : S3
**Fichiers impactés** :
- À créer : `frontend/src/components/cards/replay-stats-panel.tsx`
- À modifier : `frontend/src/hooks/use-replay-engine.ts`
**Référence Jinja2** : `replay.html:139-149` (stats panel) + accumulation dans `tick()`

#### Description fonctionnelle

- 5 KPIs dans la sidebar : `Bougies` (position/total), `Période` (date from → date to), `Trades` (count), `Win Rate` (XX%), `PnL total` ($X signé).
- Mise à jour à chaque clôture de trade pendant le replay.

#### Composants React

- `<ReplayStatsPanel>` (à créer) — props : `{ stats: ReplayStats }`.

#### Critères d'acceptation

- [ ] Stats à 0 au début du replay.
- [ ] Stats correctes à la fin du replay (égal au backtest équivalent).

#### Plan de migration

Intégré à RPL-001.

---

### RPL-005 — Raccourcis clavier

**Priorité** : HIGH | **Effort** : 2 | **Sprint** : S3

**User story.** En tant que trader, je veux utiliser des raccourcis clavier (Space, ←/→, Home/End, 1/2/5/0) pendant le replay, afin de naviguer rapidement sans cliquer.

**Référence Jinja2** : `replay.html:734-749`.

**Description** :
- Space → play/pause.
- ←/→ → ±1 (Shift = ±10).
- Home/End → début/fin.
- 1/2/5/0 → vitesses 1×/2×/5×/MAX.
- Désactivé si focus dans un input/select.

**Composants** : hook `useReplayKeyboard` attaché au `window` dans `replay-view.tsx`.

**Critères** : [ ] Tous les raccourcis fonctionnels. [ ] Pas d'interférence avec les inputs.

---

### RPL-006 — Range slider Mois avec hint bougies

**Priorité** : HIGH | **Effort** : 2 | **Sprint** : S3

**User story.** En tant que trader, je veux choisir la fenêtre de replay via un slider Mois (1-24) avec hint dynamique « ≈ X bougies », afin d'estimer la durée du replay.

**Référence Jinja2** : `replay.html:288-296`.

**Description** : Slider Radix `Slider` 1-24, label `X mois`, hint `≈ N bougies` (calcul : `months × 30 × 24 / TF_hours`).

**Critères** : [ ] Hint mis à jour au changement de slider ou de TF.

---

### RPL-007 — Layout plein écran (100vh - topbar)

**Priorité** : HIGH | **Effort** : 2 | **Sprint** : S3

**User story.** En tant que trader, je veux que le replay occupe tout l'écran (sans scroll page), afin d'avoir une expérience immersive.

**Description** : Container principal `height: calc(100vh - 56px)`, sidebar sticky, chart area flex-1, playback bar fixed bottom, signal log fixed bottom.

**Critères** : [ ] Pas de scroll page. [ ] Sidebar scrollable indépendamment.

---

### RPL-008 — Select stratégie overlay (dropdown)

**Priorité** : MEDIUM | **Effort** : 2 | **Sprint** : S3

**User story.** En tant que trader, je veux choisir la stratégie overlay via un dropdown (pas un input CSV libre), afin de sélectionner rapidement parmi les stratégies activées.

**Référence Jinja2** : `replay.html` (select peuplé depuis `/api/status`).

**Description** : `<Select>` peuplé depuis `GET /api/status` champ `strategies_enabled`. Groupes optgroup : `Classiques`, `ML`, `SMC`.

**Critères** : [ ] Dropdown fermé au choix. [ ] Stratégie sélectionnable au clavier.

---

### RPL-009 — Welcome screen + log panel horodaté

**Priorité** : MEDIUM | **Effort** : 2 | **Sprint** : S4

**User story.** En tant que trader, je veux un welcome screen avant le chargement (4 tips : vitesse variable, overlay, contrôle total, multi-TF) et un log panel horodaté pendant le chargement, afin d'être guidé.

**Référence Jinja2** : `replay.html:155-181` (welcome) + `replay.html:90-149` (log panel).

**Description** :
- Welcome : 4 cards tip avant tout chargement.
- Log panel : lignes `HH:MM:SS · <niveau> · <message>` pendant le fetch et le calcul initial.

**Critères** : [ ] Welcome visible au premier mount. [ ] Log panel scrollable.

---

---

## 6. ML

⚠ **Note critique** : La vue Next.js actuelle (`ml-view.tsx`, 247 l.) est en lecture seule. Aucun lancement d'optimisation ML, aucun job, aucun apply. Le feature entier d'optimizer ML doit être réintégré.

Page actuelle : `frontend/src/components/views/ml-view.tsx` (247 l.) + `frontend/src/components/cards/ml-recipes-list.tsx` (132 l.).
Page Jinja2 de référence : `ml.html` (790 l.) — quasi identique à `optimizer.html` mais avec filtre ML + `ml_tune_hp`.

### 6.0 Layout cible

Reprendre le layout 2 colonnes de l'Optimizer (OPT-012) :
- **Zone 1 — Panneau config gauche (300 px, sticky)** : stratégies ML filtrées (chips avec badge ML), method, trials, workers, early stopping, TFs multi, preview matrix, bougies par TF + hint IS/OOS, **`ml_tune_hp` checkbox**, auto-apply (avec note « + entraîner le modèle »), bouton « ⬡ Lancer l'optimisation ML ».
- **Zone 2 — Zone jobs droite** : identique à l'Optimizer mais avec note apply « Modèle ML sauvegardé automatiquement » et couleurs cyan.
- **Zone 3 — Panel « État ML »** (en haut, existant) : conserver `StrategyTable` (AUC + prochain retrain) + `CandlesStatsTable` + `MLRecipesList` (ajouts Next.js à préserver).

### ML-001 — Optimizer ML complet (jobs cards avec progress SSE + apply)

**User story.** En tant que trader, je veux pouvoir lancer des jobs d'optimisation sur mes stratégies ML, voir la progress SSE en temps réel, et appliquer les params optimisés (+ entraîner le modèle), afin d'optimiser mes stratégies ML depuis le Lab sans passer par la ligne de commande.

**Priorité** : CRITIQUE
**Effort** : 8
**Sprint** : S4
**Fichiers impactés** :
- À créer : `frontend/src/components/views/ml-optimizer-view.tsx` (duplication de `optimizer-view.tsx` avec filtre `is_ml=true`)
- À modifier : `frontend/src/app/lab/page.tsx` (onglet ML pointe vers `ml-optimizer-view`)
**Endpoints API consommés** : `POST /api/optimize/start` (avec `is_ml=true`), `GET /api/optimize/status`, `GET /api/optimize/stream`, `POST /api/optimize/apply`
**Référence Jinja2** : `ml.html:113-167` (config) + `ml.html:587-736` (job cards) — voir Annexe A.18

#### Description fonctionnelle

**Bloc 1 — Config panel** :
- Stratégies filtrées : `info.is_ml === true` uniquement. Badge `ML` (cyan) à côté de chaque chip.
- Méthode (bayesian/random/grid), trials (10-200), workers, early stopping.
- TFs multi + preview matrix (cf. OPT-005 pour hint IS/OOS).
- `ml_tune_hp` checkbox (voir ML-002).
- Auto-apply avec note explicite : « Appliquer automatiquement les meilleurs params + **entraîner le modèle** ».
- Bouton « ⬡ Lancer l'optimisation ML » (cyan, icône hexagone).

**Bloc 2 — Jobs cards** :
- Identiques à l'Optimizer (OPT-001 à OPT-007) mais :
  - Couleurs cyan (au lieu de purple pour non-ML).
  - Note apply : « Écrase uniquement les params optimisés — **le modèle ML est sauvegardé automatiquement** ».

**Bloc 3 — Conservation des acquis Next.js** :
- `StrategyTable` (AUC + prochain retrain) reste affiché en haut de la page.
- `CandlesStatsTable` (cache bougies) reste.
- `MLRecipesList` (recettes LightGBM) reste avec le bouton « Entraîner » (cf. ML-003).

#### Composants React

- `<MLOptimizerView>` (à créer) — wrapper autour de `<OptimizerView>` avec `filterMl={true}` prop, OU duplication pure (recommandé pour clarté).

#### Critères d'acceptation

- [ ] Seules les stratégies ML sont sélectionnables.
- [ ] Lancement d'un job crée un job avec `is_ml=true` côté backend.
- [ ] Apply entraîne bien le modèle (vérifier via `MLRecipesList` qui doit montrer le statut « Entraîné » après apply).

#### Non-régression

- [ ] L'info card existante « Cette page est en lecture seule » doit être retirée.
- [ ] Le lien vers `/models` (registre versionné) reste accessible.

#### Plan de migration

1. Dupliquer `optimizer-view.tsx` en `ml-optimizer-view.tsx`.
2. Ajouter le filtre `is_ml=true` sur la liste de stratégies.
3. Ajouter `ml_tune_hp` checkbox.
4. Ajouter la note apply « modèle sauvegardé ».
5. Brancher l'onglet ML du Lab sur `ml-optimizer-view`.
6. Feature flag `LAB_ML_OPTIMIZER_ENABLED`.

---

### ML-002 — `ml_tune_hp` checkbox (two-phase HP tuning)

**Priorité** : CRITIQUE | **Effort** : 1 | **Sprint** : S4

(Couvert par ML-001 — le hook `useStartOptimize` accepte déjà `ml_tune_hp`, il suffit de l'exposer dans l'UI.)

**Référence Jinja2** : `ml.html:144` (`opt-ml-tune`).

**Description** : Checkbox « Régler aussi les hyperparamètres d'entraînement (two-phase, plus lent) » dans la config panel, visible seulement si une stratégie ML est sélectionnée.

**Critères** : [ ] Checkbox envoie `ml_tune_hp: true` dans le payload. [ ] Masquée si aucune stratégie ML.

---

### ML-003 — Bouton « Entraîner » directement depuis la recette

**User story.** En tant que trader, je veux pouvoir entraîner un modèle directement depuis la liste des recettes ML (sans redirect vers `/models`), afin de garder mon workflow dans le Lab.

**Priorité** : HIGH
**Effort** : 3
**Sprint** : S4
**Fichiers impactés** :
- À modifier : `frontend/src/components/cards/ml-recipes-list.tsx` (l. ~120 — bouton « Entraîner »)
- À créer : `frontend/src/hooks/use-train-recipe.ts`
**Endpoints API consommés** : `POST /api/ml/train` (à vérifier — endpoint existant pour entraîner une recette)
**Référence Jinja2** : N/A (les recettes n'existaient pas en Jinja2, c'est un ajout Next.js).

#### Description fonctionnelle

- Le bouton « Entraîner » actuel redirige vers `/models?recipe=…`.
- Nouveau comportement : au clic, ouvre une `Dialog` Radix avec :
  - Récap de la recette (nom, label_scheme, features_catalog, heads).
  - Input symbole (défaut : BTC/USDC).
  - Input timeframe (défaut : 1h).
  - Input bougies (défaut : 2000, min 1500 pour omnibus).
  - Bouton « Lancer l'entraînement » → appelle `POST /api/ml/train`.
  - Progress SSE via `GET /api/ml/train/stream` (à vérifier).
- À la fin : toast succès + refresh `StrategyTable` (le statut doit passer à « Entraîné »).
- Le lien « Voir dans le registre » reste accessible (redirige vers `/models?recipe=…`).

#### Composants React

- `<TrainRecipeDialog>` (à créer) — props : `{ recipe: MLRecipe, open: boolean, onOpenChange: (o: boolean) => void }`.

#### Critères d'acceptation

- [ ] Clic sur « Entraîner » ouvre la dialog (pas de redirect).
- [ ] Lancement déclenche `POST /api/ml/train`.
- [ ] Progress visible en temps réel.
- [ ] À la fin : `StrategyTable` rafraîchie.

#### Non-régression

- [ ] Le redirect vers `/models` reste disponible via un lien secondaire dans la dialog.

#### Plan de migration

1. Vérifier backend : `POST /api/ml/train` + `GET /api/ml/train/stream` existent ?
2. Créer le hook `useTrainRecipe`.
3. Créer `<TrainRecipeDialog>`.
4. Brancher le bouton « Entraîner » sur la dialog au lieu du redirect.

---

### ML-004 — Auto-apply + entraînement modèle

**Priorité** : CRITIQUE | **Effort** : 2 | **Sprint** : S4

(Couvert par ML-001 — l'auto-apply doit déclencher l'entraînement en plus de l'apply des params.)

**Référence Jinja2** : `ml.html` (auto-apply note).

**Description** : Quand `auto_apply=true` et qu'un job ML se termine, le backend doit :
1. Appliquer les params optimisés dans le YAML.
2. Lancer l'entraînement du modèle avec ces params.
3. Mettre à jour le statut dans `MLRecipesList` (passer à « Entraîné »).

**Critères** : [ ] Vérifier backend : `auto_apply` déclenche bien l'entraînement. Si non, ajouter dans `app/api/routes/optimizer.py`.

---

### ML-005 — Preview matrix strat × TF pour stratégies ML

**Priorité** : MEDIUM | **Effort** : 1 | **Sprint** : S4

(Couvert par ML-001 — réutilisation du composant `PreviewMatrix` de l'Optimizer.)

---

### ML-006 — Hint IS/OOS dynamique pour ML (omnibus exigent ~2200+ bougies)

**Priorité** : MEDIUM | **Effort** : 1 | **Sprint** : S4

(Couvert par OPT-005 — mais avec une spécificité ML : warning si `< 2200` bougies pour les recettes omnibus.)

**Description** : Hint IS/OOS standard + warning ambre si `limit_per_tf < 2200` et qu'une stratégie omnibus est sélectionnée : « ⚠ Les recettes omnibus exigent ≥ 2200 bougies pour un entraînement fiable. »

---

### ML-007 — Note « modèle ML sauvegardé automatiquement »

**Priorité** : MEDIUM | **Effort** : 1 | **Sprint** : S4

(Couvert par ML-001 bloc 2.)

---

---

## 7. Compare

Page actuelle : `frontend/src/components/views/compare-view.tsx` (521 l.).
Page Jinja2 de référence : `compare.html` (206 l.).

La vue Next.js est déjà de bonne qualité (10 colonnes, tri, equity curves overlaid — 🆕 acquis à préserver). Quelques manques ciblés à combler.

### 7.0 Layout cible

Conserver le layout actuel (stack vertical avec equity curves overlaid). Ajouter l'input bougies libre + colonne Equity finale + rangs + raccourcis.

### CMP-001 — Input bougies libre (200-50000)

**User story.** En tant que trader, je veux saisir librement le nombre de bougies (200-50000) au lieu d'être limité à 4 presets, afin de comparer sur des fenêtres longues (5000+ bougies = ~208j en 1h).

**Priorité** : HIGH
**Effort** : 1
**Sprint** : S5
**Fichiers impactés** :
- À modifier : `frontend/src/components/views/compare-view.tsx` (l. 29, 314 — remplacer le `<Select>` par un `<Input type="number">`)
**Référence Jinja2** : `compare.html:46` (input libre 200-50000).

#### Description fonctionnelle

- Input `<Input type="number" min={200} max={50000} step={100}>`.
- Hint dynamique (cf. BT-012) : conversion bougies → durée selon TF.
- Validation : si `< 200` ou `> 50000`, bouton « Comparer » désactivé + message.

#### Critères d'acceptation

- [ ] 5000, 10000, 50000 saisissables.
- [ ] Hint dynamique.
- [ ] Bouton désactivé si invalide.

#### Non-régression

- [ ] Les 4 valeurs actuelles (100/500/1000/2000) restent valides (l'utilisateur peut les saisir).

#### Plan de migration

Direct : remplacer le `<Select>` par `<Input>`.

---

### CMP-002 — Colonne Equity Finale

**User story.** En tant que trader, je veux voir la colonne « Equity Finale » dans le tableau comparatif, afin de comparer le capital final atteint par chaque stratégie (et non juste le PnL).

**Priorité** : HIGH
**Effort** : 1
**Sprint** : S5
**Fichiers impactés** :
- À modifier : `frontend/src/components/views/compare-view.tsx` (tableau l. 378-459)
- À modifier : `frontend/src/lib/compare-csv.ts` (export CSV — ajouter la colonne)
**Référence Jinja2** : `compare.html:148-187` (colonne Equity finale).

#### Description fonctionnelle

- Ajouter colonne `Equity Finale` entre `PF` et `Expectancy` (ou après `PnL Net`).
- Valeur : `$X,XXX.XX` (formaté FR).
- Best value surlignée (vert).
- Tri cliquable.

#### Critères d'acceptation

- [ ] Colonne présente.
- [ ] Tri fonctionnel.
- [ ] CSV export inclut la colonne.

#### Non-régression

- [ ] Conserver les colonnes `Best Trade` / `Worst Trade` (ajouts Next.js à préserver).

#### Plan de migration

Direct.

---

### CMP-003 — Rang #1, #2… devant chaque stratégie

**Priorité** : MEDIUM | **Effort** : 1 | **Sprint** : S5

**User story.** En tant que trader, je veux voir un rang devant chaque stratégie (classé par PnL desc), afin de lire rapidement le classement quand j'ai 10+ stratégies.

**Référence Jinja2** : `compare.html:148-187` (rang devant nom stratégie).

**Description** : Ajouter colonne `#` à gauche de `Strategy`. Rang calculé après tri (si tri par PnL desc, rang = position dans la table ; sinon rang absolu par PnL).

**Critères** : [ ] Rang visible. [ ] Rang persistant même si tri par autre colonne (rang absolu par PnL).

---

### CMP-004 — Raccourcis « toutes / aucune / omnibus »

**Priorité** : MEDIUM | **Effort** : 1 | **Sprint** : S5

**User story.** En tant que trader, je veux 3 boutons raccourcis pour sélectionner rapidement toutes les stratégies, aucune, ou seulement les variantes `opus_omnibus*`, afin de gagner du temps.

**Référence Jinja2** : `compare.html:40-57` (3 raccourcis).

**Description** : 3 boutons inline au-dessus de la liste de chips : `Toutes (N)` / `Aucune` / `Omnibus` (sélectionne `opus_omnibus*`).

**Critères** : [ ] `Toutes` coche tout. [ ] `Aucune` décoche tout. [ ] `Omnibus` ne coche que les `opus_omnibus*`.

---

### CMP-005 — Intro card explicative + indicateur ▼▲

**Priorité** : MEDIUM | **Effort** : 1 | **Sprint** : S5

**User story.** En tant que trader, je veux une intro card qui rappelle le périmètre (même fenêtre de données, complémentaire de l'Audit OOS) et un indicateur visuel ▼▲ sur la colonne triée, afin de comprendre le contexte et lire le tri.

**Référence Jinja2** : `compare.html:5-22` (intro) + indicateur ▼▲.

**Description** :
- Intro card en haut : « Comparatif de N stratégies sur la même fenêtre de données. Complémentaire de l'Audit OOS qui mesure la robustesse out-of-sample. »
- Indicateur ▼ (asc) / ▲ (desc) à côté du label de la colonne triée (au lieu de l'icône générique `ArrowUpDown`).

**Critères** : [ ] Intro visible. [ ] Flèche directionnelle explicite.

---

---

## 8. Synthèse transverse

### 8.1 Patterns récurrents de manque

9 patterns identifiés qui se répètent sur plusieurs features — les traiter une fois bénéficie à tout le Lab :

| # | Pattern | Spécifications concernées | Effort consolidé |
|---|---|---|---|
| **P1** | Feedback temps réel pendant les runs longs (progress bar + ETA + log) | BT-005, OPT-006, RPL-009 | 8 SP (vs 9 si traités séparément) |
| **P2** | Tables détaillées triables/paginables/filtrables/expandables | BT-002, OPT-002, OPT-007 | 13 SP |
| **P3** | Warnings contextuels anti-pied (sample size, overfit, seuils) | BT-011, OPT-003, ML-006 | 5 SP |
| **P4** | Vue comparative multi-stratégies (tabs, groupes, rangs) | BT-007, OPT-007, CMP-003 | 8 SP |
| **P5** | Layout 2 colonnes (config sticky + zone principale) | BT-000 (layout), OPT-012, RPL-007 | 5 SP |
| **P6** | Sync serveur + persistance session | BT-004, (OPT déjà couvert via SSE) | 5 SP |
| **P7** | Hints dynamiques limit↔durée selon TF | BT-012, OPT-005, RPL-006, CMP-001 | 5 SP |
| **P8** | Personnalisation réduite (champs manquants cachés dans l'UI) | OPT-004, ML-002, CMP-001, OPT-009 | 5 SP |
| **P9** | Visualisation des trades (markers entrée/sortie + stop lines) | BT-001, RPL-002 | 13 SP |

### 8.2 Forces du Next.js à préserver absolument

À ne surtout pas régresser dans la refonte :

| # | Acquis Next.js | Localisation | Risque de régression |
|---|---|---|---|
| **F1** | `Verdict` en clair (recommandation actionnable) | `lab/page.tsx:451-539` | Si on modifie la KPI grid (BT-006), le `Verdict` doit utiliser les 9 KPIs |
| **F2** | `CostModelCard` (contexte facturé) | `cards/cost-model-card.tsx` | À intégrer dans les job cards OPT/ML |
| **F3** | `StudyVsLiveCard` (passe étude vs réelle) | `cards/study-vs-live-card.tsx` | Conserver dans BT |
| **F4** | Switch mode expert (opt-in WF/MC/dual_pass) | `lab/page.tsx:350-399` | Conserver |
| **F5** | Equity curves superposées dans Compare | `compare-view.tsx:461-508` | Conserver |
| **F6** | SSE pour l'optimizer (EventSource) | `optimizer-view.tsx:58-115` | Étendre à BT-005 si SSE backend ajouté |
| **F7** | WalkForwardTable avec colonne PnL IS | `charts/walk-forward-table.tsx:133` | Conserver |
| **F8** | MonteCarloPanel avec `prob_ruin_10pct` | `charts/monte-carlo-panel.tsx:159` | Conserver |
| **F9** | `MLRecipesList` (recettes LightGBM) | `cards/ml-recipes-list.tsx` | Conserver dans ML-001 bloc 3 |
| **F10** | `CandlesStatsTable` (cache bougies) | `ml-view.tsx:90-146` | Conserver |
| **F11** | Gestion erreurs par stratégie dans Compare | `compare-view.tsx:436-441` | Conserver |
| **F12** | Composants UI partagés (TimeframeButtons, ChartFullscreen, CsvExportButton, JsonExportButton, ConfirmDialog) | `components/ui/` | Réutiliser partout |
| **F13** | Validation schémas Zod | `lib/schemas.ts` | Étendre aux nouveaux payloads |
| **F14** | Multi-symboles dans l'optimizer | `optimizer-view.tsx:343` | Conserver (Jinja2 = 1 symbole) |
| **F15** | Accessibilité (aria-label, role, tabIndex, caption) | tous les composants | Conserver |
| **F16** | e2e Playwright + snapshots visuels | `frontend/e2e/` | À mettre à jour, pas à supprimer |

### 8.3 Récapitulatif des 52 spécifications par priorité

| Priorité | Count | Spécifications |
|---|---|---|
| **CRITIQUE** | 14 | BT-001, BT-002, BT-003, BT-004, OPT-001, OPT-002, OPT-003, RPL-001, RPL-002, RPL-003, RPL-004, ML-001, ML-002, ML-004 |
| **HIGH** | 14 | BT-005, BT-006, BT-007, BT-008, BT-009, OPT-004, OPT-005, OPT-006, OPT-007, RPL-005, RPL-006, RPL-007, ML-003, CMP-001, CMP-002 |
| **MEDIUM** | 16 | BT-010, BT-011, BT-012, BT-013, BT-014, OPT-008, OPT-009, OPT-010, OPT-011, RPL-008, RPL-009, ML-005, ML-006, ML-007, CMP-003, CMP-004, CMP-005 |
| **LOW** | 8 | BT-015, BT-016, BT-017, BT-018, OPT-012, OPT-013 |

Total effort estimé : **~145 SP** (story points).

### 8.4 Dépendances entre spécifications

```
BT-004 (sync session) ← BT-005 (progress)
BT-007 (tabs) ← BT-001, BT-002, BT-003 (les 3 s'affichent par tab)
BT-012 (hint dynamique) ← OPT-005, RPL-006, CMP-001 (réutilisent l'util)
OPT-007 (groupes + collapsible) ← OPT-001, OPT-002, OPT-003 (contenu des cards)
ML-001 (optimizer ML) ← OPT-001 à OPT-007 (réutilise les composants)
ML-002, ML-004, ML-005, ML-006, ML-007 ← ML-001 (couverts par ML-001)
RPL-001 (moteur) ← RPL-002, RPL-003, RPL-004 (intégrés au moteur)
```

### 8.5 Vérifications backend à faire en amont

Avant de coder le frontend, vérifier ces endpoints/champs backend (probablement déjà présents, à confirmer) :

| Endpoint | Champ à vérifier | Specs dépendantes |
|---|---|---|
| `POST /api/backtest` | `diagnostics` (bars_total, signals_accepted, rejections, per-strategy) | BT-003 |
| `POST /api/backtest` | `ml_info` (auc, n_features, lookahead, proba_up) | BT-010 |
| `POST /api/backtest` | `trades[].stop_trail` (array de {bar, stop}) | BT-001 |
| `POST /api/backtest` | `trades[].conditions`, `trades[].indicators`, `trades[].signal_reason` | BT-002 |
| `POST /api/backtest` | `expectancy`, `buy_hold_return`, `alpha`, `equity_final` | BT-006 |
| `GET /api/backtest/status` | endpoint existe ? | BT-004 |
| `GET /api/optimize/status` | `result.baseline`, `result.after`, `result.top_trials`, `result.best_params`, `result.overfit`, `result.oos_score`, `result.oos_trades` | OPT-001, OPT-002, OPT-003 |
| `POST /api/optimize/start` | accepte `early_stopping`, `limit_per_tf`, `ml_tune_hp` | OPT-004, ML-002 |
| `GET /api/optimize/spaces` | `recommended_tfs` par stratégie | OPT-011 |
| `POST /api/ml/train` + `GET /api/ml/train/stream` | endpoints existent ? | ML-003 |
| `POST /api/optimize/apply` (avec `is_ml=true`) | déclenche l'entraînement du modèle ? | ML-004 |

---

## 9. Plan de migration et non-régression

### 9.1 Stratégie de migration globale

**Approche recommandée : feature flags + coexistence temporaire**.

Pour chaque spec CRITIQUE / HIGH :
1. Créer le nouveau composant isolé (feature flag `LAB_<SPEC>_ENABLED` via env var `NEXT_PUBLIC_LAB_*`).
2. Intégrer en coexistence (ancien + nouveau visibles temporairement, ou toggle dans la toolbar).
3. Après validation visuelle + tests e2e : rendre le nouveau défaut, ancien en opt-in.
4. Après 1 sprint sans régression : supprimer le flag et l'ancien code.

### 9.2 Feature flags à introduire

```bash
# .env.local (dev)
NEXT_PUBLIC_LAB_PRICE_CHART=true      # BT-001
NEXT_PUBLIC_LAB_TRADES_TABLE=true     # BT-002
NEXT_PUBLIC_LAB_DIAGNOSTICS=true      # BT-003
NEXT_PUBLIC_LAB_SESSION_SYNC=true     # BT-004
NEXT_PUBLIC_LAB_PROGRESS=true         # BT-005
NEXT_PUBLIC_LAB_OPT_BEFORE_AFTER=true # OPT-001
NEXT_PUBLIC_LAB_OPT_WARNINGS=true     # OPT-003
NEXT_PUBLIC_LAB_REPLAY_V2=true        # RPL-001
NEXT_PUBLIC_LAB_ML_OPTIMIZER=true     # ML-001
```

En production, tous les flags à `false` par défaut, activés un par un après validation.

### 9.3 Tests de non-régression à conserver

| Test | Localisation | Action |
|---|---|---|
| Snapshot visuel `laboratoire-chromium-linux.png` | `frontend/e2e/tests/visual.spec.ts-snapshots/` | Régénérer après chaque spec CRITIQUE |
| Snapshot visuel `bots-chromium-linux.png` | idem | Inchangé |
| `pages.spec.ts` (e2e navigation) | `frontend/e2e/tests/pages.spec.ts` | Étendre avec nouveaux onglets |
| `a11y.spec.ts` | `frontend/e2e/tests/a11y.spec.ts` | Conserver, nouveaux composants doivent passer |
| `components.test.tsx` (unit) | `frontend/src/components/ui/__tests__/` | Étendre avec tests des nouveaux composants |
| `schemas.test.ts` | `frontend/src/lib/__tests__/` | Étendre avec nouveaux payloads |
| `risk-contracts.test.ts` | `frontend/src/lib/__tests__/` | Inchangé |
| Backend `test_backtest.py` | `tests/test_backtest.py` | Ajouter assertions sur `diagnostics`, `ml_info`, `stop_trail` |
| Backend `test_optimizer.py` (existe ?) | `tests/` | Ajouter assertions sur `baseline`, `top_trials`, `overfit` |
| Backend `test_api_routes.py` | `tests/test_api_routes.py` | Étendre avec nouveaux champs |

### 9.4 Checklist de non-régression par spec

Chaque spec CRITIQUE / HIGH doit valider :

- [ ] Tests unitaires frontend existants passent.
- [ ] Tests e2e Playwright passent (après régénération des snapshots si layout change).
- [ ] Tests backend pertinents passent.
- [ ] Pas de warning TypeScript (`npm run build`).
- [ ] Pas de warning ESLint (`npm run lint`).
- [ ] Pas de régression accessibilité (axe-core dans `a11y.spec.ts`).
- [ ] Pas de régression performance (Lighthouse CI si configuré).
- [ ] Le feature flag permet de revenir à l'ancien comportement en 1 env var.
- [ ] Documentation utilisateur mise à jour (README ou page `/help`).

### 9.5 Plan de rollback

Pour chaque spec :
- **Rollback immédiat** : `NEXT_PUBLIC_LAB_<SPEC>_ENABLED=false` + rebuild.
- **Rollback propre** : revert le commit de la spec (les commits doivent être atomiques par spec, taggés `[BT-001]`, `[OPT-002]`, etc.).
- **Rollback backend** (si spec a nécessité un changement backend) : revert le commit backend correspondant, OU garder le champ backend (rétro-compatible car le frontend ancien l'ignore).

---

## 10. Planning suggéré (6 sprints × 2 semaines)

### Sprint 1 — Backtest critique (35 SP)

| Spec | Priorité | SP | dépend de |
|---|---|---|---|
| BT-001 — Prix + Signaux + stops | CRITIQUE | 8 | — |
| BT-002 — Tableau des trades | CRITIQUE | 8 | — |
| BT-003 — Diagnostics panel | CRITIQUE | 5 | backend |
| BT-004 — Sync + persistance | CRITIQUE | 5 | — |
| BT-005 — Progress + ETA + log | HIGH | 5 | BT-004 |
| BT-006 — KPIs manquants | HIGH | 3 | — |
| BT-007 — Tabs + tableau comparatif | HIGH | 5 | BT-001, BT-002, BT-003 |
| BT-008 — Stats agrégées | HIGH | 3 | BT-002 |
| BT-009 — Export CSV trades 19 col | HIGH | 2 | BT-002 |

**Total** : 44 SP (surchargé — recommandé de découper en 2 sous-sprints ou décaler BT-007/BT-008/BT-009 en S1.5).

### Sprint 2 — Optimizer critique (24 SP)

| Spec | Priorité | SP |
|---|---|---|
| OPT-001 — Before/After | CRITIQUE | 5 |
| OPT-002 — Top-5 + best params | CRITIQUE | 5 |
| OPT-003 — Overfit + warnings | CRITIQUE | 3 |
| OPT-004 — Champs manquants | HIGH | 3 |
| OPT-005 — Hint IS/OOS | HIGH | 2 |
| OPT-006 — ETA progress bar | HIGH | 2 |
| OPT-007 — Groupes + collapsible | HIGH | 5 |

**Total** : 25 SP.

### Sprint 3 — Replay (25 SP)

| Spec | Priorité | SP |
|---|---|---|
| RPL-001 — Moteur replay interactif | CRITIQUE | 13 |
| RPL-002 — Markers overlay | CRITIQUE | 5 (intégré) |
| RPL-003 — Signal log | CRITIQUE | 3 (intégré) |
| RPL-004 — Live stats | CRITIQUE | 2 (intégré) |
| RPL-005 — Raccourcis clavier | HIGH | 2 |
| RPL-006 — Slider Mois + hint | HIGH | 2 |
| RPL-007 — Layout plein écran | HIGH | 2 |
| RPL-008 — Select stratégie | MEDIUM | 2 |

**Total** : 26 SP (surchargé — RPL-001 est l'effort le plus lourd du backlog).

### Sprint 4 — ML (15 SP)

| Spec | Priorité | SP |
|---|---|---|
| ML-001 — Optimizer ML complet | CRITIQUE | 8 |
| ML-002 — ml_tune_hp | CRITIQUE | 1 (intégré) |
| ML-003 — Bouton Entraîner dialog | HIGH | 3 |
| ML-004 — Auto-apply + train | CRITIQUE | 2 |
| ML-005, 006, 007 | MEDIUM | 3 (intégrés) |

**Total** : 14 SP.

### Sprint 5 — Compare + Backtest medium (15 SP)

| Spec | Priorité | SP |
|---|---|---|
| CMP-001 — Input bougies libre | HIGH | 1 |
| CMP-002 — Colonne Equity finale | HIGH | 1 |
| CMP-003 — Rangs | MEDIUM | 1 |
| CMP-004 — Raccourcis omnibus | MEDIUM | 1 |
| CMP-005 — Intro + indicateur tri | MEDIUM | 1 |
| BT-010 — Panneau ML | MEDIUM | 3 |
| BT-011 — Threshold/sample warnings | MEDIUM | 2 |
| BT-012 — Hint dynamique + presets | MEDIUM | 2 |
| BT-013 — Validation regex | MEDIUM | 1 |
| BT-014 — Badge « ● actif » | MEDIUM | 1 |

**Total** : 14 SP.

### Sprint 6 — Détails transverses (15 SP)

| Spec | Priorité | SP |
|---|---|---|
| BT-015 à BT-018 (LOW) | LOW | 4 |
| OPT-008 — Feedback post-lancement | MEDIUM | 2 |
| OPT-009 — Symbol libre | MEDIUM | 2 |
| OPT-010 — Note globaux non-optimisés | MEDIUM | 1 |
| OPT-011 — Badges compat TF | MEDIUM | 2 |
| OPT-012 — Layout 2 colonnes | LOW | 2 |
| OPT-013 — n_jobs select guidé | LOW | 1 |
| RPL-009 — Welcome + log panel | MEDIUM | 2 |

**Total** : 16 SP.

### Planning consolidé

| Sprint | Focus | SP | Cumul |
|---|---|---|---|
| S1 | Backtest critique | 44 | 44 |
| S2 | Optimizer critique | 25 | 69 |
| S3 | Replay | 26 | 95 |
| S4 | ML | 14 | 109 |
| S5 | Compare + Backtest medium | 14 | 123 |
| S6 | Détails transverses | 16 | 139 |

**Total** : ~139 SP sur 12 semaines (6 sprints × 2 semaines), soit ~11.5 SP/semaine. Pour un dev solo à 8 SP/semaine, prévoir 16-18 semaines (8-9 sprints).

### Recommandations de séquençage

1. **Commencer par BT-006, BT-013, BT-014, CMP-001, CMP-002** (specs LOW/HIGH à 1 SP) : quick wins pour la crédibilité.
2. **Faire le backend d'abord** pour BT-003 (diagnostics) et BT-010 (ml_info) : sans ces champs, les composants frontend ne peuvent pas être validés.
3. **RPL-001 en isolé** : c'est le plus gros effort (13 SP), à lancer dès S3 mais à ne pas mélanger avec d'autres specs.
4. **ML-001 après OPT-001 à OPT-007** : réutilisation directe des composants Optimizer.

---

---

## Annexe A — Extraits de code Jinja2 de référence

Les 6 templates Jinja2 complets sont fournis en fichiers compagnons dans le dossier `jinja2-reference/` livré avec ce document :

| Fichier | Lignes | Spécs concernées |
|---|---|---|
| `jinja2-reference/backtest.html` | 1091 | BT-001 à BT-018 |
| `jinja2-reference/optimizer.html` | 790 | OPT-001 à OPT-013 |
| `jinja2-reference/replay.html` | 814 | RPL-001 à RPL-009 |
| `jinja2-reference/ml.html` | 790 | ML-001 à ML-007 |
| `jinja2-reference/compare.html` | 206 | CMP-001 à CMP-005 |
| `jinja2-reference/base.html` | 401 | layout + helpers partagés |

Ces fichiers proviennent du commit `ecc87b2~1` (juste avant suppression S6-09). Pour les régénérer : `git show ecc87b2~1:app/web/templates/<file>.html > <file>.html`.

### A.1 — `buildPrice()` — backtest.html:668-755

Fonction clé pour **BT-001** (graphique prix + signaux + stop lines).

```javascript
function buildPrice(){
  if(tvPrice){try{tvPrice.remove();}catch(e){}tvPrice=null;}
  if(!btData||!activeStrat||!btData.ohlcv)return;
  const ohlcv=btData.ohlcv;
  if(!ohlcv.close||!ohlcv.close.length)return;
  const n=ohlcv.close.length;
  const c=SC(activeStrat),sd=btData.by_strategy[activeStrat];
  const trades=(sd?.trades||[]).filter(t=>t.status&&!t.status.startsWith('open')&&t.bar!=null&&t.exit_bar!=null);
  // ... création chart + series candlestick/line ...

  // ── Markers (entry + exit) ──
  const _exitAbbr={
    'take_profit':'TP','stop_loss':'SL','trailing_stop':'TS',
    'exit_after_bars':'T','end_of_data':'FIN',
    'p_dir_inversion':'INV','p_dir_drop':'DROP',
    'regime_exit_TD':'TD','regime_exit_choppy':'CH',
    'regime_to_TD':'TD↓','p_dir_weak':'WEAK','to_TD':'↓TD'
  };
  trades.forEach(t=>{
    const bi=parseInt(t.bar),ei=parseInt(t.exit_bar),isLong=t.side==='long',isWin=(t.pnl||0)>=0;
    const entryColor=isLong?'#34d399':'#fb7185';
    const setupTxt=t.setup?t.setup.replace('_',' ')
      .replace('SIGNAL UP','↑SIG')
      .replace('SHORT TD HIGH','↓TDH')
      .replace('SHORT TD','↓TD')
      .replace('SHORT CHOPPY','↓CH')
      .replace('LONG RANGE STRICT','↑RNG')
      .replace('LONG CHOPPY','↑CH'):'';
    if(!isNaN(bi)&&bi>=0&&bi<n&&timeMap[bi])
      markers.push({time:timeMap[bi],position:isLong?'belowBar':'aboveBar',color:entryColor,shape:isLong?'arrowUp':'arrowDown',text:setupTxt});
    const exitAbbr=_exitAbbr[t.exit_reason]||t.exit_reason||'';
    if(!isNaN(ei)&&ei>=0&&ei<n&&timeMap[ei])
      markers.push({time:timeMap[ei],position:isLong?'aboveBar':'belowBar',color:isWin?'#34d399':'#fb7185',shape:'circle',text:exitAbbr});
  });
  markers.sort((a,b)=>a.time-b.time);
  tvPriceS.setMarkers(markers);

  // ── Stop lines : initial (dashed amber) + trailing (solid purple) ──
  trades.forEach(t=>{
    const bi=parseInt(t.bar),ei=parseInt(t.exit_bar);
    if(isNaN(bi)||isNaN(ei)) return;
    const t0=timeMap[bi],t1=timeMap[ei];
    if(!t0||!t1||t0>=t1) return;

    const initialStop=t.stop;
    const trail=Array.isArray(t.stop_trail)?t.stop_trail.filter(s=>s&&s.bar!=null&&s.stop!=null):[];
    const trailInRange=trail.filter(s=>{
      const b=parseInt(s.bar);return!isNaN(b)&&b>=bi&&b<=ei&&timeMap[b];
    });

    // Initial stop (dashed amber) from entry to first trail update (or exit)
    const firstTrailTime=trailInRange.length>0?timeMap[parseInt(trailInRange[0].bar)]:t1;
    if(initialStop!=null&&isFinite(initialStop)&&firstTrailTime){
      const stopS=tvPrice.addLineSeries({
        color:'rgba(251,191,36,.5)',lineWidth:1,
        lineStyle:LightweightCharts.LineStyle.Dashed,
        priceLineVisible:false,lastValueVisible:false
      });
      stopS.setData([{time:t0,value:initialStop},{time:firstTrailTime,value:initialStop}]);
    }

    // Trailing stop evolution (solid purple segments)
    if(trailInRange.length>=2){
      const trailData=trailInRange.map(s=>({time:timeMap[parseInt(s.bar)],value:s.stop}));
      const lastPt=trailData[trailData.length-1];
      if(lastPt.time<t1) trailData.push({time:t1,value:lastPt.value});
      const trailS=tvPrice.addLineSeries({
        color:'rgba(167,139,250,.75)',lineWidth:1.5,
        priceLineVisible:false,lastValueVisible:false
      });
      trailS.setData(trailData);
    }
  });
  tvPrice.timeScale().fitContent();
}
```

**Points clés à transposer en React/TypeScript** :
- Utiliser `useRef` pour stocker l'instance `LightweightCharts.createChart` (comme `smart-replay-view.tsx:11`).
- Les markers doivent être **triés chronologiquement** avant `setMarkers` (exigence LightweightCharts).
- Les stop lines sont des `LineSeries` séparées (pas des `PriceLine`) pour pouvoir les étendre sur une plage temporelle.
- `stop_trail` est un array `{bar, stop}[]` — vérifier qu'il est bien sérialisé par le backend.

### A.2 — `buildTrades()` — backtest.html:770-981

Fonction clé pour **BT-002** (tableau des trades + lignes expandables + stats agrégées). Trop longue pour être reproduite intégralement ici — voir `jinja2-reference/backtest.html:770-981`.

**Points clés** :
- Filtres via variable globale `tradeFilter` ('all' | 'long' | 'short' | 'win' | 'loss').
- Tri via `sortKey` + `sortAsc`, fonction de comparaison gérant strings et numbers.
- Pagination `PG=20`, calcul `pages = Math.ceil(fil.length/PG)`.
- Colonne `Setup` conditionnelle (`hasSetup = all.some(t => t.setup)`).
- Badges exit_reason via `exitReasonBadge()` (cf. A.2.b).
- Ligne expandable via `detail(t)` (cf. A.2.c).
- Stats agrégées calculées dans la même fonction : `bySetup`, `byExit`, `wrLong`, `wrShort`, `avgWin`, `avgLoss`.

### A.2.b — `exitReasonBadge()` — backtest.html:946-962

```javascript
function exitReasonBadge(r){
  const m={
    take_profit:['🎯','TP','#34d399'],
    stop_loss:['🛑','SL','#fb7185'],
    trailing_stop:['⤵','TS','#a78bfa'],
    exit_after_bars:['⏱','T','#fbbf24'],
    end_of_data:['⏹','FIN','#94a3b8'],
    p_dir_inversion:['🔄','INV','#f59e0b'],
    regime_exit_TD:['🚪','TD','#06b6d4'],
    manual_close:['✋','MAN','#94a3b8']
  };
  const e=m[r]||['',r||'','var(--muted)'];
  return `<span class="xbadge" style="color:${e[2]};border-color:${e[2]}33;background:${e[2]}15">${e[0]} ${e[1]}</span>`;
}
```

### A.2.c — `detail(t)` — backtest.html:965-981

Ligne expandable en 2 colonnes (signal | sortie & sizing). Voir le fichier pour le HTML exact. À transposer en JSX avec les types `BacktestTrade`.

### A.3 — `diagHTML()` — backtest.html:582-597

Panneau Diagnostics pour **BT-003**. Voir `jinja2-reference/backtest.html:582-597` pour les 9 KPIs + 3 warnings + tableau per-strategy.

### A.4 — Sync serveur + persistance session — backtest.html:277-335

```javascript
// Sync serveur toutes les 5s
function _syncBacktestRunState(){
  fetch('/api/backtest/status').then(r=>r.json()).then(s=>{
    if(s.running){
      btStartBtn.disabled=true;
      msgEl.innerHTML='⏳ Un backtest est déjà en cours côté serveur. <a href="#" onclick="cancelBacktest();return false">Annuler</a>';
    }else{
      btStartBtn.disabled=false;
    }
  });
}
setInterval(_syncBacktestRunState, 5000); _syncBacktestRunState();

// Persistance session
function saveBtSession(){
  if(!btData) return;
  try{
    sessionStorage.setItem('bt_data', JSON.stringify({
      result: btData,
      config: { symbol: iSym.value, timeframe: iTf.value, limit: iLim.value,
                strategies: [...stratChecks].filter(c=>c.checked).map(c=>c.value),
                walk_forward: iWf.checked, monte_carlo: iMc.checked },
      timestamp: Date.now()
    }));
  }catch(_){}
}
function loadBtSession(){
  try{
    const raw=sessionStorage.getItem('bt_data'); if(!raw) return;
    const s=JSON.parse(raw); if(!s||!s.result) return;
    if(Date.now()-s.timestamp > 30*60*1000){ sessionStorage.removeItem('bt_data'); return; }
    // Restore config + result
    btData=s.result;
    // ... restaurer les inputs ...
    toast(`↩ Résultat restauré · ${btData.ohlcv?.close?.length||0} bougies · ${Object.keys(btData.by_strategy||{}).length} strat.`);
  }catch(_){}
}
```

### A.5 — Barre de progression + ETA + log — backtest.html:373-398

```javascript
// Steps simulés calendaires
const dynSteps = btData?.strategies?.map(s=>`↳ ${s} : backtest IS/OOS en cours…`) || [];
const steps = [
  'Connexion OKX & initialisation…',
  'Téléchargement des données OHLCV…',
  'Données reçues — calcul des indicateurs…',
  ...btData?.strategies?.map(s=>`Exécution ${s}…`) || [],
  'Calcul des métriques & statistiques…',
  'Finalisation des résultats de base…',
  ...dynSteps,
  btData?.walk_forward ? 'Walk-Forward : découpage des folds…' : null,
  btData?.monte_carlo ? 'Monte-Carlo : simulation des scénarios…' : null,
  'Assemblage final & sérialisation JSON…'
].filter(Boolean);

let stepIdx=0;
const progInterval=setInterval(()=>{
  // Met à jour la barre de progression
  const elapsed=(Date.now()-btStartTs)/1000;
  const progress=Math.min(95, (stepIdx+1)/steps.length*100);
  document.getElementById('loader-bar-fill').style.width=`${progress}%`;
  // ETA
  if(elapsed>5 && stepIdx>0){
    const eta=(100-progress)/ (progress/elapsed);
    document.getElementById('loader-eta').textContent=
      eta>60 ? `~${Math.ceil(eta/60)}min restant` : `~${Math.ceil(eta)}s restant`;
  }
  // Pousse le log step
  if(stepIdx<steps.length){
    addLog('run', steps[stepIdx]);
    stepIdx++;
  }
}, 400);
```

### A.6 — KPIs grid 9 métriques — backtest.html:539-549

Voir `jinja2-reference/backtest.html:539-549`. 9 KPIs : PnL Net (+%), Win Rate (+n), Sharpe (⚠ si <30 trades), Max DD, Expectancy, PF, Equity Finale, Buy & Hold (+%), Alpha.

### A.7 — `buildCmp()` + tab bar — backtest.html:458-466, 757-768

```javascript
// Tab bar par stratégie (couleurs persistées)
const PAL=['#06b6d4','#a855f7','#f59e0b','#ec4899','#10b981','#3b82f6'];
function SC(n){ const i=Object.keys(btData.by_strategy).indexOf(n); return PAL[i%PAL.length]; }
// ... rendu des tabs ...

// Tableau comparatif si >1 stratégie
function buildCmp(){
  const strats=Object.keys(btData.by_strategy||{});if(strats.length<2)return;
  // 10 métriques : Trades, Win Rate, PnL net, Max DD, Sharpe, Expectancy, PF, Avg Win, Avg Loss, Alpha
  // Best value par colonne surlignée + marqueur ✦
}
```

### A.8 — Stats agrégées — backtest.html:861-926

Voir `jinja2-reference/backtest.html:861-926` pour les 3 tableaux (chips Long/Short/WR/Avg Win/Avg Loss, par setup, par raison de sortie).

### A.9 — `exportCSV()` — backtest.html:1048-1075

```javascript
function exportCSV(){
  if(!btData||!activeStrat)return;
  const sd=btData.by_strategy[activeStrat];
  const trades=(sd.trades||[]).filter(t=>t.status!=='open');
  const headers=['id','side','setup','entry_time','exit_time','entry','exit',
    'duration_bars','exit_reason','score','pnl','pnl_pct','fees',
    'sl_atr_mult','tp_atr_mult','exit_after_bars','size_factor',
    'regime_lbl','bearish_excess'];
  const rows=trades.map(t=>headers.map(h=>{
    const v=t[h]; return v==null?'':typeof v==='string'?`"${v.replace(/"/g,'""')}"`:v;
  }).join(','));
  const csv='\ufeff'+headers.join(',')+'\n'+rows.join('\n'); // BOM UTF-8
  const blob=new Blob([csv],{type:'text/csv;charset=utf-8'});
  const a=document.createElement('a');
  a.href=URL.createObjectURL(blob);
  a.download=`trades_${btData.symbol}_${btData.timeframe}_${activeStrat}_${tsStr()}.csv`;
  a.click();
}
```

### A.10 — `mlHTML()` — backtest.html:575-580

Voir `jinja2-reference/backtest.html:575-580` pour le panneau ML (4 KPIs + warning 0 trades + note réentraînement).

### A.11 — `STRAT_THRESHOLD` + warnings — backtest.html:223, 535-537

```javascript
const STRAT_THRESHOLD={
  fear_momentum:0.72, pullback_trend:0.65, /* ... */
};
// Threshold warning
if(STRAT_THRESHOLD[activeStrat] && currentScoreThreshold < STRAT_THRESHOLD[activeStrat]){
  msgEl.innerHTML+=`<div class="warn info">⚠ score_threshold actuel (${currentScoreThreshold}) < seuil recommandé (${STRAT_THRESHOLD[activeStrat]}) pour ${activeStrat}. <a href="/config#strategies">Ajuster dans Config →</a></div>`;
}
// Sample size warning
if(nTrades<30){
  msgEl.innerHTML+=`<div class="warn">⚠ ${nTrades} trades — échantillon trop petit pour des conclusions statistiques fiables.</div>`;
}
```

### A.12 — `updateLimHint()` — backtest.html:196-206

```javascript
function updateLimHint(){
  const lim=parseInt(iLim.value)||0;
  const tf=iTf.value;
  const tfMin={'1m':1,'5m':5,'15m':15,'30m':30,'1h':60,'4h':240,'1d':1440}[tf]||60;
  const hours=lim*tfMin/60;
  const days=hours/24;
  let hint;
  if(days<1) hint=`≈ ${Math.round(hours)}h de données`;
  else if(days<30) hint=`≈ ${Math.round(days)}j de données`;
  else if(days<90) hint=`≈ ${Math.round(days)}j de données`;
  else if(days<365) hint=`≈ ${Math.round(days)}j (~${Math.round(days/30)} mois) de données`;
  else hint=`≈ ${Math.round(days)}j (~${(days/365).toFixed(1)} an) de données`;
  limHintEl.textContent=hint;
  limHintEl.style.color = days<30 ? 'var(--amber)' : 'var(--green)';
}
```

### A.13 — Before/After grid — optimizer.html:648-671

Voir `jinja2-reference/optimizer.html:648-671`. Grid 2 colonnes avec 6 métriques (Trades, PnL, Sharpe, WR, DD, Alpha) + source de la baseline.

### A.14 — Best params + Top-5 — optimizer.html:673-698

Voir `jinja2-reference/optimizer.html:673-698`. Best params en mono font coloré + tableau top-5 avec ligne #1 surlignée 🏆.

### A.15 — Warnings overfit — optimizer.html:712-723

```javascript
// Warning overfit
if(result.overfit && result.overfit > 2){
  html+=`<div class="warn">⚠ Overfit détecté (${result.overfit.toFixed(2)}) — la performance IS est largement supérieure à l'OOS. Les params optimisés risquent de ne pas se généraliser.</div>`;
}
// Warning trades OOS < 3
if(result.oos_trades < 3){
  html+=`<div class="warn">⚠ Seulement ${result.oos_trades} trades sur la période OOS — échantillon insuffisant pour valider l'edge.</div>`;
}
// Warning score OOS < -0.05
if(result.oos_score < -0.05){
  html+=`<div class="warn critical">🚫 Score OOS < -0.05 — stratégie <strong>exclue du live trading</strong> même si vous appliquez les params.</div>`;
}
```

### A.16 — Groupes par statut + toggle — optimizer.html:564-596

Voir `jinja2-reference/optimizer.html:564-596`. 4 sections (En cours/Erreurs/Annulés/Terminés) + bouton « Tout ouvrir/Réduire tout ».

### A.17 — Moteur replay complet — replay.html:542-689

Trop long pour être reproduit ici — voir `jinja2-reference/replay.html:542-689` pour les fonctions `tick()`, `step(n)`, `seekTo(pos)`, `setSpeed(s)`.

**Points clés** :
- `tick()` utilise `series.update(candle)` (incrémental, pas de reset).
- `step(n)` négatif = reconstruction complète depuis 0 (LightweightCharts ne supporte pas `undo`).
- `seekTo(pos)` = reconstruction complète (bougies + markers + stats).
- `setSpeed(s)` préserve l'état play/pause.
- Mode MAX : batch 100 bougies par frame via `requestAnimationFrame`.
- Markers entrée/sortie triés chronologiquement avant `setMarkers`.

### A.18 — Optimizer ML — ml.html:113-167, 587-736

Voir `jinja2-reference/ml.html`. Quasi identique à `optimizer.html` mais avec :
- Filtre `is_ml=true` sur les stratégies.
- Checkbox `opt-ml-tune` (ml_tune_hp).
- Note apply « modèle ML sauvegardé automatiquement ».
- Couleurs cyan.

---

## Annexe B — Index des composants React à créer/modifier

### B.1 Composants à créer (29 nouveaux)

#### Charts (5)
| Composant | Spécifications | Fichier |
|---|---|---|
| `<PriceSignalsChart>` | BT-001 | `frontend/src/components/charts/price-signals-chart.tsx` |
| `<ReplayCandlestickChart>` | RPL-001 | `frontend/src/components/charts/replay-candlestick-chart.tsx` |

#### Tables (4)
| Composant | Spécifications | Fichier |
|---|---|---|
| `<TradesTable>` | BT-002 | `frontend/src/components/tables/trades-table.tsx` |
| `<TradesTableRowDetail>` | BT-002 | `frontend/src/components/tables/trades-table-row-detail.tsx` |
| `<TopTrialsTable>` | OPT-002 | `frontend/src/components/tables/top-trials-table.tsx` |
| `<StrategyComparisonTable>` | BT-007 | `frontend/src/components/cards/strategy-comparison-table.tsx` |

#### Cards / Panels (12)
| Composant | Spécifications | Fichier |
|---|---|---|
| `<DiagnosticsPanel>` | BT-003 | `frontend/src/components/cards/diagnostics-panel.tsx` |
| `<BacktestProgress>` | BT-005 | `frontend/src/components/cards/backtest-progress.tsx` |
| `<MLBacktestPanel>` | BT-010 | `frontend/src/components/cards/ml-backtest-panel.tsx` |
| `<TradesStatsPanel>` | BT-008 | `frontend/src/components/cards/trades-stats-panel.tsx` |
| `<BeforeAfterGrid>` | OPT-001 | `frontend/src/components/cards/before-after-grid.tsx` |
| `<BestParamsBlock>` | OPT-002 | `frontend/src/components/cards/best-params-block.tsx` |
| `<OptimizerWarnings>` | OPT-003 | `frontend/src/components/cards/optimizer-warnings.tsx` |
| `<BacktestRunningBanner>` | BT-004 | `frontend/src/components/cards/backtest-running-banner.tsx` |
| `<ReplaySignalLog>` | RPL-003 | `frontend/src/components/cards/replay-signal-log.tsx` |
| `<ReplayStatsPanel>` | RPL-004 | `frontend/src/components/cards/replay-stats-panel.tsx` |
| `<TrainRecipeDialog>` | ML-003 | `frontend/src/components/cards/train-recipe-dialog.tsx` |

#### Views (2)
| Composant | Spécifications | Fichier |
|---|---|---|
| `<MLOptimizerView>` | ML-001 | `frontend/src/components/views/ml-optimizer-view.tsx` |
| `<ReplayView>` (nouveau) | RPL-001 | `frontend/src/components/views/replay-view.tsx` (remplace l'actuel renommé `multi-tf-batch-view.tsx`) |

#### Controls (1)
| Composant | Spécifications | Fichier |
|---|---|---|
| `<PlaybackControls>` | RPL-001 | `frontend/src/components/controls/playback-controls.tsx` |

#### Hooks (5)
| Hook | Spécifications | Fichier |
|---|---|---|
| `useBacktestStatus` | BT-004 | `frontend/src/hooks/use-backtest-status.ts` |
| `useBacktestSession` | BT-004 | `frontend/src/hooks/use-backtest-session.ts` |
| `useReplayEngine` | RPL-001 | `frontend/src/hooks/use-replay-engine.ts` |
| `useReplayKeyboard` | RPL-005 | `frontend/src/hooks/use-replay-keyboard.ts` |
| `useTrainRecipe` | ML-003 | `frontend/src/hooks/use-train-recipe.ts` |

#### Libs (5)
| Module | Spécifications | Fichier |
|---|---|---|
| `trades-csv` | BT-009 | `frontend/src/lib/trades-csv.ts` |
| `limit-hint` | BT-012, OPT-005, RPL-006, CMP-001 | `frontend/src/lib/limit-hint.ts` |
| `is-oos-hint` | OPT-005 | `frontend/src/lib/is-oos-hint.ts` |
| `strat-thresholds` | BT-011 | `frontend/src/lib/strat-thresholds.ts` |
| `exit-reason-badges` | BT-002, BT-017 | `frontend/src/lib/exit-reason-badges.ts` |

### B.2 Composants à modifier (12 existants)

| Composant | Spécifications | Fichier |
|---|---|---|
| `lab/page.tsx` (BacktestTab + BacktestResults) | BT-001, BT-002, BT-003, BT-004, BT-005, BT-006, BT-007, BT-008, BT-009, BT-010, BT-011, BT-012, BT-013, BT-014 | `frontend/src/app/lab/page.tsx` |
| `optimizer-view.tsx` | OPT-001 à OPT-013 | `frontend/src/components/views/optimizer-view.tsx` |
| `compare-view.tsx` | CMP-001 à CMP-005 | `frontend/src/components/views/compare-view.tsx` |
| `ml-view.tsx` | ML-001 (info card à retirer) | `frontend/src/components/views/ml-view.tsx` |
| `ml-recipes-list.tsx` | ML-003 (bouton Entraîner → dialog) | `frontend/src/components/cards/ml-recipes-list.tsx` |
| `ChartFullscreen` | BT-001 (accepter children render-prop) | `frontend/src/components/ui/chart-fullscreen.tsx` |
| `CsvExportButton` / nouveau `TradesCsvExportButton` | BT-009 | `frontend/src/components/ui/export-buttons.tsx` |
| `types/index.ts` | BT-003, BT-010, OPT-001, OPT-002, OPT-003 (nouveaux types) | `frontend/src/types/index.ts` |
| `lib/api.ts` | vérifier endpoints | `frontend/src/lib/api.ts` |
| `lib/schemas.ts` | étendre Zod schemas | `frontend/src/lib/schemas.ts` |
| `TimeframeButtons` | pas de modif (réutilisé) | `frontend/src/components/ui/timeframe-select.tsx` |

### B.3 Vérifications backend à faire en amont (cf. §8.5)

Avant de coder le frontend, auditer ces endpoints backend et ajouter les champs manquants si nécessaire :

| Endpoint | Action backend | Spécs dépendantes |
|---|---|---|
| `POST /api/backtest` | Vérifier présence de `diagnostics`, `ml_info`, `trades[].stop_trail`, `trades[].conditions`, `trades[].indicators`, `trades[].signal_reason`, `expectancy`, `buy_hold_return`, `alpha`, `equity_final` | BT-001, BT-002, BT-003, BT-006, BT-010 |
| `GET /api/backtest/status` | Endpoint existe ? Si non, créer. | BT-004 |
| `GET /api/optimize/status` | Vérifier `result.baseline`, `result.after`, `result.top_trials`, `result.best_params`, `result.overfit`, `result.oos_score`, `result.oos_trades` | OPT-001, OPT-002, OPT-003 |
| `POST /api/optimize/start` | Vérifier acceptation de `early_stopping`, `limit_per_tf`, `ml_tune_hp` | OPT-004, ML-002 |
| `GET /api/optimize/spaces` | Vérifier `recommended_tfs` par stratégie | OPT-011 |
| `POST /api/ml/train` + `GET /api/ml/train/stream` | Endpoints existent ? | ML-003 |
| `POST /api/optimize/apply` (avec `is_ml=true`) | Déclenche l'entraînement du modèle ? | ML-004 |

---

## Document terminé

**Récapitulatif** :
- **52 spécifications** détaillées (18 Backtest, 13 Optimizer, 9 Replay, 7 ML, 5 Compare).
- **~139 SP** d'effort estimé sur **6 sprints × 2 semaines**.
- **29 nouveaux composants React** à créer, **12 composants existants** à modifier.
- **7 vérifications backend** à faire en amont.
- **Plan de migration** par feature flags + coexistence temporaire + rollback atomique par spec.
- **6 templates Jinja2** restaurés en fichiers compagnons (`jinja2-reference/`) pour référence directe du code d'origine.

**Fichiers livrés** :
- `SPECIFICATIONS_RATTRAPAGE_LAB_NEXTJS.md` — ce document (~2 500 lignes).
- `jinja2-reference/backtest.html` — 1 091 lignes.
- `jinja2-reference/optimizer.html` — 790 lignes.
- `jinja2-reference/replay.html` — 814 lignes.
- `jinja2-reference/ml.html` — 790 lignes.
- `jinja2-reference/compare.html` — 206 lignes.
- `jinja2-reference/base.html` — 401 lignes.

**Prochaine étape recommandée** : démarrer par les quick wins (BT-006, BT-013, BT-014, CMP-001, CMP-002 — 1 SP chacun) pour valider le workflow de migration avant d'attaquer les specs CRITIQUE. Puis enchaîner sur le Sprint 1 Backtest (BT-001, BT-002, BT-003, BT-004, BT-005) qui porte 80% de la valeur perçue.

---

*Document généré le 07/08/2026 — audit produit/frontend du repo `montreuild/bot-crypto`.*






