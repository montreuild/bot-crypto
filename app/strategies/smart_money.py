"""Stratégie Smart Money Concepts (SMC) — moteur ``app/core/smc.py``.

Deux familles de setups, prises uniquement dans le sens de la structure ou sur
rejet net d'une poche de liquidité :

  1. SWEEP_REVERSAL — une mèche prend la liquidité (equal lows / swing low pour
     un long, miroir pour un short) puis la bougie clôture de retour du bon
     côté (rejet). Les stops des retail viennent d'être consommés : entrée
     dans le sens opposé au sweep, stop sous l'extrême de la mèche.

  2. OB_RETEST — après une cassure de structure (BOS/CHoCH), le prix revient
     pour la première fois dans l'order block à l'origine de l'impulsion
     (zone de demande pour un long, d'offre pour un short) : entrée sur la
     zone, stop de l'autre côté de l'order block.

  3. BREAKER_RETEST — un order block invalidé inverse sa polarité (breaker
     block) : premier retest de la zone dans le nouveau sens (les stops
     piégés dans l'ancienne zone alimentent le mouvement).

Filtres DURS (validés sur BTC/USDC 15m→1d, 2019-2026) :
  - structure alignée obligatoire (long uniquement en trend haussier…) ;
  - côté momentum du range : pas de long en zone discount, pas de short en
    zone premium — sur crypto la force appelle la force, le « deep discount »
    d'une tendance haussière est le plus souvent une structure qui casse ;
  - biais EMA(``ema_filter_len``) : long au-dessus, short en dessous ;
  - biais MULTI-TIMEFRAME (``htf_filter: soft`` par défaut) : jamais de trade
    contre la structure du timeframe supérieur. Le HTF est celui de la « source
    unique de vérité » ``_HTF_MAP`` (app/live/utils : 4h→1d, 1h→4h…) — seul
    enrichissement gagnant sur TOUS les TF testés (campagne 2026-07).

Confluences additionnées au score de base 0.50 (cap 1.0) :
  +0.10 structure alignée (toujours vrai pour OB_RETEST / BREAKER_RETEST)
  +0.10 pool « véritable » (equal highs/lows) plutôt que swing isolé (sweep)
  +0.10 order block « strength 2 » (son impulsion a cassé la structure)
  +0.10 prix du côté momentum fort (premium pour un long, discount pour un short)
  +0.05 chevauchement avec un FVG ouvert de même direction
  +0.05 volume > ``vol_confluence`` × SMA20(volume)
  +0.05 bougie de rejet colorée dans le sens du trade
  +0.05 tap de la trendline automatique (support pour un long, résistance
        pour un short, à ``tl_tol_atr``×ATR près) — le « buy orders » des
        traders de canaux

Cibles de TP : poches de liquidité opposées ET bords opposés des liquidity
voids non comblés (zones fines = aimants), première cible satisfaisant
``min_gain_pct`` et ``min_rr``.

Sorties : bracket FIXE (pas de trailing). SL sous/за l'extrême de la zone
(+ ``sl_buffer_atr``×ATR), TP posé juste devant la prochaine poche de
liquidité opposée (front-run de ``tp_front_run_atr``×ATR), sinon fallback
``tp_rr_fallback``×R.

⚠ FILTRE DE GAIN : une position n'est retenue que si le gain potentiel
(distance entrée → TP) dépasse ``min_gain_pct`` (défaut 0.4 %) ET si le
ratio gain/risque atteint ``min_rr``. Les setups dont la cible de liquidité
la plus proche est trop courte sont rejetés (ou re-ciblés sur la poche
suivante si elle respecte les deux contraintes).
"""
import logging
from typing import Any, Dict, List, Optional

import numpy as np
import polars as pl

from app.engine.engine import BaseStrategy
from app.strategies import smart_money_signals as _sm_signals
from app.core import smc
from app.core.indicators_core import (ema as _ema_series, volume_ratio as _vol_ratio,
                                       choppiness as _choppiness, pin_bar as _pin_bar,
                                       engulfing as _engulfing)

logger = logging.getLogger(__name__)


def _tf_to_sec(tf: str) -> int:
    """Convertit un libellé de timeframe (« 4h », « 30m », « 1d ») en secondes."""
    try:
        return int(tf[:-1]) * {"m": 60, "h": 3600, "d": 86400}.get(tf[-1], 60)
    except (ValueError, IndexError):
        return 0


# Biais HTF aligné sur la source unique app/core/timeframes (secondes LTF →
# secondes HTF), au lieu d'un multiplicateur ×N arbitraire.
# Validé sur BTC : ≥ aussi bon que ×4 (4h→1d : PF 1.52 vs 1.49).
from app.core.timeframes import HTF_SECONDS_MAP as _HTF_SEC_MAP  # noqa: E402


