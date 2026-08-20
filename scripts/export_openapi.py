"""U-05 : exporte le schéma OpenAPI FastAPI vers frontend/src/types/openapi.json.

Usage (depuis la racine du dépôt) :
    python scripts/export_openapi.py

Le JSON est la source pour typer les réponses API. Les contrats critiques
sont aussi maintenus à la main dans ``frontend/src/types/index.ts``.
"""
from __future__ import annotations

import json
from pathlib import Path


def main() -> None:
    from app.api.main import app

    dest = Path(__file__).resolve().parents[1] / "frontend" / "src" / "types" / "openapi.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"écrit {dest} ({dest.stat().st_size} octets)")
    try:
        from scripts.gen_frontend_types import main as gen_ts
        gen_ts()
    except Exception as e:
        print(f"types TS non régénérés : {e}")


if __name__ == "__main__":
    main()
