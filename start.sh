#!/usr/bin/env bash
# PodLore 一键启动：后端 FastAPI + 前端 Vite
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d backend/.venv ]; then
  echo "❌ 尚未安装，请先运行 ./install.sh"
  exit 1
fi

source backend/.venv/bin/activate

cleanup() { kill $BACK_PID $FRONT_PID 2>/dev/null || true; }
trap cleanup EXIT INT TERM

echo "---- 启动后端 http://localhost:8000 ----"
(cd backend && uvicorn app.main:app --reload --port 8000) &
BACK_PID=$!

echo "---- 启动前端 http://localhost:5173 ----"
(cd frontend && npm run dev) &
FRONT_PID=$!

echo ""
echo "✅ PodLore 已启动：前端 http://localhost:5173 ｜ 后端文档 http://localhost:8000/docs"
echo "   Ctrl+C 退出"
wait
