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

USER_NAME=$(id -un)
echo "$USER_NAME ALL=(root) NOPASSWD: /usr/bin/systemctl start *.service, /usr/bin/systemctl stop *.service, /usr/bin/systemctl restart *.service, /usr/bin/systemctl reboot, /usr/bin/systemctl poweroff, /usr/bin/docker system prune *" > /tmp/zgx-launcher.sudoers
sudo chown root:root /tmp/zgx-launcher.sudoers
sudo chmod 440 /tmp/zgx-launcher.sudoers
sudo visudo -cf /tmp/zgx-launcher.sudoers
sudo mv /tmp/zgx-launcher.sudoers /etc/sudoers.d/zgx-launcher

cat > /tmp/zgx-launcher.service << UNIT
[Unit]
Description=HP ZGX Nano Fleet Console
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER_NAME
Group=$USER_NAME
WorkingDirectory=$HOME/zgx-partner-kit
Environment=HOME=$HOME
Environment=PYTHONUNBUFFERED=1
Environment=ZGX_DEMOS_ROOT=$HOME/zgx-demos
Environment=ZGX_LAUNCHER_HOST=0.0.0.0
Environment=ZGX_LAUNCHER_PORT=18888
ExecStart=/usr/bin/python3 -u $HOME/zgx-partner-kit/launcher_web.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT
sudo mv /tmp/zgx-launcher.service /etc/systemd/system/zgx-launcher.service
sudo chown root:root /etc/systemd/system/zgx-launcher.service
sudo systemctl daemon-reload
sudo systemctl enable --now zgx-launcher.service
echo '[OK] Fleet Console ready on port 18888 (default login: admin / Admin321).'
