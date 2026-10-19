"""Обработчики Telegram: клиенты и команды для Маргариты."""
from __future__ import annotations

import asyncio
import html
import time

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (CallbackQuery, ChatJoinRequest, ChatMemberUpdated, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)

from .core import App

ADMIN_HELP = """<b>Команды для тебя</b>

/stats — статистика за всё время, /stats 7 — за 7 дней
/user <i>id</i> — карточка клиента (ярлыки, источник)
/schet <i>id</i> <i>продукт</i> [<i>сумма</i>] — выставить счёт (например, ведение: /schet 123456 vedenie 10190)
/vydat <i>id</i> <i>продукт</i> — выдать продукт вручную (если оплатили не через бота)
/chain <i>id</i> <i>цепочка</i> — запустить цепочку (например, after_konsult после встречи)
/rassylka [<i>ярлык</i>] — ответь этой командой на сообщение, и бот разошлёт его всем (или только с ярлыком)
/files — какие файлы продуктов загружены
/gruppy — закрытые группы продуктов: привязаны ли и хватает ли прав у бота
/dostup [<i>продукт</i>] — у кого сейчас открыт доступ и до какого числа
Загрузить файл: отправь боту PDF с подписью <code>/file kniga.pdf</code>
/reload — перечитать content.yaml после правок
/id — узнать id чата

Продукты: {products}
Цепочки: {chains}

Чтобы ответить клиенту, просто ответь (reply) на уведомление о нём."""


