#!/usr/bin/env bash
# =============================================================================
# Автоустановка основного VPN-сервера (Master) на Ubuntu 22.04 / 24.04:
#   - Let's Encrypt сертификат для вашего домена
#   - Marzban (панель + Xray, VLESS + Reality), панель строго на 127.0.0.1:8000
#   - Автонастройка Hosts в Marzban API + сохранение секций stats/policy/routing
#   - Hysteria2 с HTTP-аутентификацией через auth_server.py бота
#   - vpn-bot с systemd-юнитами (vpnbot.service и hy2auth.service)
#   - Утилита /usr/local/bin/vpn-add-node для подключения вторых серверов (нод)
#   - ufw: открыты только 22, 80, 443 (tcp+udp); 9443 — для localhost, self и нод
# =============================================================================
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

# ---------- helpers ----------------------------------------------------------
log()  { echo -e "\n\033[1;32m==> $*\033[0m"; }
warn() { echo -e "\033[1;33m[!] $*\033[0m"; }
die()  { echo -e "\033[1;31m[x] $*\033[0m" >&2; exit 1; }

ask() {  # ask VAR "вопрос" [default]
  local var="$1" q="$2" def="${3:-}"
  if [[ -n "${!var:-}" ]]; then return 0; fi
  local val=""
  read -rp "$q${def:+ [$def]}: " val || true
  printf -v "$var" '%s' "${val:-$def}"
}

[[ $EUID -eq 0 ]] || die "Запустите от root (sudo -i)"

# ---------- 1. Входные данные -------------------------------------------------
log "Параметры установки основного сервера (Master)"
ask DOMAIN            "Домен, указывающий на этот сервер (A-запись)"
ask SERVER_LABEL      "Название локации для клиентов" "Main"
ask LE_EMAIL          "Email для Let's Encrypt"
ask BOT_TOKEN         "BOT_TOKEN от @BotFather"
ask ADMIN_ID          "Ваш Telegram ID (ADMIN_ID)"
ask BOT_REPO          "Git-URL репозитория бота"
ask SUPPORT_USERNAME  "Username поддержки (без @)" "support"
ask SUPPORT_PHONE     "Телефон для оплаты переводом" ""
ask BANK_NAME         "Название банка" "Т-Банк"
ask PRICE_1_MONTH     "Цена за 1 месяц, ₽" "100"
ask TRIAL_DAYS        "Пробный период, дней" "3"

[[ -n "$DOMAIN" && -n "$LE_EMAIL" && -n "$BOT_TOKEN" && -n "$ADMIN_ID" && -n "$BOT_REPO" ]] \
  || die "Не заданы обязательные параметры: DOMAIN, LE_EMAIL, BOT_TOKEN, ADMIN_ID, BOT_REPO"

BOT_DIR="/root/vpn_bot/vpntg"
CRED_FILE="/root/install_credentials.txt"
MARZBAN_ADMIN_USER="admin"
MARZBAN_ADMIN_PASS="$(openssl rand -hex 12)"
HY2_AUTH_TOKEN="$(openssl rand -hex 24)"
REALITY_SHORT_ID="$(openssl rand -hex 8)"
REALITY_DEST="www.microsoft.com"

# ---------- 2. Проверки -------------------------------------------------------
log "Проверка сети и DNS"
PUBLIC_IP="$(curl -s4 https://api.ipify.org || true)"
[[ -n "$PUBLIC_IP" ]] || die "Не удалось определить публичный IPv4 сервера"
RESOLVED_IP="$(getent ahostsv4 "$DOMAIN" | awk 'NR==1{print $1}' || true)"
if [[ "$RESOLVED_IP" != "$PUBLIC_IP" ]]; then
  die "Домен $DOMAIN указывает на '${RESOLVED_IP:-ничего}', а IP сервера $PUBLIC_IP. Исправьте A-запись и подождите."
fi
log "DNS OK: $DOMAIN -> $PUBLIC_IP"

# ---------- 3. Пакеты и Docker -----------------------------------------------
log "Установка пакетов"
apt-get update -y
apt-get install -y curl wget socat ufw certbot git openssl jq python3-venv python3-pip

if ! command -v docker >/dev/null 2>&1; then
  log "Установка Docker"
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker

