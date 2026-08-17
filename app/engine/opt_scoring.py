"""Scoring des résultats d'optimisation (score composite IS/OOS, surapprentissage).

Extrait de ``optimizer.py`` (découpage V13 : recherche / scoring / persistance).
Module sans état ni dépendance lourde : importable par les workers spawn.
"""
import logging
import math
from statistics import NormalDist
from typing import Optional

logger = logging.getLogger(__name__)

_STD_NORMAL = NormalDist(mu=0.0, sigma=1.0)
_EULER_MASCHERONI = 0.5772156649015329


# Capital de repli quand le résultat ne porte pas son ``initial_capital``
# (vieux dicts de test, résumés partiels). Sert uniquement d'échelle pour
# convertir un PnL absolu en rendement % — n'affecte jamais le classement au
# sein d'une même optimisation (tous les essais partagent le même capital).
_FALLBACK_CAPITAL = 1000.0

from app.core.stats_thresholds import (  # noqa: E402
    MIN_SIGNIFICANT_TRADES,
)


def composite_score(res, min_trades: int = MIN_SIGNIFICANT_TRADES) -> float:
    """Score composite d'un résultat de backtest (dict ou BacktestResult).

    Combine Sharpe (borné), win-rate, profit factor, expectancy, drawdown,
    nombre de trades, **rendement %** et alpha vs buy & hold. Retourne -999 si
    moins de ``min_trades`` trades.

    ``min_trades`` vaut ``MIN_SIGNIFICANT_TRADES`` — le MÊME seuil que
    ``beats_baseline`` et le lifecycle. Il valait 2 auparavant, au motif qu'un
    classement n'est pas une décision : mais c'est ce classement qui désigne le
    jeu retenu, et un plancher de 2 laissait gagner des Sharpe de 7,83 sur deux
    trades (docs/SUITE_ABLATION_V3.md §1). Un seul seuil, désormais.

    Surchargeable par ``optimizer.min_trades`` pour les études exploratoires.

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
        sharpe = res.get("sharpe") or 0
        wr     = res.get("win_rate", 0) / 100
        pf     = res.get("profit_factor", 0)
        pnl    = res.get("net_profit", res.get("total_pnl", 0))
        dd     = abs(res.get("max_drawdown", -100))
        exp    = res.get("expectancy", 0)
        alpha  = res.get("alpha", 0)
        cap    = res.get("initial_capital") or _FALLBACK_CAPITAL
    else:
        sharpe = res.sharpe or 0
        wr     = res.win_rate / 100
        pf     = res.profit_factor
        pnl    = getattr(res, "net_profit", res.total_pnl)
        dd     = abs(res.max_drawdown)
        exp    = res.expectancy
        alpha  = getattr(res, "alpha", 0)
        cap    = getattr(res, "initial_capital", None) or _FALLBACK_CAPITAL

    cap = float(cap) if cap else _FALLBACK_CAPITAL

    if pf is None or (isinstance(pf, float) and not math.isfinite(pf)):
        pf = 6.0  # F-10 : aucune perte → plafond qualité, pas sentinelle 999
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
    """Ratio IS/OOS (> 2.5 = surapprentissage probable), NaN si INDÉFINI.

    Un ratio n'a de sens que si les deux scores sont positifs. Les trois cas
    dégénérés rendent donc ``NaN`` — pas une valeur numérique qui prendrait
    rang dans un classement :

    - score IS ou OOS non significatif (≤ −990, run dégénéré) ;
    - **score IS ≤ 0** : la configuration échoue déjà en apprentissage. Il n'y a
      pas de « sur-apprentissage » à mesurer, il n'y a pas d'apprentissage ;
    - **score OOS ≤ 0** : la dégradation est totale. Le rapport tendrait vers
      l'infini ; le saturer à 10 en ferait un nombre comparable à un vrai ratio.

    ⚠ Correction. La version précédente rendait **0.0** quand le score IS était
    négatif — c'est-à-dire *la meilleure valeur de l'échelle* pour une
    configuration qui ne marche nulle part. Mesuré sur la campagne de
    recalibration : `multi_tf_sr` ETH 4 h (PnL OOS **+371,7**, Sharpe 1,35) et
    `fear_momentum` BTC 1 h (PnL OOS **−168,4**, Sharpe −2,48) recevaient tous
    deux ``overfit = 0.0``. Et elle saturait à **10.0** dès que le score OOS
    passait sous 0.01, par l'effet du garde ``max(oos_score, 0.01)`` — ce qui
    faisait lire « surapprentissage extrême » là où le fait mesuré était
    seulement « score OOS négatif ». Cf. docs/DEFAUT_METRIQUE_OVERFIT.md.
    """
    if oos_score <= -990 or is_score <= -990:
        return float('nan')
    if is_score <= 0 or oos_score <= 0:
        return float('nan')
    return round(min(is_score / oos_score, 10.0), 2)


# Alias privés historiques (compat avec le code/les tests existants)
_composite_score   = composite_score
_overfitting_ratio = overfitting_ratio


def beats_baseline(oos_trades: int, oos_pnl: float, oos_wr: float,
                   oos_sharpe: float, baseline: dict,
                   min_trades: int = MIN_SIGNIFICANT_TRADES,
                   n_trials: int = 1,
                   min_deflated_sharpe: Optional[float] = None) -> tuple:
    """Garde-fou UNIQUE d'application d'un paramétrage optimisé (BT-04/BT-06).

    Retourne ``(ok, raison)``. Partagé par l'auto-apply (AutoOptimizer) et
    l'apply manuel (route /api/optimize/apply) — mêmes exigences des deux
    côtés :

    1. échantillon suffisant : ``oos_trades >= min_trades``
       (défaut ``MIN_SIGNIFICANT_TRADES`` — cf. app/core/stats_thresholds.py) ;
    2. PnL OOS strictement positif ;
    3. PnL OOS strictement meilleur que le baseline (params actuels) ;
    4. amélioration d'au moins un critère de qualité (win-rate OU Sharpe) ;
    5. **Deflated Sharpe** (P0 — câblage du module ``app/core/deflated_sharpe.py``)
       si ``n_trials > 1`` et ``min_deflated_sharpe`` fourni > 0 : corrige le
       biais de multiple testing (López de Prado 2014). Un Sharpe OOS élevé
       obtenu après 50 essais est beaucoup moins significatif que le même
       Sharpe obtenu au premier essai — le Deflated Sharpe pénalise cela.
       Désactivable via ``min_deflated_sharpe=None`` ou ``n_trials=1`` (par
       défaut, préserve la rétrocompatibilité).
    """
    baseline = baseline or {}
    b_pnl    = baseline.get("pnl", float("-inf"))
    b_wr     = baseline.get("wr", 0)
    b_sharpe = baseline.get("sharpe", 0)
    if oos_trades < min_trades:
        return False, (f"échantillon OOS insuffisant ({oos_trades} trades "
                       f"< {min_trades} requis)")
    if oos_pnl <= 0:
        return False, f"PnL OOS non positif ({oos_pnl:+.2f})"
    if oos_pnl <= b_pnl:
        return False, (f"PnL OOS ({oos_pnl:+.2f}) ≤ baseline ({b_pnl:+.2f})")
    # F-02 : un Sharpe None n'est pas mesurable — il ne peut pas battre
    # le baseline. On n'autorise l'amélioration de qualité que via le WR.
    _sharpe_ok = (oos_sharpe is not None
                  and b_sharpe is not None
                  and oos_sharpe > b_sharpe)
    if not (oos_wr > b_wr or _sharpe_ok):
        _sh_txt = "—" if oos_sharpe is None else f"{oos_sharpe:.2f}"
        _bsh_txt = "—" if b_sharpe is None else f"{b_sharpe:.2f}"
        return False, (f"aucune amélioration de qualité (WR {oos_wr:.1f}% vs "
                       f"{b_wr:.1f}%, Sharpe {_sh_txt} vs {_bsh_txt})")

    # ── 5. Deflated Sharpe gate (P0 — câblage TODO auto_optimizer.py:521) ──
    # Ne s'active QUE si n_trials > 1 (sinon pas de biais de sélection à
    # corriger) ET min_deflated_sharpe est fourni (> 0). En cas d'erreur de
    # calcul (Sharpe NaN, etc.), on n'échoue pas silencieusement : on logge
    # et on accepte (préserve la rétrocompatibilité — un gate trop strict
    # silencieux serait pire qu'un gate absent).
    if (n_trials and n_trials > 1
            and min_deflated_sharpe is not None and min_deflated_sharpe > 0
            and oos_sharpe is not None):
        try:
            # F-07 : formule Bailey & López de Prado (probabilité ∈ [0,1]),
            # plus l'heuristique maison de core/deflated_sharpe.py.
            dsr = deflated_sharpe_ratio(
                float(oos_sharpe),
                n_observations=int(oos_trades),
                n_trials=int(n_trials),
            )
            if dsr < float(min_deflated_sharpe):
                return False, (f"Deflated Sharpe gate refusé : DSR={dsr:.2f} "
                               f"< seuil={min_deflated_sharpe:.2f} "
                               f"(n_trials={n_trials})")
        except Exception as _ds_err:
            logger.warning(
                f"[beats_baseline] Deflated Sharpe KO ({_ds_err}) — gate ignoré "
                f"(n_trials={n_trials}, sharpe={oos_sharpe})"
            )

    return True, "ok"


# ── Deflated Sharpe Ratio (S4-04) ────────────────────────────────────────────
#
# L'optimiseur teste des dizaines/centaines de combinaisons de paramètres et
# retient le meilleur Sharpe OOS — sans correction, ce max est biaisé à la
# hausse par le nombre d'essais (biais de sélection multiple, cf. Bailey &
# López de Prado, "The Deflated Sharpe Ratio: Correcting for Selection Bias,
# Backtest Overfitting and Non-Normality", 2014). Le DSR estime la probabilité
# que le Sharpe observé soit RÉELLEMENT > 0 une fois ce biais retiré.
#
# Fonction volontairement AUTONOME (pas encore câblée dans composite_score) :
# une intégration au score de classement nécessiterait de faire remonter
# n_trials/trial_sharpes_std depuis la boucle de l'optimiseur (optimizer.py/
# opt_workers.py) jusqu'à chaque appel de composite_score — changement plus
# large que ce correctif, laissé en suivi. Utilisable dès maintenant pour
# annoter un rapport d'optimisation (ex. DSR du meilleur essai).

def _expected_max_sharpe(n_trials: int, sharpe_std: float) -> float:
    """E[max SR] sur ``n_trials`` essais indépendants dont l'écart-type des
    Sharpe individuels vaut ``sharpe_std`` — approximation de Bailey & López
    de Prado (2014, éq. 7), basée sur la loi des extrêmes gaussiens."""
    if n_trials <= 1 or sharpe_std <= 0:
        return 0.0
    n = float(n_trials)
    term1 = (1 - _EULER_MASCHERONI) * _STD_NORMAL.inv_cdf(1 - 1 / n)
    term2 = _EULER_MASCHERONI * _STD_NORMAL.inv_cdf(1 - 1 / (n * math.e))
    return sharpe_std * (term1 + term2)


def deflated_sharpe_ratio(sharpe: float, n_observations: int, n_trials: int = 1,
                          trial_sharpes_std: float = None,
                          skew: float = 0.0, kurtosis: float = 3.0) -> float:
    """Deflated Sharpe Ratio ∈ [0, 1] : probabilité que ``sharpe`` soit
    réellement positif, corrigée du biais de sélection multiple (``n_trials``
    essais d'optimisation) et de la non-normalité des rendements.

    Paramètres
    ----------
    sharpe : Sharpe annualisé observé (le "meilleur essai" à évaluer).
    n_observations : nombre de rendements utilisés pour calculer ce Sharpe
        (≈ nombre de trades ou de barres selon la méthode — cohérence avec
        le calcul source requise).
    n_trials : nombre d'essais indépendants dont ``sharpe`` est le maximum
        (ex. nombre de combinaisons de paramètres testées par l'optimiseur).
        1 = pas de correction de sélection (DSR ≈ probabilistic Sharpe ratio
        simple).
    trial_sharpes_std : écart-type des Sharpe individuels entre essais, si
        connu (variance empirique de la recherche). ``None`` → 1.0, valeur
        conservative standard de la littérature quand la distribution des
        essais n'est pas trackée.
    skew, kurtosis : moments des rendements (défauts = gaussien standard,
        skew=0/kurtosis=3 → aucune correction de non-normalité).

    Retourne 0.0 si l'échantillon est dégénéré (< 2 observations).
    """
    if sharpe is None or n_observations is None or n_observations < 2:
        return 0.0
    sr = float(sharpe)
    t  = float(n_observations)
    std_sr = trial_sharpes_std if trial_sharpes_std is not None else 1.0
    sr0 = _expected_max_sharpe(max(int(n_trials), 1), std_sr)

    # γ3 (skew) et γ4 (kurtosis) corrigent l'écart à la normalité des
    # rendements — un Sharpe calculé sur des rendements à queue épaisse
    # (kurtosis > 3) est moins fiable qu'annoncé par le t-test gaussien.
    denom = math.sqrt(max(1 - skew * sr + (kurtosis - 1) / 4.0 * sr ** 2, 1e-9))
    z = (sr - sr0) * math.sqrt(t - 1) / denom
    return round(_STD_NORMAL.cdf(z), 6)
