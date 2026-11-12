#!/bin/sh
# cy-edit 剪辑引擎的统一入口（Mac / Linux）：sh run.sh <命令> [参数]（专用运行环境；经验库与长视频版共享）
# 第一次运行时自动准备专用的 Python 3.12 环境（放在用户数据目录，不改系统 Python），之后直接用它运行 editor.py。
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
DATA="${CY_EDIT_DATA:-$HOME/.cy-edit}"
VENV="$DATA/venv"
PY="$VENV/bin/python"
if [ ! -x "$PY" ]; then
  UV="$(command -v uv 2>/dev/null || true)"
  [ -z "$UV" ] && [ -x "$HOME/.local/bin/uv" ] && UV="$HOME/.local/bin/uv"
  if [ -z "$UV" ]; then
    echo "正在配置：第一次使用，安装 uv（用来准备独立的 Python 3.12）…" >&2
    curl -LsSf https://astral.sh/uv/install.sh | sh >&2
    UV="$HOME/.local/bin/uv"
  fi
  mkdir -p "$DATA"
  echo "正在配置：准备 Python 3.12 专用环境…" >&2
  "$UV" venv "$VENV" --python 3.12 --seed >&2
fi
exec "$PY" "$HERE/editor.py" "$@"
