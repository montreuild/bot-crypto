"""Routes scanner — couche mince : query params, données, sérialisation.

La logique métier (payloads chart/setups/SMC/rejeu/signaux) vit dans
app/api/services/scanner_service.py (V4-K / ARCH-07)."""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from app.api import state
from app.api.helpers import _clean, verify_api_key
from app.api.services import scanner_service
from app.core.config import DEFAULT_MAKER_FEE, DEFAULT_TAKER_FEE
from app.core.exchange import create_exchange
from app.core.param_resolution import DEFAULT_CONFIG_SYMBOL
from app.engine.scanner import MarketScanner

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/scanner/fast_analysis", dependencies=[Depends(verify_api_key)])
def fast_analysis(symbol: str, tf: str, taker: float = DEFAULT_TAKER_FEE,
                  maker: float = DEFAULT_MAKER_FEE):
    """Fast Analyse & optimisation : screening des indicateurs sur les données
    EN CACHE de (symbol, tf) — aucun appel exchange. Familles tendance / retour
    à la moyenne, sensibilité taker/maker, split IS/OOS."""
    from app.core.candle_store import get_store
    from app.core.fast_analysis import analyze
    try:
        df = get_store().load_cached(symbol, tf)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    if df is None or df.height < 260:
        return JSONResponse(
            {"error": f"Données insuffisantes pour {symbol}/{tf} "
                      f"({0 if df is None else df.height} barres) — chargez-les "
                      f"d'abord depuis la page Données."}, status_code=400)
    return JSONResponse(analyze(df, taker=taker, maker=maker))


@router.get("/api/scanner", dependencies=[Depends(verify_api_key)])
def run_scanner(timeframe: str = None, limit: int = 200):
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    try:
        exchange = create_exchange(state.cfg)
        scanner  = MarketScanner(exchange, state.cfg)
        tf       = timeframe or state.cfg["trading"].get("timeframe", "1h")
        results  = scanner.screen(tf, limit)
        return {"timeframe": tf, "symbols_scanned": len(results), "results": results}
    except Exception as e:
        err_id = uuid.uuid4()
        logger.error(f"[API] Erreur {err_id} scanner : {e}", exc_info=True)
        raise HTTPException(500, f"Erreur interne ({err_id})")


@router.get("/api/scanner/config", dependencies=[Depends(verify_api_key)])
def scanner_config():
    """Retourne la configuration active du scanner."""
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    from app.engine.optimizer_search import STRATEGY_TIMEFRAMES
    if state.trader:
        active_per_tf = {tf: [s["name"] for s in v]
                         for tf, v in state.trader._active_per_tf.items()}
    else:
        tfs    = state.cfg["trading"].get("timeframes",
                                          [state.cfg["trading"].get("timeframe", "1h")])
        strats = state.cfg["strategies"].get("enabled", [])
        active_per_tf = {tf: strats for tf in tfs} if strats else {}
    return {
        "scanner":             state.cfg.get("scanner", {}),
        "timeframes":          state.cfg["trading"].get("timeframes",
                               [state.cfg["trading"].get("timeframe", "1h")]),
        # Clé JSON conservée pour compat (Jinja2 scanner.html) ; valeur résolue
        # via l'alias générique min_volume_quote_24h (S2-03).
        "min_volume_usdc_24h": state.cfg["trading"].get(
            "min_volume_quote_24h", state.cfg["trading"].get("min_volume_usdc_24h", 5_000_000)
        ),
        "active_per_tf":       active_per_tf,
        "strategy_timeframes": STRATEGY_TIMEFRAMES,
        "min_viable_score":    -0.05,
    }


@router.get("/api/scanner/opportunities", dependencies=[Depends(verify_api_key)])
def scanner_opportunities(timeframe: str = None, limit: int = 200):
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    try:
        exchange = create_exchange(state.cfg)
        scanner  = MarketScanner(exchange, state.cfg)
        tf       = timeframe or state.cfg["trading"].get("timeframe", "1h")
        results  = scanner.opportunity_scan(tf)
        return {"timeframe": tf, "count": len(results), "opportunities": results}
    except Exception as e:
        err_id = uuid.uuid4()
        logger.error(f"[API] Erreur {err_id} scanner/opportunities : {e}", exc_info=True)
        raise HTTPException(500, f"Erreur interne ({err_id})")


