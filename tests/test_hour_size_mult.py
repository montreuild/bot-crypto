"""Multiplicateur de taille par heure UTC (_HOUR_SIZE_MULT) — opus_stat_v4.

Dérivé de la heatmap jour×heure de l'analyse V4 recouvrée (50k bougies 15m) :
la fenêtre 13h-17h UTC concentre le taux d'événements, avec un pic net à 14h.
Le filtre horaire existant (enable_hour_filter) coupe déjà tout hors [13h,20h] ;
ce multiplicateur ajoute une pondération continue à L'INTÉRIEUR (et au-delà,
si le filtre est désactivé) de cette fenêtre plutôt qu'un plateau plat.
"""
import app.strategies.opus_stat_pretrained_v4 as pretrained
import app.strategies.opus_stat_retrained_v4 as retrained


def test_peak_hour_gets_full_multiplier():
    # 14h UTC = lift maximal mesuré (×2.43) -> mult exactement 1.0.
    assert pretrained._HOUR_SIZE_MULT[14] == 1.0
    assert retrained._HOUR_SIZE_MULT[14] == 1.0


def test_all_24_hours_covered_with_floor_respected():
    for mod in (pretrained, retrained):
        assert set(mod._HOUR_SIZE_MULT.keys()) == set(range(24))
        for h, mult in mod._HOUR_SIZE_MULT.items():
            assert 0.20 <= mult <= 1.0, f"hour {h} mult={mult} hors [0.20, 1.0]"


def test_us_session_hours_score_higher_than_overnight():
    # 13h-17h UTC (session US, cf. rapport) doit dominer 0h-6h (nuit EU/Asie).
    for mod in (pretrained, retrained):
        us_session = [mod._HOUR_SIZE_MULT[h] for h in (13, 14, 15, 16, 17)]
        overnight  = [mod._HOUR_SIZE_MULT[h] for h in (0, 1, 2, 3, 4, 5, 6)]
        assert min(us_session) >= max(overnight)


def test_pretrained_and_retrained_use_identical_table():
    # Même heatmap source -> même table, dans les deux stratégies (fichiers
    # auto-portants par convention, cf. docstrings module).
    assert pretrained._HOUR_LIFT_15M == retrained._HOUR_LIFT_15M
    assert pretrained._HOUR_SIZE_MULT == retrained._HOUR_SIZE_MULT


def test_floor_applies_to_quietest_hour():
    # 0h UTC = lift le plus bas mesuré (×0.44) -> clampé au plancher 0.20,
    # pas laissé filer à 0.44/2.43 ≈ 0.18.
    for mod in (pretrained, retrained):
        raw_ratio = mod._HOUR_LIFT_15M[0] / mod._HOUR_LIFT_MAX
        assert raw_ratio < mod._HOUR_SIZE_MULT_FLOOR
        assert mod._HOUR_SIZE_MULT[0] == mod._HOUR_SIZE_MULT_FLOOR


def test_enable_hour_sizing_declared_in_defaults():
    for mod in (pretrained, retrained):
        assert mod.Strategy._DEFAULTS["enable_hour_sizing"] is True
