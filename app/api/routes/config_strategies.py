"""Routes config STRATÉGIES — split ARCH-013 de config.py (684 lignes → 4 routers).
Endpoints : POST /api/config/{strategies,timeframes,auto-optimizer,strategy-params,
strategy-timeframe}, GET /api/config/strategy-overrides."""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api import state
from app.api.helpers import _discover_strategies, verify_api_key
from app.api.routes._config_helpers import _save_strategy_yaml, _save_yaml
from app.api.schemas import (
    AutoOptimizerBody,
    StrategiesEnabledBody,
    StrategyParamsBody,
    StrategyTimeframeBody,
    TimeframesBody,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/config/strategies", dependencies=[Depends(verify_api_key)])
@state.limiter.limit("30/minute")
def update_strategies(request: Request, body: StrategiesEnabledBody):
    """Active/désactive des stratégies en écrivant `enabled: true/false`
    dans chaque fichier strategies/{name}.yaml."""
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    strat_list = list(body.enabled)
    if not strat_list:
        raise HTTPException(400, "Aucune stratégie spécifiée")
    allowed = _discover_strategies()
    invalid = [s for s in strat_list if s not in allowed]
    if invalid:
        raise HTTPException(400, f"Stratégie(s) inconnue(s) : {', '.join(invalid)}")

    state.cfg["strategies"]["enabled"] = strat_list
    result = {"config_updated": True, "strategies": strat_list, "trader_updated": False}

    if state.trader:
        reload_result = state.trader.reload_strategies(strat_list)
        result["trader_updated"] = True
        result.update(reload_result)

    # Écriture de enabled: true/false dans chaque fichier strategies/
    errors = []
    for name in allowed:
        try:
            _save_strategy_yaml(name, lambda data, _n=name: data.update(
                {"enabled": _n in strat_list}
            ))
        except Exception as e:
            errors.append(f"{name}: {e}")
    result["saved_to_disk"] = len(errors) == 0
    if errors:
        result["save_errors"] = errors
    return result


@router.post("/api/config/timeframes", dependencies=[Depends(verify_api_key)])
@state.limiter.limit("30/minute")
def update_timeframes(request: Request, body: TimeframesBody):
    """Met à jour les timeframes actifs (corps JSON ``{timeframes: [...]}``)."""
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    tf_list = list(body.timeframes)
    if not tf_list:
        raise HTTPException(400, "Aucun timeframe spécifié")

    state.cfg["trading"]["timeframes"] = tf_list
    state.cfg["trading"]["timeframe"]  = tf_list[0]

    result = {"timeframes": tf_list, "trader_updated": False}
    if state.trader:
        state.trader.timeframes = tf_list
        state.trader.tf         = tf_list[0]
        state.trader._build_active_per_tf()
        result["trader_updated"] = True
        result["active_per_tf"]  = {tf: [s["name"] for s in v]
                                    for tf, v in state.trader._active_per_tf.items()}
    try:
        def _upd(d):
            d.setdefault("trading", {})["timeframes"] = tf_list
            d["trading"]["timeframe"] = tf_list[0]
        _save_yaml(_upd)
        result["saved_to_disk"] = True
    except Exception as e:
        result["save_error"] = str(e)
    return result


@router.post("/api/config/auto-optimizer", dependencies=[Depends(verify_api_key)])
@state.limiter.limit("30/minute")
def update_auto_optimizer(request: Request, body: AutoOptimizerBody):
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    enabled = body.enabled
    interval_h = body.interval_h
    state.cfg.setdefault("optimizer", {})["enabled"]        = enabled
    state.cfg["optimizer"]["auto_interval_h"]               = interval_h
    if state.trader:
        state.trader.set_auto_optimizer(enabled, interval_h)
    try:
        def _upd(d):
            d.setdefault("optimizer", {})["enabled"]        = enabled
            d["optimizer"]["auto_interval_h"]               = interval_h
        _save_yaml(_upd)
        saved = True
    except Exception as e:
        logger.warning(f"[config/auto-optimizer] sauvegarde YAML KO : {e}")
        saved = False
    return {"enabled": enabled, "interval_h": interval_h,
            "trader_updated": state.trader is not None, "saved_to_disk": saved}


@router.get("/api/config/strategy-overrides", dependencies=[Depends(verify_api_key)])
def strategy_overrides(strategy: str):
    """Liste les overrides ``optimizer_results[tf][symbole]`` d'une stratégie
    (cf. UI-02). Une entrée héritée est présentée comme la config BTC/USDC."""
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    from app.api.schemas import validate_strategy_name
    try:
        strategy = validate_strategy_name(strategy)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    allowed = _discover_strategies()
    if strategy not in allowed:
        raise HTTPException(400, f"Stratégie inconnue : {strategy}")
    from app.core.param_resolution import DEFAULT_CONFIG_SYMBOL, _is_legacy_tf_entry
    tf_map = (state.cfg.get("optimizer_results") or {}).get(strategy) or {}
    overrides = []
    for tf, entry in tf_map.items():
        if not isinstance(entry, dict):
            continue
        if _is_legacy_tf_entry(entry):
            overrides.append({"timeframe": tf, "symbol": DEFAULT_CONFIG_SYMBOL,
                              "legacy": True, "oos_score": entry.get("oos_score"),
                              "run_date": entry.get("run_date"),
                              "params": entry.get("params", {})})
        else:
            for sym, sub in entry.items():
                if isinstance(sub, dict):
                    overrides.append({"timeframe": tf, "symbol": sym,
                                      "legacy": False, "oos_score": sub.get("oos_score"),
                                      "run_date": sub.get("run_date"),
                                      "params": sub.get("params", {})})
    symbols = list((state.cfg.get("scanner") or {}).get("symbols") or [])
    return {"strategy": strategy, "overrides": overrides, "symbols": symbols,
            "base_params": (state.cfg.get("strategy_params") or {}).get(strategy, {})}


@router.post("/api/config/strategy-params", dependencies=[Depends(verify_api_key)])
@state.limiter.limit("30/minute")
def update_strategy_params(request: Request, body: StrategyParamsBody):
    """Sauvegarde les paramètres d'une stratégie. Sans ``timeframe``/``symbol`` :
    écrit le bloc ``params:`` de base de strategies/{strategy}.yaml. Avec les deux
    (cf. UI-02) : écrit un OVERRIDE ``optimizer_results[tf][symbole]`` via
    ``apply_best_params`` (schéma per-symbole canonique ; ``oos_score`` préservé)."""
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    strategy = body.strategy
    params = body.params
    timeframe = body.timeframe
    symbol = body.symbol
    allowed = _discover_strategies()
    if strategy not in allowed:
        raise HTTPException(400, f"Stratégie inconnue : {strategy}")
    if (timeframe is None) != (symbol is None):
        raise HTTPException(400, "timeframe et symbol doivent être fournis ensemble")

    if timeframe and symbol:
        from app.core.param_resolution import _select_symbol_entry
        from app.engine.opt_persistence import apply_best_params
        tf_entry = ((state.cfg.get("optimizer_results") or {}).get(strategy, {}).get(timeframe) or {})
        prev = _select_symbol_entry(tf_entry, symbol) if isinstance(tf_entry, dict) else None
        prev_score = float((prev or {}).get("oos_score") or 0.0)
        ok = apply_best_params(strategy, params, "config.yaml",
                               timeframe=timeframe, oos_score=prev_score, symbol=symbol)
        if not ok:
            raise HTTPException(500, "Erreur écriture override")
        # Mise à jour à chaud de la config en mémoire (même schéma que le YAML).
        opt = state.cfg.setdefault("optimizer_results", {}).setdefault(strategy, {})
        cur = opt.get(timeframe)
        from app.core.param_resolution import DEFAULT_CONFIG_SYMBOL, _is_legacy_tf_entry
        if isinstance(cur, dict) and _is_legacy_tf_entry(cur):
            cur = {DEFAULT_CONFIG_SYMBOL: cur}
        elif not isinstance(cur, dict):
            cur = {}
        cur[symbol] = {"oos_score": prev_score, "params": dict(params)}
        opt[timeframe] = cur
        if state.trader:
            try:
                state.trader.reload_active_strategies()
            except Exception as e:
                logger.warning(f"[config] reload après override KO : {e}")
        return {"saved": True, "strategy": strategy, "timeframe": timeframe,
                "symbol": symbol, "params": params, "file": f"strategies/{strategy}.yaml",
                "scope": "override"}

    state.cfg.setdefault("strategy_params", {})[strategy] = params
    if state.trader:
        state.trader.strat_params = state.cfg["strategy_params"]
    try:
        def _upd(data):
            data["params"] = params
        _save_strategy_yaml(strategy, _upd)
        return {"saved": True, "strategy": strategy, "params": params,
                "file": f"strategies/{strategy}.yaml", "scope": "base"}
    except Exception as e:
        err_id = uuid.uuid4()
        logger.error(f"[API] Erreur {err_id} config/strategy-params : {e}", exc_info=True)
        raise HTTPException(500, f"Erreur interne ({err_id})")


@router.post("/api/config/strategy-timeframe", dependencies=[Depends(verify_api_key)])
@state.limiter.limit("30/minute")
def toggle_strategy_timeframe(request: Request, body: StrategyTimeframeBody):
    """Active/désactive une stratégie sur un timeframe spécifique.
    Fonctionne via strategy_params[strategy]["disabled_timeframes"]."""
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    strategy = body.strategy
    timeframe = body.timeframe
    enabled = body.enabled
    allowed = _discover_strategies()
    if strategy not in allowed:
        raise HTTPException(400, f"Stratégie inconnue : {strategy}")

    sp = state.cfg.setdefault("strategy_params", {}).setdefault(strategy, {})
    disabled: list = list(sp.get("disabled_timeframes", []))

    if enabled:
        disabled = [tf for tf in disabled if tf != timeframe]
    else:
        if timeframe not in disabled:
            disabled.append(timeframe)

    sp["disabled_timeframes"] = disabled

    # Propagation à chaud sur la stratégie chargée
    trader_updated = False
    if state.trader:
        strat_obj = getattr(state.trader, "_loaded_strategies", {}).get(strategy)
        if strat_obj and hasattr(strat_obj, "disabled_timeframes"):
            strat_obj.disabled_timeframes = list(disabled)
            trader_updated = True

    try:
        def _upd(data):
            data.setdefault("params", {})["disabled_timeframes"] = disabled
        _save_strategy_yaml(strategy, _upd)
        saved = True
    except Exception as e:
        saved = False
        logger.warning(f"[config/strategy-timeframe] save KO: {e}")

    return {"strategy": strategy, "timeframe": timeframe, "enabled": enabled,
            "disabled_timeframes": disabled, "trader_updated": trader_updated,
            "saved_to_disk": saved}
