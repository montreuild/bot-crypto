"""Shims ARCH-03 : recopie le namespace du sous-paquet (y compris ``_privé``)."""
from __future__ import annotations

import importlib


def reexport(target: str, dest_globals: dict) -> None:
    mod = importlib.import_module(target)
    for key, val in mod.__dict__.items():
        if key in {"__name__", "__file__", "__package__", "__spec__", "__loader__"}:
            continue
        dest_globals[key] = val


def copy_privates(target: str, dest_globals: dict) -> None:
    """Complète un shim ``from X import *`` avec les ``_privés`` (runtime)."""
    mod = importlib.import_module(target)
    for key, val in mod.__dict__.items():
        if key.startswith("_") and key not in dest_globals:
            dest_globals[key] = val
