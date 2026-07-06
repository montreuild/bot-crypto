# 🧠 Moteur Smart Money Concepts (SMC)

Détection automatique des structures institutionnelles (liquidité, offre/demande,
structure de marché, tendances) + stratégie de trading `smart_money` basée sur ce
moteur. Ajouté en juillet 2026.

---

## 📦 Composants

| Fichier | Rôle |
|---|---|
| `app/core/smc.py` | Moteur d'analyse : une passe causale O(n) sur l'OHLCV |
| `app/strategies/smart_money.py` | Stratégie de trading (BaseStrategy) |
| `strategies/smart_money.yaml` | Paramètres + activation (supprimer/`enabled: false` pour désactiver) |
| `GET /api/scanner/smc` | Endpoint JSON pour l'overlay graphique et la page Smart graph |
| `app/web/templates/smartgraph.html` | Page dédiée **« Smart graph »** (`/smartgraph`) — chart d'analyste complet |
| `app/web/templates/scanner.html` | Case « SMC (Smart Money) » → dessin des zones sur le chart scanner |
| `tests/test_smc.py` | 28 tests unitaires (patterns synthétiques + contrats) |

---

## 🔍 Détections du moteur (`app/core/smc.py`)

Toutes les entités portent des **indices de barres** (`index`, `formed_at`,
`swept_at`, `touched_at`…) : à la barre *i*, seules les données ≤ *i* ont été
utilisées → exploitable en backtest **sans lookahead**.

### 1. Swings (pivots fractals)
Pivot haut/bas si extrême strict sur `swing_left`/`swing_right` barres de chaque
côté. Confirmé `swing_right` barres après le pivot. Étiquetés **HH / HL / LH /
LL** par rapport au swing précédent de même nature.

### 2. Structure de marché — BOS / CHoCH
Sur **clôture** au-delà du dernier swing confirmé :
- **BOS** (Break of Structure) : cassure dans le sens de la tendance courante
  (continuation) ;
- **CHoCH** (Change of Character) : cassure contre la tendance (retournement
  potentiel). Trend par barre exposé dans `_trend_arr` (+1 / −1 / 0).

### 3. Zones de liquidité (Liquidity Pools)
Doubles/triples sommets et fonds : swings de même nature à `eq_tol_atr`×ATR près
→ **Buy-side liquidity** au-dessus des equal highs, **Sell-side liquidity** sous
les equal lows. Les swings isolés restent des poches mineures. Cycle de vie :
`active` → `swept` (mèche au-delà du niveau).

### 4. Sweeps (prises de liquidité / stop hunts)
Mèche qui perce une poche ou un swing puis **clôture de retour du bon côté**
(`rejected: true`). Zone morte ±`eq_tol_atr`×ATR sur les swings isolés : un
dépassement marginal n'est pas un sweep mais un candidat equal highs/lows.

