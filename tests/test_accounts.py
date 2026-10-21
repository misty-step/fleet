import copy
import io
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fleet import config, derive, hub
from fleet.sources.accounts import Accounts

NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
RESET = "2026-10-22T12:00:00Z"
STATUS = {
    "observed_at": NOW.isoformat(), "evaluated_at": NOW.isoformat(), "stale_after_s": 600,
    "accounts": [{
        "instance_id": "account-a", "name": "Example A", "provider": "codex", "state": "current",
        "email": "private@example.invalid", "sign_in": {"secret": "do-not-publish"},
        "room_percent": 10, "resets_at": RESET,
        "allowances": [{"label": "daily", "active": True, "room_percent": 99}, {
            "label": "weekly", "active": True, "room_percent": 48, "resets_at": RESET,
            "observed_at": NOW.isoformat(),
            "pace": {"burn_pp_per_hour": 2, "outlasts_reset": True, "runs_out_at": RESET},
        }],
    }],
}


class AccountsTest(unittest.TestCase):
    def collect(self, status):
        with patch("fleet.sources.accounts.urllib.request.urlopen", return_value=io.BytesIO(json.dumps(status).encode())) as fetch:
            raw = Accounts("https://upkeep.example.invalid/status.json").collect(None)
        fetch.assert_called_once_with("https://upkeep.example.invalid/status.json", timeout=20)
        return raw["accounts"]

    def project(self, status=None, now=NOW, threads=()):
        raw = self.collect(STATUS if status is None else status)
        return derive.accounts(raw, derive.Context(list(threads), set(), now, config.Thresholds()))[0]

    def test_weekly_allowance_contract_and_active_thread_count(self):
        threads = [{"account": "account-a", "status": state} for state in
                   ["running", "starting", "preparing", "queued", "completed"]]
        threads.append({"account": "account-b", "status": "running"})
        self.assertEqual(self.project(threads=threads), {
            "id": "account-a", "name": "Example A", "provider": "codex", "state": "current",
            "roomPercent": 48, "resetsAt": RESET, "pace": "on_pace", "burnPerHour": 2,
            "sustainablePerHour": 2, "runsOutAt": None, "running": 4,
        })
        self.assertNotIn("private@example.invalid", json.dumps(self.collect(STATUS)))

    def test_pace_verdicts_and_inclusive_boundaries(self):
        for burn, expected in [(1, "too_slow"), (1.5, "on_pace"), (2.5, "on_pace"), (3, "too_fast")]:
            with self.subTest(burn=burn):
                status = copy.deepcopy(STATUS)
                status["accounts"][0]["allowances"][1]["pace"] = {
                    "burn_pp_per_hour": burn, "outlasts_reset": burn <= 2, "runs_out_at": RESET}
                result = self.project(status)
                self.assertEqual(result["pace"], expected)
                self.assertEqual(result["runsOutAt"], None if burn <= 2 else RESET)

    def test_slow_burn_outlasts_reset_even_without_upkeep_flag(self):
        status = copy.deepcopy(STATUS)
        status["accounts"][0]["allowances"][1]["pace"] = {
            "burn_pp_per_hour": 1, "runs_out_at": "2026-10-23T12:00:00Z"}
        self.assertIsNone(self.project(status)["runsOutAt"])

    def test_account_fallback_and_name_identifier(self):
        status = copy.deepcopy(STATUS)
        account = status["accounts"][0]
        account.pop("instance_id")
        account["allowances"][1]["active"] = False
        account["pace"] = {"burn_pp_per_hour": 1}
        result = self.project(status)
        self.assertEqual((result["id"], result["roomPercent"], result["pace"]), ("Example A", 10, "too_fast"))

    def test_stale_reading_ages_between_collections(self):
        raw = self.collect(STATUS)
        for seconds, state, pace in [(600, "current", "on_pace"), (601, "stale", "unknown")]:
            context = derive.Context([], set(), NOW + timedelta(seconds=seconds), config.Thresholds())
            result = derive.accounts(raw, context)[0]
            self.assertEqual((result["state"], result["pace"]), (state, pace))

    def test_age_without_observation_timestamp(self):
        status = copy.deepcopy(STATUS)
        status.pop("observed_at")
        account = status["accounts"][0]
        account["allowances"][1].pop("observed_at")
        account["age_s"] = 550
        self.assertEqual(self.project(status, now=NOW + timedelta(seconds=51))["state"], "stale")

    def test_missing_or_invalid_reading_has_unknown_pace(self):
        for key, value in [("room_percent", None), ("resets_at", None), ("resets_at", "invalid"),
                           ("resets_at", NOW.isoformat()), ("room_percent", float("nan"))]:
            with self.subTest(key=key, value=value):
                status = copy.deepcopy(STATUS)
                status["accounts"][0]["allowances"][1][key] = value
                self.assertEqual(self.project(status)["pace"], "unknown")
        status = copy.deepcopy(STATUS)
        status["accounts"][0]["allowances"][1]["pace"] = {}
        self.assertEqual(self.project(status)["pace"], "unknown")
        for state in ["stale", "unknown"]:
            status["accounts"][0]["state"] = state
            self.assertEqual(self.project(status)["pace"], "unknown")

    def test_no_url_is_empty_without_network(self):
        with patch("fleet.sources.accounts.urllib.request.urlopen") as fetch:
            self.assertEqual(Accounts("").collect(None), {"accounts": []})
        fetch.assert_not_called()

    def test_hub_assembles_accounts_and_retains_them_on_safe_failure(self):
        source = Accounts("https://upkeep.example.invalid/status.json")
        the_hub = hub.Hub(config.Config(), [source], clock=lambda: NOW)
        with patch("fleet.sources.accounts.urllib.request.urlopen", return_value=io.BytesIO(json.dumps(STATUS).encode())):
            the_hub.run_once(source)
        self.assertEqual(the_hub.current["accounts"][0]["roomPercent"], 48)
        with patch("fleet.sources.accounts.urllib.request.urlopen", side_effect=OSError("private body")):
            the_hub.run_once(source)
        self.assertEqual(the_hub.current["accounts"][0]["roomPercent"], 48)
        self.assertFalse(the_hub.current["sources"]["accounts"]["ok"])
        self.assertNotIn("private", the_hub.encoded)


if __name__ == "__main__":
    unittest.main()
