# 8. API reference

Every endpoint of the Django backend, with its permissions, inputs, outputs and errors. **Examples are produced from the real code with test fixture data** (not from a live system), and long lists are shortened with `"…"`. A test (`api/test_docs.py`) fails if a route is added to `config/urls.py` without being listed here.

## Conventions

| Topic | Rule |
|---|---|
| Base path | `/api`. All bodies and responses are JSON. |
| Authentication | `Authorization: Bearer <access token>` on every endpoint except the six `auth` endpoints. The access token lasts 10 minutes (`ACCESS_TOKEN_TTL`). Missing, invalid or expired: `401 {"error": "Authentication required"}`. |
| Who may call | **Signed in**: any valid account. **Staff**: `is_staff` is true (`python manage.py makestaff <username>`). Staff-only endpoints answer `403 {"error": "Forbidden"}` to others. |
| Errors | Always `{"error": "<message>"}` with the status codes listed per endpoint. |
| Cookie-using calls | Every `POST` under `/api/auth/` must send the header `X-Requested-With: fetch`, or it is refused with `403`. The refresh token is an HTTP-only, `SameSite=Strict` cookie scoped to `/api/auth` (and `Secure` when `DJANGO_DEBUG=0`). |
| Dates | `YYYY-MM-DD`. Timestamps in responses are ISO 8601 UTC with a trailing `Z`. Timestamps stored without a time zone in the sources are treated as UTC. |
| Client scoping | **Optional, off by default.** While `DJANGO_CLIENT_ACCESS_ENFORCED` is off, any signed-in user can read every client's data and `client` and `org` are only filters. When it is on, **staff** still see everything and everyone else sees **only the clients they have been given** (see *Access to clients* below). Naming a client or customer outside that is refused with `403 {"error": "Forbidden"}`, never shown as empty. Someone with no clients sees nothing. |

## Summary

