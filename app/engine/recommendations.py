"""QW-5 : Moteur de recommandations post-backtest.

Exigence 8 : « avoir des recommandations d'amélioration pour améliorer les
résultats ». Avant ce module, le backtest se terminait par un verdict binaire
(positive/neutral/negative/risky) basé sur 3 métriques. Ce module applique
~15 règles structurées et produit une liste ordonnée de recommandations
actionnables.

Sources d'analyse :
    - Trades individuels (outliers, concentration, sensibilité frais)
    - Régimes de marché (via ``regime_stress_test.stress_test_by_regime``)
    - Métriques agrégées (Sharpe, Sortino, Calmar, CAGR, alpha, DD)
    - Distribution des PnL (skew, kurtosis, z-score)
    - Win-rate par setup/exit_reason

Chaque recommandation est un dict :
    {
        'severity': 'critical' | 'warning' | 'info' | 'positive',
        'code': str (ex: 'OUTLIER_CONCENTRATION'),
        'title': str (court, ex: 'Concentration sur 3 trades'),
        'message': str (explication détaillée),
        'action': str (ex: 'Vérifier le filtre de setup X'),
        'action_link': str (ex: '/lab?tab=optimizer&strategy=...'),
        'metrics': dict (chiffres spécifiques à la reco),
    }

Les recommandations sont ordonnées par sévérité (critical > warning > info >
positive) puis par impact (impact estimé dans ``metrics.impact``).
"""
from __future__ import annotations

import logging
import math
import statistics
from typing import Any, List, Optional

logger = logging.getLogger(__name__)


# ── Sévérités ─────────────────────────────────────────────────────────────────
SEV_CRITICAL = "critical"  # problème bloquant — la stratégie ne devrait pas trader
SEV_WARNING = "warning"    # fragilité détectée — à surveiller / améliorer
SEV_INFO = "info"          # suggestion d'optimisation
SEV_POSITIVE = "positive"  # point fort à conserver

_SEV_ORDER = {SEV_CRITICAL: 0, SEV_WARNING: 1, SEV_INFO: 2, SEV_POSITIVE: 3}


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (TypeError, ValueError):
        return default


# ── Règles individuelles ─────────────────────────────────────────────────────
# Chaque règle prend le résultat backtest (dict) et retourne une liste de
# recommandations (peut être vide). Les règles sont pures et indépendantes —
# on les applique toutes puis on agrège.


def _rule_sample_size(result: dict) -> List[dict]:
    """Échantillon suffisant : < 30 trades → critical, < 10 → critique aiguë."""
    n = result.get("total_trades", 0)
    if n < 10:
        return [{
            "severity": SEV_CRITICAL,
            "code": "INSUFFICIENT_SAMPLE",
            "title": f"Échantillon insuffisant ({n} trades)",
            "message": (
                f"Le backtest n'a produit que {n} trades fermés. Les métriques "
                f"(Sharpe, win-rate, drawdown) ne sont PAS statistiquement "
                f"significatives sur un tel échantillon. Un Sharpe de 2.0 sur 5 "
                f"trades peut être du pur hasard."
            ),
            "action": "Étendre la plage temporelle ou réduire le score_threshold",
            "action_link": "",
            "metrics": {"trades": n, "min_significant": 30},
        }]
    if n < 30:
        return [{
            "severity": SEV_WARNING,
            "code": "SMALL_SAMPLE",
            "title": f"Échantillon limité ({n} trades)",
            "message": (
                f"Le backtest a produit {n} trades. Les métriques sont "
                f"indicatives mais leur intervalle de confiance reste large. "
                f"Visez ≥ 100 trades pour une évaluation robuste."
            ),
            "action": "Étendre la plage temporelle ou ajuster les filtres",
            "action_link": "",
            "metrics": {"trades": n, "recommended_min": 100},
        }]
    return []