# ---------- 4. Firewall -------------------------------------------------------
log "Настройка ufw"
ufw allow 22/tcp    comment 'ssh'
ufw allow 80/tcp    comment 'http (certbot)'
ufw allow 443/tcp   comment 'xray reality'
ufw allow 443/udp   comment 'hysteria2'
# Разрешаем hy2auth (9443) как через внешний IP (hairpin), так и через 127.0.0.1 (lo)
ufw allow from "$PUBLIC_IP" to any port 9443 proto tcp comment 'hy2auth (self-ip)'
ufw allow from 127.0.0.1 to any port 9443 proto tcp comment 'hy2auth (localhost)'
ufw --force enable
warn "Панель Marzban (порт 8000) привязана к 127.0.0.1 — доступ снаружи закрыт."

# ---------- 5. Let's Encrypt --------------------------------------------------
log "Получение сертификата Let's Encrypt"
if [[ ! -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]]; then
  certbot certonly --standalone -d "$DOMAIN" -m "$LE_EMAIL" \
    --agree-tos --non-interactive --keep-until-expiring
fi

mkdir -p /etc/letsencrypt/renewal-hooks/deploy
cat > /etc/letsencrypt/renewal-hooks/deploy/restart-vpn.sh <<'EOF'
#!/usr/bin/env bash
systemctl restart hysteria hy2auth 2>/dev/null || true
EOF
chmod +x /etc/letsencrypt/renewal-hooks/deploy/restart-vpn.sh

# ---------- 6. Marzban --------------------------------------------------------
log "Установка Marzban (официальный скрипт)"
if ! command -v marzban >/dev/null 2>&1; then
  bash -c "$(curl -sL https://github.com/Gozargah/Marzban-scripts/raw/master/marzban.sh)" @ install
fi

MARZBAN_ENV="/opt/marzban/.env"
touch "$MARZBAN_ENV"

set_env_var() {
  local key="$1" val="$2" file="$3"
  if grep -q "^#\?\s*${key}=" "$file"; then
    sed -i "s|^#\?\s*${key}=.*|${key}=${val}|" "$file"
  else
    echo "${key}=${val}" >> "$file"
  fi
}

# Привязываем панель строго к 127.0.0.1, чтобы Docker не открыл порт 8000 наружу
set_env_var "UVICORN_HOST" "127.0.0.1" "$MARZBAN_ENV"
set_env_var "UVICORN_PORT" "8000" "$MARZBAN_ENV"
set_env_var "SUDO_USERNAME" "$MARZBAN_ADMIN_USER" "$MARZBAN_ENV"
set_env_var "SUDO_PASSWORD" "$MARZBAN_ADMIN_PASS" "$MARZBAN_ENV"

marzban restart || true
sleep 5
marzban cli admin import-from-env -y >/dev/null 2>&1 \
  || warn "Не удалось импортировать админа из env. Проверьте: marzban cli admin create --sudo"

# ---------- 7. Xray: VLESS + Reality inbound (со статистикой!) ---------------
log "Настройка Xray (VLESS + Reality + Stats + Anti-Torrent)"
for _ in $(seq 1 30); do
  docker ps --format '{{.Names}}' | grep -qx marzban && break
  sleep 2
done

REALITY_KEYS="$(docker exec marzban xray x25519)"
REALITY_PRIV="$(echo "$REALITY_KEYS" | grep -i 'private' | awk '{print $NF}')"
REALITY_PUB="$(echo "$REALITY_KEYS" | grep -i 'public' | awk '{print $NF}')"
[[ -n "$REALITY_PRIV" ]] || die "Не удалось сгенерировать ключ Reality"

XRAY_JSON="/var/lib/marzban/xray_config.json"
mkdir -p /var/lib/marzban
cat > "$XRAY_JSON" <<EOF
{
  "log": {
    "loglevel": "warning"
  },
  "stats": {},
  "policy": {
    "levels": {
      "0": {
        "statsUserUplink": true,
        "statsUserDownlink": true
      }
    },
    "system": {
      "statsInboundUplink": true,
      "statsInboundDownlink": true,
      "statsOutboundUplink": true,
      "statsOutboundDownlink": true
    }
  },
  "inbounds": [
    {
      "tag": "VLESS_INBOUND",
      "listen": "0.0.0.0",
      "port": 443,
      "protocol": "vless",
      "settings": {
        "clients": [],
        "decryption": "none"
      },
      "streamSettings": {
        "network": "tcp",
        "security": "reality",
        "realitySettings": {
          "show": false,
          "dest": "${REALITY_DEST}:443",
          "xver": 0,
          "serverNames": [
            "${REALITY_DEST}"
          ],
          "privateKey": "${REALITY_PRIV}",
          "shortIds": [
            "${REALITY_SHORT_ID}"
          ]
        }
      },
      "sniffing": {
        "enabled": true,
        "destOverride": [
          "http",
          "tls",
          "quic"
        ]
      }
    }
  ],
  "outbounds": [
    {
      "protocol": "freedom",
      "tag": "DIRECT"
    },
    {
      "protocol": "blackhole",
      "tag": "BLOCK"
    }
  ],
  "routing": {
    "domainStrategy": "AsIs",
    "rules": [
      {
        "type": "field",
        "ip": [
          "geoip:private"
        ],
        "outboundTag": "BLOCK"
      },
      {
        "type": "field",
        "protocol": [
          "bittorrent"
        ],
        "outboundTag": "BLOCK"
      }
    ]
  }
}
EOF
marzban restart

