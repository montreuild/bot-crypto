"""Route backtest — POST /api/backtest."""
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.api import state
from app.api.helpers import _clean, _discover_strategies, _get_bt_exchange, detect_ohlcv_gaps, verify_api_key
from app.core.candle_store import get_store
from app.core.param_resolution import DEFAULT_CONFIG_SYMBOL

logger = logging.getLogger(__name__)
router = APIRouter()


def _cost_model_for(symbol: str, tf: str) -> dict:
    """Contexte d'exécution facturé pour ce couple (symbole, timeframe).

    Best-effort : un modèle de coûts indisponible ne doit jamais faire échouer
    un backtest — l'UI masque simplement la carte.
    """
    try:
        from app.core.bot_identity import resolve_venue
        from app.core.execution import cost_model
        return cost_model(state.cfg, resolve_venue(state.cfg, tf=tf, symbol=symbol))
    except Exception as e:      # pragma: no cover — défensif
        logger.debug(f"[API] modèle de coûts indisponible ({symbol}/{tf}) : {e}")
        return {}


# ── QW-2 : plage temporelle disponible (exigence 5) ───────────────────────────
@router.get("/api/backtest/range", dependencies=[Depends(verify_api_key)])
@state.limiter.limit("30/minute")
def backtest_range(request: Request, symbol: str, timeframe: str = "1h"):
    """Retourne la plage temporelle disponible pour un (symbole, timeframe).

    Exigence 5 : permet à l'UI de proposer un bouton « Max disponible » qui
    pré-remplit ``start_date``/``end_date`` à partir du cache local. Ne
    déclenche PAS de fetch réseau — lecture pure du Parquet si présent.

    Returns
    -------
    dict
        {
            "symbol": str,
            "timeframe": str,
            "from": str (ISO),
            "to": str (ISO),
            "bars": int,
            "available": bool  # False si aucune donnée en cache
        }
    """
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    try:
        tf = timeframe.strip() or state.cfg["trading"].get("timeframe", "1h")
        # `stats()` lit le Parquet complet : il donne la VRAIE plage du cache.
        # Ne pas la dériver d'un `fetch(total=N)`, qui ne renvoie que les N
        # dernières bougies et rapporterait donc une plage tronquée.
        stats = get_store().stats(symbol, tf) or {}
        bars = int(stats.get("bars") or 0)
        if bars == 0:
            return {"symbol": symbol, "timeframe": tf, "from": None, "to": None,
                    "bars": 0, "available": False}
        return {
            "symbol": symbol,
            "timeframe": tf,
            # `stats()` renvoie déjà des datetimes stringifiés (UTC implicite) ;
            # on tronque au format "YYYY-MM-DDTHH:MM" attendu par l'UI.
            "from": str(stats.get("from") or "")[:16] or None,
            "to": str(stats.get("to") or "")[:16] or None,
            "bars": bars,
            "available": True,
        }
    except Exception as e:
        logger.warning(f"[API] backtest/range KO ({symbol}/{timeframe}) : {e}")
        err_id = uuid.uuid4()
        logger.error(f"[API] Erreur {err_id} backtest/range : {e}", exc_info=True)
        raise HTTPException(500, f"Erreur interne ({err_id})")


@router.post("/api/backtest/cancel", dependencies=[Depends(verify_api_key)])
@state.limiter.limit("30/minute")
def cancel_backtest(request: Request):
    """Annule le backtest en cours en levant le signal d'arrêt."""
    state._bt_cancel_event.set()
    from app.engine.compute_pool import request_cancel
    request_cancel()
    return {"status": "cancelling"}


@router.get("/api/backtest/status", dependencies=[Depends(verify_api_key)])
def backtest_status():
    """Indique si un backtest est en cours côté serveur.

    Utilisé par l'UI au chargement de la page pour désactiver le bouton 'Lancer'
    si un backtest tourne déjà (ex. après reload pendant un long backtest).
    """
    # Le sémaphore est libre = pas de backtest en cours ; on l'acquiert puis on
    # le relâche aussitôt — le pattern évite une race avec ``run_backtest``.
    running = not state._bt_semaphore.acquire(blocking=False)
    if not running:
        state._bt_semaphore.release()
    return {"running": running}



