#!/usr/bin/env bash
# Two-command macOS deployment. Agent launchd service + authenticated remote channels.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
if [[ "$(uname -s)" != "Darwin" ]]; then
  echo 'Synapse Mac must run on macOS (portable tests can run on other systems).' >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo 'Python 3.10+ is required. Install with: brew install python' >&2
  exit 1
fi
python3 - <<'PY'
import sys
if sys.version_info < (3,10):
    raise SystemExit('Python 3.10+ required: brew install python')
PY
python3 -m synapse_mac init
case "${1:-install}" in
  install)
    python3 -m synapse_mac install-service
    echo 'Starting authenticated main + backup internet access when tunnel accounts are configured...'
    python3 -m synapse_mac remote-start
    echo 'Open local setup page: http://127.0.0.1:8765'
    echo 'Admin token: cat ~/.synapse-mac/admin.token'
    echo 'If either tunnel needs account login, use: ./bootstrap.sh --remote-setup'
    command -v open >/dev/null 2>&1 && open http://127.0.0.1:8765 || true
    ;;
  --with-chat)
    if ! command -v brew >/dev/null 2>&1; then
      echo 'Homebrew required for automatic Ollama setup: https://brew.sh/' >&2
      exit 1
    fi
    if ! command -v ollama >/dev/null 2>&1; then
      brew install ollama
    fi
    brew services start ollama
    ollama pull llama3.2:3b
    python3 -m synapse_mac install-service
    python3 -m synapse_mac remote-start
    echo 'Synapse Mac + local AI ready (tunnels need Tailscale/ngrok authorization).'
    echo 'Open http://127.0.0.1:8765 and log in using: cat ~/.synapse-mac/admin.token'
    command -v open >/dev/null 2>&1 && open http://127.0.0.1:8765 || true
    ;;
  --foreground|start|serve) exec python3 -m synapse_mac serve ;;
  --install-service) exec python3 -m synapse_mac install-service ;;
  --uninstall-service) exec python3 -m synapse_mac uninstall-service ;;
  --status) exec python3 -m synapse_mac status ;;
  --stop) exec python3 -m synapse_mac stop ;;
  --resume) exec python3 -m synapse_mac resume ;;
  --channels) exec python3 -m synapse_mac channels ;;
  --remote-start) exec python3 -m synapse_mac remote-start ;;
  --remote-stop) exec python3 -m synapse_mac remote-stop ;;
  --remote-status) exec python3 -m synapse_mac remote-status ;;
  --remote-setup) exec python3 -m synapse_mac remote-setup ;;
  --start-main) exec python3 -m synapse_mac start-main ;;
  --stop-main) exec python3 -m synapse_mac stop-main ;;
  --start-backup) exec python3 -m synapse_mac start-backup ;;
  --stop-backup) exec python3 -m synapse_mac stop-backup ;;
  *) echo 'Usage: ./bootstrap.sh [--with-chat|--remote-start|--remote-stop|--remote-status|--remote-setup|--foreground|--stop|--resume|--uninstall-service]' >&2;exit 2 ;;
esac
