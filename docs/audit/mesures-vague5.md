# Vague 5 — campagnes de mesure (IS/OOS)

Protocole commun : un run `Backtester` complet par (symbole, TF, variante) sur
tout l'historique parquet local ; trades partitionnés à 2/3 des barres
(IS = premier tiers ×2, OOS = dernier tiers). Baseline = config résolue
réelle (optimizer_results par TF/symbole). Chaque feature reste **off par
défaut** tant que la mesure ne montre pas une amélioration OOS nette sans
dégradation IS.

## [SMC-03] Liquidité calendaire PDH/PDL/PWH/PWL (2026-07-12)

Primitive `smc.calendar_liquidity_levels` (causale, jour/semaine UTC clôturés) ;
flag `use_calendar_liquidity` ∈ {False, "targets", "sweeps", True}.
`targets` = cibles TP additionnelles ; `sweeps` = déclencheur SWEEP_REVERSAL
sur prise du niveau + rejet (première barre de perce).

| Slot | Mode | FULL PnL | IS n / PnL / PF | OOS n / PnL / PF / WR |
|---|---|---:|---|---|
| BTC 4h | **off (baseline)** | +329.3 | 86 / +261.1 / 2.19 | 52 / +68.2 / 1.47 / 36.5 |
| BTC 4h | targets | +329.3 | identique baseline | identique baseline |
| BTC 4h | sweeps | +311.6 | 100 / +230.1 / 1.82 | 58 / +81.5 / 1.51 / 36.2 |
| BTC 4h | both | +311.6 | = sweeps | = sweeps |
| BTC 1h | off | −31.2 | 61 / −23.7 / 0.83 | 22 / −7.5 / 0.77 |
| BTC 1h | targets | −27.7 | = baseline | 22 / −4.0 / 0.87 |
| BTC 1h | sweeps | −33.2 | = baseline | 23 / −9.5 / 0.73 |
| ETH 4h | off | −73.1 | 96 / −17.8 / 0.95 | 65 / −55.4 / 0.70 |
| ETH 4h | targets | −44.9 | 96 / +14.5 / 1.04 | 65 / −59.3 / 0.70 |
| ETH 4h | sweeps | −98.3 | 107 / −25.2 / 0.94 | 66 / −73.1 / 0.60 |
| ETH 1h | off | −243.5 | 310 / −187.5 / 0.69 | 144 / −56.0 / 0.71 |
| ETH 1h | targets/sweeps/both | −261 à −241 | ≈ baseline | légèrement pire |

**Verdict : OFF par défaut (inchangé).**
- `targets` : strictement neutre sur BTC 4h (les cibles liquidité/void du
  moteur sont toujours plus proches) ; gain IS sur ETH 4h (+32) mais OOS
  légèrement pire — pas d'edge OOS.
- `sweeps` : +6 trades OOS sur BTC 4h avec OOS un peu meilleur (+81.5 vs
  +68.2, PF 1.51 vs 1.47) mais IS nettement dégradé (PF 2.19 → 1.82) et FULL
  en retrait — signal non robuste. Négatif sur ETH.
- Le flag est exposé dans `param_space` : l'optimiseur pourra l'explorer par
  TF/symbole ; aucune activation manuelle.

## [SMC-01] SMT à la barre d'origine de la zone (2026-07-12)

Flag `smt_at_origin` (off) : pour OB_RETEST/BREAKER_RETEST, la divergence SMT
est lue à `created_at` (barre de l'impulsion d'origine) au lieu de la barre de
retest. SWEEP_REVERSAL inchangé. Corrélé : ETH pour BTC, BTC pour ETH.

| Slot | Mode | FULL PnL | IS n / PnL / PF | OOS n / PnL / PF |
|---|---|---:|---|---|
| BTC 4h | baseline | +329.3 | 86 / +261.1 / 2.19 | 52 / +68.2 / 1.47 |
| BTC 4h | filter@retest | +328.2 | 85 / +260.0 / 2.18 | 52 / +68.2 / 1.47 |
| BTC 4h | filter@origin | +284.6 | 83 / +247.8 / 2.12 | 48 / +36.8 / 1.28 |
| BTC 1h | filter@origin | −28.9 | 55 / −21.4 / 0.84 | 22 / −7.6 / 0.77 |
| ETH 4h | filter@retest | −83.6 | 95 / −31.2 / 0.91 | 65 / −52.4 / 0.71 |
| ETH 4h | filter@origin | −45.0 | 83 / **+13.1** / 1.05 | 57 / −58.1 / 0.67 |
| ETH 1h | filter@origin | −221.6 | 278 / −168.8 / 0.69 | 128 / −52.8 / 0.70 |

(`bonus@origin` ≈ baseline partout : sans sizing par confluence, un bonus de
score ne change presque rien.)

