"""Asian Range (§30) et fenêtres Silver Bullet (§31).

Le test qui porte tout est :func:`test_la_plage_asiatique_ne_lit_pas_le_futur`.
Publier la plage de la session EN COURS reviendrait à connaître son extrême
avant qu'il soit formé — une fuite qui ne lève aucune erreur, rend le backtest
flatteur, et se découvre en production.

Les séries sont construites à la main pour que la réponse soit connue d'avance :
sur des données réelles on ne peut que constater qu'un niveau « a l'air
plausible », ce qui ne distingue pas une implémentation juste d'une
implémentation décalée d'une session.
"""
import datetime as dt

import numpy as np
import polars as pl
import pytest

# `silver_bullet_flags` vit dans `app.core.ict` et non ici : il PRÉEXISTAIT, et
# `smart_money` s'en sert déjà via `sb_bonus` / `sb_filter`. En écrire une
# seconde version dans `smc_sessions` aurait créé deux définitions des mêmes
# fenêtres, qui auraient fini par diverger.
from app.core.ict import SILVER_BULLET_UTC, silver_bullet_flags
from app.core.smc_sessions import SESSIONS, asian_range_levels


def _serie(n_jours: int = 4) -> pl.DataFrame:
    """Une barre par heure, avec des plages asiatiques d'amplitude DÉCROISSANTE.

    Jour ``j`` : high = 100 + (n_jours − j), low = 100 − (n_jours − j). La
    décroissance est délibérée — avec des plages croissantes, la session du jour
    dépasserait mécaniquement celle de la veille et allumerait le drapeau de
    balayage avant toute mèche injectée, ce qui rendrait les tests de balayage
    inexploitables. Hors session, le prix reste plat à 100.
    """
    debut = dt.datetime(2024, 1, 1)
    n = n_jours * 24
    temps, hi, lo = [], [], []
    a_deb, a_fin = SESSIONS["asia"]
    for i in range(n):
        t = debut + dt.timedelta(hours=i)
        temps.append(t)
        j = i // 24
        if a_deb <= t.hour < a_fin:
            hi.append(100.0 + (n_jours - j))
            lo.append(100.0 - (n_jours - j))
        else:
            hi.append(100.2)
            lo.append(99.8)
    return pl.DataFrame({
        "time": temps, "open": [100.0] * n, "high": hi, "low": lo,
        "close": [100.0] * n, "volume": [1.0] * n,
    })


# ─────────────────────────────────────────────────────────────────────────────
#  Asian Range
# ─────────────────────────────────────────────────────────────────────────────
def test_publie_la_session_close_pas_celle_en_cours():
    """Le cœur de la causalité. À 03:00 du jour 1, la session asiatique du
    jour 1 est EN TRAIN de se former : on doit voir celle du jour 0."""
    r = asian_range_levels(_serie(4))
    # jour 0 : aucune session close avant → rien à publier
    assert np.isnan(r["asia_high"][3])
    # jour 1, 03:00 (indice 24 + 3) → plage du jour 0 : 104 / 96
    assert r["asia_high"][27] == pytest.approx(104.0)
    assert r["asia_low"][27] == pytest.approx(96.0)
    # jour 2, 03:00 → plage du jour 1 : 103 / 97
    assert r["asia_high"][51] == pytest.approx(103.0)
    assert r["asia_low"][51] == pytest.approx(97.0)


def test_le_mid_est_le_milieu():
    r = asian_range_levels(_serie(3))
    fini = np.isfinite(r["asia_high"])
    assert np.allclose(r["asia_mid"][fini],
                       (r["asia_high"][fini] + r["asia_low"][fini]) / 2.0)


