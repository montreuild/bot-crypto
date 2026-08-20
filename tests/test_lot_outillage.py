"""Lot outillage — API-01, DAT-04, LIVE-02, SEC-01.

Constats petits mais qui ferment de vrais trous : une image de test qui ne
pouvait plus jouer la suite, un horodatage nul qui levait, un ordre au statut
inconnu compté comme exécuté, et une contrainte de déploiement nulle part
écrite.
"""
import datetime as dt
from pathlib import Path

import polars as pl
import pytest

RACINE = Path(__file__).resolve().parents[1]


# ── API-01 : l'étage `test` du Dockerfile doit porter ce dont les tests ont besoin ──

def _dockerfile() -> str:
    return (RACINE / "Dockerfile").read_text(encoding="utf-8")


def test_l_image_de_test_copie_les_dependances_hors_runtime():
    """`tests/test_openapi_contracts.py` importe `scripts.gen_frontend_types`
    et lit `generated.ts` : sans eux, `docker compose run tests` s'arrête à la
    collecte. La CI GitHub ne le voyait pas — elle lance pytest sur
    l'arborescence complète."""
    etage = _dockerfile().split("FROM runtime AS test", 1)[-1]
    for chemin in ("scripts/gen_frontend_types.py",
                   "frontend/src/types/generated.ts",
                   "scripts/audit_param_space.py",
                   "frontend/next.config.mjs"):
        assert chemin in etage, f"{chemin} n'est pas copié dans l'étage de test"


def test_toute_dependance_hors_app_des_tests_est_copiee():
    """Garde-fou générique : un futur test qui LIT un fichier hors `app/` et
    `tests/` doit ajouter son COPY, sinon l'image se re-casse en silence.

    On ne cherche que des accès réels — un chemin entre guillemets, ou un
    import depuis `scripts`. Une première version scannait tous les mots du
    fichier et remontait `frontend/src/app/page.tsx` mentionné dans un
    commentaire de `test_legacy_redirects.py` : un garde-fou qui crie sur des
    commentaires finit désactivé, ce qui est pire que pas de garde-fou.
    """
    import re

    etage = _dockerfile().split("FROM runtime AS test", 1)[-1]
    chemin = re.compile(r"""["'](frontend/[\w./-]+|scripts/[\w./-]+)["']""")
    import_scripts = re.compile(r"^\s*(?:from|import)\s+scripts\.([\w]+)", re.M)

    manquants = []
    for f in sorted((RACINE / "tests").glob("*.py")):
        texte = f.read_text(encoding="utf-8", errors="ignore")
        cibles = set(chemin.findall(texte))
        cibles |= {f"scripts/{m}.py" for m in import_scripts.findall(texte)}
        for cible in sorted(cibles):
            if cible not in etage:
                manquants.append(f"{f.name} → {cible}")
    assert not manquants, (
        "dépendances hors app/ non copiées dans l'étage de test du "
        "Dockerfile : " + ", ".join(manquants)
    )


# ── DAT-04 : un horodatage nul ne doit pas faire lever la détection ──────────

def test_un_horodatage_nul_ne_fait_pas_lever_la_detection():
    from app.core.ohlcv_gaps import detect_ohlcv_gaps

    debut = dt.datetime(2021, 1, 1, tzinfo=dt.timezone.utc)
    temps = [debut + dt.timedelta(hours=i) for i in range(6)]
    temps[3] = None
    df = pl.DataFrame({
        "time": temps,
        "open": [1.0] * 6, "high": [1.0] * 6,
        "low": [1.0] * 6, "close": [1.0] * 6, "volume": [1.0] * 6,
    })
    # Le contrat est de ne pas lever : la donnée est douteuse, mais un
    # rattrapage abandonné en silence est pire qu'un trou signalé.
    gaps = detect_ohlcv_gaps(df, "1h", symbol="BTC/USDC")
    assert isinstance(gaps, list)


# ── LIVE-02 : un ordre non prouvé est un échec ──────────────────────────────