class Strategy(BaseStrategy):
    name = "smart_money"

    # Edge validé sur BTC/USDC 2019→2026 avec les défauts : 4h (PF 1.20,
    # DD −12 %). Les TF < 2h testent négatif (bruit + frais) — laissés à
    # l'optimiseur via param_space plutôt que recommandés ici.
    timeframes: List[str] = ["2h", "4h", "1d"]
    warmup_bars: int = 260

    param_space: Dict[str, List] = {
        "swing_len":      [2, 3, 5],
        "eq_tol_atr":     [0.15, 0.25, 0.4],
        "disp_body_atr":  [1.0, 1.3, 1.6],
        "min_rr":         [1.0, 1.2, 1.5],
        "min_gain_pct":   [0.4, 0.8, 1.2],
        "sl_buffer_atr":  [0.15, 0.25, 0.5],
        "ema_filter_len": [0, 100, 200],
        "choch_exit":     [True, False],
        "use_breakers":   [True, False],
        "use_void_targets": [True, False],
        "htf_filter":     ["off", "soft", "strict"],
        "kz_bonus":       [True, False],
        "amd_bonus":      [True, False],
        "vp_confluence":  [True, False],
        "use_rejection_blocks": [True, False],
        "time_stop_bars": [0, 12, 16, 24],
        "use_trailing":   [True, False],
        "trail_mult":     [2.0, 2.5, 3.5],
        "size_by_confluence": [True, False],
        "size_conf_slope": [2.0, 3.0, 4.0],
        "ext_structure_filter": [True, False],
        "tp_measured_move": [True, False],
        "inv_fvg_bonus":  [True, False],
        "chop_filter_max": [0.0, 61.8],
        "candle_bonus":   [True, False],
        "smt_bonus":      [True, False],
        "smt_filter":     [True, False],
        "use_calendar_liquidity": [False, "targets", "sweeps", True],
    }

    fixed_params: Dict[str, Any] = {
        "swing_len":        3,
        "eq_tol_atr":       0.25,
        "disp_body_atr":    1.3,
        "ob_lookback":      5,
        "fvg_min_atr":      0.2,
        "min_gain_pct":     0.4,    # ⚠ gain potentiel minimal (%) entrée → TP
        "min_rr":           1.2,    # ratio gain/risque minimal
        "sl_buffer_atr":    0.25,
        "tp_rr_fallback":   2.0,    # TP fallback (×R) si aucune poche exploitable
        "tp_front_run_atr": 0.1,    # TP posé juste avant la poche (front-run)
        "vol_confluence":   1.2,
        "allow_counter_trend": False,  # sweeps contre-tendance interdits
        "use_breakers":     False,  # setup BREAKER_RETEST : négatif sur BTC 4h
                                    # (−163 USDC / 220 trades, validation 2026-07)
                                    # → off par défaut, exploré par l'optimiseur
        "use_void_targets": True,   # bords des liquidity voids comme cibles TP
        "tl_tol_atr":       0.3,    # tolérance du tap de trendline (×ATR)
        # ── Enrichissements 2026-07 (validés individuellement, cf. YAML) ─────
        "htf_filter":       "soft",  # biais multi-TF : off | soft (pas contre) | strict (aligné)
                                    # → seul enrichissement gagnant sur TOUS les TF
                                    # (4h : PF 1.52 vs 1.41)
        "htf_mult":         4,      # fallback si le LTF détecté n'est pas dans
                                    # _HTF_MAP ; sinon HTF = _HTF_MAP[tf] (4h→1d)
        "use_rejection_blocks": False,  # setups REJECTION_RETEST (mèches de swing)
        "rb_wick_atr":      0.5,    # mèche minimale d'un rejection block (×ATR)
        "vp_confluence":    False,  # bonus si la zone chevauche un HVN (acceptation)
        "vp_targets":       False,  # POC/HVN comme cibles TP additionnelles
        "vp_lookback":      240,    # fenêtre du volume profile (barres)
        "vp_bins":          40,
        "kz_bonus":         False,  # bonus si signal dans une killzone (LDN/NY)
        "kz_filter":        False,  # filtre dur : signaux uniquement en killzone
        "amd_bonus":        False,  # bonus sweep après compression (manipulation AMD)
        "amd_bars":         12,     # fenêtre de la phase d'accumulation
        "amd_range_atr":    2.0,    # range max de l'accumulation (×ATR)
        "min_score":        0.0,    # seuil de score interne à la stratégie —
                                    # équivalent de score_threshold mais
                                    # SURCHARGEABLE par optimizer_results/TF
                                    # (score_threshold est une clé globale
                                    # protégée de l'overlay, cf. app/live/utils)
        "ema_filter_len":   200,    # biais EMA : long si close>EMA, short si close<EMA (0 = off)
        "choch_exit":       False,  # sortie anticipée sur CHoCH contre la position
                                    # (False par défaut : coupe systématiquement en perte
                                    # sur BTC 30m→1d, cf. validation 2026-07)
        "max_window":       3000,   # fenêtre d'analyse max (bornage O(n))
        "ob_max_age":       250,    # âge max (barres) d'un OB jouable
        "pool_max_age":     500,    # âge max d'une poche utilisable comme cible
        "choch_guard_bars": 5,      # pas d'entrée contre un CHoCH < N barres
        # Time-stop : sortie au bout de N barres si ni TP ni SL touché (0 = off).
        # Coupe les positions qui STAGNENT dans la chop (le prix spike puis
        # revient) au lieu de tenir jusqu'à une cible lointaine qui ne se remplit
        # pas. Levier de RÉGIME : aide nettement les périodes choppy récentes
        # (BTC 4h 2024-26 : −19 → +13) au prix de l'upside des tendances fortes
        # (où l'on veut laisser courir). Arbitrage tranché par l'optimiseur/TF.
        "time_stop_bars":   0,
        # Trailing stop (via le TrailingStopManager du Backtester) au lieu du
        # TP fixe : laisse COURIR les gagnants (outil de tendance, complément du
        # time-stop). Combiné au time-stop CONDITIONNEL (coupe uniquement les
        # trades qui n'ont jamais atteint +``ts_profit_r``×R = stagnants), on
        # ride les tendances ET on coupe la chop. Validé 4h (cf. optimizer_results).
        "use_trailing":     False,
        "trail_mult":       2.5,    # multiplicateur ATR du trailing (trail_wide)
        "ts_profit_r":      1.0,    # seuil de « progression » (×R) sous lequel le
                                    # time-stop coupe quand use_trailing est actif
        # Sizing pondéré par confluence (via le hook natif ``size_factor`` du
        # Backtester/live — « demi-Kelly ×confidence »). On alloue PLUS aux
        # setups à forte confluence : size_factor = 1 + slope×(score − center),
        # borné [0.4, 1.7]. Centré sur le score moyen ⇒ exposition globale ≈
        # inchangée (RÉALLOCATION du risque, pas du levier) — le DD reste plat.
        # Le score du moteur est prédictif : gain net sur les 2 périodes (4h OOS
        # +81 → +108, score composite 0.291 → 0.332). Validé 4h (optimizer_results).
        "size_by_confluence": False,
        "size_conf_slope":  3.0,    # pente de la pondération par le score
        "size_conf_center": 0.83,   # score « neutre » (≈ moyenne 4h) → facteur 1.0
        # ── Pistes SMC optionnelles (OFF par défaut — mesurées perdantes sur
        # BTC 4h, exposées à l'optimiseur pour d'autres TF/symboles/régimes) ──
        # 1d — filtre de structure EXTERNE : n'autorise un sens que s'il est
        # aligné avec la tendance de degré SUPÉRIEUR (pivots ext_swing_len,
        # 2e analyse causale). Se compose avec le gate HTF.
        "ext_structure_filter": False,
        "ext_swing_len":    8,
        # 4b — TP par symétrie de jambe (measured move) : ajoute, comme cible
        # candidate, la projection de l'amplitude de la dernière jambe de
        # structure depuis l'entrée (en concurrence avec les cibles liquidité).
        "tp_measured_move": False,
        # 4c — inversion de rôle des FVG : bonus de confluence (+0.05) si un FVG
        # de sens OPPOSÉ, déjà mitigé, chevauche la zone d'entrée (support/
        # résistance inversé).
        "inv_fvg_bonus":    False,
        # ── Croisements indicateurs × SMC (campagne 2026-07-08) ──────────────
        # Filtre Choppiness : ne PAS générer de signal si l'indice de choppiness
        # (congestion) dépasse ce seuil (0 = off). Ne trader qu'en tendance.
        # MESURÉ GAGNANT sur BTC 4h → activé (61.8) dans optimizer_results.
        "chop_filter_max":  0.0,
        "chop_len":         14,
        # Bonus confirmation bougie : +0.05 au score si pin bar / engulfing dans
        # le sens du setup à la barre de déclenchement (qualité par trade très
        # élevée mais rare). OFF par défaut — via le sizing, monte les setups
        # confirmés. Levier d'optimiseur.
        "candle_bonus":     False,
        # ── SMT divergence (ICT) : confluence/filtre vs actif corrélé ────────
        # Divergence entre l'actif tradé et un corrélé (``smt_correlate_path``,
        # ex. data/ohlcv/ETH_USDC/4h.parquet pour BTC) : l'un prend la liquidité,
        # l'autre non. Via ``smc.smt_series`` (primitive moteur générique).
        #   smt_bonus  : +``smt_conf`` au score si la divergence CONFIRME le sens.
        #   smt_filter : rejette un setup CONTREDIT par une divergence opposée.
        # Les deux OFF par défaut → comportement byte-identique. À MESURER avant
        # activation (confluence non prouvée sur BTC ; cf. campagne SMT).
        "smt_correlate_path": "",
        "smt_lookback":     20,
        "smt_bonus":        False,
        "smt_filter":       False,
        "smt_conf":         0.05,
        # ── SMC-03 : liquidité calendaire PDH/PDL/PWH/PWL (OFF par défaut) ───
        # Niveaux du jour/semaine UTC clôturés (smc.calendar_liquidity_levels).
        #   "targets" : cibles de TP additionnelles (concurrence liquidité/void)
        #   "sweeps"  : déclencheur SWEEP_REVERSAL sur prise du niveau + rejet
        #   True      : les deux usages ; False : inactif (byte-identique).
        "use_calendar_liquidity": False,
    }

    def __init__(self):
        # Cache backtest : {index_barre: signal_dict} construit en une passe.
        self._bt_signals: Optional[Dict[int, dict]] = None
        self._bt_events_opposite: Optional[Dict[int, str]] = None
        self._bt_close_ref: Optional[np.ndarray] = None
        # Cache d'analyse live/scanner : (res, aux) mémoïsés tant que la
        # dernière barre close et les paramètres ne changent pas. Évite de
        # relancer smc.analyze (+ HTF) à chaque cycle de scan (60 s) alors que
        # le df est identique entre deux clôtures de barre, et de le refaire 3×
        # dans les endpoints (score + trade_plans + endpoint).
        self._ana_key: Optional[tuple] = None
        self._ana_res: Optional[dict] = None
        self._ana_aux: Optional[dict] = None

    # ── Paramètres ───────────────────────────────────────────────────────────
    def _p(self, params: dict = None) -> Dict[str, Any]:
        p = dict(self.fixed_params)
        for k, v in ((params or {}).get(self.name, {}) or {}).items():
            if k in p and v is not None:
                p[k] = v
        return p

    @staticmethod
    def _smc_params(p: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "swing_left":    int(p["swing_len"]),
            "swing_right":   int(p["swing_len"]),
            "eq_tol_atr":    float(p["eq_tol_atr"]),
            "disp_body_atr": float(p["disp_body_atr"]),
            "ob_lookback":   int(p["ob_lookback"]),
            "fvg_min_atr":   float(p["fvg_min_atr"]),
            "rb_wick_atr":   float(p.get("rb_wick_atr", 0.5)),
        }

    def _build_aux(self, win: pl.DataFrame, p: Dict[str, Any],
                   res: dict) -> Dict[str, Any]:
        """Séries auxiliaires par barre (toutes causales) consommées par
        ``_signal_at`` : volume ratio, EMA de biais, killzones, biais HTF et
        compression AMD (range des ``amd_bars`` barres PRÉCÉDENTES ≤ k×ATR)."""
        aux: Dict[str, Any] = {
            "volr": self._vol_ratio_arr(win),
            "ema":  self._ema_arr(win, int(p["ema_filter_len"])),
            "h": win["high"].to_numpy().astype(float),
            "l": win["low"].to_numpy().astype(float),
            "c": win["close"].to_numpy().astype(float),
            "v": win["volume"].to_numpy().astype(float),
        }
        # Killzones (nécessite la colonne time ; sinon neutre)
        if (p.get("kz_bonus") or p.get("kz_filter")) and "time" in win.columns:
            ep = win["time"].dt.epoch(time_unit="s").to_numpy().astype(np.int64)
            aux["kz"] = smc.killzone_flags(ep)
        else:
            aux["kz"] = None
        # Biais multi-timeframe — HTF cible aligné sur _HTF_MAP (secondes),
        # fallback ×htf_mult si le LTF détecté n'y figure pas.
        if str(p.get("htf_filter", "off")) != "off":
            aux["htf"], aux["htf_meta"] = smc.htf_trend_series(
                win, self._smc_params(p), mult=int(p.get("htf_mult", 4)),
                htf_sec_map=_HTF_SEC_MAP)
        else:
            aux["htf"], aux["htf_meta"] = None, None
        # Compression AMD : range des m barres précédentes (barre courante
        # exclue — c'est elle qui fait la manipulation)
        if p.get("amd_bonus"):
            m = int(p["amd_bars"])
            hi = win["high"].rolling_max(m).shift(1)
            lo = win["low"].rolling_min(m).shift(1)
            rng = (hi - lo).fill_null(float("inf")).to_numpy().astype(float)
            aux["comp"] = rng <= float(p["amd_range_atr"]) * res["_atr_arr"]
        else:
            aux["comp"] = None
        # 1d — structure de degré supérieur (ext_structure_filter, off par
        # défaut) : 2e analyse causale à pivots plus larges → tendance externe
        # par barre, consommée comme gate additionnel. None si désactivé.
        if bool(p.get("ext_structure_filter", False)):
            ext_sp = dict(self._smc_params(p))
            L = int(p.get("ext_swing_len", 8))
            ext_sp["swing_left"] = ext_sp["swing_right"] = L
            aux["ext_trend"] = smc.analyze(win, ext_sp)["_trend_arr"]
        else:
            aux["ext_trend"] = None
        # Filtre Choppiness (congestion) : série par barre si actif, sinon None.
        if float(p.get("chop_filter_max", 0.0)) > 0:
            aux["chop"] = _choppiness(win, int(p.get("chop_len", 14))) \
                .fill_null(50.0).to_numpy().astype(float)
        else:
            aux["chop"] = None
        # Confirmation bougie (pin bar / engulfing) : arrays {−1,0,+1} si actif.
        if bool(p.get("candle_bonus", False)):
            aux["pin"] = _pin_bar(win).to_numpy().astype(np.int8)
            aux["eng"] = _engulfing(win).to_numpy().astype(np.int8)
        else:
            aux["pin"] = aux["eng"] = None
        # SMT divergence vs actif corrélé (off par défaut) : {−1,0,+1} par barre,
        # chargé + aligné causalement par ``smc.smt_series``. None si désactivé
        # ou données du corrélé absentes (dégradation gracieuse).
        if (bool(p.get("smt_bonus", False)) or bool(p.get("smt_filter", False))) \
                and str(p.get("smt_correlate_path", "")):
            aux["smt"] = smc.smt_series(win, str(p["smt_correlate_path"]),
                                        int(p.get("smt_lookback", 20)))
        else:
            aux["smt"] = None
        # SMC-03 — liquidité calendaire PDH/PDL/PWH/PWL (off par défaut) :
        # niveaux causals du jour/semaine UTC clôturés, par barre.
        if p.get("use_calendar_liquidity", False) and "time" in win.columns:
            aux["cal"] = smc.calendar_liquidity_levels(win)
        else:
            aux["cal"] = None
        return aux

    @staticmethod
    def _pkey(p: Dict[str, Any]) -> tuple:
        """Signature hachable des paramètres qui influent sur l'analyse/aux."""
        return tuple(sorted((k, str(v)) for k, v in p.items()))

    def _analyze_cached(self, win: pl.DataFrame, p: Dict[str, Any]):
        """Retourne ``(res, aux)`` pour la fenêtre ``win``, mémoïsé sur
        (hauteur, timestamp de la dernière barre, paramètres). Recalcule
        uniquement quand une nouvelle barre close ou que la config change."""
        ts = None
        if "time" in win.columns and win.height:
            try:
                ts = int(win["time"][-1].timestamp())
            except (AttributeError, TypeError):
                ts = str(win["time"][-1])
        key = (win.height, ts, self._pkey(p)) if ts is not None else None
        if key is not None and key == self._ana_key \
                and self._ana_res is not None:
            return self._ana_res, self._ana_aux
        res = smc.analyze(win, self._smc_params(p))
        aux = self._build_aux(win, p, res)
        if key is not None:
            self._ana_key, self._ana_res, self._ana_aux = key, res, aux
        return res, aux

    def min_bars_required(self, params: dict = None) -> int:
        return 220

    # ── Pré-calcul backtest : une seule passe sur toute la fenêtre ───────────
    def prepare_for_backtest(self, df: pl.DataFrame) -> None:
        try:
            params = getattr(self, "_bt_params", None)
            p = self._p(params)
            res = smc.analyze(df, self._smc_params(p))
            close = df["close"].to_numpy().astype(float)
            open_ = df["open"].to_numpy().astype(float)
            low   = df["low"].to_numpy().astype(float)
            high  = df["high"].to_numpy().astype(float)
            aux   = self._build_aux(df, p, res)

            signals: Dict[int, dict] = {}
            event_bars = sorted(
                {ev["index"] for ev in res["_all_sweeps"] if ev["rejected"]} |
                {ob["touched_at"] for ob in res["_all_obs"]
                 if ob["touched_at"] is not None} |
                {brk["touched_at"] for brk in res["_all_breakers"]
                 if brk["touched_at"] is not None} |
                {rb["touched_at"] for rb in res["_all_rejections"]
                 if rb["touched_at"] is not None}
            )
            for i in event_bars:
                sig = self._signal_at(res, i, open_, high, low, close, aux, p)
                if sig is not None:
                    signals[i] = sig

            # Événements de structure opposés (early exit CHoCH)
            opposite: Dict[int, str] = {}
            for ev in res["_all_struct_events"]:
                if ev["kind"] == "CHoCH":
                    opposite[ev["index"]] = ev["direction"]

            self._bt_signals = signals
            self._bt_events_opposite = opposite
            self._bt_close_ref = close
        except Exception as e:
            logger.warning(f"[smart_money] prepare_for_backtest KO : {e}")
            self._bt_signals = None
            self._bt_events_opposite = None
            self._bt_close_ref = None

    def _cache_valid(self, df: pl.DataFrame) -> bool:
        if self._bt_signals is None or self._bt_close_ref is None:
            return False
        idx = df.height - 1
        ref = self._bt_close_ref
        return 0 <= idx < len(ref) and abs(float(df["close"][-1]) - ref[idx]) < 1e-9

    # ── Scoring ──────────────────────────────────────────────────────────────
    def score(self, df: pl.DataFrame, params: dict = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        p = self._p(params)
        if len(df) < self.min_bars_required(params):
            return self._none("historique insuffisant")

        # Chemin backtest : lookup O(1) dans le cache pré-calculé.
        if self._cache_valid(df):
            sig = self._bt_signals.get(df.height - 1)
            return dict(sig) if sig else self._none("aucun setup SMC")

        # Chemin live/scanner : analyse de la fenêtre (bornée à max_window),
        # mémoïsée tant que la dernière barre close ne change pas.
        win = df[-int(p["max_window"]):] if len(df) > int(p["max_window"]) else df
        res, aux = self._analyze_cached(win, p)
        i = len(win) - 1
        close = win["close"].to_numpy().astype(float)
        open_ = win["open"].to_numpy().astype(float)
        low   = win["low"].to_numpy().astype(float)
        high  = win["high"].to_numpy().astype(float)
        sig = self._signal_at(res, i, open_, high, low, close, aux, p)
        return sig if sig else self._none(
            f"aucun setup SMC (bias {res['bias']['label']})"
        )

    # ── Plans de trade : signal immédiat + setups EN ATTENTE ─────────────────
    def trade_plans(self, df: pl.DataFrame, params: dict = None,
                    max_plans: int = 8) -> List[dict]:
        """Liste des trades à ouvrir (ou à surveiller) sur la dernière bougie :

          - le signal immédiat s'il existe (``status: "immediate"``) ;
          - les retests d'order blocks FRAIS alignés avec la structure
            (``status: "pending"``) : entrée au bord de la zone, SL de l'autre
            côté, TP par ciblage de liquidité — avec le déclencheur à attendre ;
          - les sweeps potentiels des poches de liquidité actives alignées.

        Chaque plan respecte les mêmes filtres durs que la stratégie (tendance,
        côté momentum, EMA, gain > ``min_gain_pct``, RR ≥ ``min_rr``) ; les
        confluences dépendantes de la bougie de déclenchement (volume, couleur)
        ne sont pas connues d'avance → le score affiché est un score MINIMUM.
        """
        p = self._p(params)
        if len(df) < self.min_bars_required(params):
            return []
        win = df[-int(p["max_window"]):] if len(df) > int(p["max_window"]) else df
        res, aux = self._analyze_cached(win, p)
        i = len(win) - 1
        close = win["close"].to_numpy().astype(float)
        open_ = win["open"].to_numpy().astype(float)
        low   = win["low"].to_numpy().astype(float)
        high  = win["high"].to_numpy().astype(float)
        ema   = aux["ema"]
        atr   = float(res["_atr_arr"][i])
        price = float(close[i])
        if atr <= 0 or price <= 0:
            return []
        trend = int(res["_trend_arr"][i])
        pd_zone = smc.premium_discount_at(res, high, low, close, i) or {}
        zone = pd_zone.get("zone", "")
        long_ema_ok  = ema is None or price > float(ema[i])
        short_ema_ok = ema is None or price < float(ema[i])
        # Biais HTF appliqué aussi aux plans en attente (même helper que le signal)
        htf_mode = str(p.get("htf_filter", "off"))
        htf_t = int(aux["htf"][i]) if aux["htf"] is not None else 0
        long_htf_ok, short_htf_ok = self._htf_ok(htf_mode, htf_t)
        # 1d — structure externe (off par défaut) : composé avec le gate HTF,
        # cohérent avec _signal_at. Neutre si ext_trend None.
        ext = aux.get("ext_trend")
        if ext is not None:
            et = int(ext[i])
            long_htf_ok = long_htf_ok and et >= 0
            short_htf_ok = short_htf_ok and et <= 0
        buf = float(p["sl_buffer_atr"]) * atr
        max_ob_age = int(p["ob_max_age"])
        plans: List[dict] = []

        def _add(plan: Optional[dict], status: str, trigger: str,
                 zone_lo=None, zone_hi=None):
            if plan is None:
                return
            entry = plan.get("entry")
            dist = (entry - price) / price * 100.0 if entry else 0.0
            plans.append({
                "status": status, "side": plan["side"], "setup": plan["setup"],
                "score_min": plan["score"],
                "entry": plan["entry"], "stop": plan["stop_hint"],
                "tp": plan["tp_hint"],
                "gain_pct": plan["indicators"]["gain_pct"],
                "rr": plan["indicators"]["rr"],
                "tp_source": plan["indicators"]["tp_source"],
                "distance_pct": round(dist, 3),
                "trigger": trigger, "reason": plan["reason"],
                "zone_low": zone_lo, "zone_high": zone_hi,
            })

        # ── 1. Signal immédiat (bougie courante) ─────────────────────────────
        sig = self._signal_at(res, i, open_, high, low, close, aux, p)
        if sig is not None:
            sig = dict(sig)
            sig["entry"] = price
            _add(sig, "immediate",
                 "Déclenché sur la bougie courante — entrée au prochain open")

        # ── 2. Retests d'order blocks / rejection blocks FRAIS alignés ───────
        pending_zones = list(res["_all_obs"])
        if bool(p.get("use_rejection_blocks", False)):
            pending_zones += list(res["_all_rejections"])
        for ob in reversed(pending_zones):
            if ob["touched_at"] is not None or ob["invalidated_at"] is not None:
                continue
            if i - ob["created_at"] > max_ob_age:
                continue
            if ob["kind"] == "bullish" and price > ob["top"] \
                    and self._dir_gate("long", trend, zone, long_ema_ok, long_htf_ok):
                sc = 0.50 + 0.10 + (0.10 if ob.get("strength", 1) >= 2 else 0.0) \
                    + (0.10 if zone == "premium" else 0.0)
                plan = self._build_trade(
                    res, i, "long", float(ob["top"]),
                    float(ob["bottom"]) - buf, atr, p,
                    setup="OB_RETEST", score=round(min(sc, 1.0), 3),
                    detail=f"demande [{ob['bottom']:.6g}–{ob['top']:.6g}]",
                    trend=trend, zone=zone)
                if plan:
                    plan["entry"] = float(ob["top"])
                _add(plan, "pending",
                     f"Attendre le retour du prix dans la zone de demande "
                     f"[{ob['bottom']:.6g}–{ob['top']:.6g}] + bougie de rejet",
                     zone_lo=ob["bottom"], zone_hi=ob["top"])
            elif ob["kind"] == "bearish" and price < ob["bottom"] \
                    and self._dir_gate("short", trend, zone, short_ema_ok, short_htf_ok):
                sc = 0.50 + 0.10 + (0.10 if ob.get("strength", 1) >= 2 else 0.0) \
                    + (0.10 if zone == "discount" else 0.0)
                plan = self._build_trade(
                    res, i, "short", float(ob["bottom"]),
                    float(ob["top"]) + buf, atr, p,
                    setup="OB_RETEST", score=round(min(sc, 1.0), 3),
                    detail=f"offre [{ob['bottom']:.6g}–{ob['top']:.6g}]",
                    trend=trend, zone=zone)
                if plan:
                    plan["entry"] = float(ob["bottom"])
                _add(plan, "pending",
                     f"Attendre le retour du prix dans la zone d'offre "
                     f"[{ob['bottom']:.6g}–{ob['top']:.6g}] + bougie de rejet",
                     zone_lo=ob["bottom"], zone_hi=ob["top"])

        # ── 3. Sweeps potentiels des poches de liquidité actives ─────────────
        for pool in reversed(res["_all_pools"]):
            if pool["swept_at"] is not None or i - pool["formed_at"] > int(p["pool_max_age"]):
                continue
            lvl = float(pool["level"])
            if pool["kind"] == "sell_side" and lvl < price \
                    and self._dir_gate("long", trend, zone, long_ema_ok, long_htf_ok):
                sc = 0.50 + 0.10 + 0.10 + (0.10 if zone == "premium" else 0.0)
                # Entrée estimée au niveau sweepé ; la mèche du sweep est
                # inconnue d'avance → marge d'½ ATR sous le niveau.
                plan = self._build_trade(
                    res, i, "long", lvl, lvl - 0.5 * atr - buf, atr, p,
                    setup="SWEEP_REVERSAL", score=round(min(sc, 1.0), 3),
                    detail=f"sweep pool {lvl:.6g}", trend=trend, zone=zone)
                if plan:
                    plan["entry"] = lvl
                _add(plan, "pending",
                     f"Attendre une mèche SOUS les equal lows {lvl:.6g} "
                     f"(×{len(pool['indices'])}) avec clôture au-dessus (rejet)",
                     zone_lo=pool["bottom"], zone_hi=pool["top"])
            elif pool["kind"] == "buy_side" and lvl > price \
                    and self._dir_gate("short", trend, zone, short_ema_ok, short_htf_ok):
                sc = 0.50 + 0.10 + 0.10 + (0.10 if zone == "discount" else 0.0)
                plan = self._build_trade(
                    res, i, "short", lvl, lvl + 0.5 * atr + buf, atr, p,
                    setup="SWEEP_REVERSAL", score=round(min(sc, 1.0), 3),
                    detail=f"sweep pool {lvl:.6g}", trend=trend, zone=zone)
                if plan:
                    plan["entry"] = lvl
                _add(plan, "pending",
                     f"Attendre une mèche AU-DESSUS des equal highs {lvl:.6g} "
                     f"(×{len(pool['indices'])}) avec clôture en dessous (rejet)",
                     zone_lo=pool["bottom"], zone_hi=pool["top"])

        # Tri : signal immédiat d'abord, puis plans les plus proches du prix.
        plans.sort(key=lambda x: (x["status"] != "immediate",
                                  abs(x["distance_pct"])))
        return plans[:max_plans]

    # ── Sortie anticipée : time-stop conditionnel (trailing) + CHoCH ─────────
    def check_early_exit(self, df: pl.DataFrame, position: dict,
                         params: dict = None) -> Optional[str]:
        p = self._p(params)
        # Time-stop CONDITIONNEL (mode trailing) : coupe un trade qui STAGNE —
        # jamais atteint +ts_profit_r×R après time_stop_bars barres. Un gagnant
        # qui court (MFE au-delà du seuil) n'est PAS coupé : le trailing gère.
        ts_bars = int(p.get("time_stop_bars", 0) or 0)
        if bool(p.get("use_trailing", False)) and ts_bars > 0:
            bars_held = (df.height - 1) - int(position.get("bar", df.height))
            if bars_held >= ts_bars:
                ind = position.get("indicators") or {}
                risk_pct = float(ind.get("_risk_pct") or 0.0)
                if risk_pct <= 0:
                    st = position.get("_stop_trail") or []
                    e = float(position.get("entry") or 0.0)
                    if st and e > 0:
                        risk_pct = abs(e - float(st[0]["stop"])) / e * 100.0
                mfe = float(position.get("mfe", 0.0))
                if risk_pct > 0 and mfe < float(p.get("ts_profit_r", 1.0)) * risk_pct:
                    return "time_stop_stall"

        if not bool(p.get("choch_exit", True)):
            return None
        idx = df.height - 1
        direction = None
        if (self._bt_events_opposite is not None and self._bt_close_ref is not None
                and idx < len(self._bt_close_ref)
                and abs(float(df["close"][-1]) - self._bt_close_ref[idx]) < 1e-9):
            direction = self._bt_events_opposite.get(idx)
        else:
            # Live : réutilise l'analyse mémoïsée (même fenêtre/cache que
            # score()) au lieu de relancer un analyze dédié par position/cycle.
            win = df[-int(p["max_window"]):] if len(df) > int(p["max_window"]) else df
            res, _ = self._analyze_cached(win, p)
            last = len(win) - 1
            for ev in reversed(res["_all_struct_events"]):
                if ev["index"] < last:
                    break
                if ev["kind"] == "CHoCH":
                    direction = ev["direction"]
                    break
        if direction is None:
            return None
        side = position.get("side")
        if side == "long" and direction == "down":
            return "smc_choch_down"
        if side == "short" and direction == "up":
            return "smc_choch_up"
        return None

    # ── Cœur : dérivation du signal à la barre i ─────────────────────────────
    # V4-L / ARCH-14 : corps extraits vers smart_money_signals.py —
    # l'assignation d'une fonction (1er arg self) crée de vraies méthodes.
    _signal_at   = _sm_signals._signal_at
    _build_trade = _sm_signals._build_trade


    @staticmethod
    def _htf_ok(htf_mode: str, htf_t: int) -> tuple:
        """Autorisations HTF (long_ok, short_ok) selon le mode de filtre.
        Source unique partagée par _signal_at et trade_plans."""
        long_ok = (htf_mode == "off") \
            or (htf_mode == "soft" and htf_t >= 0) \
            or (htf_mode == "strict" and htf_t == 1)
        short_ok = (htf_mode == "off") \
            or (htf_mode == "soft" and htf_t <= 0) \
            or (htf_mode == "strict" and htf_t == -1)
        return long_ok, short_ok

    @staticmethod
    def _dir_gate(side: str, trend: int, zone: str,
                  ema_ok: bool, htf_ok: bool) -> bool:
        """Filtres directionnels DURS communs aux setups alignés-tendance
        (OB / breaker / rejection / pool sweep) : structure alignée + côté
        momentum du range + biais EMA + biais HTF. Source unique garantissant
        que ``trade_plans`` applique exactement les mêmes filtres que le
        signal réellement pris par le moteur."""
        if side == "long":
            return trend == 1 and zone != "discount" and ema_ok and htf_ok
        return trend == -1 and zone != "premium" and ema_ok and htf_ok

    @staticmethod
    def _choch_index_arrays(res: dict) -> tuple:
        """Indices des CHoCH down/up, triés et mémoïsés sur ``res`` (calculés
        une fois par analyse, réutilisés par tous les appels _signal_at)."""
        cached = res.get("_choch_idx")
        if cached is None:
            evs = res["_all_struct_events"]
            cd = np.array(sorted(e["index"] for e in evs
                                 if e["kind"] == "CHoCH" and e["direction"] == "down"),
                          dtype=np.int64)
            cu = np.array(sorted(e["index"] for e in evs
                                 if e["kind"] == "CHoCH" and e["direction"] == "up"),
                          dtype=np.int64)
            cached = (cd, cu)
            res["_choch_idx"] = cached
        return cached

    @staticmethod
    def _vol_ratio_arr(df: pl.DataFrame) -> np.ndarray:
        # Source unique : indicators_core.volume_ratio (volume / SMA20, division
        # sécurisée). fill_null(0.0) sur les barres de warmup (jamais lues à un
        # index de signal, i >> 260) pour un array numpy propre.
        return _vol_ratio(df, 20).fill_null(0.0).to_numpy().astype(float)

    @staticmethod
    def _ema_arr(df: pl.DataFrame, length: int) -> Optional[np.ndarray]:
        if length <= 0:
            return None
        # Source unique : indicators_core.ema (EMA span=n, adjust=False).
        return _ema_series(df["close"], length).to_numpy().astype(float)

    def _none(self, reason: str = "") -> dict:
        return {"score": 0, "side": "none", "name": self.name, "reason": reason}
