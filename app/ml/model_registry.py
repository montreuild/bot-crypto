"""Registre de modèles ML — daté, versionné, rangé par (symbole, TF, recette).

Concrétise **ML-02** (`docs/CONCEPTION_CYCLE_DE_VIE_ML.md` §3.2) : remplace le
slot unique `models/{stratégie}_{tf}.*`, écrasé sans comparaison par trois
écrivains différents (trainer live, train final post-optimisation, backtest
inline), par un layout daté où chaque entraînement devient un artefact
immuable, retrouvable par date et comparable à son prédécesseur.

Layout sur disque ::

    {base_dir}/{symbole}/{tf}/{recette}/{version_id}/
        model.amp.lgb
        model.dir.lgb
        model.meta.json      # features/medians/AUC/calibrators + provenance + gate
    {base_dir}/{symbole}/{tf}/{recette}/decisions.jsonl   # journal des gates

``version_id = {train_end}_{recipe_hash8}`` (ou ``legacy_{recipe_hash8}`` si
la date de fin d'entraînement est inconnue — modèles migrés depuis l'ancien
format). Le nom de fichier étant lexicographiquement trié sur la date, aucun
index séparé n'est nécessaire : la vérité est le système de fichiers.

La **recette** identifie ce qui a produit le modèle (features/labels/HP —
cf. le bloc ``model:`` du YAML de stratégie), PAS la stratégie consommatrice :
plusieurs stratégies qui partagent la même recette partagent le même artefact
(§5.5 de la conception) — ``recipe`` est un paramètre libre, généralement égal
au nom de la stratégie tant qu'aucun partage explicite n'est configuré.

Aucun format pickle : ce module ne fait que déplacer/lire les 3 fichiers déjà
écrits par ``app.ml.backend.persistence`` (``save_model``/``save_amp_dir_bundle``,
format LightGBM natif + JSON) — il n'entraîne rien et ne charge aucun booster
lui-même.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_BASE_DIR = "models"

# Décisions de gate qui laissent un artefact éligible à `resolve()` (candidat
# jamais gaté, ou gate favorable). "keep" = candidat entraîné puis rejeté par
# le gate : reste sur disque pour l'audit (decisions.jsonl) mais n'est jamais
# résolu — le sortant continue de servir.
_ELIGIBLE_DECISIONS = (None, "promote", "initial", "manual")


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────
def symbol_to_dir(symbol: Optional[str]) -> str:
    """``BTC/USDC`` → ``BTC_USDC`` (même convention que ``data/ohlcv/``)."""
    if not symbol:
        return "_unknown"
    return str(symbol).replace("/", "_").replace(":", "_")


def to_iso(ts: Any) -> Optional[str]:
    """Normalise un timestamp (str/datetime/polars scalar) en ISO tronqué à
    la seconde, seul format comparé lexicographiquement par ce module."""
    if ts is None:
        return None
    if isinstance(ts, str):
        return ts
    if isinstance(ts, _dt.datetime):
        return ts.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        return str(ts).replace(" ", "T")[:19]
    except Exception:
        return None


def train_window_bounds(df) -> Dict[str, Any]:
    """Extrait ``{train_start, train_end, n_bars}`` d'un DataFrame OHLCV
    (polars) — utilisé par tous les appelants (live trainer, policy,
    backtest simulated_live) pour ne pas dupliquer cette lecture."""
    n = len(df) if df is not None else 0
    if n == 0:
        return {"train_start": None, "train_end": None, "n_bars": 0}
    try:
        t0 = to_iso(df["time"][0]) if "time" in df.columns else None
        t1 = to_iso(df["time"][-1]) if "time" in df.columns else None
    except Exception:
        t0 = t1 = None
    return {"train_start": t0, "train_end": t1, "n_bars": n}


def recipe_hash(recipe_cfg: Optional[Dict[str, Any]]) -> str:
    """Hash canonique d'une recette (dict JSON-sérialisable) — identifie la
    recette dans le registre indépendamment de la stratégie appelante."""
    canon = json.dumps(recipe_cfg or {}, sort_keys=True, default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


_GIT_COMMIT_CACHE: Optional[str] = ""  # "" = pas encore résolu, None = résolu absent


def git_commit() -> Optional[str]:
    """Commit git court du HEAD courant (mis en cache par process). ``None``
    si hors dépôt git (ex. déploiement par archive)."""
    global _GIT_COMMIT_CACHE
    if _GIT_COMMIT_CACHE != "":
        return _GIT_COMMIT_CACHE
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        commit = out.stdout.strip() if out.returncode == 0 else None
    except Exception:
        commit = None
    _GIT_COMMIT_CACHE = commit
    return commit


def _version_id(train_end: Optional[str], rhash: str) -> str:
    ts = train_end.replace(":", "-") if train_end else "legacy"
    return f"{ts}_{rhash[:8]}"


def _recipe_dir(base_dir: str, symbol: Optional[str], tf: str, recipe: str) -> str:
    return os.path.join(base_dir, symbol_to_dir(symbol), tf, recipe)


# ─────────────────────────────────────────────────────────────────────────────
#  Référence d'artefact
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class ArtifactRef:
    """Référence à un artefact du registre — assez d'info pour charger
    (``path_prefix``) et pour décider (dates, AUC, décision de gate) sans
    relire le disque."""
    path_prefix: str
    symbol: Optional[str]
    tf: str
    recipe: str
    version_id: str
    train_start: Optional[str] = None
    train_end: Optional[str] = None
    n_bars: Optional[int] = None
    auc: float = 0.0
    recipe_hash: Optional[str] = None
    git_commit: Optional[str] = None
    source: Optional[str] = None
    created_at: Optional[str] = None
    gate_decision: Optional[str] = None
    legacy: bool = False
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path_prefix": self.path_prefix, "symbol": self.symbol, "tf": self.tf,
            "recipe": self.recipe, "version_id": self.version_id,
            "train_start": self.train_start, "train_end": self.train_end,
            "n_bars": self.n_bars, "auc": round(float(self.auc), 4),
            "recipe_hash": self.recipe_hash, "git_commit": self.git_commit,
            "source": self.source, "created_at": self.created_at,
            "gate_decision": self.gate_decision, "legacy": self.legacy,
            # train_meta : diagnostics d'entraînement (AUC par régime, top
            # features + importance, erreur de calibration...) — toujours
            # petit/borné (cf. app.ml.backend.trainer, jamais de features/
            # médianes/calibrators bruts dedans), donc exposé tel quel plutôt
            # que par une liste de clés à maintenir à la main. Page Modèles
            # (E7) : historique de versions + panneau "top features".
            "train_meta": (self.meta or {}).get("train_meta") or {},
        }


def _read_meta(version_dir: str, meta_filename: str = "model.meta.json") -> Optional[dict]:
    meta_path = os.path.join(version_dir, meta_filename)
    if not os.path.exists(meta_path):
        return None
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"[ModelRegistry] meta illisible {meta_path} : {e}")
        return None


def _artifact_from_version_dir(version_dir: str, symbol: Optional[str], tf: str,
                               recipe: str, version_id: str) -> Optional[ArtifactRef]:
    meta = _read_meta(version_dir)
    if meta is None:
        return None
    prov = meta.get("provenance") or {}
    gate = meta.get("gate") or {}
    return ArtifactRef(
        path_prefix=os.path.join(version_dir, "model"),
        symbol=prov.get("symbol", symbol), tf=tf, recipe=recipe, version_id=version_id,
        train_start=prov.get("train_start"), train_end=prov.get("train_end"),
        n_bars=prov.get("n_bars"), auc=float(meta.get("best_auc", 0.0)),
        recipe_hash=prov.get("recipe_hash"), git_commit=prov.get("git_commit"),
        source=prov.get("source"), created_at=prov.get("created_at"),
        gate_decision=gate.get("decision"), legacy=False, meta=meta,
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Lecture
# ─────────────────────────────────────────────────────────────────────────────
def list_versions(symbol: Optional[str], tf: str, recipe: str,
                  base_dir: str = DEFAULT_BASE_DIR) -> List[ArtifactRef]:
    """Toutes les versions connues pour (symbole, TF, recette), triées du
    plus ancien au plus récent (``train_end`` puis ``version_id``)."""
    rdir = _recipe_dir(base_dir, symbol, tf, recipe)
    out: List[ArtifactRef] = []
    if os.path.isdir(rdir):
        for vid in sorted(os.listdir(rdir)):
            vdir = os.path.join(rdir, vid)
            if not os.path.isdir(vdir):
                continue
            art = _artifact_from_version_dir(vdir, symbol, tf, recipe, vid)
            if art is not None:
                out.append(art)
    out.sort(key=lambda a: (a.train_end or "", a.version_id))
    return out


def _legacy_artifact(tf: str, recipe: str, base_dir: str) -> Optional[ArtifactRef]:
    """Repli sur l'ancien layout plat ``models/{recipe}_{tf}.*`` (aucune
    dimension symbole) — utilisé tant qu'un artefact n'a pas été migré/republié
    dans le nouveau layout. ``recipe`` ici == nom de fichier historique, en
    général identique au nom de la stratégie."""
    prefix = os.path.join(base_dir, f"{recipe}_{tf}")
    meta_path = f"{prefix}.meta.json"
    if not (os.path.exists(f"{prefix}.amp.lgb") and os.path.exists(meta_path)):
        return None
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except Exception as e:
        logger.warning(f"[ModelRegistry] legacy meta illisible {meta_path} : {e}")
        meta = {}
    prov = meta.get("provenance") or {}
    gate = meta.get("gate") or {}
    return ArtifactRef(
        path_prefix=prefix, symbol=prov.get("symbol"), tf=tf, recipe=recipe,
        version_id="legacy-flat", train_start=prov.get("train_start"),
        train_end=prov.get("train_end"), n_bars=prov.get("n_bars"),
        auc=float(meta.get("best_auc", 0.0)), recipe_hash=prov.get("recipe_hash"),
        git_commit=prov.get("git_commit"), source=prov.get("source", "legacy"),
        created_at=prov.get("created_at"), gate_decision=gate.get("decision"),
        legacy=True, meta=meta,
    )


def resolve(symbol: Optional[str], tf: str, recipe: str, *,
           as_of: Any = None, pin: Optional[str] = None,
           base_dir: str = DEFAULT_BASE_DIR) -> Optional[ArtifactRef]:
    """Résout l'artefact à charger pour (symbole, TF, recette).

    - ``pin`` : version_id exact pour CETTE résolution (override ponctuel) —
      ignore le filtre de date et la décision de gate. Différent du pin
      PERSISTANT posé par ``set_pin`` (voir ci-dessous) : celui-ci est un
      paramètre d'appel, pas un état écrit sur disque.
    - ``as_of`` : ne retient que les versions dont ``train_end`` est
      antérieur ou égal à cette date (str/datetime) — c'est ce qui empêche un
      backtest de charger un modèle qui a vu des données de la fenêtre
      évaluée (fuite temporelle, cf. conception §3.2/§4.1). ``None`` = la
      version active « maintenant » : le pin persistant (``set_pin``)
      s'applique alors s'il existe, sinon la dernière version éligible.
      Un ``as_of`` explicite (résolution historique/backtest) ignore
      TOUJOURS le pin persistant — un pin reflète « ce qui sert
      actuellement en production », pas une vérité du passé.
    - Seules les versions à décision de gate favorable sont éligibles
      (``_ELIGIBLE_DECISIONS``) — un candidat rejeté reste sur disque pour
      l'audit mais n'est jamais résolu (sauf ``pin``/``set_pin`` explicites).
    - Repli sur l'ancien layout plat si aucune version n'existe dans le
      nouveau layout (migration progressive, cf. tâche V4).

    Retourne ``None`` si rien n'est trouvable nulle part.
    """
    if pin:
        vdir = os.path.join(_recipe_dir(base_dir, symbol, tf, recipe), pin)
        art = _artifact_from_version_dir(vdir, symbol, tf, recipe, pin) if os.path.isdir(vdir) else None
        if art is None:
            logger.warning(f"[ModelRegistry] pin={pin!r} introuvable pour {symbol}/{tf}/{recipe}")
        return art

    if as_of is None:
        sticky = get_pin(symbol, tf, recipe, base_dir=base_dir)
        if sticky:
            vdir = os.path.join(_recipe_dir(base_dir, symbol, tf, recipe), sticky)
            art = _artifact_from_version_dir(vdir, symbol, tf, recipe, sticky) if os.path.isdir(vdir) else None
            if art is not None:
                return art
            logger.warning(
                f"[ModelRegistry] pin persistant {sticky!r} introuvable pour "
                f"{symbol}/{tf}/{recipe} — repli sur la dernière version éligible"
            )

    versions = list_versions(symbol, tf, recipe, base_dir=base_dir)
    eligible = [v for v in versions if v.gate_decision in _ELIGIBLE_DECISIONS]
    if as_of is not None:
        as_of_s = to_iso(as_of)
        eligible = [v for v in eligible if not v.train_end or v.train_end <= as_of_s]
    if eligible:
        return eligible[-1]

    return _legacy_artifact(tf, recipe, base_dir)


def latest_promoted(symbol: Optional[str], tf: str, recipe: str,
                    base_dir: str = DEFAULT_BASE_DIR) -> Optional[ArtifactRef]:
    """Alias explicite de ``resolve(as_of=None)`` — la version courante en
    production pour ce (symbole, TF, recette)."""
    return resolve(symbol, tf, recipe, as_of=None, base_dir=base_dir)


def read_decisions(symbol: Optional[str], tf: str, recipe: str, *,
                   limit: int = 50, base_dir: str = DEFAULT_BASE_DIR) -> List[dict]:
    """Dernières décisions de gate journalisées (le plus récent en dernier)."""
    path = os.path.join(_recipe_dir(base_dir, symbol, tf, recipe), "decisions.jsonl")
    if not os.path.exists(path):
        return []
    out: List[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        for line in lines[-limit:]:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        logger.warning(f"[ModelRegistry] lecture decisions.jsonl KO : {e}")
    return out


# ─────────────────────────────────────────────────────────────────────────────
#  Pin persistant — « ce qui sert actuellement en production », lu par
#  resolve(as_of=None) uniquement (jamais une résolution historique). Fichier
#  {recipe_dir}/pinned.json — un petit fichier de plus, pas un second index :
#  resolve() reste capable de fonctionner sans lui (repli sur la dernière
#  version éligible si absent ou introuvable).
# ─────────────────────────────────────────────────────────────────────────────
def _pin_path(base_dir: str, symbol: Optional[str], tf: str, recipe: str) -> str:
    return os.path.join(_recipe_dir(base_dir, symbol, tf, recipe), "pinned.json")


def get_pin(symbol: Optional[str], tf: str, recipe: str,
           base_dir: str = DEFAULT_BASE_DIR) -> Optional[str]:
    """``version_id`` actuellement épinglé pour (symbole, TF, recette), ou
    ``None`` si aucun pin n'est posé."""
    path = _pin_path(base_dir, symbol, tf, recipe)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get("version_id")
    except Exception as e:
        logger.warning(f"[ModelRegistry] lecture pinned.json KO : {e}")
        return None


