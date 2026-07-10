#!/usr/bin/env python3
"""
Provision 4 standardized calendars for one Workspace user and grant admin ACL.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
]


@dataclass(frozen=True)
class CalendarRule:
    prefix: str
    color_id: str


RULES: tuple[CalendarRule, ...] = (
    CalendarRule(prefix="[과제]", color_id="9"),   # green
    CalendarRule(prefix="[업무]", color_id="16"),  # blue
    CalendarRule(prefix="[근태]", color_id="6"),   # orange
    CalendarRule(prefix="[기타]", color_id="19"),  # grey
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Provision standard calendars for one user and grant admin ACL."
    )
    parser.add_argument(
        "--user",
        required=True,
        help="Target user for impersonation and calendar ownership.",
    )
    parser.add_argument(
        "--name",
        help="Name token used in calendar title (default: local-part of --user).",
    )
    parser.add_argument(
        "--admin-email",
        default="calendar@example.com",
        help="Account that receives management permission (default: calendar@example.com).",
    )
    parser.add_argument(
        "--time-zone",
        default="Asia/Seoul",
        help="Calendar timezone (default: Asia/Seoul).",
    )
    return parser.parse_args()


def load_credentials(user_email: str):
    key_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not key_path:
        raise RuntimeError(
            "GOOGLE_APPLICATION_CREDENTIALS is not set. "
            "Set it to your service account key JSON path."
        )
    expanded = os.path.expanduser(key_path)
    if not os.path.exists(expanded):
        raise RuntimeError(f"Credential file not found: {expanded}")

    return service_account.Credentials.from_service_account_file(
        expanded,
        scopes=SCOPES,
        subject=user_email,
    )


def get_or_create_calendar(service, summary: str, time_zone: str) -> tuple[str, bool]:
    page_token = None
    while True:
        response = (
            service.calendarList()
            .list(minAccessRole="owner", maxResults=250, pageToken=page_token)
            .execute()
        )
        for item in response.get("items", []):
            if item.get("summary") == summary:
                return item["id"], False
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    created = (
        service.calendars()
        .insert(
            body={
                "summary": summary,
                "timeZone": time_zone,
                "description": "Provisioned by ji-calendar-provision.",
            }
        )
        .execute()
    )
    return created["id"], True


def ensure_calendar_color(service, calendar_id: str, color_id: str) -> None:
    service.calendarList().patch(
        calendarId=calendar_id,
        body={"colorId": color_id},
    ).execute()


def ensure_admin_writer_acl(service, calendar_id: str, admin_email: str) -> bool:
    entries = service.acl().list(calendarId=calendar_id).execute().get("items", [])
    for entry in entries:
        scope = entry.get("scope", {})
        if scope.get("type") == "user" and scope.get("value", "").lower() == admin_email.lower():
            if entry.get("role") == "writer":
                return False
            service.acl().update(
                calendarId=calendar_id,
                ruleId=entry["id"],
                body={
                    "scope": {"type": "user", "value": admin_email},
                    "role": "writer",
                },
            ).execute()
            return True

    service.acl().insert(
        calendarId=calendar_id,
        sendNotifications=False,
        body={
            "role": "writer",
            "scope": {"type": "user", "value": admin_email},
        },
    ).execute()
    return True


def provision(user_email: str, name: str, admin_email: str, time_zone: str) -> int:
    creds = load_credentials(user_email)
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)

    for rule in RULES:
        summary = f"{rule.prefix} {name}"
        calendar_id, created = get_or_create_calendar(service, summary, time_zone)
        state = "CREATED" if created else "REUSED"
        print(f"[{state}] {summary} ({calendar_id})")

        ensure_calendar_color(service, calendar_id, rule.color_id)
        print(f"[UPDATED] colorId={rule.color_id} for {calendar_id}")

        acl_created = ensure_admin_writer_acl(service, calendar_id, admin_email)
        acl_state = "UPDATED" if acl_created else "UNCHANGED"
        print(f"[{acl_state}] writer ACL granted to {admin_email} for {calendar_id}")

    print("[DONE] Provisioning completed.")
    return 0


def main() -> None:
    args = parse_args()
    name = args.name or args.user.split("@", 1)[0]
    try:
        sys.exit(provision(args.user, name, args.admin_email, args.time_zone))
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