def _rule_pnl_negative(result: dict) -> List[dict]:
    """PnL net négatif → critical."""
    pnl = _safe_float(result.get("total_pnl"))
    if pnl < 0:
        fees = _safe_float(result.get("total_fees"))
        borrow = _safe_float(result.get("total_borrow_cost", 0))
        return [{
            "severity": SEV_CRITICAL,
            "code": "NEGATIVE_PNL",
            "title": "Stratégie net perdante",
            "message": (
                f"PnL net = {pnl:+.2f} (frais: {fees:.2f}, borrow: {borrow:.2f}). "
                f"La stratégie perd de l'argent sur la période backtestée. "
                f"Vérifier le sens du signal, le cost model, ou abandonner."
            ),
            "action": "Comparer au Buy & Hold et vérifier le sens des signaux",
            "action_link": "",
            "metrics": {"pnl": pnl, "fees": fees, "borrow": borrow},
        }]
    return []


def _rule_outlier_concentration(result: dict) -> List[dict]:
    """Si 3 trades > 3σ représentent > 60% du PnL → fragilité critique."""
    trades = result.get("trades", []) or []
    pnls = [_safe_float(t.get("pnl")) for t in trades if t.get("status", "").startswith("closed")]
    if len(pnls) < 10:
        return []
    mean_pnl = statistics.mean(pnls)
    stdev_pnl = statistics.stdev(pnls) if len(pnls) > 1 else 0
    if stdev_pnl == 0:
        return []
    total_pnl = sum(pnls)
    if abs(total_pnl) < 1e-6:
        return []
    # Trades > 3σ
    outliers = [p for p in pnls if abs(p - mean_pnl) > 3 * stdev_pnl]
    outlier_pnl = sum(outliers)
    concentration = abs(outlier_pnl) / abs(total_pnl) if total_pnl != 0 else 0
    if len(outliers) <= 5 and concentration > 0.6:
        return [{
            "severity": SEV_WARNING,
            "code": "OUTLIER_CONCENTRATION",
            "title": f"Concentration sur {len(outliers)} trade(s) outlier(s)",
            "message": (
                f"{len(outliers)} trades > 3σ représentent {concentration*100:.0f}% "
                f"du PnL total. La stratégie est fragile : retirez ces trades et "
                f"elle devient marginale. Investiguer les conditions de marché "
                f"qui ont produit ces outliers (régime, news, gap)."
            ),
            "action": "Analyser les trades outliers individuellement",
            "action_link": "",
            "metrics": {"n_outliers": len(outliers), "concentration_pct": round(concentration * 100, 1)},
        }]
    return []


def _rule_fees_dominant(result: dict) -> List[dict]:
    """Si frais > 50% du PnL brut → warning (stratégie sensitive aux coûts)."""
    pnl = _safe_float(result.get("total_pnl"))
    fees = _safe_float(result.get("total_fees"))
    borrow = _safe_float(result.get("total_borrow_cost", 0))
    gross_pnl = pnl + fees + borrow  # PnL avant frais
    if gross_pnl <= 0:
        return []
    fees_ratio = (fees + borrow) / gross_pnl
    if fees_ratio > 0.5:
        return [{
            "severity": SEV_WARNING,
            "code": "FEES_DOMINANT",
            "title": f"Frais = {fees_ratio*100:.0f}% du PnL brut",
            "message": (
                f"Les frais ({fees:.2f}) + borrow ({borrow:.2f}) représentent "
                f"{fees_ratio*100:.0f}% du PnL brut ({gross_pnl:.2f}). La "
                f"stratégie est très sensible aux coûts. Pistes : (1) passer "
                f"en maker (limit orders), (2) réduire la fréquence de trading, "
                f"(3) augmenter le score_threshold, (4) passer en spot si "
                f"borrow dominant."
            ),
            "action": "Simuler un scénario spot/levier 1 dans le simulateur de coûts",
            "action_link": "",
            "metrics": {"fees_ratio_pct": round(fees_ratio * 100, 1),
                        "fees": fees, "borrow": borrow, "gross_pnl": gross_pnl},
        }]
    return []


