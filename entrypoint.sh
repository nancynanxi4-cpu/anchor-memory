#!/bin/bash
set -e

MODE="${ANCHOR_MODE:-both}"

cleanup() {
    echo "Shutting down..."
    kill 0 2>/dev/null
    exit 0
}
trap cleanup SIGTERM SIGINT SIGQUIT

start_nginx() {
    nginx -g 'daemon off;' &
}

case "$MODE" in
  web)
    start_nginx
    exec python anchor_web.py --db-path /app/anchor_data --host 0.0.0.0 --port 8000
    ;;
  mcp)
    start_nginx
    exec python anchor_mcp.py --db-path /app/anchor_data --transport streamable-http --host 0.0.0.0 --port 8001
    ;;
  both)
    python anchor_web.py --db-path /app/anchor_data --host 0.0.0.0 --port 8000 &
    python anchor_mcp.py --db-path /app/anchor_data --transport streamable-http --host 0.0.0.0 --port 8001 &
    start_nginx
    wait
    ;;
  *)
    echo "Unknown ANCHOR_MODE: $MODE (use web|mcp|both)"
    exit 1
    ;;
esac
