#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ "$(uname -s)" != "Linux" || "$(uname -m)" != "aarch64" ]]; then
  echo "[ERROR] Este instalador esta pensado para Ubuntu ARM64 en HP ZGX Nano."
  exit 1
fi

echo "[INFO] 1/2 Instalando Portal de Monitorizacion y Partner Kit (zgx-partner-kit)..."
bash "$ROOT_DIR/zgx-partner-kit/install.sh" "$@"

echo "[INFO] 2/2 Instalando HP ZGX Nano Collective Showcase (hp-zgx-fury-showcase-v3.2.0-nano.3)..."
SHOWCASE_SRC="$ROOT_DIR/hp-zgx-fury-showcase-v3.2.0-nano.3"
SHOWCASE_DST="$HOME/hp-zgx-nano"

if [[ "$SHOWCASE_SRC" != "$SHOWCASE_DST" && ! -e "$SHOWCASE_DST" ]]; then
  ln -s "$SHOWCASE_SRC" "$SHOWCASE_DST"
  echo "[OK] Enlazado $SHOWCASE_DST -> $SHOWCASE_SRC"
fi

bash "$SHOWCASE_SRC/install.sh"

IP_ADDR=$(hostname -I | awk '{print $1}')
echo ""
echo "[OK] Instalacion unificada completada."
echo " - Portal de Monitorizacion y Flota: http://${IP_ADDR:-127.0.0.1}:18888"
echo " - Nano Collective Showcase (Free Mode): http://${IP_ADDR:-127.0.0.1}:18808/free"
