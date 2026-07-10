#!/usr/bin/env python3
"""
Smoke test for Google Admin SDK Directory API via Domain-wide Delegation.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/admin.directory.user.readonly"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Test Directory API access (single user + domain list)."
    )
    parser.add_argument(
        "--subject",
        required=True,
        help="Impersonation subject (admin account email).",
    )
    parser.add_argument(
        "--user",
        default="operator@example.com",
        help="User email to lookup (default: operator@example.com).",
    )
    parser.add_argument(
        "--domain",
        default="example.com",
        help="Domain to list users from (default: example.com).",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=20,
        help="Max users to list (default: 20).",
    )
    return parser.parse_args()


def load_creds(subject: str):
    key_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not key_path:
        raise RuntimeError("GOOGLE_APPLICATION_CREDENTIALS is not set.")
    key_path = os.path.expanduser(key_path)
    if not os.path.exists(key_path):
        raise RuntimeError(f"Credential file not found: {key_path}")

    return service_account.Credentials.from_service_account_file(
        key_path, scopes=SCOPES, subject=subject
    )


def main() -> None:
    args = parse_args()
    try:
        creds = load_creds(args.subject)
        service = build("admin", "directory_v1", credentials=creds, cache_discovery=False)

        user = service.users().get(userKey=args.user).execute()
        full_name = user.get("name", {}).get("fullName")
        primary = user.get("primaryEmail")
        print(f"[OK] User lookup: {primary} / fullName={full_name}")

        listed = (
            service.users()
            .list(
                domain=args.domain,
                maxResults=args.max_results,
                orderBy="email",
            )
            .execute()
            .get("users", [])
        )
        print(f"[OK] Domain list succeeded: {len(listed)} users (sample)")
        for u in listed:
            print(
                f"  - {u.get('primaryEmail')} / "
                f"{u.get('name', {}).get('fullName')}"
            )

        print("[DONE] Directory API test passed.")
    except HttpError as exc:
        print("[ERROR] Google API request failed.")
        print(f"[ERROR] status={exc.status_code if hasattr(exc, 'status_code') else 'unknown'}")
        try:
            detail = json.loads(exc.content.decode("utf-8"))
            print(json.dumps(detail, ensure_ascii=False, indent=2))
        except Exception:
            print(str(exc))
        sys.exit(1)
    except Exception as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