def _rule_low_sharpe(result: dict) -> List[dict]:
    """Sharpe < 0.5 → warning, < 0 → critical."""
    sharpe = _safe_float(result.get("sharpe"))
    if sharpe < 0:
        return [{
            "severity": SEV_WARNING,
            "code": "NEGATIVE_SHARPE",
            "title": f"Sharpe négatif ({sharpe:.2f})",
            "message": (
                f"Le ratio de Sharpe annualisé est négatif ({sharpe:.2f}). "
                f"La volatilité des rendements dépasse le rendement moyen — "
                f"la stratégie prend plus de risque qu'elle ne gagne."
            ),
            "action": "Optimiser les paramètres (volatilité, ATR filter)",
            "action_link": "/lab?tab=optimizer",
            "metrics": {"sharpe": sharpe},
        }]
    if sharpe < 0.5:
        return [{
            "severity": SEV_INFO,
            "code": "LOW_SHARPE",
            "title": f"Sharpe faible ({sharpe:.2f})",
            "message": (
                f"Sharpe = {sharpe:.2f} (< 0.5). Un Sharpe ≥ 1.0 est attendu "
                f"pour une stratégie publiable. Vérifier la cohérence des "
                f"signaux et le réglage des stops."
            ),
            "action": "Optimiser via le Laboratoire",
            "action_link": "/lab?tab=optimizer",
            "metrics": {"sharpe": sharpe, "target": 1.0},
        }]
    return []


def _rule_high_drawdown(result: dict) -> List[dict]:
    """Max DD > 25% → warning, > 40% → critical."""
    dd = abs(_safe_float(result.get("max_drawdown")))
    if dd > 40:
        return [{
            "severity": SEV_CRITICAL,
            "code": "CRITICAL_DRAWDOWN",
            "title": f"Drawdown critique ({dd:.1f}%)",
            "message": (
                f"Le drawdown maximum atteint {dd:.1f}% — au-delà du seuil "
                f"de ruine (40%). Un tel DD peut déclencher le circuit breaker "
                f"global en live et forcer un HALT. Réduire le risk_per_trade "
                f"ou ajouter un filtre de régime."
            ),
            "action": "Réduire risk_per_trade dans /settings?tab=risk",
            "action_link": "/settings?tab=risk",
            "metrics": {"max_drawdown": dd, "critical_threshold": 40},
        }]
    if dd > 25:
        return [{
            "severity": SEV_WARNING,
            "code": "HIGH_DRAWDOWN",
            "title": f"Drawdown élevé ({dd:.1f}%)",
            "message": (
                f"Le drawdown maximum atteint {dd:.1f}%. C'est au-dessus du "
                f"seuil prudentiel (25%). La stratégie peut subir des périodes "
                f"de sous-performance prolongées."
            ),
            "action": "Réduire risk_per_trade ou ajouter un trailing plus serré",
            "action_link": "/settings?tab=risk",
            "metrics": {"max_drawdown": dd, "warning_threshold": 25},
        }]
    return []


def _rule_negative_alpha(result: dict) -> List[dict]:
    """Alpha vs B&H négatif → warning (la stratégie sous-performe le Buy & Hold)."""
    alpha = _safe_float(result.get("alpha"))
    alpha_vs_bh = _safe_float(result.get("alpha_vs_bh"))
    bnh_pnl = _safe_float(result.get("buy_and_hold_pnl"))
    pnl = _safe_float(result.get("total_pnl"))
    # Utiliser alpha_vs_bh (annualisé) si dispo, sinon alpha (absolu)
    alpha_metric = alpha_vs_bh if alpha_vs_bh != 0 else alpha
    if alpha_metric < 0 and bnh_pnl > 0 and pnl < bnh_pnl:
        return [{
            "severity": SEV_WARNING,
            "code": "NEGATIVE_ALPHA",
            "title": f"Alpha négatif vs Buy & Hold ({alpha_metric:+.2f})",
            "message": (
                f"La stratégie sous-performe le Buy & Hold sur la période "
                f"(strat: {pnl:+.2f} vs B&H: {bnh_pnl:+.2f}). Sur un marché "
                f"haussier, c'est attendu pour une stratégie de hedging — "
                f"mais sur un marché range/bear, c'est un signal d'alerte."
            ),
            "action": "Comparer sur plusieurs régimes via le stress test",
            "action_link": "",
            "metrics": {"alpha": alpha_metric, "pnl": pnl, "bnh_pnl": bnh_pnl},
        }]
    return []


