# AXIM Remote Access (Remote Client over Tailscale)

This guide walks through connecting a second device (a laptop) to your
AXIM Server (the Mini PC) so you can monitor and control AXIM from
somewhere other than the server itself - Mission Control, session
start/stop/pause, Funds, Strategy Lab, live logs, live trade feed,
notifications, all in near-real-time.

**What this is not:** a way to expose AXIM to the public internet. There
is no public port, no public IP, and no domain involved anywhere in this
guide. The Remote Client only ever reaches the AXIM Server over a
private [Tailscale](https://tailscale.com) mesh network - a VPN that
only your own devices, signed into your own Tailscale account, can join.
Trades always execute on the AXIM Server; the Remote Client never
executes a trade directly, no matter what it's connected to.

## Overview

1. Install Tailscale on both the Mini PC (AXIM Server) and the laptop
   (Remote Client), signed into the same Tailscale account.
2. Find the Mini PC's Tailscale hostname.
3. Tell the AXIM Server to accept connections from the Tailscale network,
   not just from itself.
4. Point the Remote Client at that hostname.
5. Log in exactly as you would sitting at the Mini PC.

## Step 1 - Install Tailscale on both devices

On **both** the Mini PC and the laptop:

1. Go to [tailscale.com/download](https://tailscale.com/download) and
   install Tailscale for Windows.
2. Sign in when prompted - use the same account (Google, Microsoft,
   GitHub, or email) on both devices, so they land on the same private
   network ("tailnet").
3. Confirm Tailscale shows "Connected" in its tray icon on both devices.

That's it for Tailscale itself - no port forwarding, no router
configuration, no public IP or domain needed.

## Step 2 - Find the Mini PC's Tailscale hostname

On the **Mini PC**, open a terminal and run:

```powershell
tailscale status
```

Your own device's entry shows a hostname that looks like
`mini-pc.tailnet-name.ts.net` (or just note the `100.x.x.x` Tailscale IP
if you'd rather use that). Write this down - you'll enter it on the
laptop in Step 4.

## Step 3 - Allow the AXIM Server to accept remote connections

By default, AXIM's control API only listens on `127.0.0.1` (this
machine only) - this is why nothing outside the Mini PC can reach it
today. To open it up to your Tailscale network specifically:

1. On the **Mini PC**, open `C:\AXIM\.env` in a text editor.
2. Add or edit these lines:

   ```
   API_BIND_HOST=0.0.0.0
   API_BIND_PORT=8090
   ALLOWED_ORIGINS=http://mini-pc.tailnet-name.ts.net:8090
   ```

   Replace `mini-pc.tailnet-name.ts.net` with the hostname you noted in
   Step 2. `API_BIND_HOST=0.0.0.0` means "listen on every network
   interface this machine has" - since Tailscale itself only routes
   traffic between your own devices, this does **not** expose AXIM to
   the public internet; it's only reachable over the Tailscale network
   (and still locally). If you'd rather bind only the Tailscale
   interface specifically, use the `100.x.x.x` Tailscale IP shown by
   `tailscale status` instead of `0.0.0.0`.

3. If the AXIM API is running as a Scheduled Task, re-register it so it
   picks up the new bind address:

   ```powershell
   powershell -File scripts\install_api_scheduled_task.ps1
   Start-ScheduledTask -TaskName "AXIM API"
   ```

   Otherwise, just restart however you normally start `api/main.py`.

4. Sanity check from the Mini PC itself:

   ```powershell
   curl http://<the-tailscale-hostname-or-ip>:8090/api/auth/bootstrap-status
   ```

   should return `{"needs_bootstrap":false}` (assuming you've already
   created your Owner account).

## Step 4 - Point the Remote Client at the AXIM Server

On the **laptop**, open the AXIM desktop app. The first time it runs (or
any time you click "Change server settings" during the brief startup
screen), you'll see a choice:

- **Run AXIM locally on this PC** - not what you want here, this makes
  the laptop its own independent AXIM Server.
- **Connect to a remote AXIM Server** - choose this, and enter the
  Mini PC's Tailscale hostname and port from Step 2, e.g.
  `mini-pc.tailnet-name.ts.net:8090`.

Click **Save and Continue**. The app will connect directly to the Mini
PC - it does not spawn any local AXIM processes in this mode.

## Step 5 - Log in

You'll land on the exact same login screen as if you were sitting at the
Mini PC. Log in with your normal AXIM account. From here on, everything
you see and do - Mission Control, sessions, Funds, Strategy Lab,
Automation Studio, notifications, live logs, live trade execution - is
the real AXIM Server's live state, kept in sync in real time. Trades you
start from the laptop still execute on the Mini PC; the laptop is only
ever a window into it.

## Multiple devices

Any number of devices can connect the same way - each gets its own
session, visible (and revocable) from **Settings > Connected Devices**
(or, for an Owner/Admin managing someone else's account, from **Users >
manage user > Connected Devices**). Revoking a device immediately signs
it out on its next request.

## Troubleshooting

- **Laptop can't reach the server at all**: confirm both devices show
  "Connected" in the Tailscale tray icon, and that `tailscale status` on
  the laptop lists the Mini PC. Try `ping <hostname>` from the laptop.
- **Login works but nothing updates live**: the app falls back to
  regular polling if the live event stream can't connect, so you'll
  still see this - it just refreshes every 15-20 seconds instead of
  instantly. This isn't a broken deployment, just a degraded one; check
  the Mini PC's `logs/ui.log` for repeated stream errors if it doesn't
  resolve itself.
- **"CORS" or "Failed to fetch" errors in a browser-based Remote
  Client**: make sure `ALLOWED_ORIGINS` in `.env` on the Mini PC exactly
  matches the origin the browser is loading from (scheme + host + port).
  The AXIM desktop app itself doesn't hit this, since it isn't a
  same-origin browser page - this only matters for a future
  separately-hosted web dashboard.
- **Changed the server address and want to switch back**: restart the
  Remote Client and click "Change server settings" during the brief
  startup screen, or delete
  `remote_client_config.json` from the app's config directory
  (`%APPDATA%\com.axim.desktop\` on Windows) to reset to the picker.

## What this setup does *not* do

- Does not open any port on your router or expose AXIM to the public
  internet - Tailscale traffic never leaves the private mesh network.
- Does not require a domain name or a public TLS certificate.
- Does not let the Remote Client execute trades independently - all
  execution, broker connections, and Telegram listening remain
  exclusively on the AXIM Server.

A future hosted/cloud option with a real public domain and HTTPS
certificate is possible later, but is a deliberately separate, opt-in
path - not something this setup enables by default.