@router.get("/api/scanner/smc_signals_recent", dependencies=[Depends(verify_api_key)])
def scanner_smc_signals_recent(refresh: bool = False):
    """Signaux SMC < 5 j (job background → data/smc_signals_recent.json).

    ``refresh=true`` relance un scan synchrone (peut prendre plusieurs minutes).
    """
    from app.engine import smc_signals_scan as _smc_scan
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    try:
        if refresh:
            payload = _smc_scan.scan_once(state.cfg)
        else:
            payload = _smc_scan.load_recent()
            if not payload.get("signals"):
                # Premier appel : lancer un scan léger en arrière-plan
                threading = __import__("threading")
                threading.Thread(
                    target=lambda: _smc_scan.scan_once(state.cfg),
                    name="smc-signals-on-demand",
                    daemon=True,
                ).start()
        return payload
    except Exception as e:
        err_id = uuid.uuid4()
        logger.error(f"[API] Erreur {err_id} smc_signals_recent : {e}", exc_info=True)
        raise HTTPException(500, f"Erreur interne ({err_id})")



@router.get("/api/scanner/chart", dependencies=[Depends(verify_api_key)])
def scanner_chart(symbol: str = DEFAULT_CONFIG_SYMBOL, timeframe: str = "1h", limit: int = 300):
    """Retourne bougies OHLCV + séries indicateurs pour le graphique scanner."""
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    try:
        exchange = create_exchange(state.cfg)
        scanner  = MarketScanner(exchange, state.cfg)
        df       = scanner.fetch_ohlcv(symbol, timeframe, limit)
        if df is None:
            raise HTTPException(404, f"Données non disponibles pour {symbol}/{timeframe}")
        return JSONResponse(content=_clean(
            scanner_service.build_chart_payload(df, symbol, timeframe)))
    except HTTPException:
        raise
    except Exception as e:
        err_id = uuid.uuid4()
        logger.error(f"[API] Erreur {err_id} scanner/chart : {e}", exc_info=True)
        raise HTTPException(500, f"Erreur interne ({err_id})")


@router.get("/api/scanner/setup_series", dependencies=[Depends(verify_api_key)])
def scanner_setup_series(symbol: str = DEFAULT_CONFIG_SYMBOL, timeframe: str = "1h",
                         limit: int = 300, strategy: str = "v11"):
    """Markers des setups (entrée/TP/SL) d'une stratégie par bougie, pour le
    graphique scanner. ``strategy`` ∈ {v11, v12}.

    Le mode ``v8`` a été retiré avec le pack V4 figé : il affichait les markers
    d'un modèle de mai 2026 que le ré-entraînement bat sur les 3 TF
    (docs/CONCEPTION_ARCHITECTURE_ML_UNIFIEE.md §1.5)."""
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    strat_key = (strategy or "v11").lower()
    if strat_key not in ("v11", "v12"):
        raise HTTPException(400, "strategy doit être v11 ou v12")
    try:
        exchange = create_exchange(state.cfg)
        scanner  = MarketScanner(exchange, state.cfg)
        tf       = timeframe or state.cfg["trading"].get("timeframe", "1h")

        fetch_n = max(limit + 260, 460)
        df = scanner.fetch_ohlcv(symbol, tf, fetch_n)
        if df is None or len(df) < 230:
            raise HTTPException(404, f"Données insuffisantes pour {symbol}/{tf}")

        payload = scanner_service._setup_series_v11(df, tf, limit, strat_key, symbol=symbol)

        payload.update({"symbol": symbol, "timeframe": tf, "strategy": strat_key,
                        "n_setups": len(payload.get("markers", []))})
        return JSONResponse(content=_clean(payload))
    except HTTPException:
        raise
    except Exception as e:
        err_id = uuid.uuid4()
        logger.error(f"[API] Erreur {err_id} scanner/setup_series : {e}", exc_info=True)
        raise HTTPException(500, f"Erreur interne ({err_id})")


@router.get("/api/scanner/smc", dependencies=[Depends(verify_api_key)])
def scanner_smc(symbol: str = DEFAULT_CONFIG_SYMBOL, timeframe: str = "1h",
                limit: int = 1000):
    """Analyse SMC complète du symbole : structure (BOS/CHoCH), poches de
    liquidité, sweeps, order blocks, FVG, premium/discount, trendlines et
    canal de régression — plus le signal courant de la stratégie
    ``smart_money``. Indices convertis en timestamps epoch pour le chart."""
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    try:
        exchange = create_exchange(state.cfg)
        scanner  = MarketScanner(exchange, state.cfg)
        tf       = timeframe or state.cfg["trading"].get("timeframe", "1h")
        n_fetch  = max(300, min(int(limit), 3000))
        df       = scanner.fetch_ohlcv(symbol, tf, n_fetch)
        if df is None or len(df) < 60:
            raise HTTPException(404, f"Données insuffisantes pour {symbol}/{tf}")
        return JSONResponse(content=_clean(
            scanner_service.build_smc_payload(state.cfg, df, symbol, tf)))
    except HTTPException:
        raise
    except Exception as e:
        err_id = uuid.uuid4()
        logger.error(f"[API] Erreur {err_id} scanner/smc : {e}", exc_info=True)
        raise HTTPException(500, f"Erreur interne ({err_id})")


