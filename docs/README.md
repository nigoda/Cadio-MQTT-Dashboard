# Nivixsa demo — hosting runbook (LIVE)

**Live at:** https://nivixsa.mattrlabs.online
**Deployed on:** the PTZ PC (`ptz@<IP>`, host `ptz-Z370-AORUS-Gaming-7`), in `~/project2/Cadio-MQTT-Dashboard`
**How:** the app runs in Docker with **no host port**; a **dedicated `cloudflared` container** (its own tunnel) exposes it. Fully isolated from the PTZ system on the same PC.

> **For an assistant picking this up in a fresh chat:** read the two ⚠️ boxes below before running anything. This PC runs a **live PTZ product**, and the domain is a **shared production zone**.

---

## ⚠️ 1. This PC runs the live PTZ system — do NOT touch these

- systemd services **`ptz-agent.service`** and **`cloudflared.service`** (the PTZ device-01 tunnel) — leave running.
- **`~/.cloudflared/`** — the PTZ tunnel's credentials. Off-limits.
- **`~/project/PTZ-Zeye-Full-System`** and **`~/Documents/Antigravity Projects/PTZ-Zeye-Full-System`** — the PTZ deployments.
- **`/etc/ptz/agent.env`**, **`/dev/ttyUSB0`** (ESP32), **`/dev/video7`** (virtual camera).

**This demo never uses the host `cloudflared`.** It runs its **own** cloudflared inside Docker with a **separate tunnel** (`nivixsa-demo`), so it cannot collide with the PTZ tunnel or service.
**It uses no host port** (the PTZ API may bind host `:5000`) — cloudflared reaches the app over Docker's private network instead.

## ⚠️ 2. `mattrlabs.online` is a shared, LIVE domain