# Ждём поднятия API Marzban и прописываем Host для генерации vless:// ссылок
log "Регистрация Host (${DOMAIN}:443) в Marzban API"
MARZBAN_TOKEN=""
for _ in $(seq 1 20); do
  MARZBAN_TOKEN="$(curl -s -X POST "http://127.0.0.1:8000/api/admin/token" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "username=${MARZBAN_ADMIN_USER}&password=${MARZBAN_ADMIN_PASS}&grant_type=password" \
    | jq -r '.access_token // empty' || true)"
  [[ -n "$MARZBAN_TOKEN" ]] && break
  sleep 2
done

if [[ -n "$MARZBAN_TOKEN" ]]; then
  HOST_PAYLOAD="$(jq -n \
    --arg remark "🌐 ${SERVER_LABEL}" \
    --arg address "${DOMAIN}" \
    --arg sni "${REALITY_DEST}" \
    '{
      VLESS_INBOUND: [
        {
          remark: $remark,
          address: $address,
          port: 443,
          sni: $sni,
          security: "inbound_default",
          fingerprint: "chrome"
        }
      ]
    }')"
  curl -s -X PUT "http://127.0.0.1:8000/api/hosts" \
    -H "Authorization: Bearer ${MARZBAN_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "$HOST_PAYLOAD" >/dev/null || warn "Не удалось автоматически прописать Host в Marzban API"
else
  warn "Токен Marzban не получен — проверьте Host в панели вручную"
fi

