# Nivixsa demo — HANDOFF / current status (continue from here)

You are picking up a partially-finished task on **this PC** (`ptz@ptz-Z370-AORUS-Gaming-7`). Read this whole file first, then the sibling `README.md`.

## TL;DR
- The demo **is deployed and LIVE** at **https://nivixsa.mattrlabs.online** (Docker + a dedicated Cloudflare Tunnel, in `~/project2/Cadio-MQTT-Dashboard`).
- **Hosting/infra is 100% verified working — do not re-diagnose it.**
- **RESOLVED (2026-07-08):** the browser login bounce was caused by a Werkzeug dev-server race on concurrent long-polling. Fixed by switching to `eventlet` async mode. See "✅ RESOLVED" section below.

## ⚠️ This is the LIVE PTZ PC — do not touch the PTZ system
Off-limits (a live PTZ product runs here): systemd `ptz-agent.service` + host `cloudflared.service`; `~/.cloudflared/`; `~/project/PTZ-Zeye-Full-System`; `~/Documents/Antigravity Projects/PTZ-Zeye-Full-System`; `/etc/ptz/`; `/dev/ttyUSB0`; `/dev/video7`.
Our demo is fully isolated: **only** in `~/project2/`, its own Docker containers, and a **separate** Cloudflare tunnel named `nivixsa-demo` (NOT the host cloudflared). Passwordless sudo is enabled for `ptz`.
`mattrlabs.online` is a shared production zone — the tunnel already created the one `nivixsa` DNS record; never touch any other record.

## What's deployed
`~/project2/Cadio-MQTT-Dashboard/docker-compose.yml` runs two containers:
- `nivixsa-smart-agriculture` — the app (Flask + Socket.IO), **no host port** (only reachable on the internal Docker network).
- `nivixsa-cloudflared` — `cloudflare/cloudflared`, runs the `nivixsa-demo` tunnel via `TUNNEL_TOKEN` in `.env`.
Public hostname `nivixsa.mattrlabs.online` → `http://nivixsa-smart-agriculture:5000` (configured in Cloudflare Zero Trust → Tunnels → nivixsa-demo).

**Local modification applied (2026-07-08):** `app.py` now uses `async_mode="eventlet"` (with `import eventlet; eventlet.monkey_patch()` at the top) instead of the Werkzeug dev server. WebSocket upgrades are re-enabled. `app.py` is baked into the image (not volume-mounted), so **any app.py edit needs a rebuild**: `DOCKER_BUILDKIT=0 docker compose up -d --build nivixsa-smart-agriculture`.

## Two separate logins (important)
- `/admin/login` — admin panel, validated against the app's local DB (seeded from `ADMIN_EMAIL`/`ADMIN_PASSWORD` in `.env`). **WORKS.** (Admin dashboard shows 0/empty — expected, fresh DB.)
- `/` (main page) — customer dashboard, validated against **Nivixsa cloud** (`https://egycad.com/apis/cadio/login`). Needs a real Nivixsa customer account.
- **Working test account** (from the app owner): `shriniddhi@gmail.com` / `123123123`. Confirmed: cadio API returns `success=True` in ~1s; it logs in fine **server-side**.

## Verified working — do NOT re-test these
- Static 200; Socket.IO polling handshake 200 (valid sid); WebSocket upgrade 101 (HTTP/1.1); `cadio_login` API 200 `success=True` ~1s; MQTT broker reachable ~1s.
- Admin login works ⇒ Flask sessions + tunnel are fine.
- **Customer login SUCCEEDS server-side:** a Socket.IO polling client that emits `login` receives `mqtt_status{connected:True}` and then a **flood** of `device_update` events (the account has lots of live device data).

## ✅ RESOLVED — browser bounces to login
**Symptom:** in a real browser, after logging in with the customer account, the page loads then returns to the login screen (and "takes too long"). Reproduced after `allow_upgrades=False` + hard refresh — still bounced.

**Root cause (confirmed):** Two concurrent GET long-polling requests hit the server ~5ms apart on the same session ID right after the device flood. The Werkzeug dev server (running via `allow_unsafe_werkzeug=True`, explicitly not production-safe) allowed a concurrent double-poll on one session and corrupted the response framing under that race, producing a malformed packet the client rejected and disconnected on. Since the UI is driven entirely by Socket.IO connection state (`mqtt_status` events), any disconnect → reconnect cycle flipped the UI back to login.

**Fix applied (2026-07-08):** Switched from Werkzeug dev server to **eventlet**, the async server Flask-SocketIO is designed for:

1. **`requirements.txt`**: Added `eventlet>=0.35.0`
2. **`app.py` top**: Added `import eventlet; eventlet.monkey_patch()` before all other imports — patches stdlib `threading`, `socket`, `ssl`, `select` so paho-mqtt's `loop_start()`, `threading.Thread`, and `threading.Lock` all work as cooperative green threads
3. **`app.py` line 50**: Changed `async_mode="threading"` → `async_mode="eventlet"`
4. **`app.py` line 50**: Removed `allow_upgrades=False` — WebSocket transport works correctly under eventlet and is the preferred transport (avoids long-polling entirely)
5. **`app.py` bottom**: Removed `allow_unsafe_werkzeug=True` from `socketio.run()` — eventlet provides its own production WSGI server

