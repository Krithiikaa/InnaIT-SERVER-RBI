#!/bin/bash
# Run both RBI servers, pinned to the SAME shared folder so the admin panel and the
# streamer read/write the same config.json + sessions.json (dashboard reflects all viewers).
cd "$(dirname "$0")"
mkdir -p logs shared

# shared state + settings used by BOTH processes
export SHARED_DIR="$(pwd)/shared"
export SERVER_HOST="10.0.49.145"        # used by admin to show "Open in RBI" links
export BASE_PORT="8100"
export PORT="8100"

# admin credentials (CHANGE THESE)
export ADMIN_PORT="8200"
export ADMIN_USER="admin"
export ADMIN_PASS="precision"
export ADMIN_SECRET="change-this-secret"

# streamer settings
export BITRATE="5000000"
export WARM_W="1600"
export WARM_H="900"

./entrypoint.sh          >> ./logs/rbi-webrtc.logs 2>&1 &
python3 admin_server.py  >> ./logs/admin.logs       2>&1 &

echo "RBI started."
echo "  shared dir : $SHARED_DIR"
echo "  admin      : http://$SERVER_HOST:$ADMIN_PORT"
echo "  viewer     : http://$SERVER_HOST:8100  (and each site's own port)"
echo "Check:  curl -s -o /dev/null -w '8100 -> %{http_code}\n' http://localhost:8100"
