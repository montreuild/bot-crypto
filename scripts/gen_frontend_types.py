"""FE-03 : génère ``frontend/src/types/generated.ts`` depuis ``app.api.schemas``.

Usage (racine du dépôt) :
    python scripts/gen_frontend_types.py

Ne remplace pas ``types/index.ts`` (contrats UI plus riches). Les 5 routes
chaudes + TradeRow y sont réexportés.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


MODELS = (
    "BacktestResultModel",
    "BacktestRunResponse",
    "PortfolioResponse",
    "TradeRow",
    "TradesListResponse",
    "RiskOverviewResponse",
    "OptimizeResultsResponse",
)

_HEADER = '''\
/**
 * Contrats dérivés des `response_model` serveur (API-01 / FE-03).
 * Source : `app/api/schemas.py`. Régénérer après un changement de schéma :
 *   python scripts/gen_frontend_types.py
 * Ne pas recopier à la main dans index.ts — importer d'ici.
 */

'''


def _ts_type(schema: Dict[str, Any], defs: Dict[str, Any]) -> str:
    if not schema:
        return "unknown"
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        return name
    if "anyOf" in schema:
        parts = [_ts_type(p, defs) for p in schema["anyOf"]]
        # Pydantic Optional → T | null
        return " | ".join(dict.fromkeys(parts))
    t = schema.get("type")
    if t == "string":
        return "string"
    if t == "integer":
        return "number"
    if t == "number":
        return "number"
    if t == "boolean":
        return "boolean"
    if t == "null":
        return "null"
    if t == "array":
        return f"Array<{_ts_type(schema.get('items') or {}, defs)}>"
    if t == "object":
        extra = schema.get("additionalProperties")
        if extra is True or extra == {}:
            return "Record<string, unknown>"
        if isinstance(extra, dict):
            return f"Record<string, {_ts_type(extra, defs)}>"
        props = schema.get("properties")
        if props:
            return "Record<string, unknown>"
        return "Record<string, unknown>"
    return "unknown"


def _iface(name: str, schema: Dict[str, Any], defs: Dict[str, Any]) -> str:
    required = set(schema.get("required") or [])
    extra = schema.get("additionalProperties")
    lines = [f"export interface {name} {{"]
    for key, prop in (schema.get("properties") or {}).items():
        opt = "?" if key not in required else ""
        lines.append(f"  {key}{opt}: {_ts_type(prop, defs)};")
    if extra is True or extra == {}:
        lines.append("  [key: string]: unknown;")
    lines.append("}")
    return "\n".join(lines)


def render() -> str:
    from app.api import schemas as S

    chunks: List[str] = [_HEADER.rstrip(), ""]
    for name in MODELS:
        model = getattr(S, name)
        raw = model.model_json_schema()
        defs = raw.get("$defs") or raw.get("definitions") or {}
        chunks.append(_iface(name, raw, defs))
        chunks.append("")
    return "\n".join(chunks)


def main() -> None:
    dest = Path(__file__).resolve().parents[1] / "frontend" / "src" / "types" / "generated.ts"
    dest.write_text(render(), encoding="utf-8")
    print(f"écrit {dest} ({dest.stat().st_size} octets)")


if __name__ == "__main__":
    main()
