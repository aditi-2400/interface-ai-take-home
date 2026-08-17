"""Regenerate the exported JSON Schema for the Capability artifact.

Run as: python -m artifacts.export_schema
"""

import json
from pathlib import Path

from artifacts.models import Capability

SCHEMA_PATH = Path(__file__).parent / "schema" / "capability.schema.json"


def export() -> Path:
    schema = Capability.model_json_schema()
    SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA_PATH.write_text(json.dumps(schema, indent=2) + "\n")
    return SCHEMA_PATH


if __name__ == "__main__":
    path = export()
    print(f"Wrote {path}")
