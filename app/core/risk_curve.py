"""Shim ARCH-03 — import statique pour que mypy voie les noms."""
from app.core._compat import copy_privates
from app.core.risk.curve import *  # noqa: F401,F403
from app.core.risk.curve import risk_multiplier as risk_multiplier  # noqa: F401

copy_privates('app.core.risk.curve', globals())
