# Бот @margo_sale_bot

Продающий Telegram-бот Маргариты Тютюнник (@senorita_fitness): каталог продуктов, оплата через Робокассу (картой или Яндекс Сплит) с автоматической выдачей, бесплатные гайды, анкеты, прогрев и дожим, уведомления владельцу.

## Что где лежит

| Файл | Что это |
| --- | --- |
| `content.yaml` | **Все тексты, цены, кнопки, анкеты и цепочки.** Править нужно только его |
| `.env` | Секреты: токен бота, пароли Робокассы (на сервере, в GitHub не попадает) |
| `bot/` | Код |
| `deploy/install.sh` | Установка и обновление на сервере |
| `tests/` | Автотесты (`python -m pytest`) |

## Установка на сервер (Timeweb Cloud, Ubuntu 24.04)

1. Создать сервер (самый простой тариф), открыть консоль.
2. В DNS домена rita-fitt.ru добавить A-запись `pay` → IP сервера.
3. Выполнить в консоли сервера:

   ```
   curl -fsSL https://raw.githubusercontent.com/margo-sc/margo-bot/main/deploy/install.sh | bash
   ```

   Скрипт спросит токен бота и данные Робокассы, в конце покажет код для `/claim`.
4. Написать боту `/claim <код>` — бот запомнит владельца.
5. В Робокассе → Технические настройки:
   - Result URL: `https://pay.rita-fitt.ru/robokassa/result`, метод POST
   - Success URL: `https://t.me/margo_sale_bot`, метод GET
   - Fail URL: `https://t.me/margo_sale_bot`, метод GET

## Как поменять текст или цену

```
nano /opt/margo-bot/content.yaml
```

Сохранить (Ctrl+O, Enter, Ctrl+X), затем написать боту `/reload`. Бот проверит файл и скажет, если что-то не так.

## Как загрузить PDF продукта

Отправить боту файл с подписью `/file kniga.pdf` (имя — как в `content.yaml`). Проверить, что всё загружено: `/files`.

## Обновить код

```
curl -fsSL https://raw.githubusercontent.com/margo-sc/margo-bot/main/deploy/install.sh | bash
```

## Полезное

- Журнал: `journalctl -u margo-bot -f`
- Перезапуск: `systemctl restart margo-bot`
- Резервные копии базы: `/opt/margo-bot/backups` (каждый день, хранятся 30 дней)
