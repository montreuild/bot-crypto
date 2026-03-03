"""
Stratégie ML — Random Forest avec optimisation d'hyperparamètres par Random Search.
Features : indicateurs techniques. Prédit Hausse/Baisse à horizon configurable.
"""
import logging
import random
from copy import deepcopy
from typing import Dict, Any, List, Tuple, Optional

import numpy as np
import pandas as pd
from sklearn.ensemble        import RandomForestClassifier
from sklearn.linear_model    import LogisticRegression
from sklearn.preprocessing   import StandardScaler
from sklearn.model_selection import cross_val_score, TimeSeriesSplit
from sklearn.pipeline        import Pipeline
from sklearn.metrics         import roc_auc_score

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
#  Feature Engineering
# ═══════════════════════════════════════════════════════════════
def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Construit 20+ features techniques depuis OHLCV.
    Toutes normalisées pour être scale-agnostiques.
    """
    if len(df) < 60:
        return pd.DataFrame()

    close  = df["close"].astype(float)
    high   = df["high"].astype(float)
    low    = df["low"].astype(float)
    volume = df["volume"].astype(float)
    feats  = pd.DataFrame(index=df.index)

    # ── Retours normalisés ─────────────────────────────────────
    feats["ret_1"]  = close.pct_change(1)
    feats["ret_3"]  = close.pct_change(3)
    feats["ret_5"]  = close.pct_change(5)
    feats["ret_10"] = close.pct_change(10)
    feats["ret_20"] = close.pct_change(20)

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

    # ── RSI ───────────────────────────────────────────────────
    for period in [7, 14, 21]:
        feats[f"rsi_{period}"] = _rsi(close, period) / 100.0

    # ── MACD ──────────────────────────────────────────────────
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd  = ema12 - ema26
    sig   = macd.ewm(span=9, adjust=False).mean()
    feats["macd_hist_norm"] = (macd - sig) / close.clip(lower=1e-9)
    feats["macd_norm"]      = macd / close.clip(lower=1e-9)

    # ── ATR relatif ───────────────────────────────────────────
    for period in [7, 14]:
        feats[f"atr{period}_rel"] = _atr(df, period) / close.clip(lower=1e-9)

    # ── Bollinger %B ──────────────────────────────────────────
    for period in [10, 20]:
        sma = close.rolling(period).mean()
        std = close.rolling(period).std().replace(0, 1e-9)
        feats[f"bb_pct_{period}"]   = (close - (sma - 2*std)) / (4 * std)
        feats[f"bb_width_{period}"] = (4 * std) / sma.clip(lower=1e-9)

    # ── Momentum ──────────────────────────────────────────────
    feats["mom_5"]  = close / close.shift(5).clip(lower=1e-9)  - 1
    feats["mom_20"] = close / close.shift(20).clip(lower=1e-9) - 1

    # ── Volume ────────────────────────────────────────────────
    vol_ma = volume.rolling(20).mean().clip(lower=1e-9)
    feats["vol_ratio"]    = volume / vol_ma
    feats["vol_trend_5"]  = vol_ma.pct_change(5)
    feats["vol_price_corr"]= close.rolling(10).corr(volume)

    # ── Volatilité réalisée ───────────────────────────────────
    feats["vol_real_5"]  = close.pct_change().rolling(5).std()
    feats["vol_real_20"] = close.pct_change().rolling(20).std()
    feats["vol_ratio_rv"]= feats["vol_real_5"] / feats["vol_real_20"].clip(lower=1e-9)

    # ── ADX simplifié ─────────────────────────────────────────
    feats["adx_14"] = _adx_series(df, 14)

    # ── High/Low range normalisé ──────────────────────────────
    feats["hl_range"]  = (high - low) / close.clip(lower=1e-9)
    feats["close_pos"] = (close - low) / (high - low + 1e-9)   # position dans la bougie

    # ── Nettoyage ─────────────────────────────────────────────
    feats.replace([np.inf, -np.inf], np.nan, inplace=True)
    feats.fillna(0, inplace=True)
    return feats


def compute_labels(df: pd.DataFrame, lookahead: int = 3,
                   threshold: float = 0.002) -> pd.Series:
    """
    Label : 1 si close[t+lookahead] > close[t] * (1+threshold), sinon 0.
    Évite le look-ahead bias — la série est alignée avec les features.
    """
    close   = df["close"].astype(float)
    future  = close.shift(-lookahead)
    labels  = ((future - close) / close > threshold).astype(int)
    labels.name = "label"
    return labels


# ═══════════════════════════════════════════════════════════════
#  Optimiseur hyperparamètres — Random Search
# ═══════════════════════════════════════════════════════════════
PARAM_SPACE_RF = {
    "clf__n_estimators":       [50, 100, 200, 300, 500],
    "clf__max_depth":          [3, 4, 5, 6, 8, None],
    "clf__min_samples_leaf":   [3, 5, 10, 20],
    "clf__min_samples_split":  [5, 10, 20],
    "clf__max_features":       ["sqrt", "log2", 0.5, 0.7],
    "clf__class_weight":       [None, "balanced"],
}

PARAM_SPACE_LR = {
    "clf__C":               [0.01, 0.1, 0.5, 1.0, 5.0, 10.0],
    "clf__penalty":         ["l1", "l2"],
    "clf__solver":          ["liblinear"],
    "clf__class_weight":    [None, "balanced"],
    "clf__max_iter":        [200, 500, 1000],
}


def random_search_hyperparams(
    X: np.ndarray, y: np.ndarray,
    model_type: str = "random_forest",
    n_trials: int = 30,
    cv_folds: int = 5,
    seed: int = 42
) -> Tuple[dict, float, List[dict]]:
    """
    Random Search sur les hyperparamètres avec TimeSeriesSplit.
    Retourne (best_params, best_auc, all_results).
    """
    rng   = random.Random(seed)
    space = PARAM_SPACE_RF if model_type == "random_forest" else PARAM_SPACE_LR
    tscv  = TimeSeriesSplit(n_splits=cv_folds)

    best_params, best_score = {}, -1.0
    all_results: List[dict] = []

    logger.info(f"[MLStrategy] Random Search — {n_trials} essais, modèle={model_type}")

    for trial in range(n_trials):
        params = {k: rng.choice(v) for k, v in space.items()}
        try:
            pipeline = _build_pipeline(model_type, params)
            scores   = cross_val_score(pipeline, X, y, cv=tscv, scoring="roc_auc", n_jobs=-1)
            mean_auc = float(scores.mean())
            std_auc  = float(scores.std())
            all_results.append({"params": params, "auc": mean_auc, "std": std_auc})
            if mean_auc > best_score:
                best_score, best_params = mean_auc, params
                logger.info(f"  [Trial {trial+1:02d}] Nouveau meilleur AUC={mean_auc:.4f}±{std_auc:.4f} | {params}")
        except Exception as e:
            logger.debug(f"  [Trial {trial+1:02d}] KO : {e}")

    logger.info(f"[MLStrategy] Meilleur AUC={best_score:.4f} | {best_params}")
    all_results.sort(key=lambda x: -x["auc"])
    return best_params, best_score, all_results


def _build_pipeline(model_type: str, params: dict) -> Pipeline:
    """Construit un pipeline sklearn avec les paramètres donnés."""
    clf_params = {k.replace("clf__", ""): v for k, v in params.items() if k.startswith("clf__")}
    if model_type == "random_forest":
        clf = RandomForestClassifier(random_state=42, **clf_params)
    else:
        clf = LogisticRegression(random_state=42, **clf_params)
    return Pipeline([("scaler", StandardScaler()), ("clf", clf)])


# ═══════════════════════════════════════════════════════════════
#  Classe Strategy — intégration dans le moteur de backtest
# ═══════════════════════════════════════════════════════════════
class MLStrategy:
    """
    Stratégie IA : entraîne un modèle ML sur les données historiques
    et génère des signaux d'achat/vente basés sur la probabilité prédite.
    Hyperparamètres optimisés par Random Search.
    """
    name = "ml_strategy"

    def __init__(self,
                 model_type: str = "random_forest",
                 lookahead:  int = 3,
                 threshold:  float = 0.002,
                 proba_long: float = 0.60,
                 proba_short:float = 0.40,
                 n_trials:   int = 20,
                 min_train:  int = 150,
                 retrain_every: int = 50):
        self.model_type    = model_type
        self.lookahead     = lookahead
        self.threshold     = threshold
        self.proba_long    = proba_long
        self.proba_short   = proba_short
        self.n_trials      = n_trials
        self.min_train     = min_train
        self.retrain_every = retrain_every

        self._pipeline:    Optional[Pipeline] = None
        self._trained:     bool = False
        self._train_idx:   int  = 0
        self._best_params: dict = {}
        self._best_auc:    float = 0.0
        self._feature_cols:List[str] = []
        self._call_count:  int  = 0

    def signal(self, df: pd.DataFrame, params: dict = None) -> dict:
        """
        Génère un signal depuis le modèle ML.
        Entraîne/réentraîne le modèle si nécessaire.
        """
        self._call_count += 1
        if len(df) < self.min_train + 20:
            return self._no_signal()

        # Ré-entraînement périodique
        needs_train = (not self._trained or
                       self._call_count % self.retrain_every == 0)

        if needs_train:
            self._fit(df)

        if not self._trained:
            return self._no_signal()

        return self._predict(df)

    def _fit(self, df: pd.DataFrame):
        """Entraîne le modèle sur tout l'historique disponible."""
        try:
            feats  = compute_features(df)
            labels = compute_labels(df, self.lookahead, self.threshold)

            # Alignement sans look-ahead
            common  = feats.index.intersection(labels.dropna().index)
            # Exclure les dernières `lookahead` lignes (futur inconnu)
            valid   = common[:-self.lookahead] if len(common) > self.lookahead else common
            X       = feats.loc[valid].values
            y       = labels.loc[valid].values

            if len(X) < self.min_train or y.sum() < 10 or (len(y) - y.sum()) < 10:
                logger.debug(f"[MLStrategy] Données insuffisantes pour entraîner ({len(X)} samples)")
                return

            # Random Search hyperparamètres
            best_params, best_auc, _ = random_search_hyperparams(
                X, y, model_type=self.model_type,
                n_trials=self.n_trials, cv_folds=5
            )
            self._best_params = best_params
            self._best_auc    = best_auc

            # Entraînement final sur tout l'ensemble
            pipeline = _build_pipeline(self.model_type, best_params)
            pipeline.fit(X, y)
            self._pipeline     = pipeline
            self._feature_cols = list(feats.columns)
            self._trained      = True
            self._train_idx    = len(df)
            logger.info(f"[MLStrategy] Modèle entraîné — AUC={best_auc:.4f} | n={len(X)}")

        except Exception as e:
            logger.error(f"[MLStrategy] Erreur entraînement : {e}")

    def _predict(self, df: pd.DataFrame) -> dict:
        """Prédit la direction du prochain mouvement."""
        try:
            feats = compute_features(df)
            if feats.empty:
                return self._no_signal()

            # Dernier vecteur de features (barre courante)
            X_last = feats.iloc[[-1]].values
            proba  = float(self._pipeline.predict_proba(X_last)[0, 1])

            close = float(df["close"].iloc[-1])

            if proba >= self.proba_long:
                side  = "long"
                score = 0.5 + (proba - self.proba_long) / (1.0 - self.proba_long) * 0.5
            elif proba <= self.proba_short:
                side  = "short"
                score = 0.5 + (self.proba_short - proba) / self.proba_short * 0.5
            else:
                return self._no_signal()

            score = round(min(score, 0.99), 3)
            feats_dict = {
                "proba_up":   round(proba, 4),
                "model":      self.model_type,
                "auc":        round(self._best_auc, 4),
                "n_features": len(self._feature_cols),
                "lookahead":  self.lookahead,
                "close":      close,
            }

            return {
                "side":       side,
                "score":      score,
                "name":       self.name,
                "reason":     (f"ML {self.model_type} — proba_up={proba:.3f} "
                               f"(seuil long={self.proba_long}, short={self.proba_short})"),
                "conditions": [
                    f"Proba hausse : {proba:.3f}",
                    f"Modèle : {self.model_type}",
                    f"AUC cross-val : {self._best_auc:.4f}",
                    f"Lookahead : {self.lookahead} bougies",
                    f"Hyperparams optimisés : {self._best_params}",
                ],
                "indicators": feats_dict,
            }
        except Exception as e:
            logger.error(f"[MLStrategy] Erreur prédiction : {e}")
            return self._no_signal()

    def score(self, df: pd.DataFrame, params: dict = None) -> dict:
        """Alias de signal() — interface compatible avec Engine."""
        # Les params peuvent contenir les hyperparamètres ML configurés
        if params:
            ml_p = params.get(self.name, {})
            if ml_p and not self._trained:
                # Appliquer les params configurés si pas encore entraîné
                for k, v in ml_p.items():
                    if hasattr(self, k):
                        setattr(self, k, v)
        return self.signal(df)

    def get_train_info(self) -> dict:
        return {
            "trained":     self._trained,
            "model":       self.model_type,
            "best_auc":    round(self._best_auc, 4),
            "best_params": self._best_params,
            "n_features":  len(self._feature_cols),
            "train_size":  self._train_idx,
        }

    @staticmethod
    def _no_signal() -> dict:
        return {"side": "none", "score": 0.0, "name": "ml_strategy",
                "reason": "Pas de signal ML", "conditions": [], "indicators": {}}


