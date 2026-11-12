import os
import requests
from dotenv import load_dotenv

load_dotenv()

API_URL = "https://api.backup.management/jsonapi"
REQUEST_TIMEOUT = 60  # seconds; without a timeout a stalled connection would hang until the job timeout


class CoveClient:
    def __init__(self, username: str = None, password: str = None, partner: str = None):
        # COVE_USERNAME / COVE_PASSWORD are preferred; the original "Login" / "SECRET_KEY" names still work.
        self.username = username or os.getenv("COVE_USERNAME") or os.getenv("Login")
        self.password = password or os.getenv("COVE_PASSWORD") or os.getenv("SECRET_KEY")
        self.partner = partner or os.getenv("COVE_PARTNER")
        self.visa = None

    def _post(self, method: str, params: dict) -> dict:
        payload = {
            "jsonrpc": "2.0",
            "id": "jsonrpc",
            "method": method,
            "params": params,
        }
        if self.visa:
            payload["visa"] = self.visa

        response = requests.post(API_URL, json=payload, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()

        if "error" in data:
            raise RuntimeError(f"API error [{method}]: {data['error']}")

        if "visa" in data:
            self.visa = data["visa"]

        return data.get("result", {})

    def login(self) -> dict:
        if not self.username or not self.password:
            raise RuntimeError("Cove credentials missing: set COVE_USERNAME and COVE_PASSWORD in .env")
        result = self._post("Login", {
            "username": self.username,
            "password": self.password,
        })
        # Cache partner ID from login response if available
        inner = result.get("result", {})
        if isinstance(inner, dict) and "PartnerId" in inner:
            self.partner_id = inner["PartnerId"]
        return result

    def get_partner_info(self, name: str = None) -> dict:
        return self._post("GetPartnerInfo", {"name": name or self.partner})

    def enumerate_partners(self, parent_partner_id: int, recursive: bool = True, fields: list = None) -> list:
        result = self._post("EnumeratePartners", {
            "parentPartnerId": parent_partner_id,
            "fetchRecursively": recursive,
            "fields": fields or [0, 1, 3, 4, 5, 8],
        })
        return result.get("result", [])

    def enumerate_accounts(self, partner_id: int) -> list:
        result = self._post("EnumerateAccounts", {"partnerId": partner_id})
        return result.get("result", [])

    def enumerate_account_statistics(
        self,
        partner_id: int,
        columns: list = None,
        filter_str: str = None,
        start: int = 0,
        count: int = 100,
        order_by: str = None,
    ) -> dict:
        query = {
            "PartnerId": partner_id,
            "StartRecordNumber": start,
            "RecordsCount": count,
            "Columns": columns or ["I0", "I1", "I8", "I14", "I17", "I18", "I59"],
        }
        if filter_str:
            query["Filter"] = filter_str
        if order_by:
            query["OrderBy"] = order_by

        return self._post("EnumerateAccountStatistics", {"query": query})

    def enumerate_users(self, parent_partner_id: int) -> list:
        result = self._post("EnumerateUsers", {"parentPartnerId": parent_partner_id})
        return result.get("result", [])

    def enumerate_locations(self) -> list:
        result = self._post("EnumerateLocations", {})
        return result.get("result", [])

    def edit_account(self, account_id: int, settings: dict) -> dict:
        return self._post("EditAccount", {
            "accountInfo": {
                "Id": account_id,
                "Settings": [settings],
            }
        })

    def __enter__(self):
        self.login()
        return self

    def __exit__(self, *_):
        self.visa = None


if __name__ == "__main__":
    cove = CoveClient()
    login_result = cove.login()
    partner_id = login_result["result"]["PartnerId"]
    print(f"Logged in as: {login_result['result']['Name']}  (PartnerId: {partner_id})\n")

    stats = cove.enumerate_account_statistics(
        partner_id,
        columns=["I0", "I1", "I8", "I14", "I17", "I18"],
        count=10,
    )
    for device in stats.get("result", []):
        settings = {k: v for s in device.get("Settings", []) for k, v in s.items()}
        print(f"  [{device['AccountId']}] {settings}")
