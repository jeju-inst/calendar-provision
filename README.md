# Calendar Provision

Calendar Provision is a small organizational calendar toolkit and viewer.

It has two parts:

- admin scripts for provisioning a standardized set of calendars per user
- a lightweight web viewer for group, person, and project-based schedule lookup

The included reference implementation uses:

- Google Calendar as the calendar provider
- Monday.com as the project catalog provider
- Slack as the notification provider

Those services are deliberately treated as replaceable adapters. See
`docs/architecture.md` for the provider boundaries and adaptation notes.

This public repository is a sanitized snapshot. It intentionally does not
include real staff rosters, production launchd files, private domains, tokens,
or operating notes from the source deployment.

## Core Model

The app assumes three operational data sources:

- **Calendar Provider**: returns events for people, groups, and shared official calendars
- **Project Provider**: returns project code, PM, title, and external item IDs
- **Notification Provider**: sends lifecycle and sync health notifications

The viewer joins calendar events to project catalog entries by external item IDs
found in event descriptions or metadata.

## Reference Standard Calendar Model

Each user can have four standardized calendars:

- `[Project] Name`
- `[Work] Name`
- `[Attendance] Name`
- `[Other] Name`

The viewer can aggregate those calendars by group or by selected people.

## Project Filtering

The viewer can also load a project catalog from `config/projects.json`.
Calendar events are matched to projects by external item IDs embedded in event
descriptions. In the reference implementation, those IDs are Monday item IDs.

Use `config/projects.example.json` as the shape reference.

## Setup

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Create local configuration from the examples:

```bash
cp config/org_groups.example.json config/org_groups.json
cp config/projects.example.json config/projects.json
cp .env.example .env
```

Run demo mode:

```bash
.venv/bin/python app/server.py --demo-mode
```

Run live mode:

```bash
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
export CALENDAR_VIEWER_DIRECTORY_SUBJECT=calendar-reader@example.com
.venv/bin/python app/server.py --host 127.0.0.1 --port 8765
```

## Project Catalog Sync

The included sync script reads project data from Monday.com. Treat it as the
reference implementation of the Project Provider contract. Equivalent scripts
can be written for Jira, Asana, Notion, ClickUp, or an internal database as long
as they write the same `config/projects.json` shape.

```bash
.venv/bin/python scripts/sync_project_catalog_from_monday.py
```

## Security Notes

Do not commit real service-account keys, `.env`, staff rosters, production
launchd plists, or generated operational catalog files.
