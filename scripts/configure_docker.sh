#!/bin/bash
set -e

WIN_HOST="${WIN_HOST:-172.20.35.30}"
PROXY_PORT="${PROXY_PORT:-10809}"
PROXY_URL="http://${WIN_HOST}:${PROXY_PORT}"

mkdir -p /etc/docker
cat > /etc/docker/daemon.json << EOF
{
  "proxies": {
    "http-proxy": "${PROXY_URL}",
    "https-proxy": "${PROXY_URL}",
    "no-proxy": "127.0.0.0/8,localhost"
  }
}
EOF

pkill -9 dockerd || true
sleep 1
export http_proxy="${PROXY_URL}"
export https_proxy="${PROXY_URL}"
export HTTP_PROXY="${PROXY_URL}"
export HTTPS_PROXY="${PROXY_URL}"

nohup dockerd > /var/log/dockerd.log 2>&1 &
sleep 3
docker info | grep -i -A 6 proxy || true
