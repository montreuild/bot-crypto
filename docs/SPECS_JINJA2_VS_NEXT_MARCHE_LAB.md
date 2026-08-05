# Specs détaillées — pages Jinja2 décommissionnées vs UI Next.js

> **Périmètre** : Optimisateur, Backtests, Scanner, Smart Graph, Smart Replay, Dérivés  
> **Sources** : templates `ecc87b2^:app/web/templates/*` (commit de suppression `ecc87b2`, 29/07/2026), `docs/FIN_JINJA2.md`, `docs/audit-ui-ux-bot-crypto.md` §3, code `frontend/src/app/{lab,market}/` + `components/views/*`  
> **Date** : 2026-08-05  

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

| Onglet | Composant | Rôle |
|--------|-----------|------|
| Backtest | `lab/page.tsx` (inline) | Formulaire + résultats + charts + WF/MC |
| Optimizer | `optimizer-view.tsx` | Optimisation bayesian/grid/random |
| ML | `ml-view.tsx` | Train / sweep (hors périmètre Jinja optimizer non-ML) |
| Replay | `replay-view.tsx` | Replay stratégie (≠ Smart Replay SMC) |
| Compare | `compare-view.tsx` | Comparatif multi-stratégies |

#### Backtest Next (état actuel)
- Config : symbole, TF, limit, stratégies, options expert  
- Charts : equity, scatter, OHLCV markers (`BacktestEquityChart`, `TradesScatter`)  
- **Walk-Forward** : `WalkForwardTable`  
- **Monte-Carlo** : `MonteCarloPanel`  
- Study vs Live : `StudyVsLiveCard`  
- Cost model : `CostModelCard`  
- Export CSV/JSON  
- Bouton « Créer le bot (Essai) » (pipeline vers lifecycle)

#### Optimizer Next
- Méthodes grid/random/bayesian  
- Multi-symboles / multi-TFs  
- SSE stream progression  
- Jobs : start / cancel / apply / delete  
- Cost model card  
- **Pas** de séparation stricte ML vs non-ML dans la même vue (ML a son onglet)

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
| **Optimizer** | **~85 %** | Core jobs/SSE/apply OK ; UX cartes jobs un peu moins riche |
| **Backtest** | **~80 %** | WF/MC/scatter revenus ; fullscreen modal & PDF absents ou partiels |
| **Scanner** | **~40 %** | Fast Analyse + prédictions + opportunités ; manque scan table + multi-panneaux |
| **Smart Graph** | **~75 %** | Calques + plans + clic plan OK ; zones non remplies ; layout modernisé |
| **Smart Replay** | **~70 %** | Replay causal OK ; layout modernisé ; shortcuts / zones remplies manquants |
| **Dérivés** | **~80 %** | 4 séries OK ; lib Recharts ; overlay prix manquant |

Légende : estimation qualitative sur le **périmètre métier** (pas le pixel-perfect).

---

### 4.2 Matrice fonction × page

#### Optimisateur

| Fonction | Jinja2 | Next `/lab?tab=optimizer` |
|----------|:------:|:-------------------------:|
| Méthodes grid/random/bayesian | ✅ | ✅ |
| Multi-stratégies checkboxes | ✅ | ✅ |
| Multi-TF | ✅ | ✅ |
| Workers / early-stop | ✅ | ✅ |
| Param search optim (gel) | ✅ | ⚠️ à vérifier |
| Preview matrice TF×strat | ✅ | ⚠️ partiel |
| Jobs cards progress + top5 | ✅ | ✅ |
| SSE live progress | ⚠️ / poll | ✅ EventSource |
| Apply / cancel / delete | ✅ | ✅ |
| Auto-apply en fin de run | ✅ | ⚠️ |
| Cost model | ❌ | ✅ `CostModelCard` |
| Filtrage strict non-ML | ✅ | ✅ (ML séparé) |

#### Backtest

| Fonction | Jinja2 | Next `/lab?tab=backtest` |
|----------|:------:|:------------------------:|
| Multi-stratégies | ✅ | ✅ |
| Limit bougies + hints durée | ✅ | ✅ |
| Walk-Forward folds | ✅ | ✅ `WalkForwardTable` |
| Monte-Carlo IC | ✅ | ✅ `MonteCarloPanel` |
| Equity + buy&hold | ✅ | ✅ |
| Distrib PnL | ✅ | ⚠️ |
| Cumul trades | ✅ | ❌ |
| Prix + markers | ✅ | ✅ |
| Scatter Chart.js | ✅ | ✅ Recharts |
| Fullscreen modal | ✅ | ❌ |
| Export JSON | ✅ | ✅ |
| Export PDF | ✅ | ❌ |
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
/lab?tab=backtest|optimizer|ml|replay|compare
/market?tab=scanner|smartgraph|smartreplay|derivatives
```

- **308** depuis anciennes routes pour favoris / docs  
- Chargement **dynamic** des chunks chart (poids initial réduit)  
- Backend REST **inchangé** (mêmes endpoints) — l’écart est purement UI  

---

## 8. Références

| Document | Contenu |
|----------|---------|
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

## 9. Conclusion

Le décommissionnement Jinja2 a **conservé l’API** et **restructuré l’IA** en deux hubs (Lab / Marché).  

- **Optimizer & Backtest** : bonne parité, enrichis (cost model, study vs live, create bot).  
- **Smart Graph / Replay / Dérivés** : usage quotidien viable ; écarts sur rendu zones, raccourcis, overlay prix.  
- **Scanner** : le plus grand écart — Next = **Fast Analyse mono-symbole** ; Jinja2 = **cockpit multi-symboles + multi-indicateurs**.  

Toute reprise prioritaire doit commencer par le **Scanner table + chart 4 panneaux** et les **zones SMC remplies**, pour retrouver le niveau d’analyse de marché de l’ancienne UI sans revenir à Jinja2.
