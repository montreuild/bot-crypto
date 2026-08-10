# research/ — Scripts de recherche (archive)

Ce répertoire contient des scripts one-shot écrits pendant la phase de
recherche et développement des stratégies. **Ils ne sont jamais importés
par `app/`** et ne font pas partie du code de production.

## Scripts

| Script | Objet |
|---|---|
| `analysis_btc.py` | Analyse exploratoire BTC |
| `analysis_aggressive.py` | Variantes agressives |
| `analysis_metrics.json` | Métriques d'analyse |
| `backtest_blitz.py` | Backtest momentum_blitz |
| `backtest_harmonic.py` | Backtest harmonic_regime |
| `backtest_pine.py` | Backtest stratégies Pine Script |
| `backtest_reversion.py` | Backtest derivatives_reversion |
| `backtest_smart_trend.py` | Backtest smart_trend_adx |
| `backtest_squeeze.py` | Backtest volatility_squeeze |
| `accumulate_derivatives.py` | Accumulation données dérivés |
| `directional_hunt.py` | Recherche directionnelle |
| `optimize_pine.py` | Optimisation Pine Script |
| `optimize_smart_trend.py` | Optimisation smart_trend_adx |
| `ml10_adx_recalibration.json` | Recalibration ADX |
| `retrain_all_report.json` | Rapport re-entraînement |
| `CRITIQUE_omnibus_v7-v11.md` | Critique famille Opus |
| `DERIVATIVES_integration.md` | Intégration dérivés |
| `RESULTATS_*.md` | Résultats de stratégies |

## Statut

Ces scripts sont conservés pour leur valeur documentaire. Pour toute
nouvelle recherche, créer un script dans `scripts/` (qui est le répertoire
actif pour les outils de maintenance).