def _slot_envelope(strategy: str, tf: str, symbol: str):
    """Enveloppe du slot pour la passe LIVE.

    Priorité à celle du trader quand il tourne : c'est l'échelle qui trade
    vraiment. Hors trader, on résout comme si ce bot était seul sur son
    symbole — l'hypothèse la plus lisible pour une étude au Laboratoire.
    """
    from app.core.bot_identity import build_slot_key
    from app.core.risk_envelope import envelopes_for_active_slots

    key = build_slot_key(strategy, tf, symbol)
    live = (getattr(state.trader, "envelopes", None) or {}) if state.trader else {}
    if key in live:
        return live[key]
    resolved = envelopes_for_active_slots(
        state.cfg, {tf: [{"name": strategy, "symbol": symbol}]}, default_symbol=symbol)
    return resolved.get(key)


def _pass_summary(res) -> dict:
    """Résumé comparable d'une passe : le PnL est en % de SA base, sinon les
    deux passes ne se comparent pas (c'est tout l'objet de l'exercice)."""
    base = res.initial_capital or 0.0
    return {
        "initial_capital": round(base, 4),
        "total_trades":    res.total_trades,
        "total_pnl":       round(res.total_pnl, 4),
        "pnl_pct":         round(res.total_pnl / base * 100, 4) if base else 0.0,
        "max_drawdown":    round(res.max_drawdown, 4),
        "rejections":      res.rejections,
    }


def _envelope_payload(env) -> dict | None:
    if env is None:
        return None
    return {"venue": env.venue, "symbol": env.symbol, "currency": env.currency,
            "slot_envelope": round(env.slot_envelope, 4),
            "weight": round(env.weight, 4),
            "trade_risk_pct": env.trade_risk_pct,
            "risk_amount": round(env.slot_risk_amount, 4),
            "min_notional": env.min_notional}


def _parse_ohlcv_bound(raw: str, end_of_day: bool):
    """Parse une date ISO en datetime NAÏF (UTC implicite).

    La colonne ``time`` du CandleStore est un ``datetime[ms]`` sans
    timezone (UTC implicite). Comparer une colonne naïve à un littéral
    tz-aware lève un ``SchemaError`` polars — d'où le parsing naïf ici.
    """
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        dt = datetime.fromisoformat(
            raw + ("T23:59:59" if end_of_day else "T00:00:00")
        )
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _filter_df_by_date_range(df, start_date: str = "", end_date: str = ""):
    """QW-2 : filtre le DataFrame par plage temporelle (ISO 8601).

    Exigence 5 : permet à l'utilisateur de spécifier une plage de dates
    précise plutôt qu'un nombre de bougies. Si les deux sont vides, retourne
    le DataFrame inchangé (rétrocompatibilité).

    Parameters
    ----------
    df : polars.DataFrame
        DataFrame OHLCV avec une colonne ``time`` (datetime).
    start_date : str
        Date de début ISO 8601 (ex: "2024-01-01" ou "2024-01-01T00:00:00").
    end_date : str
        Date de fin ISO 8601 (inclusif).

    Returns
    -------
    tuple (df_filtered, used_start, used_end)
        used_start/used_end : str ISO des bornes réellement appliquées
        (pour le payload de réponse).
    """
    if not start_date and not end_date:
        return df, "", ""
    import polars as pl

    used_start = ""
    used_end = ""
    mask = pl.lit(True)
    if start_date:
        try:
            start_dt = _parse_ohlcv_bound(start_date, end_of_day=False)
        except Exception as _e:
            raise HTTPException(400, f"start_date '{start_date}' invalide (ISO 8601 attendu) : {_e}")
        mask = mask & (pl.col("time") >= start_dt)
        used_start = start_dt.isoformat()[:16]
    if end_date:
        try:
            end_dt = _parse_ohlcv_bound(end_date, end_of_day=True)
        except Exception as _e:
            raise HTTPException(400, f"end_date '{end_date}' invalide (ISO 8601 attendu) : {_e}")
        mask = mask & (pl.col("time") <= end_dt)
        used_end = end_dt.isoformat()[:16]

    # Volontairement SANS try/except : un filtre qui échoue silencieusement
    # rendrait le DataFrame complet, et l'utilisateur croirait avoir backtesté
    # sa plage alors qu'il a backtesté tout l'historique. Mieux vaut un 500
    # explicite qu'un résultat faux présenté comme juste.
    return df.filter(mask), used_start, used_end


