#!/bin/bash
cd /root/vpn_bot/vpntg
git pull origin main
source venv/bin/activate
pip install -r requirements.txt --quiet
systemctl restart vpnbot
echo "✅ Бот успешно обновлен!"