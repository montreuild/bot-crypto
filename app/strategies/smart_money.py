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
from app.core import smc
from app.core.indicators_core import ema as _ema_series, volume_ratio as _vol_ratio
from app.live.utils import _HTF_MAP

logger = logging.getLogger(__name__)


def _tf_to_sec(tf: str) -> int:
    """Convertit un libellé de timeframe (« 4h », « 30m », « 1d ») en secondes."""
    try:
        return int(tf[:-1]) * {"m": 60, "h": 3600, "d": 86400}.get(tf[-1], 60)
    except (ValueError, IndexError):
        return 0


# Biais HTF aligné sur la « source unique de vérité » _HTF_MAP d'app/live/utils
# (secondes LTF → secondes HTF), au lieu d'un multiplicateur ×N arbitraire.
# Validé sur BTC : ≥ aussi bon que ×4 (4h→1d : PF 1.52 vs 1.49).
_HTF_SEC_MAP: Dict[int, int] = {
    _tf_to_sec(k): _tf_to_sec(v)
    for k, v in _HTF_MAP.items()
    if _tf_to_sec(k) and _tf_to_sec(v)
}


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

    # ── Sortie anticipée : CHoCH contre la position ──────────────────────────
    def check_early_exit(self, df: pl.DataFrame, position: dict,
                         params: dict = None) -> Optional[str]:
        if not bool(self._p(params).get("choch_exit", True)):
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
            p = self._p(params)
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
    def _signal_at(self, res: dict, i: int, open_: np.ndarray, high: np.ndarray,
                   low: np.ndarray, close: np.ndarray, aux: Dict[str, Any],
                   p: Dict[str, Any]) -> Optional[dict]:
        """Construit le meilleur signal SMC à la barre ``i`` (causale : seules
        les entités formées avant ``i`` sont utilisées). Retourne None si aucun
        setup ne passe les filtres (dont le gain minimal ``min_gain_pct``)."""
        volr = aux["volr"]
        ema  = aux["ema"]
        trend_arr = res["_trend_arr"]
        atr = float(res["_atr_arr"][i])
        if atr <= 0 or i < 1:
            return None
        trend = int(trend_arr[i])
        c = float(close[i])
        candidates: List[dict] = []

        # ── Filtres/bonus transverses des enrichissements ─────────────────────
        # Killzones : filtre dur optionnel + bonus (sessions LDN/NY = fenêtres
        # où les desks génèrent l'essentiel des sweeps).
        in_kz = bool(aux["kz"][i]) if aux["kz"] is not None else False
        if bool(p.get("kz_filter", False)) and aux["kz"] is not None and not in_kz:
            return None
        kz_add = 0.05 if (bool(p.get("kz_bonus", False)) and in_kz) else 0.0
        # Biais multi-timeframe : la structure du timeframe supérieur commande.
        htf_mode = str(p.get("htf_filter", "off"))
        htf_t = int(aux["htf"][i]) if aux["htf"] is not None else 0
        long_htf_ok, short_htf_ok = self._htf_ok(htf_mode, htf_t)
        # AMD : la barre courante sweepe après une phase de compression
        # (accumulation) → manipulation probable, expansion à suivre.
        amd_here = bool(aux["comp"][i]) if aux["comp"] is not None else False
        amd_add = 0.10 if (bool(p.get("amd_bonus", False)) and amd_here) else 0.0
        # Volume profile : HVN = acceptation (support volumétrique de la zone).
        vp = None
        if bool(p.get("vp_confluence", False)) or bool(p.get("vp_targets", False)):
            vp = smc.volume_profile(aux["h"], aux["l"], aux["c"], aux["v"], i,
                                    lookback=int(p["vp_lookback"]),
                                    n_bins=int(p["vp_bins"]))

        def _vp_add(zone_lo: float, zone_hi: float) -> float:
            if vp is None or not bool(p.get("vp_confluence", False)):
                return 0.0
            pad = 0.25 * atr
            return 0.05 if any(zone_lo - pad <= lv <= zone_hi + pad
                               for lv in vp["hvns"]) else 0.0

        vp_above = sorted([lv for lv in ([vp["poc"]] + vp["hvns"])
                           if lv > c]) if (vp and p.get("vp_targets")) else []
        vp_below = sorted([lv for lv in ([vp["poc"]] + vp["hvns"])
                           if lv < c], reverse=True) \
            if (vp and p.get("vp_targets")) else []

        # Garde CHoCH : pas d'entrée contre un changement de caractère récent.
        # Indices CHoCH pré-triés et mémoïsés sur ``res`` → lookup O(log n) par
        # barre au lieu d'un scan O(événements) (évite le O(événements²) de la
        # passe prepare_for_backtest). Résultat strictement identique.
        guard = int(p["choch_guard_bars"])
        cd, cu = self._choch_index_arrays(res)
        recent_choch_down = bool(
            np.searchsorted(cd, i, "right") - np.searchsorted(cd, i - guard, "left"))
        recent_choch_up = bool(
            np.searchsorted(cu, i, "right") - np.searchsorted(cu, i - guard, "left"))

        # Premium/discount CAUSAL à la barre i (pas l'état final de l'analyse).
        pd_zone = smc.premium_discount_at(res, high, low, close, i) or {}
        zone = pd_zone.get("zone", "")
        vol_ok = bool(volr[i] > float(p["vol_confluence"])) if i < len(volr) else False

        # Biais EMA (filtre institutionnel simple) : long au-dessus, short en
        # dessous. ema is None quand ema_filter_len=0 → filtre désactivé.
        long_ema_ok  = ema is None or c > float(ema[i])
        short_ema_ok = ema is None or c < float(ema[i])

        # Tap de trendline automatique (causal) : la mèche touche la ligne à
        # tl_tol_atr×ATR près et la clôture tient du bon côté.
        tl_tol = float(p["tl_tol_atr"]) * atr
        tl_sup = smc.trendline_value_at(res, i, "support")
        tl_res = smc.trendline_value_at(res, i, "resistance")
        tl_tap_long = (tl_sup is not None
                       and low[i] <= tl_sup + tl_tol and c > tl_sup)
        tl_tap_short = (tl_res is not None
                        and high[i] >= tl_res - tl_tol and c < tl_res)

        # ── A. Sweep reversal ────────────────────────────────────────────────
        # Filtres durs (validés sur BTC/USDC 1h→4h 2019-2026) :
        #   - AVEC la tendance uniquement : un sweep sell-side ne s'achète que
        #     dans une structure haussière (pullback qui prend la liquidité),
        #     jamais en contre-tendance (les deux pires buckets historiques).
        #   - Côté momentum du range : pas de long en zone discount ni de short
        #     en zone premium — sur crypto, la force appelle la force ; le
        #     « deep discount » d'une tendance haussière est le plus souvent
        #     une structure en train de casser.
        allow_ct = bool(p.get("allow_counter_trend", False))
        for ev in res["_all_sweeps"]:
            if ev["index"] != i or not ev["rejected"]:
                continue
            if ev["kind"] == "sell_side" and not recent_choch_down:
                if (trend != 1 and not allow_ct) or zone == "discount" \
                        or not long_ema_ok or not long_htf_ok:
                    continue
                sc = 0.50 + kz_add + amd_add
                sc += 0.10 if trend == 1 else 0.0
                sc += 0.10 if ev["source"] == "pool" else 0.0
                sc += 0.10 if zone == "premium" else 0.0
                sc += 0.05 if vol_ok else 0.0
                sc += 0.05 if close[i] > open_[i] else 0.0
                sc += 0.05 if tl_tap_long else 0.0
                sc += _vp_add(float(ev["level"]) - 0.5 * atr,
                              float(ev["level"]) + 0.5 * atr)
                sl = min(float(low[i]), float(ev["level"])) - \
                    float(p["sl_buffer_atr"]) * atr
                cand = self._build_trade(res, i, "long", c, sl, atr, p,
                                         setup="SWEEP_REVERSAL", score=sc,
                                         detail=f"sweep {ev['source']} "
                                                f"{ev['level']:.6g}",
                                         trend=trend, zone=zone,
                                         extra_targets=vp_above)
                if cand:
                    candidates.append(cand)
            elif ev["kind"] == "buy_side" and not recent_choch_up:
                if (trend != -1 and not allow_ct) or zone == "premium" \
                        or not short_ema_ok or not short_htf_ok:
                    continue
                sc = 0.50 + kz_add + amd_add
                sc += 0.10 if trend == -1 else 0.0
                sc += 0.10 if ev["source"] == "pool" else 0.0
                sc += 0.10 if zone == "discount" else 0.0
                sc += 0.05 if vol_ok else 0.0
                sc += 0.05 if close[i] < open_[i] else 0.0
                sc += 0.05 if tl_tap_short else 0.0
                sc += _vp_add(float(ev["level"]) - 0.5 * atr,
                              float(ev["level"]) + 0.5 * atr)
                sl = max(float(high[i]), float(ev["level"])) + \
                    float(p["sl_buffer_atr"]) * atr
                cand = self._build_trade(res, i, "short", c, sl, atr, p,
                                         setup="SWEEP_REVERSAL", score=sc,
                                         detail=f"sweep {ev['source']} "
                                                f"{ev['level']:.6g}",
                                         trend=trend, zone=zone,
                                         extra_targets=vp_below)
                if cand:
                    candidates.append(cand)

        # ── B. Retest d'order block / rejection block ─────────────────────────
        # Les rejection blocks (mèches de swing) partagent la même mécanique de
        # retest que les OB : zone d'offre/demande née d'un rejet violent.
        max_ob_age = int(p["ob_max_age"])
        zone_sources = [("OB_RETEST", res["_all_obs"])]
        if bool(p.get("use_rejection_blocks", False)):
            zone_sources.append(("REJECTION_RETEST", res["_all_rejections"]))
        for setup_name, zone_list in zone_sources:
            for ob in zone_list:
                if ob["touched_at"] != i or i - ob["created_at"] > max_ob_age:
                    continue
                if ob["invalidated_at"] is not None and ob["invalidated_at"] <= i:
                    continue
                strength2 = ob.get("strength", 1) >= 2
                if ob["kind"] == "bullish" and trend == 1 and not recent_choch_down:
                    if c < ob["bottom"]:
                        continue          # zone déjà transpercée sur clôture
                    if zone == "discount" or not long_ema_ok or not long_htf_ok:
                        continue          # côté momentum uniquement (cf. sweeps)
                    sc = 0.50 + 0.10 + kz_add   # structure alignée par construction
                    sc += 0.10 if strength2 else 0.0
                    sc += 0.10 if zone == "premium" else 0.0
                    sc += 0.05 if self._fvg_overlap(res, i, "bullish",
                                                    ob["bottom"], ob["top"]) else 0.0
                    sc += 0.05 if vol_ok else 0.0
                    sc += 0.05 if close[i] > open_[i] else 0.0
                    sc += 0.05 if tl_tap_long else 0.0
                    sc += _vp_add(ob["bottom"], ob["top"])
                    sl = float(ob["bottom"]) - float(p["sl_buffer_atr"]) * atr
                    cand = self._build_trade(res, i, "long", c, sl, atr, p,
                                             setup=setup_name, score=sc,
                                             detail=f"demande [{ob['bottom']:.6g}"
                                                    f"–{ob['top']:.6g}]",
                                             trend=trend, zone=zone,
                                             extra_targets=vp_above)
                    if cand:
                        candidates.append(cand)
                elif ob["kind"] == "bearish" and trend == -1 and not recent_choch_up:
                    if c > ob["top"]:
                        continue
                    if zone == "premium" or not short_ema_ok or not short_htf_ok:
                        continue          # côté momentum uniquement (cf. sweeps)
                    sc = 0.50 + 0.10 + kz_add
                    sc += 0.10 if strength2 else 0.0
                    sc += 0.10 if zone == "discount" else 0.0
                    sc += 0.05 if self._fvg_overlap(res, i, "bearish",
                                                    ob["bottom"], ob["top"]) else 0.0
                    sc += 0.05 if vol_ok else 0.0
                    sc += 0.05 if close[i] < open_[i] else 0.0
                    sc += 0.05 if tl_tap_short else 0.0
                    sc += _vp_add(ob["bottom"], ob["top"])
                    sl = float(ob["top"]) + float(p["sl_buffer_atr"]) * atr
                    cand = self._build_trade(res, i, "short", c, sl, atr, p,
                                             setup=setup_name, score=sc,
                                             detail=f"offre [{ob['bottom']:.6g}"
                                                    f"–{ob['top']:.6g}]",
                                             trend=trend, zone=zone,
                                             extra_targets=vp_below)
                    if cand:
                        candidates.append(cand)

        # ── C. Retest de breaker block (OB invalidé → polarité inversée) ────
        # Le transpercement d'un OB accompagne le plus souvent un CHoCH : le
        # premier retest de la zone dans le NOUVEAU sens attrape les stops
        # piégés. Mêmes filtres durs que les autres setups.
        if bool(p.get("use_breakers", True)):
            for brk in res["_all_breakers"]:
                if brk["touched_at"] != i or i - brk["created_at"] > max_ob_age:
                    continue
                if brk["invalidated_at"] is not None and brk["invalidated_at"] <= i:
                    continue
                if brk["kind"] == "bullish" and trend == 1 \
                        and not recent_choch_down:
                    if c < brk["bottom"] or zone == "discount" \
                            or not long_ema_ok or not long_htf_ok:
                        continue
                    sc = 0.50 + 0.10 + kz_add
                    sc += 0.10 if zone == "premium" else 0.0
                    sc += 0.05 if vol_ok else 0.0
                    sc += 0.05 if close[i] > open_[i] else 0.0
                    sc += 0.05 if tl_tap_long else 0.0
                    sc += _vp_add(brk["bottom"], brk["top"])
                    sl = float(brk["bottom"]) - float(p["sl_buffer_atr"]) * atr
                    cand = self._build_trade(res, i, "long", c, sl, atr, p,
                                             setup="BREAKER_RETEST", score=sc,
                                             detail=f"breaker [{brk['bottom']:.6g}"
                                                    f"–{brk['top']:.6g}]",
                                             trend=trend, zone=zone,
                                             extra_targets=vp_above)
                    if cand:
                        candidates.append(cand)
                elif brk["kind"] == "bearish" and trend == -1 \
                        and not recent_choch_up:
                    if c > brk["top"] or zone == "premium" \
                            or not short_ema_ok or not short_htf_ok:
                        continue
                    sc = 0.50 + 0.10 + kz_add
                    sc += 0.10 if zone == "discount" else 0.0
                    sc += 0.05 if vol_ok else 0.0
                    sc += 0.05 if close[i] < open_[i] else 0.0
                    sc += 0.05 if tl_tap_short else 0.0
                    sc += _vp_add(brk["bottom"], brk["top"])
                    sl = float(brk["top"]) + float(p["sl_buffer_atr"]) * atr
                    cand = self._build_trade(res, i, "short", c, sl, atr, p,
                                             setup="BREAKER_RETEST", score=sc,
                                             detail=f"breaker [{brk['bottom']:.6g}"
                                                    f"–{brk['top']:.6g}]",
                                             trend=trend, zone=zone,
                                             extra_targets=vp_below)
                    if cand:
                        candidates.append(cand)

        if not candidates:
            return None
        best = max(candidates, key=lambda x: x["score"])
        best["score"] = round(min(best["score"], 1.0), 3)
        # Seuil interne par TF (surchargable par optimizer_results, contrairement
        # à score_threshold) : mêmes sémantiques que le seuil de l'engine.
        if best["score"] < float(p.get("min_score", 0.0)):
            return None
        return best

    # ── Construction du trade : ciblage liquidité + filtre 0.4 % ────────────
    def _build_trade(self, res: dict, i: int, side: str, entry: float,
                     sl: float, atr: float, p: Dict[str, Any],
                     setup: str, score: float, detail: str,
                     trend: int = 0, zone: str = "",
                     extra_targets: Optional[List[float]] = None) -> Optional[dict]:
        risk = (entry - sl) if side == "long" else (sl - entry)
        if risk <= 0 or entry <= 0:
            return None
        min_gain = float(p["min_gain_pct"])
        min_rr   = float(p["min_rr"])
        front    = float(p["tp_front_run_atr"]) * atr
        max_age  = int(p["pool_max_age"])

        # Cibles opposées : poches de liquidité + bords des liquidity voids non
        # comblés (zones fines = aimants). Première cible satisfaisant à la
        # fois le gain minimal (0.4 % par défaut) ET le RR minimal.
        use_voids = bool(p.get("use_void_targets", True))
        tp = None
        tp_src = ""
        if side == "long":
            liq = smc.liquidity_targets_above(res, i, entry, max_age=max_age)
            vds = smc.void_targets_above(res, i, entry, max_age=max_age) \
                if use_voids else []
            vps = [lv for lv in (extra_targets or []) if lv > entry]
            targets = sorted({(lv, "liquidité") for lv in liq} |
                             {(lv, "void") for lv in vds} |
                             {(lv, "volume") for lv in vps})
            for level, src in targets:
                cand = level - front
                gain_pct = (cand - entry) / entry * 100.0
                if gain_pct <= 0:
                    continue
                if gain_pct > min_gain and (cand - entry) / risk >= min_rr:
                    tp, tp_src = cand, f"{src} {level:.6g}"
                    break
        else:
            liq = smc.liquidity_targets_below(res, i, entry, max_age=max_age)
            vds = smc.void_targets_below(res, i, entry, max_age=max_age) \
                if use_voids else []
            vps = [lv for lv in (extra_targets or []) if lv < entry]
            targets = sorted({(lv, "liquidité") for lv in liq} |
                             {(lv, "void") for lv in vds} |
                             {(lv, "volume") for lv in vps}, reverse=True)
            for level, src in targets:
                cand = level + front
                gain_pct = (entry - cand) / entry * 100.0
                if gain_pct <= 0:
                    continue
                if gain_pct > min_gain and (entry - cand) / risk >= min_rr:
                    tp, tp_src = cand, f"{src} {level:.6g}"
                    break

        # Fallback : cible en multiple de R si aucune poche exploitable.
        if tp is None:
            rr = float(p["tp_rr_fallback"])
            tp = entry + rr * risk if side == "long" else entry - rr * risk
            tp_src = f"{rr:g}R"

        gain_pct = abs(tp - entry) / entry * 100.0
        rr_final = abs(tp - entry) / risk

        # ⚠ FILTRE : gain potentiel > min_gain_pct sinon position rejetée.
        if gain_pct <= min_gain or rr_final < min_rr:
            return None

        bias_label = {1: "haussier", -1: "baissier", 0: "neutre"}[int(trend)]
        arrow = "LONG" if side == "long" else "SHORT"
        return {
            "score": score, "side": side, "name": self.name, "atr": atr,
            "setup": setup,
            "stop_hint": round(sl, 8),
            "tp_hint":   round(tp, 8),
            "disable_trailing": True,
            "indicators": {
                "bias":     bias_label,
                "pd_zone":  zone or None,
                "gain_pct": round(gain_pct, 3),
                "rr":       round(rr_final, 2),
                "tp_source": tp_src,
            },
            "reason": (f"{arrow} {setup} : {detail} — TP {tp_src} "
                       f"(gain {gain_pct:.2f}% > {min_gain:g}%, RR {rr_final:.2f}), "
                       f"bias {bias_label}"),
        }

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
    def _fvg_overlap(res: dict, i: int, kind: str,
                     zone_lo: float, zone_hi: float) -> bool:
        """True si un FVG ouvert de même direction chevauche la zone [lo, hi]."""
        for fv in res["_all_fvgs"]:
            if fv["kind"] != kind or fv["index"] >= i:
                continue
            if fv["filled_at"] is not None and fv["filled_at"] <= i:
                continue
            if fv["bottom"] <= zone_hi and fv["top"] >= zone_lo:
                return True
        return False

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
