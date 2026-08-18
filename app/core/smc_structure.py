"""Shim ARCH-03 — import statique pour que mypy voie les noms."""
from app.core._compat import copy_privates
from app.core.smc.structure import *  # noqa: F401,F403
from app.core.smc.structure import analyze as analyze  # noqa: F401

copy_privates('app.core.smc.structure', globals())