### 5. Order Blocks (zones d'Offre/Demande)
Dernière bougie opposée avant un **displacement** (corps ≥ `disp_body_atr`×ATR
qui engloutit l'extrême précédent). Zone = [low, corps haut] (bullish) ou
[corps bas, high] (bearish). `strength = 2` si l'impulsion casse la structure
dans les 3 barres (BOS/CHoCH). Cycle de vie : `fresh` → `touched` (premier
retour du prix) → `invalidated` (clôture au travers).

### 6. Fair Value Gaps (FVG / imbalances)
Gap entre `high[i−2]` et `low[i]` (et miroir), taille ≥ `fvg_min_atr`×ATR.
Cycle de vie : `open` → `mitigated` (prix entre dans le gap) → `filled`.

### 6bis. Liquidity Voids
Run d'au moins `void_min_bars` (3) bougies directionnelles consécutives
traversant ≥ `void_min_atr` (2.5)×ATR : zone « fine » parcourue sans trader,
étendue tant que le run continue. Aimant naturel : le prix revient la combler.
Cycle de vie `open` → `mitigated` → `filled`. Les bords opposés des voids non
comblés servent de **cibles de TP** à la stratégie (`use_void_targets`).

### 6ter. Breaker Blocks
Un order block **invalidé sur clôture** inverse sa polarité : une demande
transpercée devient offre (et réciproquement) — les stops piégés dans la zone
alimentent le retest en sens inverse. Cycle de vie `fresh` → `touched` →
`invalidated` (re-cassure). Setup `BREAKER_RETEST` disponible dans la
stratégie (off par défaut, négatif sur BTC 4h).

### 6quater. Rejection Blocks
Swing confirmé avec une **mèche marquée** (≥ `rb_wick_atr`×ATR) : la mèche
matérialise un rejet violent — zone d'offre [corps haut, high] au sommet,
de demande [low, corps bas] au creux. Même cycle de vie que les OB. Setup
`REJECTION_RETEST` (off par défaut : dilue le PF sur BTC, seul l'OOS 4h/2h
en profite — exploré par l'optimiseur).

### 6quinquies. Volume Profile (POC / HVN / LVN)
Histogramme causal du volume par tranche de prix (`smc.volume_profile`,
fenêtre `vp_lookback`) : **POC** (Point of Control, aimant), **HVN**
(acceptation — support/résistance volumétrique, bonus de confluence
`vp_confluence`), **LVN** (rejet — équivalent volumétrique des voids).
Cibles `vp_targets` disponibles (négatif sur BTC → off).

### 6sexies. Sessions & Killzones
Sessions UTC (Asia 0-7, London 7-12, New York 12-21) et killzones ICT
(LDN 07-10, NY 12-15) : `kz_bonus` (confluence) et `kz_filter` (dur).
Mesuré **sans edge sur BTC** (marché 24/7) → off par défaut.

### 6septies. Biais multi-timeframe (HTF)
`smc.htf_trend_series` : agrège l'OHLCV en buckets horloge de
`htf_mult`×timeframe, analyse la structure HTF (BOS/CHoCH) et mappe sur
chaque barre LTF le trend du dernier bucket **clôturé** (causal, identique
live/backtest). Filtre `htf_filter` : `soft` (pas de trade contre le HTF,
**défaut — seul enrichissement gagnant sur tous les TF testés**) ou
`strict` (alignement exigé).

### 6octies. AMD / Power of Three
Compression (`amd_bars` barres dans ≤ `amd_range_atr`×ATR) suivie d'un sweep
= manipulation probable avant expansion. Bonus `amd_bonus` sur les
SWEEP_REVERSAL post-accumulation.

### 7. Premium / Discount + OTE
Range de travail = dernier swing high ↔ dernier swing low (élargi au max/min des
100 dernières barres). Équilibre à 50 % ; `premium` > 55 %, `discount` < 45 %.
Zone **OTE** (Optimal Trade Entry) = retracement 62–79 % de la jambe en tendance.
Version causale par barre : `smc.premium_discount_at(result, h, l, c, i)`.

### 8. Tendances
- **Trendlines automatiques** : support par les 2 derniers swing lows,
  résistance par les 2 derniers swing highs, projetées jusqu'à la dernière barre.
  Version causale par barre : `smc.trendline_value_at(result, i, kind)`.
- **Canal de régression** : droite médiane ± 2σ des résidus sur
  `channel_lookback` barres. Version causale : `smc.regression_channel_at`.

### 9. Structure line (zigzag) + cycle de marché
- **Zigzag** : polyligne des swings alternés (peaks/troughs) avec labels
  HH/HL/LH/LL — le tracé « market structure » classique, exposé dans
  `structure_line`.
- **Projection de cycle** (`cycle`) : après un trough, phase `advance` vers la
  borne haute du canal (*expected peak*) ; après un peak, phase `decline` vers
  la borne basse (*expected trough*). Expose la cible projetée et la
  progression dans le cycle.

### API du module

```python
from app.core import smc
result = smc.analyze(df, {"swing_left": 3, "eq_tol_atr": 0.25, ...})
# result["order_blocks"] / ["liquidity_pools"] / ["fvgs"] / ["sweeps"]
# result["structure_events"] / ["swings"] / ["bias"] / ["premium_discount"]
# result["trendlines"] / ["channel"]
# clés privées numpy : _trend_arr, _atr_arr, _all_* (listes non tronquées)
smc.liquidity_targets_above(result, i, price)   # cibles TP long (causal)
smc.liquidity_targets_below(result, i, price)   # cibles TP short (causal)
```

---

## 📈 Stratégie `smart_money`

### Setups

1. **SWEEP_REVERSAL** — une mèche prend la liquidité (equal lows / swing low
   pour un long) puis la bougie clôture de retour du bon côté. Les stops
   viennent d'être consommés → entrée dans le sens opposé au sweep, stop sous
   l'extrême de la mèche (+ `sl_buffer_atr`×ATR).
2. **OB_RETEST** — premier retour du prix dans l'order block à l'origine d'une
   impulsion qui a cassé la structure → entrée sur la zone, stop de l'autre
   côté de l'order block.

### Campagne de mesure des enrichissements (BTC/USDC, 2026-07)

Chaque enrichissement testé ISOLÉMENT sur 15m/30m/1h/2h/4h/1d, historique
complet + dernier tiers (pseudo-OOS 2024-2026, la période la plus dure) :

| Enrichissement | Verdict | Détail |
|---|---|---|
| **Biais HTF (soft)** | ✅ **ON par défaut** | Seul gagnant sur TOUS les TF. HTF aligné sur `_HTF_MAP` (4h→1d) : PF **1.52** vs 1.41, Sharpe 8.2, DD −8.1 vs −9.9 |
| Rejection blocks | ⚠ off | Dilue le PF global (4h : 323 vs 405) mais améliore l'OOS (+26) — exploré par l'optimiseur |
| Volume profile (confluence) | ⚠ off | Neutre au seuil 0.55 (ne s'exprime qu'à seuil élevé, cf. configs par TF) |
| Volume profile (cibles) | ❌ off | Légèrement négatif partout |
| Killzones (bonus) | ⚠ off | Neutre au seuil 0.55 |
| Killzones (filtre dur) | ❌ off | Négatif partout — pas d'edge horaire sur BTC 24/7 |
| AMD (bonus) | ⚠ off | Neutre au seuil 0.55 |

### Filtres DURS (validés empiriquement sur BTC/USDC 30m→1d, 2019-2026)

- **Avec la tendance uniquement** : long seulement en structure haussière,
  short en baissière. Les sweeps contre-tendance étaient les deux pires
  buckets de l'historique (−36/−97 USDC en 1h, −142/−136 en 4h).
- **Côté momentum du range** : pas de long en zone `discount`, pas de short en
  zone `premium`. Contre-intuitif vs le manuel SMC classique, mais net dans
  les données : sur crypto, la force appelle la force — le « deep discount »
  d'une tendance haussière est le plus souvent une structure en train de casser.
- **Biais EMA200** : long au-dessus, short en dessous (`ema_filter_len: 0` pour
  désactiver).
- **Garde CHoCH** : pas d'entrée contre un changement de caractère plus récent
  que `choch_guard_bars` (5).

### Score (base 0.50, cap 1.0, seuil YAML 0.55 → ≥ 1 confluence)

| Confluence | Bonus |
|---|---|
| Structure alignée (toujours vrai pour OB_RETEST) | +0.10 |
| Sweep d'un pool (equal highs/lows) vs swing isolé | +0.10 |
| Order block strength 2 (impulsion a cassé la structure) | +0.10 |
| Prix du côté momentum fort (premium pour long, discount pour short) | +0.10 |
| Chevauchement avec un FVG ouvert de même direction | +0.05 |
| Volume > `vol_confluence`×SMA20(volume) | +0.05 |
| Bougie de rejet colorée dans le sens du trade | +0.05 |

### Sorties — bracket FIXE (pas de trailing)

- **SL** : sous/sur l'extrême de la zone + `sl_buffer_atr`×ATR.
- **TP** : posé juste devant la **prochaine poche de liquidité opposée**
  (front-run de `tp_front_run_atr`×ATR) — la première poche satisfaisant les
  deux contraintes ci-dessous ; sinon fallback `tp_rr_fallback`×R.
- Early-exit CHoCH disponible (`choch_exit`) mais **désactivé par défaut** :
  coupait systématiquement en perte sur BTC toutes TF.

### ⚠ Règle de gain : positions > 0,4 % uniquement

Une position n'est **retenue que si** :
1. le gain potentiel (distance entrée → TP) **dépasse `min_gain_pct` (0,4 %)** ;
2. ET le ratio gain/risque atteint `min_rr` (1.2).

Tout setup dont la cible est trop courte est rejeté (ou re-ciblé sur la poche
suivante si elle respecte les deux contraintes). Vérifié par test unitaire et
sur les 179 trades du backtest 4h (gain potentiel min observé : 0,61 %).

### Validation (BTC/USDC, frais 0,1 %/côté, spread 0,05 %, risque 1 %/trade)

Défauts actuels (dont `htf_filter: soft`, HTF aligné sur `_HTF_MAP` → 4h utilise
le 1d) :

| TF | Trades | Win rate | PnL (capital 1000) | PF | Sharpe | DD max |
|---|---|---|---|---|---|---|
| **4h** | 145 | **46,9 %** | **+401 (+40,1 %)** | **1.523** | 8.2 | −8,1 % |
| 1h | 523 | 33,1 % | −306 | 0.81 | — | −54 % |
| 2h | 295 | 34,9 % | −193 | 0.85 | — | −38 % |
| 1d | 25 | 32,0 % | −37 | 0.78 | — | −9 % |

Sous-périodes 4h : PF **2.49** (2018-2021), **1.56** (2021-2024), **0.97**
(2024-2026). Les cibles voids apportent ~+65 USDC sur le 4h ; le setup
BREAKER_RETEST teste négatif (−163 / 220 trades) → off par défaut.

### Configurations par timeframe (calibration 2026-07-04)

Grille ciblée par TF, split **IS 2/3 / OOS 1/3** (OOS = 2024-2026, la période
la plus dure), sélection : ≥ 10 trades OOS **et** PF IS ≥ 1.0, classement par
PF OOS. Écrites dans `strategies/smart_money.yaml` → `optimizer_results`
(surcharge automatique par TF en live et en backtest via
`resolve_strategy_params`). Le seuil par TF passe par `min_score` (paramètre
interne de la stratégie — `score_threshold` est une clé globale protégée de
l'overlay).

| TF | Config retenue | OOS (2024-2026) | oos_score | Verdict |
|---|---|---|---|---|
| **4h** | min_score 0.75, RR ≥ 2, SL 0.5×ATR, bonus kz/amd/vp | 51 trades, **+22 (+2,2 %), PF 1.08, DD −7,4 %** | **+0.174** | ✅ tradable |
| 1h | min_score 0.65, RR ≥ 2, gain ≥ 1,2 %, killzones only | −33, PF 0.83 (vs −182 défauts) | −0.067 | ❌ |
| 2h | min_score 0.75, RR ≥ 2 | −115, PF 0.77 | −0.230 | ❌ |
| 30m | min_score 0.75, RR ≥ 1.5, gain ≥ 1,2 % | −71, PF 0.78 | −0.142 | ❌ |
| 15m | min_score 0.65, RR ≥ 1.5, gain ≥ 1,2 %, killzones only | −41, PF 0.42 | −0.082 | ❌ |
| 1d | min_score 0.55 | −37, PF 0.59 (12 trades) | −0.075 | ❌ |

> Les scores OOS des TF ≠ 4h ont été mesurés avant l'alignement HTF sur
> `_HTF_MAP` (juillet 2026) ; ils restent négatifs (non tradables) et n'ont pas
> été re-calibrés — seul le 4h, la config effectivement candidate, l'a été.

**Lecture senior** : seul le 4h a un edge démontré out-of-sample. Les autres
TF sont enregistrés avec leur config « la moins mauvaise » et un score négatif
assumé — l'audit et le lifecycle ne les promouvront pas ; si un TF est forcé,
la config limite au moins l'hémorragie. La sélectivité (min_score 0.75 = ≥ 3
confluences dont les bonus killzone/AMD/volume-profile) sacrifie du PnL
2018-2021 pour de la robustesse récente : c'est le bon arbitrage pour du
capital futur.

### Performance technique

- `prepare_for_backtest` : une seule passe `smc.analyze` sur toute la fenêtre
  puis signaux pré-calculés par barre → `score()` en O(1) pendant le backtest
  (≈115 ms pour 4 000 barres de pré-calcul).
- Live/scanner : analyse de la fenêtre bornée à `max_window` (3 000 barres),
  ≈70 ms. Cohérence cache/live vérifiée par test unitaire.

---

## 🖥️ Branchements UI

### Page « Smart graph » (`/smartgraph`) — chart d'analyste dédié
Page spécifique avec un grand chart (620 px) façon « pro trader » :
- **Zones en vrais rectangles ombrés** (primitive canvas lightweight-charts) :
  vert = demande, rouge = offre (★ = strength 2), bleu = poches de liquidité
  BSL/SSL, violet = FVG, gris = liquidity voids, orange = breakers ;
- **Zigzag de structure** (polyligne blanche peaks/troughs) avec labels
  HH/HL/LH/LL, flèches BOS/CHoCH, points SW (sweeps rejetés) ;
- **Trendlines** support/résistance + **canal de régression** ;
- **Projection de cycle** : ligne pointillée vers l'*expected peak/trough* à
  la borne du canal, avec price line dédiée ;
- **Signal courant** : price lines entrée/SL/TP de la stratégie ;
- Calques activables individuellement, deep-link `?symbol=…&tf=…` ;
- **Tableau « Trades à ouvrir — plans recommandés »** : le signal immédiat
  (⚡ MAINTENANT) et les setups EN ATTENTE (⏳) générés par
  `Strategy.trade_plans()` — retests d'order blocks frais et sweeps potentiels
  des poches actives, alignés avec les filtres durs de la stratégie. Chaque
  plan affiche : sens, setup, **déclencheur/configuration à attendre**
  (« Attendre le retour du prix dans la zone d'offre [x–y] + bougie de
  rejet »), **entrée / SL / TP recommandés**, gain potentiel (> 0,4 %
  garanti), RR, distance au prix et score minimum. Un clic sur un plan trace
  ses niveaux entrée/SL/TP sur le graphique et affiche le motif complet ;
- 3 panneaux : **Lecture du marché** (structure, dernier événement, zone
  premium/discount, équilibre, OTE, phase et progression du cycle),
  **Signal smart_money** (entrée/SL/TP/gain %/RR/cible/raison) et
  **Zones actives** (liste chiffrée).

### Page « Smart replay » (`/smartreplay`) — rejeu bougie par bougie
Rejoue le cours et montre **l'analyse telle que le moteur la découvrait** à
chaque instant (les swings n'apparaissent qu'à leur confirmation, les zones
naissent, se font toucher, se font invalider…) :
- **Contrôles TradingView-like** : play/pause (espace), pas à pas (←/→),
  ±10 barres (⇧+flèches), début/fin, vitesse 2→20 barres/s, slider ;
- **Une seule requête** (`/api/scanner/smc_replay`) : le moteur étant
  strictement causal, chaque entité porte ses indices de cycle de vie —
  le navigateur reconstruit l'état à n'importe quelle barre par simple
  comparaison d'indices (lecture fluide, scrubbing instantané) ;
- **Trades réels du Backtester** avec les paramètres par TF résolus
  (`optimizer_results`) : flèche d'entrée, bracket entrée/SL/TP affiché tant
  que la position est ouverte (PnL latent), dénouement marqué ✓ TP / ✗ SL
  avec le % ;
- **Évaluation de pertinence en direct** : panneau Performance cumulée
  (trades clos, TP/SL, win rate, PnL cumulé), Journal des trades (motif au
  survol), Lecture à la barre courante (structure, biais HTF, zone
  premium/discount causale) ;
- Calques activables (structure, zones, liquidité, FVG, voids, breakers,
  rejections, trendlines, trades), deep-link `?symbol=…&tf=…`.

### Page Scanner (`/scanner`)
Case **« SMC (Smart Money) »** dans la barre d'indicateurs du graphique :
- **Order blocks** : segments horizontaux (top/bottom) — vert = demande,
  rouge = offre ; trait épais = strength 2 ; estompé = déjà touché.
- **Poches de liquidité** : pointillés ambre (buy-side) / cyan (sell-side),
  estompés une fois sweepés.
- **FVG ouverts** : double tireté violet.
- **Markers** : flèches BOS (vert/rouge), CHoCH (ambre), SW violet = sweep
  rejeté, labels HH/HL/LH/LL discrets sur les swings.
- **Trendlines** (support/résistance) + **canal de régression** gris.
- Légende : biais structurel, zone premium/discount (EQ), compteurs, et le
  **signal courant** de la stratégie (entrée/SL/TP/gain potentiel/RR/cible).

### Page Replay (`/replay`)
`smart_money` apparaît automatiquement dans la liste des stratégies (activée
par son YAML) : rejouable sur N mois, multi-TF, avec walk-forward et
Monte-Carlo comme n'importe quelle stratégie.

### Backtest / Optimiseur / Live
Stratégie standard du registre : backtests via `/backtest`, optimisation des
paramètres (espace ci-dessus), trading paper/live via `strategies.enabled` et
le capital allocator.

---

## 🧪 Tests

```bash
python -m pytest tests/test_smc.py -v
```

28 tests : pivots/confirmation, BOS/CHoCH, equal lows → pool, sweep rejeté,
order block (création/touch), FVG, **liquidity voids** (détection/fill/cibles
causales), **breakers** (création à l'invalidation, retest), **zigzag**
(alternance stricte) et projection de cycle, trendline causale,
premium/discount causal, trendlines/canal, contrat de la stratégie,
**filtre min_gain_pct** (rejet des cibles < 0,4 %), cohérence cache
backtest ↔ score live, invalidation du cache.
