#!/bin/bash
set -e

MODE="${ANCHOR_MODE:-both}"

case "$MODE" in
  web)
    exec python anchor_web.py --db-path /app/anchor_data --host 0.0.0.0 --port 8000
    ;;
  mcp)
    exec python anchor_mcp.py --db-path /app/anchor_data --transport streamable-http --host 0.0.0.0 --port 8001
    ;;
  both)
    exec python anchor_server.py \
      --db-path /app/anchor_data \
      --web-host 0.0.0.0 --web-port 8000 \
      --mcp-host 0.0.0.0 --mcp-port 8001
    ;;
  *)
    echo "Unknown ANCHOR_MODE: $MODE (use web|mcp|both)"
    exit 1
    ;;
esac
