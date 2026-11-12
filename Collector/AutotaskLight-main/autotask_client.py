import argparse
import json
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ZONE_INFORMATION_URL = "https://webservices.autotask.net/atservicesrest/v1.0/zoneInformation"

DEFAULT_TIMEZONE = "Africa/Johannesburg"
BUSINESS_START_HOUR = 8
BUSINESS_END_HOUR = 17

# The PowerShell client takes Windows time zone ids. Accept the common ones so the
# same arguments work against both clients.
WINDOWS_TIMEZONE_ALIASES = {
    "South Africa Standard Time": "Africa/Johannesburg",
    "GMT Standard Time": "Europe/London",
    "W. Europe Standard Time": "Europe/Berlin",
    "UTC": "UTC",
}

TICKET_REPORT_FIELDS = [
    "id",
    "ticketNumber",
    "companyID",
    "title",
    "description",
    "resolution",
    "ticketType",
    "priority",
    "issueType",
    "subIssueType",
    "serviceLevelAgreementHasBeenMet",
    "firstResponseDateTime",
    "firstResponseDueDateTime",
    "createDate",
    "completedDate",
    "status",
]

TICKET_PICKLIST_FIELDS = ["ticketType", "priority", "issueType", "subIssueType", "status"]


class AutotaskError(Exception):
    """Raised when Autotask returns an error or the client is misconfigured."""


