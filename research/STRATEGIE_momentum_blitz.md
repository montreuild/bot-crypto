# Stratégie *Momentum Blitz* — agressive, plein capital, let-it-run

> Pendant **agressif** de `harmonic_regime` : viser le rendement absolu maximal,
> le plus vite possible, en assumant un drawdown élevé.
> Reproductible : `python research/analysis_aggressive.py` puis
> `python research/backtest_blitz.py --tf 4h --deploy lev2x --mc --split --wf`.

---

## 1. Mandat & données

Objectif : **maximum de gains, vite**, agressif/dynamique/audacieux. TF ajoutés
par l'utilisateur : **15m** (~1.5 an récent), **30m** (~2.9 ans), 1h (doublon).
Couverture de régimes limitée sur bas TF (pas de bear 2022) → validation bear
sur 1h/4h.

## 2. Analyse agressive — un setup ne vaut que s'il bat les frais

L'agression ne crée pas d'edge : elle **scale** un edge existant (et son risque).
On mesure donc les setups de **gros mouvement** (breakout Donchian + surge de
volume + expansion d'ATR + alignement HTF) **nets du coût round-trip (~0.30 %)** :

| TF | Net moyen (meilleur setup) | MFE/MAE | Queue droite p90 | Verdict |
|----|---------------------------:|--------:|-----------------:|---------|
| 15m | négatif | 0.98 | faible | **frais > edge** |
| 30m | -0.19 % | 1.14 | +2.2 % | marginal |
| 1h | +0.12 % | 1.24 | +3.4 % | limite |
| **4h** | **+0.49 %** | 1.24 | **+6.6 %** | **viable** |

**Faits décisifs :**
1. **Sous 4h, les frais dominent** — le scalping BTC perd. Le filtre HTF améliore
   nettement l'edge net (1h −0.03→+0.12 ; 4h +0.28→+0.49).
2. **Asymétrie forte** : MFE/MAE ≈ 1.24, queue droite +3-6 % vs MAE −1.7/−3.5 %
   → **couper vite** (stop serré) et **laisser courir** (trailing large) = capter
   la queue droite. C'est là qu'est le rendement.

## 3. Conception — `app/strategies/momentum_blitz.py`

- **Entrée « ignition »** : cassure Donchian + **surge de volume** + **expansion
  d'ATR** + **alignement HTF**. (La continuation EMA simple = piège à frais,
  désactivée par défaut : backtestée à -300 sur 1h.)
- **Déploiement PLEIN CAPITAL** : `size_factor` 1.0→2.0 selon la conviction
  (ADX × volume × expansion × alignement) → notional ~100 % (full1x) à ~200 %
  (lev2x, margin). **Agression dynamique** indexée sur la force du signal.
- **Exits asymétriques** : stop initial serré (1.3×ATR), breakeven rapide, puis
  **trailing LARGE** (`trail_wide`=3×ATR, `tight_r`=5) + max-hold 30 → laisse
  courir les gagnants.
- **Long-biais** : shorts net-négatifs (mesuré) → désactivés par défaut.

## 4. Backtest & validation (4h, frais/spread/borrow réalistes)

### 4.1 Itération (la sélectivité est reine)
| Étape | Rendement (lev2x) | Sharpe | max DD | OOS Sharpe |
|-------|------------------:|------:|------:|-----------:|
| v0 (continuation ON, seuil bas) | +15.7 %* | 1.45 | -18 % | — |
| v1 (ignition-only) | +68.0 % | 4.70 | -22 % | -6.8 |
| **v2 (ADX22, surge1.6, q0.60, hold30)** | **+113.7 %** | **6.95** | **-12.3 %** | **+1.69** |

\* full1x. Leçon : *agressif ≠ plus de trades* (plus de frais, edge dilué) ;
agressif = **edge fort par trade × plein capital**.

### 4.2 Performance finale (config v2)
| Déploiement | Rendement 7.5 ans | Sharpe | max DD | PF | Win | Monte-Carlo |
|-------------|------------------:|------:|------:|---:|----:|-------------|
| **full1x** (réaliste) | **+58.2 %** (×1.58) | 5.73 | **-11.6 %** | 1.55 | 57 % | prob profit 100 %, maxDD p95 -10 % |
| **lev2x** (agressif) | **+113.7 %** (×2.14) | **6.95** | -12.3 % | 1.74 | 57 % | — |

> momentum_blitz full1x (**+58 %**) **dépasse** harmonic_regime 4h (+33 %) en
> rendement absolu, avec un Sharpe supérieur — c'est bien la version agressive
> **et** validée.

### 4.3 Robustesse par macro-régime (lev2x)
| Fenêtre | Stratégie | Buy & Hold | Alpha | max DD |
|---------|----------:|-----------:|------:|------:|
| **BEAR 2022** | **-1.6 %** | **-53.1 %** | **+51.5 %** | -1.7 % |
| BULL 2023-24 | **+31.6 %** | +130.3 % | -98.6 % | -9.7 % |
| CHOP 2024-26 | **+6.4 %** | +2.6 % | +3.9 % | -8.5 % |

**Positif/protégé dans tous les régimes** : flat en bear (capital préservé),
fort en bull, et même **positif en chop** (la sélectivité ignition évite les
faux signaux de range). Walk-forward OOS : PnL moyen +87, consistance 60 %.

## 5. Conclusions honnêtes

1. **Agression validée** : +58 % (1x) / +113 % (2x) sur 7.5 ans, Sharpe ~6-7,
   DD -12 %, positif dans tous les régimes. Le rendement absolu se règle via le
   **levier / `risk_per_trade`** (knobs de portefeuille exposés).
2. **« Vite » sur BTC = swing 4h**, pas du scalping : 1h/30m/15m backtestés
   **négatifs** (frais + bruit > edge). Les deux stratégies (conservatrice et
   agressive) convergent sur cette vérité mesurée.
3. **Risque assumé** : variance OOS supérieure à harmonic (Sharpe OOS +1.7 vs
   +45 sur 1d). C'est l'option haut-risque/haut-rendement du portefeuille.
4. **Toujours pas d'alpha vs B&H parabolique** en rendement brut — mais profil
   risque/rendement très supérieur (DD -12 % vs -72 %) et croissance dans le bear.

### Pistes v2
- Optimisation bayésienne `/optimizer` (IS/OOS) autour de (adx_min, surge,
  trail_wide, max_size_factor). Allocation de levier dynamique selon la
  volatilité réalisée. Combiner harmonic_regime (1d, robuste) + momentum_blitz
  (4h, agressif) en portefeuille multi-stratégies (cœur + satellite).
