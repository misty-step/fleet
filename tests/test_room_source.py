import json
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from fleet import config, derive, hub
from fleet.sources.accounts import Accounts

NOW = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
RESET = '2026-10-25T12:00:00Z'
ROOM = {
    'schema_version': 1, 'evaluated_at': NOW.isoformat(),
    'quota_max_age_s': 600, 'probe_max_age_s': 600,
    'accounts': [{
        'provider': 'codex', 'email': 'qa@example.test', 'instance_id': 'codex-team',
        'instances': {'authority': 'codex-team', 'reader': 'codex'},
        'quota': {'state': 'current', 'observed_at': NOW.isoformat(), 'age_s': 0,
                  'five_hour': {'room_percent': 90, 'resets_at': RESET},
                  'weekly': {'room_percent': 48, 'resets_at': RESET}},
        'probe': {'state': 'not_needed'},
    }],
}

class RoomSourceTest(unittest.TestCase):
    def collect(self, value):
        command = [sys.executable, '-c', 'import sys; print(sys.argv[1])', json.dumps(value)]
        return Accounts(command).collect(None)['accounts']

    def test_command_result_reaches_fleet_with_canonical_identity(self):
        rows = self.collect(ROOM)
        context = derive.Context([{'account': 'codex-team', 'status': 'running'},
                                  {'account': 'codex', 'status': 'running'}], set(), NOW, config.Thresholds())
        row = derive.accounts(rows, context)[0]
        self.assertEqual(row['id'], 'codex-team')
        self.assertEqual(row['name'], 'codex · qa@example.test')
        self.assertEqual(row['roomPercent'], 48)
        self.assertEqual(row['running'], 2)

    def test_unknown_quota_and_working_probe_do_not_invent_percent(self):
        value = json.loads(json.dumps(ROOM))
        value['accounts'][0]['quota']['state'] = 'unknown'
        value['accounts'][0]['probe'] = {'state': 'works'}
        rows = self.collect(value)
        row = derive.accounts(rows, derive.Context([], set(), NOW, config.Thresholds()))[0]
        self.assertEqual(row['state'], 'unknown')
        self.assertIsNone(row['roomPercent'])

    def test_expired_reading_does_not_remain_numeric(self):
        rows = self.collect(ROOM)
        row = derive.accounts(rows, derive.Context([], set(), NOW + timedelta(seconds=601), config.Thresholds()))[0]
        self.assertEqual(row['state'], 'stale')
        self.assertIsNone(row['roomPercent'])

    def test_missing_weekly_room_never_uses_five_hour_room(self):
        value = json.loads(json.dumps(ROOM))
        value['accounts'][0]['quota']['weekly']['room_percent'] = None
        rows = self.collect(value)
        row = derive.accounts(rows, derive.Context([], set(), NOW, config.Thresholds()))[0]
        self.assertIsNone(row['roomPercent'])

    def test_missing_and_future_observations_are_unknown_numbers(self):
        for observed in (None, (NOW + timedelta(seconds=1)).isoformat()):
            value = json.loads(json.dumps(ROOM))
            value['accounts'][0]['quota']['observed_at'] = observed
            value['accounts'][0]['quota']['age_s'] = None
            rows = self.collect(value)
            row = derive.accounts(rows, derive.Context([], set(), NOW, config.Thresholds()))[0]
            self.assertIsNone(row['roomPercent'])

    def test_command_failure_is_safe_source_error(self):
        source = Accounts([sys.executable, '-c', 'import sys; sys.stderr.write("private-body"); sys.exit(7)'])
        the_hub = hub.Hub(config.Config(), [source], clock=lambda: NOW)
        the_hub.run_once(source)
        self.assertEqual(the_hub.current['sources']['accounts']['error'], 'accounts-unavailable')
        self.assertNotIn('private-body', the_hub.encoded)

    def test_rejects_old_url_configuration(self):
        with self.assertRaises(ValueError):
            config.parse({'upkeep_status_url': 'https://old.example.test/status.json'})

    def test_watch_only_identity_stays_visible(self):
        value = json.loads(json.dumps(ROOM))
        value['accounts'][0].update(provider='grok', email=None, instance_id='grok')
        value['accounts'][0]['quota']['state'] = 'unknown'
        rows = self.collect(value)
        self.assertEqual(rows[0]['name'], 'grok · email unknown')

if __name__ == '__main__':
    unittest.main()
