"""Routes ML — informations sur les modèles BaseStrategyML chargés, et
registre de modèles daté (ML-02 — page « Modèles »)."""
import importlib
import logging
import uuid
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
def _strategies_by_recipe() -> Dict[str, List[str]]:
    """Index inverse recette → stratégies qui la déclarent.

    Construit en important les modules de ``app/strategies`` (déjà tous
    importés par le LiveTrader en fonctionnement normal) et en lisant leur
    liaison ``models:``. N'est appelé que sur le chemin d'erreur, quand le nom
    reçu n'est pas une stratégie."""
    from app.ml.recipe import strategy_models
    index: Dict[str, List[str]] = {}
    for strat_name in sorted(_discover_strategies()):
        try:
            mod = importlib.import_module(f"app.strategies.{strat_name}")
            declared = strategy_models(getattr(mod, "Strategy", None))
        except Exception as e:
            logger.debug(f"[ML] index des recettes : {strat_name} illisible ({e})")
            continue
        for recipe in declared.values():
            index.setdefault(recipe, []).append(strat_name)
    return index


def _resolve_strategy_name(name: str) -> str:
    """Nom de stratégie à entraîner, à partir de ce que l'UI a envoyé.

    Valide contre les stratégies découvertes sur disque avant tout
    ``importlib.import_module`` — même garde que l'optimiseur/scanner (qui
    font déjà ``import_module(f"app.strategies.{name}")`` avec un nom fourni
    par l'API).

    Accepte aussi un nom de **recette** et le résout vers la stratégie qui la
    déclare : la page « Modèles » est indexée par recette (c'est la clé du
    registre), il est donc naturel d'y recopier ``omnibus_v4_multi`` alors que
    l'entraînement, lui, a besoin d'une classe ``Strategy`` à instancier. Une
    recette partagée par plusieurs stratégies reste une erreur — mais une
    erreur qui dit lesquelles choisir.
    """
    known = _discover_strategies()
    if name in known:
        return name

    owners = _strategies_by_recipe().get(name, [])
    if len(owners) == 1:
        logger.info(f"[ML] recette {name!r} → stratégie {owners[0]!r}")
        return owners[0]
    if len(owners) > 1:
        raise HTTPException(400, f"{name!r} est une recette partagée par plusieurs "
                                 f"stratégies : indiquez laquelle entraîner parmi "
                                 f"{owners}")
    raise HTTPException(400, f"Stratégie inconnue : {name!r} — attendu un nom de "
                             f"stratégie parmi {sorted(known)}, ou une recette "
                             f"rattachée à une stratégie")


def _require_trainable_recipe(name: str) -> str:
    """Valide une recette AVANT de lancer le job de fond.

    ``recipe_trainer.supports()`` sait dire pourquoi une recette n'est pas
    entraînable (persistance ``proxy``, catalogue non enregistré…) : autant
    rendre cette réponse à l'appelant tout de suite plutôt que de la lui
    faire découvrir une minute plus tard dans un statut de job."""
    from app.ml.recipe import available_recipes
    from app.ml.recipe_trainer import supports
    if name not in available_recipes():
        raise HTTPException(400, f"Recette inconnue : {name!r} — disponibles : "
                                 f"{available_recipes()}")
    why = supports(name)
    if why:
        raise HTTPException(400, f"Recette {name!r} non entraînable : {why}")
    return name


def _require_known_tf(tf: str) -> None:
    """Rejette un timeframe hors table canonique AVANT de lancer le job.

    Sans ce contrôle, un libellé approximatif (« 15min » pour « 15m ») partait
    en tâche de fond pour échouer une minute plus tard sur un « aucune donnée
    en cache » qui accusait le cache plutôt que la saisie."""
    from app.core.timeframes import TF_SECONDS
    if tf not in TF_SECONDS:
        raise HTTPException(400, f"Timeframe inconnu : {tf!r} — attendu parmi "
                                 f"{sorted(TF_SECONDS, key=lambda t: TF_SECONDS[t])}")


