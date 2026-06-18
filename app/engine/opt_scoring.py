"""Scoring des résultats d'optimisation (score composite IS/OOS, surapprentissage).

Extrait de ``optimizer.py`` (découpage V13 : recherche / scoring / persistance).
Module sans état ni dépendance lourde : importable par les workers spawn.
"""
import logging

logger = logging.getLogger(__name__)


# Capital de repli quand le résultat ne porte pas son ``initial_capital``
# (vieux dicts de test, résumés partiels). Sert uniquement d'échelle pour
# convertir un PnL absolu en rendement % — n'affecte jamais le classement au
# sein d'une même optimisation (tous les essais partagent le même capital).
_FALLBACK_CAPITAL = 1000.0


def composite_score(res, min_trades: int = 2) -> float:
    """Score composite d'un résultat de backtest (dict ou BacktestResult).

    Combine Sharpe (borné), win-rate, profit factor, expectancy, drawdown,
    nombre de trades, **rendement %** et alpha vs buy & hold. Retourne -999 si
    moins de ``min_trades`` trades (résultat non significatif).

    Indépendance au budget (Phase 0)
    --------------------------------
    Le score n'utilise plus le **PnL absolu** mais le **rendement** (PnL rapporté
    au capital initial) et l'**expectancy en % du capital**. Toutes les autres
    composantes (Sharpe, win-rate, profit factor, drawdown %, alpha) sont déjà
    des grandeurs sans dimension. Conséquence : deux bots dotés de budgets
    différents mais au comportement identique obtiennent le **même** score, ce
    qui casse la boucle de rétroaction budget → sizing → PnL → score → budget.
    Au sein d'une optimisation donnée (capital constant) ce changement est une
    simple homothétie : le classement des paramétrages est préservé.
    """
    n = res.get("total_trades", 0) if isinstance(res, dict) else res.total_trades
    if n < min_trades:
        return -999.0

    if isinstance(res, dict):
        sharpe = res.get("sharpe", 0)
        wr     = res.get("win_rate", 0) / 100
        pf     = res.get("profit_factor", 0)
        pnl    = res.get("total_pnl", 0)
        dd     = abs(res.get("max_drawdown", -100))
        exp    = res.get("expectancy", 0)
        alpha  = res.get("alpha", 0)
        cap    = res.get("initial_capital") or _FALLBACK_CAPITAL
    else:
        sharpe = res.sharpe
        wr     = res.win_rate / 100
        pf     = res.profit_factor
        pnl    = res.total_pnl
        dd     = abs(res.max_drawdown)
        exp    = res.expectancy
        alpha  = getattr(res, "alpha", 0)
        cap    = getattr(res, "initial_capital", None) or _FALLBACK_CAPITAL

    cap = float(cap) if cap else _FALLBACK_CAPITAL

    if isinstance(pf, str):
        pf = 6.0
    pf = min(float(pf), 6.0)

    trade_factor = min(n / 10, 1.0)
    dd_factor    = max(0, 1.0 - dd / 30)
    alpha_norm   = max(min(alpha / 50.0, 1.0), -1.0)
    alpha_bonus  = alpha_norm * 0.10

    # Sharpe borné : un Sharpe brut non plafonné (souvent >50 sur de petites
    # fenêtres OOS de quelques trades) écrasait tous les autres termes, ce qui
    # poussait l'optimiseur à préférer des paramétrages à 2-3 trades au Sharpe
    # absurde plutôt qu'un PnL nettement supérieur. On le normalise dans [-1, 1]
    # (saturation dès |Sharpe| ≥ 10, déjà excellent) pour qu'il pèse comme les
    # autres métriques au lieu de dominer le score.
    sharpe_norm = max(min(sharpe / 10.0, 1.0), -1.0)

    # Rendement (budget-indépendant) au lieu du PnL absolu : un +9,6 % doit
    # battre un +3,3 % toutes choses égales par ailleurs, quel que soit le
    # capital. Normalisé dans [-1, 1] (saturation à |rendement| ≥ 50 %).
    ret_pct  = pnl / cap * 100.0
    ret_norm = max(min(ret_pct / 50.0, 1.0), -1.0)

    # Expectancy exprimée en % du capital (sans dimension) au lieu d'un montant
    # absolu. Saturation à 3 % de gain moyen par trade.
    exp_pct  = exp / cap * 100.0
    exp_norm = min(exp_pct, 3.0) / 3.0

    # Bundle « qualité » : combine toutes les métriques sans dimension. Ses termes
    # sont structurellement positifs ou faiblement négatifs — il ne renseigne PAS,
    # à lui seul, sur le signe du résultat.
    quality = (
        sharpe_norm     * 0.22 +
        wr              * 0.15 +
        (pf / 6)        * 0.15 +
        exp_norm        * 0.08 +
        dd_factor       * 0.10 +
        trade_factor    * 0.10 +
        ret_norm        * 0.20 +
        alpha_bonus     * 1.00
    )

    # Monotonie avec le rendement (correctif Phase 0).
    # Avant : ``score = quality * ret_sign`` avec ``ret_sign ∈ {1.0, 0.3}`` — une
    # pénalité **multiplicative jamais négative** qui ne faisait qu'atténuer un
    # bundle positif. Conséquence : une stratégie NETTE PERDANTE (PnL < 0) au
    # win-rate/Sharpe/drawdown corrects obtenait un score **positif**, était
    # sélectionnée par l'optimiseur et passait le gate live (MIN_VIABLE_SCORE).
    # Désormais le score est monotone avec le PnL :
    #   - PnL > 0  → bundle qualité (échelle inchangée → rétro-compatible avec les
    #                scores positifs déjà persistés et le seuil -0.05) ;
    #   - PnL ≤ 0  → score = rendement normalisé (négatif, et d'autant plus négatif
    #                que la perte est grande) → toujours rejeté, et correctement
    #                ordonné entre paramétrages perdants.
    if pnl > 0:
        score = quality
    else:
        score = ret_norm

    return round(score, 6)


def overfitting_ratio(is_score: float, oos_score: float) -> float:
    """Ratio IS/OOS (>2.5 = surapprentissage probable). NaN si non significatif."""
    if oos_score <= -990 or is_score <= -990:
        return float('nan')
    if is_score <= 0:
        return 0.0
    return round(min(is_score / max(oos_score, 0.01), 10.0), 2)


# Alias privés historiques (compat avec le code/les tests existants)
_composite_score   = composite_score
_overfitting_ratio = overfitting_ratio
