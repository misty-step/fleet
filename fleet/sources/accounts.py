"""Canonical account room from the configured Upkeep command."""
import json
import subprocess

from ..hub import SourceError


def project_room(status):
    if (not isinstance(status, dict) or status.get('schema_version') != 1
            or not isinstance(status.get('accounts'), list)
            or not isinstance(status.get('quota_max_age_s'), (int, float))
            or isinstance(status.get('quota_max_age_s'), bool)
            or not 0 < status['quota_max_age_s'] <= 600):
        raise SourceError('accounts-invalid-room')
    accounts = []
    for account in status['accounts']:
        if not isinstance(account, dict) or not isinstance(account.get('quota'), dict):
            raise SourceError('accounts-invalid-account')
        quota = account['quota']
        provider = account.get('provider')
        email = account.get('email')
        if not isinstance(provider, str) or not provider or email is not None and not isinstance(email, str):
            raise SourceError('accounts-invalid-account')
        instances = account.get('instances', {})
        if not isinstance(instances, dict) or any(not isinstance(value, str) for value in instances.values()):
            raise SourceError('accounts-invalid-account')
        current = quota.get('state') == 'current'
        allowances = []
        for key, label in [('five_hour', '5h'), ('weekly', 'weekly')]:
            window = quota.get(key)
            if not isinstance(window, dict):
                raise SourceError('accounts-invalid-account')
            allowances.append({
                'active': True, 'label': label,
                'room_percent': window.get('room_percent') if current else None,
                'resets_at': window.get('resets_at') if current else None,
                'observed_at': quota.get('observed_at'), 'age_s': quota.get('age_s'),
                'pace': window.get('pace') if current else None,
            })
        accounts.append({
            'instance_id': account.get('instance_id'),
            'instances': instances,
            'name': f'{provider} · {email or "email unknown"}', 'provider': provider,
            'state': 'current' if current else 'unknown',
            'room_percent': None, 'resets_at': None,
            'observed_at': quota.get('observed_at'), 'age_s': quota.get('age_s'),
            'evaluated_at': status.get('evaluated_at'),
            'stale_after_s': status['quota_max_age_s'], 'allowances': allowances,
        })
    return accounts


class Accounts:
    name = 'accounts'
    interval = 30

    def __init__(self, command):
        self.command = command

    def collect(self, hub):
        if not self.command:
            return {'accounts': []}
        try:
            result = subprocess.run(self.command, capture_output=True, text=True, timeout=120, check=True)
            status = json.loads(result.stdout)
        except (OSError, subprocess.SubprocessError):
            raise SourceError('accounts-unavailable') from None
        except (ValueError, UnicodeError):
            raise SourceError('accounts-invalid-room') from None
        return {'accounts': project_room(status)}
