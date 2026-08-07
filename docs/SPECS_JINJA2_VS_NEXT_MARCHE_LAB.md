# Specs détaillées — pages Jinja2 décommissionnées vs UI Next.js

> **Périmètre** : Optimisateur, Backtests, Scanner, Smart Graph, Smart Replay, Dérivés  
> **Sources** : templates `ecc87b2^:app/web/templates/*` (commit de suppression `ecc87b2`, 29/07/2026), `docs/FIN_JINJA2.md`, `docs/audit-ui-ux-bot-crypto.md` §3, code `frontend/src/app/{lab,market}/` + `components/views/*`  
> **Date** : 2026-08-07 (§3.1, §4.1, §4.2, §7 et §8ter révisés après `9483dde` et `d203bb4`)  
>
> ⚠ **Fraîcheur inégale.** Les sections portant sur `/lab` ont été vérifiées
> contre le code au 07/08/2026. Celles portant sur `/market` (Scanner, Smart
> Graph, Smart Replay, Dérivés) datent du 05/08 et n'ont pas été revérifiées
> depuis — les sprints de rattrapage n'ont touché que le Laboratoire.

---

## 1. Contexte de décommissionnement

| Élément | Détail |
|--------|--------|
| Décision | D4 — Plan directeur ; acte `docs/FIN_JINJA2.md` |
| Suppression | Commit `ecc87b2` (S6-09) — ~10 600 lignes, 19 templates |
| Remplacement | Next.js 15 / React 19 — unique frontend |
| Routage legacy | **308 permanents** vers le frontend Next |

### Mapping d’URL (strangler fig)

| Ancienne route Jinja2 | Redirect 308 | Page Next actuelle |
|----------------------|--------------|-------------------|
| `/optimizer` | `/lab?tab=optimizer` | Laboratoire → Optimiseur |
| `/backtest` | `/lab?tab=backtest` | Laboratoire → Backtest |
| `/scanner` | `/market?tab=scanner` | Marché → Scanner |
| `/smartgraph` | `/market?tab=smartgraph` | Marché → Smart Graph |
| `/smartreplay` | `/market?tab=smartreplay` | Marché → Smart Replay |
| `/derivatives` | `/market?tab=derivatives` | Marché → Dérivés |

**Organisation produit actuelle**  
- **Laboratoire** (`/lab`) : Backtest · Optimizer · ML · Replay (stratégie) · Compare  
- **Marché** (`/market`) : Scanner · Smart Graph · Smart Replay · Dérivés  

---

## 2. Spécifications Jinja2 (état au décommissionnement)

### 2.1 Optimisateur — `optimizer.html` (~790 L)

**Rôle** : optimisation des paramètres des stratégies **non-ML** (grid / random / bayesian).

#### Layout
- Colonne formulaire (stratégies, paire, méthode, trials, workers, early-stop, TFs)
- Zone jobs (cartes en cours / terminés)
- Preview matrice TF × stratégies
- Vue espace de paramètres

#### Contrôles
| Contrôle | Détail |
|----------|--------|
| Stratégies | Checkboxes (filtre `!is_ml`, `!ml_*`) |
| Paire | Texte, défaut `BTC/USDC` |
| Méthode | grid / random / bayesian |
| Trials | 10–200, défaut 40 |
| Workers | 1 / 2 / 4 / -1 |
| Early stopping | 0 = off |
| Param Search Optim | Gel des params à faible impact |
| Timeframes | Checkboxes multi-TF (shared `ml-optimizer-shared.js`) |
| Limit bougies | 0 = auto, max 8000, hint jours/mois |
| Auto-apply | Appliquer meilleurs params à la fin |
| Actions job | Lancer / cancel / apply / delete |

#### API
- `GET /api/optimize/spaces`
- `POST /api/optimize/start` (implicite via startOpt)
- Status / apply / cancel / delete jobs
- `GET /api/config` (stratégies)

#### Jobs cards
- Progress bar, métriques, avant/après, top-5 params, warnings

---

### 2.2 Backtests — `backtest.html` (~1091 L)

**Rôle** : backtest multi-stratégies avec validation WF/MC et graphiques riches.

#### Layout
- **Sidebar sticky** (`bt-sidebar`) : paramètres + stratégies + validation
- Zone résultats dynamique (tabs par stratégie)
- **Modal fullscreen** chart prix + reset zoom

#### Contrôles
| Contrôle | Détail |
|----------|--------|
| Paire | Texte |
| Timeframe | Select (TFs config) |
| Nombre de bougies | 100–50000, presets 500 / 2k / 5k / 8k |
| Stratégies | Checkboxes multi-sélection |
| Walk-Forward | Checkbox |
| Monte-Carlo | Checkbox |
| Lancer / Annuler | Boutons |
| Export | JSON + PDF |
| Type chart | Toggle line / candles |

#### Graphiques (lightweight-charts + Chart.js)
1. Equity + Buy&Hold (dashed)  
2. Distribution PnL (histogramme)  
3. Cumul trades (ligne) — **présent Jinja2**  
4. Prix OHLCV + markers entry/exit  
5. Scatter Chart.js (prix × temps)  
6. Fullscreen prix  

#### API
- `POST /api/backtest`
- Cancel backtest si exposé

