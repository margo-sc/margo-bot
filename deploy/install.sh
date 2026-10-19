#!/usr/bin/env bash
# Установка бота @margo_sale_bot на чистый сервер Ubuntu 22.04/24.04 (Timeweb Cloud).
# Запуск от root:
#   curl -fsSL https://raw.githubusercontent.com/margo-sc/margo-bot/main/deploy/install.sh | bash
# Повторный запуск безопасен: обновит код и перезапустит бота, .env не тронет.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/margo-sc/margo-bot.git}"
APP_DIR=/opt/margo-bot
APP_USER=margo

say() { printf '\n\033[1;35m▶ %s\033[0m\n' "$*"; }

[ "$(id -u)" -eq 0 ] || { echo "Запусти от root"; exit 1; }

say "Устанавливаю системные пакеты"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git curl sqlite3 >/dev/null
if ! command -v caddy >/dev/null; then
  apt-get install -y -qq caddy >/dev/null 2>&1 || {
    apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https gnupg >/dev/null
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -qq && apt-get install -y -qq caddy >/dev/null
  }
fi

say "Скачиваю код бота"
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
# папка принадлежит пользователю бота, а обновляем мы от root — разрешаем git работать с ней
git config --global --get-all safe.directory | grep -qx "$APP_DIR" || git config --global --add safe.directory "$APP_DIR"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" pull --ff-only
else
  git clone "$REPO_URL" "$APP_DIR"
fi
mkdir -p "$APP_DIR/data/files" "$APP_DIR/backups"

say "Ставлю библиотеки Python"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

if [ ! -f "$APP_DIR/.env" ]; then
  say "Настройка (ввод не отображается для паролей)"
  # вопросы читаем прямо с терминала: при запуске через «curl | bash» stdin занят самим скриптом
  read -rp  "Токен бота из @BotFather: " BOT_TOKEN </dev/tty
  read -rp  "Поддомен для оплаты [pay.rita-fitt.ru]: " DOMAIN </dev/tty; DOMAIN=${DOMAIN:-pay.rita-fitt.ru}
  read -rp  "Робокасса — идентификатор магазина: " RK_LOGIN </dev/tty
  read -rsp "Робокасса — Пароль #1: " RK_P1 </dev/tty; echo
  read -rsp "Робокасса — Пароль #2: " RK_P2 </dev/tty; echo
  read -rp  "Тестовый режим Робокассы? (1 — да, 0 — боевой) [1]: " RK_TEST </dev/tty; RK_TEST=${RK_TEST:-1}
  # не через «tr </dev/urandom | head»: с pipefail такой конвейер падает (SIGPIPE) и скрипт молча завершается
  CLAIM_CODE=$(python3 -c "import secrets,string; print(''.join(secrets.choice(string.ascii_lowercase+string.digits) for _ in range(8)))")
  export BOT_TOKEN DOMAIN RK_LOGIN RK_P1 RK_P2 RK_TEST CLAIM_CODE APP_DIR
  python3 - <<'PY'
import os, re
p = os.environ["APP_DIR"] + "/.env"
text = open(os.environ["APP_DIR"] + "/env.example", encoding="utf-8").read()
vals = {
    "BOT_TOKEN": os.environ["BOT_TOKEN"].strip(),
    "PUBLIC_URL": "https://" + os.environ["DOMAIN"].strip(),
    "ROBOKASSA_LOGIN": os.environ["RK_LOGIN"].strip(),
    "ROBOKASSA_PASS1": os.environ["RK_P1"].strip(),
    "ROBOKASSA_PASS2": os.environ["RK_P2"].strip(),
    "ROBOKASSA_TEST": os.environ["RK_TEST"].strip(),
    "CLAIM_CODE": os.environ["CLAIM_CODE"],
}
for k, v in vals.items():
    if re.search(rf"^{k}=", text, flags=re.M):
        text = re.sub(rf"^{k}=.*$", lambda _: f"{k}={v}", text, flags=re.M)
    else:
        text += f"\n{k}={v}\n"
open(p, "w", encoding="utf-8").write(text)
PY
  chmod 600 "$APP_DIR/.env"
  echo
  echo "Чтобы бот узнал тебя как владельца, после установки напиши ему в Telegram:"
  echo "    /claim ${CLAIM_CODE}"
fi
DOMAIN=$(grep -E '^PUBLIC_URL=' "$APP_DIR/.env" | sed -E 's|^PUBLIC_URL=https?://||; s|/.*||')
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

say "Настраиваю HTTPS для ${DOMAIN}"
cat > /etc/caddy/Caddyfile <<EOF
${DOMAIN} {
    encode gzip
    reverse_proxy 127.0.0.1:8080
}
EOF
systemctl enable --now caddy >/dev/null
systemctl reload caddy || systemctl restart caddy

say "Запускаю бота как службу"
install -m 644 "$APP_DIR/deploy/margo-bot.service" /etc/systemd/system/margo-bot.service
cat > /etc/cron.daily/margo-bot-backup <<EOF
#!/bin/sh
sqlite3 $APP_DIR/data/bot.sqlite3 ".backup '$APP_DIR/backups/bot-\$(date +%F).sqlite3'"
find $APP_DIR/backups -name 'bot-*.sqlite3' -mtime +30 -delete
EOF
chmod +x /etc/cron.daily/margo-bot-backup
if command -v ufw >/dev/null && ufw status | grep -q active; then ufw allow 80,443/tcp >/dev/null; fi
systemctl daemon-reload
systemctl enable margo-bot >/dev/null
systemctl restart margo-bot
sleep 4

say "Проверка"
systemctl --no-pager --lines=15 status margo-bot || true
echo
echo "Готово ✅"
echo "Бот: systemctl status margo-bot | журнал: journalctl -u margo-bot -f"
echo "Тексты: nano $APP_DIR/content.yaml, затем /reload в боте"
echo "Адрес для Робокассы (Result URL): https://${DOMAIN}/robokassa/result  (метод POST)"
