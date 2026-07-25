"""app.ml.model_registry — layout daté par (symbole, TF, recette), resolve(as_of/pin),
repli sur l'ancien layout plat, garde anti-chevauchement (ML-02 §3.2)."""
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


def test_overlaps_detects_training_window_intersection():
    art = registry.ArtifactRef(
        path_prefix="x", symbol="BTC/USDC", tf="1h", recipe="r", version_id="v1",
        train_start="2026-01-01T00:00:00", train_end="2026-03-01T00:00:00",
    )
    assert registry.overlaps(art, "2026-02-01T00:00:00", "2026-04-01T00:00:00") is True
    assert registry.overlaps(art, "2026-04-01T00:00:00", "2026-05-01T00:00:00") is False


def test_overlaps_unknown_dates_returns_false_not_a_safety_claim():
    undated = registry.ArtifactRef(
        path_prefix="x", symbol=None, tf="1h", recipe="r", version_id="undated-abc",
        train_start=None, train_end=None,
    )
    assert registry.overlaps(undated, "2026-01-01T00:00:00", "2026-02-01T00:00:00") is False


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


# ─────────────────────────────────────────────────────────────────────────────
#  Pin persistant (set_pin/get_pin/clear_pin)
# ─────────────────────────────────────────────────────────────────────────────
def test_get_pin_none_when_not_set(tmp_path):
    base = str(tmp_path / "models")
    assert registry.get_pin("BTC/USDC", "1h", "r", base_dir=base) is None


def test_set_pin_then_resolve_prefers_pinned_over_newer(tmp_path):
    base = str(tmp_path / "models")
    old_src = _write_tmp_bundle(tmp_path, "old", best_auc=0.55)
    old = registry.publish("BTC/USDC", "1h", "r", old_src, train_end="2026-01-01T00:00:00",
                           decision="promote", base_dir=base)
    new_src = _write_tmp_bundle(tmp_path, "new", best_auc=0.70)
    registry.publish("BTC/USDC", "1h", "r", new_src, train_end="2026-03-01T00:00:00",
                     decision="promote", base_dir=base)

    # Sans pin : la plus récente gagne.
    assert registry.resolve("BTC/USDC", "1h", "r", base_dir=base).auc == pytest.approx(0.70)

    ok = registry.set_pin("BTC/USDC", "1h", "r", old.version_id, base_dir=base)
    assert ok is True
    assert registry.get_pin("BTC/USDC", "1h", "r", base_dir=base) == old.version_id

    pinned = registry.resolve("BTC/USDC", "1h", "r", base_dir=base)
    assert pinned.version_id == old.version_id
    assert pinned.auc == pytest.approx(0.55)


def test_set_pin_can_override_a_rejected_version(tmp_path):
    """Un pin peut ramener en service un candidat rejeté (decision='keep') —
    override humain assumé, distinct de l'éligibilité automatique du gate."""
    base = str(tmp_path / "models")
    src = _write_tmp_bundle(tmp_path, "rejected", best_auc=0.40)
    art = registry.publish("BTC/USDC", "1h", "r", src, train_end="2026-01-01T00:00:00",
                           decision="keep", base_dir=base)
    assert registry.resolve("BTC/USDC", "1h", "r", base_dir=base) is None

    registry.set_pin("BTC/USDC", "1h", "r", art.version_id, base_dir=base)
    resolved = registry.resolve("BTC/USDC", "1h", "r", base_dir=base)
    assert resolved is not None
    assert resolved.version_id == art.version_id


def test_clear_pin_restores_default_resolution(tmp_path):
    base = str(tmp_path / "models")
    src = _write_tmp_bundle(tmp_path, "v1", best_auc=0.60)
    art = registry.publish("BTC/USDC", "1h", "r", src, train_end="2026-01-01T00:00:00",
                           decision="promote", base_dir=base)
    registry.set_pin("BTC/USDC", "1h", "r", art.version_id, base_dir=base)
    registry.clear_pin("BTC/USDC", "1h", "r", base_dir=base)
    assert registry.get_pin("BTC/USDC", "1h", "r", base_dir=base) is None
    # Toujours résolu normalement (repli sur la dernière éligible).
    assert registry.resolve("BTC/USDC", "1h", "r", base_dir=base).version_id == art.version_id