#### Sorties
- Metrics par stratégie (trades, WR, PnL, Sharpe, DD, PF…)
- Folds WF si activé
- IC MC P5/P50/P95 si activé

---

### 2.3 Scanner — `scanner.html` (~1426 L)

**Rôle** : vue marché multi-symboles + chart multi-panneaux + SMC + Fast Analyse.

#### Layout
- Filtre sticky (régime, ADX, ATR, RSI lo/hi) + reset + persistance localStorage  
- Table multi-symboles triable  
- Chart **4 panneaux** : prix (+ EMA/BB/SR/SMC) · volume · RSI · MACD  
- Panneaux : config, opportunités, Fast Analyse, prédictions  

#### Contrôles
| Contrôle | Détail |
|----------|--------|
| TF scan | Select + saveFilters |
| Limit barres | Input |
| Filtres régime/ADX/ATR/RSI | Inputs numériques |
| Toggles chart | EMA 20/50/100/150/200, BB, Sup/Rés, calques SMC |
| Setups V8/V11/V12 | Markers entry + TP/SL |
| Fast Analyse | Screening indicateurs, split IS/OOS, sensibilité frais |
| Opportunités | Top paires par score |
| Prédictions | Panel par stratégie |
| Lien croisé | → Backtest / analyser paire |

#### API
- `GET /api/scanner/chart`
- `GET /api/scanner/smc`
- `GET /api/scanner/setup_series`
- `GET /api/scanner/fast_analysis` (ou équivalent)
- `GET /api/scanner/signals`

---

### 2.4 Smart Graph — `smartgraph.html` (~546 L)

**Rôle** : lecture SMC/ICT dédiée (chart analyste).

#### Layout
- Barre : symbole, TF (15m/30m/1h/**2h**/4h/1d), limit (300–3000, défaut 600), Analyser  
- Checkboxes calques avec **accent-color** par type  
- Chart **height: 620px** + primitive `SmcChart.ZonesPrimitive` (rectangles remplis)  
- Table plans recommandés  
- Panneaux lecture / signal / zones  

#### Calques Jinja2 (11)
1. Structure (zigzag + BOS/CHoCH)  
2. Offre/Demande (OB)  
3. Breakers  
4. Liquidité (EQH/EQL)  
5. FVG  
6. Liquidity voids  
7. Rejection blocks  
8. Volume profile (POC/HVN/LVN)  
9. Trendlines + canal  
10. Cycle (projection)  
11. Signal (entrée/SL/TP)  

#### Plans
Colonnes : Statut, Sens, Setup, Entry, SL, TP, Gain, RR, Dist, Score (+ raison au clic)

#### API
- `GET /api/scanner/smc`
- (optionnel) `GET /api/scanner/chart`

#### Lib technique
- `static/js/smc-chart.js` — `ZonesPrimitive` remplit les zones [t1,t2]×[bottom,top]

---

### 2.5 Smart Replay — `smartreplay.html` (~477 L)

**Rôle** : rejeu **causal** bougie par bougie des entités SMC + trades backtest smart_money.

#### Layout
- Symbole, TF (15m/30m/1h/4h/1d), limit (400–2000, défaut 1200)  
- Toggles calques  
- Transport : ⏮ ≪ ◀ ▶ ≫ ⏭ + vitesse + **slider**  
- Chart **height: 560px** + ZonesPrimitive  
- Panneaux : lecture, performance replay, journal  

#### Calques
Structure, OB, Liquidité, FVG, Voids, Breakers, Rejets, Trendlines, **Trades**

#### Raccourcis
- Espace = play/pause  
- ← → = ±1 barre  

#### API
- `GET /api/scanner/smc_replay`  
  Payload : candles + lifecycle indices (`created_at`, `invalidated_at`…) + trades Backtester

---

### 2.6 Dérivés — `derivatives.html` (~202 L)

**Rôle** : 4 séries dérivées + overlay prix.

#### Layout
- Symbole, période (15m/1h/4h/1d), limit points  
- Charger / Rafraîchir réseau  
- Stat chips  
- Grille 2×2 charts (height 280px)  

#### Séries
| Chart | Métrique | Couleur type |
|-------|----------|--------------|
| Funding | `funding_rate` | cyan |
| Open Interest | `open_interest` | violet |
| Long/Short | `long_short_ratio` | ambre |
| Taker buy/sell | `taker` | — |

Chaque chart : série métrique + **overlay prix** semi-transparent.

#### API
- `GET /api/derivatives/data?symbol=&period=&limit=&refresh=`

---

## 3. Spécifications UI Next actuelle

### 3.1 Laboratoire — `/lab`

**6 onglets** (`?tab=`) :

| Onglet | `tab=` | Composant | Rôle |
|--------|--------|-----------|------|
| Backtest | `backtest` | `lab/page.tsx` (inline) | Formulaire + résultats + charts + WF/MC |
| Optimizer | `optimizer` | `optimizer-view.tsx` | Optimisation bayesian/grid/random |
| ML | `ml` | `ml-view.tsx` | `OptimizerView` avec `filterMl` + entraînement de recettes |
| Replay | `replay` | `replay-view.tsx` | Replay **interactif** bougie-par-bougie (≠ Smart Replay SMC) |
| Multi-TF | `batch` | `multi-tf-batch-view.tsx` | Replay batch multi-TF (ancien `replay-view`) |
| Compare | `compare` | `compare-view.tsx` | Comparatif multi-stratégies |

