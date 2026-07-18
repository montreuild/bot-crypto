"""Round-trip YAML : préservation des commentaires lors de la réécriture."""

from app.core.yaml_io import load_yaml, dump_yaml, ruamel_available

_SAMPLE = """\
# En-tête de la stratégie — NE PAS supprimer
enabled: true  # commentaire en ligne
params:
  # seuil de score réglé à la main
  score_threshold: 0.6
  period: 14
optimizer_results: {}
"""


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def test_load_mutate_dump_preserves_comments(tmp_path):
    p = tmp_path / "strat.yaml"
    _write(p, _SAMPLE)

    data = load_yaml(str(p))
    # Mutation type « après optimisation » : on ajoute un résultat sans toucher
    # au reste.
    data.setdefault("optimizer_results", {})["1h"] = {
        "run_date": "2026-06-20", "oos_score": 0.42, "params": {"period": 20},
    }
    dump_yaml(str(p), data)

    out = p.read_text(encoding="utf-8")
    # Les nouvelles données sont bien écrites.
    assert "oos_score: 0.42" in out
    assert "run_date: '2026-06-20'" in out or "run_date: 2026-06-20" in out
    # Et les valeurs existantes restent là.
    assert "score_threshold: 0.6" in out
    assert "period: 14" in out

    if ruamel_available():
        # Les commentaires existants doivent survivre.
        assert "# En-tête de la stratégie" in out
        assert "# commentaire en ligne" in out
        assert "# seuil de score réglé à la main" in out


def test_load_missing_returns_default(tmp_path):
    assert load_yaml(str(tmp_path / "absent.yaml"), default={"x": 1}) == {"x": 1}


def test_strategy_file_helpers_preserve_comments(tmp_path):
    """Le chemin réel optimiseur (_load/_write_strategy_file) préserve aussi."""
    from app.engine.opt_persistence import _load_strategy_file, _write_strategy_file
    p = tmp_path / "mystrat.yaml"
    _write(p, _SAMPLE)
    data = _load_strategy_file(str(p))
    data.setdefault("optimizer_results", {})["4h"] = {"oos_score": 1.23}
    _write_strategy_file(str(p), data)
    out = p.read_text(encoding="utf-8")
    assert "oos_score: 1.23" in out
    if ruamel_available():
        assert "# En-tête de la stratégie" in out
