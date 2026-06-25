"""
FeatureStore — catalogue Parquet persistant de features pré-calculées, partagé
entre toutes les stratégies.

Idée directrice
---------------
Pendant les backtests, l'optimiseur (N trials × IS/OOS) et le live/paper, les
stratégies reconstruisent en boucle les MÊMES features lourdes (ex. le
``_FeatureBuilder`` ~436 colonnes partagé par opus_omnibus_v7…v10 et
opus_stat_pretrained_v4). Ce module mémoïse ces calculs sur disque, à côté des
OHLCV, dans un fichier unique par ``(symbol, timeframe)`` :

    data/features/{SYMBOL}/{TF}.parquet        # time + _ohlcv_h + {provider}__{col}…
    data/features/{SYMBOL}/{TF}.meta.json       # couverture + version par provider

Le fichier est un *catalogue* : il agrège les colonnes de TOUS les providers
enregistrés. Deux stratégies qui partagent le même provider partagent donc le
même calcul — effectué une seule fois puis relu en O(1).

Garanties de correction
------------------------
1. **Alignement temporel** : features stockées/relues par jointure sur ``time``
   (jamais par index positionnel).
2. **Hash strict par ligne** : une empreinte du contenu OHLCV (``_ohlcv_h``) est
   stockée par bougie. À chaque accès, on vérifie que l'OHLCV des bougies déjà en
   cache n'a pas changé (correction de données historiques). Toute divergence →
   recalcul complet plutôt que servir du périmé — priorité à la justesse (live inclus).
3. **Warmup convergence** : l'ajout incrémental recalcule le provider sur une
   fenêtre ``[premier_nouveau − warmup … fin]`` puis ne persiste que les nouvelles
   lignes. Un warmup généreux garantit que les indicateurs récursifs (EMA200…)
   ont convergé → valeurs identiques (~1e-9) au recalcul complet. Tout cas non
   géré proprement (trou, fenêtre antérieure au cache) déclenche un rebuild.

Thread-safety : un verrou par fichier (réutilise le registre de candle_store).
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import polars as pl

from app.core.candle_store import _get_file_lock  # verrous par fichier partagés

logger = logging.getLogger(__name__)

# Colonnes OHLCV servant au hash de contenu (détection de modifications historiques).
_OHLCV_HASH_COLS = ("open", "high", "low", "close", "volume")
_OHLCV_H = "_ohlcv_h"  # colonne d'empreinte par ligne dans le catalogue

# Warmup "illimité" : recalcul depuis l'index 0. Indispensable pour les builders
# contenant des features causales path-dépendantes (OBV cumulatif, compteurs type
# trend_duration / bars_since_cross…) dont la valeur à la barre i dépend de TOUT
# l'historique [0..i]. Les anciennes lignes en cache restent exactes (causalité) ;
# le gain dominant — zéro rebuild quand aucune bougie nouvelle — est préservé.
WARMUP_FULL = 10 ** 9


# ══════════════════════════════════════════════════════════════════════════════
#  Provider de features
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class FeatureProvider:
    """Décrit une source de features pré-calculables, partageable entre stratégies.

    name :
        Identifiant unique (namespace). Les colonnes sont préfixées ``{name}__``
        dans le catalogue. Ne doit pas contenir ``__``.
    build :
        ``build(df: pl.DataFrame) -> pl.DataFrame`` : prend un OHLCV Polars et
        retourne des features **alignées ligne-à-ligne** (même hauteur ; nulls de
        warmup autorisés). La colonne ``time`` est ajoutée automatiquement.
    warmup :
        Lookback (bougies) pour la convergence des indicateurs récursifs lors de
        l'ajout incrémental.
    version :
        Version du calcul ; la changer invalide le cache disque.
    """

    name: str
    build: Callable[[pl.DataFrame], pl.DataFrame]
    warmup: int = 1500
    version: str = "1"

    def __post_init__(self) -> None:
        if "__" in self.name:
            raise ValueError(
                f"[FeatureProvider] '{self.name}' : nom interdit (contient '__')."
            )


# Registre global (nom → provider) — permet le partage entre stratégies.
_REGISTRY: Dict[str, FeatureProvider] = {}
_REGISTRY_LOCK = threading.Lock()


def register_provider(provider: FeatureProvider) -> FeatureProvider:
    """Enregistre (ou récupère) un provider. Idempotent à version égale."""
    with _REGISTRY_LOCK:
        existing = _REGISTRY.get(provider.name)
        if existing is not None and existing.version == provider.version:
            return existing
        _REGISTRY[provider.name] = provider
        return provider


def get_provider(name: str) -> Optional[FeatureProvider]:
    with _REGISTRY_LOCK:
        return _REGISTRY.get(name)


def list_providers() -> List[str]:
    with _REGISTRY_LOCK:
        return sorted(_REGISTRY.keys())


# ══════════════════════════════════════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _ohlcv_hash_expr() -> pl.Expr:
    """Empreinte u64 par ligne du contenu OHLCV (déterministe, vectorisée)."""
    return pl.struct(list(_OHLCV_HASH_COLS)).hash(seed=0).alias(_OHLCV_H)


def _with_ohlcv_hash(df: pl.DataFrame) -> pl.DataFrame:
    cols = [c for c in _OHLCV_HASH_COLS if c in df.columns]
    return df.select(["time"] + cols).with_columns(
        pl.struct(cols).hash(seed=0).alias(_OHLCV_H)
    ).select(["time", _OHLCV_H])


# ══════════════════════════════════════════════════════════════════════════════
#  FeatureStore
# ══════════════════════════════════════════════════════════════════════════════

class FeatureStore:
    """Catalogue Parquet persistant de features par ``(symbol, timeframe)``."""

    def __init__(self, base_dir: str = "data/features"):
        self._base = Path(base_dir)
        logger.info(f"[FeatureStore] Initialisation — répertoire : {self._base.resolve()}")

    # ── API publique ───────────────────────────────────────────────────────

    def get_or_build(self, symbol: str, tf: str, df: pl.DataFrame,
                     provider: FeatureProvider) -> pl.DataFrame:
        """Retourne les features de ``provider`` pour les bougies de ``df``.

        Colonnes retournées : noms d'origine (sans namespace), alignées ligne-à-ligne
        avec ``df`` (jointure sur ``time``). Stratégie : cache → validation hash →
        incrémental → persistance ; rebuild complet en cas d'incohérence.
        """
        if df is None or df.height == 0 or "time" not in df.columns:
            return self._build_aligned(provider, df)  # pas d'alignement → calcul direct

        # Normalise time en ms (format CandleStore/Parquet) pour des jointures sûres.
        if df["time"].dtype != pl.Datetime("ms"):
            df = df.with_columns(pl.col("time").cast(pl.Datetime("ms")))

        path = self._path(symbol, tf)
        lock = _get_file_lock(path)
        prefix = f"{provider.name}__"

        with lock:
            cached, meta = self._load(path)
            entry = meta.get("providers", {}).get(provider.name)
            store_cols_present = cached is not None and any(
                c.startswith(prefix) for c in cached.columns)

            if cached is None or entry is None or not store_cols_present \
                    or entry.get("version") != provider.version:
                return self._full_rebuild(symbol, tf, df, provider, cached, meta, path)

            result = self._try_reuse(symbol, tf, df, provider, cached, entry, meta, path)
            if result is not None:
                return result
            return self._full_rebuild(symbol, tf, df, provider, cached, meta, path)

    # ── Cœur logique ─────────────────────────────────────────────────────────

    def _try_reuse(self, symbol, tf, df, provider, cached, entry, meta, path
                   ) -> Optional[pl.DataFrame]:
        """Relecture cache ou ajout incrémental. None → rebuild complet requis."""
        feat_cols: List[str] = entry.get("columns", [])
        prefix = f"{provider.name}__"
        req_times = df["time"]
        req_min, req_max = req_times.min(), req_times.max()
        cov_min = cached["time"].min()
        cov_max = cached["time"].max()

        # La fenêtre demandée commence avant la couverture du cache → rebuild
        # (on ne stocke pas l'OHLCV ancien pour recalculer proprement le warmup).
        if req_min < cov_min:
            return None

        # Validation hash strict sur le recouvrement (bougies déjà en cache).
        overlap_mask = (req_times >= cov_min) & (req_times <= cov_max)
        if overlap_mask.any():
            cur_h = _with_ohlcv_hash(df.filter(overlap_mask))
            cached_h = cached.select(["time", _OHLCV_H])
            joined = cur_h.join(cached_h, on="time", how="inner", suffix="_cache")
            if joined.height and (joined[_OHLCV_H] != joined[f"{_OHLCV_H}_cache"]).any():
                logger.info(
                    f"[FeatureStore] {symbol}/{tf} [{provider.name}] : OHLCV historique "
                    "modifié (hash) → rebuild complet.")
                return None

        # Bougies demandées au-delà de la couverture → ajout incrémental.
        if req_max > cov_max:
            new_mask = req_times > cov_max
            n_new = int(new_mask.sum())
            first_new_idx = df.height - n_new
            # Contiguïté : le suffixe du df doit être exactement les nouvelles bougies.
            warmup = int(entry.get("warmup", provider.warmup))
            start_idx = max(0, first_new_idx - warmup)
            window = df.slice(start_idx, df.height - start_idx)
            feats_win = self._build_aligned(provider, window)
            new_times = df["time"].filter(new_mask)
            new_feats = feats_win.filter(pl.col("time").is_in(new_times))

            renamed = new_feats.rename(
                {c: f"{prefix}{c}" for c in feat_cols if c in new_feats.columns})
            new_rows = (
                _with_ohlcv_hash(df.filter(new_mask))
                .join(renamed.select(["time"] + [f"{prefix}{c}" for c in feat_cols
                                                 if f"{prefix}{c}" in renamed.columns]),
                      on="time", how="left")
            )
            cached = pl.concat([cached, new_rows], how="diagonal").unique(
                "time", keep="last").sort("time")
            entry["min_time"] = str(cached["time"].min())
            entry["max_time"] = str(cached["time"].max())
            meta["providers"][provider.name] = entry
            self._save(path, cached, meta)
            logger.debug(
                f"[FeatureStore] {symbol}/{tf} [{provider.name}] : +{n_new} bougies "
                f"(incrémental, warmup={warmup})")

        return self._slice_for(df, cached, prefix, feat_cols)

    def _full_rebuild(self, symbol, tf, df, provider, cached, meta, path) -> pl.DataFrame:
        feats = self._build_aligned(provider, df)
        feat_cols = [c for c in feats.columns if c != "time"]
        # Build impossible (fenêtre trop courte) → pas de mise en cache, fallback.
        if feats.height == 0 or not feat_cols:
            return feats
        prefix = f"{provider.name}__"
        renamed = feats.rename({c: f"{prefix}{c}" for c in feat_cols})

        new_catalog = _with_ohlcv_hash(df).join(
            renamed.select(["time"] + [f"{prefix}{c}" for c in feat_cols]),
            on="time", how="left")

        store_df = self._merge_into_catalog(cached, new_catalog, prefix)
        meta.setdefault("providers", {})[provider.name] = {
            "version": provider.version,
            "warmup": provider.warmup,
            "columns": feat_cols,
            "min_time": str(df["time"].min()),
            "max_time": str(df["time"].max()),
        }
        self._save(path, store_df, meta)
        logger.info(
            f"[FeatureStore] {symbol}/{tf} [{provider.name}] : rebuild complet "
            f"({df.height} bougies, {len(feat_cols)} colonnes)")
        return feats

    # ── Helpers internes ──────────────────────────────────────────────────

    def _build_aligned(self, provider: FeatureProvider, df: pl.DataFrame) -> pl.DataFrame:
        """Appelle ``provider.build`` ; garantit l'alignement + colonne ``time``."""
        feats = provider.build(df)
        if feats is None:
            feats = pl.DataFrame()
        if not isinstance(feats, pl.DataFrame):
            feats = pl.from_pandas(feats)
        if df is not None and df.height and feats.height not in (0, df.height):
            raise ValueError(
                f"[FeatureStore] provider '{provider.name}' : {feats.height} lignes "
                f"pour {df.height} bougies — alignement rompu.")
        # Alignement 1:1 garanti → on (ré)écrit toujours ``time`` avec la valeur
        # canonique. Si le builder a produit sa propre colonne ``time`` (ex. le
        # _FeatureBuilder V4 réémet l'OHLCV brut), elle est ainsi normalisée et
        # ne sert que de clé de jointure, jamais de feature dupliquée.
        if df is not None and "time" in df.columns and feats.height == df.height:
            feats = feats.with_columns(df["time"].alias("time"))
        return feats

    @staticmethod
    def _slice_for(df: pl.DataFrame, store_df: pl.DataFrame,
                   prefix: str, feat_cols: List[str]) -> pl.DataFrame:
        """Extrait dans l'ordre de ``df`` les colonnes du provider (sans namespace)."""
        store_cols = [f"{prefix}{c}" for c in feat_cols
                      if f"{prefix}{c}" in store_df.columns]
        sub = store_df.select(["time"] + store_cols)
        out = df.select("time").join(sub, on="time", how="left")
        return out.rename({f"{prefix}{c}": c for c in feat_cols
                           if f"{prefix}{c}" in out.columns})

    @staticmethod
    def _merge_into_catalog(cached: Optional[pl.DataFrame], provider_df: pl.DataFrame,
                            prefix: str) -> pl.DataFrame:
        """Fusionne les colonnes d'UN provider dans le catalogue (jointure sur time),
        en remplaçant ses anciennes colonnes et conservant les autres providers."""
        if cached is None:
            return provider_df.sort("time")
        # Retire les anciennes colonnes de ce provider ; garde time/_ohlcv_h/autres.
        keep = [c for c in cached.columns if not c.startswith(prefix)]
        base = cached.select(keep)
        prov_only = provider_df.select(
            ["time"] + [c for c in provider_df.columns
                        if c.startswith(prefix)])
        merged = base.join(prov_only, on="time", how="full", coalesce=True)
        # Réconcilie _ohlcv_h (au cas où provider_df apporte des bougies nouvelles).
        if _OHLCV_H in provider_df.columns:
            h_new = provider_df.select(["time", _OHLCV_H])
            merged = (merged.drop(_OHLCV_H) if _OHLCV_H in merged.columns else merged)
            merged = merged.join(
                cached.select(["time", _OHLCV_H]), on="time", how="left")
            merged = merged.join(h_new, on="time", how="left", suffix="_n")
            merged = merged.with_columns(
                pl.coalesce([pl.col(f"{_OHLCV_H}_n"), pl.col(_OHLCV_H)]).alias(_OHLCV_H)
            ).drop(f"{_OHLCV_H}_n")
        return merged.sort("time")

    # ── Persistance ─────────────────────────────────────────────────────────

    def _path(self, symbol: str, tf: str) -> Path:
        safe = symbol.replace("/", "_").replace(":", "_")
        return self._base / safe / f"{tf}.parquet"

    def _meta_path(self, path: Path) -> Path:
        return path.with_suffix(".meta.json")

    def _load(self, path: Path) -> Tuple[Optional[pl.DataFrame], dict]:
        df: Optional[pl.DataFrame] = None
        meta: dict = {"providers": {}}
        if path.exists():
            try:
                df = pl.read_parquet(path)
                if "time" in df.columns and df["time"].dtype != pl.Datetime("ms"):
                    df = df.with_columns(pl.col("time").cast(pl.Datetime("ms")))
            except Exception as e:
                logger.warning(f"[FeatureStore] Parquet corrompu {path} — rebuild : {e}")
                df = None
        mp = self._meta_path(path)
        if mp.exists():
            try:
                meta = json.loads(mp.read_text())
                meta.setdefault("providers", {})
            except Exception as e:
                logger.warning(f"[FeatureStore] meta corrompue {mp} : {e}")
                meta = {"providers": {}}
        return df, meta

    def _save(self, path: Path, df: pl.DataFrame, meta: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            df.write_parquet(path, compression="zstd")
            self._meta_path(path).write_text(json.dumps(meta, indent=0))
        except Exception as e:
            logger.error(f"[FeatureStore] Erreur écriture {path} : {e}")

    def stats(self, symbol: str, tf: str) -> dict:
        path = self._path(symbol, tf)
        df, meta = self._load(path)
        if df is None:
            return {"symbol": symbol, "tf": tf, "rows": 0, "providers": []}
        return {
            "symbol": symbol, "tf": tf, "rows": df.height, "cols": df.width,
            "providers": list(meta.get("providers", {}).keys()),
            "size_kb": round(path.stat().st_size / 1024, 1) if path.exists() else 0,
        }


# ── Singleton global ─────────────────────────────────────────────────────────

_default_store: Optional[FeatureStore] = None
_store_lock = threading.Lock()


def get_feature_store(base_dir: str = "data/features") -> FeatureStore:
    """Retourne le singleton FeatureStore (lazy init, thread-safe)."""
    global _default_store
    if _default_store is None:
        with _store_lock:
            if _default_store is None:
                _default_store = FeatureStore(base_dir)
    return _default_store


# ══════════════════════════════════════════════════════════════════════════════
#  Helper d'intégration stratégies
# ══════════════════════════════════════════════════════════════════════════════

def cached_strategy_features(
    symbol: Optional[str],
    tf: Optional[str],
    df: "pl.DataFrame",
    *,
    name: str,
    version: str,
    warmup: int = WARMUP_FULL,
    builder: Callable,
    in_kind: str = "polars",
    out_kind: str = "polars",
    include_time: bool = True,
):
    """Pont entre une stratégie et le catalogue FeatureStore.

    Les stratégies gardent leur builder existant ; ce helper :
      1. l'enveloppe en provider polars-in / polars-out (conversions auto),
      2. passe par le catalogue disque quand ``symbol``/``tf`` sont connus
         (backtest, optimiseur, live), sinon calcule en direct,
      3. reconvertit le résultat dans le type attendu par la stratégie.

    Paramètres
    ----------
    name :
        Nom du provider (namespace). Deux stratégies passant le MÊME ``name`` et
        la même ``version`` partagent le calcul sur disque (dédup catalogue).
    builder :
        ``builder(input) -> features`` aligné 1:1 avec ``df`` (ou ``None`` si la
        fenêtre est trop courte). ``input`` est du type ``in_kind``, la sortie du
        type ``out_kind``.
    in_kind / out_kind :
        ``"polars"`` | ``"pandas"`` | ``"numpy"``.
    include_time :
        Si ``False``, la colonne ``time`` est retirée du résultat (cas des
        stratégies qui consomment les features positionnellement, ex.
        ``feats.to_numpy()`` : ``ml_dynamic_threshold``). Sans effet pour ``numpy``
        (``time`` déjà exclu).

    Retour : features dans ``out_kind`` alignées à ``df``, ou ``None`` si le
    builder n'a rien pu produire (la stratégie retombe sur son chemin habituel).
    """
    import numpy as _np

    def _pbuild(window: pl.DataFrame) -> pl.DataFrame:
        inp = window.to_pandas() if in_kind == "pandas" else window
        out = builder(inp)
        if out is None:
            return pl.DataFrame()
        if out_kind == "pandas":
            res = pl.from_pandas(out)
        elif out_kind == "numpy":
            arr = _np.asarray(out)
            if arr.ndim == 1:
                arr = arr.reshape(-1, 1)
            res = pl.DataFrame(
                arr, schema=[f"c{i}" for i in range(arr.shape[1])])
        else:
            res = out
        return res

    provider = register_provider(
        FeatureProvider(name=name, build=_pbuild, warmup=warmup, version=version))

    use_cache = bool(symbol) and bool(tf) and df is not None \
        and df.height > 0 and "time" in df.columns
    if use_cache:
        res = get_feature_store().get_or_build(symbol, tf, df, provider)
        # Garde-fou anti-désalignement du cache incrémental. Pour une fenêtre qui
        # ne commence pas au bord du cache (typiquement la tranche OOS de
        # l'optimiseur, qui suit la tranche IS sans chevauchement d'historique),
        # l'ajout incrémental calcule les features sur une fenêtre dépourvue de
        # warmup : les premières barres ressortent à NaN, y compris les colonnes
        # « passthrough » (close/OHLC), puis sont mises en cache et relues. Ces
        # close NaN faussent ensuite le labelling ML (labels mono-classe →
        # entraînement LightGBM ignoré → 0 trade en OOS → score OOS = -999 pour
        # les opus_omnibus_v*). Si une colonne passthrough est NaN là où df est
        # finie (ou si la hauteur diffère), on retombe sur un build direct correct
        # pour cette fenêtre (le cache disque n'est pas relu pour ce calcul).
        if res is not None and res.height and "close" in res.columns \
                and "close" in df.columns:
            _rc = res["close"].to_numpy()
            _dc = df["close"].to_numpy()
            _misaligned = (_rc.shape != _dc.shape) or bool(
                ((~_np.isfinite(_rc)) & _np.isfinite(_dc)).any())
            if _misaligned:
                logger.warning(
                    f"[FeatureStore] {symbol}/{tf} [{name}] : cache désaligné "
                    "(close NaN sur le warmup d'une fenêtre hors-bord) → build direct.")
                res = _pbuild(df)
    else:
        res = _pbuild(df)

    if res is None or res.height == 0:
        return None

    if not include_time and "time" in res.columns:
        res = res.drop("time")
    if out_kind == "pandas":
        return res.to_pandas()
    if out_kind == "numpy":
        cols = [c for c in res.columns if c != "time"]
        return res.select(cols).to_numpy()
    return res
