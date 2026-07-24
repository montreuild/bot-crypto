"""app.ml.model_registry — layout daté par (symbole, TF, recette), resolve(as_of/pin),
repli sur l'ancien layout plat, garde anti-chevauchement (ML-02 §3.2)."""
import json
import os

import lightgbm as lgb
import numpy as np
import pytest

import app.ml.model_registry as registry
from app.ml.backend.persistence import save_amp_dir_bundle


def _train_tiny_booster(seed: int) -> lgb.Booster:
    rng = np.random.RandomState(seed)
    X = rng.rand(200, 3).astype(np.float32)
    y = (X[:, 0] > 0.5).astype(np.int32)
    ds = lgb.Dataset(X, label=y, free_raw_data=False)
    return lgb.train({"objective": "binary", "verbosity": -1, "num_leaves": 4},
                     ds, num_boost_round=3)


def _write_tmp_bundle(tmp_path, tag: str, best_auc: float = 0.6) -> str:
    """Écrit un bundle amp+dir minimal via save_amp_dir_bundle (chemin
    d'entrée réel de publish() : les artefacts existent déjà sur disque,
    comme après un strategy.save_model(tmp_prefix))."""
    prefix = str(tmp_path / f"tmp_{tag}")
    amp = _train_tiny_booster(1)
    dir_ = _train_tiny_booster(2)
    ok = save_amp_dir_bundle(
        prefix, "1h", amp, dir_, features=["f0", "f1", "f2"],
        medians={"f0": 0.5}, best_auc=best_auc, train_meta={"n_train": 160},
    )
    assert ok
    return prefix


def test_publish_then_resolve_roundtrip(tmp_path):
    base = str(tmp_path / "models")
    src = _write_tmp_bundle(tmp_path, "v1", best_auc=0.62)

    art = registry.publish(
        "BTC/USDC", "1h", "opus_omnibus_v11", src,
        train_start="2026-01-01T00:00:00", train_end="2026-02-01T00:00:00",
        n_bars=5000, recipe_cfg={"amp_top_pct": 0.3}, source="live",
        decision="promote", base_dir=base,
    )
    assert art is not None
    assert os.path.exists(f"{art.path_prefix}.amp.lgb")
    assert os.path.exists(f"{art.path_prefix}.dir.lgb")
    assert os.path.exists(f"{art.path_prefix}.meta.json")
    # Les fichiers tmp source ont été déplacés (pas copiés) par défaut.
    assert not os.path.exists(f"{src}.amp.lgb")

    resolved = registry.resolve("BTC/USDC", "1h", "opus_omnibus_v11", base_dir=base)
    assert resolved is not None
    assert resolved.version_id == art.version_id
    assert resolved.auc == pytest.approx(0.62)
    assert resolved.train_end == "2026-02-01T00:00:00"
    assert resolved.gate_decision == "promote"


def test_resolve_as_of_excludes_future_versions(tmp_path):
    base = str(tmp_path / "models")
    old_src = _write_tmp_bundle(tmp_path, "old", best_auc=0.55)
    registry.publish("ETH/USDC", "1h", "reco", old_src,
                     train_start="2026-01-01T00:00:00", train_end="2026-02-01T00:00:00",
                     decision="promote", base_dir=base)
    new_src = _write_tmp_bundle(tmp_path, "new", best_auc=0.70)
    registry.publish("ETH/USDC", "1h", "reco", new_src,
                     train_start="2026-02-01T00:00:00", train_end="2026-06-01T00:00:00",
                     decision="promote", base_dir=base)

    # as_of antérieur à la 2e version -> doit résoudre la 1ère (0.55), pas la
    # plus récente (0.70). C'est ce qui empêche la fuite temporelle en backtest.
    art = registry.resolve("ETH/USDC", "1h", "reco", as_of="2026-03-01T00:00:00", base_dir=base)
    assert art is not None
    assert art.auc == pytest.approx(0.55)

    art_latest = registry.resolve("ETH/USDC", "1h", "reco", base_dir=base)
    assert art_latest.auc == pytest.approx(0.70)


def test_rejected_candidate_not_resolved_but_kept_on_disk(tmp_path):
    base = str(tmp_path / "models")
    src = _write_tmp_bundle(tmp_path, "rejected", best_auc=0.40)
    art = registry.publish("BTC/USDC", "1h", "opus_omnibus_v11", src,
                           train_end="2026-03-01T00:00:00",
                           decision="keep", decision_metrics={"reason": "below_floor"},
                           base_dir=base)
    assert art is not None  # publié quand même (audit trail)
    assert os.path.exists(f"{art.path_prefix}.meta.json")

    resolved = registry.resolve("BTC/USDC", "1h", "opus_omnibus_v11", base_dir=base)
    assert resolved is None  # mais jamais résolu

    decisions = registry.read_decisions("BTC/USDC", "1h", "opus_omnibus_v11", base_dir=base)
    assert len(decisions) == 1
    assert decisions[0]["decision"] == "keep"
    assert decisions[0]["reason"] == "below_floor"