def set_pin(symbol: Optional[str], tf: str, recipe: str, version_id: str,
           base_dir: str = DEFAULT_BASE_DIR) -> bool:
    """Épingle ``version_id`` comme version active pour (symbole, TF, recette)
    — ``resolve(as_of=None)`` la retournera prioritairement, jusqu'à
    ``clear_pin`` ou un nouveau ``set_pin``. Usage : rollback manuel, ou
    déploiement progressif (garder une version pendant qu'une autre est
    validée). Retourne ``False`` si ``version_id`` n'existe pas."""
    vdir = os.path.join(_recipe_dir(base_dir, symbol, tf, recipe), version_id)
    if not os.path.isdir(vdir) or _read_meta(vdir) is None:
        logger.warning(
            f"[ModelRegistry] set_pin : version {version_id!r} introuvable "
            f"pour {symbol}/{tf}/{recipe}"
        )
        return False
    path = _pin_path(base_dir, symbol, tf, recipe)
    payload = {"version_id": version_id,
              "pinned_at": _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")}
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception as e:
        logger.error(f"[ModelRegistry] écriture pinned.json KO : {e}")
        return False
    logger.info(f"[ModelRegistry] pin {symbol}/{tf}/{recipe} → {version_id}")
    return True


def clear_pin(symbol: Optional[str], tf: str, recipe: str,
             base_dir: str = DEFAULT_BASE_DIR) -> None:
    """Retire le pin persistant — ``resolve(as_of=None)`` retrouve son
    comportement par défaut (dernière version éligible). No-op si absent."""
    path = _pin_path(base_dir, symbol, tf, recipe)
    try:
        os.remove(path)
        logger.info(f"[ModelRegistry] pin retiré pour {symbol}/{tf}/{recipe}")
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning(f"[ModelRegistry] suppression pinned.json KO : {e}")


# ─────────────────────────────────────────────────────────────────────────────
#  Écriture
# ─────────────────────────────────────────────────────────────────────────────
def _append_decision(base_dir: str, symbol: Optional[str], tf: str, recipe: str,
                     record: dict) -> None:
    rdir = _recipe_dir(base_dir, symbol, tf, recipe)
    os.makedirs(rdir, exist_ok=True)
    path = os.path.join(rdir, "decisions.jsonl")
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception as e:
        logger.warning(f"[ModelRegistry] écriture decisions.jsonl KO : {e}")


def publish(symbol: Optional[str], tf: str, recipe: str, tmp_path_prefix: str, *,
           train_start: Any = None, train_end: Any = None, n_bars: Optional[int] = None,
           recipe_cfg: Optional[Dict[str, Any]] = None, source: str = "unknown",
           decision: str = "initial", decision_metrics: Optional[Dict[str, Any]] = None,
           base_dir: str = DEFAULT_BASE_DIR, keep_source: bool = False) -> Optional[ArtifactRef]:
    """Publie un artefact fraîchement écrit (``tmp_path_prefix.{amp,dir}.lgb`` +
    ``.meta.json``, produits par ``strategy.save_model(tmp_path_prefix)``)
    dans le registre daté.

    Déplace les 3 fichiers vers ``{base_dir}/{symbole}/{tf}/{recipe}/{version_id}/``,
    enrichit ``model.meta.json`` avec la provenance (dates, git commit, hash
    de recette) et la décision de gate, journalise dans ``decisions.jsonl``.

    Publie **toujours**, y compris ``decision="keep"`` (candidat rejeté) —
    l'audit trail garde le candidat, ``resolve()`` ne le retient simplement
    pas (cf. ``_ELIGIBLE_DECISIONS``). Retourne ``None`` si les fichiers
    source sont absents (entraînement KO en amont — rien à publier).
    """
    amp_src  = f"{tmp_path_prefix}.amp.lgb"
    dir_src  = f"{tmp_path_prefix}.dir.lgb"
    meta_src = f"{tmp_path_prefix}.meta.json"
    if not (os.path.exists(amp_src) and os.path.exists(dir_src) and os.path.exists(meta_src)):
        logger.warning(f"[ModelRegistry] publish : artefacts absents pour {tmp_path_prefix}")
        return None

    train_start_s = to_iso(train_start)
    train_end_s   = to_iso(train_end)
    rhash = recipe_hash(recipe_cfg)
    created_at = _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    version_id = _version_id(train_end_s, rhash)
    version_dir = os.path.join(_recipe_dir(base_dir, symbol, tf, recipe), version_id)
    os.makedirs(version_dir, exist_ok=True)

    try:
        with open(meta_src, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except Exception as e:
        logger.error(f"[ModelRegistry] publish : meta.json illisible ({e})")
        return None

    meta["provenance"] = {
        "symbol": symbol, "train_start": train_start_s, "train_end": train_end_s,
        "n_bars": n_bars, "recipe_name": recipe, "recipe_hash": rhash,
        "git_commit": git_commit(), "source": source, "created_at": created_at,
    }
    meta["gate"] = {"decision": decision, **(decision_metrics or {})}

    mover = shutil.copy2 if keep_source else shutil.move
    try:
        mover(amp_src, os.path.join(version_dir, "model.amp.lgb"))
        mover(dir_src, os.path.join(version_dir, "model.dir.lgb"))
    except Exception as e:
        logger.error(f"[ModelRegistry] publish : déplacement artefacts KO ({e})")
        return None
    with open(os.path.join(version_dir, "model.meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    if not keep_source:
        try:
            os.remove(meta_src)
        except OSError:
            pass

    _append_decision(base_dir, symbol, tf, recipe, {
        "ts": created_at, "version_id": version_id, "decision": decision,
        "train_start": train_start_s, "train_end": train_end_s, "n_bars": n_bars,
        "source": source, **(decision_metrics or {}),
    })

    logger.info(
        f"[ModelRegistry] publish {symbol}/{tf}/{recipe} → {version_id} "
        f"(decision={decision}, AUC={meta.get('best_auc', 0):.3f}, source={source})"
    )
    return _artifact_from_version_dir(version_dir, symbol, tf, recipe, version_id)


def set_decision(symbol: Optional[str], tf: str, recipe: str, version_id: str,
                 decision: str, *, reason: str = "manual override",
                 base_dir: str = DEFAULT_BASE_DIR) -> bool:
    """Modifie la décision de gate d'une version déjà publiée — promotion ou
    rejet manuel d'un artefact existant (esprit ``manual_active``, cf. ML-01 :
    même philosophie de bypass humain journalisé, mais ici sur un ARTEFACT
    MODÈLE, pas sur la stratégie). Journalise dans ``decisions.jsonl``
    (``source="manual"``). Retourne ``False`` si la version est introuvable.

    Usage typique : promouvoir un candidat publié en ``"keep"`` (rejeté par
    le gate automatique) après revue humaine, ou au contraire dépromouvoir un
    modèle en production suite à une dégradation constatée hors bande.
    """
    vdir = os.path.join(_recipe_dir(base_dir, symbol, tf, recipe), version_id)
    meta = _read_meta(vdir)
    if meta is None:
        logger.warning(
            f"[ModelRegistry] set_decision : version {version_id!r} introuvable "
            f"pour {symbol}/{tf}/{recipe}"
        )
        return False
    gate = dict(meta.get("gate") or {})
    previous = gate.get("decision")
    gate["decision"] = decision
    gate["manual_override_reason"] = reason
    meta["gate"] = gate
    try:
        with open(os.path.join(vdir, "model.meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
    except Exception as e:
        logger.error(f"[ModelRegistry] set_decision : écriture meta.json KO ({e})")
        return False

    _append_decision(base_dir, symbol, tf, recipe, {
        "ts": _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "version_id": version_id, "decision": decision, "source": "manual",
        "reason": reason, "previous_decision": previous,
    })
    logger.info(
        f"[ModelRegistry] set_decision {symbol}/{tf}/{recipe}/{version_id} : "
        f"{previous} → {decision} ({reason})"
    )
    return True


def import_legacy(symbol: Optional[str], tf: str, recipe: str,
                  legacy_prefix: str, *, base_dir: str = DEFAULT_BASE_DIR,
                  version_id: str = "legacy") -> Optional[ArtifactRef]:
    """Copie (jamais déplace — l'original reste lisible en repli) un artefact
    de l'ancien layout plat vers le registre daté, avec ``non_reproducible:
    true`` en meta. Utilisé par la migration V4 (script dédié) — cf. tâche
    « Migration V4 ». Idempotent : no-op si la version existe déjà."""
    amp_src, dir_src, meta_src = (f"{legacy_prefix}.amp.lgb", f"{legacy_prefix}.dir.lgb",
                                  f"{legacy_prefix}.meta.json")
    if not (os.path.exists(amp_src) and os.path.exists(dir_src) and os.path.exists(meta_src)):
        logger.warning(f"[ModelRegistry] import_legacy : artefacts absents pour {legacy_prefix}")
        return None

    version_dir = os.path.join(_recipe_dir(base_dir, symbol, tf, recipe), version_id)
    if os.path.isdir(version_dir) and _read_meta(version_dir) is not None:
        logger.info(f"[ModelRegistry] import_legacy : {version_id} déjà présent, skip")
        return _artifact_from_version_dir(version_dir, symbol, tf, recipe, version_id)

    os.makedirs(version_dir, exist_ok=True)
    try:
        with open(meta_src, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except Exception as e:
        logger.error(f"[ModelRegistry] import_legacy : meta.json illisible ({e})")
        return None

    prov = dict(meta.get("provenance") or {})
    prov.setdefault("symbol", symbol)
    prov["non_reproducible"] = True
    prov.setdefault("source", "legacy_import")
    prov.setdefault("created_at", _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"))
    meta["provenance"] = prov
    meta.setdefault("gate", {"decision": "manual", "note": "importé depuis l'ancien layout, jamais gaté"})

    shutil.copy2(amp_src, os.path.join(version_dir, "model.amp.lgb"))
    shutil.copy2(dir_src, os.path.join(version_dir, "model.dir.lgb"))
    with open(os.path.join(version_dir, "model.meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)

    logger.info(f"[ModelRegistry] import_legacy {symbol}/{tf}/{recipe} → {version_id}")
    return _artifact_from_version_dir(version_dir, symbol, tf, recipe, version_id)


# ─────────────────────────────────────────────────────────────────────────────
#  Énumération — vue d'ensemble du registre (page « Modèles » de l'UI)
# ─────────────────────────────────────────────────────────────────────────────
def _dir_to_symbol_guess(symbol_dir: str) -> str:
    """Repli best-effort ``BTC_USDC`` → ``BTC/USDC`` (un seul underscore
    remplacé) — utilisé seulement si aucune version n'a de ``provenance.symbol``
    exploitable ; sinon la valeur authentique de la provenance gagne."""
    return symbol_dir.replace("_", "/", 1)


def list_recipes(base_dir: str = DEFAULT_BASE_DIR) -> List[Dict[str, Any]]:
    """Énumère tous les (symbole, TF, recette) connus du registre — nouveau
    layout daté ET ancien layout plat non migré — sans que l'appelant ait
    besoin de les connaître à l'avance. Utilisé par la page « Modèles » de
    l'UI pour construire sa vue d'ensemble.

    Retourne une liste de ``{"symbol", "tf", "recipe", "versions": [ArtifactRef]}``
    — toutes les versions trouvées, triées du plus ancien au plus récent (y
    compris rejetées : c'est à l'appelant de calculer la version ACTIVE via
    ``resolve()``, qui applique correctement pin/gate/as_of).
    """
    out: List[Dict[str, Any]] = []
    if not os.path.isdir(base_dir):
        return out

    for symbol_dir in sorted(os.listdir(base_dir)):
        sym_path = os.path.join(base_dir, symbol_dir)
        if not os.path.isdir(sym_path):
            continue
        symbol_guess = _dir_to_symbol_guess(symbol_dir)
        for tf in sorted(os.listdir(sym_path)):
            tf_path = os.path.join(sym_path, tf)
            if not os.path.isdir(tf_path):
                continue
            for recipe in sorted(os.listdir(tf_path)):
                recipe_path = os.path.join(tf_path, recipe)
                if not os.path.isdir(recipe_path):
                    continue
                versions = []
                for vid in sorted(os.listdir(recipe_path)):
                    vdir = os.path.join(recipe_path, vid)
                    if not os.path.isdir(vdir):
                        continue
                    art = _artifact_from_version_dir(vdir, symbol_guess, tf, recipe, vid)
                    if art is not None:
                        versions.append(art)
                if not versions:
                    continue
                versions.sort(key=lambda a: (a.train_end or "", a.version_id))
                symbol = versions[-1].symbol or symbol_guess
                out.append({"symbol": symbol, "tf": tf, "recipe": recipe, "versions": versions})

    # Ancien layout plat : {base}/{recipe}_{tf}.meta.json (aucune dimension symbole).
    for fname in sorted(os.listdir(base_dir)):
        if not fname.endswith(".meta.json"):
            continue
        prefix = fname[: -len(".meta.json")]
        if "_" not in prefix:
            continue
        recipe, tf = prefix.rsplit("_", 1)
        art = _legacy_artifact(tf, recipe, base_dir)
        if art is not None:
            out.append({"symbol": art.symbol, "tf": tf, "recipe": recipe, "versions": [art]})

    return out


# ─────────────────────────────────────────────────────────────────────────────
#  Garde anti-chevauchement (frozen mode — cf. conception §4.1)
# ─────────────────────────────────────────────────────────────────────────────
def overlaps(artifact: ArtifactRef, window_start: Any, window_end: Any) -> bool:
    """True si la fenêtre d'entraînement de ``artifact`` chevauche
    ``[window_start, window_end]`` — signal qu'un backtest ``frozen``
    évaluerait le modèle sur des données qu'il a vues à l'entraînement.

    Si l'une des dates est inconnue (artefact legacy sans provenance), le
    chevauchement ne peut pas être vérifié : retourne ``False`` (mesure
    impossible, PAS absence de risque constatée — l'appelant doit le
    signaler séparément, cf. ``ArtifactRef.legacy``)."""
    if not artifact.train_start or not artifact.train_end:
        return False
    ws, we = to_iso(window_start), to_iso(window_end)
    if not ws or not we:
        return False
    return artifact.train_start <= we and ws <= artifact.train_end