def test_le_balayage_est_constate_a_la_barre_pas_avant():
    """Un drapeau qui s'allumerait avant la barre du dépassement anticiperait
    le mouvement — exactement ce qu'on cherche à détecter, pas à supposer."""
    df = _serie(3).to_dict(as_series=False)
    # Jour 1, 12:00 : mèche au-dessus du high asiatique publié du jour 0 (103).
    cible = 24 + 12
    df["high"][cible] = 105.0
    r = asian_range_levels(pl.DataFrame(df))

    assert r["asia_swept_high"][cible - 1] == 0, "balayage anticipé"
    assert r["asia_swept_high"][cible] == 1
    assert r["asia_swept_high"][cible + 1] == 1, "le drapeau doit rester posé"
    assert r["asia_swept_low"][cible] == 0, "le bas n'a pas été touché"


def test_le_balayage_repart_a_zero_a_la_session_suivante():
    df = _serie(4).to_dict(as_series=False)
    df["high"][24 + 12] = 105.0                 # balayage au jour 1
    r = asian_range_levels(pl.DataFrame(df))
    assert r["asia_swept_high"][24 + 20] == 1
    # Jour 2 : nouvelle plage publiée (celle du jour 1 : 103/97), compteurs
    # remis à zéro — et la session du jour 2 (102/98) ne la dépasse pas.
    assert r["asia_swept_high"][48 + 1] == 0
    assert r["asia_high"][48 + 1] == pytest.approx(103.0)


def test_la_plage_asiatique_ne_lit_pas_le_futur():
    """LE test. Recalculer sur un PRÉFIXE doit donner les mêmes valeurs que le
    calcul sur la série complète — sinon la valeur d'une barre dépend de barres
    qu'elle ne peut pas connaître."""
    df = _serie(6)
    plein = asian_range_levels(df)
    for k in (30, 61, 97):
        prefixe = asian_range_levels(df.head(k))
        for col in ("asia_high", "asia_low", "asia_mid",
                    "asia_swept_high", "asia_swept_low"):
            a = np.asarray(prefixe[col], dtype=float)
            b = np.asarray(plein[col], dtype=float)[:k]
            differents = ~(np.isclose(a, b, equal_nan=True))
            assert not differents.any(), (
                f"préfixe {k} : {col} diverge sur "
                f"{int(differents.sum())} barre(s) — fuite temporelle")


def test_sans_colonne_temps_renvoie_none():
    assert asian_range_levels(pl.DataFrame({"high": [1.0], "low": [1.0]})) is None


def test_session_surchargeable():
    """Les heures de session ne sont pas universelles : la spec insiste."""
    df = _serie(3)
    autre = asian_range_levels(df, session=(0, 3))
    defaut = asian_range_levels(df)
    assert autre is not None and defaut is not None
    # Une fenêtre plus courte ne peut pas donner une plage plus large.
    fini = np.isfinite(autre["asia_high"]) & np.isfinite(defaut["asia_high"])
    assert (autre["asia_high"][fini] <= defaut["asia_high"][fini] + 1e-9).all()


# ─────────────────────────────────────────────────────────────────────────────
#  Silver Bullet
# ─────────────────────────────────────────────────────────────────────────────
def test_les_fenetres_silver_bullet_sont_horaires():
    """Vérifie la fonction EXISTANTE (`app.core.ict`), pas une réécriture."""
    df = _serie(3)
    epoch = df["time"].dt.epoch(time_unit="s").to_numpy()
    flags = silver_bullet_flags(epoch)
    heures = (epoch // 3600) % 24
    attendu = np.zeros(len(epoch), dtype=np.int8)
    for h0 in SILVER_BULLET_UTC:
        attendu |= ((heures >= h0) & (heures < h0 + 1)).astype(np.int8)
    assert (flags == attendu).all()


def test_couverture_horaire_coherente():
    """Trois fenêtres d'une heure sur vingt-quatre = 12.5 % des barres."""
    df = _serie(10)
    epoch = df["time"].dt.epoch(time_unit="s").to_numpy()
    assert silver_bullet_flags(epoch).mean() == pytest.approx(
        len(SILVER_BULLET_UTC) / 24, abs=0.01)
