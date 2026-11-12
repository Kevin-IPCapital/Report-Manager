"""SolarWinds N-central REST API client.

Auth model (confirmed live against our instance, contradicts vendor docs in places):
  - The only credential ever sent is the long-lived NCENTRAL_API_TOKEN.
  - POST /api/auth/authenticate with that token as the Bearer header (no body)
    returns a short-lived access token nested under tokens.access.token.
  - We re-authenticate from scratch on expiry instead of using /api/auth/refresh.

Rate limiting: the API 429s heavily above ~5 concurrent requests, so all
fan-out work must go through with_concurrency_limit() rather than an unbounded
thread pool.
"""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("NCENTRAL_API_BASE_URL")
API_TOKEN = os.getenv("NCENTRAL_API_TOKEN")

REQUEST_TIMEOUT_S = 15
TOKEN_REFRESH_MARGIN_S = 60
DEFAULT_CONCURRENCY = 5

CUSTOMER_LIST_TTL_S = 15 * 60
BULK_PULL_TTL_S = 45 * 60


class NCentralError(RuntimeError):
    """API error carrying the HTTP status and (when JSON) the response body."""

    def __init__(self, message: str, status: int = None, body=None):
        super().__init__(message)
        self.status = status
        self.body = body


def _normalize_base_url(raw: str) -> str:
    # The .env value is often just a hostname; requests needs a scheme.
    url = raw.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    return url


# ---------------------------------------------------------------------------
# Monitor state vocabulary
#
# Values below were collected live from stateStatus across our estate: Normal,
# No Data and Misconfigured. Warning/Failed are documented N-central states we
# have simply not had in flight while probing, so they are included here.
# ---------------------------------------------------------------------------

HEALTHY_MONITOR_STATES = {"Normal"}

# Not a breach — the monitor is not reporting, which is its own kind of problem
# but must not be counted as a threshold failure.
UNKNOWN_MONITOR_STATES = {"No Data", "Misconfigured", "Stale", "Disconnected"}

# Worst-first, for rolling a device up to a single state.
MONITOR_STATE_SEVERITY = [
    "Failed", "Warning", "Misconfigured", "Stale", "Disconnected", "No Data", "Normal",
]

# moduleName -> bucket. Matched as a case-insensitive substring, longest first,
# so "Disk I/O" is classified before the shorter "Disk" can claim it.
MONITOR_BUCKETS = {
    # performance / capacity
    "cpu": "performance",
    "memory": "performance",
    "disk i/o": "performance",
    "disk": "performance",
    "uptime": "performance",
    "processor": "performance",
    "temperature": "performance",
    "physical drive": "performance",
    "power supply": "performance",
    "system battery": "performance",
    "chkdsk": "performance",
    "bsod": "performance",
    # reachability / network reliability
    "connectivity": "connectivity",
    "agent status": "connectivity",
    "probe status": "connectivity",
    "network card": "connectivity",
    "localip": "connectivity",
    "telnet": "connectivity",
    "clock drift": "connectivity",
    # patching
    "patch status": "patch",
    "reboot required": "patch",
    "pme verification": "patch",
    "windows update": "patch",
    "application compliance": "patch",
}

_BUCKET_KEYS = sorted(MONITOR_BUCKETS, key=len, reverse=True)


# ---------------------------------------------------------------------------
# Patch classification
#
# The `category` on an asset patch entry is a WSUS UpdateClassification GUID.
# The Microsoft ids below are the documented, stable ones. The last entry is
# N-central's own: it is not a WSUS classification, and was identified from our
# data — every entry carrying it is a third-party app pushed by patch
# management (PuTTY, Microsoft Edge) rather than a Windows update.
# ---------------------------------------------------------------------------

PATCH_CATEGORIES = {
    "e6cf1350-c01b-414d-a61f-263d14d133b4": "Critical Updates",
    "0fa1201d-4330-4fa8-8ae9-b877473b6441": "Security Updates",
    "28bc880e-0592-4cbf-8f95-c79b17911d5f": "Update Rollups",
    "cd5ffd1e-e932-4e3a-bf74-18bf0b1bbd83": "Updates",
    "e0789628-ce08-4437-be74-2495b842f43b": "Definition Updates",
    "b54e7d24-7add-428f-8b75-90a396fa584f": "Feature Packs",
    "68c5b0a3-d1a6-4553-ae49-01d3a7827828": "Service Packs",
    "b4832bd8-e735-4761-8daf-37f882276dab": "Tools",
    "ebfc1fc5-71a4-4f7b-9aca-3b9a503104a0": "Drivers",
    "77835c8d-62a7-41f5-82ad-f28d1af1e3b1": "Driver Sets",
    "871a0782-be12-a5c4-c57f-1bd6d9f7144e": "Third-Party Software",
}

