"""S12 — RejectionCounter : mêmes codes de refus, live et backtest.

Cf. docs/CONCEPTION_ENVELOPPES_DE_RISQUE.md §4.3, §5.1 : sans compteur
partage, impossible de dire pourquoi un backtest et son equivalent live
divergent -- le motif de refus EST l'explication de l'ecart.
"""
from app.core.rejections import REASONS, RejectionCounter


class TestRecordAndAggregate:
    def test_total_counts_every_record(self):
        c = RejectionCounter()
        c.record("budget_symbole")
        c.record("budget_venue")
        assert c.as_dict()["total"] == 2

    def test_aggregates_by_reason(self):
        c = RejectionCounter()
        c.record("budget_symbole")
        c.record("budget_symbole")
        c.record("budget_venue")
        d = c.as_dict()
        assert d["par_motif"] == {"budget_symbole": 2, "budget_venue": 1}

    def test_aggregates_by_slot_and_symbol(self):
        c = RejectionCounter()
        c.record("enveloppe_slot", venue="okx-margin", symbol="BTC/USDC",
                slot_key="a::1h::BTC/USDC")
        c.record("enveloppe_slot", venue="okx-margin", symbol="BTC/USDC",
                slot_key="a::1h::BTC/USDC")
        d = c.as_dict()
        assert d["par_slot"] == {"a::1h::BTC/USDC": 2}
        assert d["par_symbole"] == {"BTC/USDC": 2}

    def test_missing_slot_or_symbol_is_not_counted(self):
        """Un refus en amont de la resolution de slot (ex. venue inconnue)
        n'a pas forcement de slot_key/symbol -- ne doit pas polluer les
        agregats avec une cle vide."""
        c = RejectionCounter()
        c.record("stop_invalide")
        d = c.as_dict()
        assert d["par_slot"] == {}
        assert d["par_symbole"] == {}
        assert d["total"] == 1

    def test_all_spec_reasons_are_recordable(self):
        c = RejectionCounter()
        for r in REASONS:
            c.record(r)
        assert c.as_dict()["total"] == len(REASONS)
        assert set(c.as_dict()["par_motif"]) == set(REASONS)

    def test_reset_clears_everything(self):
        c = RejectionCounter()
        c.record("budget_symbole", symbol="BTC/USDC", slot_key="a::1h::BTC/USDC")
        c.reset()
        d = c.as_dict()
        assert d == {"total": 0, "par_motif": {}, "par_slot": {}, "par_symbole": {}}


class TestSharedAcrossLiveAndBacktest:
    def test_backtest_and_live_use_the_same_reason_codes(self):
        """Verrou de non-regression : les deux chemins doivent parler le meme
        vocabulaire pour que /api/risk et BacktestResult.rejections soient
        comparables terme a terme."""
        live = RejectionCounter()
        backtest = RejectionCounter()
        live.record("budget_symbole")
        backtest.record("budget_symbole")
        assert live.as_dict()["par_motif"] == backtest.as_dict()["par_motif"]
