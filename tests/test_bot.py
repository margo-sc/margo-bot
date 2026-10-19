"""Тесты: подпись Робокассы, оплата → выдача, цепочки, анкеты, обработчики Telegram."""
from __future__ import annotations

import datetime as dt
import hashlib
import re
from pathlib import Path
from urllib.parse import unquote

import json
import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import (AnswerCallbackQuery, ApproveChatJoinRequest, BanChatMember, CopyMessage,
                             CreateChatInviteLink, DeclineChatJoinRequest, EditMessageReplyMarkup,
                             EditMessageText, GetChatMember, GetMe, SendDocument, SendMessage, TelegramMethod,
                             UnbanChatMember)
from aiogram.types import (Chat, ChatInviteLink, ChatMemberAdministrator, Document, Message, MessageId, Update,
                           User)
from aiohttp.test_utils import TestClient, TestServer

from bot import robokassa
from bot.config import Config
from bot.content import Content
from bot.core import App
from bot.db import DB
from bot.handlers import build_router
from bot.web import build_web

ROOT = Path(__file__).resolve().parent.parent
ADMIN = 111
CLIENT = 222
FRIEND = 333
GROUP_ZAL = -100500
CHANNEL_KNIGA = -100700
BOT_USER = User(id=123, is_bot=True, first_name="Марго бот", username="margo_sale_bot")


# ------------------------------------------------------------------ фейковый Telegram
class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls: list[TelegramMethod] = []
        self._mid = 1000
        self.rights = {"can_invite_users": True, "can_restrict_members": True}

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        self._mid += 1
        chat = Chat(id=getattr(method, "chat_id", 0) or 0, type="private")
        if isinstance(method, SendMessage):
            return Message(message_id=self._mid, date=dt.datetime.now(), chat=chat, text=method.text)
        if isinstance(method, SendDocument):
            return Message(message_id=self._mid, date=dt.datetime.now(), chat=chat,
                           document=Document(file_id=f"fid{self._mid}", file_unique_id="u"))
        if isinstance(method, CopyMessage):
            return MessageId(message_id=self._mid)
        if isinstance(method, (AnswerCallbackQuery,)):
            return True
        if isinstance(method, (EditMessageReplyMarkup, EditMessageText)):
            return True
        if isinstance(method, CreateChatInviteLink):
            return ChatInviteLink(invite_link=f"https://t.me/+one{self._mid}", creator=BOT_USER,
                                  creates_join_request=bool(method.creates_join_request), is_primary=False,
                                  is_revoked=False, name=method.name)
        if isinstance(method, GetMe):
            return BOT_USER
        if isinstance(method, GetChatMember):
            fields = {n: False for n, f in ChatMemberAdministrator.model_fields.items() if f.annotation is bool}
            fields.update(self.rights)
            return ChatMemberAdministrator(user=BOT_USER, status="administrator", **fields)
        return True

    async def close(self):
        pass

    async def stream_content(self, *a, **k):  # pragma: no cover
        yield b""

    def texts_to(self, chat_id: int) -> list[str]:
        return [c.text for c in self.calls if isinstance(c, SendMessage) and c.chat_id == chat_id]

    def last_markup(self, chat_id: int):
        for c in reversed(self.calls):
            if isinstance(c, SendMessage) and c.chat_id == chat_id and c.reply_markup:
                return c.reply_markup
        return None


def make_cfg(tmp: Path, **kw) -> Config:
    base = dict(
        bot_token="123:ABC", admin_ids=[ADMIN], alert_chat_id=ADMIN, public_url="https://pay.example.ru",
        web_host="127.0.0.1", web_port=0, data_dir=tmp, content_path=ROOT / "content.yaml",
        rk_login="Shop", rk_pass1="p1", rk_pass2="p2", rk_test=True, rk_hash="md5",
        rk_receipt=True, rk_sno="", order_start=500000,
    )
    base.update(kw)
    return Config(**base)


