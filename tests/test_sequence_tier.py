"""L6 — séquence, tier et déduplication d'événement (§20, §71, §72, §97, §98).

Un même balayage produit plusieurs setups (sweep, retest d'order block, FVG…).
Les traiter comme des signaux indépendants revenait à multiplier le risque sur
un seul événement de marché — ce que §97 interdit explicitement.

Les identifiants sont **déterministes** et non des UUID comme le suggère §72 :
dérivés de la barre d'origine, ils sont reproductibles d'un run à l'autre, et
c'est la reproductibilité qui rend une analyse par séquence exploitable.
"""
import datetime as dt

import numpy as np
import polars as pl
import pytest

from app.engine.backtest import Backtester
from app.engine.engine import BaseStrategy, Engine
from app.strategies.smart_money_signals import FACTEUR_TIER, _tier

# ── §71 tiers ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("sequence,score,attendu", [
    ("CONTINUATION", 0.90, "A"),
    ("CONTINUATION", 0.70, "B"),
    ("REVERSAL", 0.95, "A"),
    ("EARLY_REVERSAL", 0.99, "C"),
    ("FAILED_REVERSAL", 0.60, "B"),
    ("NONE", 0.99, "D"),
])
def test_le_tier_vient_d_abord_de_la_sequence(sequence, score, attendu):
    """Un score élevé sur un retournement non confirmé reste un pari : c'est
    exactement ce que la hiérarchie de tiers sert à dire."""
    assert _tier(sequence, score, {"tier_a_score": 0.85}) == attendu


def test_les_facteurs_de_risque_decroissent_avec_le_tier():
    ordre = [FACTEUR_TIER[t] for t in ("A", "B", "C", "D")]
    assert ordre == sorted(ordre, reverse=True)
    assert FACTEUR_TIER["D"] == 0.0, "un setup non confirmé ne se trade pas"


# ── §97 déduplication d'événement ───────────────────────────────────────────

def _ohlcv(n: int = 400) -> pl.DataFrame:
    start = dt.datetime(2021, 1, 1)
    close = np.array([100.0 * (1.003 ** i) for i in range(n)])
    open_ = np.concatenate([[100.0], close[:-1]])
    return pl.DataFrame({
        "time": [start + dt.timedelta(hours=i) for i in range(n)],
        "open": open_, "high": np.maximum(open_, close) * 1.002,
        "low": np.minimum(open_, close) * 0.998, "close": close,
        "volume": np.full(n, 500.0),
    })


def _cfg(nom: str, dedup: bool = True) -> dict:
    return {
        "trading": {"capital": 10000, "risk_per_trade": 0.01, "timeframe": "1h",
                    "paper_mode": True, "taker_fee": 0.001, "maker_fee": 0.0004,
                    "score_threshold": 0.5, "borrow_rate_daily": 0.0,
                    "dedup_events": dedup},
        "backtest": {"spread_pct": 0.0005, "atr_stop_mult": 2.0, "trail_wide": 2.5,
                     "trail_normal": 2.0, "trail_lock": 1.5, "trail_tight": 1.0,
                     "grace_bars": 4, "breakeven_r": 1.2, "lock_r": 2.5,
                     "tight_r": 4.0, "lock_ratio": 0.6, "use_swing": False,
                     "max_notional_pct": 0.2},
        "strategies": {"enabled": [nom]},
        "strategy_params": {nom: {}},
    }


class _MemeEvenement(BaseStrategy):
    """Émet plusieurs signaux portant le MÊME `market_event_id` — le cas d'un
    balayage qui déclenche sweep, retest d'OB et FVG sur des barres voisines."""

    name = "meme_evenement"

    def min_bars_required(self, params: dict = None) -> int:
        return 60

    def score(self, df, params=None, df_htf=None, symbol: str = "") -> dict:
        n = len(df)
        if n < 100 or n % 20 != 0:
            return {"name": self.name, "side": "none", "score": 0.0}
        return {"name": self.name, "side": "long", "score": 0.9,
                "market_event_id": "long:evenement_unique",
                "exit_after_bars": 4}


class _EvenementsDistincts(_MemeEvenement):
    name = "evenements_distincts"

    def score(self, df, params=None, df_htf=None, symbol: str = "") -> dict:
        sig = super().score(df, params, df_htf, symbol)
        if sig["side"] != "none":
            sig["market_event_id"] = f"long:{len(df)}"
            sig["name"] = self.name
        return sig


def _run(strat, dedup: bool = True):
    eng = Engine()
    eng.register(strat, silent=True)
    return Backtester(eng, _cfg(strat.name, dedup), ml_mode="inline").run(
        _ohlcv(), "BTC/USDC", "1h")


def test_un_evenement_ne_produit_qu_un_trade():
    """§97 — « 1 liquidity event → 1 primary trade »."""
    res = _run(_MemeEvenement())
    assert len(res.trades) == 1, (
        f"{len(res.trades)} trades pour un seul événement de liquidité"
    )


def test_des_evenements_distincts_produisent_plusieurs_trades():
    """Le garde-fou ne doit pas assécher la stratégie : c'est l'identité de
    l'ÉVÉNEMENT qui déduplique, pas le fait d'avoir déjà tradé."""
    res = _run(_EvenementsDistincts())
    assert len(res.trades) > 1


def test_la_deduplication_est_desactivable():
    """Sans elle, on ne pourrait pas mesurer ce qu'elle coûte ou rapporte."""
    avec = len(_run(_MemeEvenement(), dedup=True).trades)
    sans = len(_run(_MemeEvenement(), dedup=False).trades)
    assert sans > avec


def test_les_refus_sont_comptes_sous_un_motif_dedie():
    res = _run(_MemeEvenement())
    motifs = (res.rejections or {}).get("par_motif", {})
    assert motifs.get("evenement_deja_trade", 0) > 0


def test_un_signal_sans_identifiant_n_est_jamais_dedupliqué():
    """Toutes les stratégies du dépôt sont dans ce cas : leur comportement ne
    doit pas changer."""
    class _SansId(_MemeEvenement):
        name = "sans_id"

        def score(self, df, params=None, df_htf=None, symbol: str = "") -> dict:
            sig = super().score(df, params, df_htf, symbol)
            sig.pop("market_event_id", None)
            sig["name"] = self.name
            return sig

    assert len(_run(_SansId()).trades) > 1