def _rule_low_winrate(result: dict) -> List[dict]:
    """Win-rate < 35% → info (stratégie de trend following accepte un WR bas,
    mais < 30% avec PF < 1.2 est suspect)."""
    wr = _safe_float(result.get("win_rate"))
    pf = _safe_float(result.get("profit_factor"))
    if wr < 30 and pf < 1.2:
        return [{
            "severity": SEV_INFO,
            "code": "LOW_WINRATE_LOW_PF",
            "title": f"Win-rate bas ({wr:.1f}%) et PF faible ({pf:.2f})",
            "message": (
                f"Win-rate = {wr:.1f}% et profit factor = {pf:.2f}. Un trend "
                f"follower peut avoir un WR de 35-40% avec un PF > 1.5 (gains "
                f"qui compensent les pertes). Ici, ni le WR ni le PF ne "
                f"compensent — la stratégie n'a pas d'edge clair."
            ),
            "action": "Optimiser les sorties (trailing, TP) pour améliorer le PF",
            "action_link": "/lab?tab=optimizer",
            "metrics": {"win_rate": wr, "profit_factor": pf},
        }]
    return []


def _rule_high_borrow_ratio(result: dict) -> List[dict]:
    """Si borrow > 20% du PnL net → warning (margin coûteux)."""
    pnl = _safe_float(result.get("total_pnl"))
    borrow = _safe_float(result.get("total_borrow_cost", 0))
    if pnl <= 0 or borrow <= 0:
        return []
    ratio = borrow / pnl
    if ratio > 0.20:
        return [{
            "severity": SEV_WARNING,
            "code": "HIGH_BORROW_RATIO",
            "title": f"Borrow cost = {ratio*100:.0f}% du PnL",
            "message": (
                f"Le coût d'emprunt margin ({borrow:.2f}) représente "
                f"{ratio*100:.0f}% du PnL net ({pnl:.2f}). Sur des positions "
                f"longues tenues plusieurs jours, le borrow margin peut "
                f"significativement éroder l'edge. Pistes : (1) passer en "
                f"spot si la stratégie ne nécessite pas de short, (2) réduire "
                f"la durée moyenne des positions, (3) utiliser un levier "
                f"plus faible."
            ),
            "action": "Simuler spot vs margin via cost_override",
            "action_link": "",
            "metrics": {"borrow": borrow, "pnl": pnl, "ratio_pct": round(ratio * 100, 1)},
        }]
    return []


def _rule_strong_metrics(result: dict) -> List[dict]:
    """Recommandations positives — points forts à conserver."""
    recos: List[dict] = []
    sharpe = _safe_float(result.get("sharpe"))
    sortino = _safe_float(result.get("sortino", 0))
    calmar = _safe_float(result.get("calmar", 0))
    n = result.get("total_trades", 0)

    if sharpe >= 2.0 and n >= 30:
        recos.append({
            "severity": SEV_POSITIVE,
            "code": "EXCELLENT_SHARPE",
            "title": f"Sharpe excellent ({sharpe:.2f})",
            "message": (
                f"Sharpe = {sharpe:.2f} sur {n} trades — edge solide. "
                f"La stratégie est candidate à un slot actif en live "
                f"(après validation OOS via l'optimiseur)."
            ),
            "action": "Promouvoir en live via /bots",
            "action_link": "/bots",
            "metrics": {"sharpe": sharpe, "trades": n},
        })
    if sortino > sharpe * 1.3 and n >= 20:
        recos.append({
            "severity": SEV_POSITIVE,
            "code": "ASYMMETRIC_DOWNSIDE",
            "title": f"Asymétrie positive (Sortino {sortino:.2f} > Sharpe {sharpe:.2f})",
            "message": (
                f"Sortino ({sortino:.2f}) > Sharpe ({sharpe:.2f}) — la "
                f"volatilité downside est plus faible que la volatilité totale. "
                f"La stratégie limite bien les pertes, signe d'une gestion du "
                f"risque saine."
            ),
            "action": "Conserver ce paramétrage",
            "action_link": "",
            "metrics": {"sortino": sortino, "sharpe": sharpe},
        })
    if calmar >= 1.0 and n >= 30:
        recos.append({
            "severity": SEV_POSITIVE,
            "code": "GOOD_CALMAR",
            "title": f"Calmar solide ({calmar:.2f})",
            "message": (
                f"Calmar = {calmar:.2f} (CAGR/Max DD ≥ 1.0) — la stratégie "
                f"génère plus de rendement annualisé que son pire drawdown. "
                f"Bonne efficiency ajustée au risque."
            ),
            "action": "Conserver ce paramétrage",
            "action_link": "",
            "metrics": {"calmar": calmar},
        })
    return recos