@pytest.fixture
async def env(tmp_path):
    cfg = make_cfg(tmp_path)
    (tmp_path / "files").mkdir()
    db = DB(cfg.db_path, cfg.order_start)
    await db.open()
    session = FakeSession()
    bot = Bot(cfg.bot_token, session=session)
    app = App(bot, db, Content(cfg.content_path), cfg)
    dp = Dispatcher()
    dp.include_router(build_router(app))
    yield app, session, dp, bot
    await db.close()


_uid = [0]


def upd_message(uid: int, text: str, reply_to: int | None = None) -> Update:
    _uid[0] += 1
    user = User(id=uid, is_bot=False, first_name="Аня", username="anya")
    chat = Chat(id=uid, type="private")
    reply = Message(message_id=reply_to, date=dt.datetime.now(), chat=chat, text="x") if reply_to else None
    ent = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}] if text.startswith("/") else None
    msg = Message(message_id=_uid[0], date=dt.datetime.now(), chat=chat, from_user=user, text=text,
                  reply_to_message=reply, entities=ent)
    return Update(update_id=_uid[0], message=msg)


def upd_callback(uid: int, data: str) -> Update:
    _uid[0] += 1
    user = User(id=uid, is_bot=False, first_name="Аня")
    chat = Chat(id=uid, type="private")
    msg = Message(message_id=_uid[0], date=dt.datetime.now(), chat=chat, text="x")
    return Update(update_id=_uid[0], callback_query={
        "id": str(_uid[0]), "from": user, "chat_instance": "ci", "message": msg, "data": data})


def callbacks(markup) -> list[str]:
    return [b.callback_data or b.url for row in markup.inline_keyboard for b in row]


# ------------------------------------------------------------------ Робокасса
def test_signature_without_receipt():
    f = robokassa.payment_fields(login="Shop", pass1="p1", out_sum="990.00", inv_id=5, description="Книга")
    assert f["SignatureValue"] == hashlib.md5(b"Shop:990.00:5:p1").hexdigest()
    assert "IsTest" not in f and "Receipt" not in f


def test_signature_with_receipt_is_urlencoded_once():
    r = robokassa.build_receipt("Книга «Я снова сорвалась»", 990, "commodity")
    assert "%" in r and "{" not in r  # закодирован
    data = json.loads(unquote(r))
    assert data["items"][0]["sum"] == 990 and data["items"][0]["tax"] == "none"
    f = robokassa.payment_fields(login="Shop", pass1="p1", out_sum="990.00", inv_id=5, description="К",
                                 receipt=r, test=True)
    assert f["SignatureValue"] == hashlib.md5(f"Shop:990.00:5:{r}:p1".encode()).hexdigest()
    assert f["IsTest"] == "1" and f["Receipt"] == r


def test_result_check_with_shp_sorted():
    sign = hashlib.md5(b"990.000000:5:p2:Shp_a=1:Shp_b=2").hexdigest().upper()
    params = {"OutSum": "990.000000", "InvId": "5", "SignatureValue": sign, "Shp_b": "2", "Shp_a": "1"}
    assert robokassa.check_result(params, "p2")
    assert not robokassa.check_result({**params, "OutSum": "1.00"}, "p2")
    assert not robokassa.check_result(params, "wrong")


def test_content_is_valid():
    assert Content(ROOT / "content.yaml").reload() == []


# ------------------------------------------------------------------ сценарии
async def test_deeplink_opens_product_card(env):
    app, s, dp, bot = env
    await dp.feed_update(bot, upd_message(CLIENT, "/start site_kniga"))
    texts = s.texts_to(CLIENT)
    assert "Я снова сорвалась" in texts[-1] and "990" in texts[-1]
    assert "a:pay:kniga" in callbacks(s.last_markup(CLIENT))
    u = await app.db.get_user(CLIENT)
    assert u["source"] == "site"
    assert await app.db.has_tag(CLIENT, "src_site_kniga")


async def test_plain_start_shows_start_screen(env):
    app, s, dp, bot = env
    await dp.feed_update(bot, upd_message(CLIENT, "/start"))
    assert "Аня" in s.texts_to(CLIENT)[-1]
    assert "a:screen:catalog" in callbacks(s.last_markup(CLIENT))


