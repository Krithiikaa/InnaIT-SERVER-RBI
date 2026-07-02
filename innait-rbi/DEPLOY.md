# InnaIT RBI — Deployment Package (for the server team)

Remote Browser Isolation. Whitelisted sites run inside a server-side sandbox browser;
each user receives **only a VP8 video stream** (WebRTC) and sends input back. All policy
enforcement is server-side.

This package deploys the system as **systemd services** on a fresh Linux server.

---

## Contents

```
innait-rbi/
├── install.sh            # automated installer (run as root)
├── uninstall.sh          # removes the services
├── rbi.env.example       # settings template (installer copies -> rbi.env)
├── app/                  # the application (installed to /opt/rbi-web-v1)
│   ├── webrtc_server.py  #   streamer (private session per viewer, load-aware cap)
│   ├── admin_server.py   #   admin console
│   ├── public/           #   viewer page + logo
│   ├── shared/           #   config.json (you edit) + auto state files
│   ├── start.sh          #   manual-run alternative (not needed if using systemd)
│   └── entrypoint.sh
├── systemd/
│   ├── rbi-webrtc.service
│   └── rbi-admin.service
└── docs/                 # architecture / flow / API-map diagrams (SVG)
```

---

## Requirements

- Ubuntu 22.04 or 24.04 (Debian-family). x86_64.
- Root / sudo.
- Outbound internet during install (apt, pip, Google Chrome .deb).
- The server must be able to reach the **target whitelisted sites** (it loads them itself).
- Sizing: each concurrent viewer ≈ **0.5 GB RAM + ~1 CPU core** (live video encode).
  CPU is the limit. A 4-core box ≈ 3 viewers; 16-core ≈ 12–15. See "Scaling" below.

---

## Quick deploy (automated)

```bash
# 1. copy this folder to the server, then:
cd innait-rbi
sudo ./install.sh
```

The installer will:
1. Install all dependencies (Xvfb, xdotool, GStreamer, python3-gi) + Python libs (aiohttp, psutil).
2. Install **Google Chrome** (non-snap — required; snap Chromium will not render headless).
3. Copy the app to `/opt/rbi-web-v1`.
4. Create `/opt/rbi-web-v1/rbi.env` with this server's IP auto-detected and a random admin secret.
5. Install + enable + start the two systemd services.
6. Run a health check and print the URLs.

When it finishes, **do the three post-install steps it prints** (set admin password,
set your sites, open the firewall).

---

## Post-install configuration

### 1. Settings — `/opt/rbi-web-v1/rbi.env`
```ini
SERVER_HOST=10.0.0.10          # this server's reachable IP/host (for admin links)
BASE_PORT=8100                 # first viewer port
ADMIN_PORT=8200                # admin console port
ADMIN_USER=admin
ADMIN_PASS=set-a-real-password # CHANGE THIS
ADMIN_SECRET=<random>          # set by installer; keep secret
BITRATE=5000000
# MAX_SESSIONS=                # blank = auto from CPU/RAM; set a number to override
```

### 2. Sites — `/opt/rbi-web-v1/shared/config.json`
```json
{
  "enabled": true,
  "bitrate": 5000000,
  "max_sessions": 3,
  "sites": [
    { "id": "default", "name": "Precision IT", "url": "https://www.precisionit.co.in/",
      "port": 8100,
      "policies": { "read_only": false, "scroll_lock": false, "copy": false, "paste": false,
                    "clipboard": false, "print": false, "download": false,
                    "devtools": false, "file_management": false } }
  ]
}
```
Each site needs a **unique port** (8100, 8101, 8102…). Users open `http://SERVER:PORT`.
Sites can also be added later from the admin console (auto-assigns the next port).

### 3. Apply changes
```bash
sudo systemctl restart rbi-webrtc rbi-admin
```

### 4. Firewall
Open the **viewer ports** in use (8100, 8101, …) and the **admin port** (8200) to the
client network. UFW example:
```bash
ufw allow 8100/tcp
ufw allow 8200/tcp
# + one rule per additional site port
```

---

## Verify

```bash
systemctl status rbi-webrtc rbi-admin
curl -s -o /dev/null -w "8100 -> %{http_code}\n" http://localhost:8100   # expect 200
curl -s http://localhost:8100/load                                        # cpu/ram/cap/used/browser
journalctl -u rbi-webrtc -n 20
```

In the streamer log, confirm the startup line:
```
WebRTC RBI (PRIVATE) up | browser=google-chrome-stable | cores=N ram=X GB | cap=M ... | psutil=yes
```
- `browser=google-chrome-stable` (NOT chromium), `psutil=yes`, and note `cap=M` (max concurrent).

Then from a client browser: `http://SERVER_IP:8100` (viewer) and `http://SERVER_IP:8200` (admin).

---

## Operations

```bash
# logs (live)
journalctl -u rbi-webrtc -f
journalctl -u rbi-admin -f

# restart / stop / start
systemctl restart rbi-webrtc rbi-admin
systemctl stop    rbi-webrtc rbi-admin
systemctl start   rbi-webrtc rbi-admin

# the services auto-restart on crash (Restart=always) and start on boot (enabled)
```

The streamer self-heals: browser auto-detect fallback, Xvfb/window retry with lock
cleanup, graceful "Server at capacity" when full, and teardown of each private sandbox
on disconnect (display number returned to the pool).

---

## Behind a reverse proxy (https + hostnames) — optional

The viewer auto-selects `ws://` (http) or `wss://` (https), so it works behind nginx.
**WebSocket upgrade headers are mandatory**, one server block per site/port:
```nginx
server {
    listen 443 ssl;
    server_name rbi.company.com;
    # ssl_certificate ... ; ssl_certificate_key ... ;
    location / {
        proxy_pass http://127.0.0.1:8100;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 86400;
    }
}
```

---

## Scaling beyond one box

One server real-time-encodes only a handful of streams (CPU-bound). For **15–50
concurrent**, run **multiple servers**, each with this same package and its own `cap`,
behind a load balancer that routes new users to the least-loaded node. Each server
exposes `GET /load` ({cpu,ram,cap,used}) — the hook a balancer / aggregated dashboard
uses. No app change needed to add nodes.

---

## Uninstall

```bash
sudo ./uninstall.sh        # stops + removes services; leaves /opt/rbi-web-v1 files
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Port returns `000` | service down or port not in config — `journalctl -u rbi-webrtc`, check `shared/config.json` |
| Connects but black screen | must be `browser=google-chrome-stable` (re-run installer's Chrome step if it shows chromium) |
| `address already in use` | a stale process — `systemctl restart rbi-webrtc` (services kill orphans on stop) |
| Admin login fails | check `ADMIN_USER/ADMIN_PASS` in `rbi.env`, then restart rbi-admin |
| Stream stutters under load | you're past CPU cap — lower `MAX_SESSIONS` or add a server (see Scaling) |

xkbcomp keysym warnings and dbus "document portal" lines in logs are harmless.

---

## Security notes for the team

- Change `ADMIN_PASS` and keep `ADMIN_SECRET` private (both in `rbi.env`, mode 600 recommended:
  `chmod 600 /opt/rbi-web-v1/rbi.env`).
- The admin console (8200) should not be exposed to the public internet — restrict by
  firewall/VPN to operators.
- RBI shields the front-end (users only get pixels), but **typed input still reaches the
  real backend**. Keep the usual protections (WAF, parameterized queries, input validation)
  on the target applications — RBI is one strong layer, not the whole defense.
