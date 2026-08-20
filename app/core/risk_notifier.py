"""Shim ARCH-03 — import statique pour que mypy voie les noms."""
from app.core._compat import copy_privates
from app.core.risk.notifier import *  # noqa: F401,F403

copy_privates('app.core.risk.notifier', globals())
