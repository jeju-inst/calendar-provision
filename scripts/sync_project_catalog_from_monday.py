#!/usr/bin/env python3
"""
Build config/projects.json from the JI Monday progress-project board.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib import request as urlrequest

REPO_DIR = Path(__file__).resolve().parents[1]
DEFAULT_ENV_PATHS = (REPO_DIR / ".env",)
DEFAULT_OUTPUT_PATH = REPO_DIR / "config" / "projects.json"

MONDAY_API_URL = "https://api.monday.com/v2"
PROGRESS_BOARD_ID = os.getenv("MONDAY_PROGRESS_BOARD_ID", "1234567890")
RELATION_COLUMN_ID = os.getenv("MONDAY_RELATION_COLUMN_ID", "board_relation_xxxxx")
CODE_MIRROR_COLUMN_ID = os.getenv("MONDAY_CODE_COLUMN_ID", "lookup_code")
PM_MIRROR_COLUMN_ID = os.getenv("MONDAY_PM_COLUMN_ID", "lookup_pm")

SOURCE_BOARD_CONFIG = json.loads(os.getenv("MONDAY_SOURCE_BOARD_CONFIG", "{}"))

ITEM_ID_PATTERN = re.compile(r"\b\d{8,}\b")


def load_env(paths: tuple[Path, ...]) -> None:
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            if key in os.environ:
                continue
            os.environ[key] = value.strip().strip("'\"")


def monday_graphql(token: str, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
    req = urlrequest.Request(
        MONDAY_API_URL,
        data=payload,
        headers={
            "Authorization": token,
            "Content-Type": "application/json",
            "API-Version": "2025-04",
        },
        method="POST",
    )
    with urlrequest.urlopen(req, timeout=60) as response:
        data = json.loads(response.read().decode("utf-8"))
    if data.get("errors"):
        raise RuntimeError(json.dumps(data["errors"], ensure_ascii=False))
    return data["data"]


def parse_linked_item_ids(value: str | None, text: str | None) -> list[str]:
    ids: list[str] = []
    if value:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            for key in ("linkedPulseIds", "linkedItemIds", "item_ids", "items"):
                raw_items = parsed.get(key)
                if not isinstance(raw_items, list):
                    continue
                for raw_item in raw_items:
                    if isinstance(raw_item, dict):
                        item_id = raw_item.get("linkedPulseId") or raw_item.get("itemId") or raw_item.get("id")
                    else:
                        item_id = raw_item
                    if item_id:
                        ids.append(str(item_id))
        ids.extend(ITEM_ID_PATTERN.findall(value))
    if text:
        ids.extend(ITEM_ID_PATTERN.findall(text))
    return list(dict.fromkeys(ids))


def fetch_progress_items(token: str, board_id: str, limit: int) -> list[dict[str, Any]]:
    query_first = """
    query ($board_id: [ID!], $limit: Int!, $column_ids: [String!]) {
      boards(ids: $board_id) {
        items_page(limit: $limit) {
          cursor
          items {
            id
            name
            column_values(ids: $column_ids) {
              id
              type
              text
              value
              ... on BoardRelationValue {
                linked_item_ids
                linked_items {
                  id
                  name
                  board { id name }
                }
              }
              ... on MirrorValue {
                display_value
              }
            }
          }
        }
      }
    }
    """
    query_next = """
    query ($cursor: String!, $limit: Int!) {
      next_items_page(cursor: $cursor, limit: $limit) {
        cursor
        items {
          id
          name
          column_values {
            id
            type
            text
            value
            ... on BoardRelationValue {
              linked_item_ids
              linked_items {
                id
                name
                board { id name }
              }
            }
            ... on MirrorValue {
              display_value
            }
          }
        }
      }
    }
    """
    page_size = min(100, max(1, limit))
    data = monday_graphql(
        token,
        query_first,
        {
            "board_id": [board_id],
            "limit": page_size,
            "column_ids": [RELATION_COLUMN_ID, CODE_MIRROR_COLUMN_ID, PM_MIRROR_COLUMN_ID],
        },
    )
    page = data["boards"][0]["items_page"]
    items = list(page["items"])
    cursor = page.get("cursor")
    while cursor and len(items) < limit:
        data = monday_graphql(token, query_next, {"cursor": cursor, "limit": min(100, limit - len(items))})
        page = data["next_items_page"]
        items.extend(page["items"])
        cursor = page.get("cursor")
    return items[:limit]


def column_display(item: dict[str, Any], column_id: str) -> str:
    for column in item.get("column_values", []):
        if column.get("id") == column_id:
            return str(column.get("display_value") or column.get("text") or "").strip()
    return ""


def relation_linked_items(item: dict[str, Any]) -> list[dict[str, Any]]:
    for column in item.get("column_values", []):
        if column.get("id") == RELATION_COLUMN_ID:
            linked_items = column.get("linked_items") or []
            if linked_items:
                return linked_items
            linked_ids = column.get("linked_item_ids") or parse_linked_item_ids(column.get("value"), column.get("text"))
            return [{"id": item_id, "name": "", "board": {"id": ""}} for item_id in linked_ids]
    return []


def build_catalog(token: str, limit: int) -> dict[str, Any]:
    progress_items = fetch_progress_items(token, PROGRESS_BOARD_ID, limit)
    projects_by_code: dict[str, dict[str, Any]] = {}

    for progress in progress_items:
        progress_id = str(progress["id"])
        code = column_display(progress, CODE_MIRROR_COLUMN_ID)
        pm = column_display(progress, PM_MIRROR_COLUMN_ID)
        if not code:
            continue
        linked_items = relation_linked_items(progress)
        if not linked_items:
            continue
        for source in linked_items:
            source_id = str(source.get("id") or "")
            name = str(source.get("name") or "").strip()
            project = projects_by_code.setdefault(
                code,
                {
                    "code": code,
                    "pm": pm,
                    "name": name,
                    "monday_item_ids": [],
                    "source_project_item_ids": [],
                    "source_project_urls": [],
                },
            )
            if not project["pm"] and pm:
                project["pm"] = pm
            if not project["name"] and name:
                project["name"] = name
            if progress_id not in project["monday_item_ids"]:
                project["monday_item_ids"].append(progress_id)
            if source_id and source_id not in project["source_project_item_ids"]:
                project["source_project_item_ids"].append(source_id)
                board_id = str(source.get("board", {}).get("id") or "")
                if board_id:
                    project["source_project_urls"].append(f"https://example.monday.com/boards/{board_id}/pulses/{source_id}")

    return {
        "source": {
            "progress_board_id": PROGRESS_BOARD_ID,
            "relation_column_id": RELATION_COLUMN_ID,
            "code_column_id": CODE_MIRROR_COLUMN_ID,
            "pm_column_id": PM_MIRROR_COLUMN_ID,
            "linked_boards": [
                f"{board_id}:{config['code']}:{config['pm']}"
                for board_id, config in SOURCE_BOARD_CONFIG.items()
            ],
            "synced_at": datetime.now(UTC).isoformat(),
        },
        "projects": sorted(projects_by_code.values(), key=lambda project: project["code"]),
    }


def main() -> None:
    load_env(DEFAULT_ENV_PATHS)
    default_limit = max(int(os.getenv("PROJECT_CATALOG_LIMIT", "2000")), 2000)

    parser = argparse.ArgumentParser(description="Sync project catalog from Monday.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH), help="Output JSON path.")
    parser.add_argument("--limit", type=int, default=default_limit)
    parser.add_argument("--dry-run", action="store_true", help="Print summary without writing.")
    args = parser.parse_args()

    token = os.getenv("MONDAY_API_TOKEN")
    if not token or token.startswith("replace"):
        raise SystemExit("MONDAY_API_TOKEN is not configured.")

    catalog = build_catalog(token, args.limit)
    output = Path(args.output)
    if args.dry_run:
        print(f"Would write {len(catalog['projects'])} projects to {output}")
        for project in catalog["projects"][:5]:
            print(f"- {project['code']} {project['pm']} {project['name']} ({len(project['monday_item_ids'])} items)")
        return

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(catalog['projects'])} projects to {output}")


if __name__ == "__main__":
    main()