Only the single **`nivixsa`** DNS record belongs to this demo (Cloudflare created it automatically when the tunnel's public hostname was added). **Never edit/delete** `@`, `www`, `control`, `device-01`, or any mail record (MX/SPF/DKIM/DMARC).

---

## Connecting to the PC

`ptz`'s SSH key is installed and **passwordless sudo** is enabled. The LAN IP **changes with WiFi**:
```bash
# get the current IP (run on the PC's console, or if you can already reach it):
ssh ptz@<known_IP> hostname -I     # first field is the LAN IP
ssh ptz@<IP>
```
The public demo URL keeps working even when the LAN IP changes — the tunnel is outbound and reconnects on its own. Only SSH-in needs the current IP.

---

## What's deployed (the app)

- **Repo:** https://github.com/nigoda/Cadio-MQTT-Dashboard — branch **`Server-cloudflare-deployed`** (this deployment; forked from `Server-Production-code`), cloned at `~/project2/Cadio-MQTT-Dashboard`.
- Flask + Socket.IO "Nivixsa Smart Irrigation" dashboard, listens on **:5000 inside its container**, connects **outbound** to MQTT (`egycad.com:1883`), stores SQLite on disk (volume-mounted → persists across restarts/reboots).
- Admin panel at `/admin/login` (creds from `.env`). Web-push works because the tunnel gives real HTTPS.

### Architecture
```
Browser ─HTTPS + WebSocket─→ nivixsa.mattrlabs.online  (Cloudflare edge)
      Socket.IO: long-polling, upgrades to WebSocket
                   │  tunnel "nivixsa-demo"  (separate from PTZ device-01)
                   ▼
 PTZ PC ── Docker network "cadio-mqtt-dashboard_default" ──
     nivixsa-cloudflared ──▶ http://nivixsa-smart-agriculture:5000
                                │  Flask + Socket.IO on eventlet WSGI server
                                └ outbound ──▶ egycad.com:1883 (MQTT, per-user)
   (no host port published; PTZ services untouched)
```

**Real-time layer (important):** the app runs Socket.IO under **eventlet** (not the Werkzeug dev server). Clients connect with Socket.IO's **default transport negotiation** — long-polling first, then upgrade to **WebSocket** where the network allows it; WebSocket-blocked networks stay on working long-polling. Cloudflare tunnels proxy WebSocket transparently, so no extra config is needed. The whole login → dashboard flow is driven by Socket.IO `mqtt_status` events (there is no server-side auth gate on `GET /`), so a healthy socket connection is what makes the dashboard appear. See `docs/HANDOFF.md` for the full history of why this matters.

### Files (in `~/project2/Cadio-MQTT-Dashboard/`)
- **`docker-compose.yml`** — two services: `nivixsa-smart-agriculture` (built from the repo Dockerfile, `expose: 5000`, **no host `ports:`**, DB + `.env` volume-mounted) and `cloudflared` (`cloudflare/cloudflared:latest`, `command: tunnel --no-autoupdate run`, `TUNNEL_TOKEN` from `.env`).
- **`.dockerignore`** — excludes the committed `.venv/` (keeps the image ~300 MB).
- **`.env`** — app secrets + `TUNNEL_TOKEN`. **Never commit/share.**
- **`cadio.db*`, `.encryption_key`** — persistent data (git-ignored, volume-mounted).

### The Cloudflare tunnel
A **remotely-managed** tunnel named **`nivixsa-demo`** in **Zero Trust → Networks → Tunnels**, with public hostname `nivixsa.mattrlabs.online` → service `http://nivixsa-smart-agriculture:5000`. Its connector token lives in `.env` as `TUNNEL_TOKEN`. This is a **different tunnel** from the PTZ `device-01` one.

---

## Everyday operations
```bash
cd ~/project2/Cadio-MQTT-Dashboard

docker compose ps                          # status of both containers
docker compose logs -f nivixsa-smart-agriculture   # app logs
docker compose logs -f cloudflared                 # tunnel logs
docker compose restart                     # restart both

# update to newer app code (keeps .env + database):
git pull
docker compose up -d --build --force-recreate
```
Both containers use `restart: always`, so the demo **survives PC reboots** automatically (as long as Docker starts on boot, which it does).

> After editing `.env`, containers must be recreated to pick it up: `docker compose up -d --force-recreate`.

---

## Rebuild from scratch (fresh machine or clean redeploy)
```bash
mkdir -p ~/project2 && cd ~/project2
git clone --depth 1 --single-branch --branch Server-cloudflare-deployed \
  https://github.com/nigoda/Cadio-MQTT-Dashboard.git
cd Cadio-MQTT-Dashboard
touch cadio.db cadio.db-wal cadio.db-shm .encryption_key
# copy docker-compose.yml, .dockerignore, .env from this folder (or recreate them), then:
nano .env                                  # fill secrets + TUNNEL_TOKEN
docker compose up -d --build
```
`.env` template:
```ini
GEMINI_API_KEY=...
ADMIN_EMAIL=...
ADMIN_PASSWORD=...
VAPID_PUBLIC_KEY=...        # 87-char public
VAPID_PRIVATE_KEY=...       # 43-char private
VAPID_CLAIMS_EMAIL=mailto:you@example.com
TUNNEL_TOKEN=...            # from Zero Trust → the nivixsa-demo tunnel
```
Creating a new tunnel: Zero Trust → Networks → Tunnels → Create → Cloudflared → name `nivixsa-demo` → copy the token (the string after `--token`) → add Public Hostname `nivixsa` . `mattrlabs.online` → Service `HTTP` `nivixsa-smart-agriculture:5000`. **Only add the `nivixsa` record.**

---

## Troubleshooting
| Symptom | Check |
|---|---|
| Site down / 502 | `docker compose ps` (both Up?); `docker compose logs cloudflared` for "Registered tunnel connection"; app logs for the eventlet WSGI banner ("Open http://localhost:5000 in your browser"). Confirm eventlet is live: the Socket.IO handshake must return `"upgrades":["websocket"]` (see cheatsheet). |
| Tunnel won't connect | `TUNNEL_TOKEN` correct/one line in `.env`? then `docker compose up -d --force-recreate cloudflared`. |
| App restarts / errors | `docker compose logs nivixsa-smart-agriculture`. |
| MQTT errors in logs | broker `egycad.com:1883` may be down or outbound 1883 blocked — the dashboard UI still loads. |
| Disk full (this PC sits ~95%) | `docker system df`; `docker builder prune -f` to reclaim build cache. |
| Web push not working | needs HTTPS (have it) + valid VAPID trio in `.env`, then `--force-recreate`. |
| A phone/PC won't load the site, but *other* new devices do | Almost always **stale DNS** on that device's network, cached during a domain/WHOIS outage — not the app. If no login form appears: set the phone's **Private DNS** to `one.one.one.one`, or reboot that wifi's router. If it loads in one browser but not another (e.g. Safari ✓ / Chrome ✗): clear the failing browser's cache/site data. See HANDOFF.md "Client-side access issues". |
| Login form shows but never connects / bounces | Socket.IO can't connect on that network. Confirm the served `dashboard.js` uses default `io()` (NOT `io({transports:["websocket"]})`) so long-polling fallback exists. WebSocket-blocked networks rely on it. |

---

## Teardown (when the demo is over)
```bash
cd ~/project2/Cadio-MQTT-Dashboard && docker compose down
cd ~ && rm -rf ~/project2/Cadio-MQTT-Dashboard      # (optional) remove the app
docker image rm cadio-mqtt-dashboard-nivixsa-smart-agriculture:latest 2>/dev/null   # reclaim disk
```
Then in **Cloudflare → Zero Trust → Networks → Tunnels**, delete the **`nivixsa-demo`** tunnel (this removes its public hostname and the auto-created DNS record). Confirm the `nivixsa` CNAME is gone under **DNS**; if it lingers, delete just that one record. **Leave the PTZ `device-01` tunnel and all other records alone.**

---

## Reference
- App repo: https://github.com/nigoda/Cadio-MQTT-Dashboard (branch `Server-cloudflare-deployed`; forked from `Server-Production-code`)
- Friend's original GCP doc (the cloud path we did not use): `GCP_SETUP-original-friend-doc.md` in this folder
- Cloudflare Tunnel docs: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/

## Local modifications (vs. the original `Server-Production-code`)
- **Socket.IO runs under eventlet** (2026-07-08). `app.py` has `import eventlet; eventlet.monkey_patch()` at the very top, `async_mode="eventlet"` in the `SocketIO(...)` constructor, and `socketio.run()` no longer passes `allow_unsafe_werkzeug=True`. `requirements.txt` adds `eventlet>=0.35.0`. This replaced the earlier `allow_upgrades=False` workaround and fixed the browser login-bounce (a Werkzeug-dev-server race under concurrent long-polling).
- **`static/js/dashboard.js`** keeps the **default `io()`** transport (long-polling → WebSocket upgrade). Do **not** force `transports:["websocket"]` — it strands clients on WebSocket-hostile networks. See HANDOFF.md.
- **`docker-compose.yml`** — no host port (`expose: 5000`), added the `cloudflared` tunnel container (deployment/isolation for the PTZ host).
- **`.dockerignore`** (new) — trims the build context.
- **`docs/`** (new) — this runbook + HANDOFF.

See HANDOFF.md "✅ RESOLVED" for the full root-cause history.