async def test_full_payment_flow(env, tmp_path):
    app, s, dp, bot = env
    await app.db.bind_group("kniga", CHANNEL_KNIGA, "Я снова сорвалась | Книга Марго")
    await dp.feed_update(bot, upd_message(CLIENT, "/start site_kniga"))
    await dp.feed_update(bot, upd_callback(CLIENT, "a:pay:kniga"))
    url = callbacks(s.last_markup(CLIENT))[0]
    assert url.startswith("https://pay.example.ru/pay/")
    token = url.rsplit("/", 1)[1]
    order = await app.db.order_by_token(token)
    assert order["id"] >= 500000 and order["amount"] == 990
    assert await app.db.has_pending(CLIENT, "dozhim:kniga")

    async with TestClient(TestServer(build_web(app))) as client:
        page = await (await client.get(f"/pay/{token}")).text()
        assert 'action="https://auth.robokassa.ru/Merchant/Index.aspx"' in page
        assert 'name="IsTest" value="1"' in page
        inv = re.search(r'name="InvId" value="(\d+)"', page).group(1)
        assert int(inv) == order["id"]

        bad = await client.post("/robokassa/result", data={"OutSum": "990.000000", "InvId": inv, "SignatureValue": "x"})
        assert bad.status == 400

        sign = hashlib.md5(f"990.000000:{inv}:p2".encode()).hexdigest()
        ok = await client.post("/robokassa/result", data={"OutSum": "990.000000", "InvId": inv, "SignatureValue": sign})
        assert await ok.text() == f"OK{inv}"
        links = [c for c in s.calls if isinstance(c, CreateChatInviteLink)]
        assert len(links) == 1 and links[0].chat_id == CHANNEL_KNIGA and links[0].creates_join_request
        assert any(u.startswith("https://t.me/+one") for u in callbacks(s.last_markup(CLIENT)))
        assert any("Оплата" in t and "990" in t and "выдано автоматически" in t for t in s.texts_to(ADMIN))
        # повторное уведомление не выдаёт продукт второй раз
        again = await client.post("/robokassa/result", data={"OutSum": "990.000000", "InvId": inv, "SignatureValue": sign})
        assert await again.text() == f"OK{inv}"
        assert len([c for c in s.calls if isinstance(c, CreateChatInviteLink)]) == 1
        paid_page = await (await client.get(f"/pay/{token}")).text()
        assert "уже оплачен" in paid_page

    assert not await app.db.has_pending(CLIENT, "dozhim:kniga")
    assert await app.db.has_pending(CLIENT, "after:kniga")
    assert await app.db.has_tag(CLIENT, "paid_kniga")


async def test_missing_files_fall_back_to_manual(env):
    app, s, dp, bot = env
    app.content.data["products"]["uchebnik"]["delivery"] = {"text": "Спасибо", "files": ["uchebnik.pdf"]}
    await app.db.touch_user(CLIENT, None, "Аня", "site")
    order = await app.db.create_order(CLIENT, "uchebnik", 4990)
    await app.db.mark_paid(order["id"])
    await app.on_paid(await app.db.order_by_id(order["id"]))
    assert any("пришлю лично" in t for t in s.texts_to(CLIENT))
    assert any("выдай вручную" in t for t in s.texts_to(ADMIN))


async def test_underpaid_is_not_delivered(env):
    app, s, dp, bot = env
    await app.db.touch_user(CLIENT, None, "Аня", "site")
    order = await app.db.create_order(CLIENT, "zal", 5000)
    sign = hashlib.md5(f"10.00:{order['id']}:p2".encode()).hexdigest()
    async with TestClient(TestServer(build_web(app))) as client:
        r = await client.post("/robokassa/result", data={"OutSum": "10.00", "InvId": str(order["id"]), "SignatureValue": sign})
        assert r.status == 200
    assert (await app.db.order_by_id(order["id"]))["status"] == "new"
    assert any("меньше счёта" in t for t in s.texts_to(ADMIN))


