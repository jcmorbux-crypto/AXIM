# AXIM Desktop

A thin [Tauri](https://tauri.app) window around the existing AXIM
FastAPI control UI + Telegram listener - **not** a standalone installer
with a bundled Python runtime. On launch it spawns the same processes
you'd otherwise start manually:

```
venv\Scripts\python.exe -m uvicorn api.main:app --host 127.0.0.1 --port 8090
venv\Scripts\python.exe core\telegram_listener.py
```

waits ~2 seconds for the API to bind, then opens a native window
pointed at `http://127.0.0.1:8090`. Closing the window kills both
processes - see `src-tauri/src/lib.rs`.

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
