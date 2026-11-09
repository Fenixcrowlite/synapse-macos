"""Two independent, authenticated HTTPS transports to the same loopback-bound macOS agent.

Primary: Tailscale Funnel, persistent in Tailscale's daemon.
Backup: ngrok HTTP tunnel, supervised by the user's launchd.

Neither channel depends on the ChatGPT subscription or stores tunnel credentials.
"""
import json
import os
import platform
import plistlib
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from . import config

BACKUP_LABEL = 'dev.synapse.macos.ngrok'
BACKUP_PLIST = Path.home() / 'Library' / 'LaunchAgents' / f'{BACKUP_LABEL}.plist'
MAX_URL_LENGTH = 253


def clean_url(value, channel):
    if not isinstance(value, str) or len(value) > MAX_URL_LENGTH:
        raise ValueError('Enter a public HTTPS endpoint URL')
    parsed = urlsplit(value.strip())
    if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
        raise ValueError('Use only an HTTPS origin, without credentials, path or query')
    host = parsed.hostname or ''
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?', host) or '..' in host:
        raise ValueError('Invalid hostname')
    if parsed.port not in (None, 443, 8443, 10000) or parsed.port == 0:
        raise ValueError('Unexpected public HTTPS port')
    if channel == 'main' and not host.endswith('.ts.net'):
        raise ValueError('Tailscale Funnel URL must end with .ts.net')
    if channel == 'backup' and not (host.endswith('.ngrok-free.app') or host.endswith('.ngrok.app')):
        raise ValueError('Backup URL must be an ngrok HTTPS domain')
    return 'https://' + host + ((':' + str(parsed.port)) if parsed.port not in (None, 443) else '')


def urls():
    return config.settings().get('channels', {'main': '', 'backup': ''})


def set_public_url(channel, url):
    if channel not in ('main', 'backup'):
        raise ValueError('Invalid channel')
    url = clean_url(url, channel) if url else ''
    cfg = config.settings()
    updated = dict(cfg.get('channels', {}))
    updated[channel] = url
    config.update_settings({'channels': updated})
    return url


def _run(cmd, seconds=10):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=seconds, check=False)
    if p.returncode:
        raise RuntimeError((p.stderr.strip() or p.stdout.strip() or f'{cmd[0]} exited {p.returncode}')[-500:])
    return p.stdout + p.stderr


def _tailscale_state():
    exe = shutil.which('tailscale')
    if not exe:
        return False, '', 'Tailscale CLI missing'
    try:
        result = _run([exe, 'funnel', 'status'], 6)
        match = re.search(r'https://[A-Za-z0-9.-]+\.ts\.net(?::(?:443|8443|10000))?', result)
        # A zero-status CLI is not proof that a Funnel is enabled.
        return bool(match), clean_url(match.group(), 'main') if match else '', '' if match else 'Funnel not running'
    except (subprocess.TimeoutExpired, RuntimeError, OSError) as exc:
        return False, '', str(exc)[:160]


def _ngrok_url():
    # ngrok's management listener is loopback-only. Never send this request elsewhere.
    try:
        with urllib.request.urlopen('http://127.0.0.1:4040/api/tunnels', timeout=1.5) as r:
            data = json.loads(r.read(32768))
        expected_port = config.settings().get('port', 8765)
        for tunnel in data.get('tunnels', []):
            target = tunnel.get('config', {}).get('addr', '')
            try: target_port = urlsplit(target if '://' in target else 'http://' + target).port if target else None
            except ValueError: continue
            if target_port != expected_port:
                continue  # Ignore unrelated ngrok tunnels on the same Mac.
            if tunnel.get('proto') in ('https', 'http'):
                try:
                    return clean_url(tunnel.get('public_url', ''), 'backup')
                except ValueError:
                    continue
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return ''


def _launchd_loaded():
    if platform.system() != 'Darwin':
        return False
    domain = f'gui/{os.getuid()}'
    r = subprocess.run(['launchctl', 'print', f'{domain}/{BACKUP_LABEL}'], capture_output=True, timeout=5)
    return r.returncode == 0


