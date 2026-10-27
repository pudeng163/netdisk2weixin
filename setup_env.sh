#!/usr/bin/env bash
# netdisk2weixin 本地环境一键搭建脚本（Linux / macOS）
# 用法: bash setup_env.sh   （Windows 用户请在 PowerShell 中执行等价命令，见部署说明）
set -e

cd "$(dirname "$0")"

echo "==> 创建虚拟环境 .venv"
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> 升级 pip 并安装依赖"
pip install -q --upgrade pip
pip install -q -r requirements.txt

echo "==> 为二进制加执行权限"
chmod +x assets/bin/kuake-*-linux-amd64 pansou/dist/pansou-linux-amd64 2>/dev/null || true

echo "==> 生成 .env（若已存在则保留）"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "    已复制 .env.example -> .env，请编辑填入你的 Cookie/Webhook"
else
  echo "    .env 已存在，跳过"
fi

echo "==> 运行环境诊断"
python main.py diagnose
echo "退出码: $?"
echo "完成。运行示例: python main.py quark --preview"
