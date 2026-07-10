#!/usr/bin/env python3
"""
Add an event to a user's standard calendar ([과제], [업무], [근태], [기타]).
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


CAL_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
]

CALENDAR_PREFIXES = {
    "과제": "[과제]",
    "업무": "[업무]",
    "근태": "[근태]",
    "기타": "[기타]",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Add an event to a user's standard calendar."
    )
    parser.add_argument(
        "--calendar-owner",
        required=True,
        help="Email of the calendar owner (e.g. user@example.com).",
    )
    parser.add_argument(
        "--calendar-type",
        required=True,
        choices=list(CALENDAR_PREFIXES.keys()),
        help="Calendar type: 과제, 업무, 근태, or 기타.",
    )
    parser.add_argument(
        "--title",
        required=True,
        help="Event title.",
    )
    parser.add_argument(
        "--start",
        required=True,
        help="Start datetime in ISO format (e.g. 2026-05-27T14:00:00).",
    )
    parser.add_argument(
        "--end",
        required=True,
        help="End datetime in ISO format (e.g. 2026-05-27T16:00:00).",
    )
    parser.add_argument(
        "--location",
        default="",
        help="Event location.",
    )
    parser.add_argument(
        "--description",
        default="",
        help="Event description.",
    )
    parser.add_argument(
        "--timezone",
        default="Asia/Seoul",
        help="Timezone for the event (default: Asia/Seoul).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print event details without creating it.",
    )
    return parser.parse_args()


def get_key_path() -> str:
    key_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not key_path:
        raise RuntimeError("GOOGLE_APPLICATION_CREDENTIALS is not set.")
    expanded = os.path.expanduser(key_path)
    if not os.path.exists(expanded):
        raise RuntimeError(f"Credential file not found: {expanded}")
    return expanded


def build_calendar_service(key_path: str, subject: str):
    creds = service_account.Credentials.from_service_account_file(
        key_path,
        scopes=CAL_SCOPES,
        subject=subject,
    )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def find_calendar_by_prefix(service, prefix: str) -> tuple[str, str] | None:
    page_token = None
    while True:
        response = (
            service.calendarList()
            .list(minAccessRole="writer", maxResults=250, pageToken=page_token)
            .execute()
        )
        for item in response.get("items", []):
            summary = item.get("summary", "")
            if summary.startswith(prefix):
                return (summary, item["id"])
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return None


def create_event(
    service,
    calendar_id: str,
    title: str,
    start: str,
    end: str,
    location: str,
    description: str,
    timezone: str,
) -> dict:
    event_body = {
        "summary": title,
        "location": location,
        "description": description,
        "start": {
            "dateTime": start,
            "timeZone": timezone,
        },
        "end": {
            "dateTime": end,
            "timeZone": timezone,
        },
    }
    return service.events().insert(calendarId=calendar_id, body=event_body).execute()


def main() -> None:
    args = parse_args()
    prefix = CALENDAR_PREFIXES[args.calendar_type]

    try:
        key_path = get_key_path()
        service = build_calendar_service(key_path, args.calendar_owner)

        result = find_calendar_by_prefix(service, prefix)
        if not result:
            print(f"[ERROR] No calendar found with prefix '{prefix}' for {args.calendar_owner}")
            sys.exit(1)

        calendar_name, calendar_id = result
        print(f"[INFO] Found calendar: {calendar_name} ({calendar_id})")

        if args.dry_run:
            print("[DRY-RUN] Would create event:")
            print(f"  Title: {args.title}")
            print(f"  Start: {args.start}")
            print(f"  End: {args.end}")
            print(f"  Location: {args.location}")
            print(f"  Description: {args.description}")
            return

        event = create_event(
            service,
            calendar_id,
            args.title,
            args.start,
            args.end,
            args.location,
            args.description,
            args.timezone,
        )
        print(f"[SUCCESS] Event created: {event.get('htmlLink')}")

    except HttpError as exc:
        print(f"[ERROR] Google API error: {exc}")
        sys.exit(1)
    except Exception as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