# What counts as security-relevant when reporting a patch backlog. Definition
# Updates are deliberately excluded — Defender definitions turn over several
# times a day, so counting them makes every estate look permanently behind.
SECURITY_PATCH_CATEGORIES = {"Critical Updates", "Security Updates"}


def _patch_category(category_id: str) -> str:
    """WSUS classification GUID -> label. Unknown ids are returned as-is."""
    return PATCH_CATEGORIES.get((category_id or "").strip().lower(),
                                category_id or "Unknown")


def _bucket_for(module_name: str) -> str:
    needle = (module_name or "").lower()
    for key in _BUCKET_KEYS:
        if key in needle:
            return MONITOR_BUCKETS[key]
    return "other"


def _worst_state(states) -> str:
    """Roll a device's monitors up to its worst single state."""
    present = {s for s in states if s}
    for state in MONITOR_STATE_SEVERITY:
        if state in present:
            return state
    return next(iter(present), None)


def _data(payload, default=None):
    """Unwrap the API's {"data": ...} envelope."""
    value = (payload or {}).get("data")
    return default if value is None else value


# The API is inconsistent about timestamp format depending on the endpoint:
#   monitor lastScanTime    '2026-07-29 14:31:25.268 +0200'   (offset)
#   monitor transitionTime  '2026-02-16 10:47:30.205'         (naive, local)
#   device  checkin         '2026-07-29T14:31:22.168Z'        (ISO, UTC)
#   asset   installeddate   '2026-05-25 02:00:00.0'           (naive, local)
_TIMESTAMP_FORMATS = (
    "%Y-%m-%d %H:%M:%S.%f %z",
    "%Y-%m-%d %H:%M:%S %z",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
)


def _parse_api_datetime(value):
    """
    Any of the API's timestamp shapes -> datetime, or None.

    Naive values are returned naive; the age helpers below compare them against
    a naive local now, so an offset-less local timestamp is not silently read as
    UTC (which would shift every age by two hours on our instance).
    """
    if isinstance(value, datetime):
        return value

    text = (value or "").strip()
    if not text:
        return None

    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass

    for fmt in _TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


# Kept under its original name — the patch code and any external caller use it.
_parse_asset_datetime = _parse_api_datetime


def _age_hours(value):
    """Hours between `value` and now, or None. Negative clamps to 0."""
    parsed = _parse_api_datetime(value)
    if parsed is None:
        return None
    now = datetime.now(tz=parsed.tzinfo) if parsed.tzinfo else datetime.now()
    return round(max(0.0, (now - parsed).total_seconds() / 3600.0), 1)


def _age_days(value):
    hours = _age_hours(value)
    return None if hours is None else round(hours / 24.0, 1)


