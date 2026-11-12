"""Smoke test: authenticate, then GET /api/org-units?pageSize=500&pageNumber=1,
and print totalItems plus the first 3 CUSTOMER-type org units.

Run with: python smoke_test.py
"""

import json
import sys

from ncentral_client import get_access_token, request


def main():
    access_token = get_access_token()
    print("Authenticated. Access token (truncated):", access_token[:12] + "...")

    page = request("/api/org-units?pageSize=500&pageNumber=1")
    print("totalItems:", page["totalItems"])

    first_three_customers = [
        ou for ou in page["data"] if ou.get("orgUnitType") == "CUSTOMER"
    ][:3]
    print("First 3 CUSTOMER org units:", json.dumps(first_three_customers, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as err:
        print(f"Error: {err}", file=sys.stderr)
        sys.exit(1)