**Why this fixes the bug:** with `allow_upgrades=False` removed, the handshake advertises `"upgrades":["websocket"]`, so real browsers **upgrade to a single persistent WebSocket connection immediately** — no long-polling, therefore no double-poll race and no framing corruption. That is what resolves the browser bounce.

**Verification (measured 2026-07-08):**
- eventlet WSGI server is serving (startup banner "Open http://localhost:5000 in your browser"); the Socket.IO handshake returns `"upgrades":["websocket"]` — proof eventlet is live, since the old Werkzeug threading mode could never advertise a WebSocket upgrade.
- **WebSocket login soak, 90s** (test account, real `login` emit): connected over `websocket`, `mqtt_status{connected:True}`, **63 `device_update` events received, 0 disconnects**. This is the real browser path — fixed.
- Public URL serving 200 OK through the Cloudflare tunnel.

**Correction (2026-07-08, later): long-polling actually works too — earlier "polling is broken" was a test-client artifact.** Under eventlet, both transports work in a real browser. The initial login emits ~65 socket packets (`mqtt_status` + ~63 `device_update`), which engine.io batches into one polling response. **python-engineio's client caps a batch at 16 packets** (`Payload.max_decode_packets`) and aborts with `Unexpected packet from server, aborting` — but that cap is python-only; **the browser's JS engine.io parser has no per-batch packet cap**, so browsers accept the 65-packet batch fine. Verified through the **public Cloudflare tunnel** by raising the python client's cap to simulate a browser: polling-only login → `mqtt_status{connected:True}`, 63 `device_update`, 0 disconnects, stable 20s. So a WS-blocked network falls back to working long-polling.

**Do NOT force `transports:["websocket"]` on the client.** A brief WebSocket-only experiment (dashboard.js) fixed WS-capable devices but **stranded a phone on a WebSocket-hostile mobile network** (endless spinner + login bounce, since it had no fallback). Reverted to default `io()` (polling first, upgrade to WebSocket) — validated that WS-blocked polling-only AND normal upgrade both work through the tunnel. If you ever want defence-in-depth against a hypothetical browser that *does* cap batch size, reduce the initial `device_update` dump on login (search `emit("device_update"` in `app.py` — login-path emits at ~868/~909, MQTT broadcast at ~475), e.g. yield with `socketio.sleep(0)` between emits or send one consolidated snapshot event.

**Note — MQTT client-ID flap (separate, pre-existing):** Logging the SAME account in repeatedly within seconds causes an MQTT same-client-id flap (each login kicks the previous MQTT connection → disconnect event → UI flip). This is NOT the browser bounce bug — it only happens on rapid re-login. The fast-path in `handle_login` (line 866) avoids this when the session is already alive and the password matches.

## Operating cheatsheet
```bash
cd ~/project2/Cadio-MQTT-Dashboard
docker compose ps
docker compose logs -f nivixsa-smart-agriculture      # app logs
docker compose logs -f cloudflared                    # tunnel logs
DOCKER_BUILDKIT=0 docker compose up -d --build nivixsa-smart-agriculture   # after an app.py edit
curl -sS "https://nivixsa.mattrlabs.online/socket.io/?EIO=4&transport=polling"   # check "upgrades"
```

## WiFi changes
The demo survives WiFi changes — the Cloudflare tunnel is outbound and auto-reconnects; the public URL keeps working. Only `ssh ptz@<IP>` from another machine needs the new IP (`hostname -I`). The PTZ system's tunnel reconnects the same way. Docker `restart: always` means everything also survives reboots.

## Client-side access issues (NOT the app — network/browser caching)
The server is healthy and reachable globally; several "it won't open" reports traced entirely to **stale caches on the viewer's side**, all seeded by one event: the domain's **WHOIS verification lapsed and Namecheap suspended the nameservers**, so DNS briefly returned "does not exist." Resolvers and browsers that looked it up during that window cached the failure and kept serving it after recovery. Symptom → cause → fix:

| Symptom | Layer that cached the outage | Fix |
|---|---|---|
| Page never loads / no login form on one device's network; **new devices work** | That network's **router/ISP DNS resolver** (a phone's airplane-toggle can't clear it) | Set the phone's **Private DNS** to hostname `one.one.one.one` (Android: Settings → Network → Private DNS → "provider hostname"; not the IP). Or **reboot that wifi's router**. Or wait for the negative-cache TTL to expire. |
| Loads fine on the **PC's wifi** but not on the phone's own network | Same as above — PC's wifi uses a healthy resolver | Same as above. |
| Works in **Safari but not Chrome** on the *same* iPhone | That **browser's own cache/site-data** (iOS browsers share DNS but not caches) | Clear the failing browser's cache + site data, or use its Incognito/Private tab, or just use Safari. |
| Login **form** appears but socket never connects | Not DNS — Socket.IO transport blocked on that network | Ensure `dashboard.js` uses default `io()` (long-polling fallback). WebSocket-hostile networks depend on it. |

**Permanent prevention:** keep the `mattrlabs.online` **WHOIS contact verified** so DNS never gets suspended again — that outage is the single root of every caching symptom above.
