"""Shim ARCH-03 — import statique pour que mypy voie les noms."""
from app.core._compat import copy_privates
from app.core.risk.sizer import *  # noqa: F401,F403
from app.core.risk.sizer import RiskSizer as RiskSizer  # noqa: F401
from app.core.risk.sizer import _floor_to as _floor_to  # noqa: F401

copy_privates('app.core.risk.sizer', globals())
