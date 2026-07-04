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

| TF | Trades | Win rate | PnL (capital 1000) | PF | Sharpe | DD max |
|---|---|---|---|---|---|---|
| **4h** | 181 | **46,4 %** | **+405 (+40,5 %)** | **1.41** | 6.5 | −9,9 % |
| 1h | 582 | 31,4 % | −350 | 0.80 | — | −60 % |
| 2h | 379 | 33,2 % | −284 | 0.81 | — | −44 % |
| 1d | 28 | 28,6 % | −62 | 0.69 | — | −11 % |

Sous-périodes 4h : PF **2.28** (2018-2021), **1.54** (2021-2024), **0.95**
(2024-2026) — l'edge décroît sur la période récente : à surveiller via le
forward-test et le lifecycle avant toute allocation réelle. Les cibles voids
(`use_void_targets`) apportent ~+65 USDC sur le 4h ; le setup BREAKER_RETEST
teste négatif (−163 / 220 trades) → off par défaut. Les TF < 4h sont laissés à
l'optimiseur (`param_space` expose `swing_len`, `eq_tol_atr`, `disp_body_atr`,
`min_rr`, `min_gain_pct`, `sl_buffer_atr`, `ema_filter_len`, `choch_exit`,
`use_breakers`, `use_void_targets`).

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
- 3 panneaux : **Lecture du marché** (structure, dernier événement, zone
  premium/discount, équilibre, OTE, phase et progression du cycle),
  **Signal smart_money** (entrée/SL/TP/gain %/RR/cible/raison) et
  **Zones actives** (liste chiffrée).

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