def _apply_cost_override(cfg: dict, cost_override: dict) -> dict:
    """QW-4 : applique une override de cost_model POUR CE RUN UNIQUEMENT.

    Exigence 4 : permet de comparer frais taker/maker, spot/margin, levier
    différent sans modifier config.yaml. On clone la config et on surcharge
    uniquement les clés fournies.

    Parameters
    ----------
    cfg : dict
        Config chargée.
    cost_override : dict
        Override partiel : {taker_fee, maker_fee, borrow_rate_daily, max_leverage,
        slippage_model, slippage_k, spread_pct}.

    Returns
    -------
    dict
        Nouvelle config avec overrides appliquées (shallow copy — ne mute pas
        la config d'origine).
    """
    if not cost_override:
        return cfg
    import copy
    new_cfg = copy.deepcopy(cfg)
    try:
        # Surcharger trading.taker_fee / maker_fee / max_leverage
        trading = new_cfg.setdefault("trading", {})
        if "taker_fee" in cost_override:
            trading["taker_fee"] = float(cost_override["taker_fee"])
        if "maker_fee" in cost_override:
            trading["maker_fee"] = float(cost_override["maker_fee"])
        if "max_leverage" in cost_override:
            trading["max_leverage"] = float(cost_override["max_leverage"])
        # Surcharger backtest.spread_pct / slippage_model / slippage_k
        bt = new_cfg.setdefault("backtest", {})
        if "spread_pct" in cost_override:
            bt["spread_pct"] = float(cost_override["spread_pct"])
        if "slippage_model" in cost_override:
            bt["slippage_model"] = str(cost_override["slippage_model"])
        if "slippage_k" in cost_override:
            bt["slippage_k"] = float(cost_override["slippage_k"])
        # Surcharger venues.borrow_rate_daily si fourni
        if "borrow_rate_daily" in cost_override:
            venues = new_cfg.setdefault("venues", {})
            # Si la structure est venues.defs.<venue>.borrow_rate_daily, on
            # surcharge toutes les venues margin — sinon on met à la racine.
            if "defs" in venues:
                for v_name, v_def in venues["defs"].items():
                    if v_def.get("market_type") == "margin":
                        v_def["borrow_rate_daily"] = float(cost_override["borrow_rate_daily"])
            else:
                venues["borrow_rate_daily"] = float(cost_override["borrow_rate_daily"])
    except Exception as e:
        logger.warning(f"[backtest] cost_override KO ({e}) — config inchangée")
    return new_cfg


