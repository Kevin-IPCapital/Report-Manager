"""Quick smoke test for the N-central REST API using credentials from .env

Run with: python test_connection.py

Note: this one deliberately uses raw requests calls (no client module) so it can
be used to debug the auth handshake itself. It reads NCENTRAL_JWT; if that is
empty it falls back to NCENTRAL_API_TOKEN, which is the same credential.
"""

import json
import os
import sys

import requests
from dotenv import load_dotenv

from ncentral_client import _normalize_base_url

load_dotenv()

BASE = os.getenv("NCENTRAL_API_BASE_URL")
JWT = os.getenv("NCENTRAL_JWT") or os.getenv("NCENTRAL_API_TOKEN")

TIMEOUT_S = 15


def main():
    if not BASE or not JWT:
        raise RuntimeError("Missing NCENTRAL_API_BASE_URL or NCENTRAL_JWT in .env")

    base = _normalize_base_url(BASE)
    print(f"Base URL: {base}")

    # 1. Health check (no auth)
    health = requests.get(f"{base}/api/health", timeout=TIMEOUT_S)
    print("GET /api/health ->", health.status_code, health.text)

    # 2. Exchange JWT for access token
    auth_res = requests.post(
        f"{base}/api/auth/authenticate",
        headers={"Authorization": f"Bearer {JWT}"},
        timeout=TIMEOUT_S,
    )
    try:
        auth_body = auth_res.json()
    except ValueError:
        auth_body = {}
    print("POST /api/auth/authenticate ->", auth_res.status_code, auth_body)

    if not auth_res.ok:
        print("Authentication failed, stopping here.", file=sys.stderr)
        return

    access_token = (
        ((auth_body.get("tokens") or {}).get("access") or {}).get("token")
        or (auth_body.get("access") or {}).get("token")
        or auth_body.get("accessToken")
    )
    if not access_token:
        print("Could not find access token in response shape:", auth_body, file=sys.stderr)
        return

    # 3. List service organizations
    so_res = requests.get(
        f"{base}/api/service-orgs?pageSize=10",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=TIMEOUT_S,
    )
    try:
        so_body = so_res.json()
    except ValueError:
        so_body = {}
    print("GET /api/service-orgs ->", so_res.status_code, json.dumps(so_body, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as err:
        print(f"Error: {err}", file=sys.stderr)
        sys.exit(1)
