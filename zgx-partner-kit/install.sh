#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ $(uname -s) != Linux || $(uname -m) != aarch64 ]]; then
  echo 'This installer targets Ubuntu ARM64 on HP ZGX Nano. No changes made.'
  exit 1
fi
echo 'HP ZGX Nano partner kit: first setup needs internet and administrator access.'
echo 'Public profile: about 25 GB of models, 80 GB free disk recommended.'
sudo apt-get update
sudo apt-get install -y git curl ca-certificates build-essential cmake ninja-build python3 python3-venv gnome-terminal xdg-utils xdg-user-dirs ffmpeg
if ! command -v uv >/dev/null && [[ ! -x "$HOME/.local/bin/uv" ]]; then
  uv_script=$(mktemp)
  trap 'rm -f "$uv_script"' EXIT
  curl --fail --show-error --location https://astral.sh/uv/install.sh -o "$uv_script"
  sh "$uv_script"
  rm -f "$uv_script"
  trap - EXIT
fi
export PATH="/usr/local/cuda/bin:$HOME/.local/bin:$PATH"
export LD_LIBRARY_PATH="/usr/local/cuda/lib64:${LD_LIBRARY_PATH:-}"
if [[ $# -eq 0 ]]; then
  set -- all
fi
python3 portable.py setup "$@"

if [[ -d "$PWD/custom-overrides/campuscompass-local" && -d "$HOME/zgx-demos/campuscompass-local" ]]; then
  cp -r "$PWD/custom-overrides/campuscompass-local/." "$HOME/zgx-demos/campuscompass-local/"
  echo '[OK] Applied bilingual and LAN overrides to campuscompass-local.'
fi

if [[ -d "$PWD/custom-overrides/research-observatory" && -d "$HOME/zgx-demos/research-observatory" ]]; then
  cp -r "$PWD/custom-overrides/research-observatory/." "$HOME/zgx-demos/research-observatory/"
  echo '[OK] Applied layout and overlay overrides to research-observatory.'
fi

echo '[OK] ZGX Partner Kit demos and public models installed.'
