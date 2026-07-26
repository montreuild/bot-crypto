"""Routes ML — informations sur les modèles BaseStrategyML chargés, et
registre de modèles daté (ML-02 — page « Modèles »)."""
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.api import state
from app.api.helpers import _discover_strategies, verify_api_key
from app.core.audit_log import audit_log
from app.core.candle_store import get_store
from app.core.param_resolution import DEFAULT_CONFIG_SYMBOL

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/ml/strategy-info", dependencies=[Depends(verify_api_key)])
def ml_strategy_info():
    """Retourne l'état d'entraînement des stratégies BaseStrategyML chargées."""
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")

    strategies_info: dict = {}
    if state.trader:
        from app.engine.engine import BaseStrategyML
        ml_trainer = getattr(state.trader, "_ml_trainer", None)
        loaded     = getattr(state.trader, "_loaded_strategies", {})
        for name, strat in loaded.items():
            if not isinstance(strat, BaseStrategyML):
                continue
            next_retrain = None
            if ml_trainer:
                # Le trainer stocke un timer par paire (name@tf) ; on prend le plus proche.
                ts_list = [ts for key, ts in ml_trainer._retrain_at.items()
                           if key.startswith(f"{name}@")]
                if ts_list:
                    next_retrain = int(min(ts_list))
            strategies_info[name] = {
                "is_trained":      strat.is_trained,
                "best_auc":        round(float(getattr(strat, "_best_auc", 0.0)), 4),
                "next_retrain_at": next_retrain,
            }

    return {"strategies": strategies_info}


@router.get("/api/candles/stats", dependencies=[Depends(verify_api_key)])
def candles_stats():
    """Retourne les statistiques du cache Parquet local (toutes paires/TFs stockés)."""
    return {"store": get_store().all_stats()}


# ─────────────────────────────────────────────────────────────────────────────
#  Registre de modèles (ML-02 — page « Modèles »)
# ─────────────────────────────────────────────────────────────────────────────
def _require_known_strategy(name: str) -> None:
    """Valide ``name`` contre les stratégies découvertes sur disque avant tout
    ``importlib.import_module`` — même garde que l'optimiseur/scanner (qui
    font déjà ``import_module(f"app.strategies.{name}")`` avec un nom fourni
    par l'API), appliquée ici pour cohérence plutôt que pour combler une
    lacune propre à ce module."""
    if name not in _discover_strategies():
        raise HTTPException(400, f"Stratégie inconnue : {name!r}")


@router.get("/api/ml/registry", dependencies=[Depends(verify_api_key)])
def ml_registry_overview():
    """Vue d'ensemble du registre : tous les (TF, recette) connus, avec leur
    version active résolue (pin/gate inclus) et une alerte de fraîcheur —
    construit la table principale de la page « Modèles ».

    ``train_symbol`` accompagne chaque ligne : c'est le symbole sur lequel
    l'artefact actif a été entraîné (provenance). Il ne partitionne rien — le
    modèle sert tous les symboles tradés."""
    import app.ml.model_registry as ml_registry
    from app.ml.policy import freshness_warning

    out: List[Dict[str, Any]] = []
    for r in ml_registry.list_recipes():
        tf, recipe = r["tf"], r["recipe"]
        active = ml_registry.resolve(tf, recipe)
        pinned = ml_registry.get_pin(tf, recipe)
        out.append({
            "tf": tf, "recipe": recipe, "train_symbol": r.get("train_symbol"),
            "n_versions": len(r["versions"]),
            "active": active.to_dict() if active else None,
            "pinned_version_id": pinned,
            "freshness_warning": (freshness_warning(active) if active
                                  else "aucune version active (toutes rejetées ou absentes)"),
        })
    out.sort(key=lambda x: (x["tf"], x["recipe"]))
    return {"models": out}


@router.get("/api/ml/registry/versions", dependencies=[Depends(verify_api_key)])
def ml_registry_versions(tf: str, recipe: str):
    """Historique complet des versions (plus récent en premier) pour un
    (TF, recette) donné."""
    import app.ml.model_registry as ml_registry
    versions = ml_registry.list_versions(tf, recipe)
    return {"versions": [v.to_dict() for v in reversed(versions)]}


@router.get("/api/ml/registry/decisions", dependencies=[Depends(verify_api_key)])
def ml_registry_decisions(tf: str, recipe: str, limit: int = 50):
    """Journal des décisions de gate (plus récent en premier)."""
    import app.ml.model_registry as ml_registry
    decisions = ml_registry.read_decisions(tf, recipe, limit=min(limit, 500))
    return {"decisions": list(reversed(decisions))}


class _RecipeKey(BaseModel):
    tf: str
    recipe: str


class _PinBody(_RecipeKey):
    version_id: str


@router.post("/api/ml/registry/pin", dependencies=[Depends(verify_api_key)])
def ml_registry_pin(request: Request, body: _PinBody):
    """Épingle une version comme version active — restera résolue par
    resolve(as_of=None) jusqu'à un nouveau pin ou un unpin explicite (rollback
    manuel, déploiement progressif)."""
    import app.ml.model_registry as ml_registry
    ok = ml_registry.set_pin(body.tf, body.recipe, body.version_id)
    if not ok:
        raise HTTPException(404, f"Version {body.version_id!r} introuvable pour "
                                 f"{body.tf}/{body.recipe}")
    audit_log("ml.registry.pin", ip=request.client.host if request.client else "",
             details=body.model_dump())
    return {"status": "pinned", **body.model_dump()}


