#!/usr/bin/env python3
"""
Normalize ACLs on standardized calendars across a Workspace domain.

- Each target user keeps owner role on their calendars.
- calendar@example.com is downgraded to writer role.
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


RULES: tuple[CalendarRule, ...] = (
    CalendarRule(prefix="[과제]"),
    CalendarRule(prefix="[업무]"),
    CalendarRule(prefix="[근태]"),
    CalendarRule(prefix="[기타]"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Normalize ACLs for standardized calendars across a domain."
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
        help="Account that should keep writer access.",
    )
    parser.add_argument(
        "--max-users",
        type=int,
        help="Limit number of users for testing.",
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


def retryable_call(func):
    for i in range(3):
        try:
            return func()
        except HttpError as exc:
            if exc.resp.status == 404 and i < 2:
                time.sleep(1.2 * (i + 1))
                continue
            raise
    return None


def update_acl_role(service, calendar_id: str, rule_id: str, email: str, role: str) -> None:
    retryable_call(
        lambda: service.acl().update(
            calendarId=calendar_id,
            ruleId=rule_id,
            body={"scope": {"type": "user", "value": email}, "role": role},
        ).execute()
    )


def insert_acl_role(service, calendar_id: str, email: str, role: str) -> None:
    retryable_call(
        lambda: service.acl().insert(
            calendarId=calendar_id,
            sendNotifications=False,
            body={"scope": {"type": "user", "value": email}, "role": role},
        ).execute()
    )


def ensure_acl_role(service, calendar_id: str, email: str, role: str) -> bool:
    entries = retryable_call(
        lambda: service.acl().list(calendarId=calendar_id).execute().get("items", [])
    )
    for entry in entries:
        scope = entry.get("scope", {})
        if scope.get("type") == "user" and scope.get("value", "").lower() == email.lower():
            if entry.get("role") == role:
                return False
            update_acl_role(service, calendar_id, entry["id"], email, role)
            return True

    insert_acl_role(service, calendar_id, email, role)
    return True


def find_target_calendars(service, full_name: str) -> list[tuple[str, str]]:
    targets = {f"{rule.prefix} {full_name}" for rule in RULES}
    matched: list[tuple[str, str]] = []
    page_token = None
    while True:
        response = retryable_call(
            lambda: service.calendarList()
            .list(minAccessRole="owner", maxResults=250, pageToken=page_token)
            .execute()
        )
        for item in response.get("items", []):
            if item.get("summary") in targets:
                matched.append((item["summary"], item["id"]))
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return matched


def normalize_one_user(key_path: str, user_email: str, full_name: str, admin_email: str) -> int:
    service = build_calendar_service(key_path, user_email)
    calendars = find_target_calendars(service, full_name)
    print(f"[USER] {user_email} / {full_name} / calendars={len(calendars)}")
    updates = 0
    for summary, calendar_id in calendars:
        user_changed = ensure_acl_role(service, calendar_id, user_email, "owner")
        admin_changed = ensure_acl_role(service, calendar_id, admin_email, "writer")
        state = "UPDATED" if user_changed or admin_changed else "UNCHANGED"
        print(f"  [{state}] {summary} ({calendar_id})")
        updates += int(user_changed) + int(admin_changed)
    return updates


def is_target_user(user: dict, domain: str) -> bool:
    email = (user.get("primaryEmail") or "").lower()
    return email.endswith(f"@{domain.lower()}") and not user.get("suspended")


def main() -> None:
    args = parse_args()
    try:
        key_path = get_key_path()
        directory_service = build_directory_service(key_path, args.directory_subject)
        users = [
            u
            for u in list_domain_users(directory_service, args.domain)
            if is_target_user(u, args.domain)
            and (u.get("primaryEmail") or "").lower() != args.admin_email.lower()
        ]
        if args.max_users:
            users = users[: args.max_users]

        print(f"[INFO] Target users: {len(users)}")
        success = 0
        failed = 0
        acl_updates = 0
        for user in users:
            email = user.get("primaryEmail")
            full_name = user.get("name", {}).get("fullName") or email.split("@", 1)[0]
            try:
                acl_updates += normalize_one_user(key_path, email, full_name, args.admin_email)
                success += 1
            except Exception as exc:
                failed += 1
                print(f"[USER-ERROR] {email}: {exc}")

        print(
            f"[DONE] ACL normalization completed. success={success} failed={failed} acl_updates={acl_updates}"
        )
        if failed:
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
