"""Deterministically export the complete v2 FastAPI OpenAPI contract."""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.v2.main import create_application

OUTPUT = ROOT / "contracts" / "auvra-v2.openapi.json"


def document() -> dict:
    spec = create_application().openapi()
    spec["openapi"] = "3.1.1"
    return spec


def encoded() -> str:
    return json.dumps(document(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> None:
    output = Path(sys.argv[1]).resolve() if len(sys.argv) == 2 else OUTPUT
    output.parent.mkdir(exist_ok=True)
    output.write_text(encoded(), encoding="utf-8")


if __name__ == "__main__":
    main()
