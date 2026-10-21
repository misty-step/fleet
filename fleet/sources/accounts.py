"""Account room from Upkeep's published status.json."""
import json
import urllib.error
import urllib.request


class Accounts:
    name = "accounts"
    interval = 30

    def __init__(self, url):
        self.url = url

    def collect(self, hub):
        if not self.url:
            return {"accounts": []}
        try:
            with urllib.request.urlopen(self.url, timeout=20) as response:
                status = json.load(response)
        except (urllib.error.URLError, OSError, ValueError):
            raise RuntimeError("accounts-unavailable") from None
        if not isinstance(status, dict) or not isinstance(status.get("accounts"), list):
            raise ValueError("accounts-invalid-status")
        accounts = []
        for account in status["accounts"]:
            if not isinstance(account, dict):
                raise ValueError("accounts-invalid-account")
            # Only retain inputs used by the snapshot, never sign-in or personal details.
            item = {key: account.get(key) for key in (
                "instance_id", "name", "provider", "state", "room_percent", "resets_at",
                "observed_at", "age_s", "pace")}
            item["allowances"] = [
                {key: allowance.get(key) for key in (
                    "active", "label", "room_percent", "resets_at", "observed_at", "age_s", "pace")}
                for allowance in account.get("allowances", []) if isinstance(allowance, dict)]
            item["status_observed_at"] = status.get("observed_at")
            item["status_age_s"] = status.get("age_s")
            item["evaluated_at"] = status.get("evaluated_at")
            item["stale_after_s"] = status.get("stale_after_s")
            accounts.append(item)
        return {"accounts": accounts}
