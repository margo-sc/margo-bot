"""Логика бота: экраны, оплата, выдача, гайды, анкеты, цепочки, уведомления."""
from __future__ import annotations

import asyncio
import datetime as dt
import html
import json
import logging
import time
from dataclasses import dataclass, field

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import ChatJoinRequest, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from .config import Config
from .content import Content, parse_delay, render
from .db import DB, now

log = logging.getLogger("margo.core")


@dataclass
class AnketaState:
    key: str
    idx: int = 0
    answers: dict = field(default_factory=dict)
    order_id: int | None = None


class App:
    def __init__(self, bot: Bot, db: DB, content: Content, cfg: Config):
        self.bot = bot
        self.db = db
        self.content = content
        self.cfg = cfg
        self.anketa: dict[int, AnketaState] = {}

    # ------------------------------------------------------------------ utils
    def is_admin(self, uid: int) -> bool:
        return uid in self.cfg.admin_ids

    async def _user_vars(self, uid: int) -> dict:
        u = await self.db.get_user(uid)
        name = (u["first_name"] if u and u["first_name"] else "").strip()
        return {"first_name": html.escape(name) if name else "привет"}

    def keyboard(self, buttons, vars: dict | None = None) -> InlineKeyboardMarkup | None:
        rows = []
        for row in buttons or []:
            label, action = str(row[0]), render(str(row[1]), **(vars or {}))
            if action.startswith("url:"):
                rows.append([InlineKeyboardButton(text=label, url=action[4:])])
            else:
                data = f"a:{action}"
                if len(data.encode()) > 64:
                    log.warning("callback слишком длинный, пропущен: %s", data)
                    continue
                rows.append([InlineKeyboardButton(text=label, callback_data=data)])
        return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None

    async def send(self, uid: int, text: str, markup=None, **kw) -> bool:
        """Отправка с обработкой блокировок и лимитов. True — доставлено."""
        for _ in range(3):
            try:
                await self.bot.send_message(
                    uid, text, reply_markup=markup, parse_mode="HTML", disable_web_page_preview=True, **kw
                )
                return True
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except TelegramForbiddenError:
                await self.db.set_blocked(uid)
                return False
            except TelegramBadRequest as e:
                log.warning("send to %s failed: %s", uid, e)
                return False
        return False

    async def send_file(self, uid: int, name: str, protect: bool = True) -> bool:
        cached = await self.db.file_id(name)
        try:
            if cached:
                await self.bot.send_document(uid, cached, protect_content=protect)
                return True
            path = self.cfg.files_dir / name
            if not path.exists():
                return False
            msg = await self.bot.send_document(uid, FSInputFile(path), protect_content=protect)
            if msg.document:
                await self.db.save_file_id(name, msg.document.file_id)
            return True
        except TelegramForbiddenError:
            await self.db.set_blocked(uid)
            return False
        except TelegramBadRequest as e:
            log.warning("send_file %s to %s failed: %s", name, uid, e)
            if cached:  # file_id устарел — пробуем загрузить заново
                await self.db.save_file_id(name, "")
            return False

    async def alert(self, text: str, about_uid: int | None = None, markup=None) -> None:
        """Уведомление Маргарите. Если указан клиент — ответ на это сообщение уйдёт ему."""
        chat = self.cfg.alert_chat_id
        if not chat:
            log.warning("ALERT_CHAT_ID не задан: %s", text)
            return
        if about_uid:
            u = await self.db.get_user(about_uid)
            who = html.escape((u["first_name"] or "") if u else "")
            uname = f" @{u['username']}" if u and u["username"] else ""
            text = f"{text}\n\n👤 {who}{uname} · id <code>{about_uid}</code>\n<i>Ответь на это сообщение — ответ уйдёт клиенту.</i>"
        try:
            msg = await self.bot.send_message(chat, text, parse_mode="HTML", reply_markup=markup,
                                              disable_web_page_preview=True)
            if about_uid:
                await self.db.save_relay(chat, msg.message_id, about_uid)
        except Exception as e:  # noqa: BLE001 — уведомление не должно ронять сценарий
            log.error("alert failed: %s", e)

    # ------------------------------------------------------------------ router
    async def do(self, uid: int, action: str) -> None:
        kind, _, arg = action.partition(":")
        if kind == "screen":
            await self.show_screen(uid, arg)
        elif kind == "product":
            await self.show_product(uid, arg)
        elif kind == "pay":
            await self.start_payment(uid, arg)
        elif kind == "lead":
            await self.give_lead(uid, arg)
        elif kind == "anketa":
            await self.ask_consent(uid, arg)
        elif kind == "help":
            await self.send(uid, self.content.screens.get("help", {}).get("text", "Напиши вопрос сюда 🤍"))
            await self.db.add_tag(uid, "asked_help")
        elif kind == "answer":
            q, _, value = arg.partition(":")
            await self.db.add_tag(uid, f"ans_{q}_{value}"[:64])
            await self.send(uid, self.content.sys("answer_thanks") or "Спасибо 🤍")
        else:
            await self.show_screen(uid, "start")

    # ------------------------------------------------------------------ экраны
    async def show_screen(self, uid: int, name: str) -> None:
        s = self.content.screens.get(name) or self.content.screens.get("start", {})
        v = await self._user_vars(uid)
        await self.send(uid, render(s.get("text", ""), **v), self.keyboard(s.get("buttons"), v))

    async def show_product(self, uid: int, key: str) -> None:
        p = self.content.products.get(key)
        if not p:
            return await self.show_screen(uid, "start")
        await self.db.add_tag(uid, f"view_{key}")
        if p.get("payable", True):
            buttons = [["💳 Оплатить", f"pay:{key}"]]
        else:
            buttons = [["Заполнить анкету", f"anketa:{key}"]]
        buttons += [[self.content.sys("help_button") or "Задать вопрос", "help"],
                    [self.content.sys("back_button") or "← В начало", "screen:start"]]
        v = {**await self._user_vars(uid), **self.content.product_vars(key)}
        await self.send(uid, render(p.get("card", ""), **v), self.keyboard(buttons, v))

    # ------------------------------------------------------------------ оплата
    async def start_payment(self, uid: int, key: str, amount: float | None = None, note: str = "") -> str | None:
        p = self.content.products.get(key)
        if not p:
            return None
        if not p.get("payable", True) and amount is None:
            # ведение оплачивается только по счёту от Маргариты
            await self.ask_consent(uid, key)
            return None
        price = float(amount if amount is not None else p["price"])
        order = await self.db.create_order(uid, key, price, note)
        url = f"{self.cfg.public_url}/pay/{order['token']}"
        v = {**self.content.product_vars(key), "price": f"{int(price):,}".replace(",", " ")}
        markup = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=self.content.sys("pay_button") or "Оплатить", url=url)]]
        )
        await self.send(uid, render(self.content.sys("pay_message"), **v), markup)
        await self.db.add_tag(uid, f"pay_open_{key}")
        if amount is None:  # дожим только для обычной покупки, не для счёта, выставленного вручную
            await self.start_chain(uid, "dozhim", group_key=f"dozhim:{key}", params={"product": key})
        return url

    async def on_paid(self, order) -> None:
        """Вызывается после проверенного уведомления Робокассы (или ручной выдачи)."""
        uid, key = order["user_id"], order["product"]
        p = self.content.products.get(key, {})
        await self.db.add_tag(uid, f"paid_{key}")
        await self.db.add_tag(uid, "buyer")
        await self.db.cancel(uid, group_key=f"dozhim:{key}")
        for name, chain in self.content.chains.items():
            if chain.get("stop_on_purchase"):
                await self.db.cancel(uid, prefix=f"chain:{name}")
        delivered = await self.deliver(uid, key)
        status = "✅ выдано автоматически" if delivered else "⚠️ <b>выдай вручную</b> (нет файлов/ссылок)"
        if p.get("delivery", {}).get("anketa"):
            status = "📝 клиент заполняет анкету"
        await self.alert(
            f"💰 <b>Оплата</b>: {html.escape(p.get('name', key))} — {int(order['amount'])} ₽ (заказ {order['id']})\n{status}",
            about_uid=uid,
        )
        if p.get("after_chain"):
            await self.start_chain(uid, p["after_chain"], group_key=f"after:{key}", params={"product": key})

    async def deliver(self, uid: int, key: str) -> bool:
        p = self.content.products.get(key, {})
        d = p.get("delivery", {}) or {}
        v = {**await self._user_vars(uid), **self.content.product_vars(key)}
        if d.get("text"):
            await self.send(uid, render(d["text"], **v))
        sent_any = False
        for name in d.get("files") or []:
            if await self.send_file(uid, name):
                sent_any = True
        links = [l for l in (d.get("links") or []) if isinstance(l, (list, tuple)) and len(l) == 2]
        if links:
            await self.send(uid, "👇", self.keyboard([[l[0], f"url:{l[1]}"] for l in links]))
            sent_any = True
        if d.get("access") and await self.grant_access(uid, key):
            sent_any = True
        if d.get("anketa"):
            await self.ask_consent(uid, d["anketa"])
            return True
        expected = bool(d.get("files") or links or d.get("access"))
        if expected and not sent_any:
            # файлы ещё не загружены на сервер — клиент получит доступ лично
            await self.send(uid, self.content.sys("delivery_manual") or "Доступ пришлю лично 🤍")
        return sent_any

    # ------------------------------------------------------------------ закрытые группы
    def access_groups(self) -> dict[str, dict]:
        """{ключ группы: {'products': [...], 'timed': bool}} — из delivery.access в content.yaml."""
        out: dict[str, dict] = {}
        for key, p in self.content.products.items():
            acc = (p.get("delivery") or {}).get("access") or {}
            g = acc.get("group")
            if not g:
                continue
            item = out.setdefault(g, {"products": [], "timed": False})
            item["products"].append(key)
            item["timed"] = item["timed"] or bool(acc.get("days"))
        return out

    @staticmethod
    def fmt_date(ts: int | None) -> str:
        if not ts:
            return "навсегда"
        msk = dt.timezone(dt.timedelta(hours=3))
        return dt.datetime.fromtimestamp(ts, msk).strftime("%d.%m.%Y")

    async def bot_rights(self, chat_id: int, need_ban: bool) -> list[str]:
        """Проверяет права бота в чате. Возвращает список того, чего не хватает (пустой — всё хорошо)."""
        try:
            me = await self.bot.get_me()
            m = await self.bot.get_chat_member(chat_id, me.id)
        except Exception as e:  # noqa: BLE001
            return [f"бот не видит чат ({e})"]
        if getattr(m, "status", "") not in ("administrator", "creator"):
            return ["бот не администратор"]
        missing = []
        if not getattr(m, "can_invite_users", False):
            missing.append("«Пригласительные ссылки» (Добавление участников)")
        if need_ban and not getattr(m, "can_restrict_members", False):
            missing.append("«Блокировка пользователей» — нужна, чтобы убирать по окончании доступа")
        return missing

    async def bind_group(self, key: str, chat_id: int, title: str) -> str:
        """Привязывает чат к ключу группы и возвращает отчёт для Маргариты."""
        groups = self.access_groups()
        if key not in groups:
            return (f"Нет группы с ключом <code>{html.escape(key)}</code>. "
                    f"Ключи: {', '.join(groups) or '—'}")
        await self.db.bind_group(key, chat_id, title)
        names = ", ".join(self.content.products[p].get("name", p) for p in groups[key]["products"])
        missing = await self.bot_rights(chat_id, groups[key]["timed"])
        text = f"✅ «{html.escape(title)}» привязана к продукту: {html.escape(names)}"
        if missing:
            text += "\n\n⚠️ Не хватает прав бота: " + "; ".join(html.escape(x) for x in missing)
        return text

    async def grant_access(self, uid: int, key: str) -> bool:
        """Открывает доступ к закрытой группе продукта и присылает персональную ссылку-заявку.
        Ссылка работает только для покупательницы: заявку от любого другого человека бот отклонит."""
        p = self.content.products.get(key, {})
        acc = (p.get("delivery") or {}).get("access") or {}
        grp, days = acc.get("group"), acc.get("days")
        expires = await self.db.grant_access(uid, key, grp, days)
        g = await self.db.group(grp)
        button = acc.get("button") or "Войти в группу"
        until = f"\n\nДоступ открыт до <b>{self.fmt_date(expires)}</b>." if expires else ""
        if g:
            try:
                link = await self.bot.create_chat_invite_link(
                    g["chat_id"], name=f"{key} {uid}"[:32], creates_join_request=True)
                text = (self.content.sys("access_link") or
                        "Нажми кнопку и отправь заявку — я одобрю её автоматически 🤍") + until
                return await self.send(uid, text, self.keyboard([[button, f"url:{link.invite_link}"]]))
            except Exception as e:  # noqa: BLE001
                log.warning("invite link for %s failed: %s", grp, e)
                problem = f"бот не смог создать ссылку ({html.escape(str(e))[:200]})"
        else:
            problem = "группа ещё не привязана"
        fallback = acc.get("link")
        await self.alert(f"⚠️ Доступ к группе <b>{html.escape(str(grp))}</b>: {problem}. "
                         + ("Клиентке выдана общая ссылка." if fallback else "<b>Выдай доступ вручную.</b>"),
                         about_uid=uid)
        if fallback:
            text = (self.content.sys("access_link_plain") or "Вот вход в закрытую группу 👇") + until
            return await self.send(uid, text, self.keyboard([[button, f"url:{fallback}"]]))
        return False

    async def on_join_request(self, req: ChatJoinRequest) -> None:
        uid, chat_id = req.from_user.id, req.chat.id
        groups = await self.db.group_keys_for_chat(chat_id)
        if not groups:
            return  # чат не наш — решает Маргарита
        if await self.db.has_access(uid, groups):
            try:
                await self.bot.approve_chat_join_request(chat_id, uid)
            except TelegramBadRequest as e:  # уже в группе или заявка устарела
                log.info("approve %s in %s: %s", uid, chat_id, e)
            return
        try:
            await self.bot.decline_chat_join_request(chat_id, uid)
        except TelegramBadRequest as e:
            log.info("decline %s in %s: %s", uid, chat_id, e)
        products = [k for g in groups for k in self.access_groups().get(g, {}).get("products", [])]
        kb = self.keyboard([["Посмотреть продукт", f"product:{products[0]}"]]) if products else None
        await self.send(req.user_chat_id or uid,
                        self.content.sys("access_denied") or
                        "Вход в эту группу открывается после покупки 🤍", kb)

    async def access_tick(self) -> None:
        """Напоминания за 3 дня и закрытие доступа по окончании срока."""
        for a in await self.db.access_to_remind(3 * 86400):
            p = self.content.products.get(a["product"], {})
            v = {**await self._user_vars(a["user_id"]), **self.content.product_vars(a["product"]),
                 "date": self.fmt_date(a["expires_at"])}
            text = render(self.content.sys("access_reminder") or
                          "Доступ к «{name}» закончится {date}.", **v)
            await self.send(a["user_id"], text, self.keyboard([["Продлить доступ", f"pay:{a['product']}"]])
                            if p.get("payable", True) else None)
            await self.db.set_access(a["user_id"], a["product"], reminded=1)
        for a in await self.db.access_expired():
            uid, key = a["user_id"], a["product"]
            g = await self.db.group(a["grp"])
            removed = False
            if g:
                try:
                    await self.bot.ban_chat_member(g["chat_id"], uid, until_date=int(time.time()) + 60)
                    await self.bot.unban_chat_member(g["chat_id"], uid, only_if_banned=True)
                    removed = True
                except TelegramBadRequest as e:
                    msg = str(e).lower()
                    removed = "not found" in msg or "participant" in msg  # уже вышла сама
                    if not removed:
                        log.warning("remove %s from %s failed: %s", uid, a["grp"], e)
                except Exception as e:  # noqa: BLE001
                    log.warning("remove %s from %s failed: %s", uid, a["grp"], e)
            await self.db.set_access(uid, key, status="expired")
            v = {**await self._user_vars(uid), **self.content.product_vars(key)}
            p = self.content.products.get(key, {})
            await self.send(uid, render(self.content.sys("access_expired") or
                                        "Доступ к «{name}» закончился.", **v),
                            self.keyboard([["Продлить доступ", f"pay:{key}"]]) if p.get("payable", True) else None)
            if not removed:
                await self.alert(f"⚠️ Доступ к «{html.escape(p.get('name', key))}» закончился, но бот не смог "
                                 f"убрать клиентку из группы — убери вручную.", about_uid=uid)

    # ------------------------------------------------------------------ гайды
    async def give_lead(self, uid: int, key: str) -> None:
        lead = self.content.leads.get(key)
        if not lead:
            return await self.show_screen(uid, "guides")
        await self.send(uid, render(lead.get("text", ""), **await self._user_vars(uid)))
        ok = await self.send_file(uid, lead.get("file", ""), protect=False)
        if not ok:
            await self.alert(f"⚠️ Нет файла гайда <code>{html.escape(lead.get('file', ''))}</code> — пришли вручную",
                             about_uid=uid)
        first = not await self.db.has_tag(uid, f"lead_{key}")
        await self.db.add_tag(uid, f"lead_{key}")
        chain = lead.get("chain")
        if chain and first and not await self.db.has_tag(uid, "buyer"):
            # одна цепочка прогрева на человека, даже если он взял оба гайда
            if not await self.db.has_pending(uid, f"chain:{chain}"):
                await self.start_chain(uid, chain, group_key=f"chain:{chain}", params={})

    # ------------------------------------------------------------------ анкеты
    async def ask_consent(self, uid: int, anketa_key: str) -> None:
        if anketa_key not in self.content.anketas:
            return
        c = self.content.consent
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=c.get("agree", "Согласна"), callback_data=f"consent:{anketa_key}:1")],
            [InlineKeyboardButton(text=c.get("decline", "Назад"), callback_data=f"consent:{anketa_key}:0")],
        ])
        await self.send(uid, c.get("text", "Согласна на обработку персональных данных?"), kb)

    async def begin_anketa(self, uid: int, key: str) -> None:
        await self.db.add_tag(uid, "consent_pd")
        await self.db.add_tag(uid, f"consent_pd_{key}")
        self.anketa[uid] = AnketaState(key=key)
        a = self.content.anketas[key]
        if a.get("title"):
            await self.send(uid, f"<b>{html.escape(a['title'])}</b>")
        await self.ask_question(uid)

    async def ask_question(self, uid: int) -> None:
        st = self.anketa.get(uid)
        if not st:
            return
        q = self.content.anketas[st.key]["questions"][st.idx]
        kb = None
        if q.get("buttons"):
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=b, callback_data=f"ans:{st.idx}:{i}")] for i, b in enumerate(q["buttons"])
            ])
        await self.send(uid, q["text"], kb)

    async def answer_question(self, uid: int, value: str) -> None:
        st = self.anketa.get(uid)
        if not st:
            return
        a = self.content.anketas[st.key]
        q = a["questions"][st.idx]
        st.answers[q["key"]] = value.strip()[:2000]
        st.idx += 1
        if st.idx < len(a["questions"]):
            return await self.ask_question(uid)
        # анкета заполнена
        self.anketa.pop(uid, None)
        await self.db.save_answers(uid, st.key, st.answers)
        await self.db.add_tag(uid, f"anketa_{st.key}")
        await self.send(uid, a.get("done", "Спасибо 🤍"))
        lines = [f"📝 <b>{html.escape(a.get('title', st.key))}</b>"]
        for qq in a["questions"]:
            lines.append(f"\n<b>{html.escape(qq['text'])}</b>\n{html.escape(st.answers.get(qq['key'], '—'))}")
        await self.alert("\n".join(lines), about_uid=uid)
        fu = a.get("followup")
        if fu and st.answers.get(fu.get("if_key")) == fu.get("equals"):
            await self.send(uid, fu.get("text", ""), self.keyboard([fu["button"]]) if fu.get("button") else None)

    # ------------------------------------------------------------------ цепочки
    async def start_chain(self, uid: int, chain: str, group_key: str, params: dict) -> None:
        c = self.content.chains.get(chain)
        if not c:
            return
        t = now()
        # задержка каждого шага считается от момента старта цепочки
        steps = [(i, t + parse_delay(step.get("delay", "0m"))) for i, step in enumerate(c.get("steps", []))]
        await self.db.schedule(uid, chain, steps, group_key, params)

    async def run_job(self, job) -> None:
        chain = self.content.chains.get(job["chain"])
        if not chain or job["step"] >= len(chain.get("steps", [])):
            return await self.db.job_done(job["id"], 3)
        params = json.loads(job["params"] or "{}")
        # если продукт уже куплен — дожим не нужен
        if job["group_key"].startswith("dozhim:") and await self.db.has_tag(job["user_id"], f"paid_{params.get('product')}"):
            return await self.db.job_done(job["id"], 2)
        step = chain["steps"][job["step"]]
        v = {**await self._user_vars(job["user_id"])}
        if params.get("product"):
            v.update(self.content.product_vars(params["product"]))
        ok = await self.send(job["user_id"], render(step.get("text", ""), **v), self.keyboard(step.get("buttons"), v))
        await self.db.job_done(job["id"], 1 if ok else 4)

    async def scheduler(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                for job in await self.db.due_jobs():
                    await self.run_job(job)
                    await asyncio.sleep(0.05)
            except Exception as e:  # noqa: BLE001
                log.exception("scheduler error: %s", e)
            try:
                await self.access_tick()
            except Exception as e:  # noqa: BLE001
                log.exception("access error: %s", e)
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.cfg.tick_seconds)
            except asyncio.TimeoutError:
                pass