@router.get("/api/scanner/smc_replay", dependencies=[Depends(verify_api_key)])
def scanner_smc_replay(symbol: str = DEFAULT_CONFIG_SYMBOL, timeframe: str = "4h",
                       limit: int = 800):
    """Payload de rejeu Smart Money : UNE requête précalcule tout, le
    navigateur reconstruit l'état à n'importe quelle barre.

    Le moteur étant strictement causal, chaque entité porte ses indices de
    cycle de vie (``index``/``created_at``/``confirmed_at`` → apparition,
    ``touched_at``/``swept_at``/``filled_at``/``invalidated_at`` → mutations) :
    l'état à la barre ``i`` se déduit par simple comparaison d'indices, sans
    nouvel appel serveur. Les trades sont ceux du VRAI Backtester avec les
    paramètres par timeframe résolus (optimizer_results)."""
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")

    import time as _time
    cache_key = (symbol, timeframe, int(limit))
    # Cache court : le payload ne change qu'à la clôture d'une nouvelle barre.
    with state._smc_replay_lock:
        hit = state._smc_replay_cache.get(cache_key)
        if hit and (_time.monotonic() - hit[0]) < state._SMC_REPLAY_TTL:
            return JSONResponse(content=hit[1])

    # Borne la concurrence : un backtest complet sur le thread API est lourd —
    # même garde que /api/replay (429 si saturé) pour ne pas affamer le bot.
    if not state._smc_semaphore.acquire(blocking=False):
        raise HTTPException(429, "Trop de rejeux SMC simultanés — réessayez.")
    try:
        exchange = create_exchange(state.cfg)
        scanner  = MarketScanner(exchange, state.cfg)
        tf       = timeframe or state.cfg["trading"].get("timeframe", "4h")
        n_fetch  = max(400, min(int(limit), 2000))
        df       = scanner.fetch_ohlcv(symbol, tf, n_fetch)
        if df is None or len(df) < 320:
            raise HTTPException(404, f"Données insuffisantes pour {symbol}/{tf}")

        cleaned = _clean(
            scanner_service.build_smc_replay_payload(state.cfg, df, symbol, tf))
        with state._smc_replay_lock:
            state._smc_replay_cache[cache_key] = (_time.monotonic(), cleaned)
            # Borne la taille du cache (garde les 16 entrées les plus récentes).
            if len(state._smc_replay_cache) > 16:
                for k in sorted(state._smc_replay_cache,
                                key=lambda k: state._smc_replay_cache[k][0])[:-16]:
                    state._smc_replay_cache.pop(k, None)
        return JSONResponse(content=cleaned)
    except HTTPException:
        raise
    except Exception as e:
        err_id = uuid.uuid4()
        logger.error(f"[API] Erreur {err_id} scanner/smc_replay : {e}", exc_info=True)
        raise HTTPException(500, f"Erreur interne ({err_id})")
    finally:
        state._smc_semaphore.release()


@router.get("/api/scanner/signals", dependencies=[Depends(verify_api_key)])
def scanner_signals(symbol: str = DEFAULT_CONFIG_SYMBOL, timeframe: str = "1h", limit: int = 300):
    """Exécute toutes les stratégies sur le symbole et retourne leurs signaux."""
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    try:
        exchange = create_exchange(state.cfg)
        scanner  = MarketScanner(exchange, state.cfg)
        tf       = timeframe or state.cfg["trading"].get("timeframe", "1h")
        df       = scanner.fetch_ohlcv(symbol, tf, limit)
        if df is None:
            raise HTTPException(404, f"Données non disponibles pour {symbol}/{tf}")
        return JSONResponse(content=_clean(
            scanner_service.build_signals_payload(state.cfg, df, symbol, tf)))
    except HTTPException:
        raise
    except Exception as e:
        err_id = uuid.uuid4()
        logger.error(f"[API] Erreur {err_id} scanner/signals : {e}", exc_info=True)
        raise HTTPException(500, f"Erreur interne ({err_id})")