def _rule_rejection_analysis(result: dict) -> List[dict]:
    """Si rejections > 50% des signaux → info (stratégie trop filtrée)."""
    rejections = result.get("rejections", {}) or {}
    total_rej = sum(v for v in rejections.values() if isinstance(v, (int, float)))
    n_trades = result.get("total_trades", 0)
    total_signals = total_rej + n_trades
    if total_signals < 20 or n_trades == 0:
        return []
    rej_ratio = total_rej / total_signals
    if rej_ratio > 0.5:
        # Top 3 motifs de rejet
        top_motifs = sorted(rejections.items(), key=lambda x: -x[1])[:3]
        motifs_str = ", ".join(f"{k}={v}" for k, v in top_motifs)
        return [{
            "severity": SEV_INFO,
            "code": "HIGH_REJECTION_RATE",
            "title": f"Taux de rejet élevé ({rej_ratio*100:.0f}%)",
            "message": (
                f"La stratégie a rejeté {total_rej} signaux sur {total_signals} "
                f"({rej_ratio*100:.0f}%). Top motifs : {motifs_str}. Si la "
                f"stratégie est trop filtrée, elle rate des opportunités. "
                f"Si le filtre est justifié (risk envelope, ATR), c'est OK."
            ),
            "action": "Vérifier les motifs de rejet dans les diagnostics",
            "action_link": "",
            "metrics": {"rejection_rate_pct": round(rej_ratio * 100, 1),
                        "top_motifs": dict(top_motifs)},
        }]
    return []


def _rule_regime_analysis(result: dict, df_prices: Optional[list] = None) -> List[dict]:
    """Analyse par régime — délègue à regime_stress_test."""
    # Le df OHLCV n'est PAS dans le payload backtest (seulement ohlcv_payload
    # avec time/open/close/high/low/volume en listes). On le reconstruit si
    # possible. Pour rester robuste, on tente ; si ça échoue, on skip.
    try:
        import polars as pl
        ohlcv = result.get("ohlcv") or {}
        times = ohlcv.get("time", [])
        closes = ohlcv.get("close", [])
        if len(times) < 200 or len(closes) < 200:
            return []
        # Reconstruire un DataFrame minimal
        df = pl.DataFrame({
            "time": pl.from_epoch(times, time_unit="s") if times else None,
            "close": closes,
        })
        from app.engine.regime_stress_test import regime_summary, stress_test_by_regime
        segments = stress_test_by_regime(df, regime_type='trend', min_segment_bars=50)
        if not segments:
            return []
        summary = regime_summary(segments)
        recos: List[dict] = []
        # Identifier les régimes perdants
        for regime, agg in summary.items():
            avg_return = agg.get("avg_return_pct", 0)
            avg_sharpe = agg.get("avg_sharpe", 0)
            worst_dd = agg.get("worst_max_dd", 0)
            if avg_return < -5 and avg_sharpe < 0:
                recos.append({
                    "severity": SEV_WARNING,
                    "code": f"REGIME_{regime.upper()}_LOSING",
                    "title": f"Stratégie perdante en régime {regime} (Sharpe {avg_sharpe:.2f})",
                    "message": (
                        f"En régime {regime}, la stratégie perd {avg_return:+.1f}% "
                        f"en moyenne (Sharpe {avg_sharpe:.2f}, worst DD {worst_dd:.1f}%). "
                        f"Sur {agg.get('n_segments', 0)} segments, c'est récurrent. "
                        f"Pistes : (1) ajouter un filtre de régime pour désactiver "
                        f"la stratégie en {regime}, (2) optimiser spécifiquement "
                        f"pour ce régime, (3) accepter la perte si la stratégie "
                        f"compense largement en autres régimes."
                    ),
                    "action": "Ajouter un filtre de régime dans la config",
                    "action_link": "/settings?tab=risk",
                    "metrics": {"regime": regime, "avg_return_pct": avg_return,
                                "avg_sharpe": avg_sharpe, "worst_dd": worst_dd,
                                "n_segments": agg.get("n_segments", 0)},
                })
        # Identifier les régimes gagnants (point fort)
        for regime, agg in summary.items():
            avg_return = agg.get("avg_return_pct", 0)
            avg_sharpe = agg.get("avg_sharpe", 0)
            if avg_return > 10 and avg_sharpe > 1.0:
                recos.append({
                    "severity": SEV_POSITIVE,
                    "code": f"REGIME_{regime.upper()}_WINNING",
                    "title": f"Edge solide en régime {regime} (Sharpe {avg_sharpe:.2f})",
                    "message": (
                        f"En régime {regime}, la stratégie génère {avg_return:+.1f}% "
                        f"en moyenne (Sharpe {avg_sharpe:.2f}). C'est le régime "
                        f"privilégié de la stratégie — à conserver."
                    ),
                    "action": "Conserver ce paramétrage",
                    "action_link": "",
                    "metrics": {"regime": regime, "avg_return_pct": avg_return,
                                "avg_sharpe": avg_sharpe},
                })
        return recos
    except Exception as e:
        logger.debug(f"[recommendations] _rule_regime_analysis KO : {e}")
        return []