@router.get("/api/ml/recipes", dependencies=[Depends(verify_api_key)])
def ml_recipes():
    """Recettes du dépôt et leur entraînabilité — alimente la liste déroulante
    de la page « Modèles », qui parle désormais le même vocabulaire que sa
    table de registre."""
    from app.ml.recipe import available_recipes, load_recipe
    from app.ml.recipe_trainer import supports

    out: List[Dict[str, Any]] = []
    for name in available_recipes():
        why = supports(name)
        try:
            r = load_recipe(name)
            catalog, scheme, heads = r.features_catalog, r.label_scheme, r.heads
        except Exception:
            catalog = scheme = None
            heads = []
        out.append({"recipe": name, "trainable": why is None, "reason": why,
                    "features_catalog": catalog, "label_scheme": scheme,
                    "heads": heads})
    return {"recipes": out}


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
    # ``strategy`` reste optionnel depuis l'étape C : une RECETTE suffit à
    # entraîner (app.ml.recipe_trainer). C'est ce qui permet à la page
    # « Modèles », indexée par recette, de cesser de demander une stratégie —
    # le formulaire parlait un vocabulaire que le tableau juste au-dessus
    # n'utilisait pas.
    strategy: Optional[str] = None
    recipe: Optional[str] = None
    symbol: str = DEFAULT_CONFIG_SYMBOL
    #: ML-16 — entraînement POOLÉ. Non vide, ``symbol`` est ignoré et la
    #: recette est entraînée sur tous ces symboles mis en commun.
    symbols: Optional[List[str]] = None
    #: Nom d'un univers (``sbf120``) — alternative à ``symbols``, et la seule
    #: praticable depuis l'UI : personne ne saisit 120 mnémoniques à la main.
    #: Les deux peuvent se combiner ; l'union est prise, sans doublon.
    universe: Optional[str] = None
    #: Borne le pool aux N titres les mieux dotés en historique. 0 = tous.
    #: Un pool de 120 titres coûte cher ; pouvoir en prendre 20 rend
    #: l'expérimentation possible sans écrire la liste.
    max_symbols: int = 0
    #: Entraîne aussi un modèle DÉDIÉ pour les N premiers titres et rapporte
    #: l'écart avec le poolé. Opt-in : c'est un entraînement complet par titre.
    compare_solo: int = 0
    tf: str
    as_of: Optional[str] = None
    window_bars: Optional[int] = None
    params: Dict[str, Any] = {}
    publish: bool = False


def _resolve_pool(body: "_TrainBody") -> List[str]:
    """Symboles du pool : union de ``symbols`` et des membres de ``universe``.

    ``max_symbols`` trie par PROFONDEUR D'HISTORIQUE décroissante avant de
    couper. Prendre les N premiers dans l'ordre du fichier d'univers donnerait
    un pool ordonné alphabétiquement, donc arbitraire — alors que la profondeur
    est exactement ce qui décide de l'éligibilité d'un titre. Un titre trop
    court n'est pas écarté ici : ``train_multi_and_publish`` le fait, et il sait
    dire pourquoi.
    """
    out: List[str] = [s.strip() for s in (body.symbols or []) if s and s.strip()]
    if body.universe:
        from app.core.universe import universe_symbols
        members = universe_symbols(body.universe)
        if not members:
            raise HTTPException(400, f"Univers {body.universe!r} inconnu ou vide")
        out.extend(members)
    out = list(dict.fromkeys(out))

    if body.max_symbols and len(out) > body.max_symbols:
        depth: Dict[str, int] = {}
        for s in get_store().all_stats():
            if s.get("tf") == body.tf:
                depth[s["symbol"]] = int(s.get("bars") or 0)
        out.sort(key=lambda sym: -depth.get(sym, 0))
        out = out[:body.max_symbols]
    return out