# ═══════════════════════════════════════════════════════════════
#  Optimiseur dédié MLStrategy — Random Search sur params ML
# ═══════════════════════════════════════════════════════════════
ML_STRATEGY_PARAM_SPACE = {
    "lookahead":   [2, 3, 5, 8, 10],
    "threshold":   [0.001, 0.002, 0.003, 0.005, 0.008],
    "proba_long":  [0.55, 0.58, 0.60, 0.62, 0.65],
    "proba_short": [0.30, 0.35, 0.38, 0.40, 0.42, 0.45],
    "model_type":  ["random_forest", "logistic"],
    "n_trials":    [10, 20, 30],
}


def optimize_ml_strategy(df: pd.DataFrame, cfg: dict, n_outer: int = 15) -> dict:
    """
    Optimise les hyperparamètres de MLStrategy par Random Search externe.
    Chaque combinaison est évaluée par backtest OOS.
    """
    from app.engine.engine   import Engine
    from app.engine.backtest import Backtester

    rng     = random.Random(77)
    split   = int(len(df) * 0.7)
    df_is   = df.iloc[:split].copy().reset_index(drop=True)
    df_oos  = df.iloc[split:].copy().reset_index(drop=True)

    best_score, best_params, all_results = -999.0, {}, []

    for trial in range(n_outer):
        params = {k: rng.choice(v) for k, v in ML_STRATEGY_PARAM_SPACE.items()}
        try:
            strat = MLStrategy(**{k: v for k, v in params.items() if k != "model_type"},
                               model_type=params.get("model_type", "random_forest"),
                               min_train=100, retrain_every=9999)
            # Pré-entraîner sur IS
            strat._fit(df_is)
            if not strat._trained:
                continue

            # Backtest OOS
            from app.engine.engine import Engine
            eng = Engine()
            # Monkey-patch : injecte la stratégie ML dans le moteur
            eng._ml_strat = strat
            eng_result = _run_ml_backtest(strat, df_oos, cfg)

            score = _composite_score(eng_result)
            all_results.append({"params": params, "score": score,
                                 "pnl": eng_result.get("total_pnl",0),
                                 "sharpe": eng_result.get("sharpe",0),
                                 "wr": eng_result.get("win_rate",0)})
            if score > best_score:
                best_score, best_params = score, params
                logger.info(f"[MLOptim] Trial {trial+1}: score={score:.4f} pnl={eng_result.get('total_pnl',0):.4f}")
        except Exception as e:
            logger.debug(f"[MLOptim] Trial {trial+1} KO : {e}")

    all_results.sort(key=lambda x: -x["score"])
    return {"best_params": best_params, "best_score": round(best_score,4),
            "all_results": all_results, "n_trials": n_outer}


