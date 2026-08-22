"""BT-03 — le mode ML du walk-forward vient de la même source que l'optimiseur.

`cfg["optimizer"]["ml_mode"]` était lu par l'optimiseur et ses workers, mais le
walk-forward forçait `"frozen"` : la validation ne tournait pas sous le mode
qui avait servi à la recherche, et la surcharge n'y arrivait jamais.
"""
import pytest

from app.core.is_oos import resolve_ml_mode
from app.engine.engine import Engine
from app.engine.walk_forward import WalkForwardAnalyzer


def test_resolveur_lit_la_cle_optimizer():
    assert resolve_ml_mode({"optimizer": {"ml_mode": "frozen"}}) == "frozen"
    assert resolve_ml_mode({"optimizer": {"ml_mode": "inline"}}) == "inline"


def test_defaut_inline_sans_config():
    assert resolve_ml_mode(None) == "inline"
    assert resolve_ml_mode({}) == "inline"
    assert resolve_ml_mode({"optimizer": {}}) == "inline"


def test_explicite_prime_sur_la_config():
    cfg = {"optimizer": {"ml_mode": "inline"}}
    assert resolve_ml_mode(cfg, "frozen") == "frozen"


def test_walk_forward_honore_la_surcharge():
    """Le forçage à `"frozen"` rendait cette clé sans effet côté validation."""
    cfg = {"optimizer": {"ml_mode": "frozen"}}
    assert WalkForwardAnalyzer(Engine(), cfg).ml_mode == "frozen"
    cfg = {"optimizer": {"ml_mode": "inline"}}
    assert WalkForwardAnalyzer(Engine(), cfg).ml_mode == "inline"


@pytest.mark.parametrize("source", ["optimizer_search", "opt_workers", "walk_forward"])
def test_une_seule_lecture_de_la_cle(source):
    """La clé ne doit plus être lue en dur ailleurs que dans le résolveur."""
    from pathlib import Path
    texte = Path(f"app/engine/{source}.py").read_text(encoding="utf-8")
    assert '"ml_mode"' not in texte, (
        f"{source}.py relit cfg['optimizer']['ml_mode'] au lieu de passer par "
        f"resolve_ml_mode — c'est ainsi que le walk-forward avait divergé."
    )