def test_pin_bypasses_gate_decision_and_as_of(tmp_path):
    base = str(tmp_path / "models")
    src = _write_tmp_bundle(tmp_path, "pinned", best_auc=0.40)
    art = registry.publish("BTC/USDC", "1h", "opus_omnibus_v11", src,
                           train_end="2026-05-01T00:00:00", decision="keep", base_dir=base)
    pinned = registry.resolve("BTC/USDC", "1h", "opus_omnibus_v11",
                              pin=art.version_id, base_dir=base)
    assert pinned is not None
    assert pinned.version_id == art.version_id

    assert registry.resolve("BTC/USDC", "1h", "opus_omnibus_v11",
                            pin="does-not-exist", base_dir=base) is None


def test_resolve_falls_back_to_legacy_flat_layout(tmp_path):
    base = str(tmp_path / "models")
    os.makedirs(base, exist_ok=True)
    legacy_prefix = os.path.join(base, "opus_omnibus_v11_1h")
    amp = _train_tiny_booster(3)
    dir_ = _train_tiny_booster(4)
    assert save_amp_dir_bundle(legacy_prefix, "1h", amp, dir_, ["f0"], {}, 0.58, {})

    # Aucune version dans le nouveau layout -> repli sur l'ancien chemin plat,
    # qui n'a pas de dimension symbole.
    art = registry.resolve("BTC/USDC", "1h", "opus_omnibus_v11", base_dir=base)
    assert art is not None
    assert art.legacy is True
    assert art.path_prefix == legacy_prefix
    assert art.auc == pytest.approx(0.58)


def test_import_legacy_is_idempotent(tmp_path):
    base = str(tmp_path / "models")
    os.makedirs(base, exist_ok=True)
    legacy_prefix = os.path.join(base, "opus_stat_pretrained_v4_1h")
    amp = _train_tiny_booster(5)
    dir_ = _train_tiny_booster(6)
    assert save_amp_dir_bundle(legacy_prefix, "1h", amp, dir_, ["f0"], {}, 0.76, {})

    art1 = registry.import_legacy("BTC/USDC", "1h", "opus_stat_pretrained_v4",
                                  legacy_prefix, base_dir=base)
    assert art1 is not None
    assert art1.meta["provenance"]["non_reproducible"] is True
    # L'original reste lisible (copie, pas déplacement) — repli toujours possible.
    assert os.path.exists(f"{legacy_prefix}.amp.lgb")

    art2 = registry.import_legacy("BTC/USDC", "1h", "opus_stat_pretrained_v4",
                                  legacy_prefix, base_dir=base)
    assert art2.version_id == art1.version_id  # no-op, pas de doublon

    resolved = registry.resolve("BTC/USDC", "1h", "opus_stat_pretrained_v4", base_dir=base)
    assert resolved is not None
    assert resolved.legacy is False  # servi depuis le nouveau layout désormais
    assert resolved.auc == pytest.approx(0.76)


def test_overlaps_detects_training_window_intersection():
    art = registry.ArtifactRef(
        path_prefix="x", symbol="BTC/USDC", tf="1h", recipe="r", version_id="v1",
        train_start="2026-01-01T00:00:00", train_end="2026-03-01T00:00:00",
    )
    assert registry.overlaps(art, "2026-02-01T00:00:00", "2026-04-01T00:00:00") is True
    assert registry.overlaps(art, "2026-04-01T00:00:00", "2026-05-01T00:00:00") is False


def test_overlaps_unknown_dates_returns_false_not_a_safety_claim():
    legacy_art = registry.ArtifactRef(
        path_prefix="x", symbol=None, tf="1h", recipe="r", version_id="legacy-flat",
        train_start=None, train_end=None, legacy=True,
    )
    assert registry.overlaps(legacy_art, "2026-01-01T00:00:00", "2026-02-01T00:00:00") is False


def test_list_versions_sorted_oldest_first(tmp_path):
    base = str(tmp_path / "models")
    for tag, end in (("a", "2026-03-01T00:00:00"), ("b", "2026-01-01T00:00:00"),
                     ("c", "2026-02-01T00:00:00")):
        src = _write_tmp_bundle(tmp_path, tag)
        registry.publish("BTC/USDC", "1h", "r", src, train_end=end,
                         decision="promote", base_dir=base)
    versions = registry.list_versions("BTC/USDC", "1h", "r", base_dir=base)
    assert [v.train_end for v in versions] == [
        "2026-01-01T00:00:00", "2026-02-01T00:00:00", "2026-03-01T00:00:00",
    ]


def test_publish_missing_source_files_returns_none(tmp_path):
    base = str(tmp_path / "models")
    art = registry.publish("BTC/USDC", "1h", "r", str(tmp_path / "does_not_exist"),
                           base_dir=base)
    assert art is None
