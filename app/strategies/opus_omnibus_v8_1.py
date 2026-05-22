"""V8.1 = V8 + score additif (compatible AUC inline 0.52-0.65).

L'ancienne formule multiplicative ``0.55 + p_event * confidence * 0.30`` avec
``confidence = abs(p_up - 0.5) * 2`` exigeait des AUC_dir très élevées (≥0.87)
pour produire des scores ≥ threshold. Or les modèles V4 inline plafonnent
autour de AUC_dir 0.52-0.65 — résultat : 0 trades dans la plupart des
backtests d'optimisation, score toujours -999.

Formule additive (inspirée V3) :
    dir_dist = abs(p_up - 0.5)        # 0 à 0.5
    score    = 0.50 + dir_dist*3.0 + (p_event-0.5)*0.50

À p_up=0.55 (dir_dist=0.05), p_event=0.55 : score = 0.50+0.15+0.025 = 0.675 ✓
À p_up=0.50 (aucun edge), p_event=0.60     : score = 0.50+0   +0.05  = 0.55  ✓
À p_up=0.65, p_event=0.65                  : score = 0.50+0.45+0.075 = 0.94 (cap)

Comportement V8 inchangé par ailleurs : mêmes setups, mêmes seuils, même
SIGNAL_UP ×1.5, mêmes modèles V4 pré-entraînés.
"""

from app.strategies.opus_omnibus_v8 import Strategy as _V8Strategy


class Strategy(_V8Strategy):
    name = "opus_omnibus_v8_1"

    def score(self, df, params=None, df_htf=None, symbol=""):
        # Aliasing : permet de lire la config sous le nom v8 sans dupliquer
        # le YAML. Le sous-dict opus_omnibus_v8 est cherché en fallback si
        # opus_omnibus_v8_1 n'est pas défini.
        p = (params or {})
        if self.name not in p and "opus_omnibus_v8" in p:
            params = {**p, self.name: p["opus_omnibus_v8"]}
        sig = super().score(df, params=params, df_htf=df_htf, symbol=symbol)
        if not sig or sig.get("side") == "none":
            return sig
        # Recalcul du score selon la formule additive V3-style
        p_up    = float(sig.get("p_up", 0.5))
        p_event = float(sig.get("p_event", 0.5))
        dir_dist = abs(p_up - 0.5)
        score = round(min(0.50 + dir_dist * 3.0 + (p_event - 0.5) * 0.50, 0.94), 3)
        sig["score"] = score
        sig["name"]  = self.name
        return sig