# ---------- 8. Hysteria2 -----------------------------------------------------
log "Установка Hysteria2"
if [[ ! -x /usr/local/bin/hysteria ]]; then
  bash <(curl -fsSL https://get.hy2.sh/)
fi
systemctl disable --now hysteria-server.service 2>/dev/null || true

mkdir -p /etc/hysteria
cat > /etc/hysteria/config.yaml <<EOF
listen: :443

tls:
  cert: /etc/letsencrypt/live/${DOMAIN}/fullchain.pem
  key: /etc/letsencrypt/live/${DOMAIN}/privkey.pem

auth:
  type: http
  http:
    url: https://${DOMAIN}:9443/hy2auth/${HY2_AUTH_TOKEN}

masquerade:
  type: proxy
  proxy:
    url: https://www.bing.com/
    rewriteHost: true
EOF

cat > /etc/systemd/system/hysteria.service <<'EOF'
[Unit]
Description=Hysteria2 server
After=network-online.target hy2auth.service
Wants=network-online.target

[Service]
ExecStart=/usr/local/bin/hysteria server -c /etc/hysteria/config.yaml
Restart=always
RestartSec=3
User=root

[Install]
WantedBy=multi-user.target
EOF

# ---------- 9. Бот ------------------------------------------------------------
log "Установка vpn-bot"
if [[ ! -d "$BOT_DIR/.git" ]]; then
  mkdir -p "$(dirname "$BOT_DIR")"
  git clone "$BOT_REPO" "$BOT_DIR"
fi
cd "$BOT_DIR"
[[ -d venv ]] || python3 -m venv venv
venv/bin/pip install --upgrade pip --quiet
venv/bin/pip install -r requirements.txt --quiet
venv/bin/pip install aiohttp httpx --quiet

cat > "$BOT_DIR/.env" <<EOF
# ===== BOT =====
BOT_TOKEN=${BOT_TOKEN}
ADMIN_ID=${ADMIN_ID}
ADDITIONAL_ADMINS=

# ===== SUPPORT =====
SUPPORT_USERNAME=${SUPPORT_USERNAME}
SUPPORT_PHONE=${SUPPORT_PHONE}
BANK_NAME=${BANK_NAME}

# ===== MARZBAN (панель локально на 127.0.0.1) =====
MARZBAN_URL=http://127.0.0.1:8000
MARZBAN_ADMIN_USERNAME=${MARZBAN_ADMIN_USER}
MARZBAN_ADMIN_PASSWORD=${MARZBAN_ADMIN_PASS}
MARZBAN_VLESS_INBOUND=VLESS_INBOUND
MARZBAN_HYSTERIA_INBOUND=
DEFAULT_DATA_LIMIT_GB=100

# ===== CRYPTOBOT (выключен по умолчанию) =====
CRYPTOBOT_TOKEN=
CRYPTOBOT_ENABLED=false
CRYPTOBOT_TESTNET=true

# ===== TRIAL & PRICES =====
TRIAL_DAYS=${TRIAL_DAYS}
PRICE_1_MONTH=${PRICE_1_MONTH}

# ===== HYSTERIA2 =====
HY2_SERVERS=${DOMAIN}|${SERVER_LABEL}
HY2_PORT=443
HY2_AUTH_TOKEN=${HY2_AUTH_TOKEN}
HY2_AUTH_HOST=0.0.0.0
HY2_AUTH_PORT=9443
HY2_AUTH_CERTFILE=/etc/letsencrypt/live/${DOMAIN}/fullchain.pem
HY2_AUTH_KEYFILE=/etc/letsencrypt/live/${DOMAIN}/privkey.pem
EOF
chmod 600 "$BOT_DIR/.env"

cat > /etc/systemd/system/vpnbot.service <<EOF
[Unit]
Description=VPN Telegram bot
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=${BOT_DIR}
ExecStart=${BOT_DIR}/venv/bin/python bot.py
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/hy2auth.service <<EOF
[Unit]
Description=Hysteria2 HTTP auth backend
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=${BOT_DIR}
ExecStart=${BOT_DIR}/venv/bin/python auth_server.py
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
EOF

# ---------- 10. Утилита добавления нод (vpn-add-node) -------------------------
log "Создание утилиты /usr/local/bin/vpn-add-node"
cat > /usr/local/bin/vpn-add-node <<'EOF'
#!/usr/bin/env bash
# Подключает второй сервер (ноду) к главному серверу:
#   vpn-add-node <NODE_IP> <NODE_DOMAIN> <NODE_LABEL>
set -euo pipefail

if [[ $# -lt 3 ]]; then
  echo "Использование: vpn-add-node <NODE_IP> <NODE_DOMAIN> <NODE_LABEL>"
  echo "Пример:        vpn-add-node 185.10.20.30 nl.example.com Netherlands"
  exit 1
fi

NODE_IP="$1"
NODE_DOMAIN="$2"
NODE_LABEL="$3"
BOT_ENV="/root/vpn_bot/vpntg/.env"

[[ -f "$BOT_ENV" ]] || { echo "Не найден $BOT_ENV"; exit 1; }
source "$BOT_ENV"

echo "==> 1. Открываем порт 9443 (hy2auth) в UFW для ноды $NODE_IP..."
ufw allow from "$NODE_IP" to any port 9443 proto tcp comment "hy2auth ($NODE_LABEL)"

echo "==> 2. Авторизуемся в Marzban API..."
TOKEN="$(curl -s -X POST "http://127.0.0.1:8000/api/admin/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=${MARZBAN_ADMIN_USERNAME}&password=${MARZBAN_ADMIN_PASSWORD}&grant_type=password" \
  | jq -r '.access_token // empty')"

if [[ -z "$TOKEN" ]]; then
  echo "[x] Ошибка авторизации в Marzban API"
  exit 1
fi

echo "==> 3. Регистрируем ноду $NODE_LABEL ($NODE_IP) в Marzban..."
NODE_PAYLOAD="$(jq -n \
  --arg name "$NODE_LABEL" \
  --arg address "$NODE_IP" \
  '{
    name: $name,
    address: $address,
    port: 62050,
    api_port: 62051,
    usage_coefficient: 1.0,
    add_as_new_host: false
  }')"

curl -s -X POST "http://127.0.0.1:8000/api/node" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "$NODE_PAYLOAD" >/dev/null || true

echo "==> 4. Добавляем домен $NODE_DOMAIN в список Hosts (VLESS_INBOUND)..."
CURRENT_HOSTS="$(curl -s -X GET "http://127.0.0.1:8000/api/hosts" -H "Authorization: Bearer $TOKEN")"
UPDATED_HOSTS="$(echo "$CURRENT_HOSTS" | jq --arg dom "$NODE_DOMAIN" --arg lbl "🌐 $NODE_LABEL" '
  .VLESS_INBOUND = ((.VLESS_INBOUND // []) | map(select(.address != $dom)) + [{
    "remark": $lbl,
    "address": $dom,
    "port": 443,
    "sni": "www.microsoft.com",
    "security": "inbound_default",
    "fingerprint": "chrome"
  }])
