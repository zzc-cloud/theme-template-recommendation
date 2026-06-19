#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -x "venv/bin/python" ]; then
  echo "未找到 venv/bin/python。请先执行："
  echo "python3.11 -m venv venv"
  echo "venv/bin/python -m pip install -e ."
  exit 1
fi

PORT="${INTERACTION_CONSOLE_PORT:-5174}"
if [ -f ".env" ]; then
  ENV_PORT="$(grep -E '^INTERACTION_CONSOLE_PORT=' .env | tail -n 1 | cut -d= -f2- | tr -d '"'"'"'\"')"
  PORT="${ENV_PORT:-$PORT}"
fi

PIDS="$(lsof -tiTCP:"$PORT" -sTCP:LISTEN || true)"
if [ -n "$PIDS" ]; then
  echo "端口 $PORT 已被占用，正在停止旧进程：$PIDS"
  kill $PIDS
  sleep 1
fi

exec venv/bin/python -m interaction_console.main
