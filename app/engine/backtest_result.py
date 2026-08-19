"""Métriques et sérialisation d'un run de backtest."""
from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

import numpy as np

from app.core.sanitize import safe_float as _sf
from app.core.timeframes import bars_per_year as _bars_per_year

logger = logging.getLogger(__name__)


@dataclass
class BacktestPayload:
    """Contrat de sortie typé de ``BacktestResult.to_dict`` (ARCH-01).

    Une seule déclaration des clés exposées — ``to_dict`` en dérive,
    l'API Pydantic aussi. ``entry_fees`` et ``fees`` ne peuvent plus
    être confondus : ce sont deux champs distincts.
    """
    initial_capital: float
    rejections: dict
    final_equity: float
    total_pnl: float
    total_pnl_hors_frais_entree: float
    total_fees: float
    total_borrow_cost: float
    total_slippage_cost: float
    total_funding_cost: float
    total_entry_fees: float
    gross_profit: float
    net_profit: float
    total_trades: int
    win_rate: float
    max_drawdown: float
    sharpe: Optional[float]
    sortino: Optional[float]
    calmar: Optional[float]
    cagr: float
    alpha_vs_bh: float
    expectancy: float
    avg_mae: float
    avg_mfe: float
    avg_win: float
    avg_loss: float
    profit_factor: Optional[float]
    buy_and_hold_pnl: float
    buy_and_hold_pct: float
    alpha: float
    equity_curve: List[float]
    timestamps: List[str]
    by_strategy: dict
    by_setup: dict
    by_module: dict
    by_exit_reason: dict
    by_exit_leg: dict
    exit_mode: str
    by_structure_state: dict
    by_sequence_type: dict
    by_tier: dict
    by_target_class: dict
    trades: list
    diagnostics: Any
    ml_info: Any
    fallback_to_inline: bool
    cost_model: Any
    realistic_risk: bool
    realistic_risk_diagnostics: Any

    def to_dict(self) -> dict:
        return asdict(self)


