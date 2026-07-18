"""Tests — scripts/audit_param_space.py (BT-05/STRAT-03)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from audit_param_space import audit, cardinality, COVERAGE_WARN_THRESHOLD


class TestCardinality:
    def test_product_of_choice_lengths(self):
        assert cardinality({"a": [1, 2, 3], "b": [1, 2]}) == 6

    def test_single_param(self):
        assert cardinality({"a": [1, 2, 3, 4]}) == 4

    def test_empty_space(self):
        assert cardinality({}) == 0


class TestAudit:
    def _cfg(self, n_trials):
        return {"optimizer": {"n_trials": n_trials}}

    def test_flags_low_coverage(self):
        rows = audit(self._cfg(1))
        assert rows, "le registre doit exposer au moins une stratégie"
        # Toute stratégie avec une cardinalité >> 1/1e-4 doit être signalée.
        for r in rows:
            expected_warn = (r["n_trials"] / r["cardinality"]) < COVERAGE_WARN_THRESHOLD
            assert r["warn"] == expected_warn

    def test_sorted_worst_coverage_first(self):
        rows = audit(self._cfg(40))
        coverages = [r["coverage"] for r in rows]
        assert coverages == sorted(coverages)

    def test_high_n_trials_reduces_warnings(self):
        few_trials  = audit(self._cfg(1))
        many_trials = audit(self._cfg(10_000_000))
        n_warn_few  = sum(1 for r in few_trials if r["warn"])
        n_warn_many = sum(1 for r in many_trials if r["warn"])
        assert n_warn_many <= n_warn_few
