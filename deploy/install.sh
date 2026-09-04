#!/usr/bin/env bash
# Скрипт первичной установки бота на чистый Ubuntu/Debian VPS.
# Запускать от root (или через sudo).
set -euo pipefail

APP_DIR="/opt/asvet-antispam"
APP_USER="asvet"

if [ "$(id -u)" -ne 0 ]; then
  echo "❌ Запусти от root: sudo bash deploy/install.sh"
  exit 1
fi

echo "==> Создаю пользователя $APP_USER (если нет)"
if ! id -u "$APP_USER" >/dev/null 2>&1; then
  useradd --system --home "$APP_DIR" --shell /bin/bash "$APP_USER"
fi

echo "==> Копирую файлы в $APP_DIR"
mkdir -p "$APP_DIR"
cp -r . "$APP_DIR/"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

echo "==> Проверяю uv"
if ! command -v uv >/dev/null 2>&1; then
  echo "    uv не найден. Устанавливаю…"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # обычно ставит в ~/.local/bin, у рута — в /root/.local/bin
  export PATH="$HOME/.local/bin:$PATH"
fi

echo "==> Подтягиваю зависимости через uv"
cd "$APP_DIR"
sudo -u "$APP_USER" uv sync --no-dev

echo "==> Создаю data/ и logs/"
mkdir -p "$APP_DIR/data" "$APP_DIR/logs"
chown -R "$APP_USER:$APP_USER" "$APP_DIR/data" "$APP_DIR/logs"

if [ ! -f "$APP_DIR/.env" ]; then
  echo ""
  echo "⚠️  Файл .env не найден!"
  echo "    Скопируй .env.example в .env, заполни BOT_TOKEN/CHAT_ID/ADMIN_IDS,"
  echo "    затем снова запусти этот скрипт или выполни вручную:"
  echo ""
  echo "      sudo cp $APP_DIR/.env.example $APP_DIR/.env"
  echo "      sudo nano $APP_DIR/.env"
  echo "      sudo chown $APP_USER:$APP_USER $APP_DIR/.env"
  echo "      sudo chmod 600 $APP_DIR/.env"
  echo ""
  exit 0
fi
chmod 600 "$APP_DIR/.env"
chown "$APP_USER:$APP_USER" "$APP_DIR/.env"

echo "==> Ставлю systemd-юнит"
cp "$APP_DIR/deploy/asvet-antispam.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable asvet-antispam.service
systemctl restart asvet-antispam.service

echo ""
echo "✅ Готово. Проверить статус:  sudo systemctl status asvet-antispam"
echo "   Логи:                       sudo journalctl -u asvet-antispam -f"
echo "   Перезапустить:              sudo systemctl restart asvet-antispam"
