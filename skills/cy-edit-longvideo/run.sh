#!/usr/bin/env bash
set -euo pipefail
cy_skill="$(cd "$(dirname "$0")" && pwd)"
cy_data="${CY_EDIT_LONGVIDEO_DATA:-${LONG_VIDEO_EDITOR_DATA:-$HOME/Library/Application Support/cy-edit-longvideo}}"
if [ -z "${CY_EDIT_LONGVIDEO_DATA:-}" ] && [ -z "${LONG_VIDEO_EDITOR_DATA:-}" ] && [ ! -e "$cy_data" ] && [ -d "$HOME/Library/Application Support/long-video-editor" ]; then cy_data="$HOME/Library/Application Support/long-video-editor"; fi
cy_next=0
cy_offline=0
for cy_arg in "$@"; do
  if [ "$cy_next" = 1 ]; then cy_data="$cy_arg"; cy_next=0; continue; fi
  case "$cy_arg" in --state-dir) cy_next=1;; --state-dir=*) cy_data="${cy_arg#*=}";; --offline) cy_offline=1;; esac
done
cy_python=''
if [ -f "$cy_data/python-path.txt" ]; then IFS= read -r cy_python < "$cy_data/python-path.txt" || true; fi
if [ ! -x "$cy_python" ]; then
  for cy_kind in faster mlx none; do
    if [ -x "$cy_data/runtime/v2-$cy_kind/bin/python" ]; then cy_python="$cy_data/runtime/v2-$cy_kind/bin/python"; break; fi
  done
fi
if [ ! -x "$cy_python" ]; then
  if [ "$cy_offline" = 1 ]; then
    bash "$cy_skill/scripts/bootstrap.sh" --state-dir "$cy_data" --backend none --offline
  else
    bash "$cy_skill/scripts/bootstrap.sh" --state-dir "$cy_data" --backend none
  fi
  IFS= read -r cy_python < "$cy_data/python-path.txt" || true
fi
export CY_EDIT_LONGVIDEO_DATA="$cy_data"
exec "$cy_python" "$cy_skill/scripts/editor.py" "$@"
