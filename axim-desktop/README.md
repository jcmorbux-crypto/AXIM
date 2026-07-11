# AXIM Desktop

A thin [Tauri](https://tauri.app) window that runs in one of two modes,
chosen on first launch (see `docs/AXIM_REMOTE_ACCESS.md`):

- **Local mode** (default) - runs this PC as the AXIM Server. On launch
  it spawns the same processes you'd otherwise start manually:

  ```
  venv\Scripts\python.exe -m uvicorn api.main:app --host <API_BIND_HOST> --port <API_BIND_PORT>
  venv\Scripts\python.exe core\telegram_listener.py
  ```

  (host/port read from the project's `.env`, defaulting to
  `127.0.0.1:8090` - same values `config/settings.py` uses), polls until
  the API actually accepts connections, then opens a native window
  pointed at it. Closing the window force-kills both processes (there is
  no reliable way to deliver a specific child process a Ctrl+C-equivalent
  on Windows), then automatically runs `scripts\cleanup_axim_chrome.ps1
  -Kill` to close out any Chrome tabs the listener didn't get a chance to
  close itself - see `src-tauri/src/lib.rs`. **Not** a standalone
  installer with a bundled Python runtime - see "Known limitation" below.

- **Remote mode** - points the window at a remote AXIM Server's address
  (typically a Tailscale hostname) instead, and spawns nothing locally.
  Use this to control an AXIM Server running on another machine (e.g.
  your Mini PC) from this laptop/PC.

The choice is persisted to a small `remote_client_config.json` under
this app's OS-standard config directory. Restart the app to see the
picker screen again with a "Change server settings" link during the
brief auto-connect delay.

## Known limitation

This only works on a machine with the AXIM project checkout and its
`venv/` already set up (see `../INSTALL.md`) - it locates the project
root via the `AXIM_PROJECT_ROOT` environment variable, falling back to
`C:\AXIM` if unset. Packaging a fully self-contained installer (bundled
Python interpreter + dependencies, no separate setup step) is real
additional work - not attempted in this pass. See
`docs/AXIM_APP_PLAN.md`'s Phase 6 section for the honest gap list.

## Prerequisites

- Rust (`rustup`) with the MSVC toolchain
- Visual Studio Build Tools, "Desktop development with C++" workload
  (Tauri on Windows links against the MSVC toolchain - the plain Rust
  installer alone isn't enough)
- Node.js (for the `npm run tauri ...` scripts below)
- The main AXIM `venv/` already created and working (`../requirements.txt`)

## Development

```powershell
cd axim-desktop
npm install
npm run tauri dev
```

## Building an installer

```powershell
npm run tauri build
```

Produces a Windows installer (`.msi`/`.exe`, via NSIS/WiX depending on
what's configured) under `src-tauri/target/release/bundle/`.