def test_clear_pin_is_noop_when_absent(tmp_path):
    base = str(tmp_path / "models")
    registry.clear_pin("BTC/USDC", "1h", "r", base_dir=base)  # ne lève pas


def test_set_pin_unknown_version_returns_false(tmp_path):
    base = str(tmp_path / "models")
    assert registry.set_pin("BTC/USDC", "1h", "r", "does-not-exist", base_dir=base) is False


def test_pin_does_not_leak_into_historical_as_of_resolution(tmp_path):
    """Un pin reflète l'état courant de production, jamais une vérité
    historique — resolve(as_of=...) doit l'ignorer, sinon un backtest
    "frozen" verrait un modèle différent de ce qui existait réellement à
    cette date (fuite via le pin, contournant tout le travail anti-fuite)."""
    base = str(tmp_path / "models")
    old_src = _write_tmp_bundle(tmp_path, "old", best_auc=0.55)
    old = registry.publish("BTC/USDC", "1h", "r", old_src, train_end="2026-01-01T00:00:00",
                           decision="promote", base_dir=base)
    new_src = _write_tmp_bundle(tmp_path, "new", best_auc=0.70)
    new = registry.publish("BTC/USDC", "1h", "r", new_src, train_end="2026-03-01T00:00:00",
                           decision="promote", base_dir=base)

    registry.set_pin("BTC/USDC", "1h", "r", new.version_id, base_dir=base)

    # as_of antérieur au pin : doit résoudre "old" (seule version existante
    # à cette date), PAS le pin (qui pointe sur une version future à as_of).
    historical = registry.resolve("BTC/USDC", "1h", "r", as_of="2026-02-01T00:00:00", base_dir=base)
    assert historical.version_id == old.version_id


# ─────────────────────────────────────────────────────────────────────────────
#  set_decision — promotion/rejet manuel d'une version déjà publiée
# ─────────────────────────────────────────────────────────────────────────────
def test_set_decision_promotes_a_rejected_candidate(tmp_path):
    base = str(tmp_path / "models")
    src = _write_tmp_bundle(tmp_path, "rejected", best_auc=0.40)
    art = registry.publish("BTC/USDC", "1h", "r", src, train_end="2026-01-01T00:00:00",
                           decision="keep", base_dir=base)
    assert registry.resolve("BTC/USDC", "1h", "r", base_dir=base) is None

    ok = registry.set_decision("BTC/USDC", "1h", "r", art.version_id, "manual",
                              reason="revue humaine : régression jugée mineure", base_dir=base)
    assert ok is True

    resolved = registry.resolve("BTC/USDC", "1h", "r", base_dir=base)
    assert resolved is not None
    assert resolved.version_id == art.version_id
    assert resolved.gate_decision == "manual"


def test_set_decision_appends_manual_source_to_decisions_log(tmp_path):
    base = str(tmp_path / "models")
    src = _write_tmp_bundle(tmp_path, "v1", best_auc=0.60)
    art = registry.publish("BTC/USDC", "1h", "r", src, train_end="2026-01-01T00:00:00",
                           decision="promote", base_dir=base)
    registry.set_decision("BTC/USDC", "1h", "r", art.version_id, "keep",
                          reason="dépromotion manuelle", base_dir=base)

    decisions = registry.read_decisions("BTC/USDC", "1h", "r", base_dir=base)
    assert decisions[-1]["source"] == "manual"
    assert decisions[-1]["decision"] == "keep"
    assert decisions[-1]["previous_decision"] == "promote"


def test_set_decision_unknown_version_returns_false(tmp_path):
    base = str(tmp_path / "models")
    assert registry.set_decision("BTC/USDC", "1h", "r", "nope", "manual", base_dir=base) is False


