"""LAB-A — les formulaires du Laboratoire entrent dans le contrat dérivé.

Leurs types côté front étaient écrits à la main, donc invérifiables : une
liste typée en objet imbriqué affichait 5 160 lignes de rebut, et un
entraînement poolé partait avec le mauvais champ. Décrire ces échanges dans
`app/api/schemas.py` les fait couvrir par le test de dérive qui compare
`generated.ts` au générateur, ligne pour ligne.
"""
import pytest
from pydantic import ValidationError

from app.api.main import app
from app.api.schemas import (
    CandleDatasetStats,
    CandlesStatsResponse,
    MLTrainRequest,
    MLTrainStarted,
)


@pytest.mark.parametrize("modele", [
    CandleDatasetStats, CandlesStatsResponse, MLTrainRequest, MLTrainStarted,
])
def test_les_contrats_du_lab_sont_generes(modele):
    """Sans cela, un type faux côté front ne se voit qu'à l'écran."""
    from pathlib import Path

    ts = Path("frontend/src/types/generated.ts").read_text(encoding="utf-8")
    assert f"export interface {modele.__name__} {{" in ts


def test_l_inventaire_du_cache_est_une_liste_pas_un_objet():
    """LAB-01 : la forme rendue, pinnée. `Record<symbole, Record<tf, …>>`
    faisait itérer les indices comme symboles et les clés comme timeframes."""
    champs = CandlesStatsResponse.model_fields
    assert set(champs) == {"store"}
    schema = CandlesStatsResponse.model_json_schema()
    assert schema["properties"]["store"]["type"] == "array"


def test_une_entree_de_cache_porte_la_completude_et_les_trous():
    """LAB-09 : ce que la route remplit, contre ce qu'elle ne remplit pas."""
    champs = CandleDatasetStats.model_fields
    for attendu in ("symbol", "tf", "bars", "size_kb", "gaps", "completeness"):
        assert attendu in champs, attendu
    # `from`/`to` restent déclarés — l'inventaire complet ne les date pas,
    # mais ils appartiennent à la forme publique.
    assert champs["first"].alias == "from"
    assert champs["last"].alias == "to"


def test_l_entrainement_distingue_recette_et_strategie():
    """LAB-02 : deux points d'entrée, pas deux synonymes. Le pooling n'existe
    que par la recette ; les confondre le rendait inatteignable."""
    champs = MLTrainRequest.model_fields
    for attendu in ("recipe", "strategy", "symbols", "universe",
                    "max_symbols", "compare_solo"):
        assert attendu in champs, attendu
    # Transmis en chaîne, `symbols` partait en 422 `list_type`. On vérifie la
    # forme acceptée, pas l'annotation — c'est elle qui décide du rejet.
    ok = MLTrainRequest(tf="1h", recipe="r", symbols=["BTC/USDC"])
    assert ok.symbols == ["BTC/USDC"]
    with pytest.raises(ValidationError):
        MLTrainRequest(tf="1h", recipe="r", symbols="BTC/USDC,ETH/USDC")


def test_les_routes_du_lab_declarent_leur_reponse():
    """Un `response_model` est ce qui fait entrer une route dans l'OpenAPI,
    donc dans le générateur de types."""
    chemins = app.openapi()["paths"]
    for methode, chemin, modele in (
        ("get", "/api/candles/stats", "CandlesStatsResponse"),
        ("post", "/api/ml/train", "MLTrainStarted"),
    ):
        ref = (chemins[chemin][methode].get("responses", {}).get("200", {})
               .get("content", {}).get("application/json", {}).get("schema", {}))
        nom = ref.get("title") or ref.get("$ref", "").rsplit("/", 1)[-1]
        assert nom == modele, f"{methode.upper()} {chemin} : {ref}"


def test_le_corps_d_entrainement_est_publie_dans_l_openapi():
    corps = (app.openapi()["paths"]["/api/ml/train"]["post"]
             .get("requestBody", {}).get("content", {})
             .get("application/json", {}).get("schema", {}))
    assert corps.get("$ref", "").endswith("MLTrainRequest"), corps
