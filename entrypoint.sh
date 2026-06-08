#!/bin/bash
set -e

MODE="${ANCHOR_MODE:-web}"

case "$MODE" in
  web)
    exec python anchor_web.py --db-path /data --host 0.0.0.0 --port 5000
    ;;
  mcp)
    exec python anchor_mcp.py --db-path /data --transport streamable-http --host 0.0.0.0 --port 3333
    ;;
  both)
    python anchor_web.py --db-path /data --host 0.0.0.0 --port 5000 &
    exec python anchor_mcp.py --db-path /data --transport streamable-http --host 0.0.0.0 --port 3333
    ;;
  *)
    echo "Unknown ANCHOR_MODE: $MODE (use web|mcp|both)"
    exit 1
    ;;
esac