> Le 6ᵉ onglet est né du découpage de `d203bb4` : `?tab=replay` est devenu le
> replay interactif, et le replay batch multi-TF qui l'occupait a été déplacé
> tel quel sous `?tab=batch`. Aucune capacité perdue.

#### Backtest Next (état actuel)
- Config : symbole, TF, limit (+ presets 500/2k/5k/8k et hint durée), stratégies, options expert  
- Validation regex du symbole, badge « ● actif » sur les stratégies activées  
- Charts : equity, scatter, OHLCV markers (`BacktestEquityChart`, `TradesScatter`)  
- **Prix + signaux** : `PriceSignalsChart` (candlestick, markers ▲▼●, lignes de stop)  
- **Trades** : `TradesTable` (triable, paginée, filtrable, dépliable) + `TradesStatsPanel`  
- **Diagnostics** : `DiagnosticsPanel` (9 KPIs, warnings, détail par stratégie)  
- **Comparatif** : `StrategyComparisonTable`  
- **ML** : `MLBacktestPanel` (AUC, `n_features`, lookahead, `proba_up`)  
- **Walk-Forward** : `WalkForwardTable`  
- **Monte-Carlo** : `MonteCarloPanel`  
- Progression : `BacktestProgress` (barre + ETA) et `BacktestRunningBanner`  
- Reprise de session : `useBacktestStatus` (poll serveur) + `useBacktestSession` (`sessionStorage`, TTL 30 min)  
- Study vs Live : `StudyVsLiveCard`  
- Cost model : `CostModelCard`  
- Export CSV (19 colonnes, BOM UTF-8) / JSON / PDF (impression navigateur)  
- Fullscreen chart (`ChartFullscreen`)  
- Bouton « Créer le bot (Essai) » (pipeline vers lifecycle)

#### Optimizer Next
- Méthodes grid/random/bayesian  
- Multi-symboles / multi-TFs, `early_stopping`, `limit_per_tf`, `ml_tune_hp`  
- SSE stream progression + ETA  
- **Avant/après** : `BeforeAfterGrid` (6 métriques, delta coloré)  
- **Top-5** : `TopTrialsTable` + bloc des meilleurs paramètres  
- **Garde-fous** : `OptimizerWarnings` (overfit, trades insuffisants, score effondré)  
- Hint IS/OOS par TF coché, badges de compatibilité TF par stratégie  
- Jobs : groupés par statut, repliables, start / cancel / apply / delete  
- Cost model card  
- Séparation ML / non-ML par la prop `filterMl` — même composant, deux onglets

#### ML Next
- `OptimizerView` monté avec `filterMl` : ne liste que les stratégies `is_ml`  
- `TrainRecipeDialog` : entraînement d'une recette in-place (remplace le renvoi vers `/models`), polling `useMLTrainStatus`  
- Avertissement omnibus ≥ 2 200 bougies  
- Apply : applique les paramètres **et** entraîne le modèle (sauvegardé automatiquement)

#### Replay Next (interactif)
- `useReplayEngine` : position, play/pause, `step`, `seekTo`, 7 vitesses (0.5× → MAX)  
- `ReplayCandlestickChart` plein écran + markers entrée ▲▼ / sortie ●, filtrés par barre  
- `PlaybackControls` + barre de progression scrubbable  
- `ReplaySignalLog` (plus récent en tête) et `ReplayStatsPanel` (trades, WR, PnL accumulés)  
- `useReplayKeyboard` : Espace, ← → (Shift = ±10), Home/End, 1/2/5/0  
- Sélecteur de mois (1–24) converti en bougies via `monthsToBougies`  
- Données issues de `runBacktest` (OHLCV + trades avec `bar`/`exit_bar`) — le replay est **100 % côté client** après chargement

---

### 3.2 Marché — `/market`

| Onglet | Composant | Rôle |
|--------|-----------|------|
| Scanner | `scanner-view.tsx` + `OpportunitiesWidget` | Fast Analyse + prédictions + top opportunités |
| Smart Graph | `smart-graph-view.tsx` | Chart SMC full-width + plans cliquables |
| Smart Replay | `smart-replay-view.tsx` | Replay SMC + transport + bandeau meta |
| Dérivés | `derivatives-view.tsx` | 4 charts Recharts |

#### Scanner Next
- Fast Analyse (un symbole/TF)  
- Panel prédictions (`PredictionsPanel` + `/api/scanner/signals`)  
- Widget opportunités (sidebar page)  
- **Pas** de table multi-symboles full market scan  
- **Pas** de chart 4 panneaux (prix/vol/RSI/MACD)  
- Lien vers Laboratoire backtest  

#### Smart Graph Next
- Chart full-width, `autoSize`, hauteur `min(70vh,720px)`  
- 14 toggles overlays (OB, FVG, voids, breakers, rejections, pools, trendlines, channel, structure, swings, zigzag, prem/disc, VP, cycle)  
- Table plans recommandés **cliquable** → Entry (depuis signal_time) / SL / TP pleins  
- Bandeau meta sous les plans (Signal, Bias, PD, Cycle, VP, Channel)  
- Tables entités SMC  
- **Zones = line series** (bords + milieu), pas `ZonesPrimitive` remplie  