')"
curl -s -X PUT "http://127.0.0.1:8000/api/hosts" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "$UPDATED_HOSTS" >/dev/null

echo "==> 5. Добавляем $NODE_DOMAIN|$NODE_LABEL в HY2_SERVERS бота..."
if ! grep -q "$NODE_DOMAIN" "$BOT_ENV"; then
  CUR="$(sed -n 's/^HY2_SERVERS=//p' "$BOT_ENV")"
  NEW="${CUR:+${CUR},}${NODE_DOMAIN}|${NODE_LABEL}"
  sed -i "s@^HY2_SERVERS=.*@HY2_SERVERS=${NEW}@" "$BOT_ENV"
fi

systemctl restart vpnbot
echo "✅ Нода $NODE_LABEL ($NODE_DOMAIN / $NODE_IP) успешно подключена к боту и Marzban!"
EOF
chmod +x /usr/local/bin/vpn-add-node

# Сохраняем клиентский сертификат Marzban для быстрой установки нод
NODE_CERT_FILE="/root/marzban_node_cert.pem"
if [[ -n "$MARZBAN_TOKEN" ]]; then
  curl -s -X GET "http://127.0.0.1:8000/api/node/settings" \
    -H "Authorization: Bearer ${MARZBAN_TOKEN}" \
    | jq -r '.certificate // empty' > "$NODE_CERT_FILE" || true
  chmod 600 "$NODE_CERT_FILE"
fi

# ---------- 11. Запуск --------------------------------------------------------
log "Запуск сервисов"
systemctl daemon-reload
systemctl enable --now hy2auth hysteria vpnbot
sleep 5

# ---------- 12. Проверка ------------------------------------------------------
log "Проверка"
HEALTH="$(curl -sk "https://127.0.0.1:9443/health" || true)"
[[ "$HEALTH" == *'"ok"'* ]] && echo "hy2auth: OK" || warn "hy2auth не отвечает: journalctl -u hy2auth"
systemctl is-active --quiet hysteria && echo "hysteria: OK"  || warn "hysteria не запущен: journalctl -u hysteria"
systemctl is-active --quiet vpnbot   && echo "vpnbot: OK"    || warn "vpnbot не запущен: journalctl -u vpnbot"
docker ps --format '{{.Names}}' | grep -qx marzban && echo "marzban: OK" || warn "marzban не запущен: marzban logs"

# ---------- 13. Итог ----------------------------------------------------------
cat > "$CRED_FILE" <<EOF
Домен (Master):      ${DOMAIN}
IP сервера:          ${PUBLIC_IP}
Marzban панель:      http://127.0.0.1:8000  (строго локально, через SSH-туннель)
Marzban логин:       ${MARZBAN_ADMIN_USER}
Marzban пароль:      ${MARZBAN_ADMIN_PASS}
HY2_AUTH_TOKEN:      ${HY2_AUTH_TOKEN}
Reality PublicKey:   ${REALITY_PUB}
Reality ShortId:     ${REALITY_SHORT_ID}
Сертификат для нод:  ${NODE_CERT_FILE}
Бот:                 ${BOT_DIR}  (конфиг: ${BOT_DIR}/.env)
EOF
chmod 600 "$CRED_FILE"

log "Готово!"
echo "Учётные данные сохранены в $CRED_FILE (chmod 600)."
echo
echo "Доступ к панели Marzban с вашего ПК:"
echo "  ssh -L 8000:127.0.0.1:8000 root@${PUBLIC_IP}"
echo "  Затем откройте: http://127.0.0.1:8000/dashboard/"
echo
echo "Для подключения второго сервера (ноды):"
echo "  1) Скопируйте сертификат из ${NODE_CERT_FILE} и HY2_AUTH_TOKEN (${HY2_AUTH_TOKEN})"
echo "  2) Запустите install_node.sh на втором сервере"
echo "  3) Выполните на ЭТОМ сервере: vpn-add-node <IP_НОДЫ> <ДОМЕН_НОДЫ> <НАЗВАНИЕ>"