async def test_lead_warmup_once_and_stops_on_purchase(env):
    app, s, dp, bot = env
    await dp.feed_update(bot, upd_message(CLIENT, "/start insta_mify"))
    assert any("Держи гайд" in t for t in s.texts_to(CLIENT))
    assert any("Нет файла гайда" in t for t in s.texts_to(ADMIN))  # файла нет — Маргарите предупреждение
    await dp.feed_update(bot, upd_callback(CLIENT, "a:lead:pms"))
    cur = await app.db.conn.execute("SELECT COUNT(*) FROM jobs WHERE user_id=? AND chain='warmup' AND done=0", (CLIENT,))
    assert (await cur.fetchone())[0] == 5  # одна цепочка, не две
    order = await app.db.create_order(CLIENT, "kniga", 990)
    await app.db.mark_paid(order["id"])
    await app.on_paid(await app.db.order_by_id(order["id"]))
    assert not await app.db.has_pending(CLIENT, "chain:warmup")


async def test_scheduler_sends_due_job_and_skips_paid_dozhim(env):
    app, s, dp, bot = env
    await app.db.touch_user(CLIENT, None, "Аня", "x")
    await app.start_chain(CLIENT, "dozhim", "dozhim:konsult", {"product": "konsult"})
    await app.db.conn.execute("UPDATE jobs SET run_at=0 WHERE step=0")
    await app.db.conn.commit()
    for j in await app.db.due_jobs():
        await app.run_job(j)
    assert any("Личная консультация" in t for t in s.texts_to(CLIENT))
    assert "a:pay:konsult" in callbacks(s.last_markup(CLIENT))
    await app.db.add_tag(CLIENT, "paid_konsult")
    await app.db.conn.execute("UPDATE jobs SET run_at=0")
    await app.db.conn.commit()
    before = len(s.texts_to(CLIENT))
    for j in await app.db.due_jobs():
        await app.run_job(j)
    assert len(s.texts_to(CLIENT)) == before


async def test_vedenie_anketa_with_followup(env):
    app, s, dp, bot = env
    await dp.feed_update(bot, upd_message(CLIENT, "/start site_vedenie"))
    await dp.feed_update(bot, upd_callback(CLIENT, "a:product:vedenie"))
    assert "a:interest:vedenie" in callbacks(s.last_markup(CLIENT))
    await dp.feed_update(bot, upd_callback(CLIENT, "a:interest:vedenie"))
    assert callbacks(s.last_markup(CLIENT))[0].startswith("https://t.me/margo_tyutyunnik?text=")
    note = s.texts_to(ADMIN)[-1]
    assert "личное ведение" in note and f"/schet {CLIENT} vedenie" in note
    assert await app.db.has_tag(CLIENT, "interest_vedenie")
    await dp.feed_update(bot, upd_callback(CLIENT, "a:anketa:vedenie"))  # старая анкета всё ещё доступна по ссылке
    assert "Согласна" in str(s.last_markup(CLIENT))
    await dp.feed_update(bot, upd_callback(CLIENT, "consent:vedenie:1"))
    await dp.feed_update(bot, upd_message(CLIENT, "Минус 5 кг к лету"))
    await dp.feed_update(bot, upd_message(CLIENT, "Диеты"))
    await dp.feed_update(bot, upd_message(CLIENT, "Устала сама"))
    await dp.feed_update(bot, upd_callback(CLIENT, "ans:3:1"))   # «Пока не уверена»
    await dp.feed_update(bot, upd_callback(CLIENT, "ans:4:1"))   # Сплит
    await dp.feed_update(bot, upd_message(CLIENT, "Вечером"))
    admin_texts = "\n".join(s.texts_to(ADMIN))
    assert "Анкета для личного ведения" in admin_texts and "Минус 5 кг" in admin_texts and "Пока не уверена" in admin_texts
    assert any("консультация" in t for t in s.texts_to(CLIENT))
    assert await app.db.has_tag(CLIENT, "consent_pd_vedenie")
    assert CLIENT not in app.anketa