@router.post("/api/ml/registry/unpin", dependencies=[Depends(verify_api_key)])
def ml_registry_unpin(request: Request, body: _RecipeKey):
    """Retire le pin — resolve(as_of=None) retrouve son comportement par
    défaut (dernière version éligible)."""
    import app.ml.model_registry as ml_registry
    ml_registry.clear_pin(body.tf, body.recipe)
    audit_log("ml.registry.unpin", ip=request.client.host if request.client else "",
             details=body.model_dump())
    return {"status": "unpinned", **body.model_dump()}


class _PromoteBody(_PinBody):
    decision: str = "manual"
    reason: str = "promotion manuelle (UI)"


@router.post("/api/ml/registry/promote", dependencies=[Depends(verify_api_key)])
def ml_registry_promote(request: Request, body: _PromoteBody):
    """Change la décision de gate d'une version déjà publiée — promeut un
    candidat rejeté (``decision="manual"``) ou dépromeut un modèle en place
    (``decision="keep"``) après revue humaine. Journalisé dans
    ``decisions.jsonl`` (source="manual") ET dans l'audit log applicatif."""
    import app.ml.model_registry as ml_registry
    if body.decision not in ("manual", "keep"):
        raise HTTPException(400, "decision doit être 'manual' (promotion) ou 'keep' (rejet)")
    ok = ml_registry.set_decision(body.tf, body.recipe, body.version_id,
                                  body.decision, reason=body.reason)
    if not ok:
        raise HTTPException(404, f"Version {body.version_id!r} introuvable pour "
                                 f"{body.tf}/{body.recipe}")
    audit_log("ml.registry.set_decision", ip=request.client.host if request.client else "",
             details=body.model_dump())
    return {"status": "ok", **body.model_dump()}


# ─────────────────────────────────────────────────────────────────────────────
#  Entraînement / window sweep (jobs asynchrones)
# ─────────────────────────────────────────────────────────────────────────────
class _TrainBody(BaseModel):
    strategy: str
    symbol: str = DEFAULT_CONFIG_SYMBOL
    tf: str
    as_of: Optional[str] = None
    window_bars: Optional[int] = None
    params: Dict[str, Any] = {}
    publish: bool = False


@router.post("/api/ml/train", dependencies=[Depends(verify_api_key)])
@state.limiter.limit("10/minute")
def ml_train_start(request: Request, body: _TrainBody):
    """Lance un entraînement (dry-run par défaut — ``publish=false`` : rien
    n'est écrit au registre ; ``publish=true`` : gate + publication réelle,
    même chemin que le live/backtest simulated_live). Retourne un ``job_id``
    à interroger via ``GET /api/ml/train/status``."""
    _require_known_strategy(body.strategy)
    from app.engine.ml_jobs import start_train_job
    job_id = start_train_job(body.strategy, body.symbol, body.tf, as_of=body.as_of,
                             window_bars=body.window_bars, params=body.params,
                             publish=body.publish)
    if body.publish:
        audit_log("ml.train.publish", ip=request.client.host if request.client else "",
                 details=body.model_dump())
    return {"job_id": job_id}


@router.get("/api/ml/train/status", dependencies=[Depends(verify_api_key)])
def ml_train_status(job_id: str):
    from app.engine.ml_jobs import get_job
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, f"Job {job_id!r} introuvable")
    return job


class _SweepBody(BaseModel):
    strategy: str
    symbol: str = DEFAULT_CONFIG_SYMBOL
    tf: str
    windows: List[int]
    as_of: Optional[str] = None
    params: Dict[str, Any] = {}
    publish_best: bool = False


@router.post("/api/ml/sweep", dependencies=[Depends(verify_api_key)])
@state.limiter.limit("10/minute")
def ml_sweep_start(request: Request, body: _SweepBody):
    """Compare plusieurs tailles de fenêtre d'entraînement sur un holdout
    commun. ``publish_best=false`` (défaut) : comparaison seule, rien n'est
    écrit. ``publish_best=true`` : publie uniquement le meilleur candidat,
    lui-même gaté contre le sortant courant."""
    _require_known_strategy(body.strategy)
    if not body.windows:
        raise HTTPException(400, "windows ne peut pas être vide")
    from app.engine.ml_jobs import start_sweep_job
    job_id = start_sweep_job(body.strategy, body.symbol, body.tf, body.windows,
                             as_of=body.as_of, params=body.params,
                             publish_best=body.publish_best)
    if body.publish_best:
        audit_log("ml.sweep.publish_best", ip=request.client.host if request.client else "",
                 details=body.model_dump())
    return {"job_id": job_id}


@router.get("/api/ml/sweep/status", dependencies=[Depends(verify_api_key)])
def ml_sweep_status(job_id: str):
    from app.engine.ml_jobs import get_job
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, f"Job {job_id!r} introuvable")
    return job
