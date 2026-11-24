#!/usr/bin/env bash
set -euo pipefail

PORTAL_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$PORTAL_DIR/.." && pwd)"
KIT_DIR="${ZGX_KIT_DIR:-$ROOT_DIR/zgx-partner-kit}"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "[ERROR] Este instalador requiere Linux (Ubuntu en HP ZGX Nano)."
  exit 1
fi

USER_NAME=$(id -un)
echo "$USER_NAME ALL=(root) NOPASSWD: /usr/bin/systemctl start *.service, /usr/bin/systemctl stop *.service, /usr/bin/systemctl restart *.service, /usr/bin/systemctl reboot, /usr/bin/systemctl poweroff, /usr/bin/docker system prune *, /usr/bin/nmcli *" > /tmp/zgx-launcher.sudoers
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
WorkingDirectory=$PORTAL_DIR
Environment=HOME=$HOME
Environment=PYTHONUNBUFFERED=1
Environment=ZGX_DEMOS_ROOT=$HOME/zgx-demos
Environment=ZGX_KIT_DIR=$KIT_DIR
Environment=ZGX_LAUNCHER_HOST=0.0.0.0
Environment=ZGX_LAUNCHER_PORT=18888
ExecStart=/usr/bin/python3 -u $PORTAL_DIR/launcher_web.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT

sudo mv /tmp/zgx-launcher.service /etc/systemd/system/zgx-launcher.service
sudo chown root:root /etc/systemd/system/zgx-launcher.service
sudo systemctl daemon-reload
sudo systemctl enable --now zgx-launcher.service

echo "[OK] Fleet Console ready on port 18888 (default login: admin / Admin321)."
