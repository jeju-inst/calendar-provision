#!/usr/bin/env python3
"""
Smoke test for Google Workspace Domain-wide Delegation against one user.

Usage:
  python scripts/test_google_ws_control.py --user operator@example.com --create-test-calendar
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Test impersonation and calendar control for one Workspace user."
    )
    parser.add_argument(
        "--user",
        required=True,
        help="Target Workspace user email for impersonation (e.g. operator@example.com).",
    )
    parser.add_argument(
        "--create-test-calendar",
        action="store_true",
        help="Create one temporary calendar to verify write permission.",
    )
    parser.add_argument(
        "--keep-test-calendar",
        action="store_true",
        help="Keep created test calendar instead of deleting it.",
    )
    return parser.parse_args()


def load_credentials(user_email: str):
    key_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not key_path:
        raise RuntimeError(
            "GOOGLE_APPLICATION_CREDENTIALS is not set. "
            "Set it to your service account key JSON path."
        )
    if not os.path.exists(os.path.expanduser(key_path)):
        raise RuntimeError(
            f"Credential file not found: {os.path.expanduser(key_path)}"
        )

    creds = service_account.Credentials.from_service_account_file(
        os.path.expanduser(key_path),
        scopes=SCOPES,
        subject=user_email,
    )
    return creds


def run_smoke_test(
    user_email: str, create_test_calendar: bool, keep_test_calendar: bool
) -> int:
    creds = load_credentials(user_email)
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)

    try:
        calendars = (
            service.calendarList()
            .list(minAccessRole="owner", maxResults=10)
            .execute()
            .get("items", [])
        )
        print(f"[OK] Impersonation succeeded for: {user_email}")
        print(f"[INFO] Visible owner calendars (sample): {len(calendars)}")
        for cal in calendars[:5]:
            print(f"  - {cal.get('summary')} ({cal.get('id')})")

        if create_test_calendar:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            body = {
                "summary": f"[TEST] ji-provision-control-{stamp}",
                "description": "Temporary calendar created by smoke test.",
                "timeZone": "Asia/Seoul",
            }
            created = service.calendars().insert(body=body).execute()
            created_id = created.get("id")
            print(f"[OK] Created test calendar: {created.get('summary')} ({created_id})")

            if keep_test_calendar:
                print(f"[INFO] Kept test calendar: {created_id}")
            else:
                service.calendars().delete(calendarId=created_id).execute()
                print(f"[OK] Deleted test calendar: {created_id}")

        print("[DONE] Google Workspace control test passed.")
        return 0
    except HttpError as exc:
        print("[ERROR] Google API request failed.")
        print(f"[ERROR] status={exc.status_code if hasattr(exc, 'status_code') else 'unknown'}")
        try:
            detail = json.loads(exc.content.decode("utf-8"))
            print(json.dumps(detail, ensure_ascii=False, indent=2))
        except Exception:
            print(str(exc))
        return 1
    except Exception as exc:
        print(f"[ERROR] {exc}")
        return 1


def main() -> None:
    args = parse_args()
    sys.exit(
        run_smoke_test(
            args.user,
            args.create_test_calendar,
            args.keep_test_calendar,
        )
    )


if __name__ == "__main__":
    main()