class BacktestResult:
    diagnostics: Any
    ml_info: Any
    exit_mode: str
    cost_model: Any
    realistic_risk: bool
    realistic_risk_diagnostics: Any
    buy_and_hold_pnl: float
    buy_and_hold_pct: float
    alpha: float
    _close_prices: Any

    def __init__(self, trades: List[dict], equity_curve: List[float],
                 initial_capital: float, timestamps: Optional[List[str]] = None,
                 timeframe: str = "1d", rejections: Optional[dict] = None,
                 n_bars: int = 0, equity_mtm: Optional[List[float]] = None):
        self.trades          = trades
        self.equity_curve    = equity_curve
        self.equity_mtm      = equity_mtm or []
        self.initial_capital = initial_capital
        self.timestamps      = timestamps or []
        self._timeframe      = timeframe
        # equity_curve n'a un point qu'à chaque trade — seule _n_bars mesure le temps.
        self._n_bars         = int(n_bars or 0)
        self.rejections      = rejections or {}
        self._compute_metrics()

    def _years(self) -> Optional[float]:
        """Durée en années, ou None (jamais une durée inventée pour annualiser)."""
        bpy = _bars_per_year(self._timeframe)
        if not self._n_bars or not bpy:
            return None
        return self._n_bars / bpy

    def _compute_metrics(self):
        closed = [t for t in self.trades if t.get("status", "").startswith("closed")]
        pnls   = [t["pnl"] for t in closed]
        fees   = [t.get("fees", 0) for t in closed]
        wins   = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        self.total_trades = len(closed)
        self.win_rate     = len(wins) / len(closed) * 100 if closed else 0.0
        self.total_pnl    = sum(pnls)
        self.total_fees   = sum(fees)
        self.final_equity = self.equity_curve[-1] if self.equity_curve else self.initial_capital

        eq = np.array(self.equity_curve, dtype=float)
        # F-06 : le drawdown se lit sur l'équité mark-to-market (un point
        # par barre), pas sur la courbe par trade qui ignore les pertes
        # latentes.
        eq_dd = np.array(self.equity_mtm, dtype=float) if len(self.equity_mtm) > 1 else eq
        if len(eq_dd) > 1:
            peak              = np.maximum.accumulate(eq_dd)
            drawdowns         = (eq_dd - peak) / np.where(peak > 0, peak, 1.0) * 100
            self.max_drawdown = _sf(float(drawdowns.min()), 0.0)
        else:
            self.max_drawdown = 0.0
        if len(eq) > 1:
            returns           = np.diff(eq) / np.where(eq[:-1] > 0, eq[:-1], 1.0)
            std               = float(returns.std())
            # Annualisation à la cadence RÉELLE de la série. `equity_curve` a un
            # point par trade clôturé, pas un par bougie : l'annualiser avec
            # `bars_per_year` supposait 365 observations/an là où le bot en
            # produit 1,5, et gonflait le Sharpe de sqrt(bougies/trades) — ×15
            # sur un run de 8 trades en 5,5 ans (Sharpe affiché 9,5).
            from app.core.performance_metrics import returns_per_year
            from app.core.stats_thresholds import MIN_SIGNIFICANT_TRADES
            # F-02 : un écart-type sur 1-3 points n'est pas estimable. None
            # (non mesurable) ≠ 0.0 (ratio nul). Aligné sur MIN_SIGNIFICANT_TRADES.
            if len(returns) < MIN_SIGNIFICANT_TRADES:
                self.sharpe = None
            else:
                ann_factor        = np.sqrt(returns_per_year(
                    len(returns), self._years(), _bars_per_year(self._timeframe)))
                raw_sharpe        = float(returns.mean() / std * ann_factor) if std > 0 else 0.0
                self.sharpe       = _sf(raw_sharpe, 0.0)
        else:
            self.sharpe       = None

        self.avg_win  = _sf(float(np.mean(wins)),   0.0) if wins   else 0.0
        self.avg_loss = _sf(float(np.mean(losses)), 0.0) if losses else 0.0

        self.expectancy = (
            len(wins) / len(closed) * self.avg_win +
            len(losses) / len(closed) * self.avg_loss
        ) if closed else 0.0

        win_sum  = sum(wins)
        loss_sum = abs(sum(losses))
        # F-10 : aucune perte → non mesurable (None), pas une sentinelle 999
        # qui gagne tous les tris.
        self.profit_factor = (win_sum / loss_sum) if loss_sum > 0 else (None if win_sum > 0 else 0.0)

        maes = [t.get("mae", 0) for t in closed if t.get("mae") is not None]
        mfes = [t.get("mfe", 0) for t in closed if t.get("mfe") is not None]
        self.avg_mae = _sf(float(np.mean(maes)), 0.0) if maes else 0.0
        self.avg_mfe = _sf(float(np.mean(mfes)), 0.0) if mfes else 0.0

        # ── Métriques par stratégie ───────────────────────────────────────────
        # S4-05 : pré-groupement en une passe — l'ancien code refiltrait
        # `closed` (par ex. `[t for t in closed if t.get("strategy") == s]`)
        # TROIS fois par stratégie, soit O(n×k) sur n trades / k stratégies.
        # `trades_by_strategy` préserve l'ordre chronologique (celui de
        # `closed`), requis par la courbe d'équité et le Sharpe ci-dessous.
        from collections import defaultdict as _defaultdict
        trades_by_strategy: Dict[str, list] = _defaultdict(list)
        for t in closed:
            trades_by_strategy[t.get("strategy", "unknown")].append(t)

        self.by_strategy = self._group_metrics(trades_by_strategy)

        # ── §65 : statistiques par SETUP et par MODULE ────────────────────────
        # Une stratégie SMC agrège plusieurs familles de setups dont rien ne dit
        # qu'elles ont le même edge — la spécification demande des statistiques
        # SÉPARÉES par module (SMC Core / ICT Session / ICT Advanced), et le
        # YAML de `smart_money` documente déjà une ablation setup par setup
        # faite à la main.
        #
        # Générique DÉLIBÉRÉMENT : toute stratégie qui pose `setup` et/ou
        # `module` sur son signal obtient le découpage sans rien coder ici, et
        # les dicts restent vides pour celles qui n'en posent pas — aucun
        # appelant existant ne change de comportement.
        trades_by_setup: Dict[str, list] = _defaultdict(list)
        trades_by_module: Dict[str, list] = _defaultdict(list)
        for t in closed:
            if t.get("setup"):
                trades_by_setup[str(t["setup"])].append(t)
            if t.get("module"):
                trades_by_module[str(t["module"])].append(t)
        self.by_setup: Dict[str, dict] = self._group_metrics(trades_by_setup)
        self.by_module: Dict[str, dict] = self._group_metrics(trades_by_module)

        # ── L0 (§101) — statistiques par RAISON DE SORTIE ────────────────────
        # La question « les positions meurent-elles sur stop, sur trailing ou sur
        # expiration ? » n'avait aucune réponse chiffrée : c'est elle qui décide
        # si le problème est le stop, la cible ou la durée de détention.
        trades_by_exit: Dict[str, list] = _defaultdict(list)
        for t in closed:
            trades_by_exit[str(t.get("exit_reason") or "inconnu")].append(t)
        self.by_exit_reason: Dict[str, dict] = self._group_metrics(trades_by_exit)

        # ── §5 — répartition du PnL PAR JAMBE de sortie ──────────────────────
        # `by_exit_reason` compte des TRADES ; il ne dit rien de la façon dont
        # un trade fractionné a gagné son argent. Or c'est exactement la
        # question que pose un mode TP1/TP2/runner : le reliquat paie-t-il les
        # jambes prises tôt, ou les jambes sauvent-elles un reliquat perdant ?
        #
        # INVARIANT : la somme des postes redonne EXACTEMENT `total_pnl`. Sans
        # lui, le découpage est un piège — voir le commentaire du poste
        # « complet » plus bas. Le reliquat n'est pas journalisé comme une
        # jambe (il part avec la clôture du trade) : on le reconstitue par
        # différence, ce qui est précisément ce qui rend l'invariant vrai.
        jambes: Dict[str, dict] = {}

        def _cumule(cle: str, pnl: float) -> None:
            d = jambes.setdefault(cle, {"n": 0, "pnl": 0.0, "wins": 0})
            d["n"] += 1
            d["pnl"] += float(pnl)
            if pnl > 0:
                d["wins"] += 1

        for t in closed:
            legs = t.get("exits") or []
            if legs:
                for leg in legs:
                    _cumule(str(leg.get("reason") or "jambe"),
                            float(leg.get("pnl") or 0.0))
                reste = float(t.get("pnl", 0.0)) - sum(float(x.get("pnl") or 0.0)
                                                       for x in legs)
                _cumule("runner", reste)
            else:
                # Trade sorti EN UNE FOIS. L'inclure n'est pas un détail : sans
                # lui, le découpage ne couvrait que les trades fractionnés —
                # tous gagnants par construction, puisqu'une jambe partielle ne
                # se déclenche qu'en atteignant sa cible. Mesuré sur ETH/USDC
                # 4 h : 80 trades fractionnés à +1 902 affichés, 89 trades non
                # fractionnés à −1 629 invisibles, et un tableau qui annonçait
                # 695 % du PnL réel avec 100 % de réussite sur chaque ligne.
                _cumule(f"complet · {t.get('exit_reason') or 'inconnu'}",
                        float(t.get("pnl", 0.0)))
        total_abs = sum(abs(d["pnl"]) for d in jambes.values()) or 1.0
        for d in jambes.values():
            d["pnl"] = round(d["pnl"], 4)
            d["win_rate"] = round(d["wins"] / d["n"] * 100, 1) if d["n"] else 0.0
            # Part du mouvement TOTAL (en valeur absolue) portée par la jambe :
            # dit d'un coup d'œil si le reliquat pèse ou s'il est décoratif.
            d["part_pct"] = round(abs(d["pnl"]) / total_abs * 100, 1)
        self.by_exit_leg: Dict[str, dict] = jambes

        # ── L3 (§60) / L6 (§72) — par état de structure et par séquence ──────
        # « Entrer en WARNING est-il pire qu'entrer en CONFIRMED ? » est la
        # question que L3 doit trancher par un chiffre, pas par un principe.
        for axe, cle in (("by_structure_state", "structure_state"),
                         ("by_sequence_type", "sequence_type"),
                         ("by_tier", "tier")):
            groupes: Dict[str, list] = _defaultdict(list)
            for t in closed:
                if t.get(cle):
                    groupes[str(t[cle])].append(t)
            setattr(self, axe, self._group_metrics(groupes))

        # L4 (§77 §78) — par CLASSE de liquidité visée. C'est la mesure qui dit
        # si la hiérarchie de la spécification correspond à quelque chose : une
        # cible hebdomadaire est-elle vraiment plus souvent atteinte qu'un
        # swing local ?
        par_classe: Dict[str, list] = _defaultdict(list)
        for t in closed:
            cl = (t.get("indicators") or {}).get("tp_class")
            if cl:
                par_classe[str(cl)].append(t)
        self.by_target_class: Dict[str, dict] = self._group_metrics(par_classe)

        # ── QW-1 : métriques étendues (Sortino, Calmar, CAGR, alpha vs B&H) ──
        self._compute_extended_metrics()

        # ── QW-3 : agrégats de coûts (borrow + slippage) pour analyse frais ──
        # Exigence 4 (estimation frais/levier) : sans ces agrégats, l'utilisateur
        # ne peut pas savoir quelle part du PnL est mangée par le borrow margin
        # vs le slippage. On les calcule à partir des trades fermés.
        # L0 (§49) — chaque poste de coût est désormais porté par le trade
        # (_close_at), donc agrégeable au lieu d'être estimé ou laissé à 0.
        def _sum(key: str) -> float:
            return round(_sf(float(sum(t.get(key, 0) or 0 for t in closed)), 0.0), 4)

        try:
            self.total_borrow_cost   = _sum("borrow_cost")
            self.total_slippage_cost = _sum("slippage_cost")
            self.total_funding_cost  = _sum("funding_cost")
            self.gross_profit        = _sum("gross_pnl")
            # ⚠ `total_pnl` somme les `pnl` de clôture, qui ne retranchent PAS
            # les frais d'entrée (prélevés sur le capital à l'ouverture) :
            # `net_profit` est la variation d'équité réelle, et l'écart entre
            # les deux vaut exactement la somme des frais d'entrée.
            self.net_profit          = round(_sf(
                self.final_equity - self.initial_capital, 0.0), 4)
            self.total_entry_fees    = _sum("entry_fees")
        except Exception:
            self.total_borrow_cost = self.total_slippage_cost = 0.0
            self.total_funding_cost = self.gross_profit = 0.0
            self.net_profit = self.total_entry_fees = 0.0

    def _compute_extended_metrics(self):
        """QW-1 — Sortino, Calmar, CAGR et alpha vs Buy & Hold (S3-07).

        `app/core/performance_metrics.py` était écrit et testé unitairement mais
        jamais appelé : ces 4 métriques alimentent le comparatif multi-stratégies
        (exigence 7) et les recommandations (exigence 8).

        Appelée deux fois : une première depuis `_compute_metrics()` (dans
        `__init__`), puis une seconde depuis `_add_buy_and_hold()` une fois que
        `_close_prices` est disponible. L'alpha vs B&H a besoin de la série des
        prix, qui n'est connue qu'après le run — sans ce second appel il
        resterait silencieusement à 0 (`alpha_vs_buy_hold` renvoie 0 quand le
        benchmark est vide). L'opération est idempotente et peu coûteuse.

        La durée du backtest se déduit du nombre de BOUGIES, jamais du nombre
        de points d'équité : `equity_curve` ne reçoit un point qu'à chaque trade
        clôturé (cf. `_close_at`). Compter 9 points comme 9 périodes donnait
        « 0,025 an » pour un run de 5,5 ans, et un CAGR de 3 809 %/an.
        """
        try:
            from app.core.performance_metrics import compute_extended_metrics
            closed = [t for t in self.trades if t.get("status", "").startswith("closed")]
            prices = getattr(self, "_close_prices", None) or []
            bars_per_year = int(_bars_per_year(self._timeframe) or 252)
            # `_n_bars` est fourni au constructeur : la durée est connue dès le
            # premier appel, plus besoin d'attendre `_add_buy_and_hold`.
            years = self._years()
            ext = compute_extended_metrics(
                trades=closed,
                equity_curve=self.equity_curve,
                initial_capital=self.initial_capital,
                prices=prices,
                years=years,
                periods_per_year=bars_per_year,
            )
            self.sortino = ext["sortino"]
            self.calmar = ext["calmar"]
            self.cagr = ext["cagr"]
            self.alpha_vs_bh = ext["alpha_vs_bh"]
        except Exception as _ext_err:
            # Ne jamais planter le backtest à cause des métriques étendues —
            # on logge et on dégrade vers 0 (comportement inchangé avant QW-1).
            logger.warning(
                f"[BacktestResult] compute_extended_metrics KO ({_ext_err}) — "
                f"Sortino/Calmar/CAGR/alpha_vs_bh mis à 0"
            )
            self.sortino = 0.0
            self.calmar = 0.0
            self.cagr = 0.0
            self.alpha_vs_bh = 0.0

    def _group_metrics(self, trades_by_key: Dict[str, list]) -> Dict[str, dict]:
        """Statistiques complètes par groupe de trades.

        Extraite de la boucle `by_strategy` pour être réutilisée telle quelle
        par les découpages par setup et par module : trois copies de ce calcul
        auraient fini par diverger, et un profit factor calculé différemment
        selon l'axe d'analyse ne serait comparable à rien.

        Le groupement préserve l'ordre chronologique de `closed`, requis par la
        courbe d'équité et le Sharpe.
        """
        out: Dict[str, dict] = {}
        for s, strat_trades in trades_by_key.items():
            d: Dict[str, Any] = {"trades": 0, "wins": 0, "pnl": 0.0, "fees": 0.0}
            for t in strat_trades:
                d["trades"] += 1
                d["pnl"]    += t["pnl"]
                d["fees"]   += t.get("fees", 0)
                if t["pnl"] > 0:
                    d["wins"] += 1
            out[s] = d

        for s, d in out.items():
            strat_trades = trades_by_key[s]
            sd_pnls = [t["pnl"] for t in strat_trades]
            wins_s  = [p for p in sd_pnls if p > 0]
            loss_s  = [p for p in sd_pnls if p <= 0]

            d["win_rate"]     = round(d["wins"] / d["trades"] * 100, 1) if d["trades"] else 0.0
            d["pnl"]          = round(d["pnl"], 4)
            d["fees"]         = round(d["fees"], 4)
            d["avg_win"]      = round(_sf(float(np.mean(wins_s)), 0.0), 4) if wins_s else 0.0
            d["avg_loss"]     = round(_sf(float(np.mean(loss_s)), 0.0), 4) if loss_s else 0.0
            _loss_sum = abs(sum(loss_s))
            d["profit_factor"] = (round(sum(wins_s) / _loss_sum, 3) if _loss_sum > 0
                                  else (None if wins_s else 0.0))
            d["expectancy"]   = round(
                len(wins_s) / len(sd_pnls) * d["avg_win"] +
                len(loss_s) / len(sd_pnls) * d["avg_loss"], 4
            ) if sd_pnls else 0.0
            d["total_trades"] = d["trades"]
            d["total_pnl"]    = d["pnl"]
            d["total_fees"]   = d["fees"]

            eq_s  = [self.initial_capital]
            cap   = self.initial_capital
            for t in strat_trades:
                cap += t["pnl"]
                eq_s.append(round(cap, 4))
            d["equity_curve"]    = eq_s
            d["initial_capital"] = self.initial_capital
            d["final_equity"]    = round(eq_s[-1], 4)

            eq_arr = np.array(eq_s, dtype=float)
            peak_s = np.maximum.accumulate(eq_arr)
            if len(eq_arr) > 1:
                dd_arr = (eq_arr - peak_s) / np.where(peak_s > 0, peak_s, 1.0) * 100
                d["max_drawdown"] = round(_sf(float(dd_arr.min()), 0.0), 2)
            else:
                d["max_drawdown"] = 0.0

            if len(eq_arr) > 1:
                denom  = np.where(eq_arr[:-1] > 0, eq_arr[:-1], 1.0)
                rets_s = np.diff(eq_arr) / denom
            else:
                rets_s = np.array([0.0])
            # Même correction que le Sharpe global : `eq_s` est construite trade
            # par trade juste au-dessus, sa cadence est celle des trades DE CETTE
            # stratégie — qui peut différer de celle du portefeuille entier.
            from app.core.performance_metrics import returns_per_year as _rpy
            ann_s  = np.sqrt(_rpy(len(rets_s), self._years(),
                                  _bars_per_year(self._timeframe)))
            std_s  = float(rets_s.std())
            from app.core.stats_thresholds import MIN_SIGNIFICANT_TRADES as _MIN_SH
            if len(rets_s) < _MIN_SH:
                d["sharpe"] = None
            elif std_s > 0:
                d["sharpe"] = round(_sf(float(rets_s.mean() / std_s * ann_s), 0.0), 3)
            else:
                d["sharpe"] = 0.0

            d["trades"] = strat_trades
        return out

    def to_payload(self) -> BacktestPayload:
        pf = self.profit_factor
        if pf is None:
            pf_safe = None
        else:
            pf_safe = round(float(pf), 3) if math.isfinite(float(pf)) else None
        ml_info = getattr(self, "ml_info", None)
        fallback = False
        if isinstance(ml_info, dict):
            fallback = any(
                isinstance(m, dict) and m.get("fallback_to_inline")
                for m in (ml_info.get("models") or {}).values()
            )
        return BacktestPayload(
            initial_capital=self.initial_capital,
            rejections=self.rejections,
            final_equity=round(_sf(self.final_equity, 0.0), 4),
            total_pnl=round(_sf(self.total_pnl, 0.0), 4),
            total_pnl_hors_frais_entree=round(
                _sf(self.total_pnl + getattr(self, "total_entry_fees", 0.0), 0.0), 4),
            total_fees=round(_sf(self.total_fees, 0.0), 4),
            total_borrow_cost=round(_sf(getattr(self, "total_borrow_cost", 0.0), 0.0), 4),
            total_slippage_cost=round(_sf(getattr(self, "total_slippage_cost", 0.0), 0.0), 4),
            total_funding_cost=round(_sf(getattr(self, "total_funding_cost", 0.0), 0.0), 4),
            total_entry_fees=round(_sf(getattr(self, "total_entry_fees", 0.0), 0.0), 4),
            gross_profit=round(_sf(getattr(self, "gross_profit", 0.0), 0.0), 4),
            net_profit=round(_sf(getattr(self, "net_profit", 0.0), 0.0), 4),
            total_trades=self.total_trades,
            win_rate=round(_sf(self.win_rate, 0.0), 2),
            max_drawdown=round(_sf(self.max_drawdown, 0.0), 2),
            sharpe=(None if self.sharpe is None else round(_sf(self.sharpe, 0.0), 3)),
            sortino=(None if getattr(self, "sortino", None) is None
                     or not math.isfinite(float(self.sortino))
                     else round(float(self.sortino), 3)),
            calmar=(None if getattr(self, "calmar", None) is None
                    or not math.isfinite(float(self.calmar))
                    else round(float(self.calmar), 3)),
            cagr=round(_sf(getattr(self, "cagr", 0.0), 0.0), 3),
            alpha_vs_bh=round(_sf(getattr(self, "alpha_vs_bh", 0.0), 0.0), 4),
            expectancy=round(_sf(self.expectancy, 0.0), 4),
            avg_mae=round(_sf(self.avg_mae, 0.0), 4),
            avg_mfe=round(_sf(self.avg_mfe, 0.0), 4),
            avg_win=round(_sf(self.avg_win, 0.0), 4),
            avg_loss=round(_sf(self.avg_loss, 0.0), 4),
            profit_factor=pf_safe,
            buy_and_hold_pnl=round(_sf(getattr(self, "buy_and_hold_pnl", 0), 0.0), 4),
            buy_and_hold_pct=round(_sf(getattr(self, "buy_and_hold_pct", 0), 0.0), 3),
            alpha=round(_sf(getattr(self, "alpha", 0), 0.0), 4),
            equity_curve=[round(_sf(e, 0.0), 4) for e in self.equity_curve],
            timestamps=self.timestamps,
            by_strategy=self.by_strategy,
            by_setup=getattr(self, "by_setup", {}),
            by_module=getattr(self, "by_module", {}),
            by_exit_reason=getattr(self, "by_exit_reason", {}),
            by_exit_leg=getattr(self, "by_exit_leg", {}),
            exit_mode=getattr(self, "exit_mode", "as_declared"),
            by_structure_state=getattr(self, "by_structure_state", {}),
            by_sequence_type=getattr(self, "by_sequence_type", {}),
            by_tier=getattr(self, "by_tier", {}),
            by_target_class=getattr(self, "by_target_class", {}),
            trades=self.trades,
            diagnostics=getattr(self, "diagnostics", None),
            ml_info=ml_info,
            fallback_to_inline=fallback,
            cost_model=getattr(self, "cost_model", None),
            realistic_risk=getattr(self, "realistic_risk", False),
            realistic_risk_diagnostics=getattr(self, "realistic_risk_diagnostics", None),
        )

    def to_dict(self) -> dict:
        return self.to_payload().to_dict()


