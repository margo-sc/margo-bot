#!/usr/bin/env bash
# Поменять одну настройку в /opt/margo-bot/.env и перезапустить бота.
# Пример:  bash /opt/margo-bot/deploy/setenv.sh BOT_TOKEN
#          bash /opt/margo-bot/deploy/setenv.sh ROBOKASSA_TEST
set -euo pipefail
KEY="${1:?Укажи название настройки, например BOT_TOKEN}"
ENV_FILE=/opt/margo-bot/.env
[ -f "$ENV_FILE" ] || { echo "Нет $ENV_FILE — сначала запусти install.sh"; exit 1; }
case "$KEY" in *PASS*|*TOKEN*) read -rsp "Новое значение $KEY: " VALUE; echo ;; *) read -rp "Новое значение $KEY: " VALUE ;; esac
KEY="$KEY" VALUE="$VALUE" ENV_FILE="$ENV_FILE" python3 - <<'PY'
import os, re
p, k, v = os.environ["ENV_FILE"], os.environ["KEY"], os.environ["VALUE"].strip()
t = open(p, encoding="utf-8").read()
if re.search(rf"^{re.escape(k)}=", t, flags=re.M):
    t = re.sub(rf"^{re.escape(k)}=.*$", lambda _: f"{k}={v}", t, flags=re.M)
else:
    t = t.rstrip("\n") + f"\n{k}={v}\n"
open(p, "w", encoding="utf-8").write(t)
PY
systemctl restart margo-bot
echo "Сохранено, бот перезапущен ✅"
