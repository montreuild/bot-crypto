"""Shim ARCH-03 — import statique pour que mypy voie les noms."""
from app.core._compat import copy_privates
from app.core.smc.state import *  # noqa: F401,F403

copy_privates('app.core.smc.state', globals())
