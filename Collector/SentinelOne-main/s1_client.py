"""
SentinelOne management console — REST API client.

A thin wrapper over /web/api/v2.1 that handles auth, pagination and the
`{"data": ..., "pagination": ...}` envelope. No analysis happens here; that is
s1_metrics.py's job, and shaping for the database is s1_dataset.py's.

Credentials come from .env next to this file:
    SENTINELONE_BASE_URL=https://euce1-swprd2.sentinelone.net
    SENTINELONE_API_TOKEN=...

Every listing call takes an optional `site_ids` argument. Passing it filters
server-side, so a single-customer run pulls only that customer's data instead of
the whole estate.

Entry points:
  S1Client(base_url, token)      explicit construction
  S1Client.from_env()            read .env
  client.get_sites()             the customer roster
  client.get_threats(...)        threats in a window
  client.get_agents(...)         managed endpoints
  client.latest_agent_version()  newest GA Windows package

CLI:
  python s1_client.py sites          list sites with agent counts
  python s1_client.py check          verify the token works
"""

import os
import re
from pathlib import Path
from urllib.parse import urlencode

import requests

SCRIPT_DIR = Path(__file__).resolve().parent

API_PREFIX = "/web/api/v2.1"
PAGE_LIMIT = 100
TIMEOUT = 120


def load_env(env_file=None) -> dict:
    """
    Read a simple KEY=VALUE .env file.

    Deliberately the same parser generate_monthly_reports.py uses (and the same
    utf-8-sig handling, so a BOM-prefixed file written by PowerShell still
    parses) rather than python-dotenv, so both entry points agree on what the
    file means. Real environment variables win over the file.
    """
    env_file = Path(env_file) if env_file else SCRIPT_DIR / ".env"
    values = {}
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
            if m:
                values[m.group(1)] = m.group(2).strip()
    values.update({k: v for k, v in os.environ.items() if k in values or k.startswith(
        ("SENTINELONE_", "S1_", "IPC_", "COVE_"))})
    return values


def get(obj, *path, default=None):
    """Safely walk a nested dict path. Treats null like missing."""
    cur = obj
    for key in path:
        if isinstance(cur, dict) and key in cur and cur[key] is not None:
            cur = cur[key]
        else:
            return default
    return cur


class S1Client:
    def __init__(self, base_url: str, token: str):
        if not base_url or not token:
            raise ValueError("base_url and token are both required")
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"ApiToken {token}",
            "Content-Type": "application/json",
        })

    @classmethod
    def from_env(cls, env_file=None) -> "S1Client":
        env = load_env(env_file)
        token = env.get("SENTINELONE_API_TOKEN", "")
        base_url = env.get("SENTINELONE_BASE_URL", "")
        if not token or not base_url:
            raise RuntimeError(
                "SENTINELONE_API_TOKEN and SENTINELONE_BASE_URL must be set in "
                f"{env_file or SCRIPT_DIR / '.env'}"
            )
        return cls(base_url, token)

    # -- transport ----------------------------------------------------------

    def _url(self, path: str, params: dict = None) -> str:
        query = {k: v for k, v in (params or {}).items() if v not in (None, "")}
        url = f"{self.base_url}{API_PREFIX}/{path.lstrip('/')}"
        return f"{url}?{urlencode(query)}" if query else url

    def _get(self, url: str) -> dict:
        resp = self.session.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    def get_paged(self, path: str, params: dict = None) -> list:
        """Follow pagination cursors and collect every data record."""
        params = dict(params or {})
        params.setdefault("limit", PAGE_LIMIT)
        all_data = []
        cursor = None
        while True:
            page_params = dict(params)
            if cursor:
                page_params["cursor"] = cursor
            r = self._get(self._url(path, page_params))
            all_data.extend(r.get("data") or [])
            cursor = get(r, "pagination", "nextCursor")
            if not cursor:
                break
        return all_data

    def get_count(self, path: str, params: dict = None) -> int:
        """totalItems for a query, without pulling the rows."""
        params = dict(params or {})
        params["limit"] = 1
        r = self._get(self._url(path, params))
        return get(r, "pagination", "totalItems", default=0)

    # -- resources ----------------------------------------------------------

    @staticmethod
    def _site_param(site_ids) -> str:
        """Normalise a str / list of site ids into the API's comma-joined form."""
        if not site_ids:
            return None
        if isinstance(site_ids, str):
            return site_ids
        return ",".join(str(s) for s in site_ids)

    def get_sites(self, include_inactive: bool = True) -> list:
        """
        Every site on the console — the customer roster.

        /sites nests its payload one level deeper than the other endpoints
        ({"data": {"sites": [...]}}), and its cursor lives in the same place, so
        it gets its own loop rather than get_paged.
        """
        sites, cursor = [], None
        while True:
            params = {"limit": PAGE_LIMIT}
            if not include_inactive:
                params["state"] = "active"
            if cursor:
                params["cursor"] = cursor
            r = self._get(self._url("sites", params))
            sites.extend(get(r, "data", "sites", default=[]))
            cursor = get(r, "pagination", "nextCursor")
            if not cursor:
                break
        return sites

    def get_threats(self, month_start: str, month_end: str, site_ids=None) -> list:
        """Threats created inside [month_start, month_end], newest first."""
        return self.get_paged("threats", {
            "createdAt__gte": month_start,
            "createdAt__lte": month_end,
            "siteIds": self._site_param(site_ids),
            "sortBy": "createdAt",
            "sortOrder": "desc",
        })

    def count_threats(self, month_start: str, month_end: str, site_ids=None) -> int:
        return self.get_count("threats", {
            "createdAt__gte": month_start,
            "createdAt__lte": month_end,
            "siteIds": self._site_param(site_ids),
        })

    def get_agents(self, site_ids=None) -> list:
        """
        Managed endpoints — current state only; the API keeps no agent history.

        Note this is *not* filtered by the report month: an endpoint's health is
        whatever it is at capture time, which is why loads are snapshots.
        """
        return self.get_paged("agents", {"siteIds": self._site_param(site_ids)})

    def latest_agent_version(self) -> str:
        """Newest GA Windows .msi package version, or 'N/A'."""
        r = self._get(self._url("update/agent/packages", {
            "limit": 1,
            "sortBy": "version",
            "sortOrder": "desc",
            "osTypes": "windows",
            "fileExtension": ".msi",
            "status": "ga",
        }))
        data = r.get("data") or []
        return data[0].get("version") if data else "N/A"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv) -> int:
    cmd = argv[1] if len(argv) > 1 else ""

    if cmd == "check":
        client = S1Client.from_env()
        version = client.latest_agent_version()
        print(f"OK — {client.base_url} reachable. Latest GA agent: {version}")
        return 0

    if cmd == "sites":
        client = S1Client.from_env()
        sites = client.get_sites()
        print(f"{len(sites)} sites\n")
        for s in sorted(sites, key=lambda x: (x.get("name") or "").lower()):
            agents = s.get("activeLicenses") or 0
            print(f"  {str(s.get('id')):<22} {(s.get('name') or ''):<45} "
                  f"{(s.get('state') or ''):<10} {agents:>5} licensed")
        return 0

    print(__doc__)
    return 1


if __name__ == "__main__":
    import sys
    sys.exit(_main(sys.argv))