def load_dotenv(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if key and key not in os.environ:
            os.environ[key] = value


def resolve_timezone(name: str) -> ZoneInfo:
    lookup = WINDOWS_TIMEZONE_ALIASES.get(name, name)
    try:
        return ZoneInfo(lookup)
    except ZoneInfoNotFoundError as error:
        raise AutotaskError(
            f"Unknown time zone '{name}'. Use an IANA name such as '{DEFAULT_TIMEZONE}'."
        ) from error


def parse_autotask_datetime(value: Any) -> datetime | None:
    """Parse an Autotask timestamp into an aware UTC datetime."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as error:
            raise AutotaskError(f"Could not parse Autotask date '{value}'.") from error

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def to_timezone(value: Any, tz: ZoneInfo) -> datetime | None:
    parsed = parse_autotask_datetime(value)
    return parsed.astimezone(tz) if parsed else None


def format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_business_day(day: date, holiday_dates: dict[str, str]) -> bool:
    if day.weekday() >= 5:
        return False
    return day.isoformat() not in holiday_dates


def business_hours_between(
    start_value: Any,
    end_value: Any,
    holiday_dates: dict[str, str],
    tz: ZoneInfo,
    business_start_hour: int = BUSINESS_START_HOUR,
    business_end_hour: int = BUSINESS_END_HOUR,
) -> float | None:
    """Elapsed hours between two timestamps, counting only business hours."""
    start = to_timezone(start_value, tz)
    end = to_timezone(end_value, tz)

    if not start or not end:
        return None
    if end <= start:
        return 0.0

    total_hours = 0.0
    day = start.date()

    while day <= end.date():
        if is_business_day(day, holiday_dates):
            window_start = datetime.combine(day, time(business_start_hour), tzinfo=tz)
            window_end = datetime.combine(day, time(business_end_hour), tzinfo=tz)
            effective_start = max(start, window_start)
            effective_end = min(end, window_end)

            if effective_end > effective_start:
                total_hours += (effective_end - effective_start).total_seconds() / 3600

        day += timedelta(days=1)

    return round(total_hours, 2)


def first_response_met(
    first_response: Any,
    first_response_due: Any,
) -> bool | None:
    responded = parse_autotask_datetime(first_response)
    due = parse_autotask_datetime(first_response_due)

    if not responded or not due:
        return None

    return responded <= due


class AutotaskClient:
    def __init__(
        self,
        username: str,
        secret: str,
        integration_code: str,
        base_url: str | None = None,
        timeout: int = 30,
    ) -> None:
        if not username:
            raise AutotaskError("AUTOTASK_USERNAME is required.")
        if not secret:
            raise AutotaskError("AUTOTASK_SECRET is required.")
        if not integration_code:
            raise AutotaskError("AUTOTASK_INTEGRATION_CODE is required.")

        self.username = username
        self.secret = secret
        self.integration_code = integration_code
        self.base_url = self._normalize_base_url(base_url) if base_url else None
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "AutotaskClient":
        load_dotenv()
        return cls(
            username=os.environ.get("AUTOTASK_USERNAME", ""),
            secret=os.environ.get("AUTOTASK_SECRET", ""),
            integration_code=os.environ.get("AUTOTASK_INTEGRATION_CODE", ""),
            base_url=os.environ.get("AUTOTASK_BASE_URL") or None,
        )

    def discover_base_url(self) -> str:
        query = urlencode({"user": self.username})
        response = self._send_json_request(
            f"{ZONE_INFORMATION_URL}?{query}",
            headers={},
            include_auth=False,
        )

        url = response.get("url")
        if not url:
            raise AutotaskError(f"Zone discovery did not return a URL: {response}")

        self.base_url = self._normalize_base_url(url)
        return self.base_url

    def get_by_id(self, entity: str, entity_id: int | str) -> dict[str, Any]:
        return self.request("GET", f"{entity}/{entity_id}")

    def query(
        self,
        entity: str,
        filters: list[dict[str, Any]],
        include_fields: list[str] | None = None,
        max_records: int | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"filter": filters}

        if include_fields:
            body["includeFields"] = include_fields
        if max_records:
            body["MaxRecords"] = max_records

        return self.request("POST", f"{entity}/query", body=body)

    def query_all(
        self,
        entity: str,
        filters: list[dict[str, Any]],
        include_fields: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        first_page = self.query(entity, filters, include_fields=include_fields)
        items = list(first_page.get("items", []))
        next_page_url = first_page.get("pageDetails", {}).get("nextPageUrl")

        while next_page_url:
            page = self.request_url("GET", next_page_url)
            items.extend(page.get("items", []))
            next_page_url = page.get("pageDetails", {}).get("nextPageUrl")

        return items

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.base_url:
            self.discover_base_url()

        url = f"{self.base_url}v1.0/{path.lstrip('/')}"
        return self.request_url(method, url, body=body)

    def request_url(
        self,
        method: str,
        url: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._send_json_request(url, method=method, body=body)

    # -- metadata -------------------------------------------------------

    def entity_fields(self, entity: str) -> dict[str, Any]:
        return self.request("GET", f"{entity}/entityInformation/fields")

    def picklist_maps(self, entity: str, fields: list[str]) -> dict[str, dict[str, str]]:
        """Map picklist integer values to their labels, per field."""
        metadata = self.entity_fields(entity)
        maps: dict[str, dict[str, str]] = {}

        for field in metadata.get("fields", []):
            name = field.get("name")
            values = field.get("picklistValues")
            if name not in fields or not values:
                continue

            maps[name] = {str(value["value"]): value["label"] for value in values}

        return maps

    @staticmethod
    def resolve_picklist(
        maps: dict[str, dict[str, str]],
        field: str,
        value: Any,
    ) -> Any:
        if value is None:
            return None
        return maps.get(field, {}).get(str(value), value)

    # -- reporting helpers ----------------------------------------------

    def default_holiday_set_id(self) -> Any:
        try:
            location = self.query(
                "InternalLocationWithBusinessHours",
                [{"op": "eq", "field": "isDefault", "value": True}],
                include_fields=["id", "name", "isDefault", "holidaySetID", "noHoursOnHolidays"],
                max_records=1,
            )
        except AutotaskError as error:
            print(
                f"Warning: could not read default internal location holiday set: {error}",
                file=sys.stderr,
            )
            return None

        items = location.get("items") or []
        return items[0].get("holidaySetID") if items else None

    def holiday_dates(
        self,
        holiday_set_id: Any,
        from_date: datetime,
        to_date: datetime,
        tz: ZoneInfo,
    ) -> dict[str, str]:
        holidays: dict[str, str] = {}
        if not holiday_set_id:
            return holidays

        try:
            response = self.query(
                "Holidays",
                [
                    {"op": "eq", "field": "holidaySetID", "value": holiday_set_id},
                    {"op": "gte", "field": "holidayDate", "value": format_utc(from_date)},
                    {"op": "lte", "field": "holidayDate", "value": format_utc(to_date)},
                ],
                include_fields=["id", "holidayName", "holidayDate", "holidaySetID"],
                max_records=500,
            )
        except AutotaskError as error:
            print(
                f"Warning: could not read Autotask holidays for holiday set "
                f"{holiday_set_id}: {error}",
                file=sys.stderr,
            )
            return holidays

        for holiday in response.get("items") or []:
            local_date = to_timezone(holiday.get("holidayDate"), tz)
            if local_date:
                holidays[local_date.date().isoformat()] = holiday.get("holidayName")

        return holidays

    def ticket_sla_result(self, ticket_id: int) -> dict[str, Any] | None:
        try:
            response = self.query(
                "ServiceLevelAgreementResults",
                [{"op": "eq", "field": "ticketID", "value": ticket_id}],
                include_fields=[
                    "ticketID",
                    "serviceLevelAgreementName",
                    "isFirstResponseMet",
                    "isResolutionMet",
                    "firstResponseElapsedHours",
                    "resolutionElapsedHours",
                ],
                max_records=1,
            )
        except AutotaskError:
            return None

        items = response.get("items") or []
        return items[0] if items else None

    def ticket_notes_summary(self, ticket_id: int, max_records: int = 10) -> list[dict[str, Any]]:
        response = self.query(
            "TicketNotes",
            [{"op": "eq", "field": "ticketID", "value": ticket_id}],
            include_fields=["id", "createDateTime", "title", "description", "noteType", "publish"],
            max_records=max_records,
        )

        return [
            {
                "id": note.get("id"),
                "date": note.get("createDateTime"),
                "title": note.get("title"),
                "description": note.get("description"),
                "noteType": note.get("noteType"),
                "publish": note.get("publish"),
            }
            for note in response.get("items") or []
        ]

    def find_company(self, name: str) -> dict[str, Any]:
        response = self.query(
            "Companies",
            [{"op": "contains", "field": "CompanyName", "value": name}],
            include_fields=["id", "companyName"],
            max_records=5,
        )

        items = response.get("items") or []
        if not items:
            raise AutotaskError(f"No company found matching '{name}'.")
        if len(items) > 1:
            exact = [
                item
                for item in items
                if (item.get("companyName") or "").strip().casefold() == name.strip().casefold()
            ]
            if len(exact) == 1:
                return exact[0]

            matched = ", ".join(item.get("companyName", "?") for item in items)
            raise AutotaskError(
                f"More than one company matched '{name}' ({matched}). "
                "Use a more specific company name."
            )

        return items[0]

    def company_contacts(self, company_id: int | str) -> list[dict[str, Any]]:
        return self.query_all(
            "Contacts",
            [{"op": "eq", "field": "companyID", "value": company_id}],
            include_fields=[
                "id",
                "firstName",
                "lastName",
                "title",
                "emailAddress",
                "phone",
                "mobilePhone",
                "isActive",
            ],
        )

    def company_details(
        self,
        name: str | None = None,
        company_id: int | str | None = None,
        with_contacts: bool = False,
    ) -> dict[str, Any]:
        """Full company record, looked up by name or by id."""
        if company_id is None:
            if not name:
                raise AutotaskError("Provide either a company name or a company id.")
            company_id = self.find_company(name)["id"]

        details = self.get_by_id("Companies", company_id).get("item")
        if not details:
            raise AutotaskError(f"No company found with id {company_id}.")

        if with_contacts:
            details["contacts"] = self.company_contacts(company_id)

        return details

    def company_tickets_report(
        self,
        name: str,
        last_days: int = 7,
        max_records: int = 5,
        timezone_name: str = DEFAULT_TIMEZONE,
        with_notes: bool = False,
    ) -> list[dict[str, Any]]:
        company = self.find_company(name)
        tz = resolve_timezone(timezone_name)

        to_date = datetime.now(timezone.utc)
        from_date = to_date - timedelta(days=last_days)

        tickets = self.query(
            "Tickets",
            [
                {"op": "eq", "field": "companyID", "value": company["id"]},
                {"op": "gte", "field": "createDate", "value": format_utc(from_date)},
            ],
            include_fields=TICKET_REPORT_FIELDS,
            max_records=max_records,
        )

        maps = self.picklist_maps("Tickets", TICKET_PICKLIST_FIELDS)
        holiday_set_id = self.default_holiday_set_id()
        holidays = self.holiday_dates(
            holiday_set_id,
            from_date - timedelta(days=1),
            to_date + timedelta(days=30),
            tz,
        )

        rows: list[dict[str, Any]] = []
        for ticket in tickets.get("items") or []:
            sla_result = self.ticket_sla_result(ticket["id"])
            hours = business_hours_between(
                ticket.get("createDate"),
                ticket.get("completedDate"),
                holidays,
                tz,
            )

            row: dict[str, Any] = {
                "companyName": company.get("companyName"),
                "companyID": company.get("id"),
                "ticketID": ticket.get("id"),
                "ticketNumber": ticket.get("ticketNumber"),
                "title": ticket.get("title"),
                "description": ticket.get("description"),
                "ticketDetails": ticket.get("description"),
                "resolutionComments": ticket.get("resolution"),
                "ticketType": self.resolve_picklist(maps, "ticketType", ticket.get("ticketType")),
                "ticketPriority": self.resolve_picklist(maps, "priority", ticket.get("priority")),
                "ticketIssue": self.resolve_picklist(maps, "issueType", ticket.get("issueType")),
                "ticketSubIssue": self.resolve_picklist(
                    maps, "subIssueType", ticket.get("subIssueType")
                ),
                "slaMet": (
                    sla_result["isResolutionMet"]
                    if sla_result
                    else ticket.get("serviceLevelAgreementHasBeenMet")
                ),
                "firstResponseMet": (
                    sla_result["isFirstResponseMet"]
                    if sla_result
                    else first_response_met(
                        ticket.get("firstResponseDateTime"),
                        ticket.get("firstResponseDueDateTime"),
                    )
                ),
                "averageHours": hours,
                "businessHours": hours,
                "businessHoursStart": f"{BUSINESS_START_HOUR:02d}:00",
                "businessHoursEnd": f"{BUSINESS_END_HOUR:02d}:00",
                "holidaySetID": holiday_set_id,
                "holidaysExcluded": len(holidays),
                "firstResponseDateTime": ticket.get("firstResponseDateTime"),
                "firstResponseDueDateTime": ticket.get("firstResponseDueDateTime"),
                "createdDate": ticket.get("createDate"),
                "completedDate": ticket.get("completedDate"),
                "status": self.resolve_picklist(maps, "status", ticket.get("status")),
            }

            if with_notes:
                row["ticketNotes"] = self.ticket_notes_summary(ticket["id"])

            rows.append(row)

        return rows

    # -- transport -------------------------------------------------------

    def _send_json_request(
        self,
        url: str,
        method: str = "GET",
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        include_auth: bool = True,
    ) -> dict[str, Any]:
        request_headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        if include_auth:
            request_headers.update(
                {
                    "Username": self.username,
                    "Secret": self.secret,
                    "APIIntegrationcode": self.integration_code,
                }
            )

        if headers:
            request_headers.update(headers)

        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")

        request = Request(url, data=data, headers=request_headers, method=method.upper())

        try:
            with urlopen(request, timeout=self.timeout) as response:
                response_body = response.read().decode("utf-8")
        except HTTPError as error:
            error_body = error.read().decode("utf-8", errors="replace")
            raise AutotaskError(f"HTTP {error.code} from Autotask: {error_body}") from error
        except URLError as error:
            raise AutotaskError(f"Could not reach Autotask: {error.reason}") from error

        if not response_body:
            return {}

        try:
            return json.loads(response_body)
        except json.JSONDecodeError as error:
            raise AutotaskError(f"Autotask returned invalid JSON: {response_body}") from error

    @staticmethod
    def _normalize_base_url(url: str) -> str:
        normalized = url.strip()
        if not normalized:
            raise AutotaskError("AUTOTASK_BASE_URL is blank.")
        return normalized if normalized.endswith("/") else f"{normalized}/"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Autotask REST API helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("zone", help="Discover and print the Autotask API zone URL")

    companies = subparsers.add_parser("companies", help="Query active companies")
    companies.add_argument("--limit", type=int, default=5, help="Maximum records to request")
    companies.add_argument("--name", help="Only companies whose name contains this text")
    companies.add_argument(
        "--include-inactive",
        action="store_true",
        help="Include inactive companies as well as active ones",
    )

    company = subparsers.add_parser("company", help="Full detail record for one company")
    group = company.add_mutually_exclusive_group(required=True)
    group.add_argument("--name", help="Company name to match")
    group.add_argument("--id", dest="company_id", type=int, help="Company id, e.g. 367")
    company.add_argument(
        "--contacts",
        action="store_true",
        help="Include the company's contacts",
    )

    tickets = subparsers.add_parser("tickets", help="Recent ticket report for one company")
    tickets.add_argument("--company", default="Vulpes Technologies", help="Company name to match")
    tickets.add_argument("--days", type=int, default=7, help="How many days back to look")
    tickets.add_argument("--limit", type=int, default=10, help="Maximum tickets to request")
    tickets.add_argument(
        "--timezone",
        default=DEFAULT_TIMEZONE,
        help=f"Report time zone, IANA or Windows id (default {DEFAULT_TIMEZONE})",
    )
    tickets.add_argument(
        "--include-notes",
        action="store_true",
        help="Include the most recent ticket notes for each ticket",
    )

    fields = subparsers.add_parser("fields", help="Print field metadata for an entity")
    fields.add_argument("entity", help="Entity name, e.g. Tickets")
    fields.add_argument(
        "--picklists-only",
        action="store_true",
        help="Print only the picklist value maps",
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        client = AutotaskClient.from_env()

        if args.command == "zone":
            print(client.discover_base_url())
            return 0

        if args.command == "companies":
            filters: list[dict[str, Any]] = []
            if not args.include_inactive:
                filters.append({"op": "eq", "field": "IsActive", "value": True})
            if args.name:
                filters.append({"op": "contains", "field": "CompanyName", "value": args.name})
            if not filters:
                filters.append({"op": "exist", "field": "id"})

            result = client.query(
                "Companies",
                filters,
                include_fields=["id", "companyName", "isActive"],
                max_records=args.limit,
            )
            print(json.dumps(result, indent=2))
            return 0

        if args.command == "company":
            details = client.company_details(
                name=args.name,
                company_id=args.company_id,
                with_contacts=args.contacts,
            )
            print(json.dumps(details, indent=2))
            return 0

        if args.command == "tickets":
            report = client.company_tickets_report(
                name=args.company,
                last_days=args.days,
                max_records=args.limit,
                timezone_name=args.timezone,
                with_notes=args.include_notes,
            )
            print(json.dumps(report, indent=2))
            return 0

        if args.command == "fields":
            metadata = client.entity_fields(args.entity)
            if args.picklists_only:
                picklists = {
                    field["name"]: {
                        str(value["value"]): value["label"] for value in field["picklistValues"]
                    }
                    for field in metadata.get("fields", [])
                    if field.get("picklistValues")
                }
                print(json.dumps(picklists, indent=2))
            else:
                print(json.dumps(metadata, indent=2))
            return 0

        parser.error(f"Unknown command: {args.command}")
        return 2
    except AutotaskError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
