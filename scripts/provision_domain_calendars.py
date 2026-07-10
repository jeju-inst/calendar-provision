#!/usr/bin/env python3
"""
Provision standard calendars for all users in a Workspace domain.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

CAL_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
]
DIR_SCOPES = ["https://www.googleapis.com/auth/admin.directory.user.readonly"]


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
        description="Provision calendars for all users in a domain."
    )
    parser.add_argument("--domain", required=True, help="Workspace domain (e.g. example.com).")
    parser.add_argument(
        "--directory-subject",
        required=True,
        help="Impersonation subject for Directory API (admin account).",
    )
    parser.add_argument(
        "--admin-email",
        default="calendar@example.com",
        help="Account that receives writer ACL on each created calendar.",
    )
    parser.add_argument(
        "--time-zone",
        default="Asia/Seoul",
        help="Calendar timezone (default: Asia/Seoul).",
    )
    parser.add_argument(
        "--include-suspended",
        action="store_true",
        help="Include suspended users as targets.",
    )
    parser.add_argument(
        "--max-users",
        type=int,
        help="Limit number of users for safety/testing.",
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


def build_directory_service(key_path: str, subject: str):
    creds = service_account.Credentials.from_service_account_file(
        key_path,
        scopes=DIR_SCOPES,
        subject=subject,
    )
    return build("admin", "directory_v1", credentials=creds, cache_discovery=False)


def build_calendar_service(key_path: str, subject: str):
    creds = service_account.Credentials.from_service_account_file(
        key_path,
        scopes=CAL_SCOPES,
        subject=subject,
    )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def list_domain_users(directory_service, domain: str) -> list[dict]:
    users: list[dict] = []
    page_token = None
    while True:
        response = (
            directory_service.users()
            .list(
                domain=domain,
                maxResults=500,
                orderBy="email",
                projection="basic",
                pageToken=page_token,
            )
            .execute()
        )
        users.extend(response.get("users", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return users


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
    for i in range(3):
        try:
            service.calendarList().patch(
                calendarId=calendar_id,
                body={"colorId": color_id},
            ).execute()
            return
        except HttpError as exc:
            if exc.resp.status == 404 and i < 2:
                time.sleep(1.2 * (i + 1))
                continue
            raise


def ensure_admin_writer_acl(service, calendar_id: str, admin_email: str) -> bool:
    for i in range(3):
        try:
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
        except HttpError as exc:
            if exc.resp.status == 404 and i < 2:
                time.sleep(1.2 * (i + 1))
                continue
            raise
    return False


def provision_one_user(
    key_path: str,
    user_email: str,
    full_name: str,
    admin_email: str,
    time_zone: str,
) -> None:
    service = build_calendar_service(key_path, user_email)
    print(f"[USER] {user_email} / {full_name}")
    for rule in RULES:
        summary = f"{rule.prefix} {full_name}"
        calendar_id, created = get_or_create_calendar(service, summary, time_zone)
        state = "CREATED" if created else "REUSED"
        print(f"  [{state}] {summary} ({calendar_id})")
        ensure_calendar_color(service, calendar_id, rule.color_id)
        print(f"  [UPDATED] colorId={rule.color_id}")
        acl_created = ensure_admin_writer_acl(service, calendar_id, admin_email)
        acl_state = "UPDATED" if acl_created else "UNCHANGED"
        print(f"  [{acl_state}] writer ACL -> {admin_email}")


def is_target_user(user: dict, domain: str, include_suspended: bool) -> bool:
    email = (user.get("primaryEmail") or "").lower()
    if not email.endswith(f"@{domain.lower()}"):
        return False
    if not include_suspended and user.get("suspended"):
        return False
    return True


def main() -> None:
    args = parse_args()
    try:
        key_path = get_key_path()
        directory_service = build_directory_service(key_path, args.directory_subject)
        users = list_domain_users(directory_service, args.domain)
        targets = [
            u for u in users if is_target_user(u, args.domain, args.include_suspended)
        ]
        if args.max_users:
            targets = targets[: args.max_users]

        print(f"[INFO] Domain users fetched: {len(users)}")
        print(f"[INFO] Target users: {len(targets)}")

        success = 0
        failed = 0
        for user in targets:
            email = user.get("primaryEmail")
            full_name = user.get("name", {}).get("fullName") or email.split("@", 1)[0]
            try:
                provision_one_user(
                    key_path=key_path,
                    user_email=email,
                    full_name=full_name,
                    admin_email=args.admin_email,
                    time_zone=args.time_zone,
                )
                success += 1
            except Exception as exc:
                failed += 1
                print(f"[USER-ERROR] {email}: {exc}")

        print(f"[DONE] Domain provisioning completed. success={success} failed={failed}")
        if failed > 0:
            sys.exit(2)
    except HttpError as exc:
        print("[ERROR] Google API request failed.")
        print(
            f"[ERROR] status={exc.status_code if hasattr(exc, 'status_code') else 'unknown'}"
        )
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
