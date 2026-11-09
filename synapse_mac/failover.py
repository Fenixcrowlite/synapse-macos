"""No-dependency MCP stdio bridge with primary/backup HTTPS health-based failover.

Install it on the MCP client's computer. This bridge never replays a request that
might have already reached its destination. It chooses a healthy channel BEFORE
sending each JSON-RPC message.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from .channels import clean_url


def load_config(path):
    path = Path(path).expanduser()
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('Invalid client config')
    main = clean_url(data.get('main', ''), 'main')
    backup = clean_url(data.get('backup', ''), 'backup')
    token = data.get('mcp_token', '')
    if not isinstance(token, str) or len(token) < 32:
        raise ValueError('mcp_token missing')
    return {'main': main, 'backup': backup, 'token': token}


def healthy(base, timeout=2):
    req = urllib.request.Request(base + '/health', method='GET', headers={'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status == 200 and json.loads(response.read(2048)).get('alive') is True
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return False


def select_base(cfg, health=healthy):
    for name in ('main', 'backup'):
        if health(cfg[name]):
            return cfg[name], name
    raise ConnectionError('Neither Synapse HTTPS channel is reachable')


def forward(base, token, request, timeout=75):
    body = json.dumps(request, ensure_ascii=False).encode('utf-8')
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json',
               'Authorization': 'Bearer ' + token, 'MCP-Protocol-Version': '2025-06-18'}
    req = urllib.request.Request(base + '/mcp', method='POST', data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 202:
                return None
            return json.loads(resp.read(1024 * 1024))
    except urllib.error.HTTPError as exc:
        # Never replay on the other channel: the first POST may already have run.
        return {'jsonrpc':'2.0','id':request.get('id'),
                'error':{'code':-32003,'message':f'MCP HTTP failure: {exc.code}'}}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return {'jsonrpc':'2.0','id':request.get('id'),
                'error':{'code':-32004,'message':f'MCP request failed; not replayed: {type(exc).__name__}'}}


def run(config_path):
    cfg = load_config(config_path)
    # MCP over stdio is one JSON-RPC message per line. stdout must contain only replies.
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError('Expected JSON-RPC object')
            base, channel = select_base(cfg)
            print(f'Synapse selected {channel} HTTPS channel', file=sys.stderr, flush=True)
            result = forward(base, cfg['token'], request)
        except (ValueError, ConnectionError, OSError) as exc:
            result = {'jsonrpc':'2.0','id':request.get('id') if isinstance(locals().get('request'),dict) else None,
                      'error':{'code':-32002,'message':str(exc)}}
        if result is not None:
            print(json.dumps(result, ensure_ascii=False, separators=(',', ':')), flush=True)


def main():
    parser = argparse.ArgumentParser(description='Synapse macOS failover MCP stdio client')
    parser.add_argument('--config', default=str(Path.home()/'.synapse-mac'/'client.json'))
    args = parser.parse_args()
    run(args.config)


if __name__ == '__main__':
    main()
