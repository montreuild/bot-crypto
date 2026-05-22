"""V9.1 = V9 + score additif. Voir opus_omnibus_v8_1 pour la motivation."""

from app.strategies.opus_omnibus_v9 import Strategy as _V9Strategy


class Strategy(_V9Strategy):
    name = "opus_omnibus_v9_1"

    def score(self, df, params=None, df_htf=None, symbol=""):
        p = (params or {})
        if self.name not in p and "opus_omnibus_v9" in p:
            params = {**p, self.name: p["opus_omnibus_v9"]}
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
