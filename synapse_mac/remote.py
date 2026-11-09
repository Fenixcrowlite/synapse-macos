"""Authenticated public access lifecycle: dual live HTTPS tunnels and a kill switch.

Remote access means PUBLIC HTTPS REACHABILITY, never unauthenticated machine control.
An admin token and independent MCP bearer token remain mandatory. Fail-closed when
no tunnel credentials are configured; no port-forwarding and no 0.0.0.0 binding.
"""
import json
import shutil
import time

from . import config, channels


def up(wait_seconds=8):
    config.update_settings({'remote_access_enabled': True})
    results = {}
    for name in ('main', 'backup'):
        try:
            channels.start(name)
            results[name] = {'requested': True}
        except Exception as exc:
            results[name] = {'requested': False, 'error': str(exc)[:350]}
    deadline = time.monotonic() + max(0, wait_seconds)
    current = channels.status()
    while time.monotonic() < deadline and any(
        results[name]['requested'] and not current[name]['running'] for name in ('main', 'backup')
    ):
        time.sleep(0.5)
        current = channels.status()
    for name in ('main', 'backup'):
        results[name]['running'] = current[name]['running']
        results[name]['url'] = current[name]['url'] if current[name]['running'] else ''
    return {'remote_access_enabled': True, 'channels': results,
            'at_least_one_online': any(current[n]['running'] for n in ('main', 'backup')),
            'both_online': all(current[n]['running'] for n in ('main', 'backup'))}


def down():
    # Fail closed even if a provider's CLI cannot disable its tunnel.
    config.update_settings({'remote_access_enabled': False})
    results = {}
    for name in ('main', 'backup'):
        try:
            channels.stop(name)
            results[name] = {'stopped': True}
        except Exception as exc:
            results[name] = {'stopped': False, 'error': str(exc)[:350]}
    return {'remote_access_enabled': False, 'channels': results}


def prerequisites():
    return {
        'tailscale_installed': bool(shutil.which('tailscale')),
        'ngrok_installed': bool(shutil.which('ngrok')),
        'instructions': [
            'Install Tailscale open-source macOS CLI and ngrok CLI. Homebrew: brew install tailscale ngrok',
            'Sign into your own Tailscale network and enable Funnel in your tailnet policy when prompted.',
            'Sign into ngrok: ngrok config add-authtoken YOUR_OWN_TOKEN (run only in your local Terminal).',
            'Start both: ./bootstrap.sh --remote-start',
            'Admin token stays in ~/.synapse-mac/admin.token; MCP token stays in ~/.synapse-mac/mcp.token.',
        ],
    }


def pretty_status():
    return json.dumps({'status': channels.status(), 'prerequisites': prerequisites()}, ensure_ascii=False, indent=2)
