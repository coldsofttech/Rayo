#!/usr/bin/env python3
"""Write the OpenAPI document to docs/openapi.json.

CI regenerates it and fails if it differs, so the committed contract always matches the code.
Usage: uv run python scripts/export_openapi.py
"""

import importlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "installer"))
import projectinfo  # noqa: E402

INFO = projectinfo.load()
# The contract does not depend on settings; avoid needing an API key to build the app. The
# settings prefix is the API package's name upper-cased (RAYO_API_ for rayo_api).
os.environ.setdefault(f"{INFO.api_env_prefix}AUTH_MODE", "none")
os.environ[f"{INFO.api_env_prefix}DOCS_ENABLED"] = "true"

create_app = importlib.import_module(f"{INFO.api_package}.main").create_app
OUTPUT = ROOT / "docs" / "openapi.json"


def main() -> None:
    spec = create_app().openapi()
    OUTPUT.write_text(
        json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
