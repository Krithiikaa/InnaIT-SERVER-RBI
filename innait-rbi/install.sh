#!/usr/bin/env bash
# InnaIT RBI — automated installer for a fresh Ubuntu server (22.04/24.04).
# Run as root:   sudo ./install.sh
set -euo pipefail

INSTALL_DIR="/opt/rbi-web-v1"
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "==> InnaIT RBI installer"
[ "$(id -u)" -eq 0 ] || { echo "Please run as root (sudo ./install.sh)"; exit 1; }

echo "==> Installing system dependencies..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y \
  xvfb xdotool x11-utils wget \
  python3 python3-pip python3-gi \
  gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly gstreamer1.0-nice \
  gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0

echo "==> Installing Python libraries..."
pip3 install --break-system-packages aiohttp psutil || pip3 install aiohttp psutil

echo "==> Installing Google Chrome (non-snap; required for headless rendering)..."
if ! command -v google-chrome-stable >/dev/null 2>&1; then
  wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb -O /tmp/chrome.deb
  apt-get install -y /tmp/chrome.deb
fi
command -v google-chrome-stable >/dev/null 2>&1 && echo "    Chrome OK" || echo "    WARNING: Chrome not found"

echo "==> Copying application to $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR"
cp -r "$HERE/app/." "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/start.sh" "$INSTALL_DIR/entrypoint.sh" 2>/dev/null || true
mkdir -p "$INSTALL_DIR/logs"

echo "==> Verifying Python compiles..."
python3 -m py_compile "$INSTALL_DIR/webrtc_server.py" "$INSTALL_DIR/admin_server.py"
echo "    Python OK"

# ---- env file ----
if [ ! -f "$INSTALL_DIR/rbi.env" ]; then
  echo "==> Creating $INSTALL_DIR/rbi.env ..."
  DETECTED_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
  SECRET="$(head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 40)"
  cp "$HERE/rbi.env.example" "$INSTALL_DIR/rbi.env"
  sed -i "s|CHANGE_ME_SERVER_IP|${DETECTED_IP:-127.0.0.1}|" "$INSTALL_DIR/rbi.env"
  sed -i "s|change-this-long-random-secret|${SECRET}|" "$INSTALL_DIR/rbi.env"
  echo "    Wrote rbi.env (SERVER_HOST=${DETECTED_IP:-127.0.0.1}). EDIT IT to set admin password."
else
  echo "==> Keeping existing $INSTALL_DIR/rbi.env"
fi

# ---- systemd ----
echo "==> Installing systemd services..."
cp "$HERE/systemd/rbi-webrtc.service" "$HERE/systemd/rbi-admin.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable rbi-webrtc rbi-admin
systemctl restart rbi-webrtc rbi-admin

sleep 8
echo "==> Status:"
systemctl --no-pager --lines=3 status rbi-webrtc || true
echo
BASE_PORT="$(grep -E '^BASE_PORT=' "$INSTALL_DIR/rbi.env" | cut -d= -f2)"
ADMIN_PORT="$(grep -E '^ADMIN_PORT=' "$INSTALL_DIR/rbi.env" | cut -d= -f2)"
HOST="$(grep -E '^SERVER_HOST=' "$INSTALL_DIR/rbi.env" | cut -d= -f2)"
echo "==> Health check:"
curl -s -o /dev/null -w "    viewer :$BASE_PORT -> %{http_code}\n" "http://localhost:${BASE_PORT}" || true
curl -s "http://localhost:${BASE_PORT}/load" | head -c 200; echo

cat <<DONE

============================================================
 InnaIT RBI installed.
   Viewer : http://${HOST}:${BASE_PORT}
   Admin  : http://${HOST}:${ADMIN_PORT}   (login in rbi.env)

 IMPORTANT:
   1) Edit /opt/rbi-web-v1/rbi.env  -> set ADMIN_PASS (and confirm SERVER_HOST)
   2) Edit /opt/rbi-web-v1/shared/config.json -> your sites + ports
   3) Open firewall for the viewer ports (8100, 8101, ...) and admin (8200)
   4) Apply changes:  systemctl restart rbi-webrtc rbi-admin

 Logs:    journalctl -u rbi-webrtc -f      journalctl -u rbi-admin -f
 Confirm: the streamer log should say  browser=google-chrome-stable  psutil=yes
============================================================
DONE
