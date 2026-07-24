"""Couche ML — registre de modèles, gate de promotion, scoring, backend.

Point d'accroche unique pour le routage des messages LightGBM vers le
logging Python (``lgb_logging``) : tout chargement/entraînement de modèle du
dépôt passe par un sous-module de ``app.ml`` (``app.ml.backend.persistence``,
``app.ml.policy``, ``app.ml.scoring``…), et Python exécute ce ``__init__``
avant tout sous-module — l'installation est donc garantie sans avoir à la
répéter dans chaque appelant.

Sans effet si lightgbm n'est pas installé (le dépôt reste importable sans).
"""
from app.ml.lgb_logging import install as _install_lgb_logging

_install_lgb_logging()