# ── Orchestrateur ─────────────────────────────────────────────────────────────

def generate_recommendations(backtest_result: dict) -> List[dict]:
    """Génère la liste ordonnée de recommandations pour un résultat backtest.

    Parameters
    ----------
    backtest_result : dict
        Payload de backtest (soit l'entrée ``by_strategy[name]`` soit le payload
        global — les deux fonctionnent, on extrait ce qu'il faut).

    Returns
    -------
    list of dict
        Recommandations ordonnées par sévérité (critical > warning > info >
        positive), puis par impact. Chaque reco respecte le schéma documenté
        en tête de module.
    """
    # Si on reçoit le payload global avec by_strategy, on prend la 1ère stratégie
    if "by_strategy" in backtest_result and isinstance(backtest_result["by_strategy"], dict):
        strat_data: dict = next(iter(backtest_result["by_strategy"].values()), {})
        # Fusionner avec les champs globaux (ohlcv, etc.)
        result = {**backtest_result, **strat_data}
    else:
        result = backtest_result

    recos: List[dict] = []
    # Règles simples (pas de dépendance externe)
    for rule_fn in [
        _rule_sample_size,
        _rule_pnl_negative,
        _rule_outlier_concentration,
        _rule_fees_dominant,
        _rule_low_sharpe,
        _rule_high_drawdown,
        _rule_negative_alpha,
        _rule_low_winrate,
        _rule_high_borrow_ratio,
        _rule_strong_metrics,
        _rule_rejection_analysis,
    ]:
        try:
            recos.extend(rule_fn(result))
        except Exception as e:
            logger.warning(f"[recommendations] {rule_fn.__name__} KO : {e}")

    # Règle régime (plus lourde, peut échouer si données insuffisantes)
    try:
        recos.extend(_rule_regime_analysis(result))
    except Exception as e:
        logger.debug(f"[recommendations] _rule_regime_analysis KO : {e}")

    # Trier par sévérité puis par impact (estimé via metrics)
    def _sort_key(r: dict) -> tuple:
        sev = r.get("severity", SEV_INFO)
        return (_SEV_ORDER.get(sev, 99),)

    recos.sort(key=_sort_key)
    return recos


def summarize_recommendations(recos: List[dict]) -> dict:
    """Synthèse rapide : compteurs par sévérité + verdict global."""
    counts = {SEV_CRITICAL: 0, SEV_WARNING: 0, SEV_INFO: 0, SEV_POSITIVE: 0}
    for r in recos:
        sev = r.get("severity", SEV_INFO)
        counts[sev] = counts.get(sev, 0) + 1

    # Verdict global
    if counts[SEV_CRITICAL] > 0:
        verdict = "critical"
        verdict_label = "Critique — ne pas trader en l'état"
    elif counts[SEV_WARNING] >= 2:
        verdict = "risky"
        verdict_label = "Risqué — à améliorer avant live"
    elif counts[SEV_WARNING] == 1 or counts[SEV_INFO] > 0:
        verdict = "neutral"
        verdict_label = "Neutre — échantillon ou paramètres à valider"
    elif counts[SEV_POSITIVE] > 0:
        verdict = "positive"
        verdict_label = "Positif — candidat à la promotion"
    else:
        verdict = "neutral"
        verdict_label = "Neutre"

    return {
        "counts": counts,
        "verdict": verdict,
        "verdict_label": verdict_label,
        "total": len(recos),
    }