def _run_ml_backtest(strat: "MLStrategy", df: pd.DataFrame, cfg: dict) -> dict:
    """Mini-backtest rapide pour évaluer une MLStrategy sur OOS."""
    capital   = cfg["trading"].get("capital", 1000.0)
    threshold = cfg["trading"].get("score_threshold", 0.55)
    fee_rate  = cfg["trading"].get("taker_fee", 0.001)
    risk      = cfg["trading"].get("risk_per_trade", 0.01)
    equity    = capital
    trades    = []
    position  = None
    warmup    = max(strat.min_train, 60)

    for i in range(warmup, len(df) - 1):
        window = df.iloc[:i+1].copy()
        if position is not None:
            price = float(df["open"].iloc[i])
            from app.scanner.scanner import _compute_atr
            atr  = _compute_atr(window)
            stop = position["stop"]
            # Trailing stop simplifié
            if position["side"] == "long":
                new_stop = max(stop, price - atr * 1.5)
                triggered = price <= new_stop
            else:
                new_stop = min(stop, price + atr * 1.5)
                triggered = price >= new_stop
            position["stop"] = new_stop
            if triggered:
                fees = price * position["size"] * fee_rate
                gross = (price - position["entry"]) * position["size"] * (1 if position["side"]=="long" else -1)
                pnl   = gross - fees
                equity += pnl
                position.update({"exit": price, "pnl": round(pnl,6), "fees": round(fees,6),
                                 "exit_bar": i, "status": "closed"})
                trades.append(position)
                position = None
            continue

        sig = strat.signal(window)
        if sig["side"] == "none" or sig["score"] < threshold:
            continue
        price  = float(df["open"].iloc[i+1])
        from app.scanner.scanner import _compute_atr
        atr    = _compute_atr(window)
        if atr <= 0: continue
        size   = (equity * risk) / max(atr, 1e-8)
        size   = min(size, equity * 0.2 / price)
        fees   = price * size * fee_rate
        equity -= fees
        stop   = price - atr * 1.5 if sig["side"] == "long" else price + atr * 1.5
        position = {"side": sig["side"], "entry": price, "stop": stop,
                    "size": round(size,6), "bar": i+1, "strategy": "ml_strategy",
                    "score": sig["score"], "reason": sig.get("reason",""),
                    "conditions": sig.get("conditions",[]), "indicators": sig.get("indicators",{}),
                    "pnl": None, "exit": None, "fees": 0, "exit_bar": None}

    if not trades:
        return {"total_trades": 0, "win_rate": 0, "total_pnl": 0, "sharpe": 0}

    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    eq   = [capital]
    c    = capital
    for p in pnls: c += p; eq.append(c)
    eq_a = np.array(eq)
    peak = np.maximum.accumulate(eq_a)
    dd   = (eq_a - peak) / np.where(peak>0, peak, 1)
    rets = np.diff(eq_a) / np.where(eq_a[:-1]>0, eq_a[:-1], 1)
    return {
        "total_trades": len(pnls),
        "win_rate": round(len(wins)/len(pnls)*100, 1),
        "total_pnl": round(sum(pnls), 4),
        "max_drawdown": round(float(dd.min())*100, 2),
        "sharpe": round(float(rets.mean()/rets.std()*np.sqrt(252)), 3) if rets.std()>0 else 0,
    }


def _composite_score(r: dict) -> float:
    if r.get("total_trades", 0) < 3:
        return -999.0
    pf = r.get("sharpe", 0) * 0.5 + r.get("win_rate", 0) / 100 * 0.3 + \
         min(r.get("total_pnl", 0) / 100, 1.0) * 0.2
    return pf


# ── Helpers indicateurs ───────────────────────────────────────
def _rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    h, l, c = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float)
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def _adx_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float)
    tr      = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    plus_dm = (h - h.shift()).clip(lower=0)
    minus_dm= (l.shift() - l).clip(lower=0)
    plus_dm [plus_dm  <= minus_dm] = 0
    minus_dm[minus_dm <= plus_dm ] = 0
    atr14   = tr.rolling(period).mean().replace(0, np.nan)
    dip     = 100 * plus_dm.rolling(period).mean() / atr14
    dim     = 100 * minus_dm.rolling(period).mean() / atr14
    dx      = 100 * (dip - dim).abs() / (dip + dim).replace(0, np.nan)
    return dx.rolling(period).mean().fillna(0)


# Alias pour compatibilité avec le moteur
class Strategy(MLStrategy):
    pass
