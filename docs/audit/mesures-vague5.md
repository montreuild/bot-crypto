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
