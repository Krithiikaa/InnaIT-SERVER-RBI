#!/usr/bin/env bash
set -e
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
systemctl disable --now rbi-webrtc rbi-admin 2>/dev/null || true
rm -f /etc/systemd/system/rbi-webrtc.service /etc/systemd/system/rbi-admin.service
systemctl daemon-reload
pkill -9 -f webrtc_server.py 2>/dev/null || true
pkill -9 -f admin_server.py 2>/dev/null || true
pkill -9 -f chrome 2>/dev/null || true; pkill -9 -f chromium 2>/dev/null || true; pkill -9 -f Xvfb 2>/dev/null || true
echo "Services removed. App files left in /opt/rbi-web-v1 (delete manually if desired)."
