"""
Stratégie ML à seuil dynamique — V6

Différences clés vs ml_strategy.py :
  - Labels DYNAMIQUES : le seuil de hausse/baisse s'adapte à la volatilité
    réalisée du marché (ATR/écart-type sur 20 périodes * sqrt(lookahead)).
    Évite les faux signaux en range serré et les signaux trop tardifs en trend fort.
  - Filtre de régime ADX : bloque le modèle si le marché est trop plat.
  - Double modèle : Random Forest ou Logistic Regression au choix.
  - Correction robuste UndefinedMetricWarning via np.nanmean sur les folds
    mono-classe (problème fréquent sur petits datasets crypto).
  - Vérification de la distribution des classes avant entraînement.
"""
import logging
import random
import warnings
from typing import Dict, Any, List, Tuple, Optional

import numpy as np
import pandas as pd
from sklearn.ensemble        import RandomForestClassifier
from sklearn.linear_model    import LogisticRegression
from sklearn.preprocessing   import StandardScaler
from sklearn.model_selection import cross_val_score, TimeSeriesSplit
from sklearn.pipeline        import Pipeline
from sklearn.exceptions      import UndefinedMetricWarning

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
#  Feature Engineering — identique à ml_strategy (avancé)
# ═══════════════════════════════════════════════════════════════
def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Construit 30+ features techniques depuis OHLCV.
    Inclut log-returns, VWAP rolling, micro-structure des bougies,
    divergence RSI/prix — plus robustes que les simples pct_change.
    """
    if len(df) < 100:
        return pd.DataFrame()

    close  = df["close"].astype(float)
    high   = df["high"].astype(float)
    low    = df["low"].astype(float)
    volume = df["volume"].astype(float)
    open_p = df["open"].astype(float)
    feats  = pd.DataFrame(index=df.index)

    # ── Log-Returns (normalisation statistique) ────────────────
    feats["log_ret_1"]  = np.log(close / close.shift(1).clip(lower=1e-9))
    feats["log_ret_3"]  = np.log(close / close.shift(3).clip(lower=1e-9))
    feats["log_ret_5"]  = np.log(close / close.shift(5).clip(lower=1e-9))
    feats["log_ret_10"] = np.log(close / close.shift(10).clip(lower=1e-9))
    feats["log_ret_20"] = np.log(close / close.shift(20).clip(lower=1e-9))

    # ── Rolling VWAP (Volume Weighted Average Price) ───────────
    typical_price   = (high + low + close) / 3
    vwap_20 = (typical_price * volume).rolling(20).sum() / volume.rolling(20).sum().clip(lower=1e-9)
    vwap_50 = (typical_price * volume).rolling(50).sum() / volume.rolling(50).sum().clip(lower=1e-9)
    feats["vwap_dist_20"] = (close - vwap_20) / vwap_20.clip(lower=1e-9)
    feats["vwap_dist_50"] = (close - vwap_50) / vwap_50.clip(lower=1e-9)

    # ── Micro-structure : pression intra-bougie ────────────────
    body_size   = (close - open_p).abs()
    upper_wick  = high - pd.concat([open_p, close], axis=1).max(axis=1)
    lower_wick  = pd.concat([open_p, close], axis=1).min(axis=1) - low
    total_range = (high - low).clip(lower=1e-9)
    feats["body_to_range"]    = body_size   / total_range
    feats["upper_wick_ratio"] = upper_wick  / total_range
    feats["lower_wick_ratio"] = lower_wick  / total_range

    # ── RSI & divergence approximative ────────────────────────
    rsi_14 = _rsi(close, 14)
    feats["rsi_14"] = rsi_14 / 100.0
    price_slope     = close.diff(5)
    rsi_slope       = rsi_14.diff(5)
    feats["rsi_divergence"] = np.sign(price_slope) * np.sign(rsi_slope)

    # ── EMAs relatifs au prix ──────────────────────────────────
    for s in [8, 13, 21, 50, 100]:
        ema = close.ewm(span=s, adjust=False).mean()
        feats[f"ema{s}_rel"] = (close - ema) / close.clip(lower=1e-9)

    # ── Croisements EMA ───────────────────────────────────────
    ema8  = close.ewm(span=8,  adjust=False).mean()
    ema21 = close.ewm(span=21, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    feats["cross_8_21"]  = (ema8  - ema21) / close.clip(lower=1e-9)
    feats["cross_21_50"] = (ema21 - ema50) / close.clip(lower=1e-9)

    # ── MACD ──────────────────────────────────────────────────
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd  = ema12 - ema26
    sig   = macd.ewm(span=9, adjust=False).mean()
    feats["macd_hist_norm"] = (macd - sig) / close.clip(lower=1e-9)
    feats["macd_norm"]      = macd / close.clip(lower=1e-9)

    # ── ATR relatif ───────────────────────────────────────────
    for period in [7, 14]:
        feats[f"atr{period}_rel"] = _atr_series(df, period) / close.clip(lower=1e-9)

    # ── Bollinger %B ──────────────────────────────────────────
    for period in [10, 20]:
        sma = close.rolling(period).mean()
        std = close.rolling(period).std().replace(0, 1e-9)
        feats[f"bb_pct_{period}"]   = (close - (sma - 2*std)) / (4 * std)
        feats[f"bb_width_{period}"] = (4 * std) / sma.clip(lower=1e-9)

    # ── Momentum ──────────────────────────────────────────────
    feats["mom_5"]  = close / close.shift(5).clip(lower=1e-9) - 1
    feats["mom_20"] = close / close.shift(20).clip(lower=1e-9) - 1

    # ── Volume ────────────────────────────────────────────────
    vol_ma = volume.rolling(20).mean().clip(lower=1e-9)
    feats["vol_ratio"]     = volume / vol_ma
    feats["vol_trend_5"]   = vol_ma.pct_change(5)
    feats["vol_price_corr"]= close.rolling(10).corr(volume)

    # ── Volatilité réalisée ───────────────────────────────────
    feats["vol_real_5"]  = close.pct_change().rolling(5).std()
    feats["vol_real_20"] = close.pct_change().rolling(20).std()
    feats["vol_ratio_rv"]= feats["vol_real_5"] / feats["vol_real_20"].clip(lower=1e-9)

    # ── ADX ───────────────────────────────────────────────────
    feats["adx_14"] = _adx_series(df, 14)

    # ── High/Low range normalisé ──────────────────────────────
    feats["hl_range"]  = (high - low) / close.clip(lower=1e-9)
    feats["close_pos"] = (close - low) / (high - low + 1e-9)

    feats.replace([np.inf, -np.inf], np.nan, inplace=True)
    feats.fillna(0, inplace=True)
    return feats


# ═══════════════════════════════════════════════════════════════
#  Labeling DYNAMIQUE — seuil adaptatif basé sur la volatilité
# ═══════════════════════════════════════════════════════════════
def compute_labels(df: pd.DataFrame, lookahead: int = 3,
                   vol_multiplier: float = 0.6) -> pd.Series:
    """
    Le seuil de hausse n'est pas fixe (ex: +0.2%) mais adaptatif :
      seuil = volatilité_20p * sqrt(lookahead) * vol_multiplier

    Avantage : en range serré le seuil est bas (→ plus de signaux valides),
    en tendance forte il est plus élevé (→ filtre les micro-retours).
    """
    close = df["close"].astype(float)
    log_ret = np.log(close / close.shift(1).clip(lower=1e-9))
    volatility = log_ret.rolling(window=20).std()
    dynamic_threshold = volatility * np.sqrt(lookahead) * vol_multiplier
    future = np.log(close.shift(-lookahead) / close.clip(lower=1e-9))
    labels = (future > dynamic_threshold).astype(int)
    labels.name = "label"
    return labels


# ═══════════════════════════════════════════════════════════════
#  Espaces de paramètres
# ═══════════════════════════════════════════════════════════════
PARAM_SPACE_RF = {
    "clf__n_estimators":      [100, 200, 300],
    "clf__max_depth":         [3, 5, 8, None],
    "clf__min_samples_leaf":  [5, 10, 20],
    "clf__min_samples_split": [10, 20],
    "clf__class_weight":      ["balanced", None],
}

PARAM_SPACE_LR = {
    "clf__C":            [0.1, 1.0, 10.0],
    "clf__penalty":      ["l2"],
    "clf__solver":       ["liblinear"],
    "clf__class_weight": [None, "balanced"],
}


def random_search_hyperparams(
    X: np.ndarray, y: np.ndarray,
    model_type: str = "random_forest",
    n_trials: int = 20,
    cv_folds: int = 5,
    seed: int = 42,
) -> Tuple[dict, float, List[dict]]:
    """
    Random Search avec TimeSeriesSplit.
    Correction UndefinedMetricWarning : utilise np.nanmean pour ignorer
    les folds mono-classe sans crasher.
    """
    rng          = random.Random(seed)
    space        = PARAM_SPACE_RF if model_type == "random_forest" else PARAM_SPACE_LR
    actual_folds = min(cv_folds, 5)
    if actual_folds < 2:
        return {}, 0.0, []

    tscv        = TimeSeriesSplit(n_splits=actual_folds)
    best_params = {}
    best_score  = -1.0
    all_results: List[dict] = []

    logger.info(f"[MLDynThreshold] Random Search — {n_trials} essais, modèle={model_type}")

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=UndefinedMetricWarning)
        for trial in range(n_trials):
            params = {k: rng.choice(v) for k, v in space.items()}
            try:
                pipeline = _build_pipeline(model_type, params)
                scores   = cross_val_score(
                    pipeline, X, y, cv=tscv,
                    scoring="roc_auc", n_jobs=-1,
                    error_score=np.nan,   # NaN sur fold mono-classe au lieu de crash
                )
                if np.isnan(scores).all():
                    continue  # tous les folds invalides → skip
                mean_auc = float(np.nanmean(scores))
                std_auc  = float(np.nanstd(scores))
                all_results.append({"params": params, "auc": mean_auc, "std": std_auc})
                if mean_auc > best_score:
                    best_score, best_params = mean_auc, params
                    logger.info(
                        f"  [Trial {trial+1:02d}] Nouveau meilleur AUC={mean_auc:.4f}"
                        f"±{std_auc:.4f} | {params}"
                    )
            except Exception as e:
                logger.debug(f"  [Trial {trial+1:02d}] KO : {e}")

    logger.info(f"[MLDynThreshold] Meilleur AUC={best_score:.4f} | {best_params}")
    all_results.sort(key=lambda x: -x["auc"])
    return best_params, best_score, all_results


def _build_pipeline(model_type: str, params: dict) -> Pipeline:
    clf_params = {k.replace("clf__", ""): v for k, v in params.items() if k.startswith("clf__")}
    if model_type == "random_forest":
        clf = RandomForestClassifier(random_state=42, **clf_params)
    else:
        clf = LogisticRegression(random_state=42, **clf_params)
    return Pipeline([("scaler", StandardScaler()), ("clf", clf)])


# ═══════════════════════════════════════════════════════════════
#  Classe Strategy — interface compatible moteur v6
# ═══════════════════════════════════════════════════════════════
class MLDynamicThresholdStrategy:
    """
    Stratégie ML à seuil dynamique.
    Interface identique aux autres stratégies v6 : méthode score() retourne
    un dict {score, side, name, reason, conditions, indicators}.
    """
    name = "ml_dynamic_threshold"

    def __init__(self,
                 model_type:     str   = "random_forest",
                 lookahead:      int   = 3,
                 vol_multiplier: float = 0.6,
                 adx_min:        float = 20.0,
                 proba_long:     float = 0.60,
                 proba_short:    float = 0.40,
                 n_trials:       int   = 15,
                 min_train:      int   = 150,
                 retrain_every:  int   = 50):

        self.model_type     = model_type
        self.lookahead      = lookahead
        self.vol_multiplier = vol_multiplier
        self.adx_min        = adx_min
        self.proba_long     = proba_long
        self.proba_short    = proba_short
        self.n_trials       = n_trials
        self.min_train      = min_train
        self.retrain_every  = retrain_every

        self._pipeline:     Optional[Pipeline] = None
        self._trained:      bool  = False
        self._call_count:   int   = 0
        self._best_auc:     float = 0.0
        self._best_params:  dict  = {}
        self._feature_cols: List[str] = []
        self._train_idx:    int   = 0

    # ── Interface principale ───────────────────────────────────
    def score(self, df: pd.DataFrame, params: dict = None) -> Dict[str, Any]:
        """
        Interface compatible Engine v6.
        Applique les params de config si fournis (premier appel).
        """
        if params:
            p = params.get(self.name, {})
            if p and not self._trained:
                for k, v in p.items():
                    if hasattr(self, k):
                        setattr(self, k, v)
        return self._signal(df)

    def _signal(self, df: pd.DataFrame) -> Dict[str, Any]:
        self._call_count += 1
        if len(df) < self.min_train + 20:
            return self._no_signal()

        needs_train = (
            not self._trained or
            self._call_count % self.retrain_every == 0
        )
        if needs_train:
            self._fit(df)

        if not self._trained:
            return self._no_signal()

        return self._predict(df)

    # ── Entraînement ──────────────────────────────────────────
    def _fit(self, df: pd.DataFrame) -> None:
        try:
            feats  = compute_features(df)
            labels = compute_labels(df, self.lookahead, self.vol_multiplier)

            common = feats.index.intersection(labels.dropna().index)
            valid  = common[:-self.lookahead] if len(common) > self.lookahead else common
            X      = feats.loc[valid].values
            y      = labels.loc[valid].values

            # Sécurité : deux classes minimum
            if len(np.unique(y)) < 2:
                logger.warning(
                    f"[{self.name}] Fit annulé : une seule classe dans les labels. "
                    "Augmentez min_train ou réduisez vol_multiplier."
                )
                return

            # Sécurité : effectif minimum par classe
            class_counts = np.bincount(y)
            if class_counts.min() < 10:
                logger.warning(
                    f"[{self.name}] Fit annulé : classe minoritaire trop petite "
                    f"({class_counts.min()} exemples)."
                )
                return

            best_p, best_auc, _ = random_search_hyperparams(
                X, y, self.model_type, self.n_trials
            )
            pipeline = _build_pipeline(self.model_type, best_p)
            pipeline.fit(X, y)

            self._pipeline     = pipeline
            self._best_params  = best_p
            self._best_auc     = best_auc
            self._feature_cols = list(feats.columns)
            self._trained      = True
            self._train_idx    = len(df)

            # ── Validation IS/OOS — détection sur-apprentissage ───────────
            try:
                split = int(len(X) * 0.8)
                if split > 20 and len(X) - split > 10:
                    from sklearn.metrics import roc_auc_score as _auc
                    pipe_oos = _build_pipeline(self.model_type, best_p)
                    pipe_oos.fit(X[:split], y[:split])
                    proba_oos = pipe_oos.predict_proba(X[split:])[:, 1]
                    oos_auc   = float(_auc(y[split:], proba_oos))
                    ratio     = best_auc / max(oos_auc, 1e-9)
                    status    = "✅ robuste" if ratio < 1.3 else "⚠️ surapprentissage probable"
                    logger.info(
                        f"[{self.name}] IS AUC={best_auc:.4f} | OOS AUC={oos_auc:.4f} "
                        f"| Ratio IS/OOS={ratio:.2f} — {status}"
                    )
            except Exception as oe:
                logger.debug(f"[{self.name}] IS/OOS check KO : {oe}")

            logger.info(
                f"[{self.name}] Modèle entraîné — AUC={best_auc:.4f} | n={len(X)}"
            )
        except Exception as e:
            logger.error(f"[{self.name}] Erreur entraînement : {e}")

    # ── Prédiction ────────────────────────────────────────────
    def _predict(self, df: pd.DataFrame) -> Dict[str, Any]:
        try:
            feats = compute_features(df)
            if feats.empty:
                return self._no_signal()

            # Filtre ADX — régime de marché
            adx_val = float(_adx_series(df, 14).iloc[-1])
            if adx_val < self.adx_min:
                return {
                    "score":      0.0,
                    "side":       "none",
                    "name":       self.name,
                    "reason":     f"Filtre régime : ADX={adx_val:.1f} < {self.adx_min}",
                    "conditions": [f"ADX insuffisant ({adx_val:.1f})"],
                    "indicators": {"adx": round(adx_val, 2), "auc": round(self._best_auc, 4)},
                }

            X_last = feats.iloc[[-1]].values
            proba  = float(self._pipeline.predict_proba(X_last)[0, 1])
            close  = float(df["close"].iloc[-1])

            if proba >= self.proba_long:
                side  = "long"
                score = 0.5 + (proba - self.proba_long) / max(1.0 - self.proba_long, 1e-9) * 0.5
            elif proba <= self.proba_short:
                side  = "short"
                score = 0.5 + (self.proba_short - proba) / max(self.proba_short, 1e-9) * 0.5
            else:
                return self._no_signal()

            score = round(min(score, 0.99), 3)

            indicators = {
                "proba_up":      round(proba, 4),
                "model":         self.model_type,
                "auc":           round(self._best_auc, 4),
                "adx":           round(adx_val, 2),
                "n_features":    len(self._feature_cols),
                "lookahead":     self.lookahead,
                "vol_multiplier":self.vol_multiplier,
                "close":         close,
            }

            return {
                "score":      score,
                "side":       side,
                "name":       self.name,
                "reason":     (
                    f"ML-DynThreshold {self.model_type} — proba_up={proba:.3f} "
                    f"(long≥{self.proba_long}, short≤{self.proba_short}, ADX={adx_val:.1f})"
                ),
                "conditions": [
                    f"Proba hausse : {proba:.3f}",
                    f"Modèle : {self.model_type}",
                    f"AUC cross-val : {self._best_auc:.4f}",
                    f"Lookahead : {self.lookahead} bougies",
                    f"Seuil dynamique (vol_mult={self.vol_multiplier})",
                    f"ADX : {adx_val:.1f}",
                ],
                "indicators": indicators,
            }

        except Exception as e:
            logger.error(f"[{self.name}] Erreur prédiction : {e}")
            return self._no_signal()

    @staticmethod
    def _no_signal() -> Dict[str, Any]:
        return {
            "score":      0.0,
            "side":       "none",
            "name":       "ml_dynamic_threshold",
            "reason":     "Pas de signal ML",
            "conditions": [],
            "indicators": {},
        }


# ═══════════════════════════════════════════════════════════════
#  Helpers indicateurs (locaux — pas de dépendance circulaire)
# ═══════════════════════════════════════════════════════════════
def _rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _atr_series(df: pd.DataFrame, period: int) -> pd.Series:
    h, l, c = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float)
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def _adx_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c  = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float)
    tr       = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    plus_dm  = (h - h.shift()).clip(lower=0)
    minus_dm = (l.shift() - l).clip(lower=0)
    plus_dm [plus_dm  <= minus_dm] = 0
    minus_dm[minus_dm <= plus_dm ] = 0
    atr14    = tr.rolling(period).mean().replace(0, np.nan)
    dip      = 100 * plus_dm.rolling(period).mean()  / atr14
    dim      = 100 * minus_dm.rolling(period).mean() / atr14
    dx       = 100 * (dip - dim).abs() / (dip + dim).replace(0, np.nan)
    return dx.rolling(period).mean().fillna(0)


# Alias Engine
class Strategy(MLDynamicThresholdStrategy):
    pass
