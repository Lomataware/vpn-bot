#!/bin/bash
set -e
cd /root/vpn_bot/vpntg
git pull origin main
source venv/bin/activate
pip install -r requirements.txt --quiet
systemctl restart vpnbot hy2auth
echo "✅ Бот и сервис авторизации успешно обновлены!"
