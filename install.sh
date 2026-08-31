#!/usr/bin/env bash
# PodLore 一键安装：venv + 后端依赖 + 前端依赖 + FunASR 模型下载
set -euo pipefail
cd "$(dirname "$0")"

echo "======================================"
echo " PodLore 一键安装"
echo "======================================"

# ---- 0. 选择 Python 解释器 ----
# 注意：Intel Mac（x86_64）上 torch 官方 wheel 最高只到 Python 3.12
# （torch 2.2.2 为最后支持版本）；Apple Silicon（arm64）则 3.13 也可用。
ARCH=$(uname -m)
if [ "$ARCH" = "x86_64" ]; then
  CANDIDATES="python3.12 python3.11 python3.10"
else
  CANDIDATES="python3.13 python3.12 python3.11 python3.10"
fi
PY=""
for cand in $CANDIDATES; do
  if command -v "$cand" >/dev/null 2>&1; then PY="$cand"; break; fi
done

# 兜底：本机无合理解释器时，用 uv 安装托管版 3.12
if [ -z "$PY" ]; then
  if command -v uv >/dev/null 2>&1; then
    echo "---- 未找到合适 Python，使用 uv 安装托管版 3.12 ----"
    uv python install 3.12
    PY="python3.12"
  else
    echo "❌ 未找到可用的 Python 3.10-3.12（Intel Mac 的 torch 最高支持 3.12）"
    exit 1
  fi
fi
echo "---- 使用解释器：$PY（$($PY --version 2>&1)，$ARCH）----"

# ---- 1. 创建虚拟环境 ----
if [ ! -d backend/.venv ]; then
  echo "---- [1/4] 创建虚拟环境 backend/.venv ----"
  "$PY" -m venv backend/.venv
else
  echo "---- [1/4] 虚拟环境已存在，跳过 ----"
fi
source backend/.venv/bin/activate
python -m pip install --upgrade pip

# ---- 2. 后端依赖 ----
echo "---- [2/4] 安装后端依赖（含 torch/funasr，体积较大，请耐心等待）----"
pip install -e ".[dev]"

# ---- 3. 前端依赖 ----
if [ ! -d frontend ]; then
  echo "❌ 未找到 frontend/ 目录，请先按 M0.2b 创建前端脚手架"
  exit 1
fi
echo "---- [3/4] 安装前端依赖 ----"
(cd frontend && npm install)

# ---- 4. FunASR 模型下载（modelscope 源，自带进度条）----
echo "---- [4/4] 下载 3 个 FunASR 模型：SenseVoiceSmall(~230MB) + fsmn-vad + ct-punc ----"
python backend/scripts/download_models.py

# ---- 5. 环境配置 ----
if [ ! -f .env ]; then
  cp .env.example .env
  echo "---- 已生成 .env（请填入 DEEPSEEK_API_KEY）----"
fi

echo ""
echo "✅ 安装完成！运行 ./start.sh 启动（后端 :8000，前端 :5173）"
