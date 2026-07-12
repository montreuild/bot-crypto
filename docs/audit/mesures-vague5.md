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
