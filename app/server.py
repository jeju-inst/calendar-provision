#!/usr/bin/env python3
"""
Local calendar viewer for standardized JI calendars.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import sys
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Any
from urllib import request as urlrequest
from urllib.parse import parse_qs, urlparse
from wsgiref.simple_server import WSGIServer, make_server

from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

BASE_DIR = Path(__file__).resolve().parent
REPO_DIR = BASE_DIR.parent
STATIC_DIR = BASE_DIR / "static"
DEFAULT_CONFIG_PATH = REPO_DIR / "config" / "org_groups.json"
DEFAULT_PROJECTS_PATH = REPO_DIR / "config" / "projects.json"

CALENDAR_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
]

CALENDAR_RULES: tuple[tuple[str, str], ...] = (
    ("project", "[과제]"),
    ("work", "[업무]"),
    ("attendance", "[근태]"),
    ("other", "[기타]"),
)

IMPORTANT_CALENDAR_TYPE = "important"
IMPORTANT_CALENDAR_LABEL = "제주연구원"
IMPORTANT_CALENDAR_DISPLAY_NAME = "제주연구원"
IMPORTANT_CALENDAR_SUMMARIES = (
    "제주연구원 공식 일정",
    "중요 행사",
    "중요 행사 채널",
    "중요행사",
    "중요행사 채널",
    "[중요] 행사",
    "[중요행사]",
)

TYPE_COLORS = {
    "project": "#2e7d32",
    "work": "#1565c0",
    "attendance": "#ef6c00",
    "other": "#616161",
    "important": "#d93025",
}

SYNC_STAGGER_SECONDS = 1.5
BINDING_DISCOVERY_RETRY_ATTEMPTS = 3
BINDING_DISCOVERY_RETRY_SLEEP_SECONDS = 1.0
BINDING_DISCOVERY_ERROR_BACKOFF_SECONDS = 60.0
BINDING_DISCOVERY_BATCH_SIZE = 3
SYNC_BINDING_BATCH_SIZE = 12
GOOGLE_API_TIMEOUT_SECONDS = 30
GLOBAL_OUTAGE_FAILURE_THRESHOLD = 5
SLACK_ENV_PATH = Path(os.getenv("CALENDAR_VIEWER_SLACK_ENV", str(REPO_DIR / ".env")))
MONDAY_ITEM_ID_PATTERN = re.compile(r"[a-z0-9-]+\.monday\.com/(?:boards|pulse)/\d+/(?:pulses/)?(\d+)|(?:pulse|item)[_/ -]?id[:= ]+(\d+)", re.IGNORECASE)
TEST_RANGE_START = datetime(2026, 7, 1, tzinfo=UTC)
TEST_RANGE_END = datetime(2026, 8, 1, tzinfo=UTC)
DEFAULT_TEST_COHORT = (
    "director@example.com",
    "manager@example.com",
    "researcher1@example.com",
    "researcher2@example.com",
)
TEST_COHORT_ORDER = {email: index for index, email in enumerate(DEFAULT_TEST_COHORT)}

_ORIGINAL_GETADDRINFO = socket.getaddrinfo
_IPV4_ONLY_SUFFIXES = (".googleapis.com",)


def prefer_ipv4_for_google_apis() -> None:
    """Avoid long IPv6 stalls on Google API calls in the local network."""

    def getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        results = _ORIGINAL_GETADDRINFO(host, port, family, type, proto, flags)
        if isinstance(host, str) and host.endswith(_IPV4_ONLY_SUFFIXES):
            ipv4_results = [result for result in results if result[0] == socket.AF_INET]
            if ipv4_results:
                return ipv4_results
        return results

    socket.getaddrinfo = getaddrinfo


prefer_ipv4_for_google_apis()


@dataclass(frozen=True)
class CalendarBinding:
    person_email: str
    person_name: str
    calendar_type: str
    calendar_label: str
    calendar_id: str
    summary: str


@dataclass(frozen=True)
class Person:
    email: str
    name: str
    role: str
    calendar_name: str


@dataclass(frozen=True)
class Project:
    code: str
    name: str
    pm: str
    item_ids: tuple[str, ...]


class AppError(RuntimeError):
    pass


class SyncTokenExpired(RuntimeError):
    pass


class SlackNotifier:
    def __init__(self, env_path: Path):
        self.env_path = env_path
        self.token = os.getenv("SLACK_BOT_TOKEN") or self._env_value("SLACK_BOT_TOKEN")
        self.operator = os.getenv("GW_NOTIFY_OPERATOR") or self._env_value("GW_NOTIFY_OPERATOR")

    def _env_value(self, key: str) -> str:
        try:
            lines = self.env_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return ""
        prefix = f"{key}="
        for line in reversed(lines):
            if not line.startswith(prefix):
                continue
            return line[len(prefix) :].strip().strip("'\"")
        return ""

    def send(self, text: str) -> None:
        if not self.token or not self.operator:
            return
        payload = json.dumps({"channel": self.operator, "text": text}).encode("utf-8")
        req = urlrequest.Request(
            "https://slack.com/api/chat.postMessage",
            data=payload,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlrequest.urlopen(req, timeout=10) as response:
                body = response.read()
            data = json.loads(body.decode("utf-8"))
            if not data.get("ok"):
                print(f"Slack notification failed: {data}", file=sys.stderr)
        except Exception as exc:
            print(f"Slack notification failed: {exc}", file=sys.stderr)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local JI calendar viewer.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host. Default: 127.0.0.1")
    parser.add_argument("--port", type=int, default=8765, help="Bind port. Default: 8765")
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to the group/person settings JSON file.",
    )
    parser.add_argument(
        "--directory-subject",
        default=os.getenv("CALENDAR_VIEWER_DIRECTORY_SUBJECT", "calendar@example.com"),
        help="Workspace user to impersonate when reading calendars. Default: calendar@example.com",
    )
    parser.add_argument(
        "--demo-mode",
        action="store_true",
        help="Serve sample events without calling Google APIs.",
    )
    parser.add_argument(
        "--test-mode",
        action="store_true",
        help="Limit live sync to a small test cohort and July 2026 event range.",
    )
    parser.add_argument(
        "--test-person-limit",
        type=int,
        default=10,
        help="Number of people to include in --test-mode. Default: 10.",
    )
    parser.add_argument(
        "--projects",
        default=str(DEFAULT_PROJECTS_PATH),
        help="Path to project catalog JSON. Default: config/projects.json",
    )
    return parser.parse_args()


def read_text(path: Path, content_type: str) -> tuple[bytes, str]:
    return path.read_bytes(), content_type


def static_version(*paths: Path) -> str:
    newest = max(int(path.stat().st_mtime) for path in paths)
    return str(newest)


def read_index_html() -> bytes:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    version = static_version(STATIC_DIR / "app.js", STATIC_DIR / "styles.css")
    html = html.replace("/static/styles.css", f"/static/styles.css?v={version}")
    html = html.replace("/static/app.js", f"/static/app.js?v={version}")
    return html.encode("utf-8")


def json_response(payload: Any, status: str = "200 OK") -> tuple[str, list[tuple[str, str]], bytes]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = [
        ("Content-Type", "application/json; charset=utf-8"),
        ("Content-Length", str(len(body))),
        ("Cache-Control", "no-store"),
    ]
    return status, headers, body


def text_response(text: str, status: str = "200 OK") -> tuple[str, list[tuple[str, str]], bytes]:
    body = text.encode("utf-8")
    headers = [
        ("Content-Type", "text/plain; charset=utf-8"),
        ("Content-Length", str(len(body))),
    ]
    return status, headers, body


def load_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise AppError(f"Settings file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    raw_people = data.get("people", [])
    raw_groups = data.get("groups", [])
    people: list[dict[str, Any]] = []
    people_by_email: dict[str, Person] = {}

    for raw in raw_people:
        email = str(raw["email"]).strip().lower()
        name = str(raw["name"]).strip()
        role = str(raw.get("role", "research")).strip()
        calendar_name = str(raw.get("calendar_name") or name).strip()
        person = Person(email=email, name=name, role=role, calendar_name=calendar_name)
        people_by_email[email] = person
        people.append(
            {
                "email": email,
                "name": name,
                "role": role,
                "calendar_name": calendar_name,
            }
        )

    groups: list[dict[str, Any]] = []
    for raw in raw_groups:
        group_id = str(raw["id"]).strip()
        name = str(raw["name"]).strip()
        members = [str(email).strip().lower() for email in raw.get("members", [])]
        missing = [email for email in members if email not in people_by_email]
        if missing:
            raise AppError(f"Group {group_id} references unknown people: {', '.join(missing)}")
        groups.append(
            {
                "id": group_id,
                "name": name,
                "members": members,
                "member_count": len(members),
            }
        )

    return {
        "title": data.get("title", "Calendar Viewer"),
        "timezone": data.get("timezone", "Asia/Seoul"),
        "people": people,
        "groups": groups,
        "people_by_email": people_by_email,
    }


def limit_settings_to_test_cohort(settings: dict[str, Any], limit: int) -> dict[str, Any]:
    allowed = set(DEFAULT_TEST_COHORT[:limit])
    people = sorted(
        [person for person in settings["people"] if person["email"] in allowed],
        key=lambda person: TEST_COHORT_ORDER.get(person["email"], 999),
    )
    people_by_email = {
        person["email"]: settings["people_by_email"][person["email"]]
        for person in people
    }
    groups = []
    for group in settings["groups"]:
        members = [email for email in group["members"] if email in allowed]
        if not members:
            continue
        next_group = dict(group)
        next_group["members"] = members
        next_group["member_count"] = len(members)
        groups.append(next_group)
    return {
        **settings,
        "title": f"🚧 {settings['title']} 테스트",
        "people": people,
        "groups": groups,
        "people_by_email": people_by_email,
    }


def load_projects(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        raw_projects = json.load(handle)
    if isinstance(raw_projects, dict):
        raw_projects = raw_projects.get("projects", [])
    projects: list[Project] = []
    for raw in raw_projects:
        code = str(raw.get("code") or raw.get("project_code") or "").strip()
        if not code:
            continue
        item_ids = raw.get("item_ids") or raw.get("monday_item_ids") or raw.get("ids") or []
        item_ids = tuple(str(item_id).strip() for item_id in item_ids if str(item_id).strip())
        projects.append(
            Project(
                code=code,
                name=str(raw.get("name") or raw.get("project_name") or "").strip(),
                pm=str(raw.get("pm") or raw.get("pm_name") or "").strip(),
                item_ids=item_ids,
            )
        )
    projects.sort(key=lambda project: project.code)
    return [
        {
            "code": project.code,
            "name": project.name,
            "pm": project.pm,
            "item_ids": list(project.item_ids),
            "label": " - ".join(part for part in [project.code, project.pm, project.name] if part),
        }
        for project in projects
    ]


def extract_monday_item_ids(text: str) -> set[str]:
    item_ids: set[str] = set()
    for match in MONDAY_ITEM_ID_PATTERN.finditer(text or ""):
        for group in match.groups():
            if group:
                item_ids.add(group)
    return item_ids


def credential_key_path() -> str:
    key_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not key_path:
        raise AppError("GOOGLE_APPLICATION_CREDENTIALS is not set.")
    expanded = os.path.expanduser(key_path)
    if not os.path.exists(expanded):
        raise AppError(f"Credential file not found: {expanded}")
    return expanded


def build_calendar_service(key_path: str, subject: str):
    if not subject:
        raise AppError("Calendar subject is not set.")
    creds = service_account.Credentials.from_service_account_file(
        key_path,
        scopes=CALENDAR_SCOPES,
        subject=subject,
    )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


class CalendarRepository:
    def __init__(
        self,
        settings: dict[str, Any],
        projects: list[dict[str, Any]],
        key_path: str | None,
        subject: str,
        demo_mode: bool,
        config_path: Path,
        test_mode: bool = False,
    ):
        self.settings = settings
        self.projects = projects
        self.project_item_ids_by_code = {
            project["code"]: set(project.get("item_ids", []))
            for project in projects
        }
        self.key_path = key_path
        self.subject = subject
        self.demo_mode = demo_mode
        self.config_path = config_path
        self.test_mode = test_mode
        self._services: dict[str, Any] = {}
        self._sessions: dict[str, AuthorizedSession] = {}
        self._calendar_cache: dict[str, list[dict[str, str]]] = {}
        self._lock = threading.RLock()
        self._bindings: list[CalendarBinding] = []
        self._events_by_calendar: dict[str, dict[str, dict[str, Any]]] = {}
        self._sync_tokens: dict[str, str] = {}
        self._sync_meta: dict[str, dict[str, Any]] = {}
        self._last_error: str | None = None
        self._sync_cursor = 0
        self._notifier = SlackNotifier(SLACK_ENV_PATH)
        self._startup_notified = False
        self._initial_sync_notified = False
        self._global_outage_active = False
        self._consecutive_sync_failures = 0
        self._last_sync_success_at: str | None = None
        self._last_global_error: str | None = None
        self._worker_started = False
        self._worker_thread: threading.Thread | None = None
        self._binding_errors: dict[str, str] = {}
        self._binding_attempted_ok: set[str] = set()
        self._binding_retry_after: dict[str, float] = {}
        self._binding_discovery_running = False

    def setup_state(self) -> dict[str, Any]:
        ready = self.demo_mode or bool(self.key_path)
        with self._lock:
            synced = sum(1 for meta in self._sync_meta.values() if meta.get("last_synced"))
            last_synced_values = [meta.get("last_synced") for meta in self._sync_meta.values() if meta.get("last_synced")]
            last_synced = max(last_synced_values) if last_synced_values else None
            cached = len(self._bindings)
            sync_percent = 0 if cached == 0 else min(100, int((synced / cached) * 100))
            initial_sync_complete = self._initial_sync_complete_locked()
        return {
            "demo_mode": self.demo_mode,
            "google_ready": ready,
            "directory_subject": self.subject,
            "config_path": str(self.config_path),
            "cached_calendars": cached,
            "synced_calendars": synced,
            "sync_percent": sync_percent,
            "initial_sync_complete": initial_sync_complete,
            "global_outage": self._global_outage_active,
            "last_synced_at": last_synced,
            "last_error": self._last_error,
        }

    def _initial_sync_complete_locked(self) -> bool:
        if self.demo_mode:
            return True
        people_emails = set(self.settings["people_by_email"])
        attempted_people = self._binding_attempted_ok | set(self._binding_errors)
        subject_attempted = self.subject in self._binding_attempted_ok or self.subject in self._binding_errors
        if not subject_attempted or not people_emails.issubset(attempted_people):
            return False
        return all(meta.get("last_synced") for meta in self._sync_meta.values())

    def diagnostics(self) -> dict[str, Any]:
        with self._lock:
            bindings_by_person: dict[str, list[CalendarBinding]] = {}
            for binding in self._bindings:
                bindings_by_person.setdefault(binding.person_email, []).append(binding)

            people = []
            for person in self.settings["people"]:
                found = bindings_by_person.get(person["email"], [])
                found_types = sorted(binding.calendar_type for binding in found)
                missing_types = sorted(
                    type_id for type_id, _prefix in CALENDAR_RULES if type_id not in found_types
                )
                binding_error = self._binding_errors.get(person["email"])
                synced = sum(
                    1
                    for binding in found
                    if self._sync_meta.get(binding.calendar_id, {}).get("last_synced")
                )
                people.append(
                    {
                        "email": person["email"],
                        "name": person["name"],
                        "role": person["role"],
                        "found_types": found_types,
                        "missing_types": missing_types,
                        "found_count": len(found),
                        "synced_count": synced,
                        "binding_error": binding_error,
                    }
                )

            groups = []
            for group in self.settings["groups"]:
                missing_people = []
                ready_people = 0
                for email in group["members"]:
                    found = bindings_by_person.get(email, [])
                    if len(found) == len(CALENDAR_RULES):
                        ready_people += 1
                    else:
                        missing_people.append(email)
                groups.append(
                    {
                        "id": group["id"],
                        "name": group["name"],
                        "member_count": group["member_count"],
                        "ready_people": ready_people,
                        "missing_people": missing_people,
                    }
                )

        return {
            "groups": groups,
            "people": people,
        }

    def start_background_sync(self) -> None:
        if self.demo_mode or self._worker_started:
            return
        self._worker_started = True
        if not self._startup_notified:
            self._startup_notified = True
            self._notifier.send("일정조회 서버 재시작됨. 초기 동기화를 시작합니다.")
        self._worker_thread = threading.Thread(target=self._sync_loop, name="calendar-sync", daemon=True)
        self._worker_thread.start()

    def _record_google_success(self) -> None:
        notify_recovered = False
        with self._lock:
            self._last_sync_success_at = datetime.now(UTC).isoformat()
            self._consecutive_sync_failures = 0
            self._last_global_error = None
            if self._global_outage_active:
                self._global_outage_active = False
                notify_recovered = True
        if notify_recovered:
            self._notifier.send("일정조회 전역 동기화 장애 해소. 동기화가 재개되었습니다.")

    def _record_google_failure(self, exc: Exception) -> None:
        message = str(exc)
        notify_outage = False
        with self._lock:
            self._consecutive_sync_failures += 1
            self._last_global_error = message
            if (
                not self._global_outage_active
                and self._consecutive_sync_failures >= GLOBAL_OUTAGE_FAILURE_THRESHOLD
            ):
                self._global_outage_active = True
                notify_outage = True
        if notify_outage:
            self._notifier.send(f"일정조회 전역 동기화 장애 감지: {message}")

    def _maybe_notify_initial_sync_complete(self) -> None:
        notify_complete = False
        with self._lock:
            if not self._initial_sync_notified and self._initial_sync_complete_locked():
                self._initial_sync_notified = True
                notify_complete = True
                people_count = len(self.settings["people_by_email"])
                calendar_count = len(self._bindings)
            else:
                people_count = 0
                calendar_count = 0
        if notify_complete:
            self._notifier.send(f"일정조회 초기 동기화 완료. 대상 {people_count}명, 캘린더 {calendar_count}개.")

    def service_for(self, subject: str):
        if self.demo_mode:
            return []
        if not self.key_path:
            raise AppError("Google credentials are not configured.")
        if subject not in self._services:
            self._services[subject] = build_calendar_service(self.key_path, subject)
        return self._services[subject]

    def session_for(self, subject: str) -> AuthorizedSession:
        if self.demo_mode:
            raise AppError("Google API session is unavailable in demo mode.")
        if not self.key_path:
            raise AppError("Google credentials are not configured.")
        if subject not in self._sessions:
            creds = service_account.Credentials.from_service_account_file(
                self.key_path,
                scopes=CALENDAR_SCOPES,
                subject=subject,
            )
            self._sessions[subject] = AuthorizedSession(creds)
        return self._sessions[subject]

    def list_owner_calendars(self, subject: str) -> list[dict[str, str]]:
        if self.demo_mode:
            return []
        cache_key = subject
        if cache_key in self._calendar_cache:
            return self._calendar_cache[cache_key]
        calendars: list[dict[str, str]] = []
        page_token = None
        session = self.session_for(subject)
        while True:
            params = {
                "minAccessRole": "owner",
                "maxResults": 250,
            }
            if page_token:
                params["pageToken"] = page_token
            response = session.get(
                "https://www.googleapis.com/calendar/v3/users/me/calendarList",
                params=params,
                timeout=GOOGLE_API_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            data = response.json()
            self._record_google_success()
            for item in data.get("items", []):
                calendars.append(
                    {
                        "id": item["id"],
                        "summary": item.get("summary", ""),
                    }
                )
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        self._calendar_cache[cache_key] = calendars
        return calendars

    def discover_bindings_for_person(self, person: Person) -> list[CalendarBinding]:
        calendars = self.list_owner_calendars(person.email)
        matched: list[CalendarBinding] = []
        for type_id, prefix in CALENDAR_RULES:
            summary = f"{prefix} {person.calendar_name}"
            for item in calendars:
                if item["summary"] == summary:
                    matched.append(
                        CalendarBinding(
                            person_email=person.email,
                            person_name=person.name,
                            calendar_type=type_id,
                            calendar_label=prefix,
                            calendar_id=item["id"],
                            summary=summary,
                        )
                    )
                    break
        return matched

    def discover_important_bindings(self) -> list[CalendarBinding]:
        calendars = self.list_owner_calendars(self.subject)
        summaries = set(IMPORTANT_CALENDAR_SUMMARIES)
        matched: list[CalendarBinding] = []
        for item in calendars:
            summary = item["summary"].strip()
            if summary not in summaries:
                continue
            matched.append(
                CalendarBinding(
                    person_email=self.subject,
                    person_name=IMPORTANT_CALENDAR_DISPLAY_NAME,
                    calendar_type=IMPORTANT_CALENDAR_TYPE,
                    calendar_label=IMPORTANT_CALENDAR_LABEL,
                    calendar_id=item["id"],
                    summary=summary,
                )
            )
        return matched

    def _ensure_bindings(self) -> None:
        if self.demo_mode:
            return
        now = time.monotonic()
        with self._lock:
            if self._binding_discovery_running:
                return
            pending = [
                person
                for person in self.settings["people_by_email"].values()
                if person.email not in self._binding_attempted_ok
                and self._binding_retry_after.get(person.email, 0.0) <= now
            ]
            binding_batch_size = 1 if self.test_mode else BINDING_DISCOVERY_BATCH_SIZE
            pending = pending[:binding_batch_size]
            important_pending = (
                self.subject not in self._binding_attempted_ok
                and self._binding_retry_after.get(self.subject, 0.0) <= now
            )
            if not pending and not important_pending:
                return
            self._binding_discovery_running = True

        try:
            if important_pending and not self.test_mode:
                last_error: Exception | None = None
                discovered: list[CalendarBinding] = []
                for attempt in range(BINDING_DISCOVERY_RETRY_ATTEMPTS):
                    try:
                        discovered = self.discover_important_bindings()
                        last_error = None
                        break
                    except Exception as exc:
                        last_error = exc
                        if attempt < BINDING_DISCOVERY_RETRY_ATTEMPTS - 1:
                            time.sleep(BINDING_DISCOVERY_RETRY_SLEEP_SECONDS)

                with self._lock:
                    if last_error is not None:
                        self._binding_errors[self.subject] = str(last_error)
                        self._binding_retry_after[self.subject] = time.monotonic() + BINDING_DISCOVERY_ERROR_BACKOFF_SECONDS
                    else:
                        known_calendar_ids = {binding.calendar_id for binding in self._bindings}
                        for binding in discovered:
                            if binding.calendar_id in known_calendar_ids:
                                continue
                            self._bindings.append(binding)
                            known_calendar_ids.add(binding.calendar_id)
                            self._events_by_calendar.setdefault(binding.calendar_id, {})
                            self._sync_meta.setdefault(
                                binding.calendar_id,
                                {"summary": binding.summary, "person": binding.person_name, "last_synced": None},
                            )
                        self._binding_attempted_ok.add(self.subject)
                        self._binding_errors.pop(self.subject, None)
                        self._binding_retry_after.pop(self.subject, None)
                if last_error is not None:
                    self._record_google_failure(last_error)
                else:
                    self._maybe_notify_initial_sync_complete()

            for person in pending:
                discovered: list[CalendarBinding] = []
                last_error: Exception | None = None
                for attempt in range(BINDING_DISCOVERY_RETRY_ATTEMPTS):
                    try:
                        discovered = self.discover_bindings_for_person(person)
                        last_error = None
                        break
                    except Exception as exc:
                        last_error = exc
                        if attempt < BINDING_DISCOVERY_RETRY_ATTEMPTS - 1:
                            time.sleep(BINDING_DISCOVERY_RETRY_SLEEP_SECONDS)

                with self._lock:
                    if last_error is not None:
                        self._binding_errors[person.email] = str(last_error)
                        self._binding_retry_after[person.email] = time.monotonic() + BINDING_DISCOVERY_ERROR_BACKOFF_SECONDS
                        continue

                    known_calendar_ids = {binding.calendar_id for binding in self._bindings}
                    for binding in discovered:
                        if binding.calendar_id in known_calendar_ids:
                            continue
                        self._bindings.append(binding)
                        known_calendar_ids.add(binding.calendar_id)
                        self._events_by_calendar.setdefault(binding.calendar_id, {})
                        self._sync_meta.setdefault(
                            binding.calendar_id,
                            {"summary": binding.summary, "person": binding.person_name, "last_synced": None},
                        )
                    self._binding_attempted_ok.add(person.email)
                    self._binding_errors.pop(person.email, None)
                    self._binding_retry_after.pop(person.email, None)
                if last_error is not None:
                    self._record_google_failure(last_error)
                else:
                    self._maybe_notify_initial_sync_complete()

            if important_pending and self.test_mode:
                last_error: Exception | None = None
                discovered: list[CalendarBinding] = []
                for attempt in range(BINDING_DISCOVERY_RETRY_ATTEMPTS):
                    try:
                        discovered = self.discover_important_bindings()
                        last_error = None
                        break
                    except Exception as exc:
                        last_error = exc
                        if attempt < BINDING_DISCOVERY_RETRY_ATTEMPTS - 1:
                            time.sleep(BINDING_DISCOVERY_RETRY_SLEEP_SECONDS)

                with self._lock:
                    if last_error is not None:
                        self._binding_errors[self.subject] = str(last_error)
                        self._binding_retry_after[self.subject] = time.monotonic() + BINDING_DISCOVERY_ERROR_BACKOFF_SECONDS
                    else:
                        known_calendar_ids = {binding.calendar_id for binding in self._bindings}
                        for binding in discovered:
                            if binding.calendar_id in known_calendar_ids:
                                continue
                            self._bindings.append(binding)
                            known_calendar_ids.add(binding.calendar_id)
                            self._events_by_calendar.setdefault(binding.calendar_id, {})
                            self._sync_meta.setdefault(
                                binding.calendar_id,
                                {"summary": binding.summary, "person": binding.person_name, "last_synced": None},
                            )
                        self._binding_attempted_ok.add(self.subject)
                        self._binding_errors.pop(self.subject, None)
                        self._binding_retry_after.pop(self.subject, None)
                if last_error is not None:
                    self._record_google_failure(last_error)
                else:
                    self._maybe_notify_initial_sync_complete()
        finally:
            with self._lock:
                self._binding_discovery_running = False

    def _sync_loop(self) -> None:
        while True:
            try:
                self._ensure_bindings()
                bindings = self._next_sync_batch()
                if not bindings:
                    time.sleep(2.0)
                    continue
                for binding in bindings:
                    self._sync_binding(binding)
                    self._maybe_notify_initial_sync_complete()
                    time.sleep(SYNC_STAGGER_SECONDS)
            except Exception as exc:
                with self._lock:
                    self._last_error = str(exc)
                self._record_google_failure(exc)
                time.sleep(5.0)

    def _next_sync_batch(self) -> list[CalendarBinding]:
        with self._lock:
            bindings = list(self._bindings)
            if not bindings:
                return []
            unsynced = [
                binding
                for binding in bindings
                if not self._sync_meta.get(binding.calendar_id, {}).get("last_synced")
            ]
            if unsynced:
                return unsynced[:SYNC_BINDING_BATCH_SIZE]

            start = self._sync_cursor % len(bindings)
            ordered = bindings[start:] + bindings[:start]
            batch = ordered[:SYNC_BINDING_BATCH_SIZE]
            self._sync_cursor = (start + len(batch)) % len(bindings)
            return batch

    def _sync_binding(self, binding: CalendarBinding) -> None:
        session = self.session_for(binding.person_email)
        sync_token = self._sync_tokens.get(binding.calendar_id)
        try:
            items, next_sync_token = self._fetch_events_page_set(session, binding, sync_token)
        except SyncTokenExpired:
            items, next_sync_token = self._fetch_events_page_set(session, binding, None)

        with self._lock:
            bucket = self._events_by_calendar.setdefault(binding.calendar_id, {})
            for item in items:
                event_id = item.get("id")
                if not event_id:
                    continue
                if item.get("status") == "cancelled":
                    bucket.pop(event_id, None)
                    continue
                person = self.settings["people_by_email"].get(
                    binding.person_email,
                    Person(
                        email=binding.person_email,
                        name=binding.person_name,
                        role="calendar",
                        calendar_name=binding.person_name,
                    ),
                )
                bucket[event_id] = normalize_event(item, person, binding.calendar_type)
            if next_sync_token:
                self._sync_tokens[binding.calendar_id] = next_sync_token
            self._sync_meta[binding.calendar_id] = {
                "summary": binding.summary,
                "person": binding.person_name,
                "last_synced": datetime.now(UTC).isoformat(),
            }
            self._last_error = None

    def _sync_important_bindings_if_needed(self) -> None:
        with self._lock:
            bindings = [
                binding
                for binding in self._bindings
                if binding.calendar_type == IMPORTANT_CALENDAR_TYPE
                and not self._sync_meta.get(binding.calendar_id, {}).get("last_synced")
            ]
        for binding in bindings:
            self._sync_binding(binding)

    def _fetch_events_page_set(self, session: AuthorizedSession, binding: CalendarBinding, sync_token: str | None) -> tuple[list[dict[str, Any]], str | None]:
        items: list[dict[str, Any]] = []
        page_token = None
        next_sync_token = None
        while True:
            params = {
                "calendarId": binding.calendar_id,
                "singleEvents": True,
                "showDeleted": True,
                "maxResults": 250,
                "pageToken": page_token,
                "quotaUser": binding.person_email,
            }
            if sync_token:
                params["syncToken"] = sync_token
            elif self.test_mode:
                params["timeMin"] = TEST_RANGE_START.isoformat()
                params["timeMax"] = TEST_RANGE_END.isoformat()
            response = session.get(
                f"https://www.googleapis.com/calendar/v3/calendars/{binding.calendar_id}/events",
                params=params,
                timeout=GOOGLE_API_TIMEOUT_SECONDS,
            )
            if response.status_code == 410:
                raise SyncTokenExpired()
            response.raise_for_status()
            data = response.json()
            self._record_google_success()
            items.extend(data.get("items", []))
            page_token = data.get("nextPageToken")
            next_sync_token = data.get("nextSyncToken", next_sync_token)
            if not page_token:
                break
        return items, next_sync_token

    def load_events(
        self,
        emails: list[str],
        start: datetime,
        end: datetime,
        selected_types: set[str],
        selected_project_codes: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        if not selected_types:
            return []
        if self.demo_mode:
            return self._demo_events(emails, start, selected_types)

        selected_email_set = set(emails)
        selected_type_set = set(selected_types)
        selected_project_codes = selected_project_codes or set()
        selected_project_item_ids = set()
        for code in selected_project_codes:
            selected_project_item_ids.update(self.project_item_ids_by_code.get(code, set()))
        events: list[dict[str, Any]] = []
        with self._lock:
            for binding in self._bindings:
                if binding.calendar_type not in selected_type_set:
                    continue
                is_important = binding.calendar_type == IMPORTANT_CALENDAR_TYPE
                person_selected = binding.person_email in selected_email_set
                if not is_important and not person_selected and not selected_project_item_ids:
                    continue
                bucket = self._events_by_calendar.get(binding.calendar_id, {})
                for event in bucket.values():
                    if event_in_range(event, start, end):
                        event_matches_project = False
                        if selected_project_item_ids:
                            event_matches_project = not set(event.get("mondayItemIds", [])).isdisjoint(selected_project_item_ids)
                        if not is_important and not person_selected and not event_matches_project:
                            continue
                        events.append(event)
        return sorted(events, key=event_sort_key)

    def _demo_events(
        self,
        emails: list[str],
        start: datetime,
        selected_types: set[str],
    ) -> list[dict[str, Any]]:
        people = [self.settings["people_by_email"][email] for email in emails if email in self.settings["people_by_email"]]
        sample_events: list[dict[str, Any]] = []
        base = start.astimezone(UTC)
        if IMPORTANT_CALENDAR_TYPE in selected_types:
            event_start = base + timedelta(days=1, hours=10)
            sample_events.append(
                {
                    "id": "demo-important",
                    "title": "기관 주요 행사",
                    "start": event_start.isoformat(),
                    "end": (event_start + timedelta(hours=2)).isoformat(),
                    "allDay": False,
                    "personName": IMPORTANT_CALENDAR_DISPLAY_NAME,
                    "personEmail": self.subject,
                    "calendarType": IMPORTANT_CALENDAR_TYPE,
                    "calendarTypeLabel": IMPORTANT_CALENDAR_LABEL,
                    "color": TYPE_COLORS[IMPORTANT_CALENDAR_TYPE],
                    "location": "",
                }
            )
        for idx, person in enumerate(people[:4]):
            for offset, (type_id, label) in enumerate(CALENDAR_RULES):
                if type_id not in selected_types:
                    continue
                seed = f"{person.email}:{type_id}:{offset}".encode("utf-8")
                digest = hashlib.sha256(seed).digest()
                day_offset = digest[0] % 5
                start_hour = 8 + (digest[1] % 11)
                start_minute = 30 if digest[2] % 2 else 0
                duration_minutes = 60 + ((digest[3] % 3) * 30)
                event_start = base + timedelta(
                    days=day_offset,
                    hours=start_hour,
                    minutes=start_minute,
                )
                event_end = event_start + timedelta(minutes=duration_minutes)
                sample_events.append(
                    {
                        "id": f"demo-{person.email}-{type_id}-{offset}",
                        "title": f"{person.name} {label} 일정",
                        "start": event_start.isoformat(),
                        "end": event_end.isoformat(),
                        "allDay": False,
                        "personName": person.name,
                        "personEmail": person.email,
                        "calendarType": type_id,
                        "calendarTypeLabel": label,
                        "color": TYPE_COLORS[type_id],
                        "location": "",
                    }
                )
        return sorted(sample_events, key=event_sort_key)


def event_sort_key(event: dict[str, Any]) -> tuple[str, str]:
    return (event["start"], event["title"])


def event_in_range(event: dict[str, Any], start: datetime, end: datetime) -> bool:
    event_start = parse_iso_datetime(event["start"], start)
    raw_end = event.get("end") or event["start"]
    event_end = parse_iso_datetime(raw_end, event_start)
    return event_start < end and event_end > start


def normalize_event(item: dict[str, Any], person: Person, calendar_type: str) -> dict[str, Any]:
    start_info = item.get("start", {})
    end_info = item.get("end", {})
    is_all_day = "date" in start_info
    if calendar_type == IMPORTANT_CALENDAR_TYPE:
        label = IMPORTANT_CALENDAR_LABEL
    else:
        label = next(prefix for type_id, prefix in CALENDAR_RULES if type_id == calendar_type)
    description = item.get("description") or ""
    monday_item_ids = sorted(extract_monday_item_ids(description))
    return {
        "id": item.get("id"),
        "title": item.get("summary") or "(제목 없음)",
        "start": start_info.get("dateTime") or start_info.get("date"),
        "end": end_info.get("dateTime") or end_info.get("date"),
        "allDay": is_all_day,
        "personName": person.name,
        "personEmail": person.email,
        "calendarType": calendar_type,
        "calendarTypeLabel": label,
        "color": TYPE_COLORS[calendar_type],
        "location": item.get("location", ""),
        "mondayLinked": "example.monday.com" in description and "✅" in description,
        "mondayItemIds": monday_item_ids,
    }


def parse_iso_datetime(value: str, default: datetime) -> datetime:
    if not value:
        return default
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(value)
    except ValueError as exc:
        raise AppError(f"Invalid datetime: {value}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def build_app(config_path: Path, directory_subject: str, demo_mode: bool, projects_path: Path, test_mode: bool = False, test_person_limit: int = 10):
    settings = load_settings(config_path)
    if test_mode:
        settings = limit_settings_to_test_cohort(settings, test_person_limit)
    projects = load_projects(projects_path)
    key_path = None if demo_mode else credential_key_path()
    repo = CalendarRepository(settings, projects, key_path, directory_subject, demo_mode, config_path, test_mode=test_mode)
    repo.start_background_sync()

    def app(environ, start_response):
        try:
            method = environ["REQUEST_METHOD"]
            response_body = method != "HEAD"
            parsed = urlparse(environ.get("PATH_INFO", "/"))
            query = parse_qs(environ.get("QUERY_STRING", ""))
            if method not in {"GET", "HEAD"}:
                status, headers, body = text_response("Method Not Allowed", "405 Method Not Allowed")
            elif parsed.path == "/":
                body, content_type = read_index_html(), "text/html; charset=utf-8"
                status, headers = "200 OK", [
                    ("Content-Type", content_type),
                    ("Content-Length", str(len(body))),
                    ("Cache-Control", "no-store"),
                ]
            elif parsed.path == "/favicon.ico":
                body, content_type = read_text(STATIC_DIR / "icons" / "calendar_icon_dark_68.png", "image/png")
                status, headers = "200 OK", [
                    ("Content-Type", content_type),
                    ("Content-Length", str(len(body))),
                    ("Cache-Control", "no-store"),
                ]
            elif parsed.path == "/healthz":
                status, headers, body = text_response("ok\n")
            elif parsed.path == "/static/app.js":
                body, content_type = read_text(STATIC_DIR / "app.js", "application/javascript; charset=utf-8")
                status, headers = "200 OK", [
                    ("Content-Type", content_type),
                    ("Content-Length", str(len(body))),
                    ("Cache-Control", "no-store"),
                ]
            elif parsed.path == "/static/styles.css":
                body, content_type = read_text(STATIC_DIR / "styles.css", "text/css; charset=utf-8")
                status, headers = "200 OK", [
                    ("Content-Type", content_type),
                    ("Content-Length", str(len(body))),
                    ("Cache-Control", "no-store"),
                ]
            elif parsed.path.startswith("/static/icons/"):
                icon_path = STATIC_DIR / "icons" / Path(parsed.path).name
                if icon_path.exists() and icon_path.suffix == ".png":
                    body, content_type = read_text(icon_path, "image/png")
                    status, headers = "200 OK", [
                        ("Content-Type", content_type),
                        ("Content-Length", str(len(body))),
                        ("Cache-Control", "no-store"),
                    ]
                else:
                    status, headers, body = text_response("Not Found", "404 Not Found")
            elif parsed.path == "/api/config":
                payload = {
                    "title": settings["title"],
                    "timezone": settings["timezone"],
                    "groups": settings["groups"],
                    "people": settings["people"],
                    "projects": projects,
                    "test_mode": test_mode,
                    "setup": repo.setup_state(),
                }
                status, headers, body = json_response(payload)
            elif parsed.path == "/api/events":
                selected = [email.strip().lower() for email in ",".join(query.get("emails", [])).split(",") if email.strip()]
                type_filter = {value.strip() for value in ",".join(query.get("types", [])).split(",") if value.strip()}
                project_codes = {value.strip() for value in ",".join(query.get("project_codes", [])).split(",") if value.strip()}
                now = datetime.now(UTC)
                start = parse_iso_datetime(query.get("start", [""])[0], now)
                end = parse_iso_datetime(query.get("end", [""])[0], now + timedelta(days=7))
                if test_mode:
                    start = max(start, TEST_RANGE_START)
                    end = min(end, TEST_RANGE_END)
                payload = {
                    "events": repo.load_events(selected, start, end, type_filter, project_codes),
                    "requested_emails": selected,
                    "requested_project_codes": sorted(project_codes),
                }
                status, headers, body = json_response(payload)
            elif parsed.path == "/api/diagnostics":
                status, headers, body = json_response(repo.diagnostics())
            else:
                status, headers, body = text_response("Not Found", "404 Not Found")
        except AppError as exc:
            status, headers, body = json_response({"error": str(exc)}, "400 Bad Request")
        except HttpError as exc:
            try:
                detail = json.loads(exc.content.decode("utf-8"))
            except Exception:
                detail = {"message": str(exc)}
            status, headers, body = json_response({"error": "Google API request failed", "detail": detail}, "502 Bad Gateway")
        except Exception as exc:
            status, headers, body = json_response({"error": str(exc)}, "500 Internal Server Error")

        start_response(status, headers)
        return [body if response_body else b""]

    return app


class ThreadedWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class IPv6LoopbackWSGIServer(ThreadedWSGIServer):
    address_family = socket.AF_INET6

    def server_bind(self) -> None:
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        super().server_bind()


def main() -> None:
    args = parse_args()
    app = build_app(
        Path(args.config),
        args.directory_subject,
        args.demo_mode,
        Path(args.projects),
        test_mode=args.test_mode,
        test_person_limit=args.test_person_limit,
    )
    ipv6_server = make_server("::1", args.port, app, server_class=IPv6LoopbackWSGIServer)
    ipv6_thread = threading.Thread(target=ipv6_server.serve_forever, name="calendar-viewer-ipv6", daemon=True)
    ipv6_thread.start()
    with make_server(args.host, args.port, app, server_class=ThreadedWSGIServer) as server:
        print(f"[INFO] Calendar viewer running at http://{args.host}:{args.port}")
        print(f"[INFO] IPv6 loopback enabled at http://[::1]:{args.port}")
        print(f"[INFO] Config: {args.config}")
        if args.demo_mode:
            print("[INFO] Demo mode is enabled.")
        elif not args.directory_subject:
            print("[WARN] Live Google mode needs --directory-subject or CALENDAR_VIEWER_DIRECTORY_SUBJECT.")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n[INFO] Stopped.")
        finally:
            ipv6_server.shutdown()
            ipv6_server.server_close()


if __name__ == "__main__":
    try:
        main()
    except AppError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)
