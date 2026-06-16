"""Robustesse : détection d'optimisation en cours (pour différer les tâches de fond).

Le durcissement de la boucle principale (les tâches de maintenance ne peuvent plus
arrêter le bot) est un try/except dans live_trader.start() ; il est couvert par
revue + compilation. Ici on teste le signal utilisé pour différer forward-test /
cycle de vie pendant une optimisation lourde (cause probable d'OOM).
"""
import app.engine.auto_optimizer as ao


def test_any_optimization_running_reflects_registry():
    ao._jobs.clear()
    assert ao.any_optimization_running() is False
    ao._jobs["j1"] = {"status": "running"}
    assert ao.any_optimization_running() is True
    ao._jobs["j1"] = {"status": "queued"}
    assert ao.any_optimization_running() is True
    ao._jobs["j1"] = {"status": "done"}
    ao._jobs["j2"] = {"status": "error"}
    assert ao.any_optimization_running() is False
    ao._jobs.clear()