@pytest.mark.parametrize("key,price,title", [("konsult", 2500, "АНКЕТА ПЕРЕД"), ("individ", 7000, "ТЕСТ ДЛЯ")])
async def test_paid_service_sends_owner_questionnaire_and_dm_button(env, key, price, title):
    app, s, dp, bot = env
    await app.db.touch_user(CLIENT, None, "Аня", "x")
    order = await app.db.create_order(CLIENT, key, price)
    await app.db.mark_paid(order["id"])
    await app.on_paid(await app.db.order_by_id(order["id"]))
    msgs = [c for c in s.calls if isinstance(c, SendMessage) and c.chat_id == CLIENT]
    assert len(msgs) == 1 and title in msgs[0].text and len(msgs[0].text) < 4096
    assert callbacks(msgs[0].reply_markup) == ["https://t.me/margo_tyutyunnik"]
    assert not any("consent:" in str(c.reply_markup) for c in msgs)  # бот больше не собирает анкету сам
    assert any("ответы придут тебе в личку" in t for t in s.texts_to(ADMIN))


async def test_client_message_relay_and_admin_reply(env):
    app, s, dp, bot = env
    await dp.feed_update(bot, upd_message(CLIENT, "/start"))
    await dp.feed_update(bot, upd_message(CLIENT, "Подойдёт ли мне книга?"))
    copies = [c for c in s.calls if isinstance(c, CopyMessage) and c.chat_id == ADMIN]
    assert copies
    cur = await app.db.conn.execute("SELECT admin_msg FROM relay WHERE user_id=? ORDER BY rowid DESC", (CLIENT,))
    admin_msg = (await cur.fetchone())[0]
    await dp.feed_update(bot, upd_message(ADMIN, "Да, подойдёт!", reply_to=admin_msg))
    assert any(isinstance(c, CopyMessage) and c.chat_id == CLIENT for c in s.calls)


async def test_admin_invoice_and_stats(env):
    app, s, dp, bot = env
    await dp.feed_update(bot, upd_message(CLIENT, "/start site_vedenie"))
    await dp.feed_update(bot, upd_message(ADMIN, f"/schet {CLIENT} vedenie 12000"))
    assert any("12 000" in t for t in s.texts_to(CLIENT))
    assert not await app.db.has_pending(CLIENT, "dozhim:vedenie")
    await dp.feed_update(bot, upd_message(ADMIN, "/stats"))
    assert any("Подписчиков: 1" in t for t in s.texts_to(ADMIN))
    # клиент не может пользоваться командами Маргариты
    await dp.feed_update(bot, upd_message(CLIENT, f"/vydat {CLIENT} kniga"))
    assert not await app.db.has_tag(CLIENT, "paid_kniga")


async def test_admin_uploads_file_via_telegram_then_delivery_uses_it(env):
    app, s, dp, bot = env
    _uid[0] += 1
    user = User(id=ADMIN, is_bot=False, first_name="Марго")
    msg = Message(message_id=_uid[0], date=dt.datetime.now(), chat=Chat(id=ADMIN, type="private"), from_user=user,
                  document=Document(file_id="BOOKFID", file_unique_id="b"), caption="/file uchebnik.pdf")
    app.content.data["products"]["uchebnik"]["delivery"] = {"text": "Спасибо", "files": ["uchebnik.pdf"]}
    await dp.feed_update(bot, Update(update_id=_uid[0], message=msg))
    assert await app.db.file_id("uchebnik.pdf") == "BOOKFID"
    await dp.feed_update(bot, upd_message(ADMIN, "/files"))
    assert "✅ <code>uchebnik.pdf</code>" in s.texts_to(ADMIN)[-1]
    await app.db.touch_user(CLIENT, None, "Аня", "x")
    order = await app.db.create_order(CLIENT, "uchebnik", 4990)
    await app.db.mark_paid(order["id"])
    await app.on_paid(await app.db.order_by_id(order["id"]))
    docs = [c for c in s.calls if isinstance(c, SendDocument) and c.chat_id == CLIENT]
    assert docs and docs[0].document == "BOOKFID"
    assert any("выдано автоматически" in t for t in s.texts_to(ADMIN))