@pytest.mark.parametrize("order,attendu,motif", [
    (None, True, "pas d'ordre du tout"),
    ({"status": "rejected", "filled": 0}, True, "rejet explicite"),
    ({"status": "open", "filled": 0}, True, "ordre au carnet, rien de rempli"),
    ({"status": "new", "filled": 0}, True, "accepté mais pas exécuté"),
    ({"status": "closed", "filled": 1.0}, False, "exécuté"),
    ({"status": "filled", "filled": 2.0}, False, "exécuté"),
    ({"status": "PARTIALLY_CANCELED", "filled": 0.5}, False, "partiellement rempli"),
    ({"status": "PARTIALLY_CANCELED", "filled": 0}, True, "statut inconnu, rien de rempli"),
    ({"status": None, "filled": 0}, True, "statut absent, rien de rempli"),
    ({}, True, "dict vide"),
])
def test_order_failed(order, attendu, motif):
    """Le repli d'un statut inconnu valait `False` : le bot enregistrait une
    position possiblement inexistante, avec un stop posé sur du vide."""
    from app.live.position_open_mixin import _order_failed

    assert _order_failed(order) is attendu, motif


# ── SEC-01 : la contrainte mono-processus doit rester vraie ─────────────────

def test_l_api_reste_lancee_en_process_unique():
    """Le registre de jetons WS vit en mémoire du processus. Un lancement
    multi-workers ferait échouer le handshake de façon intermittente, à une
    fréquence proportionnelle au nombre de workers."""
    suspects = []
    for nom in ("Dockerfile", "docker-compose.yml", "docker-compose.prod.yml"):
        f = RACINE / nom
        if not f.exists():
            continue
        for ligne in f.read_text(encoding="utf-8").splitlines():
            nu = ligne.strip()
            if nu.startswith("#"):
                continue
            if "--workers" in nu or "gunicorn" in nu:
                suspects.append(f"{nom}: {nu}")
    assert not suspects, (
        "lancement multi-processus détecté alors que app/api/ws_tickets.py "
        "stocke les jetons en mémoire — rendre le registre partagé d'abord : "
        + " | ".join(suspects)
    )


def test_la_contrainte_est_ecrite_dans_le_module():
    """Une contrainte de déploiement non écrite se perd au premier scale-up."""
    texte = (RACINE / "app" / "api" / "ws_tickets.py").read_text(encoding="utf-8")
    assert "process unique" in texte
    assert "SEC-01" in texte


# ── LIVE-02 bis : un ordre au REPOS n'est pas un ordre au marché ────────────
# `_order_failed` répond à « mon ordre au marché a-t-il été exécuté ? ». Un stop
# ou une jambe OCO sont posés pour ATTENDRE : `open` est leur état nominal et
# `filled=0` la normale. Leur appliquer `_order_failed` déclarait en échec
# toute pose de stop réussie sur un exchange renvoyant `status="open"` — le bot
# renonçait à protéger la position, ou annulait un stop bel et bien actif.

@pytest.mark.parametrize("order,attendu,motif", [
    (None, True, "pas d'ordre"),
    ({"status": "rejected", "id": "x"}, True, "refus explicite"),
    ({"status": "canceled", "id": "x"}, True, "annulé"),
    ({"status": "open", "filled": 0, "id": "x"}, False, "stop au carnet : NOMINAL"),
    ({"status": "new", "filled": 0, "id": "x"}, False, "accepté, en attente"),
    ({"id": "stop-1"}, False, "identifiant rendu = enregistré"),
    ({"ordId": "42"}, False, "variante OKX de l'identifiant"),
    ({}, True, "ni statut ni identifiant : rien ne prouve la pose"),
    ({"status": "open", "filled": 0}, True, "accepté mais sans identifiant"),
])
def test_order_rejected(order, attendu, motif):
    from app.live.position_open_mixin import _order_rejected

    assert _order_rejected(order) is attendu, motif


def test_les_deux_questions_divergent_bien_sur_un_ordre_au_carnet():
    """Le cœur de la distinction : un même dict, deux réponses opposées, et
    c'est normal — un ordre au marché resté au carnet a échoué, un stop resté
    au carnet fait exactement son travail."""
    from app.live.position_open_mixin import _order_failed, _order_rejected

    au_carnet = {"status": "open", "filled": 0, "id": "x"}
    assert _order_failed(au_carnet) is True
    assert _order_rejected(au_carnet) is False


def test_la_pose_de_stop_utilise_la_bonne_question():
    """Garde-fou : un retour à `_order_failed` sur ces deux sites rendrait
    toute pose de stop « échouée » sur un exchange qui répond `open`."""
    import inspect

    from app.live import position_manage_mixin as m

    src = inspect.getsource(m.PositionManageMixin._place_exchange_stop)
    assert "_order_rejected(order)" in src
    assert "_order_failed(order)" not in src, (
        "la pose d'un ordre au repos ne doit pas être jugée sur son exécution"
    )
