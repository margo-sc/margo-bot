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
from aiogram.methods import (AnswerCallbackQuery, CopyMessage, EditMessageReplyMarkup, EditMessageText,
                             SendDocument, SendMessage, TelegramMethod)
from aiogram.types import Chat, Document, Message, MessageId, Update, User
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


# ------------------------------------------------------------------ фейковый Telegram
class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls: list[TelegramMethod] = []
        self._mid = 1000

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
    (tmp_path / "files" / "kniga.pdf").write_bytes(b"%PDF-1.4 test")
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
        docs = [c for c in s.calls if isinstance(c, SendDocument) and c.chat_id == CLIENT]
        assert len(docs) == 1 and docs[0].protect_content is True
        assert any("Оплата" in t and "990" in t for t in s.texts_to(ADMIN))
        # повторное уведомление не выдаёт продукт второй раз
        again = await client.post("/robokassa/result", data={"OutSum": "990.000000", "InvId": inv, "SignatureValue": sign})
        assert await again.text() == f"OK{inv}"
        assert len([c for c in s.calls if isinstance(c, SendDocument) and c.chat_id == CLIENT]) == 1
        paid_page = await (await client.get(f"/pay/{token}")).text()
        assert "уже оплачен" in paid_page

    assert not await app.db.has_pending(CLIENT, "dozhim:kniga")
    assert await app.db.has_pending(CLIENT, "after:kniga")
    assert await app.db.has_tag(CLIENT, "paid_kniga")


async def test_missing_files_fall_back_to_manual(env):
    app, s, dp, bot = env
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
    assert "a:anketa:vedenie" in callbacks(s.last_markup(CLIENT))
    await dp.feed_update(bot, upd_callback(CLIENT, "a:anketa:vedenie"))
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


async def test_paid_konsult_starts_anketa(env):
    app, s, dp, bot = env
    await app.db.touch_user(CLIENT, None, "Аня", "x")
    order = await app.db.create_order(CLIENT, "konsult", 2500)
    await app.db.mark_paid(order["id"])
    await app.on_paid(await app.db.order_by_id(order["id"]))
    assert "consent:konsult:1" in callbacks(s.last_markup(CLIENT))


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
