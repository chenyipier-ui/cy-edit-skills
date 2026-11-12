#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd "$(dirname "$0")" && pwd)"
if [ "$(uname -s)" != Darwin ]; then echo '此入口用于 macOS；Windows 使用 bootstrap.ps1。' >&2; exit 2; fi
lve_data="${CY_EDIT_LONGVIDEO_DATA:-${LONG_VIDEO_EDITOR_DATA:-$HOME/Library/Application Support/cy-edit-longvideo}}"
if [ -z "${CY_EDIT_LONGVIDEO_DATA:-}" ] && [ -z "${LONG_VIDEO_EDITOR_DATA:-}" ] && [ ! -e "$lve_data" ] && [ -d "$HOME/Library/Application Support/long-video-editor" ]; then
  lve_data="$HOME/Library/Application Support/long-video-editor"
fi
lve_offline=0; lve_next=0
for arg in "$@"; do
  if [ "$lve_next" = 1 ]; then lve_data="$arg"; lve_next=0; continue; fi
  case "$arg" in
    --state-dir) lve_next=1;;
    --state-dir=*) lve_data="${arg#*=}";;
    --offline) lve_offline=1;;
    --plan) printf '%s\n' '自动流程：本机目录 → uv → Python 3.12 → 隔离依赖 → FFmpeg → 转写模型 → 配置记录。已有文稿可 --backend none。此 plan 不安装。'; exit 0;;
  esac
done
mkdir -p "$lve_data/bootstrap/bin"
lve_data="$(cd "$lve_data" && pwd)"
export CY_EDIT_LONGVIDEO_DATA="$lve_data"
export UV_PYTHON_INSTALL_DIR="$lve_data/bootstrap/python"
export UV_PYTHON_PREFERENCE=only-managed
export UV_CACHE_DIR="$lve_data/bootstrap/cache"
lve_uv="$lve_data/bootstrap/bin/uv"
if [ ! -x "$lve_uv" ]; then
  if command -v uv >/dev/null 2>&1; then lve_uv="$(command -v uv)";
  else
    if [ "$lve_offline" = 1 ]; then echo '离线配置需要已缓存 uv/Python；提供联网环境或预备离线包。' >&2; exit 2; fi
    /usr/bin/curl --proto '=https' --tlsv1.2 -fL --retry 3 'https://astral.sh/uv/0.13.7/install.sh' -o "$lve_data/bootstrap/uv-installer.sh"
    UV_UNMANAGED_INSTALL="$lve_data/bootstrap/bin" UV_NO_MODIFY_PATH=1 sh "$lve_data/bootstrap/uv-installer.sh"
  fi
fi
if [ "$lve_offline" = 1 ]; then
  lve_python="$("$lve_uv" python find 3.12 --offline)"
else
  if ! "$lve_uv" python install 3.12; then
    # 国内网络：GitHub 下载 Python 失败时换镜像；用户已设 UV_PYTHON_INSTALL_MIRROR 时不改写。
    if [ -n "${UV_PYTHON_INSTALL_MIRROR:-}" ]; then exit 2; fi
    echo 'Python 官方下载失败，改用国内镜像重试' >&2
    UV_PYTHON_INSTALL_MIRROR='https://registry.npmmirror.com/-/binary/python-build-standalone' "$lve_uv" python install 3.12
  fi
  lve_python="$("$lve_uv" python find 3.12)"
fi
exec "$lve_python" "$script_dir/bootstrap.py" --python "$lve_python" "$@"
