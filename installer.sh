#!/usr/bin/env bash
set -e

USER=pi
HOME_DIR=/home/$USER
VENV_DIR=$HOME_DIR/ezshare_env
SLEEP_SCRIPT=sleep.py
WEB_SCRIPT=web.py

# 1. System packages
echo "==> Updating apt & installing system deps…"
apt update
apt install -y python3-venv python3-pip network-manager

# 2. Create & populate the venv
echo "==> Creating virtualenv at $VENV_DIR…"
sudo -u $USER python3 -m venv $VENV_DIR

echo "==> Installing Python packages…"
sudo -u $USER $VENV_DIR/bin/pip install --upgrade pip
sudo -u $USER $VENV_DIR/bin/pip install \
    requests \
    beautifulsoup4 \
    Flask

# 3. Fix permissions on your home directory
echo "==> Fixing ownership of $HOME_DIR…"
chown -R $USER:$USER $HOME_DIR

# 4. Create a sample config.json if missing
CFG=$HOME_DIR/config.json
if [ ! -f $CFG ]; then
  echo "==> Writing sample $CFG…"
  cat > $CFG <<EOF
{
  "client_id":     "YOUR_CLIENT_ID",
  "client_secret": "YOUR_CLIENT_SECRET",
  "username":      "YOUR_USERNAME",
  "password":      "YOUR_PASSWORD"
}
EOF
  chown $USER:$USER $CFG
  echo "   Edit $CFG with your SleepHQ credentials"
fi

# 5. Write systemd units
echo "==> Writing systemd unit files…"
SERVICE_DIR=/etc/systemd/system

# sleep.service
cat > $SERVICE_DIR/sleep.service <<EOF
[Unit]
Description=SleepHQ uploader (runs every 15m)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$HOME_DIR
ExecStart=$VENV_DIR/bin/python $HOME_DIR/$SLEEP_SCRIPT
User=$USER

[Install]
WantedBy=multi-user.target
EOF

# sleep.timer
cat > $SERVICE_DIR/sleep.timer <<EOF
[Unit]
Description=Run sleep.py every 15 minutes

[Timer]
OnBootSec=5min
OnUnitActiveSec=15min
Persistent=true

[Install]
WantedBy=timers.target
EOF

# web.service
cat > $SERVICE_DIR/web.service <<EOF
[Unit]
Description=SleepHQ Web Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$HOME_DIR
ExecStart=$VENV_DIR/bin/python $HOME_DIR/$WEB_SCRIPT
User=$USER
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# 6. Enable & start
echo "==> Reloading systemd & enabling services…"
systemctl daemon-reload
systemctl enable --now sleep.timer
systemctl enable --now web.service

echo "✅ Installation complete!"
echo "   Check uploader:  systemctl list-timers | grep sleep.timer"
echo "   Logs:           journalctl -u sleep.service -f"
echo "   Check web:      systemctl status web.service"
echo "   Web logs:       journalctl -u web.service -f"