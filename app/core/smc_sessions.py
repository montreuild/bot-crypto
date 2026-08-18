"""Shim ARCH-03 — import statique pour que mypy voie les noms."""
from app.core._compat import copy_privates
from app.core.smc.sessions import *  # noqa: F401,F403
from app.core.smc.sessions import _htf_buckets as _htf_buckets  # noqa: F401
from app.core.smc.sessions import killzone_flags as killzone_flags  # noqa: F401
from app.core.smc.sessions import session_label as session_label  # noqa: F401

copy_privates('app.core.smc.sessions', globals())