@router.post("/api/ml/train", dependencies=[Depends(verify_api_key)])
@state.limiter.limit("10/minute")
def ml_train_start(request: Request, body: _TrainBody):
    """Lance un entraînement (dry-run par défaut — ``publish=false`` : rien
    n'est écrit au registre ; ``publish=true`` : gate + publication réelle,
    même chemin que le live/backtest simulated_live). Retourne un ``job_id``
    à interroger via ``GET /api/ml/train/status``.

    ``symbols`` non vide déclenche le mode POOLÉ (ML-16) : une recette
    entraînée sur plusieurs symboles mis en commun, holdout prélevé par
    symbole, gate sur la moyenne des scores."""
    _require_known_tf(body.tf)
    recipe = _require_trainable_recipe(body.recipe) if body.recipe else None
    pooled = _resolve_pool(body)
    # Refusé TÔT plutôt que découvert dans un statut de job : le pooling
    # n'existe que sur le chemin recette, qui seul sait entraîner sans
    # instancier une classe Strategy par symbole.
    if pooled and not recipe:
        raise HTTPException(400, "l'entraînement poolé (symbols/universe) exige "
                                 "une recette — le chemin stratégie entraîne un "
                                 "symbole à la fois")
    strategy = None if recipe else _resolve_strategy_name(body.strategy or "")
    from app.engine.ml_jobs import start_train_job
    job_id = start_train_job(strategy or "", body.symbol, body.tf, as_of=body.as_of,
                             window_bars=body.window_bars, params=body.params,
                             publish=body.publish, recipe=recipe, symbols=pooled,
                             compare_solo=body.compare_solo)
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
    strategy = _resolve_strategy_name(body.strategy)
    _require_known_tf(body.tf)
    if not body.windows:
        raise HTTPException(400, "windows ne peut pas être vide")
    from app.engine.ml_jobs import start_sweep_job
    job_id = start_sweep_job(strategy, body.symbol, body.tf, body.windows,
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


# ── P0-5 : Route d'audit versioning (model_versioning.migration_check) ────────
@router.get("/api/ml/versioning/audit", dependencies=[Depends(verify_api_key)])
@state.limiter.limit("5/minute")
def ml_versioning_audit(request: Request):
    """Audit global du versioning des modèles ML.

    Appelle ``app.ml.model_versioning.migration_check()`` qui scanne le
    répertoire ``models/`` et retourne ``{total, with_hash, without_hash,
    incompatible}``. Permet à l'UI de signaler les modèles sans hash de
    features (à re-entraîner pour activer le versioning) et les modèles
    incompatibles (hash ne correspondant plus aux features actuelles).

    Route d'admin — rate-limit 5/min (scan FS potentiellement lourd).
    """
    try:
        from app.ml.model_versioning import migration_check
        result = migration_check(models_dir="models")
        # `migration_check` renvoie `without_hash`/`incompatible` sous forme de
        # LISTES de chemins. `audit` les expose telles quelles (le détail est
        # utile pour savoir quoi re-entraîner) ; `summary` n'expose que des
        # compteurs — mélanger les deux ferait afficher un chemin de fichier là
        # où l'UI attend un nombre.
        without_hash = result.get("without_hash") or []
        incompatible = result.get("incompatible") or []
        total = int(result.get("total", 0) or 0)
        with_hash = int(result.get("with_hash", 0) or 0)
        return {
            "status": "ok",
            "audit": result,
            # Indicateurs synthétiques pour l'UI (compteurs uniquement)
            "summary": {
                "total": total,
                "with_hash": with_hash,
                "without_hash": len(without_hash),
                "incompatible": len(incompatible),
                "coverage_pct": round(100.0 * with_hash / total, 1) if total else 0.0,
            },
        }
    except Exception as e:
        logger.error(f"[API] ml/versioning/audit KO : {e}", exc_info=True)
        err_id = uuid.uuid4()
        logger.error(f"[API] Erreur {err_id} ml : {e}", exc_info=True)
        raise HTTPException(500, f"Erreur interne ({err_id})")


# ── P1-2 : Route pour lister les jobs ML récents ──────────────────────────────
@router.get("/api/ml/jobs", dependencies=[Depends(verify_api_key)])
@state.limiter.limit("30/minute")
def ml_jobs_list(request: Request, status: str = "", kind: str = "", limit: int = 50):
    """Liste les jobs ML récents (train + sweep).

    Expose ``app.engine.ml_jobs.get_all_jobs()`` qui était jusque-là invisible
    côté UI. Permet à l'utilisateur de retrouver un job après fermeture du
    dialog (jobs orphelins) et de suivre les entraînements en cours.

    Parameters
    ----------
    status : str
        Filtre par statut (running/done/error). Vide = tous.
    kind : str
        Filtre par type (train/sweep). Vide = tous.
    limit : int
        Nombre max de jobs à retourner (défaut 50, max 200).

    Returns
    -------
    dict
        ``{jobs: [...], total, running_count}``
    """
    try:
        from app.engine.ml_jobs import get_all_jobs
        all_jobs = get_all_jobs()
        # Filtrage
        jobs = list(all_jobs.values())
        if status.strip():
            jobs = [j for j in jobs if j.get("status") == status.strip()]
        if kind.strip():
            jobs = [j for j in jobs if j.get("kind") == kind.strip()]
        # Tri par started_at décroissant (plus récent d'abord)
        jobs.sort(key=lambda j: j.get("started_at", 0), reverse=True)
        # Limit
        limit = max(1, min(limit, 200))
        jobs = jobs[:limit]
        running_count = sum(1 for j in all_jobs.values() if j.get("status") == "running")
        return {
            "jobs": jobs,
            "total": len(all_jobs),
            "running_count": running_count,
        }
    except Exception as e:
        logger.error(f"[API] ml/jobs KO : {e}", exc_info=True)
        err_id = uuid.uuid4()
        logger.error(f"[API] Erreur {err_id} ml : {e}", exc_info=True)
        raise HTTPException(500, f"Erreur interne ({err_id})")


# ── P1-2 : Route pour supprimer un job ML ────────────────────────────────────
@router.delete("/api/ml/jobs/{job_id}", dependencies=[Depends(verify_api_key)])
@state.limiter.limit("30/minute")
def ml_jobs_delete(request: Request, job_id: str):
    """Supprime un job ML de la liste (uniquement si terminé/error/cancelled)."""
    try:
        from app.engine.ml_jobs import delete_job, get_job
        job = get_job(job_id)
        if not job:
            raise HTTPException(404, f"Job {job_id!r} introuvable")
        if job.get("status") == "running":
            raise HTTPException(400, "Impossible de supprimer un job en cours — annulez-le d'abord")
        if not delete_job(job_id):
            raise HTTPException(400, "Suppression impossible")
        return {"status": "deleted", "job_id": job_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] ml/jobs/{job_id} DELETE KO : {e}", exc_info=True)
        err_id = uuid.uuid4()
        logger.error(f"[API] Erreur {err_id} ml : {e}", exc_info=True)
        raise HTTPException(500, f"Erreur interne ({err_id})")


# ── P1-3 : Audit trail global des décisions ML (toutes recettes confondues) ──
@router.get("/api/ml/registry/decisions/recent", dependencies=[Depends(verify_api_key)])
@state.limiter.limit("30/minute")
def ml_registry_decisions_recent(request: Request, limit: int = 100):
    """Agrège les décisions récentes de TOUTES les recettes du registre.

    Permet à l'UI d'afficher un audit trail global sans devoir déplier chaque
    recette une par une. Lit les ``decisions.jsonl`` de chaque répertoire de
    recette via ``model_registry.list_recipes()`` + ``read_decisions()``.
    """
    try:
        import app.ml.model_registry as registry
        recipes = registry.list_recipes()
        all_decisions = []
        for recipe_name in recipes:
            try:
                # Lire les décisions pour tous les TFs de cette recette
                versions = registry.list_versions(None, recipe_name)
                tfs_seen = set()
                for v in versions:
                    if v.tf and v.tf not in tfs_seen:
                        tfs_seen.add(v.tf)
                        decs = registry.read_decisions(v.tf, recipe_name, limit=50)
                        for d in (decs or []):
                            d["_recipe"] = recipe_name
                            d["_tf"] = v.tf
                            all_decisions.append(d)
            except Exception:
                continue
        # Trier par timestamp décroissant (plus récent d'abord)
        all_decisions.sort(
            key=lambda d: d.get("ts", d.get("timestamp", "")),
            reverse=True,
        )
        limit = max(1, min(limit, 500))
        return {
            "decisions": all_decisions[:limit],
            "total": len(all_decisions),
        }
    except Exception as e:
        logger.error(f"[API] ml/registry/decisions/recent KO : {e}", exc_info=True)
        err_id = uuid.uuid4()
        logger.error(f"[API] Erreur {err_id} ml : {e}", exc_info=True)
        raise HTTPException(500, f"Erreur interne ({err_id})")


# ── P1-4 : Garde anti-chevauchement pour backtest frozen ─────────────────────
@router.get("/api/ml/registry/overlaps", dependencies=[Depends(verify_api_key)])
@state.limiter.limit("30/minute")
def ml_registry_overlaps(
    request: Request,
    tf: str,
    recipe: str,
    window_start: str = "",
    window_end: str = "",
):
    """Vérifie si la fenêtre d'évaluation chevauche le train_end de l'artefact actif.

    Permet au frontend backtest d'afficher une bannière d'alerte si le backtest
    évalue un modèle frozen entraîné sur des données qui chevauchent la fenêtre
    de test — risquant une fuite temporelle (data leakage).
    """
    try:
        import app.ml.model_registry as registry
        # Pas de chargement de config ici : le registre lit ses artefacts sur
        # disque et n'en a pas besoin. Un `load_config()` par requête serait de
        # l'I/O pure perte sur une route appelée à chaque ouverture de backtest.
        active = registry.latest_promoted(tf, recipe)
        if active is None:
            return {"overlaps": False, "reason": "Aucun artefact actif", "active_version": None}
        # Si pas de fenêtre fournie, on ne peut pas vérifier
        if not window_start or not window_end:
            return {
                "overlaps": False,
                "reason": "Pas de fenêtre d'évaluation fournie",
                "active_version": active.version_id,
                "train_end": active.train_end,
            }
        does_overlap = registry.overlaps(active, window_start, window_end)

        # `overlaps()` ne détecte QUE l'intersection des deux fenêtres. Il rend
        # donc `False` dans le cas le plus extrême de look-ahead : un modèle
        # entraîné ENTIÈREMENT APRÈS la fenêtre évaluée. Cette route interroge
        # `latest_promoted()` — le modèle actif aujourd'hui — et non
        # `resolve(as_of=window_start)` comme le ferait un backtest causal :
        # sans distinction, on répondrait « causalement valide » à propos d'un
        # modèle qui n'existait même pas à la date testée.
        ws = registry.to_iso(window_start)
        posterieur = bool(
            active.train_start and ws and not does_overlap and active.train_start > ws
        )
        if does_overlap:
            verdict, reason = "leak", (
                f"Le backtest [{window_start} → {window_end}] chevauche les données "
                f"d'entraînement du modèle (train_end={active.train_end}) — risque "
                f"de fuite temporelle (data leakage)."
            )
        elif posterieur:
            verdict, reason = "posterior", (
                f"Le modèle actif a été entraîné APRÈS la fenêtre testée "
                f"(train_start={active.train_start} > {window_start}) : il n'existait "
                f"pas à cette date. Un backtest frozen résoudrait un artefact plus "
                f"ancien — ce verdict ne porte donc pas sur le modèle qui serait "
                f"réellement utilisé."
            )
        elif not active.train_start or not active.train_end:
            verdict, reason = "unknown", (
                "Artefact sans provenance datée : le chevauchement n'est pas "
                "vérifiable (absence de mesure, pas absence de risque)."
            )
        else:
            verdict, reason = "ok", (
                "Fenêtre d'entraînement antérieure et disjointe — le backtest est "
                "causalement valide."
            )

        return {
            "overlaps": does_overlap,
            "verdict": verdict,
            "reason": reason,
            "active_version": active.version_id,
            "train_start": active.train_start,
            "train_end": active.train_end,
            "window_start": window_start,
            "window_end": window_end,
        }
    except Exception as e:
        logger.error(f"[API] ml/registry/overlaps KO : {e}", exc_info=True)
        err_id = uuid.uuid4()
        logger.error(f"[API] Erreur {err_id} ml : {e}", exc_info=True)
        raise HTTPException(500, f"Erreur interne ({err_id})")
