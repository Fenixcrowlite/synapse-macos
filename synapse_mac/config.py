"""Local-only configuration and secrets for Synapse macOS agent."""
import json
import os
import secrets
import tempfile
from pathlib import Path

BASE = Path(os.environ.get('SYNAPSE_MAC_HOME', str(Path.home() / '.synapse-mac'))).expanduser()
DEFAULT = {
    'remote_access_enabled': False,  # Bootstrap activates it only after authorized tunnel setup.
    'control_enabled': False,
    'shell_enabled': False,
    'gui_enabled': False,
    'allowed_roots': [str(Path.home() / 'Documents'), str(Path.home() / 'Desktop'), str(Path.home() / 'Downloads')],
    'max_command_seconds': 15,
    'llm_model': 'llama3.2:3b',
    'port': 8765,
    'channels': {'main': '', 'backup': ''},
}


def atomic_write(path: Path, value: str):
    BASE.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(BASE, 0o700)
    fd, name = tempfile.mkstemp(prefix='.tmp-', dir=str(BASE))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(value)
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def init():
    BASE.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(BASE, 0o700)
    cfg = BASE / 'config.json'
    if not cfg.exists():
        atomic_write(cfg, json.dumps(DEFAULT, indent=2) + '\n')
    for fname in ('admin.token', 'mcp.token'):
        path = BASE / fname
        if not path.exists():
            atomic_write(path, secrets.token_urlsafe(48) + '\n')
    return BASE


def settings():
    init()
    data = json.loads((BASE / 'config.json').read_text(encoding='utf-8'))
    return {**DEFAULT, **data}


def update_settings(delta):
    allowed = {'remote_access_enabled', 'control_enabled', 'shell_enabled', 'gui_enabled', 'allowed_roots', 'max_command_seconds', 'channels', 'llm_model'}
    if not isinstance(delta, dict) or set(delta) - allowed:
        raise ValueError('Unknown setting')
    next_cfg = settings()
    for k, v in delta.items():
        if k in {'remote_access_enabled', 'control_enabled', 'shell_enabled', 'gui_enabled'} and type(v) is not bool:
            raise ValueError(f'{k} must be boolean')
        if k == 'allowed_roots':
            if not isinstance(v, list) or len(v) > 16 or not all(isinstance(x, str) and x.startswith('/') and len(x) < 1024 for x in v):
                raise ValueError('allowed_roots must be absolute paths')
            v = [str(Path(x).expanduser().resolve()) for x in v]
        if k == 'channels':
            if not isinstance(v, dict) or set(v) != {'main', 'backup'} or not all(isinstance(s, str) and len(s) < 254 for s in v.values()):
                raise ValueError('channels must specify both public URLs')
        if k == 'llm_model':
            import re
            if not isinstance(v, str) or not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,100}', v):
                raise ValueError('Invalid Ollama model identifier')
        if k == 'max_command_seconds' and (type(v) is not int or not 1 <= v <= 60):
            raise ValueError('max_command_seconds range 1..60')
        next_cfg[k] = v
    atomic_write(BASE / 'config.json', json.dumps(next_cfg, indent=2) + '\n')
    return next_cfg


def token(which):
    if which not in {'admin', 'mcp'}:
        raise ValueError('invalid token kind')
    init()
    return (BASE / (which + '.token')).read_text(encoding='utf-8').strip()


def rotate_mcp():
    next_token = secrets.token_urlsafe(48)
    atomic_write(BASE / 'mcp.token', next_token + '\n')
    return next_token


def stopped():
    return (BASE / 'STOP').exists()


def stop():
    atomic_write(BASE / 'STOP', 'Emergency stop enabled.\n')


def resume():
    (BASE / 'STOP').unlink(missing_ok=True)
