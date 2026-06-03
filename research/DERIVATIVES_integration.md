# Intégration de données de dérivés (gratuites) pour l'edge directionnel

> Pourquoi : la chasse à l'edge directionnel (`research/directional_hunt.py`) a
> montré que **la direction OHLCV est faiblement prévisible** (AUC OOS ≈ 0.52) et
> que le seul edge robuste — la **mean-reversion sur la position-dans-le-range**
> (P(up)≈57 %, z=7.6) — **n'est PAS monétisable seul** : à payoff asymétrique
> (petit TP, gros stop sur cassure de range), il faut **71 % de win** pour être
> rentable, on en mesure **59 %** (backtest derivatives_reversion : ≈ breakeven).
>
> Conclusion : **pour rendre la direction rentable, il faut filtrer les fausses
> reversions (cassures de range)** — et cette information vit dans les **dérivés**
> (funding, open interest, sentiment, order-flow), pas dans l'OHLCV.

---

## 1. Sources GRATUITES intégrées (sans clé API)

Module : `app/core/derivatives.py` → `DerivativesStore` (cache Parquet, polars,
thread-safe, **dégradation gracieuse** si réseau indisponible).

| Métrique | Source gratuite | Accès | Historique |
|----------|-----------------|-------|-----------|
| **funding_rate** | Binance perp | `ccxt.fetch_funding_rate_history` | **Plusieurs années** ✅ |
| **open_interest** | Binance perp | `ccxt.fetch_open_interest_history` | ~30 jours ⚠️ |
| **long_short_ratio** | Binance futures-data | REST `globalLongShortAccountRatio` | ~30 jours ⚠️ |
| **taker_buy_sell_ratio** | Binance futures-data | REST `takerlongshortRatio` | ~30 jours ⚠️ |

- **Aucune clé API requise** (endpoints publics). ccxt est déjà une dépendance.
- **Liquidations** : pas d'historique gratuit fiable (REST restreint). En live,
  flux websocket `!forceOrder@arr` (gratuit) → à brancher séparément si besoin.
- ⚠️ **Limite des 30 jours** (OI / LS / taker) : impossible de backtester ces
  métriques sur plusieurs années depuis le gratuit. Deux options : (a) accumuler
  localement au fil de l'eau (le cache Parquet du module le permet), (b) source
  payante (Coinglass/Coinalyze/Glassnode) pour l'historique long. Le **funding**,
  lui, a un historique long → backtestable.

> Note environnement : la validation live n'a pas pu être faite dans le bac à
> sable de développement (IP géo-bloquée par les exchanges, 403). Elle se fait
> dans ton déploiement, où le bot atteint déjà Binance pour l'OHLCV.

## 2. Thèses directionnelles (où est l'alpha)

- **Funding reversion** (le plus fort) : funding très positif = longs surchargés
  qui *paient* pour être long → pression de **reversion baissière** (et inverse).
  `funding_z` (z-score roulant) extrême = signal contrarian fort.
- **Sentiment extrême** : `long_short_ratio` / `taker_buy_sell_ratio` aux extrêmes
  = euphorie/panique de la foule → fade (contrarian).
- **OI + prix** : OI↑ & prix↑ = trend sain ; OI↑ & prix↓ = shorts agressifs
  (risque de short-squeeze) ; OI↓ = désengagement (reversion probable).
- Ces signaux servent à **confirmer ou opposer un veto** au fade de range : on ne
  fade un extrême que si le sentiment confirme l'excès (→ lève le win-rate vers la
  zone rentable). C'est la pièce qui manquait à l'OHLCV pur.

## 3. Stratégie consommatrice : `derivatives_reversion`

`app/strategies/derivatives_reversion.py` (rule-based, zéro ML) :
- **Cœur OHLCV** (backtestable) : fade des extrêmes de range en régime range,
  stop au-delà de l'extrême. Edge directionnel mesuré (z=7.6) mais ≈ breakeven seul.
- **Couche dérivés** (live, quand les colonnes sont présentes) :
  - `funding_z ≥ +seuil` (longs surchargés) → **VETO LONG** / confirme SHORT.
  - `funding_z ≤ −seuil` (shorts surchargés) → **VETO SHORT** / confirme LONG.
  - `lsr_z` / `taker_z` extrêmes → boost contrarian.
- **Dégradation gracieuse** : sans colonnes dérivées → OHLCV pur (ne casse rien).

## 4. Câblage (1 ligne, additif, sans risque pour la boucle live)

Enrichir le df OHLCV **avant** de le passer au moteur de scoring. Point d'insertion
naturel : juste après le fetch OHLCV (live pipeline / `ohlcv_cache`), par symbole :

```python
from app.core.derivatives import DerivativesStore
_DERIV = DerivativesStore()                      # singleton process

# ... après avoir obtenu df (pl.DataFrame OHLCV) pour `symbol` :
df = _DERIV.align_to_ohlcv(df, symbol, exchange=robust_exchange,
                           period=timeframe, refresh=True)   # live
# en backtest : refresh=False (lit le cache Parquet si tu as accumulé l'historique)
```

Les stratégies qui savent lire les colonnes (`funding_z`, `lsr_z`, `taker_z`,
`oi_change_pct`) en profitent ; les autres les ignorent. Activer la stratégie :

```yaml
strategies:
  enabled:
    - derivatives_reversion
```

## 5. Prochaine étape recommandée

1. **Accumuler** funding/OI/LS/taker en live (le cache Parquet se remplit) pendant
   quelques semaines → constituer un historique pour backtester la couche dérivés.
2. **Re-mesurer** l'AUC directionnel AVEC funding_z/lsr_z/taker_z (la littérature
   funding-reversion suggère AUC > 0.55) via `research/directional_hunt.py` enrichi.
3. Si confirmé : calibrer les seuils `funding_z_extreme` / `sentiment_z_extreme`
   sur IS/OOS, et envisager un **score P(up) calibré** combinant OHLCV + dérivés.
