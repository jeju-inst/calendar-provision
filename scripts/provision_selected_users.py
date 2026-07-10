#!/usr/bin/env python3
"""
Provision standard calendars for a small, explicit list of Workspace users.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from provision_user_calendars import provision

DIR_SCOPES = ["https://www.googleapis.com/auth/admin.directory.user.readonly"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Provision calendars for an explicit list of users."
    )
    parser.add_argument(
        "--directory-subject",
        required=True,
        help="Impersonation subject for Directory API (admin account).",
    )
    parser.add_argument(
        "--users-file",
        required=True,
        help="Path to newline-delimited target user emails.",
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


def load_target_users(users_file: str) -> list[str]:
    with open(users_file, "r", encoding="utf-8") as handle:
        users = []
        seen = set()
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            email = line.lower()
            if email in seen:
                continue
            seen.add(email)
            users.append(email)
    if not users:
        raise RuntimeError(f"No target users found in {users_file}")
    return users


def get_full_name(directory_service, user_email: str) -> str:
    user = directory_service.users().get(userKey=user_email).execute()
    return user.get("name", {}).get("fullName") or user_email.split("@", 1)[0]


def main() -> None:
    args = parse_args()
    try:
        key_path = get_key_path()
        directory_service = build_directory_service(key_path, args.directory_subject)
        targets = load_target_users(args.users_file)
        print(f"[INFO] Target users loaded: {len(targets)}")

        success = 0
        failed = 0
        for user_email in targets:
            try:
                full_name = get_full_name(directory_service, user_email)
                print(f"[USER] {user_email} / {full_name}")
                provision(
                    user_email=user_email,
                    name=full_name,
                    admin_email=args.admin_email,
                    time_zone=args.time_zone,
                )
                success += 1
            except Exception as exc:
                failed += 1
                print(f"[USER-ERROR] {user_email}: {exc}")

        print(f"[DONE] Selected-user provisioning completed. success={success} failed={failed}")
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
