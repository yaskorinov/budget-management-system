#!/usr/bin/env bash
# Установка на чистый Debian 12/13. Запускать от root: bash deploy/install.sh
set -euo pipefail

APP_DIR=/opt/budget-bot
APP_USER=budget
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> Пакеты"
apt-get update
apt-get install -y python3 python3-venv python3-pip nginx certbot python3-certbot-nginx \
    fonts-dejavu-core fonts-noto-color-emoji

echo "==> Пользователь и каталог"
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin "$APP_USER"
mkdir -p "$APP_DIR"
if [ "$REPO_DIR" != "$APP_DIR" ]; then
    cp -r "$REPO_DIR"/. "$APP_DIR"/
fi
mkdir -p "$APP_DIR/data"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

echo "==> Виртуальное окружение"
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "==> Конфигурация"
if [ ! -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    sed -i "s|^SECRET_KEY=.*|SECRET_KEY=$(openssl rand -hex 32)|" "$APP_DIR/.env"
    sed -i "s|^WEBHOOK_SECRET=.*|WEBHOOK_SECRET=$(openssl rand -hex 16)|" "$APP_DIR/.env"
    chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
    chmod 600 "$APP_DIR/.env"
    echo "    Создан $APP_DIR/.env — впишите BOT_TOKEN, LLM_API_KEY и PUBLIC_BASE_URL."
fi

echo "==> systemd"
cp "$APP_DIR/deploy/budget-bot.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable budget-bot

cat <<'HINT'

Готово. Осталось:
  1. nano /opt/budget-bot/.env            — BOT_TOKEN, LLM_API_KEY, PUBLIC_BASE_URL
  2. cp /opt/budget-bot/deploy/nginx.conf.example /etc/nginx/sites-available/budget-bot
     и поправить server_name
  3. ln -s /etc/nginx/sites-available/budget-bot /etc/nginx/sites-enabled/
  4. certbot --nginx -d budget.example.com
  5. systemctl start budget-bot && journalctl -u budget-bot -f

HINT