**Verdict : OFF par défaut (inchangé).**
- L'inertie mesurée est bien LEVÉE : filter@retest touchait 0-1 trade
  (recouvrement quasi nul), filter@origin en filtre désormais des dizaines
  (BTC 4h : 4 trades OOS retirés ; ETH 1h : 16 trades OOS retirés).
- Mais la divergence SMT à l'origine n'est PAS un filtre de qualité ici :
  elle retire des gagnants sur le slot déployé (BTC 4h OOS +68.2 → +36.8,
  PF 1.47 → 1.28). Le gain IS sur ETH 4h (+31) ne tient pas en OOS.
- Exposé au param_space (`smt_at_origin`) pour l'optimiseur.

## [SMC-11] Inducement pour OB/BREAKER_RETEST (2026-07-12) — ✅ ACTIVÉ BTC 4h

Primitive partagée `smc.recent_sweep` (factorisée depuis `vizion._recent_sweep`) ;
flag `require_inducement` (off) + `inducement_lookback` (12) : un retest
d'OB/breaker n'est accepté que si un sweep rejeté OPPOSÉ (prise de liquidité)
a eu lieu dans les N barres avant l'origine de la zone.

| Slot | Mode | FULL PnL | IS n / PnL / PF | OOS n / PnL / PF / WR |
|---|---|---:|---|---|
| BTC 4h | baseline | +329.3 | 86 / +261.1 / 2.19 | 52 / +68.2 / 1.47 / 36.5 |
| BTC 4h | **inducement 12** | **+326.8** | **58 / +251.4 / 2.71** | **32 / +75.4 / 1.88 / 43.8** |
| BTC 4h | inducement 20 | +322.7 | 61 / +247.6 / 2.59 | 32 / +75.1 / 1.88 / 43.8 |
| BTC 1h | inducement 12 | −27.0 | 38 / −20.7 / 0.74 | 12 / −6.3 / 0.71 |
| ETH 4h | inducement 12 | −47.6 | 57 / +25.1 / 1.14 | 44 / −72.6 / 0.53 |
| ETH 1h | inducement 12 | −114.7 | 185 / −112.0 / 0.70 | 91 / −2.8 / 0.98 |

**Verdict : ACTIVÉ sur le slot BTC 4h uniquement**
(`strategies/smart_money.yaml › optimizer_results.4h.params`) :
- Sélectivité pure sur le slot déployé : PnL FULL quasi inchangé avec 38 %
  de trades en moins, IS PF 2.19 → 2.71 ET OOS PF 1.47 → 1.88 (WR +7 pts) —
  amélioration cohérente sur les deux périodes, n OOS = 32 ≥ 10.
- ETH : le gain IS ne tient pas en OOS (0.53) → PAS activé ; ETH 1h
  s'améliore nettement en OOS (−56 → −2.8) mais reste non positif.
- Défaut global inchangé (off) ; exposé au param_space.

## [SMC-04/05/06/07] Judas, TP écart-type, BPR+CE, Silver Bullet (2026-07-12)

Quatre détecteurs ICT morts câblés derrière des flags off :
`judas_bonus`/`judas_filter` (sweeps uniquement), `tp_std_dev` (grille
−1/−2/−2.5/−4 SD du dealing range), `use_bpr` (setup BPR_REVERSAL au CE),
`sb_bonus`/`sb_filter` (fenêtres 08/15/19 UTC). Vizion : `entry_at_ce` (off,
tap du CE du FVG déclencheur requis) via la nouvelle primitive
`ict.fvg_overlap_ce`. Baseline BTC 4h = config avec inducement activé.

Points saillants (mesure complète BTC+ETH × 4h/1h, 7 modes) :

| Slot | Mode | FULL | IS pnl/PF | OOS pnl/PF |
|---|---|---:|---|---|
| BTC 4h | baseline | +326.8 | +251.4 / 2.71 | +75.4 / 1.88 |
| BTC 4h | judas_filter | +346.1 | +269.6 / 2.82 | +76.5 / 1.88 |
| BTC 4h | use_bpr | +357.8 | +280.7 / 2.81 | +77.1 / 1.88 |
| BTC 4h | tp_std_dev | +318.9 | +244.0 / 2.59 | +74.9 / 1.88 |
| BTC 4h | sb_filter | +31.8 | 8 tr / 7.06 | 2 tr / −7.4 |
| ETH 4h | tp_std_dev | +24.0 | +65.2 / 1.18 | −41.2 / 0.78 |
| ETH 1h | tp_std_dev | −174.0 | −99.5 / 0.83 | −74.4 / 0.62 |
| ETH 1h | sb_filter | −65.0 | −50.1 / 0.47 | −14.9 / 0.80 |