def status():
    saved = urls()
    main_live, main_url, main_error = _tailscale_state()
    backup_url = _ngrok_url()
    backup_service = _launchd_loaded()
    # Persist tunnel URLs on local introspection only, never from untrusted remote input.
    for name, detected in [('main', main_url), ('backup', backup_url)]:
        if detected and detected != saved.get(name):
            set_public_url(name, detected)
            saved = urls()
    return {
        'remote_access_enabled': config.settings()['remote_access_enabled'],
        'main': {
            'name': 'Tailscale Funnel', 'installed': bool(shutil.which('tailscale')),
            'running': main_live, 'url': main_url or saved.get('main', ''),
            'last_error': main_error, 'setup': 'Install Tailscale (open-source macOS build), run tailscale up, then enable Funnel.'
        },
        'backup': {
            'name': 'ngrok', 'installed': bool(shutil.which('ngrok')),
            'running': bool(backup_url), 'service_installed': backup_service,
            'url': backup_url or saved.get('backup', ''),
            'last_error': '' if backup_url else 'Tunnel not active or inspector unavailable',
            'setup': 'Install ngrok, then run: ngrok config add-authtoken YOUR_TOKEN (Terminal only).'
        },
        'local': f'http://127.0.0.1:{config.settings().get("port", 8765)}',
        'plan': 'Both HTTPS transports and the Web UI are independent of ChatGPT Plus. Free/Go plugin tool invocation is not guaranteed.'
    }


def start(channel):
    if platform.system() != 'Darwin':
        raise RuntimeError('Tunnel management can only run on macOS')
    opts = config.settings()
    if not opts['remote_access_enabled']:
        raise PermissionError('Enable remote access before starting tunnels')
    port = opts.get('port', 8765)
    if channel == 'main':
        exe = shutil.which('tailscale')
        if not exe:
            raise RuntimeError('Install Tailscale CLI first')
        _run([exe, 'funnel', '--bg', '--yes', str(port)], 22)
        return status()
    if channel == 'backup':
        exe = shutil.which('ngrok')
        if not exe:
            raise RuntimeError('Install ngrok CLI first')
        config.init()
        BACKUP_PLIST.parent.mkdir(parents=True, exist_ok=True)
        plist = {
            'Label': BACKUP_LABEL,
            'ProgramArguments': [exe, 'http', '--log=stdout', str(port)],
            'RunAtLoad': True, 'KeepAlive': True,
            'StandardOutPath': str(config.BASE / 'ngrok.log'),
            'StandardErrorPath': str(config.BASE / 'ngrok.err.log'),
        }
        BACKUP_PLIST.write_bytes(plistlib.dumps(plist))
        os.chmod(BACKUP_PLIST, 0o600)
        domain = f'gui/{os.getuid()}'
        subprocess.run(['launchctl', 'bootout', domain, str(BACKUP_PLIST)], capture_output=True, timeout=6)
        _run(['launchctl', 'bootstrap', domain, str(BACKUP_PLIST)], 8)
        _run(['launchctl', 'enable', f'{domain}/{BACKUP_LABEL}'], 8)
        return status()
    raise ValueError('Unknown channel')


def stop(channel):
    if platform.system() != 'Darwin':
        raise RuntimeError('Tunnel management can only run on macOS')
    if channel == 'main':
        exe = shutil.which('tailscale')
        if not exe:
            raise RuntimeError('Tailscale CLI missing')
        _run([exe, 'funnel', '--https=443', 'off'], 12)
    elif channel == 'backup':
        if BACKUP_PLIST.exists():
            _run_or_ignore(['launchctl', 'bootout', f'gui/{os.getuid()}', str(BACKUP_PLIST)])
            BACKUP_PLIST.unlink()
    else:
        raise ValueError('Unknown channel')
    # Untrust the former public host immediately after shutting down a channel.
    set_public_url(channel, '')
    return status()


def _run_or_ignore(args):
    try:
        subprocess.run(args, capture_output=True, timeout=8)
    except (subprocess.TimeoutExpired, OSError):
        pass


def remote_origin_host(host):
    """Only explicit discovered/configured HTTPS origins are trusted by HTTP handler."""
    if not config.settings()['remote_access_enabled']:
        return False
    for value in urls().values():
        if value:
            parsed = urlsplit(value)
            if parsed.netloc == host or (parsed.port is None and parsed.netloc + ':443' == host):
                return True
    return False
