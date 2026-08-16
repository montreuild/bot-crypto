"""Analyse statistique des motifs SMC/ICT — un symbole, plusieurs timeframes.

Étude de MESURE : ce paquet ne détecte aucun motif et ne modifie aucune
stratégie. Il consomme les détecteurs causaux du dépôt
(``app/core/smc_structure``, ``smc_state``, ``ict``, ``smc_sessions``), les
date, et mesure ce qui les suit.

  journal.py     journal daté à la barre de CONNAISSABILITÉ, contexte HTF
                 aligné sur des buckets entièrement clôturés
  stats.py       fréquence, transitions, rendements — chaque chiffre publié
                 à côté de ses TÉMOINS
  composites.py  motifs composés, découverts et confirmés sur des tranches
                 DISJOINTES

Le point d'entrée opérateur est ``scripts/analyze_smc_patterns.py``.
"""
from app.engine.smc_patterns.journal import (  # noqa: F401
    CONNAISSABILITE,
    FAMILLES_MUTABLES,
    barre_connaissable,
    journal_multi_tf,
    journal_un_tf,
)