def build_router(app: App) -> Router:
    r = Router()
    pending_broadcast: dict[int, tuple[int, int, str | None]] = {}

    def admin(m: Message | CallbackQuery) -> bool:
        return app.is_admin(m.from_user.id)

    # ---------------------------------------------------------------- старт
    @r.message(CommandStart())
    async def start(m: Message, command: CommandObject):
        uid = m.from_user.id
        app.anketa.pop(uid, None)
        code = (command.args or "").strip()
        link = app.content.deeplinks.get(code) if code else None
        source = (link or {}).get("source") or (code or "direct")
        await app.db.touch_user(uid, m.from_user.username, m.from_user.first_name, source)
        if code:
            await app.db.add_tag(uid, f"src_{code}"[:64])
        await app.do(uid, (link or {}).get("open", "screen:start"))

    @r.message(Command("claim"))
    async def claim(m: Message, command: CommandObject):
        code = (command.args or "").strip()
        if not app.cfg.claim_code or code != app.cfg.claim_code:
            return await app.show_screen(m.from_user.id, "start")
        uid = m.from_user.id
        if uid not in app.cfg.admin_ids:
            app.cfg.admin_ids.append(uid)
            app.cfg.data_dir.mkdir(parents=True, exist_ok=True)
            with open(app.cfg.data_dir / "admins.txt", "a", encoding="utf-8") as f:
                f.write(f"{uid}\n")
        if not app.cfg.alert_chat_id:
            app.cfg.alert_chat_id = uid
        await m.answer("Готово, теперь ты владелец бота 🤍 Сюда будут приходить оплаты, анкеты и вопросы клиентов. "
                       "Список команд — /help")

    @r.message(Command("id"))
    async def my_id(m: Message):
        await m.answer(f"id этого чата: <code>{m.chat.id}</code>", parse_mode="HTML")

    @r.message(Command("menu"))
    async def menu(m: Message):
        await app.db.touch_user(m.from_user.id, m.from_user.username, m.from_user.first_name, None)
        await app.show_screen(m.from_user.id, "start")

    # ---------------------------------------------------------------- кнопки
    @r.callback_query(F.data.startswith("a:"))
    async def on_action(c: CallbackQuery):
        await c.answer()
        await app.db.touch_user(c.from_user.id, c.from_user.username, c.from_user.first_name, None)
        await app.do(c.from_user.id, c.data[2:])

    @r.callback_query(F.data.startswith("consent:"))
    async def on_consent(c: CallbackQuery):
        await c.answer()
        _, key, yes = c.data.split(":", 2)
        try:
            await c.message.edit_reply_markup(reply_markup=None)
        except Exception:  # noqa: BLE001
            pass
        if yes == "1":
            await app.begin_anketa(c.from_user.id, key)
        else:
            await app.show_screen(c.from_user.id, "start")

    @r.callback_query(F.data.startswith("ans:"))
    async def on_anketa_button(c: CallbackQuery):
        await c.answer()
        st = app.anketa.get(c.from_user.id)
        _, idx, choice = c.data.split(":", 2)
        if not st or int(idx) != st.idx:
            return
        q = app.content.anketas[st.key]["questions"][st.idx]
        value = q["buttons"][int(choice)]
        try:
            await c.message.edit_text(f"{q['text']}\n\n<b>{html.escape(value)}</b>", parse_mode="HTML")
        except Exception:  # noqa: BLE001
            pass
        await app.answer_question(c.from_user.id, value)

    # ---------------------------------------------------------------- команды Маргариты
    @r.message(Command("help", "admin"), admin)
    async def admin_help(m: Message):
        await m.answer(
            ADMIN_HELP.format(products=", ".join(app.content.products), chains=", ".join(app.content.chains)),
            parse_mode="HTML",
        )

    @r.message(Command("stats"), admin)
    async def stats(m: Message, command: CommandObject):
        days = int(command.args) if (command.args or "").strip().isdigit() else 0
        since = int(time.time()) - days * 86400 if days else 0
        s = await app.db.stats(since)
        period = f"за {days} дн." if days else "за всё время"
        names = {k: v.get("name", k) for k, v in app.content.products.items()}
        total = sum(x[2] for x in s["paid"])
        lines = [f"<b>Статистика {period}</b>",
                 f"Подписчиков: {s['users']} (заблокировали бота: {s['blocked']})",
                 f"Открывали оплату: {s['opened_pay']}",
                 f"Выручка: <b>{int(total):,} ₽</b>".replace(",", " "),
                 "", "<b>Источники подписчиков</b>"]
        lines += [f"{html.escape(str(src))}: {n}" for src, n in s["sources"]] or ["—"]
        lines += ["", "<b>Продажи по продуктам</b>"]
        lines += [f"{html.escape(names.get(p, p))}: {n} шт., {int(a):,} ₽".replace(",", " ") for p, n, a in s["paid"]] or ["—"]
        lines += ["", "<b>Продажи по источникам</b>"]
        lines += [f"{html.escape(str(src))}: {n} шт., {int(a):,} ₽".replace(",", " ") for src, n, a in s["paid_by_source"]] or ["—"]
        await m.answer("\n".join(lines), parse_mode="HTML")

    @r.message(Command("user"), admin)
    async def user_card(m: Message, command: CommandObject):
        arg = (command.args or "").strip()
        if not arg.isdigit():
            return await m.answer("Формат: /user 123456789")
        u = await app.db.get_user(int(arg))
        if not u:
            return await m.answer("Такого клиента нет в базе.")
        tags = await app.db.tags_of(u["id"])
        uname = f"@{u['username']}" if u["username"] else "—"
        await m.answer(
            f"<b>{html.escape(u['first_name'] or '')}</b> {uname}\nid <code>{u['id']}</code>\n"
            f"Источник: {html.escape(u['source'] or '—')}\nЗаблокировал бота: {'да' if u['blocked'] else 'нет'}\n"
            f"Ярлыки: {html.escape(', '.join(tags) or '—')}",
            parse_mode="HTML",
        )

    @r.message(Command("schet"), admin)
    async def invoice(m: Message, command: CommandObject):
        parts = (command.args or "").split()
        if len(parts) < 2 or not parts[0].isdigit() or parts[1] not in app.content.products:
            return await m.answer("Формат: /schet <id> <продукт> [сумма]\nПродукты: " + ", ".join(app.content.products))
        uid, key = int(parts[0]), parts[1]
        amount = float(parts[2]) if len(parts) > 2 and parts[2].replace(".", "", 1).isdigit() else float(
            app.content.products[key]["price"])
        url = await app.start_payment(uid, key, amount=amount, note="счёт вручную")
        await m.answer(f"Счёт на {int(amount)} ₽ отправлен клиенту." if url else "Не получилось выставить счёт.")

    @r.message(Command("vydat"), admin)
    async def give(m: Message, command: CommandObject):
        parts = (command.args or "").split()
        if len(parts) != 2 or not parts[0].isdigit() or parts[1] not in app.content.products:
            return await m.answer("Формат: /vydat <id> <продукт>")
        uid, key = int(parts[0]), parts[1]
        order = await app.db.create_order(uid, key, 0, note="выдано вручную")
        await app.db.mark_paid(order["id"])
        await app.on_paid(await app.db.order_by_id(order["id"]))
        await m.answer("Готово, продукт выдан.")

    @r.message(Command("chain"), admin)
    async def chain(m: Message, command: CommandObject):
        parts = (command.args or "").split()
        if len(parts) != 2 or not parts[0].isdigit() or parts[1] not in app.content.chains:
            return await m.answer("Формат: /chain <id> <цепочка>\nЦепочки: " + ", ".join(app.content.chains))
        await app.start_chain(int(parts[0]), parts[1], group_key=f"chain:{parts[1]}", params={})
        await m.answer("Цепочка запущена.")

    @r.message(Command("reload"), admin)
    async def reload(m: Message):
        try:
            problems = app.content.reload()
        except Exception as e:  # noqa: BLE001
            return await m.answer(f"Ошибка в content.yaml, тексты не обновлены:\n<code>{html.escape(str(e))}</code>",
                                  parse_mode="HTML")
        if problems:
            await m.answer("Тексты обновлены, но есть проблемы:\n" + "\n".join(html.escape(p) for p in problems))
        else:
            await m.answer("Тексты обновлены ✅")

    @r.message(Command("rassylka"), admin)
    async def broadcast(m: Message, command: CommandObject):
        if not m.reply_to_message:
            return await m.answer("Ответь командой /rassylka на сообщение, которое нужно разослать.")
        tag = (command.args or "").strip() or None
        ids = await app.db.user_ids(tag)
        pending_broadcast[m.from_user.id] = (m.chat.id, m.reply_to_message.message_id, tag)
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=f"Отправить {len(ids)} людям", callback_data="bc:go"),
            InlineKeyboardButton(text="Отмена", callback_data="bc:no"),
        ]])
        await m.answer(f"Рассылка {'по ярлыку ' + tag if tag else 'всем'}: {len(ids)} получателей. Отправляем?",
                       reply_markup=kb)

    @r.callback_query(F.data.startswith("bc:"), admin)
    async def broadcast_go(c: CallbackQuery):
        await c.answer()
        job = pending_broadcast.pop(c.from_user.id, None)
        await c.message.edit_reply_markup(reply_markup=None)
        if c.data == "bc:no" or not job:
            return await c.message.answer("Рассылка отменена.")
        chat_id, msg_id, tag = job
        ids = await app.db.user_ids(tag)
        await c.message.answer(f"Начинаю рассылку на {len(ids)}…")

        async def run():
            ok = 0
            for uid in ids:
                try:
                    await app.bot.copy_message(uid, chat_id, msg_id)
                    ok += 1
                except Exception as e:  # noqa: BLE001
                    if "blocked" in str(e).lower() or "deactivated" in str(e).lower():
                        await app.db.set_blocked(uid)
                await asyncio.sleep(0.05)  # не больше ~20 сообщений в секунду
            await app.bot.send_message(c.message.chat.id, f"Рассылка завершена: доставлено {ok} из {len(ids)}.")

        asyncio.create_task(run())

    # ---------------------------------------------------------------- файлы продуктов через Telegram
    @r.message(F.document, F.caption.startswith("/file"), admin)
    async def upload_file(m: Message):
        parts = (m.caption or "").split()
        if len(parts) != 2:
            return await m.answer("Подпись к файлу: /file имя.pdf (имя как в content.yaml)")
        await app.db.save_file_id(parts[1], m.document.file_id)
        await m.answer(f"Файл <code>{html.escape(parts[1])}</code> сохранён ✅", parse_mode="HTML")

    @r.message(Command("files"), admin)
    async def files_status(m: Message):
        needed: list[tuple[str, str]] = []
        for key, p in app.content.products.items():
            for f in (p.get("delivery") or {}).get("files") or []:
                needed.append((f, p.get("name", key)))
        for key, lead in app.content.leads.items():
            if lead.get("file"):
                needed.append((lead["file"], lead.get("name", key)))
        lines = ["<b>Файлы для выдачи</b>"]
        for name, owner in needed:
            ok = bool(await app.db.file_id(name)) or (app.cfg.files_dir / name).exists()
            lines.append(f"{'✅' if ok else '❌'} <code>{html.escape(name)}</code> — {html.escape(owner)}")
        lines.append("\nЧтобы загрузить: отправь боту PDF с подписью <code>/file имя.pdf</code>")
        await m.answer("\n".join(lines), parse_mode="HTML")

    # ---------------------------------------------------------------- закрытые группы продуктов
    def group_buttons(chat_id: int) -> InlineKeyboardMarkup | None:
        rows = []
        for key, g in app.access_groups().items():
            names = " + ".join(app.content.products[p].get("name", p) for p in g["products"])
            rows.append([InlineKeyboardButton(text=names[:60], callback_data=f"grp:{key}:{chat_id}")])
        rows.append([InlineKeyboardButton(text="Это не группа продукта", callback_data=f"grp:-:{chat_id}")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    @r.my_chat_member()
    async def bot_status_changed(e: ChatMemberUpdated):
        if e.chat.type == "private":
            return
        new = e.new_chat_member.status
        title = html.escape(e.chat.title or str(e.chat.id))
        if new == "administrator":
            keys = await app.db.group_keys_for_chat(e.chat.id)
            if keys:  # уже привязана — просто перепроверим права
                for k in keys:
                    await app.alert(await app.bind_group(k, e.chat.id, e.chat.title or str(e.chat.id)))
                return
            if not (e.from_user and app.is_admin(e.from_user.id)):
                return  # спрашиваем только тогда, когда бота добавила сама Маргарита
            await app.alert(f"Меня сделали администратором в «{title}». Для какого продукта эта группа? 👇",
                            markup=group_buttons(e.chat.id))
        elif new in ("left", "kicked") and await app.db.group_keys_for_chat(e.chat.id):
            await app.alert(f"⚠️ Меня убрали из «{title}» — выдавать доступ туда я больше не смогу. "
                            f"Верни меня администратором, если это случайно.")

    @r.callback_query(F.data.startswith("grp:"), admin)
    async def group_pick(c: CallbackQuery):
        await c.answer()
        _, key, chat = c.data.split(":", 2)
        try:
            await c.message.edit_reply_markup(reply_markup=None)
        except Exception:  # noqa: BLE001
            pass
        if key == "-":
            return await c.message.answer("Хорошо, эту группу не трогаю.")
        try:
            title = (await app.bot.get_chat(int(chat))).title or chat
        except Exception:  # noqa: BLE001
            title = chat
        await c.message.answer(await app.bind_group(key, int(chat), title), parse_mode="HTML")

    async def bind_here(m: Message, command: CommandObject):
        key = (command.args or "").strip()
        report = await app.bind_group(key, m.chat.id, m.chat.title or str(m.chat.id))
        await app.alert(report)
        try:
            await m.delete()
        except Exception:  # noqa: BLE001
            pass

    @r.message(Command("bind"), F.chat.type.in_({"group", "supergroup"}), admin)
    async def bind_in_group(m: Message, command: CommandObject):
        await bind_here(m, command)

    @r.channel_post(Command("bind"))
    async def bind_in_channel(m: Message, command: CommandObject):
        await bind_here(m, command)  # писать в канал могут только его администраторы

    @r.message(Command("gruppy", "groups"), admin)
    async def groups_status(m: Message):
        lines = ["<b>Закрытые группы продуктов</b>"]
        for key, g in app.access_groups().items():
            names = ", ".join(app.content.products[p].get("name", p) for p in g["products"])
            row = await app.db.group(key)
            if not row:
                lines.append(f"\n❌ <b>{html.escape(names)}</b> — группа не привязана. "
                             f"Убери бота из администраторов группы и добавь снова — я спрошу, что это за группа.")
                continue
            missing = await app.bot_rights(row["chat_id"], g["timed"])
            mark = "✅" if not missing else "⚠️"
            lines.append(f"\n{mark} <b>{html.escape(names)}</b> → «{html.escape(row['title'] or '')}»")
            if missing:
                lines.append("Не хватает прав: " + "; ".join(html.escape(x) for x in missing))
        await m.answer("\n".join(lines), parse_mode="HTML")

    @r.message(Command("dostup"), admin)
    async def access_status(m: Message, command: CommandObject):
        key = (command.args or "").strip() or None
        rows = await app.db.access_list(key)
        if not rows:
            return await m.answer("Активных доступов нет.")
        names = {k: v.get("name", k) for k, v in app.content.products.items()}
        lines = [f"<b>Открытые доступы: {len(rows)}</b>"]
        for a in rows[:100]:
            u = await app.db.get_user(a["user_id"])
            who = html.escape((u["first_name"] or "") if u else "")
            lines.append(f"{html.escape(names.get(a['product'], a['product']))}: {who} "
                         f"<code>{a['user_id']}</code> — до {app.fmt_date(a['expires_at'])}")
        await m.answer("\n".join(lines), parse_mode="HTML")

    @r.chat_join_request()
    async def join_request(req: ChatJoinRequest):
        await app.on_join_request(req)

    # ---------------------------------------------------------------- ответ клиенту через reply
    @r.message(F.reply_to_message, admin)
    async def admin_reply(m: Message):
        uid = await app.db.relay_user(m.chat.id, m.reply_to_message.message_id)
        if not uid:
            return
        try:
            await app.bot.copy_message(uid, m.chat.id, m.message_id)
            await m.reply("Отправлено ✅")
        except Exception as e:  # noqa: BLE001
            await m.reply(f"Не доставлено: {html.escape(str(e))}")

    # ---------------------------------------------------------------- сообщения клиентов
    @r.message(F.chat.type == "private")
    async def user_message(m: Message):
        uid = m.from_user.id
        await app.db.touch_user(uid, m.from_user.username, m.from_user.first_name, None)
        if uid in app.anketa:
            if m.text:
                return await app.answer_question(uid, m.text)
            return await m.answer("Ответь, пожалуйста, текстом 🙂")
        if app.is_admin(uid) and m.chat.id == app.cfg.alert_chat_id:
            return  # твои обычные сообщения боту никуда не пересылаются
        # всё остальное — вопрос Маргарите
        await app.alert("✉️ <b>Сообщение от клиента</b>", about_uid=uid)
        try:
            copy = await app.bot.copy_message(app.cfg.alert_chat_id, m.chat.id, m.message_id)
            await app.db.save_relay(app.cfg.alert_chat_id, copy.message_id, uid)
        except Exception:  # noqa: BLE001
            pass
        await m.answer(app.content.sys("help_sent") or "Получила, отвечу лично 🤍")

    return r