#### Smart Replay Next
- Chart full-width (même pattern hauteur)  
- Transport play/pause/speed/slider  
- Overlays filtrés par index de barre  
- Bandeau : Structure, trades ouverts, OBs, LPs, FVGs  
- Trades fermés table  
- **Pas** de raccourcis clavier Espace/←→ documentés  
- **Pas** de ZonesPrimitive remplie  

#### Dérivés Next
- 4 charts **Recharts** (pas lightweight-charts)  
- Symbole + période  
- Status store (`useDerivativesStatus`)  
- Downsample 400 pts  
- **Pas** d’overlay prix sur chaque chart (contrairement à Jinja2)  

---

## 4. Comparatif détaillé (Jinja2 ↔ Next)

### 4.1 Synthèse par page

| Page | Parité fonctionnelle | Commentaire |
|------|---------------------|-------------|
| **Optimizer** | **~95 %** | Avant/après, top-5, warnings, ETA, groupes repliables livrés (S2) ; reste layout 2 colonnes et `n_jobs` guidé |
| **Backtest** | **~95 %** | Trades table, diagnostics, prix+signaux, CSV, fullscreen, PDF livrés (S1) ; reste distribution PnL et cumul trades |
| **Scanner** | **~40 %** | Fast Analyse + prédictions + opportunités ; manque scan table + multi-panneaux |
| **Smart Graph** | **~75 %** | Calques + plans + clic plan OK ; zones non remplies ; layout modernisé |
| **Smart Replay** | **~70 %** | Replay causal OK ; layout modernisé ; shortcuts / zones remplies manquants |
| **Dérivés** | **~80 %** | 4 séries OK ; lib Recharts ; overlay prix manquant |

Légende : estimation qualitative sur le **périmètre métier** (pas le pixel-perfect).

> ⚠ Les lignes **Smart Graph**, **Smart Replay**, **Scanner** et **Dérivés**
> décrivent `/market` et n'ont **pas** été retouchées par les sprints de
> rattrapage du Laboratoire (`9483dde`, `d203bb4`), qui ne portent que sur
> `/lab`. Ne pas les lire comme un état vérifié à la date de ce document.

---

### 4.2 Matrice fonction × page

#### Optimisateur

