# Architecture

Calendar Provision is organized around provider boundaries rather than around
one vendor stack.

The reference stack is Google Calendar, Monday.com, and Slack, but the core
application model is more general:

```text
Calendar Provider
  -> people, groups, official calendars, event sync

Project Provider
  -> project code, PM, full project name, external item IDs

Notification Provider
  -> server restart, initial sync completion, global outage, recovery

Roster / Group Config
  -> selectable people, departments, temporary groups, display names
```

## Calendar Provider

The current implementation reads Google Calendar through a service account with
Domain-wide Delegation.

The viewer expects normalized events with this shape:

```json
{
  "id": "event-id",
  "title": "Meeting title",
  "start": "2026-01-01T10:00:00+09:00",
  "end": "2026-01-01T11:00:00+09:00",
  "allDay": false,
  "personName": "Researcher One",
  "personEmail": "researcher1@example.com",
  "calendarType": "project",
  "calendarTypeLabel": "[Project]",
  "color": "#2e7d32",
  "location": "",
  "mondayLinked": true,
  "mondayItemIds": ["12345678901"]
}
```

To adapt another calendar system, replace the calendar discovery and event sync
layer in `app/server.py` while preserving the normalized event response.

Likely alternatives:

- Microsoft Graph Calendar
- Naver Works Calendar
- CalDAV
- internal scheduling database

## Project Provider

The current implementation uses Monday.com to generate `config/projects.json`.
The runtime viewer only needs the generated JSON. It does not need to call
Monday directly.

Provider contract:

```json
{
  "projects": [
    {
      "code": "26P01",
      "pm": "Project Manager",
      "name": "Example Research Project",
      "monday_item_ids": ["12345678901"],
      "source_project_item_ids": ["98765432101"],
      "source_project_urls": [
        "https://example.monday.com/boards/1234567890/pulses/98765432101"
      ]
    }
  ]
}
```

The field name `monday_item_ids` is retained for compatibility with the current
reference implementation. If adapting to another provider, either keep this
field as `external_item_ids` in your generator and adjust `app/server.py`, or
write provider IDs into `monday_item_ids` for compatibility.

Likely alternatives:

- Jira issue IDs
- Asana task IDs
- Notion page/database item IDs
- ClickUp task IDs
- internal project/task IDs

## Notification Provider

The current implementation can send Slack DM lifecycle messages:

- server restart
- initial sync completion
- global sync outage
- global sync recovery

This is intentionally optional. If Slack variables are missing, notification
delivery is skipped.

To adapt another channel, replace `SlackNotifier` in `app/server.py` with a
provider implementing the same `send(text: str)` behavior.

Likely alternatives:

- Microsoft Teams webhook
- email
- generic webhook
- SMS or incident-management service

## Roster / Group Config

`config/org_groups.json` is local configuration, not a provider integration.
This is intentional: departments, temporary groups, executive views, and pilot
cohorts are often operational decisions rather than authoritative identity
system records.

If desired, this can be generated from:

- Google Workspace Directory
- Microsoft Entra ID
- HRIS data
- Slack/Teams user directories
- a manually maintained admin spreadsheet

## Reference Implementation Boundaries

Files that are intentionally provider-specific:

- `scripts/sync_project_catalog_from_monday.py`
- Google Calendar service/session code in `app/server.py`
- `SlackNotifier` in `app/server.py`

Files that should remain mostly provider-neutral:

- `app/static/index.html`
- `app/static/app.js`
- `app/static/styles.css`
- `config/org_groups.example.json`
- `config/projects.example.json`

When changing SaaS providers, preserve the UI contracts first, then replace the
provider adapters behind them.