async def test_claim_makes_owner(tmp_path):
    cfg = make_cfg(tmp_path, admin_ids=[], alert_chat_id=None, claim_code="abc12345")
    db = DB(cfg.db_path); await db.open()
    s = FakeSession(); bot = Bot(cfg.bot_token, session=s)
    app = App(bot, db, Content(cfg.content_path), cfg)
    dp = Dispatcher(); dp.include_router(build_router(app))
    await dp.feed_update(bot, upd_message(CLIENT, "/claim wrong"))
    assert not app.is_admin(CLIENT)
    await dp.feed_update(bot, upd_message(CLIENT, "/claim abc12345"))
    assert app.is_admin(CLIENT) and app.cfg.alert_chat_id == CLIENT
    assert (tmp_path / "admins.txt").read_text().strip() == str(CLIENT)
    from bot.config import load_config
    import os
    os.environ["DATA_DIR"] = str(tmp_path); os.environ.pop("ADMIN_IDS", None)
    assert CLIENT in load_config(tmp_path / "none.env").admin_ids
    await db.close()


# ------------------------------------------------------------------ закрытые группы
def upd_join(uid: int, chat_id: int) -> Update:
    _uid[0] += 1
    return Update(update_id=_uid[0], chat_join_request={
        "chat": Chat(id=chat_id, type="supergroup", title="Группа"), "from": User(id=uid, is_bot=False, first_name="Х"),
        "user_chat_id": uid, "date": dt.datetime.now()})


async def pay(app, uid, key, amount):
    await app.db.touch_user(uid, None, "Аня", "site")
    order = await app.db.create_order(uid, key, amount)
    await app.db.mark_paid(order["id"])
    await app.on_paid(await app.db.order_by_id(order["id"]))


async def test_bot_added_to_group_asks_product_and_binds(env):
    app, s, dp, bot = env
    _uid[0] += 1
    chat = Chat(id=GROUP_ZAL, type="supergroup", title="Программа тренировок для зала")
    upd = Update(update_id=_uid[0], my_chat_member={
        "chat": chat, "from": User(id=ADMIN, is_bot=False, first_name="Марго"), "date": dt.datetime.now(),
        "old_chat_member": {"status": "left", "user": BOT_USER},
        "new_chat_member": {**{n: False for n, f in ChatMemberAdministrator.model_fields.items()
                               if f.annotation is bool},
                            "status": "administrator", "user": BOT_USER,
                            "can_invite_users": True, "can_restrict_members": True}})
    await dp.feed_update(bot, upd)
    assert "Для какого продукта" in s.texts_to(ADMIN)[-1]
    assert f"grp:zal:{GROUP_ZAL}" in callbacks(s.last_markup(ADMIN))
    await dp.feed_update(bot, upd_callback(ADMIN, f"grp:zal:{GROUP_ZAL}"))
    assert (await app.db.group("zal"))["chat_id"] == GROUP_ZAL
    assert "привязана" in s.texts_to(ADMIN)[-1] and "Не хватает" not in s.texts_to(ADMIN)[-1]
    # клиент не может привязывать группы
    await dp.feed_update(bot, upd_callback(CLIENT, f"grp:dom:{GROUP_ZAL}"))
    assert await app.db.group("dom") is None


async def test_missing_ban_right_is_reported(env):
    app, s, dp, bot = env
    s.rights = {"can_invite_users": True, "can_restrict_members": False}
    report = await app.bind_group("zal", GROUP_ZAL, "Зал")
    assert "Блокировка" in report
    assert "Блокировка" not in await app.bind_group("uchebnik", -100900, "Учебник")  # навсегда — блокировка не нужна


async def test_paid_zal_gets_personal_link_and_only_buyer_is_approved(env):
    app, s, dp, bot = env
    await app.db.bind_group("zal", GROUP_ZAL, "Зал")
    await pay(app, CLIENT, "zal", 5000)
    link = [c for c in s.calls if isinstance(c, CreateChatInviteLink)][-1]
    assert link.chat_id == GROUP_ZAL and link.creates_join_request
    assert any("Доступ открыт до" in t for t in s.texts_to(CLIENT))
    await dp.feed_update(bot, upd_join(CLIENT, GROUP_ZAL))
    assert any(isinstance(c, ApproveChatJoinRequest) and c.user_id == CLIENT for c in s.calls)
    await dp.feed_update(bot, upd_join(FRIEND, GROUP_ZAL))   # подруга по пересланной ссылке
    assert any(isinstance(c, DeclineChatJoinRequest) and c.user_id == FRIEND for c in s.calls)
    assert not any(isinstance(c, ApproveChatJoinRequest) and c.user_id == FRIEND for c in s.calls)
    assert any("после покупки" in t for t in s.texts_to(FRIEND))


