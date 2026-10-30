#!/usr/bin/env bash
# =============================================================================
# Автоустановка ВТОРОГО СЕРВЕРА (Node) на Ubuntu 22.04 / 24.04:
#   - Let's Encrypt сертификат для домена ноды (NODE_DOMAIN)
#   - Marzban-node (Xray VLESS + Reality на 443/tcp, управление с Master по 62050/62051)
#   - Hysteria2 на 443/udp с проверкой подписок через hy2auth главного сервера
#   - ufw: 22, 80, 443 (tcp+udp) открыты всем; 62050-62051 открыты ТОЛЬКО для MAIN_IP
# =============================================================================
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

# ---------- helpers ----------------------------------------------------------
log()  { echo -e "\n\033[1;32m==> $*\033[0m"; }
warn() { echo -e "\033[1;33m[!] $*\033[0m"; }
die()  { echo -e "\033[1;31m[x] $*\033[0m" >&2; exit 1; }

ask() {
  local var="$1" q="$2" def="${3:-}"
  if [[ -n "${!var:-}" ]]; then return 0; fi
  local val=""
  read -rp "$q${def:+ [$def]}: " val || true
  printf -v "$var" '%s' "${val:-$def}"
}

[[ $EUID -eq 0 ]] || die "Запустите от root (sudo -i)"

# ---------- 1. Входные данные -------------------------------------------------
log "Параметры установки ноды (Второго сервера)"
ask NODE_DOMAIN     "Домен ЭТОЙ ноды (A-запись должна указывать на этот сервер)"
ask NODE_LABEL      "Название локации (латиницей или кириллицей, напр. NL)" "Node-2"
ask LE_EMAIL        "Email для Let's Encrypt"
ask MAIN_DOMAIN     "Домен ГЛАВНОГО сервера (Master)"
ask MAIN_IP         "IPv4 ГЛАВНОГО сервера (Master)"
ask HY2_AUTH_TOKEN  "HY2_AUTH_TOKEN с главного сервера (из /root/install_credentials.txt)"

[[ -n "$NODE_DOMAIN" && -n "$LE_EMAIL" && -n "$MAIN_DOMAIN" && -n "$MAIN_IP" && -n "$HY2_AUTH_TOKEN" ]] \
  || die "Заполните все обязательные параметры: NODE_DOMAIN, LE_EMAIL, MAIN_DOMAIN, MAIN_IP, HY2_AUTH_TOKEN"

CERT_PATH="/var/lib/marzban-node/ssl_client_cert.pem"
mkdir -p /var/lib/marzban-node

if [[ ! -s "$CERT_PATH" ]]; then
  echo
  warn "Вставьте содержимое сертификата ноды с главного сервера (/root/marzban_node_cert.pem)."
  warn "После вставки нажмите Enter, затем Ctrl+D:"
  cat > "$CERT_PATH"
fi

grep -q "BEGIN CERTIFICATE" "$CERT_PATH" || die "В файле $CERT_PATH нет корректного SSL-сертификата (BEGIN CERTIFICATE)"
chmod 600 "$CERT_PATH"

# ---------- 2. Проверка сети и DNS -------------------------------------------
log "Проверка сети и DNS ноды"
NODE_IP="$(curl -s4 https://api.ipify.org || true)"
[[ -n "$NODE_IP" ]] || die "Не удалось определить публичный IPv4 ноды"
RESOLVED_IP="$(getent ahostsv4 "$NODE_DOMAIN" | awk 'NR==1{print $1}' || true)"
if [[ "$RESOLVED_IP" != "$NODE_IP" ]]; then
  die "Домен $NODE_DOMAIN указывает на '${RESOLVED_IP:-ничего}', а IP ноды $NODE_IP. Исправьте A-запись."
fi
log "DNS OK: $NODE_DOMAIN -> $NODE_IP"

# ---------- 3. Пакеты и Docker -----------------------------------------------
log "Установка пакетов и Docker"
apt-get update -y
apt-get install -y curl wget socat ufw certbot git openssl jq

if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker

# ---------- 4. Firewall (UFW) ------------------------------------------------
log "Настройка ufw на ноде"
ufw allow 22/tcp    comment 'ssh'
ufw allow 80/tcp    comment 'http (certbot)'
ufw allow 443/tcp   comment 'xray reality (node)'
ufw allow 443/udp   comment 'hysteria2 (node)'
# Порты управления Marzban-node разрешаем ТОЛЬКО главному серверу!
ufw allow from "$MAIN_IP" to any port 62050 proto tcp comment 'marzban-node service'
ufw allow from "$MAIN_IP" to any port 62051 proto tcp comment 'marzban-node api'
ufw --force enable

# ---------- 5. Let's Encrypt --------------------------------------------------
log "Получение сертификата Let's Encrypt для $NODE_DOMAIN"
if [[ ! -f "/etc/letsencrypt/live/$NODE_DOMAIN/fullchain.pem" ]]; then
  certbot certonly --standalone -d "$NODE_DOMAIN" -m "$LE_EMAIL" \
    --agree-tos --non-interactive --keep-until-expiring
fi

mkdir -p /etc/letsencrypt/renewal-hooks/deploy
cat > /etc/letsencrypt/renewal-hooks/deploy/restart-node-vpn.sh <<'EOF'
#!/usr/bin/env bash
systemctl restart hysteria 2>/dev/null || true
EOF
chmod +x /etc/letsencrypt/renewal-hooks/deploy/restart-node-vpn.sh

# ---------- 6. Marzban-node ---------------------------------------------------
log "Установка и запуск Marzban-node"
mkdir -p /opt/marzban-node
cat > /opt/marzban-node/docker-compose.yml <<EOF
services:
  marzban-node:
    image: gozargah/marzban-node:latest
    restart: always
    network_mode: host
    environment:
      SSL_CLIENT_CERT_FILE: "/var/lib/marzban-node/ssl_client_cert.pem"
      SERVICE_PORT: "62050"
      XRAY_API_PORT: "62051"
      SERVICE_PROTOCOL: "rest"
    volumes:
      - /var/lib/marzban-node:/var/lib/marzban-node
EOF

cd /opt/marzban-node
docker compose up -d

# ---------- 7. Hysteria2 на ноде ----------------------------------------------
log "Установка Hysteria2 на ноде"
if [[ ! -x /usr/local/bin/hysteria ]]; then
  bash <(curl -fsSL https://get.hy2.sh/)
fi
systemctl disable --now hysteria-server.service 2>/dev/null || true

mkdir -p /etc/hysteria
cat > /etc/hysteria/config.yaml <<EOF
listen: :443

tls:
  cert: /etc/letsencrypt/live/${NODE_DOMAIN}/fullchain.pem
  key: /etc/letsencrypt/live/${NODE_DOMAIN}/privkey.pem

auth:
  type: http
  http:
    url: https://${MAIN_DOMAIN}:9443/hy2auth/${HY2_AUTH_TOKEN}

masquerade:
  type: proxy
  proxy:
    url: https://www.bing.com/
    rewriteHost: true
EOF

cat > /etc/systemd/system/hysteria.service <<'EOF'
[Unit]
Description=Hysteria2 Node server
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/local/bin/hysteria server -c /etc/hysteria/config.yaml
Restart=always
RestartSec=3
User=root

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now hysteria
sleep 3

# ---------- 8. Проверка и инструкция ------------------------------------------
log "Проверка сервисов ноды"
systemctl is-active --quiet hysteria && echo "hysteria (node): OK" || warn "hysteria не запущен: journalctl -u hysteria"
docker ps --format '{{.Names}}' | grep -q marzban-node && echo "marzban-node: OK" || warn "marzban-node не запущен"

log "Нода готова! Остался 1 шаг на ГЛАВНОМ сервере:"
echo "Выполните на ГЛАВНОМ сервере (Master) команду:"
echo
echo "  vpn-add-node ${NODE_IP} ${NODE_DOMAIN} \"${NODE_LABEL}\""
echo
echo "Эта команда откроет доступ ноде к hy2auth (порт 9443), подключит ноду к Marzban и добавит её в Telegram-бота."
