#!/usr/bin/env bash
# Installs ScoutAI as an always-on background service on an Ubuntu/Debian server.
# Run it inside the project folder:   bash deploy/install_ubuntu.sh
# Before starting, put your .env and the two *.session files in this folder (see docs/CLOUD_HOSTING.md).
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_NAME="$(id -un)"
cd "$DIR"

echo "==> Installing system packages"
sudo apt-get update -y
sudo apt-get install -y python3 python3-venv python3-pip

# Small free servers have 1 GB of RAM: add a 1 GB swap file once so pip and the app never run out of memory
if ! swapon --show | grep -q .; then
    echo "==> Creating 1 GB swap file"
    sudo fallocate -l 1G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

echo "==> Creating the Python environment"
python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt

if [ ! -f .env ] || [ ! -f user_session.session ]; then
    echo
    echo "Missing .env or user_session.session. Copy them from your computer first, e.g. from Windows:"
    echo "  scp .env user_session.session bot_session.session scout.db $USER_NAME@<server-ip>:$DIR/"
    echo "Then run this script again."
    exit 1
fi

echo "==> Installing the systemd service"
sudo tee /etc/systemd/system/scoutai.service >/dev/null <<EOF
[Unit]
Description=ScoutAI opportunity scout
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER_NAME
WorkingDirectory=$DIR
ExecStart=$DIR/venv/bin/python main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now scoutai
sleep 5
sudo systemctl --no-pager status scoutai | head -n 12
echo
echo "Done. Follow the log with:  journalctl -u scoutai -f   (or: tail -f $DIR/scout.log)"