async def test_unbound_group_uses_fallback_link_and_alerts(env):
    app, s, dp, bot = env
    await pay(app, CLIENT, "dom", 5000)
    assert "https://t.me/+vifC6bi2fLI0ZjEy" in callbacks(s.last_markup(CLIENT))
    assert any("не привязана" in t for t in s.texts_to(ADMIN))


async def test_access_reminder_expiry_and_renewal(env):
    app, s, dp, bot = env
    await app.db.bind_group("zal", GROUP_ZAL, "Зал")
    await pay(app, CLIENT, "zal", 5000)
    row = await (await app.db.conn.execute("SELECT * FROM access WHERE user_id=?", (CLIENT,))).fetchone()
    assert 59 * 86400 < row["expires_at"] - row["granted_at"] <= 60 * 86400
    # за 2 дня до конца — напоминание с кнопкой продления, один раз
    await app.db.set_access(CLIENT, "zal", expires_at=int(dt.datetime.now().timestamp()) + 2 * 86400)
    await app.access_tick(); await app.access_tick()
    assert sum("закончится" in t for t in s.texts_to(CLIENT)) == 1
    assert "a:pay:zal" in callbacks(s.last_markup(CLIENT))
    # продление до окончания прибавляет 60 дней к текущему сроку
    before = (await (await app.db.conn.execute("SELECT expires_at FROM access WHERE user_id=?", (CLIENT,))).fetchone())[0]
    await pay(app, CLIENT, "zal", 5000)
    after = (await (await app.db.conn.execute("SELECT expires_at, reminded FROM access WHERE user_id=?", (CLIENT,))).fetchone())
    assert after[0] == before + 60 * 86400 and after[1] == 0
    # срок вышел — бот убирает из группы (бан + разбан, чтобы можно было вернуться после оплаты)
    await app.db.set_access(CLIENT, "zal", expires_at=1)
    await app.access_tick()
    assert any(isinstance(c, BanChatMember) and c.user_id == CLIENT and c.chat_id == GROUP_ZAL for c in s.calls)
    assert any(isinstance(c, UnbanChatMember) and c.only_if_banned for c in s.calls)
    assert any("закончился" in t for t in s.texts_to(CLIENT))
    await dp.feed_update(bot, upd_join(CLIENT, GROUP_ZAL))
    assert any(isinstance(c, DeclineChatJoinRequest) and c.user_id == CLIENT for c in s.calls)


async def test_forever_access_never_expires(env):
    app, s, dp, bot = env
    await app.db.bind_group("uchebnik", -100900, "Учебник")
    await pay(app, CLIENT, "uchebnik", 4990)
    row = await (await app.db.conn.execute("SELECT expires_at FROM access WHERE user_id=?", (CLIENT,))).fetchone()
    assert row[0] is None
    assert not any("Доступ открыт до" in t for t in s.texts_to(CLIENT))
    await app.access_tick()
    assert not any(isinstance(c, BanChatMember) for c in s.calls)


async def test_admin_group_and_access_commands(env):
    app, s, dp, bot = env
    await app.db.bind_group("zal", GROUP_ZAL, "Программа для зала")
    await pay(app, CLIENT, "zal", 5000)
    await dp.feed_update(bot, upd_message(ADMIN, "/gruppy"))
    txt = s.texts_to(ADMIN)[-1]
    assert "✅" in txt and "Программа для зала" in txt and "не привязана" in txt
    await dp.feed_update(bot, upd_message(ADMIN, "/dostup zal"))
    assert str(CLIENT) in s.texts_to(ADMIN)[-1]


def test_new_prices_and_home_program():
    c = Content(ROOT / "content.yaml")
    assert c.products["individ"]["price"] == 7000
    assert c.products["dom"]["delivery"]["access"]["days"] == 60
    assert c.products["zal"]["delivery"]["access"]["days"] == 60
    assert "days" not in c.products["uchebnik"]["delivery"]["access"]
    assert "product:dom" in str(c.screens["catalog"]["buttons"])