class NCentralClient:
    def __init__(self, base_url: str = None, api_token: str = None):
        raw_base = base_url or BASE_URL
        self.base_url = _normalize_base_url(raw_base) if raw_base else None
        self.api_token = api_token or API_TOKEN

        self._session = requests.Session()
        self._access_token = None  # str
        self._access_expires_at = 0.0  # epoch seconds
        self._auth_lock = threading.Lock()  # concurrent callers share one authenticate()

        self._cache = {}  # key -> (value, expires_at)
        self._cache_lock = threading.Lock()
        self._loader_locks = {}  # key -> Lock, so one thread loads per key

    def _assert_configured(self):
        if not self.base_url or not self.api_token:
            raise NCentralError("Missing NCENTRAL_API_BASE_URL or NCENTRAL_API_TOKEN in .env")

    def authenticate(self) -> str:
        self._assert_configured()

        res = self._session.post(
            f"{self.base_url}/api/auth/authenticate",
            headers={"Authorization": f"Bearer {self.api_token}"},
            timeout=REQUEST_TIMEOUT_S,
        )
        try:
            body = res.json()
        except ValueError:
            body = {}

        if not res.ok:
            message = body.get("message") or res.text
            msg = f"N-central authenticate failed ({res.status_code}): {message}"
            if res.status_code == 401 and "DmsLoginException" in (body.get("message") or ""):
                msg += (
                    " — this is an N-central-side role/permission problem on the API user, "
                    "not a client auth-format bug."
                )
            raise NCentralError(msg, status=res.status_code, body=body)

        access = (body.get("tokens") or {}).get("access") or {}
        if not access.get("token"):
            raise NCentralError(f"Unexpected /authenticate response shape: {body}")

        self._access_token = access["token"]
        self._access_expires_at = time.time() + float(access["expirySeconds"])
        return self._access_token

    def get_access_token(self) -> str:
        if self._access_token and self._access_expires_at - TOKEN_REFRESH_MARGIN_S > time.time():
            return self._access_token

        with self._auth_lock:
            # Another thread may have refreshed while we waited for the lock.
            if self._access_token and self._access_expires_at - TOKEN_REFRESH_MARGIN_S > time.time():
                return self._access_token
            return self.authenticate()

    def request(self, path: str, method: str = "GET", **kwargs) -> dict:
        """Low-level authenticated request.

        No automatic retry on 429/failure by design — callers get a fast,
        catchable error instead of a silent multi-second stall.
        """
        token = self.get_access_token()
        headers = {**kwargs.pop("headers", {}), "Authorization": f"Bearer {token}"}

        res = self._session.request(
            method,
            f"{self.base_url}{path}",
            headers=headers,
            timeout=kwargs.pop("timeout", REQUEST_TIMEOUT_S),
            **kwargs,
        )

        if res.status_code == 429:
            raise NCentralError(f"Rate limited (429) on {path}", status=429)
        if not res.ok:
            raise NCentralError(
                f"Request failed ({res.status_code}) on {path}: {res.text}",
                status=res.status_code,
            )
        return res.json()

    def paginate(self, path: str, page_size: int = 500, params: dict = None) -> dict:
        """Walks pageNumber 2..totalPages, fetching sequentially per endpoint.

        Callers can still run multiple *different* paginated resources
        concurrently via with_concurrency_limit.
        """
        def build_url(page_number: int) -> str:
            qs = urlencode({**(params or {}), "pageSize": page_size, "pageNumber": page_number})
            return f"{path}?{qs}"

        first = self.request(build_url(1))
        items = list(first["data"])

        for page_number in range(2, first["totalPages"] + 1):
            items.extend(self.request(build_url(page_number))["data"])

        return {
            "items": items,
            "totalItems": first["totalItems"],
            "totalPages": first["totalPages"],
        }

    @staticmethod
    def with_concurrency_limit(items, worker, limit: int = DEFAULT_CONCURRENCY) -> list:
        """Runs `worker(item, index)` over `items` with at most `limit` in flight."""
        items = list(items)
        if not items:
            return []
        with ThreadPoolExecutor(max_workers=min(limit, len(items))) as pool:
            return list(pool.map(lambda pair: worker(pair[1], pair[0]), enumerate(items)))

    def _get_cached(self, key: str, ttl_s: float, loader):
        """Cache `loader()` under `key` for `ttl_s`, with one load per key.

        The Node client caches the in-flight promise, so concurrent callers for
        the same key share a single request. The per-key lock below reproduces
        that: the first caller loads, the rest wait and then read the result.
        Matters here because an unshared miss means N duplicate bulk pulls into
        an API that 429s above ~5 concurrent requests.
        """
        with self._cache_lock:
            hit = self._cache.get(key)
            if hit and hit[1] > time.time():
                return hit[0]
            loader_lock = self._loader_locks.setdefault(key, threading.Lock())

        with loader_lock:
            # Another thread may have loaded this key while we waited.
            with self._cache_lock:
                hit = self._cache.get(key)
                if hit and hit[1] > time.time():
                    return hit[0]

            value = loader()  # failures propagate and are not cached
            with self._cache_lock:
                self._cache[key] = (value, time.time() + ttl_s)
            return value

    def list_org_units(self) -> list:
        return self.paginate("/api/org-units", page_size=500)["items"]

    def get_customers(self) -> list:
        """orgUnitType == "CUSTOMER" rows only.

        /api/org-units already has no historical/deleted rows, so no extra
        active-only filtering is needed.
        """
        return self._get_cached(
            "customers",
            CUSTOMER_LIST_TTL_S,
            lambda: [ou for ou in self.list_org_units() if ou.get("orgUnitType") == "CUSTOMER"],
        )

    def get_devices_for_org_unit(self, org_unit_id) -> list:
        """Every device under org_unit_id, including all descendant sites (each
        row is tagged with its own siteId/siteName) — no per-site call needed."""
        return self._get_cached(
            f"devices:{org_unit_id}",
            BULK_PULL_TTL_S,
            lambda: self.paginate(f"/api/org-units/{org_unit_id}/devices", page_size=500)["items"],
        )

    def get_custom_properties(self, org_unit_id) -> list:
        return self._get_cached(
            f"custom-properties:{org_unit_id}",
            BULK_PULL_TTL_S,
            lambda: self.paginate(
                f"/api/org-units/{org_unit_id}/custom-properties", page_size=100
            )["items"],
        )

    def get_device_filters(self) -> list:
        """Deployment-wide, flat list — orgUnitId query param is silently ignored
        by the API, and there is no "enabled" flag on these rows."""
        return self._get_cached(
            "device-filters",
            CUSTOMER_LIST_TTL_S,
            lambda: self.paginate("/api/device-filters", page_size=500)["items"],
        )

    # -- health, performance and patching ----------------------------------
    #
    # Endpoint availability below was probed live against our instance
    # (2026-07-29). Absent by design, do not re-add: /api/active-issues,
    # /api/patches, /api/patch-status, /api/service-monitor-status,
    # /api/devices/{id}/patch-status, /api/tasks/{taskId}, /api/service-items/{id}
    # — all 404. There is no OpenAPI document either (/api/api-docs 404s).
    #
    # Note the two response shapes: /api/devices and /api/org-units/{id}/active-issues
    # are paginated (data + totalPages), while the per-device sub-resources return
    # {"data": [...], "totalItems": n} with NO totalPages — so paginate() would
    # raise KeyError on them and _data() is used instead.

    def get_devices(self) -> list:
        """Every device the token can see, deployment-wide."""
        return self._get_cached(
            "devices:all",
            BULK_PULL_TTL_S,
            lambda: self.paginate("/api/devices", page_size=500)["items"],
        )

    def get_device(self, device_id) -> dict:
        """One device's summary row."""
        return _data(self.request(f"/api/devices/{device_id}"))

    def get_device_assets(self, device_id) -> dict:
        """
        Full asset record: os, processor, memory, logicaldevice (volumes),
        networkadapter, application and patch inventory.

        Heavy — ~200 KB per device on our estate — so it is cached and should
        never be pulled in a tight loop over a large customer.
        """
        return self._get_cached(
            f"assets:{device_id}",
            BULK_PULL_TTL_S,
            lambda: _data(self.request(f"/api/devices/{device_id}/assets")),
        )

    def get_service_monitor_status(self, device_id, use_cache: bool = True) -> list:
        """
        Every service monitor on a device with its current state.

        This is what N-central exposes instead of performance metrics: each row
        carries moduleName ("CPU", "Memory", "Disk", "Connectivity",
        "Patch Status v2", ...) and stateStatus ("Normal", "Warning", "Failed",
        "No Data", "Misconfigured"), plus lastScanTime and transitionTime — but
        no threshold and no measured value. "Disk = Warning" is available;
        "8% free" is not, at least not over REST. Probing confirmed there is no
        per-task drill-down endpoint to get the reading from — and the legacy
        SOAP deviceGetStatus returns the same fields, so this is the API's limit
        rather than this endpoint's.

        use_cache=False bypasses the 45-minute cache. The availability collector
        must set this: cached state carries an old transitionTime, so a cached
        read silently skips whatever changed in that window.
        """
        fetch = lambda: _data(self.request(  # noqa: E731 - matches the caching style below
            f"/api/devices/{device_id}/service-monitor-status"), [])
        if not use_cache:
            return fetch()
        return self._get_cached(f"monitors:{device_id}", BULK_PULL_TTL_S, fetch)

    def get_device_custom_properties(self, device_id) -> list:
        """Per-device custom property values (the org-unit call is separate)."""
        return self._get_cached(
            f"device-properties:{device_id}",
            BULK_PULL_TTL_S,
            lambda: _data(self.request(
                f"/api/devices/{device_id}/custom-properties"), []),
        )

    def get_device_scheduled_tasks(self, device_id) -> list:
        return _data(self.request(f"/api/devices/{device_id}/scheduled-tasks"), [])

    def get_active_issues(self, org_unit_id) -> list:
        """
        Current breaches for a customer/site — the monitors that are not Normal
        right now, one row per failing monitor.

        notificationState is an undecoded integer (6 and 8 both appear on our
        instance); serviceName/serviceType and _extra.deviceName are the useful
        fields. Deployment-wide /api/active-issues does not exist — this must be
        called per org unit.
        """
        return self._get_cached(
            f"active-issues:{org_unit_id}",
            CUSTOMER_LIST_TTL_S,
            lambda: self.paginate(
                f"/api/org-units/{org_unit_id}/active-issues", page_size=500)["items"],
        )

    # -- derived views over the above --------------------------------------

    def get_device_health(self, device_id, active_issues: list = None) -> dict:
        """
        Bucket a device's monitors into performance / connectivity / patch /
        other, and list the ones that are not Normal.

        Returned counts are of *monitors*, not of measurements.

        Pass `active_issues` (this device's rows, or the whole customer's — they
        are filtered by deviceId here) and it is treated as authoritative for
        what is currently breached. This matters: probing found monitors sitting
        in active-issues while their own stateStatus reads "No Data" — TCP
        Connectivity and AMP Reboot Required both behave this way — so counting
        breaches from stateStatus alone silently under-reports. A monitor
        appearing in active-issues is marked active_issue=True and counted as a
        problem regardless of the state its own row claims.
        """
        monitors = self.get_service_monitor_status(device_id)

        issue_task_ids, issue_states = set(), {}
        for issue in (active_issues or []):
            if str(issue.get("deviceId")) != str(device_id):
                continue
            task_id = issue.get("taskId")
            issue_task_ids.add(task_id)
            issue_states[task_id] = issue.get("notificationState")

        buckets = {"performance": [], "connectivity": [], "patch": [], "other": []}

        for monitor in monitors:
            module  = (monitor.get("moduleName") or "").strip()
            task_id = monitor.get("taskId")
            stale_after = monitor.get("timeToStale")
            scan_age_hours = _age_hours(monitor.get("lastScanTime"))
            row = {
                "module":              module,
                "state":               monitor.get("stateStatus"),
                "active_issue":        task_id in issue_task_ids,
                "notification_state":  issue_states.get(task_id),
                "last_scan":           monitor.get("lastScanTime"),
                "transition_time":     monitor.get("transitionTime"),
                # How long the monitor has held its current state — for a failing
                # monitor this is the age of the fault.
                "state_age_days":      _age_days(monitor.get("transitionTime")),
                "scan_age_hours":      scan_age_hours,
                # Has the monitor stopped reporting within its own tolerance?
                "scan_overdue":        (
                    None if scan_age_hours is None or not stale_after
                    else scan_age_hours * 60 > float(stale_after)
                ),
                "task_id":             task_id,
                "service_id":          monitor.get("serviceId"),
                "stale_after_mins":    stale_after,
            }
            buckets[_bucket_for(module)].append(row)

        all_rows = [m for group in buckets.values() for m in group]
        problems = [m for m in all_rows
                    if m["active_issue"] or m["state"] not in HEALTHY_MONITOR_STATES]

        # An unmatched issue means a breach on a task the monitor list did not
        # return — surfaced rather than dropped.
        matched = {m["task_id"] for m in all_rows}
        unmatched = [i for i in (active_issues or [])
                     if str(i.get("deviceId")) == str(device_id)
                     and i.get("taskId") not in matched]

        states = [m["state"] for m in all_rows]
        if issue_task_ids or unmatched:
            states.append("Failed")

        problem_ages = [p["state_age_days"] for p in problems
                        if p["state_age_days"] is not None]
        overdue = [m for m in all_rows if m["scan_overdue"]]

        return {
            # Ageing: how long the worst fault on this device has been in place,
            # and how many monitors have stopped reporting inside their own
            # staleness tolerance.
            "oldest_problem_days":  max(problem_ages) if problem_ages else None,
            "newest_problem_days":  min(problem_ages) if problem_ages else None,
            "scan_overdue_count":   len(overdue),
            "device_id":        device_id,
            "monitor_count":    len(monitors),
            "problem_count":    len([p for p in problems
                                     if p["active_issue"]
                                     or p["state"] not in UNKNOWN_MONITOR_STATES]),
            "no_data_count":    len([p for p in problems
                                     if not p["active_issue"]
                                     and p["state"] in UNKNOWN_MONITOR_STATES]),
            "active_issue_count": len(issue_task_ids) + len(unmatched),
            "worst_state":      _worst_state(states),
            "buckets":          buckets,
            "problems":         problems,
            "unmatched_issues": unmatched,
        }

    def get_device_patch_status(self, device_id) -> dict:
        """
        Patch position for one device, from the asset record's patch inventory.

        This is the only patch data on the REST API — there is no /patches
        endpoint. Each entry has title, installationresult ("Installed" /
        "Not Installed"), installeddate and a category GUID (the WSUS
        classification id, not decoded here).

        "Not Installed" is the list of patches the device still needs, so
        `missing` is the answer to "what needs patching". Windows only: Linux
        devices come back with an empty patch section.

        The Patch Status v2 / Reboot Required monitor states are included
        alongside, since those are what N-central alerts on.
        """
        assets  = self.get_device_assets(device_id)
        entries = (((assets.get("_extra") or {}).get("patch") or {}).get("list")) or []

        installed, missing = [], []
        for entry in entries:
            row = {
                "title":        entry.get("title"),
                "category":     _patch_category(entry.get("category")),
                "category_id":  entry.get("category"),
                "installed_at": _parse_asset_datetime(entry.get("installeddate")),
                "result":       entry.get("installationresult"),
            }
            row["is_security"] = row["category"] in SECURITY_PATCH_CATEGORIES
            if (entry.get("installationresult") or "").strip().lower() == "not installed":
                missing.append(row)
            else:
                installed.append(row)

        missing_by_category = {}
        for row in missing:
            missing_by_category[row["category"]] = missing_by_category.get(row["category"], 0) + 1

        monitor_states = {
            (m.get("moduleName") or "").strip(): m.get("stateStatus")
            for m in self.get_service_monitor_status(device_id)
            if _bucket_for((m.get("moduleName") or "").strip()) == "patch"
        }

        installed_dates = [r["installed_at"] for r in installed if r["installed_at"]]

        return {
            "device_id":            device_id,
            "patch_entry_count":    len(entries),
            "installed_count":      len(installed),
            "missing_count":        len(missing),
            # The headline number for a report: excludes Defender definition
            # churn and third-party app versions.
            "missing_security_count": len([r for r in missing if r["is_security"]]),
            "missing_by_category":  missing_by_category,
            "missing":              missing,
            "installed":            installed,
            "last_installed_at":    max(installed_dates) if installed_dates else None,
            # Patch currency. Note this is the age of the newest *recorded*
            # install: installeddate is blank on a fair number of entries, so a
            # device can be better patched than this number suggests.
            "days_since_last_install": (
                _age_days(max(installed_dates)) if installed_dates else None
            ),
            "undated_installed_count": len([r for r in installed
                                            if r["installed_at"] is None]),
            "patch_monitor_states": monitor_states,
            "reboot_monitor_state": monitor_states.get("Reboot Required"),
        }

    def get_customer_health(self, org_unit_id, include_patches: bool = False,
                            limit: int = DEFAULT_CONCURRENCY) -> dict:
        """
        Health rollup for every device under a customer, plus its active issues.

        include_patches also pulls each device's asset record — accurate but
        expensive (~200 KB per device), so it is off by default.

        Fan-out goes through with_concurrency_limit because the API 429s above
        roughly five concurrent requests.
        """
        devices = self.get_devices_for_org_unit(org_unit_id)
        # Fetched once up front — every device's health is scored against it.
        issues = self.get_active_issues(org_unit_id)

        def load(device, _index):
            device_id = device.get("deviceId")
            row = {
                "device_id":    device_id,
                "name":         device.get("longName"),
                "device_class": device.get("deviceClass"),
                "os":           device.get("supportedOsLabel") or device.get("supportedOs"),
                "site_name":    device.get("siteName"),
                "last_checkin": device.get("lastApplianceCheckinTime"),
                # A device that has not checked in is the one case where the
                # monitor states are all stale and cannot be trusted.
                "checkin_age_hours": _age_hours(device.get("lastApplianceCheckinTime")),
            }
            try:
                row["health"] = self.get_device_health(device_id, active_issues=issues)
            except NCentralError as error:
                row["health"] = None
                row["error"] = str(error)
            if include_patches:
                try:
                    row["patch"] = self.get_device_patch_status(device_id)
                except NCentralError as error:
                    row["patch"] = None
                    row.setdefault("error", str(error))
            return row

        rows = self.with_concurrency_limit(devices, load, limit=limit)

        return {
            "org_unit_id":   org_unit_id,
            "device_count":  len(rows),
            "devices":       rows,
            "active_issues": issues,
        }