| Fonction | Jinja2 | Next `/lab?tab=optimizer` |
|----------|:------:|:-------------------------:|
| Méthodes grid/random/bayesian | ✅ | ✅ |
| Multi-stratégies checkboxes | ✅ | ✅ |
| Multi-TF | ✅ | ✅ |
| Workers / early-stop | ✅ | ✅ `early_stopping` (OPT-004) |
| Param search optim (gel) | ✅ | ⚠️ à vérifier |
| Preview matrice TF×strat | ✅ | ✅ (préexistante ; héritée par l'onglet ML via ML-005) |
| Jobs cards progress + top5 | ✅ | ✅ `TopTrialsTable` (OPT-002) |
| Avant / après métriques | ⚠️ | ✅ `BeforeAfterGrid` (OPT-001) |
| Warnings overfit / trades / score | ❌ | ✅ `OptimizerWarnings` (OPT-003) |
| Hint IS/OOS par TF | ❌ | ✅ (OPT-005) |
| ETA sur progress bar | ❌ | ✅ (OPT-006) |
| Groupes par statut + repli | ❌ | ✅ (OPT-007) |
| Feedback post-lancement | ⚠️ | ✅ bougies/TF + combinaisons ignorées (OPT-008) |
| Badges compatibilité TF | ❌ | ✅ (OPT-011) |
| SSE live progress | ⚠️ / poll | ✅ EventSource |
| Apply / cancel / delete | ✅ | ✅ |
| Auto-apply en fin de run | ✅ | ⚠️ |
| Layout 2 colonnes (config sticky) | ✅ | ❌ (OPT-012, reporté S7) |
| `n_jobs` select guidé | ✅ | ❌ (OPT-013, reporté S7) |
| Cost model | ❌ | ✅ `CostModelCard` |
| Filtrage strict non-ML | ✅ | ✅ (prop `filterMl`, ML séparé) |

#### Backtest

| Fonction | Jinja2 | Next `/lab?tab=backtest` |
|----------|:------:|:------------------------:|
| Multi-stratégies | ✅ | ✅ |
| Limit bougies + hints durée | ✅ | ✅ + presets 500/2k/5k/8k (BT-012) |
| Walk-Forward folds | ✅ | ✅ `WalkForwardTable` |
| Monte-Carlo IC | ✅ | ✅ `MonteCarloPanel` |
| Equity + buy&hold | ✅ | ✅ |
| KPIs Expectancy / B&H / Alpha / Equity finale | ✅ | ✅ (BT-006) |
| Distrib PnL | ✅ | ⚠️ chips par setup et par raison de sortie (`TradesStatsPanel`), pas d'histogramme |
| Cumul trades | ✅ | ❌ |
| Prix + markers | ✅ | ✅ `PriceSignalsChart` + lignes de stop (BT-001) |
| Table des trades | ✅ | ✅ `TradesTable` 14 colonnes, triable/paginée/dépliable (BT-002) |
| Diagnostics d'exécution | ✅ | ✅ `DiagnosticsPanel` 9 KPIs + par stratégie (BT-003) |
| Comparatif par stratégie | ✅ | ✅ `StrategyComparisonTable` (BT-007) |
| Panel ML (AUC, features, `proba_up`) | ⚠️ | ✅ `MLBacktestPanel` (BT-010) |
| Warnings threshold / échantillon < 30 | ✅ | ✅ (BT-011) |
| Progression + ETA + log | ✅ | ✅ `BacktestProgress` (BT-005) |
| Reprise après reload / autre onglet | ❌ | ✅ `useBacktestStatus` + `useBacktestSession` (BT-004) |
| Scatter Chart.js | ✅ | ✅ Recharts |
| Fullscreen modal | ✅ | ✅ `ChartFullscreen` |
| Export JSON | ✅ | ✅ |
| Export CSV trades | ✅ | ✅ 19 colonnes, BOM UTF-8 (BT-009) |
| Export PDF | ✅ | ✅ via impression navigateur (sans dépendance jsPDF) |
| Study vs Live | ❌ | ✅ |
| Créer bot Essai | ❌ | ✅ |

#### Scanner

| Fonction | Jinja2 | Next `/market?tab=scanner` |
|----------|:------:|:--------------------------:|
| Table multi-symboles triable | ✅ | ❌ |
| Filtres régime/ADX/ATR/RSI + localStorage | ✅ | ❌ |
| Chart 4 panneaux (prix/vol/RSI/MACD) | ✅ | ❌ |
| Toggles EMA/BB/SR | ✅ | ❌ |
| Calques SMC sur chart scan | ✅ | ❌ (dans Smart Graph) |
| Setups V8/V11/V12 markers | ✅ | ❌ |
| Fast Analyse | ✅ | ✅ |
| Prédictions par stratégie | ✅ | ✅ |
| Top opportunités | ✅ | ✅ widget |
| Lien → backtest lab | ✅ | ✅ |

#### Smart Graph

| Fonction | Jinja2 | Next |
|----------|:------:|:----:|
| Candlestick | ✅ | ✅ |
| Zones remplies (`ZonesPrimitive`) | ✅ | ❌ (contours) |
| 11–14 calques toggleables | ✅ 11 | ✅ 14 |
| TF 2h | ✅ | ❌ (15m–1d sans 2h) |
| Chart hauteur fixe 620px | ✅ | ✅ responsive 70vh |
| Plans recommandés table | ✅ | ✅ |
| Clic plan → Entry/SL/TP | ⚠️ signal | ✅ depuis signal_time |
| Entry depuis bougie signal | ❌ plein chart | ✅ |
| SL/TP traits pleins | — | ✅ |
| Side panel → bandeau dessous | side | ✅ sous plans |
| Lecture narrative zones | ✅ | ⚠️ tables |

#### Smart Replay

| Fonction | Jinja2 | Next |
|----------|:------:|:----:|
| Payload `smc_replay` causal | ✅ | ✅ |
| Play/pause/speed/slider | ✅ | ✅ |
| Jump ±1 / ±10 / début/fin | ✅ | ✅ (step, start/end) |
| Raccourcis clavier | ✅ | ❌ |
| Zones remplies | ✅ | ❌ |
| Toggle Trades overlay | ✅ | ⚠️ via markers/trades |
| Chart 560px | ✅ | ✅ 70vh full width |
| Side panel → bandeau | side | ✅ sous transport |
| Stats performance replay | ✅ | ⚠️ trades table |
| Journal | ✅ | ⚠️ |

#### Dérivés

| Fonction | Jinja2 | Next |
|----------|:------:|:----:|
| Funding / OI / LSR / Taker | ✅ | ✅ |
| Overlay prix | ✅ | ❌ |
| lightweight-charts | ✅ | ❌ Recharts |
| Refresh réseau | ✅ | ✅ |
| Stat chips | ✅ | ⚠️ badges count |
| Grille 2×2 | ✅ | ✅ |

---

## 5. Stack graphique

| | Jinja2 | Next |
|--|--------|------|
| Candles / SMC | lightweight-charts **4.2.0** CDN + `SmcChart.ZonesPrimitive` | lightweight-charts **^4.2** npm, zones = line series |
| Dérivés | lightweight-charts dual scale (metric + price) | **Recharts** LineChart |
| Backtest scatter | Chart.js 4.4 | Recharts |
| Auth UI | Cookie HttpOnly via `_tpl()` | Proxy Next `/api/[...path]` injecte `X-API-Key` |
| Data fetching | `apiFetch` vanilla | TanStack Query + `api.ts` (timeouts) |

---

## 6. Gaps prioritaires (backlog produit)

Classés par impact utilisateur sur le périmètre demandé.

### P0 — Perte de capacité marché
1. **Scanner multi-symboles** (table + filtres + tri) — ✅ 2026-08-05  
2. **Chart scanner multi-panneaux** (prix/vol/RSI/MACD + EMA/BB) — ✅  
3. **Zones SMC remplies** (`ZonesPrimitive` TS) Smart Graph / Replay — ✅  

### P1 — Parité analyste
4. Raccourcis clavier Smart Replay — ✅  
5. Overlay prix sur charts dérivés — ✅  
6. Fullscreen + export PDF backtest — ✅  
7. Setups V8/V11/V12 markers sur scanner — **volontairement non traité**  

### P2 — Confort
8. TF 2h Smart Graph — **volontairement non traité**  
9. Jump ±10 barres Replay — ✅  
10. Preview matrice optimizer — ✅  

---

## 7. Architecture cible (déjà en place)

```
/lab?tab=backtest|optimizer|ml|replay|batch|compare
/market?tab=scanner|smartgraph|smartreplay|derivatives
```

- **308** depuis anciennes routes pour favoris / docs  
- Chargement **dynamic** des chunks chart (poids initial réduit)  
- Backend REST **inchangé** (mêmes endpoints) — l’écart est purement UI  

---

## 8. Références

| Document | Contenu |
|----------|---------|
| `docs/SPECIFICATIONS_RATTRAPAGE_LAB_NEXTJS.md` | **Spec d'origine du rattrapage du Lab** — 52 specs détaillées, critères d'acceptation, annexe A (extraits Jinja2 de référence), annexe B (index des composants) |
| `docs/FIN_JINJA2.md` | Acte de fin, raisons, checklist suppression |
| `docs/audit-ui-ux-bot-crypto.md` §3 | Inventaire 19 templates, graphs, 27 gaps |
| `docs/PLAN_DIRECTEUR_AMELIORATIONS.md` | Décision D4, vision 5 pages |
| `git show ecc87b2^:app/web/templates/<page>.html` | Source Jinja2 archivée |
| `frontend/src/app/lab/page.tsx` | Laboratoire |
| `frontend/src/app/market/page.tsx` | Marché |
| `frontend/src/components/views/*` | Vues portées |

---

## 8bis. Suivi d’implémentation (2026-08-05)

| # | Item | Statut |
|---|------|--------|
| 1 | Scanner multi-symboles + filtres + tri | ✅ `/market?tab=scanner` |
| 2 | Chart 4 panneaux (prix/vol/RSI/MACD + EMA/BB) | ✅ |
| 3 | Zones SMC remplies (`frontend/src/lib/smc-zones.ts`) | ✅ Graph + Replay |
| 4 | Raccourcis clavier Replay | ✅ Espace / ←→ / Shift±10 / Home-End |
| 5 | Overlay prix sur dérivés | ✅ dual Y-axis Recharts |
| 6 | Fullscreen + PDF backtest | ✅ modal + print-to-PDF |
| 7 | Markers setups V8/V11/V12 scanner | ⏭ hors scope demandé |
| 8 | TF 2h Smart Graph | ⏭ hors scope demandé |
| 9 | Jump ±10 Replay | ✅ boutons + clavier |
| 10 | Preview matrice optimizer | ✅ strat×TF×symboles |

---

## 8ter. Rattrapage du Laboratoire — registre des specs (2026-08-07)

> **Source.** La spécification d'origine est désormais versionnée :
> **`docs/SPECIFICATIONS_RATTRAPAGE_LAB_NEXTJS.md`** (52 specs, 2 838 lignes,
> annexes A et B incluses). Elle avait été perdue — référencée par les commits
> `9483dde` et `d203bb4` sans jamais être commitée — alors que les identifiants
> `BT-*`, `OPT-*`, `CMP-*`, `ML-*` et `RPL-*` sont cités dans les en-têtes de la
> quasi-totalité des fichiers livrés. Cette table reste utile comme **index
> inverse** : spec → fichier qui la porte, ce que la spec elle-même ne donne pas.
>
> ⚠ Deux erreurs de comptage subsistent dans le §8.3 de la spec d'origine : les
> sous-totaux annoncés (HIGH 14, MEDIUM 16, LOW 8) ne correspondent pas aux
> listes (15, 17, 6). Le total de 52 est juste.

**Livré dans `9483dde`** — quick wins + Sprints 1, 2, 5, 6 :

| ID | Objet | Porté par |
|----|-------|-----------|
| BT-001 | Chart prix + signaux (markers ▲▼●, lignes de stop) | `charts/price-signals-chart.tsx` |
| BT-002 | Table des trades triable/paginée/dépliable, 14 colonnes | `tables/trades-table.tsx` |
| BT-003 | Diagnostics : 9 KPIs, 3 warnings, détail par stratégie | `cards/diagnostics-panel.tsx` |
| BT-004 | Sync serveur + persistance de session | `hooks/use-backtest-session.ts` |
| BT-005 | Barre de progression + ETA + log horodaté | `cards/backtest-progress.tsx`, `cards/backtest-running-banner.tsx` |
| BT-006 | KPIs Expectancy / Buy&Hold / Alpha / Equity finale | `lab/page.tsx` |
| BT-007 | Comparatif des stratégies, 10 colonnes | `cards/strategy-comparison-table.tsx` |
| BT-008 | Stats de trades par setup et par raison de sortie | `cards/trades-stats-panel.tsx`, `lib/exit-reason-badges.ts` |
| BT-009 | Export CSV 19 colonnes, BOM UTF-8 | `lib/trades-csv.ts` |
| BT-010 | Panel ML du backtest (AUC, features, `proba_up`) | `cards/ml-backtest-panel.tsx` |
| BT-011 | Warnings seuil de score et échantillon < 30 trades | `lib/strat-thresholds.ts` |
| BT-012 | Hint limit ↔ durée + presets de bougies | `lib/limit-hint.ts` |
| BT-013 | Validation regex du symbole côté client | `lab/page.tsx` |
| BT-014 | Badge « ● actif » sur les stratégies activées | `lab/page.tsx` |
| BT-015 | Tabs par stratégie en couleur persistante | couvert par BT-007 (palette `STRAT_PALETTE`) |
| BT-016 | Toggle line/candles du chart prix | couvert par BT-001 (`localStorage` clé `bt.chartType`) |
| BT-017 | Badges `exit_reason` colorés | `lib/exit-reason-badges.ts` (BT-002 + BT-001) |
| BT-018 | Markers de setup abrégés (↑SIG, ↓TDH) | couvert par BT-001 |
| OPT-001 | Grille avant/après, 6 métriques, delta coloré | `cards/before-after-grid.tsx` |
| OPT-002 | Top-5 des trials + bloc des meilleurs params | `tables/top-trials-table.tsx` |
| OPT-003 | Warnings overfit / trades / score effondré | `cards/optimizer-warnings.tsx` |
| OPT-004 | Champs `early_stopping`, `limit_per_tf`, `ml_tune_hp` | `views/optimizer-view.tsx` |
| OPT-005 | Hint IS/OOS dynamique par TF coché | `lib/limit-hint.ts` |
| OPT-006 | ETA sur la progress bar | `views/optimizer-view.tsx` |
| OPT-007 | Groupes par statut + tout ouvrir / réduire | `views/optimizer-view.tsx` |
| OPT-008 | Feedback post-lancement (bougies/TF, combinaisons ignorées) | `views/optimizer-view.tsx` |
| OPT-009 | Symbole libre | encadré, non généralisé |
| OPT-010 | Note « Globaux non-optimisés » | `views/optimizer-view.tsx` |
| OPT-011 | Badges de compatibilité TF par stratégie | `views/optimizer-view.tsx` |
| OPT-012 | Layout 2 colonnes (config sticky) | ⏭ reporté S7 |
| OPT-013 | `n_jobs` en select guidé | ⏭ reporté S7 |
| CMP-001 | Input bougies libre (200–50 000) | `views/compare-view.tsx` |
| CMP-002 | Colonne Equity finale | `views/compare-view.tsx` |
| CMP-003 | Rang #1, #2… par stratégie | `views/compare-view.tsx` |
| CMP-004 | Raccourcis « toutes / aucune / omnibus » | `views/compare-view.tsx` |
| CMP-005 | Intro card + indicateur ▼▲ sur colonne triée | `views/compare-view.tsx` |

**Livré dans `d203bb4`** — Sprints 3 (Replay) et 4 (ML) :

| ID | Objet | Porté par |
|----|-------|-----------|
| ML-001 | Optimiseur ML complet via prop `filterMl` | `views/optimizer-view.tsx`, `views/ml-view.tsx` |
| ML-002 | Checkbox `ml_tune_hp` | `views/optimizer-view.tsx` |
| ML-003 | Dialog d'entraînement de recette (remplace le renvoi vers `/models`) | `cards/train-recipe-dialog.tsx`, `cards/ml-recipes-list.tsx` |
| ML-004 | Tooltip Apply + invalidation `mlInfo` / `ml-recipes` | `views/optimizer-view.tsx` |
| ML-005 | Preview matrix héritée de l'optimiseur | `views/optimizer-view.tsx` |
| ML-006 | Warning omnibus ≥ 2 200 bougies | `views/optimizer-view.tsx` |
| ML-007 | Note « modèle ML sauvegardé automatiquement » | `views/optimizer-view.tsx` |
| RPL-001 | Moteur de replay interactif + chart + contrôles | `hooks/use-replay-engine.ts`, `charts/replay-candlestick-chart.tsx`, `controls/playback-controls.tsx` |
| RPL-002 | Markers entrée ▲▼ / sortie ●, filtrés par barre | `views/replay-view.tsx` |
| RPL-003 | Journal des signaux temps réel | `cards/replay-signal-log.tsx` |
| RPL-004 | Stats accumulées (trades, WR, PnL) | `cards/replay-stats-panel.tsx` |
| RPL-005 | Raccourcis clavier | `hooks/use-replay-keyboard.ts` |
| RPL-006 | Slider Mois (1–24) + hint bougies | `lib/limit-hint.ts` (`monthsToBougies`) |
| RPL-007 | Layout plein écran | `views/replay-view.tsx` |
| RPL-008 | Sélecteur de stratégie overlay | `views/replay-view.tsx` |
| RPL-009 | Welcome screen + log panel horodaté | ⚠️ **à moitié** — les 4 tips cards sont dans `views/replay-view.tsx` ; le log panel `HH:MM:SS · niveau · message` n'existe pas |

### Audit de conformité aux critères d'acceptation (2026-08-07)

Les critères d'acceptation de la spec d'origine ont été confrontés au code.
**48 specs sur 52 sont conformes** ; 4 écarts, tous sur des critères secondaires
d'une spec par ailleurs livrée — aucune fonctionnalité principale ne manque.

| Spec | Critère non tenu | État réel |
|------|------------------|-----------|
| **BT-004** | « Bouton *Effacer* vide la session » | `useBacktestSession()` **expose** `clear`, mais `lab/page.tsx` n'utilise que `restored` et `save` : aucun bouton ne l'appelle. `clear` est du code mort et la session ne peut pas être purgée depuis l'UI. |
| **BT-011** | « Le lien *Ajuster dans Config* redirige vers `/settings?tab=strategies&strategy=<name>` » | Les deux warnings sont rendus en `<div>` de texte brut ([lab/page.tsx](../frontend/src/app/lab/page.tsx)) — pas de lien. L'utilisateur doit naviguer à la main. |
| **BT-003** | « Le tableau per-strategy est triable par *Évalué* / *Proposé* / *Erreurs* » | Les `<th>` de [diagnostics-panel.tsx](../frontend/src/components/cards/diagnostics-panel.tsx) sont statiques : ni `onClick`, ni état de tri. Le tableau s'affiche dans l'ordre backend. |
| **RPL-009** | « Log panel horodaté `HH:MM:SS · niveau · message` » | Seule la moitié *welcome screen* est livrée (4 tips cards). Le commit `d203bb4` annonce RPL-009 comme « exclu par l'utilisateur » : c'est exact pour le log panel, inexact pour le welcome. |

Écart cosmétique, sans critère associé : la spec RPL-002 demande des sorties
marquées `✓`/`✗` ; [replay-candlestick-chart.tsx](../frontend/src/components/charts/replay-candlestick-chart.tsx)
utilise la forme `circle` de lightweight-charts. Les trois critères de RPL-002
(markers progressifs, reconstruction au seek, pas de doublon) sont tenus.

Vérifié conforme par lecture du code, entre autres : persistance `localStorage`
du toggle line/candles et message « Aucun trade pour cette stratégie » (BT-001) ;
pagination 20/page et filtres Tous/Long/Short/Win/Loss (BT-002) ; masquage du
panneau si `bars_total == 0` (BT-003) ; encart « Pas de baseline disponible »
(OPT-001) ; masquage du bloc si `top_trials` et `best_params` sont vides
(OPT-002) ; colonne Equity Finale triable et présente au CSV (CMP-002).

### Écarts backend assumés

Aucun fichier backend n'a été modifié par ces deux commits. Les divergences de
nommage sont absorbées côté client par `lib/backend-normalizers.ts` :

| Champ attendu | Champ réellement renvoyé | Traitement |
|---------------|--------------------------|------------|
| `signal_reason` | `reason` | `normalizeTrade` |
| `equity_final` | `final_equity` | `equityFinal` |
| `top_trials` | `top5` | `normalizeTopTrials` |
| `recommended_tfs` | `timeframes` | vue optimiseur |
| bloc `after` consolidé | `best_oos_*` épars | `deriveAfter` |
| `baseline.win_rate` / `.max_drawdown` | `.wr` / `.dd` | `normalizeBaseline` |
| `ml_info` | non propagé au client | `normalizeMlInfo` + repli défensif |

> Ces normalisations sont une **dette assumée**, pas une architecture : le jour
> où le backend s'aligne sur ces noms, `backend-normalizers.ts` doit maigrir
> d'autant, pas se doubler d'une seconde couche.

## 9. Conclusion

Le décommissionnement Jinja2 a **conservé l’API** et **restructuré l’UI** en deux hubs (Lab / Marché).

- **Optimizer & Backtest** : parité atteinte pour l'essentiel après les sprints
  de rattrapage, et enrichis au-delà de Jinja2 (cost model, study vs live,
  create bot, reprise de session, diagnostics).  
- **Replay Laboratoire** : capacité **nouvelle** — le replay interactif
  bougie-par-bougie n'a pas d'équivalent Jinja2 ; le replay batch multi-TF
  qu'il remplace reste accessible sous `?tab=batch`.  
- **Smart Graph / Replay / Dérivés** : usage quotidien viable ; écarts
  résiduels sur le rendu des zones et l'overlay prix.  
- **Scanner** : reste le plus grand écart de périmètre par rapport à Jinja2
  (cockpit multi-symboles + multi-indicateurs), même si la table multi-symboles
  et le chart 4 panneaux ont été livrés (§8bis, items 1 et 2).

**Reprises restantes**, par ordre d'impact :

1. Les 4 critères d'acceptation non tenus (§8ter, audit de conformité) — tous
   petits, tous isolés : bouton *Effacer* de la session (BT-004, le `clear`
   existe déjà et n'attend qu'un bouton), lien *Ajuster dans Config* (BT-011),
   tri du tableau per-strategy (BT-003), log panel horodaté (RPL-009).
2. Distribution PnL et cumul des trades au backtest — les deux seuls écarts
   de périmètre subsistants du Laboratoire (§4.2).
3. Layout 2 colonnes et `n_jobs` guidé à l'optimiseur (OPT-012 / OPT-013,
   reportés en S7).
4. Revérifier les sections `/market` de ce document contre le code : elles
   datent du 05/08 et n'ont pas été réauditées depuis.
5. Réduire `lib/backend-normalizers.ts` en alignant les noms de champs côté
   backend, plutôt que d'épaissir la couche de traduction côté client.

Aucun test ne couvre les 5 881 lignes livrées par les deux sprints : les 63
tests verts portent sur des fichiers antérieurs. `useReplayEngine` et
`lib/limit-hint.ts` sont purement fonctionnels et se testent sans DOM.

Les items marqués « volontairement non traité » (markers setups V8/V11/V12 sur
le scanner, TF 2h Smart Graph) restent hors périmètre tant qu'ils n'ont pas été
redemandés.
