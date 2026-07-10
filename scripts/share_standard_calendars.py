#!/usr/bin/env python3
"""
Share standardized calendars from selected users to one viewer.
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


@dataclass(frozen=True)
class WorkspaceUser:
    email: str
    full_name: str


RULES: tuple[CalendarRule, ...] = (
    CalendarRule(prefix="[과제]"),
    CalendarRule(prefix="[업무]"),
    CalendarRule(prefix="[근태]"),
    CalendarRule(prefix="[기타]"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Grant one viewer read-only access to selected users' standard calendars."
    )
    parser.add_argument("--domain", required=True, help="Workspace domain, e.g. example.com.")
    parser.add_argument(
        "--directory-subject",
        required=True,
        help="Impersonation subject for Directory API, usually an admin account.",
    )
    parser.add_argument(
        "--viewer",
        required=True,
        help="Viewer name or email that should receive calendar access.",
    )
    parser.add_argument(
        "--source",
        action="append",
        required=True,
        help="Source user name or email. Repeat for each source user.",
    )
    parser.add_argument(
        "--role",
        default="reader",
        choices=("reader", "freeBusyReader"),
        help="Calendar ACL role to grant. Default: reader.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned ACL changes without applying them.",
    )
    parser.add_argument(
        "--subscribe-viewer",
        action="store_true",
        help="Also add matched calendars to the viewer's Google Calendar list.",
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


def list_domain_users(directory_service, domain: str) -> list[WorkspaceUser]:
    users: list[WorkspaceUser] = []
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
        for user in response.get("users", []):
            if user.get("suspended"):
                continue
            email = user.get("primaryEmail")
            if not email:
                continue
            full_name = user.get("name", {}).get("fullName") or email.split("@", 1)[0]
            users.append(WorkspaceUser(email=email.lower(), full_name=full_name))
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return users


def resolve_user(users: list[WorkspaceUser], token: str, domain: str) -> WorkspaceUser:
    normalized = token.strip().lower()
    candidates = [
        user
        for user in users
        if user.email == normalized
        or user.email.split("@", 1)[0] == normalized
        or user.full_name.lower() == normalized
    ]
    if not candidates and "@" not in normalized:
        email = f"{normalized}@{domain.lower()}"
        candidates = [user for user in users if user.email == email]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise RuntimeError(f"No Workspace user matched: {token}")
    matched = ", ".join(f"{user.full_name} <{user.email}>" for user in candidates)
    raise RuntimeError(f"Ambiguous Workspace user token {token!r}: {matched}")


def retryable_call(func):
    for i in range(3):
        try:
            return func()
        except HttpError as exc:
            if exc.resp.status in (404, 409, 429, 500, 502, 503, 504) and i < 2:
                time.sleep(1.2 * (i + 1))
                continue
            raise
    return None


def find_standard_calendars(service, full_name: str) -> list[tuple[str, str]]:
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
            summary = item.get("summary")
            if summary in targets:
                matched.append((summary, item["id"]))
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return sorted(matched)


def ensure_acl_role(service, calendar_id: str, viewer_email: str, role: str, dry_run: bool) -> bool:
    entries = retryable_call(
        lambda: service.acl().list(calendarId=calendar_id).execute().get("items", [])
    )
    for entry in entries:
        scope = entry.get("scope", {})
        if scope.get("type") == "user" and scope.get("value", "").lower() == viewer_email:
            if entry.get("role") == role:
                return False
            if not dry_run:
                retryable_call(
                    lambda: service.acl()
                    .update(
                        calendarId=calendar_id,
                        ruleId=entry["id"],
                        body={"scope": {"type": "user", "value": viewer_email}, "role": role},
                    )
                    .execute()
                )
            return True

    if not dry_run:
        retryable_call(
            lambda: service.acl()
            .insert(
                calendarId=calendar_id,
                sendNotifications=False,
                body={"scope": {"type": "user", "value": viewer_email}, "role": role},
            )
            .execute()
        )
    return True


def ensure_viewer_subscription(
    viewer_service,
    calendar_id: str,
    summary: str,
    dry_run: bool,
) -> bool:
    try:
        entry = retryable_call(
            lambda: viewer_service.calendarList().get(calendarId=calendar_id).execute()
        )
        needs_patch = entry.get("hidden") or not entry.get("selected", True)
        if needs_patch and not dry_run:
            retryable_call(
                lambda: viewer_service.calendarList()
                .patch(
                    calendarId=calendar_id,
                    body={"hidden": False, "selected": True},
                )
                .execute()
            )
        return bool(needs_patch)
    except HttpError as exc:
        if exc.resp.status != 404:
            raise
        if not dry_run:
            retryable_call(
                lambda: viewer_service.calendarList()
                .insert(body={"id": calendar_id, "hidden": False, "selected": True})
                .execute()
            )
        return True


def share_one_source(
    key_path: str,
    source: WorkspaceUser,
    viewer: WorkspaceUser,
    role: str,
    dry_run: bool,
) -> tuple[int, int, list[tuple[str, str]]]:
    service = build_calendar_service(key_path, source.email)
    calendars = find_standard_calendars(service, source.full_name)
    print(f"[SOURCE] {source.full_name} <{source.email}> / calendars={len(calendars)}")
    updates = 0
    for summary, calendar_id in calendars:
        changed = ensure_acl_role(service, calendar_id, viewer.email, role, dry_run)
        state = "WOULD-UPDATE" if dry_run and changed else "UPDATED" if changed else "UNCHANGED"
        print(f"  [{state}] {summary} -> {viewer.email} role={role}")
        updates += int(changed)
    missing = len(RULES) - len(calendars)
    if missing:
        print(f"  [WARN] Missing {missing} standard calendar(s) for {source.full_name}")
    return updates, missing, calendars


def main() -> None:
    args = parse_args()
    try:
        key_path = get_key_path()
        directory_service = build_directory_service(key_path, args.directory_subject)
        users = list_domain_users(directory_service, args.domain)
        viewer = resolve_user(users, args.viewer, args.domain)
        sources = [resolve_user(users, token, args.domain) for token in args.source]

        print(f"[VIEWER] {viewer.full_name} <{viewer.email}> role={args.role}")
        print(f"[INFO] Source users: {len(sources)}")
        updates = 0
        missing = 0
        failed = 0
        viewer_service = (
            build_calendar_service(key_path, viewer.email) if args.subscribe_viewer else None
        )
        subscribed = 0
        for source in sources:
            try:
                source_updates, source_missing, calendars = share_one_source(
                    key_path, source, viewer, args.role, args.dry_run
                )
                updates += source_updates
                missing += source_missing
                if viewer_service:
                    for summary, calendar_id in calendars:
                        changed = ensure_viewer_subscription(
                            viewer_service, calendar_id, summary, args.dry_run
                        )
                        state = (
                            "WOULD-SUBSCRIBE"
                            if args.dry_run and changed
                            else "SUBSCRIBED"
                            if changed
                            else "VISIBLE"
                        )
                        print(f"  [{state}] viewer list: {summary}")
                        subscribed += int(changed)
            except Exception as exc:
                failed += 1
                print(f"[SOURCE-ERROR] {source.full_name} <{source.email}>: {exc}")

        action = "planned" if args.dry_run else "completed"
        print(
            f"[DONE] Calendar sharing {action}. sources={len(sources)} "
            f"acl_changes={updates} viewer_list_changes={subscribed} "
            f"missing_calendars={missing} failed={failed}"
        )
        if failed or missing:
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
