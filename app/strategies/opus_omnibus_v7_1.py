"""V7.1 = V7 + score additif (compatible AUC inline 0.52-0.65).

V7 entraîne ses modèles LightGBM inline (cf. ``_train()`` dans opus_omnibus_v7).
Le fix des callbacks LightGBM (early_stopping stateful réutilisé entre amp et
dir → modèle direction dégénéré) est appliqué directement dans
``opus_omnibus_v7.py`` et v7.1 en bénéficie via l'héritage.

Voir opus_omnibus_v8_1 pour la motivation de la formule de score additive.
"""

from app.strategies.opus_omnibus_v7 import Strategy as _V7Strategy


class Strategy(_V7Strategy):
    name = "opus_omnibus_v7_1"

    def score(self, df, params=None, df_htf=None, symbol=""):
        p = (params or {})
        if self.name not in p and "opus_omnibus_v7" in p:
            params = {**p, self.name: p["opus_omnibus_v7"]}
        sig = super().score(df, params=params, df_htf=df_htf, symbol=symbol)
        if not sig or sig.get("side") == "none":
            return sig
        p_up    = float(sig.get("p_up", 0.5))
        p_event = float(sig.get("p_event", 0.5))
        dir_dist = abs(p_up - 0.5)
        score = round(min(0.50 + dir_dist * 3.0 + (p_event - 0.5) * 0.50, 0.94), 3)
        sig["score"] = score
        sig["name"]  = self.name
        return sig