| Method | Path | Who | Purpose |
|---|---|---|---|
| POST | `/api/auth/login` | public | Sign in |
| POST | `/api/auth/refresh` | cookie | New access token, rotated refresh cookie |
| POST | `/api/auth/logout` | cookie | End the session |
| POST | `/api/auth/register` | public (if open) | Create an account |
| POST | `/api/auth/forgot` | public | Email a password-reset link |
| POST | `/api/auth/reset` | public (link) | Set a new password |
| GET | `/api/sections/tickets` | signed in | Tickets created in a period |
| GET | `/api/sections/patches` | signed in | Patch compliance and scores |
| GET | `/api/sections/patch-entries` | signed in | Patches in one type and category |
| GET | `/api/sections/patch-overview` | signed in | Every client's own patch scores |
| GET | `/api/sections/patch-trend` | signed in | Saved score history |
| GET | `/api/sections/server-health` | signed in | SentinelOne agents |
| GET | `/api/sections/backups` | signed in | Cove backup devices and their health |
| GET | `/api/sections/backup-detail` | signed in | One Cove device in detail |
| GET | `/api/clients` | signed in | List the master clients you may see |
| POST | `/api/clients` | staff | Create a client, in the administrator's company |
| PUT | `/api/clients/{id}` | staff | Replace a client's details, links and aliases |
| POST | `/api/clients/{id}/claim` | staff | Claim a client for a company (the owner can give it to any company or take it back) |
| GET | `/api/companies` | staff | The companies (an administrator sees only their own) |
| POST | `/api/companies` | owner | Make a company |
| PUT | `/api/companies/{id}` | owner | Rename a company |
| PUT | `/api/users/{username}/company` | owner | Put a person in a company, or take them out |
| GET | `/api/clients/resolution` | staff | Unlinked customers (grouped) and discrepancies |
| POST | `/api/clients/resolve` | staff | Confirm groups into clients |
| GET | `/api/reports` | signed in | Your reports and shared ones |
| POST | `/api/reports` | signed in | Save a new report |
| PUT | `/api/reports/{id}` | owner, or staff for a shared one | Change a report |
| DELETE | `/api/reports/{id}` | owner, or staff for a shared one | Delete a report |
| GET | `/api/access` | staff | Who may see which clients, and whether the limits are on |
| PUT | `/api/access/{username}` | staff | Set the clients one person may see |
| GET | `/api/client-groups` | staff | The client groups (an administrator sees their own company's) |
| POST | `/api/client-groups` | staff | Make a client group |
| PUT | `/api/client-groups/{id}` | staff | Replace a client group |
| DELETE | `/api/client-groups/{id}` | staff | Delete a client group |
| POST | `/api/users` | staff | Make a person, with a one-time link to choose their password |
| PUT | `/api/users/{username}` | staff | Change a person: name, email, active, client groups |
| POST | `/api/users/{username}/link` | staff | A fresh link for a person to choose a password |
| GET | `/api/weights` | signed in | Score weights |
| PUT | `/api/weights` | signed in (shared set: staff) | Replace a set of weights |
| GET | `/api/freshness` | signed in | When each page's data was last collected, and whether that is stale |
| GET | `/api/collectors/health` | staff | Collector status, freshness and alerts |
| GET | `/api/collectors/{job}/latest-log` | staff | Latest run log of one collector |
| GET | `/api/collectors/execution` | staff | Queue state, schedule and worker heartbeat |
| POST | `/api/collectors/run` | staff | Queue one collector or all |

"Staff" for the collector endpoints can also be satisfied in development by `DJANGO_DEV_COLLECTOR_HEALTH_FOR_ALL=1` while `DJANGO_DEBUG=1`.

---

## Authentication

### POST `/api/auth/login`
Body `{"username": "...", "password": "..."}`. Header `X-Requested-With: fetch`.

| Status | Meaning |
|---|---|
| 200 | `{"access": "<token>", "expires_in": 600, "user": {"username": "kev", "name": "kev", "is_staff": false, "is_owner": false, "company": {"id": 1, "name": "IP Capital"}, "scoped": false}}` plus the `refresh_token` cookie. `is_owner` is the platform owner; `company` is the person's company (or `null`); `scoped` is `true` when the person is limited to particular clients they were given (access enforced, and neither an administrator nor the owner) |
| 401 | `{"error": "Invalid username or password"}` |
| 403 | Missing `X-Requested-With` header |
| 429 | `{"error": "Too many failed attempts. Try again later."}` after 5 failures (`THROTTLE_MAX_ATTEMPTS`) for the same address and username within 15 minutes (`THROTTLE_WINDOW_SECONDS`). Even the right password is refused until the window passes. A successful sign-in resets the count. |

While `DJANGO_DEV_LOGIN_BYPASS=1` (honoured only with `DJANGO_DEBUG=1`; off by default) any username and password succeed and the user is created on first use.

### POST `/api/auth/refresh`
No body; needs the refresh cookie and the `X-Requested-With` header. Returns the same body as login and a **new** refresh cookie (every use rotates it). Replaying an already-rotated token within 10 seconds (`REFRESH_GRACE`) returns the same successor instead of failing, which covers a lost response or a parallel request. Replaying it later revokes the whole session family. `401 {"error": "Session expired"}` clears the cookie.

### POST `/api/auth/logout`
Revokes the cookie's token family and clears the cookie. `200 {}`.

### POST `/api/auth/register`
Body `{"username": "...", "email": "...", "password": "..."}`.

| Status | Meaning |
|---|---|
| 200 | Account created and signed in (same body as login) |
| 400 | `Username is required`, or the password failed Django's validators (at least 8 characters, not a common password, not all digits, not too similar to the username or email); the messages are joined in `error` |
| 403 | `Registration is closed`. Open when `DJANGO_DEBUG=1`; elsewhere set `DJANGO_REGISTRATION_OPEN=1` |
| 409 | `That username is taken` (case-insensitive) |

### POST `/api/auth/forgot`
Body `{"identifier": "<username or email>"}`. Always `200 {"message": "If that account exists, a reset link has been sent."}`, so it cannot be used to find out which accounts exist. An email with a link of the form `<FRONTEND_URL>/reset?uid=...&token=...` (valid one hour) is sent only if the account exists and has an email address. Every request counts towards the throttle: after 5 for the same address and identifier, `429 {"error": "Too many requests. Try again later."}`.

### POST `/api/auth/reset`
Body `{"uid": "...", "token": "...", "password": "..."}`. `200 {"message": "Password updated. You can sign in now."}` and all of the user's sessions are revoked. `400` for an invalid or expired link, or a password that fails the validators (the link stays usable after a weak-password rejection).

---

## Reports: `GET /api/sections/{name}`

Names: `tickets`, `patches`, `patch-entries`, `patch-overview`, `patch-trend`, `server-health`, `backups`, `backup-detail`. Unknown name: `404`. Signed in. The parameters `client` (master client IDs) and `org` (N-central org unit IDs) take **one number or several separated by commas** (`client=3,5`; at most 50, repeats ignored); `device` (a Cove device ID) takes one. Anything else is `400 {"error": "'client' must be a number, or up to 50 numbers separated by commas"}` (`'device' must be a number` for the device).

### `tickets`
Tickets **created** in a period, newest version of each (`DISTINCT ON (ticket_id)` ordered by `snapshot_id DESC` from `autotask.ticket`).

| Parameter | Required | Meaning |
|---|---|---|
| `from`, `to` | yes | Inclusive dates. `400` if missing, malformed, or `from` is after `to` |

The browser does all filtering, grouping, averaging and sorting from these rows; one request serves every chart, tile and the table.

```json
{
  "tickets": {
    "title": "Tickets",
    "columns": [
      {
        "key": "ticket_number",
        "label": "Ticket"
      },
      {
        "key": "company_name",
        "label": "Client"
      },
      {
        "key": "title",
        "label": "Title",
        "wrap": true
      },
      "…"
    ],
    "rows": [
      {
        "snapshot_id": 7,
        "ticket_id": 1,
        "ticket_number": "T20261003.0001",
        "company_id": 101,
        "company_name": "Acme",
        "title": "VPN down",
        "description": "d",
        "resolution": null,
        "ticket_type": "Incident",
        "priority": "High",
        "issue_type": "Network",
        "sub_issue_type": "VPN",
        "status": "In Progress",
        "sla_met": null,
        "first_response_met": null,
        "first_response_at": "2026-10-05T10:30:00Z",
        "first_response_due_at": "2026-10-05T10:00:00Z",
        "created_at": "2026-10-05T08:00:00Z",
        "completed_at": null,
        "response_hours": 2.5,
        "resolution_hours": null,
        "rowTone": "high",
        "rowStatus": "bad",
        "reason": "First response 30m after the due time",
        "technician_id": 7,
        "technician": "Thandi Nkosi",
        "client_id": 1
      }
    ]
  },
  "technician_available": true
}
```

Notes:
- `response_hours` is first response minus creation; `resolution_hours` is completion minus creation. Elapsed time, not validated SLA business-hours time. `null` when a timestamp is missing.
- `rowStatus: "bad"` with a `reason` marks a late first response (the only rule at present; add rules in `_row_reasons`). `rowTone` is the lower-cased priority.
- `priority`, `ticket_type`, `issue_type`, `sub_issue_type` and `company_name` are `"Unspecified"` when the source has NULL.
- `client_id` is the master client resolved from the Autotask company ID (or, failing that, the name or an alias); `null` when unmapped.
- **Technician:** `technician_available` is `false` today, because the Autotask collector does not capture assignments. Then every row has `technician: null` and there is no `technician` column. When the source has `autotask.ticket.assigned_resource_id` (see `Collector/AutotaskLight-main/sql/proposed/technician.sql`), it becomes `true`; `technician` is the name from `autotask.resource`, `Resource <id>` when the name is unknown, or `Unassigned`.

### `patches`
Installed/missing counts and compliance per patch type and category, from each device's newest usable N-central inventory, plus the scores calculated for the calling user.

| Parameter | Meaning |
|---|---|
| `client` | Limit to the N-central org units linked to this master client, or to the clients listed (`3,5`): their customers are added together. `mapped` is `false` when none of them has any |
| `org` | Limit to one N-central customer, or several (`10,30`), for customers not linked to a client yet. **Given with `client`, the two add together** (earlier versions let `client` win) |
| `weights` | A JSON object of weights, such as `{"third-party": 2}`: used instead of the caller's personal weights for this request, on top of the shared defaults. This is how a saved report's own weights apply. `400` unless it is a JSON object of numbers from 0 to 1000 (at most 500 keys, 20,000 characters) |

```json
{
  "types": [
    {
      "key": "windows-security",
      "label": "Windows Security Patching",
      "categories": [
        {
          "category_id": "g1",
          "category": "Security Updates",
          "installed": 8,
          "missing": 2,
          "total": 10,
          "compliance": 80.0,
          "previous": 60.0
        }
      ]
    },
    {
      "key": "windows-patching",
      "label": "Windows Patching",
      "categories": []
    },
    {
      "key": "third-party",
      "label": "Third Party Patching",
      "categories": [
        {
          "category_id": null,
          "category": "Uncategorised",
          "installed": 3,
          "missing": 1,
          "total": 4,
          "compliance": 75.0,
          "previous": null
        }
      ]
    }
  ],
  "rows": [
    {
      "group": true,
      "label": "Security Monitoring"
    },
    {
      "key": "windows-security",
      "label": "Windows Security Patching",
      "indent": true,
      "current": 80.0,
      "previous": 60.0
    },
    "…"
  ],
  "overall": {
    "current": 77.5,
    "previous": 60.0
  },
  "calculation": { "types": [ { "key": "windows-security", "label": "Windows Security Patching", "score": 93.33, "weight": 1, "counts": true, "share": 25.0, "reason": null,
      "weighted_total": 280.0, "weight_total": 3.0,
      "categories": [ { "category_id": "a", "category": "Cumulative Updates", "compliance": 96.0, "weight": 2, "counts": true, "share": 66.7, "reason": null }, "…" ] }, "…" ],
    "overall": { "score": 70.83, "weighted_total": 283.3333, "weight_total": 4.0 } },
  "client": null,
  "org": null,
  "mapped": true,
  "clients": [
    {
      "key": "client:1",
      "label": "Acme",
      "client": 1,
      "org": null,
      "linked": true,
      "devices": 8
    },
    {
      "key": "org:30",
      "label": "Blue Crane",
      "client": null,
      "org": 30,
      "linked": false,
      "devices": 4
    }
  ],
  "columns": [
    {
      "key": "category",
      "label": "Category",
      "type": "link"
    },
    {
      "key": "installed",
      "label": "Installed"
    },
    "…"
  ]
}
```

- `compliance` and `previous` are `(total - missing) / total * 100`, from the newest and the second-newest usable inventory per device. `null` when there is nothing to compare, never `0`.
- A type's score is the weighted average of its **categories'** compliance; `overall` is the weighted average of the type scores. Weights come from `/api/weights` (the caller's own, over the shared default, over `1`). A weight of `0` leaves an item out; an item with no value is skipped.
- **`calculation` is the working behind the scores** (SCORE-01), made by the same code and the same weights as `rows` and `overall`, so it always adds up to them (a test checks this on hundreds of generated cases). For each type: each category with its `compliance`, its `weight`, whether it `counts`, and its `share` of the type (its weight divided by the weights that count); the type's `score`, its own `weight`, and its `share` of the overall score; `weighted_total` and `weight_total` are the top and bottom of each weighted average. A category **counts** when it lists patches (it has a compliance) and its weight is above 0; otherwise `counts` is false and `reason` says `its weight is 0` or `it lists no patches`. A type counts when it has a score and its weight is above 0 (`reason`: `its weight is 0` or `none of its categories counts`). `overall.score` is the weighted average of the type scores **before they are rounded**. Everything is `null` when nothing counts, never `0`. The Overview shows this under Score Weights, for one client.
- `clients` feeds the page's client filter. It is built from where devices actually are (`ncentral.device`): org units linked to a master client are merged under its name (`linked: true`); the rest appear under their N-central customer name. Pick with `client=<client>` or `org=<org>`.
- A category weight key is `<type key>:<category id>` (empty after the colon when the category ID is `null`).

### `patch-entries`
The patches of one type and category, latest inventory per device. Fetched when a category is opened, because it can be large.

| Parameter | Required | Meaning |
|---|---|---|
| `type` | yes | `windows-security`, `windows-patching` or `third-party`. Anything else: `404` |
| `category` | no | The category ID as text; omit for patches with no category |
| `client`, `org` | no | As for `patches` |

```json
{
  "entries": {
    "title": "Patches",
    "columns": [
      {
        "key": "device",
        "label": "Device"
      },
      {
        "key": "title",
        "label": "Title",
        "wrap": true
      },
      "…"
    ],
    "rows": [
      {
        "run_id": 5,
        "category": "Security Updates",
        "category_id": "g1",
        "result": "Installed",
        "is_security": true,
        "is_third_party": false,
        "kb": "KB5000001",
        "device_id": 11,
        "device_name": "SRV-11",
        "title": "Cumulative update",
        "is_missing": true,
        "installed_at": null,
        "device": "SRV-11",
        "installed": false
      },
      "…"
    ]
  }
}
```

`installed` is always the exact opposite of `is_missing` (`null` when unknown). `device` is the device name, falling back to its ID.

### `patch-overview`
Every client's own scores, for the Overview: one group per client with the three patch types and the overall score (current and previous), computed with the caller's weights. It is two queries however many clients there are. Customers not linked to a client appear under their N-central name (`linked: false`). Clients are in name order, not by how well they are doing. Counts of the org units that belong to one client are added together (compliance is recomputed from the sums, not averaged). A device with no customer cannot be placed and is left out.

```json
{
  "clients": [
    {
      "key": "client:1",
      "label": "Acme",
      "client": 1,
      "org": null,
      "linked": true,
      "devices": 8,
      "rows": [
        {
          "key": "windows-security",
          "label": "Windows Security Patching",
          "indent": true,
          "current": 50.0,
          "previous": 40.0
        },
        {
          "key": "windows-patching",
          "label": "Windows Patching",
          "indent": true,
          "current": null,
          "previous": null
        },
        "…"
      ],
      "overall": {
        "current": 62.5,
        "previous": 40.0
      }
    },
    "…"
  ]
}
```

For someone limited to some clients the list holds only those clients, and the query itself is limited to their customers.

### `patch-trend`
Saved score history, one point per day (the latest snapshot that day). **Never invented:** with no snapshots it returns empty lists.

| Parameter | Meaning |
|---|---|
| `scope` | `overall` (default) or a type key such as `windows-security` |
| `client` | One master client, or several (`3,5`). Without it, the all-devices history. **Several give their combined history** (the Overview does not use this: it shows the trend for one client at a time, and says "Please select a single client" otherwise; a limited person with no client named gets the combined history of their own clients): for each run the saved counts of the clients are added together and scored with the weights saved for that run (so it is the combined position, not an average of their scores); a client with no snapshot for a run is simply not in it; the heading says `combined: N clients` |
| `org` | An unlinked customer has no history (snapshots are kept per client): alone, it returns an empty trend whose heading says to link the customer; with clients, it is left out and the heading says so |

```json
{ "heading": "Overall Score Trend", "type": "line", "x": ["2026-11-03", "2026-11-10"], "y": [88.4, 90.1] }
```
```json
{ "heading": "Overall score trend: no history yet", "type": "line", "x": [], "y": [] }
```

Snapshots are written by the worker after each successful N-central patch collection (or by `manage.py snapshot_scores`) using the **shared** weights, so a user's own weights never change saved history.

### `server-health`
The newest SentinelOne agent record per customer and agent, excluding decommissioned agents.

```json
{
  "servers": [
    {
      "agent_id": "a1",
      "customer_name": "Acme",
      "computer_name": "SRV-A",
      "network_status": "connected",
      "last_collected_at": "2026-11-10T04:48:29Z",
      "data_age_hours": 1.0,
      "machine_type": "server",
      "os_type": "windows",
      "agent_version": "23.4",
      "mitigation_mode": "protect",
      "is_up_to_date": true,
      "infected": false,
      "is_active": false,
      "client_id": 1,
      "patches": {
        "status": "matched",
        "device_name": "srv-a.acme.local",
        "collection": { "state": "ok", "attempted_at": "2026-11-11T06:10:00Z", "days_ago": 0.5 },
        "seen": { "at": "2026-11-10T06:00:00Z", "days_ago": 1.5 },
        "inventory": { "at": "2026-11-11T06:10:00Z", "days_ago": 0.5 },
        "categories": [
          { "key": "windows-security", "label": "Windows Security Patching", "last_installed_at": "2026-11-08T09:12:00Z", "days_since_last": 3.0,
            "next_due": null, "up_to_date": false, "missing": 2, "oldest_missing_days": 20.0, "overdue": true },
          { "key": "windows-patching", "label": "Windows Patching", "last_installed_at": "2026-11-01T09:12:00Z", "days_since_last": 10.0,
            "next_due": null, "up_to_date": true, "missing": 0, "oldest_missing_days": null, "overdue": false },
          { "key": "third-party", "label": "Third Party Patching", "last_installed_at": null, "days_since_last": null,
            "next_due": null, "up_to_date": false, "missing": 1, "oldest_missing_days": 5.0, "overdue": false }
        ]
      },
      "collected": [
        { "key": "sentinelone", "label": "SentinelOne", "what": "Security agent", "state": "found", "at": "2026-11-10T04:48:29Z", "days_ago": 0.1 },
        { "key": "ncentral-device", "label": "N-central", "what": "Device inventory", "state": "found", "at": "2026-11-10T06:00:00Z", "days_ago": 1.5 },
        { "key": "ncentral-patches", "label": "N-central", "what": "Patch inventory", "state": "found", "at": "2026-11-11T06:10:00Z", "days_ago": 0.5 },
        { "key": "cove", "label": "Cove", "what": "Backup", "state": "no-match", "at": null, "days_ago": null }
      ]
    },
    "…"
  ],
  "patch_info": { "available": true, "grace_days": 14, "matched": 2, "disconnected": 1 }
}
```

`data_age_hours` is now minus the snapshot's `captured_at`, **not** the agent's `last_active`. `client_id` is resolved from the SentinelOne site ID, falling back to the customer name or an alias.

**Patching (`patches`).** Each server also says what N-central knows about its patching, in the same three categories as the Overview. A SentinelOne endpoint and an N-central device share **no ID**, so a server is matched to a device **by computer name inside its own client** (case and any domain ending ignored: `srv-a.acme.local` is `SRV-A`; an IP address is kept whole), using each system's own customer link to find the client. Anything less certain shows no patch data and never a guess. `status` is:

| `status` | Meaning |
|---|---|
| `matched` | Exactly one device with that name in that client; `device_name` and `categories` are given |
| `no-match` | No device with that name in that client |
| `ambiguous` | More than one device has that name in that client; none is used |
| `unlinked` | The server is linked to no client, so there is nothing safe to match inside |
| `unavailable` | The patching could not be read (logged for the administrator, never sent to the browser); the rest of the page is unaffected |

For each category of a matched server (the three on the Overview, in the Overview's order) these facts are given, and **no overall "healthy" is decided**: what that means is for the owner to define later (all three categories, or only one).

| Field | Meaning |
|---|---|
| `last_installed_at`, `days_since_last` | **Last added:** the newest **install date** in the device's latest usable patch inventory. Installs N-central gives no date for are not counted, so a category can have none (`null`) |
| `next_due` | **Next due:** always `null` for now, because the patch schedule is not collected (SERVER-02). It will hold a date once it is |
| `up_to_date` | **Up to date:** `true` when the device's newest patch inventory lists patches of that kind and **none is missing**; `false` when some are; `null` when it lists none, so nothing can be said. It is the only meaning of "up to date" |
| `missing` | How many are missing, counted in that same inventory exactly as the Overview counts them |
| `oldest_missing_days` | How long the oldest missing patch has been missing, from the open entries of the patch backlog (`ncentral.patch_backlog`, where `first_missing_at` is when this system **first saw** it missing, so the age is a floor); `null` when nothing is missing |
| `overdue` | **True when something is missing and the oldest has been for more than `grace_days`** (14, kept in one place, `PATCH_GRACE_DAYS` in `data/servers.py`). There is no patch schedule, so this stands in for "expected but not applied". It can never be true for a category that is up to date |

`patch_info.matched` counts the servers that were matched, and `patch_info.disconnected` how many of those are disconnected (below).

**Disconnected (NC-01, decided 12 Nov 2026).** For a matched server, `collection` says how its device's **newest patch collection** went, read from N-central's own record of the attempt (`ncentral.device_patch`, the newest row per device whether it worked or not). `state` is `ok`; **`disconnected`**, when the attempt could not be used (it has an error, or its coverage is `unavailable`, which is the rule the Overview uses) **and the error is a 404 / `DeviceNotFoundException`**, meaning N-central cannot find the device, so it counts as disconnected; `failed`, when it could not be used for any other reason (a timeout, a 500, no reason given); or `null`, when it has never been tried. `attempted_at` and `days_ago` say when that attempt was. The error text itself is never sent. A disconnected device keeps showing the `categories` from its **last successful** inventory, and `inventory` is the date of that inventory, so the two are never mixed up with the failed attempt. `seen` is when the inventory collector last saw the device (`ncentral.device.last_seen`). Each of `seen` and `inventory` is `{at, days_ago}`, or `null`.

**Data collected (`collected`).** For every server, the **latest date found for that device on a successful run of each collector**, as four items in a fixed order: SentinelOne (when its agent record was captured), N-central device inventory (`seen`), N-central patch inventory (`inventory`, always the newest *usable* one) and Cove backup (when the matching backup device was captured; Cove devices are matched like N-central ones, by name or hostname inside the same client). `state` is `found` (then `at` and `days_ago` are given), `none` (matched, but no date recorded), `no-match`, `ambiguous` or `unlinked` (as for `patches.status`), `not-collected` (Cove has collected nothing yet) or `unavailable` (it could not be read; the page still loads, and the reason is logged, never sent).

### `backups`
The current state of every Cove backup device, with its health and red flags. Read from the `cove` schema; nothing is invented.

Which rows are current: each load of the Cove collector is a snapshot, and `load-active` makes one per customer. A device is read from the newest snapshot of its load scope (a per-customer load, or a whole-estate load), so a device Cove stops reporting disappears once its customer is loaded again. A device in several of those snapshots is read from the newest.

`health` and the red flags are decided by the **collector**, not the API: `CRITICAL` when a flag reports no successful backup or a failed, aborted or interrupted session; `WARNING` for any other flag; `OK` for none. Rows are sorted worst first.

```json
{
  "available": true,
  "loaded": true,
  "title": "Backup devices",
  "columns": [
    {
      "key": "customer",
      "label": "Customer"
    },
    {
      "key": "device_name",
      "label": "Device"
    },
    {
      "key": "device_type",
      "label": "Device Type"
    },
    "…"
  ],
  "rows": [
    {
      "snapshot_id": 21,
      "device_id": 2,
      "device_name": "PC-RECEPTION",
      "customer": "Zed",
      "client_id": 2,
      "hostname": "srv-file",
      "os_type": "Workstation",
      "account_type": "Endpoint/Server",
      "device_type": "Workstation",
      "is_server": false,
      "is_m365": false,
      "health": "WARNING",
      "red_flag_count": 1,
      "flags": [
        {
          "category": "retention",
          "flag": "RETENTION SHORT: 45 days (recommended: 90+)"
        }
      ],
      "last_success": "2026-10-09T03:00:00Z",
      "days_since_success": 1,
      "used_storage_bytes": null,
      "retention_days": 45,
      "lsv_enabled": true,
      "lsv_status": "Healthy",
      "cloud_status": "Healthy",
      "immutable_enabled": null,
      "client_old": false,
      "os_eol": null,
      "active_datasources": "Files",
      "captured_at": "2026-11-10T06:00:00Z",
      "data_age_hours": 2.5,
      "rowStatus": "warn",
      "reason": "RETENTION SHORT: 45 days (recommended: 90+)"
    },
    "…"
  ]
}
```

- `available` is `false` when the `cove` schema does not exist; `loaded` is `false` when it exists but nothing has been collected yet (the job ships disabled). The page says which.
- `flags[].category` is one of the collector's buckets: `backup_currency`, `chronic_failure`, `m365_coverage`, `dr_readiness`, `reporting`, `security`, `lifecycle`, `retention`, `storage_health`, or `session_failure` (anything else).
- `rowStatus` is `bad` for CRITICAL and `warn` for WARNING; `reason` joins the flag texts. `device_type` is `Microsoft 365`, `Server` or `Workstation`.
- `used_storage_bytes` is binary (1 GB = 1024³ bytes), as the collector reports it. `last_success`, `days_since_success`, `retention_days` and the other values are carried through as Cove reported them, or `null`.
- `client_id` is the master client: first the Cove customer's own ID (found by name in `cove.customers`) through a client link, then the client's name or an alias; `null` when unmapped. Cove devices carry only the customer's name, so this is the weakest of the four sources to match.

### `backup-detail`
One device with its data source sessions, Microsoft 365 sources and red flags, from the same snapshot the list used.

| Parameter | Required | Meaning |
|---|---|---|
| `device` | yes | The Cove device ID. Must be digits (`400` otherwise). An unknown device, or no `cove` schema, gives `404` |

```json
{
  "device": {
    "snapshot_id": 21,
    "device_id": 2,
    "device_name": "PC-RECEPTION",
    "customer": "Zed",
    "device_type": "Workstation",
    "health": "WARNING",
    "os": "Windows Server 2022",
    "client_version": "24.1",
    "retention_days": 45,
    "last_success": "2026-10-09T03:00:00Z",
    "captured_at": "2026-10-10T06:00:00Z",
    "…": "…"
  },
  "flags": [
    {
      "category": "retention",
      "flag": "RETENTION SHORT: 45 days (recommended: 90+)"
    }
  ],
  "datasources": [
    {
      "ds_code": "D01",
      "ds_name": "Files",
      "status": "Completed",
      "status_code": "4",
      "error_count": 0,
      "duration_seconds": 600,
      "sent_bytes": 1073741824,
      "protected_bytes": 3221225472,
      "item_count": 9000,
      "retention_days": 45,
      "last_success": "2026-10-09T03:00:00Z",
      "last_session": "2026-10-09T03:00:00Z",
      "ok_days": 28,
      "failed_days": 0,
      "warning_days": 1,
      "missed_days": 1,
      "max_fail_streak": 0,
      "recurring_failures": false,
      "chronic_warnings": false,
      "chronic_missed": false,
      "imm_active": true,
      "imm_last_status": "Completed"
    }
  ],
  "m365": []
}
```

`datasources` has one entry per backed-up data source (name, status, last success and last session, counts of ok, failed, warning and missed days, protected bytes, whether immutable copies are active); `m365` lists Microsoft 365 sources (mailboxes and the like) the same way.

---

## Clients and links

A **client** is the master record. Customers in the source systems are linked to it by each system's own ID (`sources`), and other spellings of its name are `aliases`. Matching order everywhere is: ID link, then name or alias, otherwise unmapped.

### GET `/api/clients`
Signed in. A list, sorted by name:
```json
[{ "id": 3, "name": "IP Capital: BITCO", "contact_email": "", "notes": "", "is_active": true, "company": { "id": 1, "name": "IP Capital" },
   "sources": [{ "source": "autotask", "external_id": "101", "customer_name": "IP Capital: BITCO" }, { "source": "ncentral", "external_id": "7", "customer_name": null }],
   "aliases": [] }]
```
**`customer_name`** (added 12 Nov 2026, in this list only) is the name of the linked customer in its source system, so the Clients table can show names and not IDs. It is `null` when the name cannot be read: the source system was unavailable, or the customer is no longer there. It is never guessed. The other client endpoints (create, change, claim) answer without it. A person only ever receives the links of clients they may see.
`source` is one of `autotask`, `ncentral`, `sentinelone`, `cove`. `external_id` is Autotask `company_id`, N-central `org_unit_id`, SentinelOne `site_id`, Cove customer ID (always a string). `company` is the company that has **claimed** the client, or `null` while it is unclaimed (see *Companies* below). While access is enforced you are only sent the clients you may see.

### POST `/api/clients` and PUT `/api/clients/{id}`
Staff. Body (same for both; PUT **replaces** the sources and aliases with what is sent):
```json
{ "name": "IP Capital: BITCO", "contact_email": "", "notes": "", "is_active": true,
  "sources": [{ "source": "autotask", "external_id": 101 }], "aliases": ["IP Capital (BitCo)"] }
```
| Status | Meaning |
|---|---|
| 201 / 200 | The saved client (shape above) |
| 400 | `Name is required`; `That name is already used by another client or alias`; `Unknown source: x`; `<Source> needs an ID of up to 100 characters`; `<Source> <id> is already linked to <client>`; `The alias "x" is already used by <client>`; alias over 200 characters |
| 403 | Not staff |
| 404 | PUT: no such client, **or a client of another company** (the same answer, so nothing is revealed) |
| 409 | A concurrent change conflicted; reload and retry |

Names and aliases are compared ignoring case and punctuation (`IP Capital: BITCO` equals `ip capital bitco`). An alias that is the same as the client's own name is dropped as redundant. Each (source, ID) can belong to one client, across all companies. **A client you create joins your own company** (an administrator cannot choose another; the owner may send `"company": <id>` or leave it out for unclaimed). Where an error names the client in the way (`already linked to <client>`), a client you may not see is called `another client` instead of by name.

### GET `/api/clients/resolution`
Staff. Customers in the source systems that no client is linked to, **grouped by name**, and the discrepancies of existing clients.

Grouping rule: names group only when they have **exactly the same words in the same order** (capitals and punctuation ignored). `IP Capital: BITCO` and `IP Capital (BitCo)` group; `IP Capital: BITCO Holdings`, `IP Capital` and `Capital IP` do not. Every multi-customer group (and every match to an existing client) is an **inference** and is flagged as one; nothing links until confirmed.

```json
{
  "sources": [
    {
      "key": "autotask",
      "label": "Autotask",
      "available": true
    },
    "…"
  ],
  "proposals": [
    {
      "key": "blue crane",
      "name": "Blue Crane",
      "members": [
        {
          "source": "autotask",
          "external_id": "102",
          "customer_name": "Blue Crane"
        }
      ],
      "existing_client_id": null,
      "existing_client_name": null,
      "inferred": false,
      "message": null,
      "warnings": [],
      "found_in": [
        "autotask"
      ],
      "missing_from": [
        "ncentral"
      ],
      "discrepancy": "Found in Autotask. Not found in N-central."
    },
    {
      "key": "ip capital bitco",
      "name": "IP Capital: BITCO",
      "members": [
        {
          "source": "autotask",
          "external_id": "101",
          "customer_name": "IP Capital: BITCO"
        },
        {
          "source": "ncentral",
          "external_id": "7",
          "customer_name": "IP Capital (BitCo)"
        }
      ],
      "existing_client_id": null,
      "existing_client_name": null,
      "inferred": true,
      "message": "Grouped by matching name only (inference): IP Capital (BitCo), IP Capital: BITCO. Check these are the same company.",
      "warnings": [],
      "found_in": [
        "autotask",
        "ncentral"
      ],
      "missing_from": [],
      "discrepancy": null
    }
  ],
  "clients": [
    {
      "id": 3,
      "name": "Acme",
      "is_active": true,
      "linked_in": [
        "autotask",
        "ncentral"
      ],
      "missing_from": [
        "cove"
      ],
      "stale": [
        {
          "source": "ncentral",
          "external_id": "99"
        }
      ],
      "discrepancies": [
        "No Cove customer is linked to this client.",
        "N-central 99 is linked but no longer exists in N-central."
      ]
    }
  ],
  "summary": {
    "clients": 1,
    "clients_with_discrepancies": 1,
    "proposals": 2,
    "inferred": 1,
    "unlinked_customers": 3,
    "open_gaps": 4
  },
  "recently_resolved": [ { "kind": "unlinked", "source": "cove", "source_label": "Cove", "label": "Acme Backups", "external_id": "4", "first_seen_at": "2026-10-28T08:00:00Z", "resolved_at": "2026-11-09T09:30:00Z" } ]
}
```

- `proposals[].inferred` is `true` for a group of more than one customer, or one matching an existing client's name or alias (`existing_client_id`). `message` says so. `warnings` lists, for example, two customers from one source sharing a name.
- `found_in` / `missing_from` / `discrepancy` report, among the sources that could be read, where this customer exists and where it does not. A source that could not be read (`sources[].available` is `false`) is never counted as missing.
- `clients[]` is one row per existing client: `missing_from` (active clients only), `stale` (links to a customer ID that no longer exists in that source) and the plain-language `discrepancies`.
- **How long each gap has lasted.** Every time this endpoint is read it brings a lasting record of gaps up to date (table `api_clientgap`): a gap seen for the first time is recorded with that time, one still there is kept with its original first-seen time, and one that has gone is closed. Gaps are of three kinds: `unlinked` (a customer in a source that no client is linked to), `missing` (an active client has no link in a source) and `stale` (a link to a customer that no longer exists). `proposals[].since` is when the earliest customer of the group was first seen unlinked; `clients[].since` is a list in step with `discrepancies`, the first-seen time of each; both are `null` when nothing is on record. `summary.open_gaps` counts the open ones and `recently_resolved` lists up to 20 closed in the last 30 days, newest first. A source that could not be read this time (`sources[].available` false) never closes its gaps, because nothing can be said about it. If keeping the record fails, the endpoint still answers and the dates are `null`: it is logged, and the page never fails because of it.

### POST `/api/clients/resolve`
Staff. Confirms proposals: creates clients, or adds customers to an existing client.
```json
{ "groups": [ { "name": "IP Capital: BITCO",
                "members": [ { "source": "autotask", "external_id": "101", "customer_name": "IP Capital: BITCO" },
                             { "source": "ncentral", "external_id": "7", "customer_name": "IP Capital (BitCo)" } ] },
              { "client_id": 3, "members": [ { "source": "cove", "external_id": "4", "customer_name": "Acme" } ] } ] }
```
Response `200 {"created": 1, "extended": 1, "errors": []}`. Each group is saved on its own, so a conflict in one (`errors: [{"group": "...", "error": "..."}]`) does not undo the others. Spellings that differ from the name only by punctuation or case need no alias; others are kept as aliases. `400` unless 1 to 500 groups are sent; `403` for non-staff.

---

## Saved reports

A report definition is stored on the server (the PDF itself is made in the browser). It is a list of **blocks**: each block is one kind of section with its own filters, so one report can hold "Tickets for Acme" and "Tickets for Zed" side by side. Reports are private to their owner unless **shared**; only staff can share one. Anyone signed in can read the shared ones.

| Who | May |
|---|---|
| The owner | See, change and delete their own report |
| Admins (staff) | Make a report a **template** (`shared`); also change or delete a template that someone else owns (private reports of others are not visible to anyone else, admins included) |
| Everyone signed in | Create **personal** reports; see and generate templates with their own dates, and copy one to a personal report. Only admins can create or share a template |

The page shows two lists: **My Reports** (your private ones) and **Templates** (the reports admins have shared).

### GET `/api/reports`
Signed in. Your reports and the shared ones, by name:
```json
[{ "id": 7, "name": "Monthly pack", "shared": false, "owner": "kev", "mine": true, "updated_at": "2026-11-10T08:00:00Z",
   "config": { "version": 2, "title": "IP Capital Management Report", "annex": false, "maxRows": 200,
               "period": { "from": "", "to": "" }, "weights": {},
               "blocks": [ { "id": "b1", "type": "tickets", "title": "", "filters": { "customer": "Acme", "priority": "", "ticketStatus": "", "ticketType": "", "issueType": "", "subIssueType": "" } },
                           { "id": "b2", "type": "backups", "title": "", "filters": { "customer": "", "backupHealth": "CRITICAL" } } ] } }]
```

### POST `/api/reports` and PUT `/api/reports/{id}`
Body `{"name": "Monthly pack", "shared": false, "config": { ... }}`. On PUT every field is optional: what is left out is kept (a body without `config` keeps the stored one). `201` (POST) or `200` (PUT) with the saved report.

| Status | Meaning |
|---|---|
| 400 | A name of 1 to 120 characters is required; `You already have a report with that name` (ignoring case, per owner); `Only staff can share a report`; `'shared' must be true or false`; `The report is too large` (over 100 kB); or the definition is invalid (the message says what) |
| 403 | PUT: the report is shared, and you are neither its owner nor staff |
| 404 | No such report, or it is someone else's private one |

### DELETE `/api/reports/{id}`
Same permissions as PUT. `200 {}`.

### The definition (`config`)
The server rebuilds it from what was sent, so unknown fields are dropped and the stored form is always the normalised one. The same rules run in the browser (`frontend/src/report/config.js`); both are tested against one shared list of cases (`frontend/src/report/template-cases.json`).

| Field | Rule |
|---|---|
| `version` | `2`. A version `1` template (one flat set of sections and filters) is accepted and migrated into blocks |
| `title` | Text on the cover; trimmed, at most 120 characters; blank gives "IP Capital Management Report" |
| `annex` | `true` puts each section's detail table in an annex at the back; anything else is `false` (charts and totals only) |
| `maxRows` | Whole number from 10 to 500, otherwise 200 |
| `period` | Optional default period, only a default: the dates are chosen again when the report is generated. Either `{from, to}` as `YYYY-MM-DD` (or blank), start not after end, **or** a duration `{last: {amount, unit}}` with `unit` `days` (up to 3660), `weeks` (520) or `months` (120): "the last 3 months", worked out from the day the report runs (the last N days counts today as the first; the last N months starts the day after the same date N months back). A duration and dates cannot be given together. `last` is present only when set |
| `weights` | Optional patch score weights for this report: `{key: 0..1000}`, at most 500 keys, keys as on `/api/weights`. When present they replace the viewer's personal weights for this report, on top of the shared defaults |
| `blocks` | 1 to 20 blocks, in the order they appear |

A block is `{id, type, title, filters}`: `type` is one of the kinds below; `title` an optional heading (at most 120 characters); `id` is kept if it is unique and made of letters, digits, `_` or `-`, otherwise renumbered `b1`, `b2`…; `filters` may only hold the keys that kind understands. A filter is **text** of at most 200 characters (blank means all) **or a list of up to 20 such values**: a row matches if it has *any* of the values, ignoring case, and the filters of a block all apply together. A list is cleaned (each value trimmed, blanks and repeats ignoring case dropped); one value becomes plain text and none becomes blank. `patchScope` takes one value only.

| Block `type` | Section | Filters |
|---|---|---|
| `tickets` | Client Tickets | `customer`, `priority`, `ticketStatus`, `ticketType`, `issueType`, `subIssueType` |
| `patches` | Patch Compliance | `patchType`, `patchScope` |
| `patch-details` | Patch Details | `patchType`, `patchScope` |
| `server-health` | Endpoint Health | `customer`, `agentStatus` |
| `backups` | Backup Health | `customer`, `backupHealth` |

`patchScope` is `client:<id>` or `org:<id>`, as chosen on the Patches page.

### How a report is made
In the browser: the dates are asked for (presets: last 30 days, this month, last month, this quarter, last quarter), then each distinct request is made **once** (ten ticket blocks cost one ticket request; one `patches` request per client scope), then each block is filtered and turned into KPIs and charts. The PDF has a cover, a contents page with the page each section starts on, the sections, the optional annex (each table on its own page), and "Page n of N" on every page after the cover.

---

## Companies and access to clients

By default every signed-in person sees every client. Setting `DJANGO_CLIENT_ACCESS_ENFORCED=1` on the server turns on **companies as walls** and limits people to their clients. Do the set-up first (see `docs/05_OPERATIONS.md`, "Turning on client access"): with it on, someone in no company sees nothing.

### Who is who
| Level | Who | Sees |
|---|---|---|
| **Platform owner** | A Django superuser (`manage.py company owner <user>`). Sits above the companies. | Everything. The only one who can make companies, rename them, move people between them, and read the raw collector logs. |
| **Company administrator** | Staff, in a company | All of **their own company's** clients, plus clients **nobody has claimed yet** and data linked to no client yet (so there is something to link and claim). **Never another company's clients: an administrator of company A cannot see company B's data.** Can run the data collectors and see their status. |
| **Member** | Anyone else, in a company | Only the clients they were given (by their administrator) that belong to their own company. |
| Nobody in a company | | Nothing. |

A **client** belongs to one company once it is *claimed*; until then only administrators see it. An administrator claims it for their own company (`POST /api/clients/{id}/claim`) and only while nobody has; once claimed it is no longer visible to any other company. A **person** belongs to one company for now. The shared score weights and report templates are **not yet separate for each company**.

### What each answer is limited to
| What | What a limited person gets |
|---|---|
| `tickets`, `server-health`, `backups` | Only the rows of clients they may see. A row linked to no client is shown to administrators only (so they can link it) and never to a member. |
| `backup-detail` | `404` for a device of a client they may not see: the same answer as for a device that does not exist. |
| `patches`, `patch-entries`, `patch-overview` | The queries are limited to the customers of the clients they may see (and, for an administrator, customers linked to no client), so nothing about anyone else is read. The client choices list only those. Naming a client or customer outside is `403`. |
| `patch-trend` | A named client's own history (or several, combined). With no client named, **the combined history of the clients they may see**, never the history of everyone. |
| `GET /api/clients` | Only the clients they may see. |
| Client resolution | Customers linked to **any** client are not "unlinked", so another company's customer never looks free to claim; the clients and gaps shown are only the ones they may see. The record of gaps is kept for every client, and looking at it never closes another company's gap. |
| The Access page | An administrator sees and manages only the people, client groups and clients of their own company. A person or a group in another company does not exist to them (`404`), and a client group can hold only its own company's clients. |
| Data collection | Administrators can see job status and run collectors, which carry no customer names. **The raw log of a run is the platform owner's** (`403`), because it can name customers of every company. |
| Reports | Made in the browser from these sections, so they carry the same limit. Saved reports are private to their owner. **A report shared by an administrator is visible to everyone signed in, including any client names typed into its filters**, and the shared score weights are shared by everyone: both are still on the list to become per company. |

### GET `/api/companies`
Staff. `[{"id": 1, "name": "IP Capital", "members": 5, "clients": 12}]`. The owner sees every company, an administrator only their own, an administrator in no company none. `403` for anyone else.

### POST `/api/companies`
Owner. Body `{"name": "Branch"}` makes a company: `201` with the row above. `400` when the name is blank, over 200 characters, or already used (ignoring case). `403` for anyone but the owner.

### PUT `/api/companies/{id}`
Owner. Body `{"name": "..."}` renames it (same checks). `404` for an unknown company.

### PUT `/api/users/{username}/company`
Owner. Body `{"company": 2}` puts the person in that company; `null` takes them out of theirs. **Moving someone drops the clients they were given**, which belonged to their old company. `400` for an unknown company or when `company` is missing; `404` for an unknown person; `403` for anyone but the owner.

### POST `/api/clients/{id}/claim`
Staff. An **administrator** sends `{}` and the client joins their own company; it must be unclaimed (or already theirs, which changes nothing), otherwise `404`, since a client of another company is not theirs to see. `403` if they are in no company. The **owner** sends `{"company": 2}` to give it to any company, or `{"company": null}` to take it back; `400` when `company` is missing or unknown. `403` for a member. Answers with the client.

### GET `/api/access`
Staff. `{"enforced": false, "owner": false, "can_make_admin": false, "company": {"id": 1, "name": "IP Capital"}, "users": [{"username": "sam", "name": "Sam Dlamini", "email": "sam@example.com", "is_staff": false, "is_owner": false, "is_active": true, "company": {"id": 1, "name": "IP Capital"}, "clients": [3, 5], "client_groups": [2]}, "..."], "clients": [{"id": 3, "name": "Acme", "company": {"id": 1, "name": "IP Capital"}}, "..."], "client_groups": [{"id": 2, "name": "Retail", "all_clients": false, "clients": [3, 5], "members": ["sam"], "company": {"id": 1, "name": "IP Capital"}}, "..."]}`. `clients` on a person are the clients **given one by one**; `client_groups` are the IDs of the groups they are in. `can_make_admin` is true only for the platform owner. An administrator is sent only their company's people, groups and clients; the owner is sent everyone's. Whether the limits are on (`enforced`) is `DJANGO_CLIENT_ACCESS_ENFORCED`.

**What a person sees, once the limits are on:** an administrator sees every client of their company. Anyone else sees the clients of **all the client groups they are in** (an "all clients" group gives every client of the company, including ones made later) **plus any given to them one by one**, and never a client of another company, whatever a group holds. A person in no company sees nothing.

### PUT `/api/access/{username}`
Staff. Body `{"clients": [3, 5]}` **replaces** that person's clients (an empty list removes them all) and answers `{"username": "sam", "clients": [3, 5]}`. Only clients of **that person's company** can be given. `400` unless `clients` is a list of at most 500 whole numbers, all of them clients of the person's company (nothing is changed on a refusal), or when the person is in no company yet; `403` for non-staff; `404` for an unknown person or one in another company (an administrator cannot tell the difference).


### GET `/api/client-groups`
Staff. The same group rows as in the access overview: `[{"id": 2, "name": "Retail", "all_clients": false, "clients": [3, 5], "members": ["sam"], "company": {"id": 1, "name": "IP Capital"}}]`. An administrator sees their own company's groups, the owner everyone's, and an administrator in no company none. `403` for anyone who is not staff.

### POST `/api/client-groups`
Staff. Body `{"name": "Retail", "all_clients": false, "clients": [3, 5]}`, and for the **platform owner** `"company": 1` (an administrator's group is always in their own company). `201` with the group row. A group is **a named list of clients, or all of the company's clients**: with `all_clients: true` it holds no list of its own (anything sent is ignored), so it is always every client of the company. A group with no clients at all is allowed. `400` (nothing is made) when: the name is blank or over 80 characters (`Give the group a name`, `A name can have at most 80 characters`), another group of that company already has it, ignoring case and spaces (`There is already a group with that name`), `all_clients` is not true or false, `clients` is not a list of at most 500 whole numbers, or a client is not one of the company's (`Unknown client ID: 9`, which is also exactly what a client of another company looks like, so it never confirms that one exists); the owner without a company gets `Choose a company`, and an administrator in no company `You are not part of a company yet`.

### PUT `/api/client-groups/{id}`
Staff. Sends the **whole** group again, as for POST (the name is required), and replaces it, including its list of clients. The company never changes. The same `400`s, except that a group may keep its own name. `404` for an unknown group **and for another company's** (it does not exist to them).

### DELETE `/api/client-groups/{id}`
Staff. `{"deleted": 2}`. The people in it simply lose what it gave them. `404` as for PUT.

### POST `/api/users`
Staff. **Makes a person with no password chosen for them.** Body `{"username": "thandi", "email": "thandi@acme.test", "first_name": "Thandi", "last_name": "Nkosi", "client_groups": [2], "is_staff": false}`, and for the platform owner `"company": 1`. The person joins the administrator's company (the owner names it), and the account starts with an **unusable password**: any `password` in the request is ignored. `201` `{"username": "thandi", "email": "thandi@acme.test", "setup_link": "https://reports.example/reset?uid=MQ&token=...", "valid_for_minutes": 60, "sent": "sent"}`. The **link** goes to the same page "forgot password" uses (`/reset`, which calls `POST /api/auth/reset`); it works **once**, and for `valid_for_minutes` (`PASSWORD_RESET_TIMEOUT`, 3600 seconds, **shared with the forgot-password link**). It is emailed to them and also returned, because the administrator may have to pass it on. `sent` says honestly what happened to the email: `sent` (a real email backend), `console` (the default backend only prints to the server's console, so **nothing was emailed**), `failed` (the person is still made, and the link still returned) or `none` (no email address). `400` (nobody is made) when: the username is blank or has anything but letters, numbers and `. @ + - _` (`A username can only have letters, numbers and . @ + - _ (up to 150)`); the email is blank (`Email is required: the link to choose a password is sent there`) or not an address; a group is not the company's (`Unknown client group: 7`); `is_staff` is true and the caller is not the platform owner (`Only the platform owner can make an administrator`); or the company is missing as for groups. `409` `That username is taken`, whatever the case. `403` for anyone who is not staff.

### PUT `/api/users/{username}`
Staff. Send **only what changes**: `first_name`, `last_name`, `email` (cannot be emptied), `is_active`, `client_groups` (replaces the person's groups within their company; an empty list takes them out of all), and, **platform owner only**, `is_staff`. `200` with `username` and what was changed. **A person is deactivated (`is_active: false`), never deleted**: they are signed out everywhere at once and cannot sign in until `is_active: true` again; their reports and history stay. `400` for an invalid value, `Nothing to change`, `You cannot deactivate yourself`, `You cannot remove your own administrator rank`, or groups for a person in no company; `403` when a non-owner tries to change `is_staff` or touch the platform owner; `404` for an unknown person **and for one in another company**.

### POST `/api/users/{username}/link`
Staff. A **fresh link** for a person whose first expired or never arrived, emailed if they have an address, and answered like `POST /api/users` (`200`). `400` for someone deactivated (`This person is deactivated; bring them back first`); `404` as for PUT.

---

## Score weights

### GET `/api/weights`
Signed in. `{"shared": {...}, "user": {...}, "effective": {...}}`: the shared default set, the caller's own overrides, and the two layered (the caller's win). Keys are a patch type (`third-party`) or `<type>:<category id>`; values are numbers from 0 to 1000. Missing means `1`.

### PUT `/api/weights`
Body `{"scope": "user" | "shared", "weights": {"third-party": 2, "windows-security:g1": 0}}`. **Replaces** that whole set; an empty object clears it. `scope` defaults to `user`. Changing `shared` needs staff (`403` otherwise). `400` for an invalid scope, key (lower-case letters, digits and hyphens, optionally `:` and up to 150 characters), or value; at most 500 entries. Returns the same body as GET.

---

## Data freshness (everyone signed in)

### GET `/api/freshness`
Signed in. When each page's data was last collected, so a page never presents old figures as current. It carries **only a time, a state and a count of failed runs**: nothing from the logs, no exit codes, no errors and no schedule, which is why anyone signed in may read it while the collector status below stays for administrators.

```json
{ "checked_at": "2026-11-11T08:00:00+00:00",
  "sources": [
    { "key": "patches", "label": "Patch data", "as_of": "2026-11-09T12:29:00+00:00", "age_hours": 44.0, "state": "stale",
      "failures_since": 3, "basis": "last good run", "enabled": true },
    { "key": "tickets", "label": "Ticket data", "as_of": null, "age_hours": null, "state": "never",
      "failures_since": 2, "basis": "none", "enabled": false } ] }
```
| Field | Meaning |
|---|---|
| `key` | `patches` (Overview, from the N-central patch job), `servers` (Server Health, SentinelOne), `backups` (Backups, Cove) or `tickets` (both ticket pages, Autotask). Always these four, in this order |
| `as_of`, `age_hours` | The time of the **last good run** (a success or a partial run, never a failure or a dry run), so a failed run leaves the data exactly as old as it was; with no good run in the log, the newest row in the source's own schema; `null` when neither exists |
| `state` | `current` (as recent as the job's schedule expects), `stale` (older than that), `unknown` (a time, but the job has no schedule to judge it by) or `never` (nothing has ever been collected) |
| `failures_since` | How many runs have failed since the last good one (all of them, if there was none) |
| `basis` | `last good run`, `stored data` (no good run in the log, so the newest stored row) or `none` |
| `enabled` | Whether the collector is switched on in `collector_jobs.json` |

How old is too old is the job's own `interval_minutes` in your `collector_jobs.json`: data is `stale` once it is older than that. A monthly job given a weekly interval will therefore read stale after eight days.

## Collectors (staff)

Operational data, not customer reports. Logs can contain customer and device names.

### GET `/api/collectors/health`
Per-job latest run, **freshness judged from the last good run**, stored-data age, and the current alerts. `503` if `collector_jobs.json` cannot be read. The values below are illustrative: an N-central job that last worked two days ago and has failed four times since.
```json
{ "configured": true, "checked_at": "2026-11-10T08:30:00+00:00",
  "jobs": [ { "job": "ncentral_inventory", "enabled": true, "status": "FAILED",
              "last_run_at": "2026-11-10T08:18:55+00:00", "last_duration_seconds": 15.353, "last_exit_code": 1, "last_dry_run": false,
              "interval_minutes": 1440,
              "last_good_at": "2026-11-08T06:00:12+00:00", "last_good_status": "SUCCESS", "last_good_duration_seconds": 12.5,
              "failures_since_good": 4, "good_run_age_hours": 50.5, "stale_since": "2026-11-09T06:00:12+00:00",
              "freshness": "STALE", "freshness_basis": "last good run",
              "data_as_of": "2026-11-10T08:18:40+00:00", "data_age_hours": 0.2, "data_age_source": "database" }, "…" ],
  "alerts": [ { "key": "ncentral_inventory:failed", "severity": "error", "job": "ncentral_inventory",
                "message": "N-central inventory failed on its last run (2026-11-10 08:18 UTC)." } ] }
```
- `status`, `last_run_at`, `last_duration_seconds`, `last_exit_code` describe the **latest run, whatever it did** (including a failure). `status` is `SUCCESS`, `PARTIAL`, `FAILED`, `STARTED` or `NEVER_RUN`. **`PARTIAL`** is an N-central patch run whose only problems were devices that N-central cannot find (`404 DeviceNotFoundException`). Those devices count as **disconnected** (decided 12 Nov 2026, NC-01), which `server-health` shows per device; every other device's inventory was collected. `disconnected_devices` says how many (it is `null` for any other status), and the Data Collection page shows it when you hover the Partial badge.
- **Freshness is judged from the last good run:** the latest run that `SUCCESS`-ed or completed `PARTIAL`ly and was not a dry run, read from the log (its most recent megabyte). `freshness` is `CURRENT` (that run is within `interval_minutes`), `STALE` (older; `stale_since` is when it went stale) or `UNKNOWN` (no interval, or no good run). A failed run therefore neither makes old data look fresh nor hides that it has gone stale.
- `last_good_*` and `good_run_age_hours` describe that run; `failures_since_good` counts the failed runs after it, or all the failed runs in the log when there has been no good run. Dry runs are never good and their failures are not counted.
- `freshness_basis` says where `freshness` came from: `last good run`; `stored data` (the log has no good run, so the newest stored timestamp was used); or `none`.
- `data_as_of`, `data_age_hours` and `data_age_source` are the newest timestamp **in the database**. They are shown as information and are not what freshness is judged from, because a loader that creates its snapshot row and then fails leaves a fresh-looking timestamp behind.
- Failure reasons are never included.
Jobs: `ncentral_inventory`, `ncentral_patches`, `ncentral_monitoring`, `autotask_tickets`, `sentinelone_monthly`, `cove_backup`. See [`05_OPERATIONS.md`](05_OPERATIONS.md) for the alert rules.

### GET `/api/collectors/{job}/latest-log`
The latest run's block of that job's log (bounded in size). Only the six job names above are accepted, never a path: others get `404 {"error": "Unknown collector"}`.
`{"job": "ncentral_patches", "available": true, "log": "...", "truncated": false}`.

### GET `/api/collectors/execution`
```json
{ "active": true,
  "batch": { "id": 42, "requested_at": "...", "started_at": "...", "finished_at": null, "status": "RUNNING",
             "total": 3, "completed": 1, "current_job": "ncentral_patches", "dry_run": false,
             "jobs": [ { "job": "ncentral_inventory", "status": "SUCCESS", "duration_seconds": 12.5 }, "…" ] },
  "schedules": { "ncentral_inventory": { "schedule_enabled": true, "next_execution_at": "2026-11-11T08:00:00+00:00" }, "…" },
  "heartbeat": { "last_tick_at": "2026-11-10T08:00:30+00:00", "state": "RUNNING", "age_seconds": 12.0 } }
```
`batch` is `null` when nothing has ever been queued. `heartbeat.state` is `TICK`, `RUNNING`, `BUSY` or `IDLE`; all fields are `null` until the worker has run once.

### POST `/api/collectors/run`
Body `{"job": "ncentral_inventory"}` or `{"job": "all"}` (all **enabled** jobs, in a fixed order). Nothing starts directly: the request is queued and the worker runs it on its next tick.

| Status | Meaning |
|---|---|
| 202 | `{"queued": true, "batch_id": 42, "message": "Queued. Collection worker will process this request on its next tick."}` |
| 400 | Invalid JSON, not an object, or `Unknown collector` |
| 409 | A batch is already queued or running |
| 503 | Configuration or queue unavailable |

There is no dry-run option over HTTP.
