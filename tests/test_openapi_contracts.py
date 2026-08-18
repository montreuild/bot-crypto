"""API-01 / FE-03 : les 5 routes chaudes déclarent un response_model."""
from app.api.main import app

_HOT = {
    ("POST", "/api/backtest"): "BacktestRunResponse",
    ("GET", "/api/portfolio"): "PortfolioResponse",
    ("GET", "/api/trades"): "TradesListResponse",
    ("GET", "/api/risk"): "RiskOverviewResponse",
    ("GET", "/api/optimize/results"): "OptimizeResultsResponse",
}


def test_hot_routes_declare_response_model():
    spec = app.openapi()
    paths = spec["paths"]
    for (method, path), model in _HOT.items():
        op = paths[path][method.lower()]
        ref = (op.get("responses", {}).get("200", {})
               .get("content", {}).get("application/json", {})
               .get("schema", {}))
        title = ref.get("title") or ""
        deref = ""
        if "$ref" in ref:
            deref = ref["$ref"].rsplit("/", 1)[-1]
        assert model in (title, deref), f"{method} {path} sans {model}: {ref}"


def test_openapi_expose_les_cinq_schemas():
    """FE-03 : le contrat vit côté serveur, pas seulement dans index.ts."""
    schemas = set((app.openapi().get("components") or {}).get("schemas") or {})
    for name in (
        "BacktestRunResponse", "PortfolioResponse", "TradesListResponse",
        "RiskOverviewResponse", "OptimizeResultsResponse",
    ):
        assert name in schemas