# ─────────────────────────────────────────────────────────────────────────────
#  list_recipes — énumération pour la vue d'ensemble de l'UI
# ─────────────────────────────────────────────────────────────────────────────
def test_list_recipes_empty_base_dir_returns_empty(tmp_path):
    assert registry.list_recipes(base_dir=str(tmp_path / "models")) == []


def test_list_recipes_finds_new_layout_entries(tmp_path):
    base = str(tmp_path / "models")
    src1 = _write_tmp_bundle(tmp_path, "a", best_auc=0.6)
    registry.publish("BTC/USDC", "1h", "opus_omnibus_v11", src1,
                     train_end="2026-01-01T00:00:00", decision="promote", base_dir=base)
    src2 = _write_tmp_bundle(tmp_path, "b", best_auc=0.6)
    registry.publish("ETH/USDC", "4h", "opus_omnibus_v7", src2,
                     train_end="2026-01-01T00:00:00", decision="promote", base_dir=base)

    recipes = registry.list_recipes(base_dir=base)
    keys = {(r["symbol"], r["tf"], r["recipe"]) for r in recipes}
    assert ("BTC/USDC", "1h", "opus_omnibus_v11") in keys
    assert ("ETH/USDC", "4h", "opus_omnibus_v7") in keys
    for r in recipes:
        assert len(r["versions"]) >= 1


def test_list_recipes_includes_rejected_only_recipes(tmp_path):
    """Un recipe dont TOUTES les versions sont rejetées doit rester visible
    dans l'énumération (l'UI doit pouvoir montrer "aucune version active"),
    pas disparaître silencieusement."""
    base = str(tmp_path / "models")
    src = _write_tmp_bundle(tmp_path, "rejected", best_auc=0.3)
    registry.publish("BTC/USDC", "1h", "r", src, train_end="2026-01-01T00:00:00",
                     decision="keep", base_dir=base)

    recipes = registry.list_recipes(base_dir=base)
    assert len(recipes) == 1
    assert recipes[0]["versions"][0].gate_decision == "keep"


# ─────────────────────────────────────────────────────────────────────────────
#  Fin du chemin legacy (docs/CONCEPTION_ARCHITECTURE_ML_UNIFIEE.md §3.1)
# ─────────────────────────────────────────────────────────────────────────────
def test_resolve_returns_none_instead_of_falling_back_to_flat_layout(tmp_path):
    """Un fichier à l'ancien layout plat ``{base}/{recipe}_{tf}.*`` ne doit
    PLUS être servi. Le repli existait pour la migration V4 ; le garder
    reviendrait à charger un artefact sans provenance datée — donc non gatable
    et non comparable — au lieu de dire honnêtement « aucun modèle »."""
    base = str(tmp_path / "models")
    os.makedirs(base, exist_ok=True)
    flat_prefix = os.path.join(base, "opus_omnibus_v11_1h")
    assert save_amp_dir_bundle(flat_prefix, "1h", _train_tiny_booster(3),
                               _train_tiny_booster(4), ["f0"], {}, 0.58, {})

    assert registry.resolve("BTC/USDC", "1h", "opus_omnibus_v11", base_dir=base) is None
    assert registry.list_recipes(base_dir=base) == []


def test_archive_dir_is_never_enumerated(tmp_path):
    """Les artefacts mis hors service vivent sous ``models/_archive/`` : ils
    restent lisibles pour ré-examen mais ne doivent jamais réapparaître dans
    l'UI comme s'ils servaient encore."""
    base = str(tmp_path / "models")
    src = _write_tmp_bundle(tmp_path, "vivant", best_auc=0.7)
    registry.publish("BTC/USDC", "1h", "r", src, train_end="2026-01-01T00:00:00",
                     decision="promote", base_dir=base)
    # Même arborescence, mais sous _archive/.
    archived = _write_tmp_bundle(tmp_path, "archive", best_auc=0.9)
    registry.publish("BTC/USDC", "1h", "r_mort", archived,
                     train_end="2026-01-01T00:00:00", decision="promote",
                     base_dir=os.path.join(base, registry._ARCHIVE_DIRNAME))

    recipes = registry.list_recipes(base_dir=base)
    assert [r["recipe"] for r in recipes] == ["r"]