# Module-level default client, so callers can use the functions directly the way
# the Node module's named exports were used:
#     from ncentral_client import get_devices
# Delegation replaces what used to be a hand-written alias for every method —
# that list had to be extended by hand each time the class grew, and silently
# went stale when it wasn't.
client = NCentralClient()


def __getattr__(name):
    """Fall back to the default client for unknown module attributes (PEP 562).

    Only fires for names this module does not define itself, and only for public
    ones, so a typo still raises AttributeError rather than returning something
    surprising. Inside this module the bare names do not exist — use `client.x`.
    """
    if not name.startswith("_"):
        try:
            return getattr(client, name)
        except AttributeError:
            pass
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _summarise_customer(org_unit_id, include_patches: bool = False) -> None:
    """Print a health/patch overview for one customer. Used by the CLI below."""
    report = client.get_customer_health(org_unit_id, include_patches=include_patches)

    print(f"org unit {org_unit_id}: {report['device_count']} devices, "
          f"{len(report['active_issues'])} active issues\n")

    # Worst-aged faults first — that is the order a service review reads in.
    def sort_key(device):
        health = device.get("health") or {}
        return (-(health.get("oldest_problem_days") or 0), device.get("name") or "")

    for device in sorted(report["devices"], key=sort_key):
        health = device.get("health") or {}
        oldest = health.get("oldest_problem_days")
        checkin = device.get("checkin_age_hours")
        line = (f"  {device['name'] or '?':<24} {str(health.get('worst_state')):<10} "
                f"{health.get('problem_count', 0)} problem "
                f"{health.get('no_data_count', 0)} no-data "
                f"of {health.get('monitor_count', 0)} monitors")
        if oldest is not None:
            line += f"   oldest fault {oldest:.0f}d"
        if checkin is not None and checkin > 24:
            line += f"   no check-in {checkin / 24:.0f}d"
        patch = device.get("patch") if include_patches else None
        # Devices with no patch inventory at all (Linux, network kit) would
        # otherwise read as a clean "missing: 0", which is not the same thing.
        if patch and patch["patch_entry_count"]:
            line += (f"   missing: {patch['missing_count']}"
                     f" ({patch['missing_security_count']} security)")
            days = patch.get("days_since_last_install")
            if days is not None:
                line += f"   last patched {days:.0f}d ago"
        print(line)

        problems = sorted(
            (p for p in health.get("problems", [])
             if p["active_issue"] or p["state"] not in UNKNOWN_MONITOR_STATES),
            key=lambda p: -(p["state_age_days"] or 0),
        )
        for problem in problems:
            marker = "!!" if problem["active_issue"] else " !"
            age = problem["state_age_days"]
            age_text = f"{age:>5.0f}d" if age is not None else "    ?"
            print(f"     {marker} {problem['module']:<32} {problem['state']:<14}"
                  f" {age_text}   since {problem['transition_time']}")
        for row in (patch or {}).get("missing", []):
            flag = "SEC" if row["is_security"] else "   "
            print(f"      {flag} {row['category']:<22} {(row['title'] or '')[:70]}")

    if report["active_issues"]:
        print("\n  active issues:")
        for issue in report["active_issues"]:
            extra = issue.get("_extra") or {}
            print(f"    {extra.get('deviceName', '?'):<24} "
                  f"{issue.get('serviceName', '?'):<42} "
                  f"{issue.get('serviceType', '')} state={issue.get('notificationState')}")


if __name__ == "__main__":
    import sys

    args = [a for a in sys.argv[1:] if a != "--patches"]
    with_patches = "--patches" in sys.argv[1:]

    if args:
        _summarise_customer(args[0], include_patches=with_patches)
    else:
        for customer in client.get_customers():
            print(f"{customer.get('orgUnitId'):>6}  {customer.get('orgUnitName')}")
        print("\nusage: python ncentral_client.py <orgUnitId> [--patches]")
