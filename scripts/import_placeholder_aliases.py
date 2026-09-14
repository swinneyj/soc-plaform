"""Import placeholder alias definitions into the SOC Platform database.

Reads a JSON file with shape:

{
  "aliases": [
    {"alias": "host", "fields": ["Host", "dest"], "description": "..."},
    ...
  ]
}

and upserts them into the PlaceholderAlias table.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from db.models import SessionLocal, PlaceholderAlias  # type: ignore


def import_aliases_from_json(path: Path) -> dict:
    if not path.exists():
        return {"success": False, "error": f"File not found: {path}", "imported": 0, "updated": 0}

    try:
        with path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as e:
        return {"success": False, "error": f"Failed to read JSON: {e}", "imported": 0, "updated": 0}

    items = payload.get("aliases") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return {"success": False, "error": "JSON must contain an 'aliases' array", "imported": 0, "updated": 0}

    db = SessionLocal()
    imported = 0
    updated = 0
    try:
        from sqlalchemy import func  # type: ignore

        for item in items:
            try:
                alias_raw = (item.get("alias") or "").strip()
                if not alias_raw:
                    continue
                alias = alias_raw.lower()
                fields_raw = item.get("fields") or []
                if not isinstance(fields_raw, list):
                    fields_raw = []
                cleaned_fields = [str(f).strip() for f in fields_raw if str(f).strip()]
                description = (item.get("description") or "").strip()

                existing = db.query(PlaceholderAlias).filter(
                    func.lower(PlaceholderAlias.alias) == alias
                ).first()
                fields_json = json.dumps(cleaned_fields)

                if existing:
                    existing.fields = fields_json
                    existing.description = description
                    updated += 1
                else:
                    db.add(
                        PlaceholderAlias(
                            alias=alias,
                            fields=fields_json,
                            description=description,
                        )
                    )
                    imported += 1
            except Exception:
                # Skip problematic rows but continue with others
                continue

        db.commit()
        return {"success": True, "imported": imported, "updated": updated}
    except Exception as e:
        db.rollback()
        return {"success": False, "error": str(e), "imported": imported, "updated": updated}
    finally:
        db.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import placeholder aliases from JSON into the database")
    parser.add_argument("--input", required=True, help="Path to placeholder_aliases JSON file")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    result = import_aliases_from_json(Path(args.input))
    if result.get("success"):
        print(
            f"[+] Alias import complete. Imported: {result['imported']} | Updated: {result['updated']}"
        )
    else:
        print(f"[!] Alias import failed: {result.get('error')}")


if __name__ == "__main__":
    main()