@router.post("/api/backtest", dependencies=[Depends(verify_api_key)])
@state.limiter.limit("10/minute")
def run_backtest(
    request:       Request,
    symbol:        str  = DEFAULT_CONFIG_SYMBOL,
    limit:         int  = 500,
    timeframe:     str  = "",
    walk_forward:  bool = False,
    monte_carlo:   bool = False,
    dual_pass:     bool = False,
    strategies:    str  = "",
    # ── QW-2 : plage temporelle (exigence 5) ────────────────────────────────
    # Si start_date et/ou end_date fournis (ISO 8601), le DataFrame est filtré
    # APRÈS le fetch. ``limit`` est alors ignoré (on prend tout le cache puis
    # on filtre par date). Si aucun des deux n'est fourni, comportement
    # inchangé (limit dernières bougies).
    start_date:    str  = "",
    end_date:      str  = "",
    # ── QW-3 : forcer un refresh incrémental avant le backtest (exigence 6) ─
    # Si True, force prefer_cache=False (fetch réseau pour récupérer les
    # dernières bougies). Utile pour backtester "jusqu'à maintenant" sans
    # avoir à rafraîchir manuellement via le scanner ou le live.
    refresh:       bool = False,
    # ── QW-4 : override ponctuel du cost_model (exigence 4) ─────────────────
    # Permet de comparer frais/levier SANS modifier config.yaml.
    # Format : "taker_fee=0.001,maker_fee=0.0008,max_leverage=3,borrow_rate_daily=0.0005"
    # (CSV key=value — évite un body JSON pour rester compatible avec la
    # signature query-string actuelle).
    cost_override: str  = "",
    # ── QW-6 : mode realistic_risk (exigence 3 — close to live) ──────────────
    # Si True, active les circuit breakers (consecutive losses, slot daily DD,
    # max trades/day, volatility brake, kill-switch global) dans le backtest.
    # Opt-in pour préserver la parité avec les backtests existants.
    realistic_risk: bool = False,
):
    """``dual_pass`` (S12 §5.1) — OPTIONNEL, à la demande de l'UI.

    Exécute la passe d'ÉTUDE (échelle fixe, « cette stratégie vaut-elle quelque
    chose ? ») en plus de la passe LIVE (enveloppe réelle du slot, « ce bot
    est-il promouvable ? »). Elle double le temps de calcul : la spec la
    réserve aux runs *rapportés* et l'interdit par essai d'optimisation.
    Désactivée, le comportement est strictement celui d'avant.

    QW-2/3/4 : si ``start_date``/``end_date`` sont fournis, le filtre par date
    prime sur ``limit``. ``refresh=True`` force un fetch réseau. ``cost_override``
    applique une surcharge ponctuelle du cost_model pour ce run uniquement.
    """
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    # Symétrique du check dans optimizer_start() : une optimisation (potentiel-
    # lement des dizaines de jobs LightGBM concurrents) tourne dans le même
    # process et ne partage aucun portillon CPU/mémoire avec le backtest —
    # les deux en même temps risquent la contention/l'OOM.
    from app.engine.auto_optimizer import get_all_jobs as _get_all_jobs
    _opt_running = [jid for jid, j in _get_all_jobs().items() if j.get("status") == "running"]
    if _opt_running:
        raise HTTPException(
            429,
            f"Une optimisation est en cours ({len(_opt_running)} job(s) actif(s)) — "
            f"patientez avant de lancer un backtest (contention CPU/mémoire)."
        )
    if not state._bt_semaphore.acquire(blocking=False):
        raise HTTPException(429, "Un backtest est déjà en cours.")
    state._bt_cancel_event.clear()
    try:
        from app.core.config import load_config as _reload_cfg
        try:
            state.cfg.update(_reload_cfg("config.yaml"))
        except Exception as e:
            logger.warning(f"[backtest] rechargement config KO : {e}")

        tf    = timeframe.strip() or state.cfg["trading"].get("timeframe", "1h")
        # A-03 : le backfill reste celui du CandleStore (50k, 5k en 1d) —
        # persisté une fois, ça approfondit l'historique. La plage de dates
        # ne change que ce qui est renvoyé au backtest (scan Parquet filtré).
        use_date_range = bool(start_date.strip() or end_date.strip())
        if use_date_range:
            limit = 50000
            if tf == "1d":
                limit = 5000
        else:
            limit = max(100, min(limit, 50000))
            if tf == "1d":
                limit = min(limit, 5000)

        # QW-4 : appliquer l'override de cost_model si fourni
        cost_override_dict = {}
        if cost_override.strip():
            try:
                for kv in cost_override.split(","):
                    if "=" in kv:
                        k, v = kv.split("=", 1)
                        k = k.strip()
                        v = v.strip()
                        # Tenter float, sinon garder string
                        try:
                            cost_override_dict[k] = float(v)
                        except ValueError:
                            cost_override_dict[k] = v
            except Exception as e:
                logger.warning(f"[backtest] parsing cost_override '{cost_override}' KO : {e}")
        effective_cfg = _apply_cost_override(state.cfg, cost_override_dict)

        # QW-3 : refresh force prefer_cache=False
        exchange = _get_bt_exchange(effective_cfg)
        store = get_store()
        if use_date_range:
            try:
                start_dt = (
                    _parse_ohlcv_bound(start_date.strip(), False) if start_date.strip() else None
                )
                end_dt = (
                    _parse_ohlcv_bound(end_date.strip(), True) if end_date.strip() else None
                )
            except Exception as _e:
                raise HTTPException(400, f"plage de dates invalide (ISO 8601 attendu) : {_e}")
            df = store.fetch_range(
                exchange, symbol, tf, start=start_dt, end=end_dt,
                total=limit, prefer_cache=not refresh,
            )
        else:
            df = store.fetch(exchange, symbol, tf, total=limit,
                             prefer_cache=not refresh)
        if df is None or len(df) == 0:
            raise HTTPException(400, f"Aucune donnée disponible pour {symbol}/{tf}")

        # QW-2 : filtrer par plage temporelle si fournie
        used_start, used_end = "", ""
        if use_date_range:
            df, used_start, used_end = _filter_df_by_date_range(df, start_date, end_date)
            if df is None or len(df) == 0:
                raise HTTPException(
                    400,
                    f"Aucune donnée dans la plage [{start_date} → {end_date}] pour {symbol}/{tf}. "
                    f"Vérifiez le cache (GET /api/backtest/range?symbol={symbol}&timeframe={tf})."
                )

        # A-05 : le graphique n'affiche pas 50k points — sous-échantillonner.
        _n = len(df)
        _step = max(1, _n // 4000)
        _times = df["time"].dt.epoch(time_unit="s").to_list()
        _open = df["open"].to_list()
        _close = df["close"].to_list()
        _high = df["high"].to_list()
        _low = df["low"].to_list()
        _vol = df["volume"].to_list()
        _idx = list(range(0, _n, _step))
        if _idx[-1] != _n - 1:
            _idx.append(_n - 1)
        ohlcv_payload = {
            "time":   [int(_times[i]) for i in _idx],
            "open":   [round(float(_open[i]), 6) for i in _idx],
            "close":  [round(float(_close[i]), 6) for i in _idx],
            "high":   [round(float(_high[i]), 6) for i in _idx],
            "low":    [round(float(_low[i]), 6) for i in _idx],
            "volume": [round(float(_vol[i]), 2) for i in _idx],
            "step":   _step,
        }

        _ALLOWED_STRATS = _discover_strategies()
        strats_to_run   = (
            [s.strip() for s in strategies.split(",") if s.strip()]
            if strategies.strip()
            else state.cfg["strategies"]["enabled"]
        )
        invalid = [s for s in strats_to_run if s not in _ALLOWED_STRATS]
        if invalid:
            raise HTTPException(400, f"Stratégie(s) inconnue(s) : {', '.join(invalid)}")
        strats_to_run = [s for s in strats_to_run if s in _ALLOWED_STRATS]

        tf_mins      = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
                        "1h": 60, "4h": 240, "1d": 1440}
        days_covered = round(len(df) * tf_mins.get(tf, 60) / 1440, 1)
        _bars_warning = None
        if days_covered < 30:
            _bars_warning = (
                f"⚠ Seulement {days_covered} jours de données ({len(df)} bougies × {tf}). "
                f"Visez ≥ 90 jours pour des résultats fiables."
            )

        by_strategy = {}

        # A-02 : calcul lourd hors process API (ProcessPool spawn). Le fetch
        # OHLCV ci-dessus reste ici ; record_backtest / recos restent ici
        # (I/O, pas de GIL).
        from app.engine.compute_pool import clear_cancel, map_jobs
        clear_cancel()
        bt_cfg = effective_cfg.get("backtest") or {}
        payloads = []
        for name in strats_to_run:
            env = _slot_envelope(name, tf, symbol) if dual_pass else None
            payloads.append({
                "name": name,
                "cfg": effective_cfg,
                "df": df,
                "symbol": symbol,
                "timeframe": tf,
                "realistic_risk": realistic_risk,
                "dual_pass": bool(dual_pass and env is not None),
                "envelope": env,
                "walk_forward": walk_forward,
                "monte_carlo": monte_carlo,
                "days_covered": days_covered,
                "bars_warning": _bars_warning,
                "wf_folds": bt_cfg.get("walk_forward_folds", 5),
                "mc_runs": bt_cfg.get("monte_carlo_runs", 200),
            })
        computed = map_jobs(payloads, max_workers=min(len(strats_to_run), 4))
        if state._bt_cancel_event.is_set():
            raise HTTPException(499, "Backtest annulé par l'utilisateur")
        for name, entry in computed:
            if "error" not in entry:
                try:
                    from app.core.backtest_history import record_backtest
                    record_backtest(name, tf, symbol, entry, n_bars=len(df))
                except Exception as _rec_e:
                    logger.debug(f"[backtest] record_backtest({name}) KO : {_rec_e}")
                try:
                    from app.engine.recommendations import (
                        generate_recommendations,
                        summarize_recommendations,
                    )
                    recos = generate_recommendations({**entry, "ohlcv": ohlcv_payload})
                    entry["recommendations"] = recos
                    entry["recommendations_summary"] = summarize_recommendations(recos)
                except Exception as _reco_e:
                    logger.debug(f"[backtest] recommendations({name}) KO : {_reco_e}")
                    entry["recommendations"] = []
                    entry["recommendations_summary"] = {
                        "counts": {}, "verdict": "neutral",
                        "verdict_label": "Analyse indisponible", "total": 0,
                    }
            by_strategy[name] = entry

        ohlcv_gaps   = detect_ohlcv_gaps(df, tf)
        gaps_warning = None
        if ohlcv_gaps:
            total_missing = sum(g["gap_bars"] for g in ohlcv_gaps)
            gaps_warning  = (
                f"⚠ {len(ohlcv_gaps)} gap(s) détecté(s) — "
                f"{total_missing} bougies manquantes."
            )

        payload = {
            "symbol":          symbol,
            "timeframe":       tf,
            "limit":           limit,
            "n_bars":          len(df),
            "date_from":       str(df["time"][0])[:16] if len(df) else "",
            "date_to":         str(df["time"][-1])[:16] if len(df) else "",
            # QW-2 : plage temporelle réellement appliquée (vide si non utilisée)
            "requested_start_date": used_start,
            "requested_end_date":   used_end,
            # QW-3 : indique si un refresh réseau a été forcé
            "refreshed":       bool(refresh),
            # QW-4 : override appliquée (vide si aucune)
            "cost_override":   cost_override_dict,
            # QW-6 : mode realistic_risk actif
            "realistic_risk":  bool(realistic_risk),
            "ohlcv":           ohlcv_payload,
            "by_strategy":     by_strategy,
            "score_threshold": effective_cfg["trading"].get("score_threshold", 0.55),
            "ohlcv_gaps":      ohlcv_gaps[:20],
            "gaps_warning":    gaps_warning,
            # S11 : contexte d'exécution facturé (spot/margin, levier, frais,
            # emprunt). Cette route construit son PROPRE payload et ne renvoie
            # pas `BacktestResult.to_dict()` : sans cette ligne, le champ existe
            # côté moteur mais n'atteint jamais l'UI. Résolu ici plutôt que
            # repris d'un run : il est identique pour toutes les stratégies,
            # puisqu'il ne dépend que du couple (symbole, timeframe).
            "cost_model":      _cost_model_for(symbol, tf),
        }
        return JSONResponse(content=_clean(payload))
    except HTTPException:
        raise
    except Exception as e:
        err_id = uuid.uuid4()
        logger.error(f"[API] Erreur {err_id} backtest : {e}", exc_info=True)
        raise HTTPException(500, f"Erreur interne ({err_id})")
    finally:
        state._bt_semaphore.release()
