import copy
import json
import unittest
from datetime import datetime, timedelta, timezone

from fleet import config, derive, hub

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
        rows = copy.deepcopy(status['accounts'])
        for row in rows:
            row.update(status_observed_at=status.get('observed_at'),
                       status_age_s=status.get('age_s'),
                       evaluated_at=status.get('evaluated_at'),
                       stale_after_s=status.get('stale_after_s'))
        return rows

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

    def test_pace_verdicts_and_inclusive_boundaries(self):
        for burn, expected in [(1, "too_slow"), (1.5, "on_pace"), (2.5, "on_pace"), (3, "too_fast")]:
            with self.subTest(burn=burn):
                status = copy.deepcopy(STATUS)
                status["accounts"][0]["allowances"][1]["pace"] = {
                    "burn_pp_per_hour": burn, "outlasts_reset": burn <= 2, "runs_out_at": RESET}
                result = self.project(status)
                self.assertEqual(result["pace"], expected)
                self.assertEqual(result["runsOutAt"], None if burn <= 2 else RESET)

    def test_weekly_allowance_labels(self):
        for label in ["all models weekly", "Weekly", "Gemini · weekly", "Third-party models · weekly"]:
            with self.subTest(label=label):
                status = copy.deepcopy(STATUS)
                status["accounts"][0]["allowances"][1]["label"] = label
                result = self.project(status)
                self.assertEqual((result["roomPercent"], result["pace"]), (48, "on_pace"))

    def test_first_active_weekly_allowance_in_upkeep_order(self):
        status = copy.deepcopy(STATUS)
        allowances = status["accounts"][0]["allowances"]
        for label, active in [("biweekly", True), ("Weekly", False), ("weeklyish", True)]:
            allowances.insert(0, dict(allowances[-1], label=label, active=active, room_percent=1))
        allowances.append(dict(allowances[-1], label="Gemini · weekly", room_percent=2))
        result = self.project(status)
        self.assertEqual((result["roomPercent"], result["pace"]), (48, "on_pace"))

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
                account = status["accounts"][0]
                account["allowances"][1][key] = value
                account[key] = value
                self.assertEqual(self.project(status)["pace"], "unknown")
        status = copy.deepcopy(STATUS)
        status["accounts"][0]["allowances"][1]["pace"] = {"burn_pp_per_hour": None}
        self.assertEqual(self.project(status)["pace"], "too_slow")
        for state in ["stale", "unknown"]:
            status["accounts"][0]["state"] = state
            self.assertEqual(self.project(status)["pace"], "unknown")

    def test_missing_weekly_room_falls_back_to_account_room(self):
        status = copy.deepcopy(STATUS)
        status["accounts"][0]["allowances"][1]["room_percent"] = None
        result = self.project(status)
        self.assertEqual((result["roomPercent"], result["resetsAt"]), (10, RESET))
        self.assertEqual(result["pace"], "too_fast")

    def test_missing_weekly_reset_falls_back_to_account_reset(self):
        status = copy.deepcopy(STATUS)
        account = status["accounts"][0]
        account["resets_at"] = "2026-10-23T12:00:00Z"
        account["allowances"][1]["resets_at"] = None
        account["allowances"][1]["pace"] = {"burn_pp_per_hour": 1}
        result = self.project(status)
        self.assertEqual((result["roomPercent"], result["resetsAt"], result["pace"]),
                         (48, "2026-10-23T12:00:00Z", "on_pace"))

    def test_no_burn_reading_with_room_far_above_target_is_too_slow(self):
        status = copy.deepcopy(STATUS)
        account = status["accounts"][0]
        account["allowances"][1].update(room_percent=97, resets_at="2026-10-23T12:00:00Z")
        account["allowances"][1].pop("pace")
        result = self.project(status)
        self.assertEqual((result["roomPercent"], result["pace"], result["burnPerHour"],
                          result["sustainablePerHour"], result["runsOutAt"]),
                         (97, "too_slow", None, None, None))

    def test_no_burn_reading_stays_unknown_inside_the_slow_threshold(self):
        # Two days to reset: target 48/168 = 28.6%. Too slow needs over 42.9% and over 48.6%.
        status = copy.deepcopy(STATUS)
        account = status["accounts"][0]
        account["allowances"][1].update(resets_at="2026-10-23T12:00:00Z")
        account["allowances"][1].pop("pace")
        for room, expected in [(43, "unknown"), (48, "unknown"), (49, "too_slow")]:
            with self.subTest(room=room):
                account["allowances"][1]["room_percent"] = room
                self.assertEqual(self.project(status)["pace"], expected)



if __name__ == "__main__":
    unittest.main()
