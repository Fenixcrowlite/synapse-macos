# Synapse Mac 🖥️

**Local-first macOS agent + Web Control Center + MCP Streamable HTTP**, installable in two terminal commands. Built for a separate Mac, without reusing another person's Windows tokens, endpoints, credentials, or system configuration.

> **Security:** The local service listens only on `127.0.0.1`; two optional authenticated HTTPS tunnels publish its Web UI to authorized users. **Do not port-forward port 8765 directly.** Computer control, shell commands and GUI automation are OFF by default. These tunnels do not grant ChatGPT Free access to custom MCP tools.

## Install on macOS

Requirements: macOS 13+ (Ventura or later for current Tailscale), Python 3.10+ (`python3 --version`), Git and the macOS Terminal. Apple Silicon and Intel supported. Python and Git may require Xcode Command Line Tools; if Python is missing, install with `brew install python`.

```bash
git clone https://github.com/Fenixcrowlite/synapse-macos.git
cd synapse-macos && ./bootstrap.sh
```

Open **http://127.0.0.1:8765** on the same Mac. For the admin login token, open another terminal window:

```bash
cat ~/.synapse-mac/admin.token
```

The bootstrap does not use `sudo`, does not install Homebrew itself, and does not require `pip install`. The default bootstrap installs a per-user `launchd` background service and attempts to activate both HTTPS tunnels after their accounts are configured. Use `./bootstrap.sh --foreground` for a terminal-only server.

### Start automatically after login

```bash
./bootstrap.sh --install-service
./bootstrap.sh --status
```

The per-user `launchd` service runs after user login. To remove it: `./bootstrap.sh --uninstall-service`. This does not delete local config or secrets.

### Emergency stop

Click **Emergency STOP** in the Web UI or run:

```bash
./bootstrap.sh --stop
```

Resume with `./bootstrap.sh --resume`. STOP blocks all mutating MCP tools independently of normal permission toggles.

## What it provides

| Feature | Tools / setting |
| --- | --- |
| Health & inventory | `ping`, `system_info`, `processes` |
| Safe file access | `list_directory`, `read_text_file` (configured roots only) |
| Screen & application view | `desktop_screenshot`, `desktop_open_windows` |
| Clipboard | `clipboard_read`, `clipboard_write` |
| File editing | `write_text_file` (control gate) |
| Terminal automation | `run_shell` (separate shell gate; 1–60 sec timeout) |
| Desktop automation | `open_application`, `open_url`, `keyboard_type`, `keyboard_hotkey`, `mouse_click` |
| Admin UX | Browser login, allowlisted roots, permission toggles, MCP token rotation, diagnostics, STOP, audit log |
| MCP | JSON-RPC 2.0 `initialize`, `ping`, `tools/list`, `tools/call` using Streamable HTTP JSON responses |

All MCP requests require an independent bearer token stored at `~/.synapse-mac/mcp.token`. The admin UI has its own different token at `~/.synapse-mac/admin.token`. Both are random and chmod 600.

## MCP client setup

In the **Web UI**, choose **Reveal MCP token** → **Copy client config**, or configure manually:

```json
{
  "mcpServers": {
    "synapse-mac": {
      "type": "streamable-http",
      "url": "http://127.0.0.1:8765/mcp",
      "headers": {"Authorization": "Bearer REPLACE_WITH_LOCAL_MCP_TOKEN"}
    }
  }
}
```

Never commit the token, include it in a URL, or share client config publicly. A local MCP client can connect to the machine directly. **ChatGPT on the web cannot reach `127.0.0.1` on your Mac.** For remote use, configure the built-in authenticated Tailscale Funnel and ngrok HTTPS channels, or use a private SSH tunnel to your own machine (e.g. `ssh -L 8765:127.0.0.1:8765 user@your-mac` from a trusted second machine). Do not confuse SSH tunnelling with publicly publishing port 8765.

## macOS privacy permissions

macOS intentionally prevents silent remote control. First use may require manual permission approvals:

- **Screen & System Audio Recording / Screen Recording**: `desktop_screenshot` (Terminal or Python, depending on launch mode).
- **Accessibility**: `keyboard_type`, `keyboard_hotkey`, `mouse_click`, and app listings via System Events.
- **Automation**: allow Terminal/Python to control System Events when prompted.

Go to **System Settings → Privacy & Security**, and enable relevant entries. When running through `launchd`, permission prompts may require an interactive login session. System permissions cannot safely be bypassed by the installer.

## Security model

- Localhost binding is hardcoded. Public access is optional and handled by Tailscale Funnel and ngrok HTTPS tunnels. Both require separate admin and MCP credentials.
- HTTP requests and admin sessions restricted to local origin and host, with `HttpOnly; SameSite=Strict` session cookies.
- Default read-only. Separate opt-ins for general control, arbitrary shell, and mouse/keyboard. All mutating calls blocked by emergency STOP.
- File tools restricted to user-configured absolute roots, with size limits; no arbitrary external file access via read/write.
- Tokens are generated independently per install, never stored in the repository and not printed in server logs.
- Log writes record tool names and results, not shell command contents or files.
- **Important:** MCP bearer-token holders can read configured files/screenshots, and, if control is enabled, can control the machine. Treat the MCP token like a password. Local malicious users and malware are outside this model.
- Bootstrap attempts to start the user-configured public HTTPS tunnels. A separate OpenAI account is not required for the built-in Synapse Web Chat.

## Development

Run tests (Linux can run the portable API tests; macOS CI additionally runs on an actual macOS runner):

```bash
python3 -m unittest discover -s tests -v
```

## Scope and known limitations

This is a **macOS-specific agent**, not a port of the Windows Synapse daemon internals. The Web UI, MCP endpoint, local Ollama chat and tunnel lifecycle are included. Cloud hosting, OAuth gateway, notarization, multiuser accounts, arbitrary GUI element inspection, and independent Mac hardware validation are **not** part of this release. Screen and input-control functionality requires testing on the destination Mac and permission grants. No magic installer can grant Accessibility permissions on the user's behalf.

## License

MIT. Public contributions welcome. Please report security problems privately rather than posting working tokens or machine-control endpoints in issues.

## Subscription-free AI chat (v1.3)

**Important distinction:** Ordinary **ChatGPT Free cannot invoke these custom MCP
control tools inside its own chat** under the current Free/Go plugin limits.
Two public tunnels do not change OpenAI account permissions. This project
instead supplies a genuinely independent **Synapse AI Chat** inside the
password-protected Web UI. It works without any ChatGPT subscription by using
Ollama on the target Mac. It is **not** an embedded ChatGPT model.

For the simplest setup on a Mac with Homebrew already installed, run from the
extracted `synapse-macos` directory:

```bash
./bootstrap.sh --with-chat
```

This installs Ollama if needed, pulls the 3B local model, registers the agent as
a login service and attempts to start both authenticated HTTPS tunnels. First-time
Tailscale and ngrok logins still need the Mac owner's approval and configuration.

Alternatively, install Ollama separately (Homebrew required):

```bash
brew install ollama
brew services start ollama
ollama pull llama3.2:3b
```

Open either configured HTTPS URL, authenticate using the unique Mac admin token,
and use the **Synapse AI Chat** panel. Each command proposed by the model is shown
as JSON and is executed **only after** explicit one-time confirmation. The agent
rechecks `control_enabled`, `shell_enabled`, `gui_enabled` and Emergency STOP
at the moment of execution. Model input is sent only to local
`http://127.0.0.1:11434`; there is no external AI request or hidden paid API.

When both channels are configured and active, the browser UI is reachable through
the primary Tailscale Funnel HTTPS address or the backup ngrok HTTPS address.
If a channel goes down, open the other address. This version does not automatically
transfer an already open browser tab across domains (a DNS/front-proxy is needed
for that); the example MCP failover client probes both independently.

Commands are never executed automatically on behalf of a text model.