**Verdict : tout reste OFF.**
- `judas_filter` / `use_bpr` : gain IS/FULL réel sur BTC 4h mais OOS plat
  (+1 à +2) — pas de preuve OOS ; laissés à l'optimiseur (param_space).
- `tp_std_dev` : transforme l'IS d'ETH 4h (−17.8 → +65.2, FULL +24) mais
  l'OOS reste négatif (−41.2) — à revisiter si une config ETH émerge.
- `sb_filter` : quasi-dégénéré (2-10 trades OOS) — fenêtres 1 h trop
  étroites pour 4h/1h ; `sb_bonus` = bruit.
- Judas : inerte sur 1h/4h hors bonus marginal (le Judas vit sur des TF
  intra-journaliers plus fins).

## [SMC-12/13/14] IPDA, Mitigation Blocks, AMD ancré sessions (2026-07-12)

`pd_mode="ipda"` (+`ipda_lookback` 20/40/60), champ moteur additif
`subtype: ob|mitigation` + `mitigation_mode` (off|exclude|penalize),
`amd_session_anchored` (compression = session Asie 00-07 UTC + sweep en
killzone). Baseline = config courante (inducement actif sur BTC 4h).

Points saillants (8 modes × BTC/ETH × 4h/1h) :

| Slot | Mode | FULL | IS pnl/PF | OOS pnl/PF |
|---|---|---:|---|---|
| BTC 4h | baseline | +326.8 | +251.4 / 2.71 | +75.4 / 1.88 |
| BTC 4h | ipda20/40/60 | +76 à +137 | dégradé | −4.7 à +21.9 |
| BTC 4h | mit_exclude | +314.5 | +252.4 / **3.01** | +62.1 / 1.81 |
| BTC 4h | amd_anchored | +302.6 | +234.5 / 2.55 | +68.1 / 1.74 |
| ETH 4h | mit_exclude | **+19.9** | +48.6 / 1.29 | −28.7 / 0.79 |
| ETH 1h | mit_exclude | −141.2 | −124.4 / 0.65 | **−16.8 / 0.86** |
| ETH 4h | ipda20 | −23.3 | +26.2 / 1.11 | −49.5 / 0.60 |

**Verdict : tout reste OFF.**
- IPDA : le dealing range par swing est PORTEUR de la config BTC 4h
  déployée (la remplacer démolit le FULL). Aucun lookback ne gagne en OOS.
- `mitigation_mode=exclude` : le signal le plus intéressant de la campagne —
  transforme ETH 4h (FULL −73 → +20) et divise la perte OOS d'ETH 1h par 3,
  IS PF BTC 4h 2.71 → 3.01… mais coûte de l'OOS sur BTC 4h (−13) et aucun
  slot ETH ne devient positif en OOS. À réévaluer en tête de liste lors
  d'une future calibration ETH (avec l'optimiseur, `mitigation_mode` est
  dans le param_space).
- `amd_session_anchored` : moins bon que la compression générique sur le
  slot où amd_bonus est actif (BTC 4h) ; inerte ailleurs.

## [SMC-09/10 + BT-10] Fast Analyse SMC + grilles + slippage taille (2026-07-12)

- **SMC-09** : famille « smc » dans `fast_analysis.build_signals` — signaux
  « SMC sweep rejeté » et « SMC OB retest » edge-triggered depuis
  `smc.analyze`, TP = première cible de liquidité opposée. Les 9 signaux
  historiques inchangés (test de forme).
- **SMC-10** : `analyze(fee_grid=[(taker,maker)…], period_scales=(0.5,2.0))`
  opt-in — grille de frais par signal + variantes de période « nom ×0.5 » ;
  appel sans nouvel argument → byte-identique.
- **BT-10** : `backtest.slippage_model: "size"` (+`slippage_k`, défaut
  "static" = byte-identique) — coût d'impact
  `notional × spread_pct × k × (notional / volume_quote_moyen_20b)`
  (linéaire en participation, quadratique en notionnel), appliqué à
  l'entrée, aux scale-ins et à la sortie.

Écart mesuré (smart_money BTC 4h, volume moyen ≈ 25 M USDC/barre, k=1) :

| Capital | FULL off | FULL size | Δ | Sharpe off→size |
|---:|---:|---:|---:|---|
| 1 000 | +326.8 | +326.8 | 0 % | 8.87 → 8.87 |
| 1 M | +326 833 | +326 187 | −0.2 % | 8.87 → 8.84 |
| 20 M | +6.54 M | +6.28 M | −3.9 % | 8.87 → 8.30 |

**Verdict : OFF par défaut.** Au capital actuel (10³ USDC, participation
~10⁻⁶ du volume d'une barre), l'impact est strictement nul — le modèle
statique reste fidèle. Le modèle « size » devient matériel au-delà de ~1 M
de capital : à activer quand le bot gérera un notionnel significatif.

## [SMC-02 + SMC-15] Profil du moteur + index OB HTF vizion (2026-07-12)

**SMC-02 — profil (partie « PROFILER d'abord » de la directive) : CONFIRMÉ.**

| BTC | n barres | temps analyze | µs/barre |
|---|---:|---:|---:|
| 4h | 3 900 / 7 800 / 15 601 | 88 / 253 / 794 ms | 22.6 / 32.5 / 50.9 |
| 1h | 12 809 / 25 619 / 51 238 | 365 / 1 362 / 4 097 ms | 28.5 / 53.1 / 80.0 |

Cause identifiée par sonde interne : les listes `active_*` scannées à chaque
barre croissent sans borne (zones jamais retouchées) — entre les barres
4 000 et 48 000 du 1h : rejections 53→178, obs 28→117, fvgs 32→121,
breakers 18→83 (somme ~150→600 items scannés/barre). Les listes
cumulatives (swings 10 395, obs 2 046) ne sont accédées que par tranches
bornées (`[-4:]`, `[-12:]`) — hors de cause.

Le correctif (index triés par niveau + suppression paresseuse, byte-identique
sur `touched_at`/`invalidated_at`/`swept_at`) reste À FAIRE dans un chantier
dédié : 6 types d'entités × 2 polarités × 2 seuils (touch/invalidation)
chacun — trop risqué à glisser en fin de vague. Coût actuel absolu :
0.8 s (4h) / 4.1 s (1h) par analyse complète, une fois par backtest —
gênant pour l'optimiseur multi-symboles, pas bloquant en live.

**SMC-15 — fait.** `vizion._active_htf_obs` mémoïsé par bucket HTF
(des dizaines de barres LTF partagent le même `hidx`, et `_signals`
rappelait le refiltrage complet de `_all_obs` pour CHAQUE candidat →
O(événements_LTF × OB_HTF) ; coût amorti O(1) désormais). Sortie vérifiée
strictement identique sur BTC 4h réel ; tests test_vizion.py verts.

**SMC-02 — clôturé (2026-07-13).** Deux hypothèses testées avec
correctif appliqué UNIQUEMENT si le gain est mesuré :

1. *Suppression paresseuse* (reconstruction filtrée au lieu de N appels
   `list.remove()`) : implémentée, vérifiée byte-identique, **mesurée
   neutre à légèrement négative** (`list.remove()` en C avec fast-path
   d'identité est déjà aussi rapide qu'un filtre Python) → **rejetée**,
   codebase laissé inchangé sur ce point.
2. *Profilage fin par bloc* (13 timers internes, BTC 1h 51 238 barres) :
   confirme que 5 blocs de cycle de vie (rejections 27.4 %, FVG 20.2 %,
   OB 17.7 %, breakers 10.5 %, pool sweep 6.7 % — 82.6 % du total)
   dominent, et que le coût réel est la ré-extraction scalaire numpy
   (`h[i]`/`l[i]`/`c[i]`/`o[i]`) À CHAQUE item actif de CHAQUE bloc,
   alors que ce sont des valeurs invariantes pour toute la barre.
   **Correctif appliqué** : hoisting de `h_i/l_i/c_i/o_i` (4 floats
   Python) une fois en tête de la boucle `for i in range(n)`, réutilisés
   partout où le code lisait `h[i]/l[i]/c[i]/o[i]` (44 sites) — les accès
   à d'autres index (`h[i-1]`, `h[pi]`, `o[run_start]`…) restent des
   accès numpy inchangés.

| BTC/ETH | n barres | avant | après | accélération |
|---|---:|---:|---:|---:|
| BTC 4h | 15 601 | 1 018 ms | 617 ms | ×1.65 |
| BTC 1h | 51 238 | 6 002 ms | 3 294 ms | **×1.82** |
| ETH 4h | 15 601 | 937 ms | 507 ms | ×1.85 |
| ETH 1h | 46 520 | 3 900 ms | 2 201 ms | ×1.77 |

Sortie strictement identique vérifiée (comparaison profonde dict+numpy,
tous champs, contre `git HEAD`) sur 12 combinaisons symbole×TF×taille.
Régression permanente : `tests/test_smc.py::TestAnalyzeSnapshotRegression`
(empreinte figée sur dataset déterministe + test de déterminisme
inter-appels). Reliquat restant (non traité, gain marginal décroissant
au vu du profil résiduel — dominé par les lookups dict Python, pas par
la donnée numpy) : remplacement des scans par index triés/bisect —
chantier plus lourd, documenté comme optionnel, pas engagé faute de
gain/risque favorable à ce stade.
