"""HTTP: страница перехода на оплату и приём уведомлений Робокассы (ResultURL)."""
from __future__ import annotations

import logging

from aiohttp import web

from . import robokassa
from .core import App

log = logging.getLogger("margo.web")


def build_web(app: App) -> web.Application:
    cfg = app.cfg

    async def pay(request: web.Request) -> web.Response:
        order = await app.db.order_by_token(request.match_info["token"])
        if not order:
            return web.Response(status=404, text="Ссылка на оплату не найдена. Вернись в бот и нажми «Оплатить» ещё раз.")
        if order["status"] == "paid":
            return web.Response(text="Этот заказ уже оплачен 🤍 Всё пришло в бот.", content_type="text/plain")
        p = app.content.products.get(order["product"], {})
        name = p.get("name", order["product"])
        out_sum = robokassa.money(order["amount"])
        receipt = robokassa.build_receipt(name, order["amount"], p.get("payment_object", "service"), cfg.rk_sno) \
            if cfg.rk_receipt else None
        fields = robokassa.payment_fields(
            login=cfg.rk_login, pass1=cfg.rk_pass1, out_sum=out_sum, inv_id=order["id"],
            description=name, receipt=receipt, test=cfg.rk_test, algo=cfg.rk_hash,
        )
        return web.Response(text=robokassa.autosubmit_page(fields, name), content_type="text/html")

    async def result(request: web.Request) -> web.Response:
        params = dict(request.query)
        if request.method == "POST":
            params.update(dict(await request.post()))
        if not robokassa.check_result(params, cfg.rk_pass2, cfg.rk_hash):
            log.warning("ResultURL: неверная подпись, InvId=%s", params.get("InvId"))
            return web.Response(status=400, text="bad sign")
        try:
            inv_id = int(params["InvId"])
        except (KeyError, ValueError):
            return web.Response(status=400, text="bad InvId")
        order = await app.db.order_by_id(inv_id)
        if not order:
            log.warning("ResultURL: заказ %s не найден", inv_id)
            # отвечаем OK, чтобы Робокасса не повторяла уведомление о чужом/старом счёте
            return web.Response(text=f"OK{inv_id}")
        if float(params.get("OutSum", "0")) + 0.01 < float(order["amount"]):
            log.warning("ResultURL: сумма %s меньше заказа %s", params.get("OutSum"), order["amount"])
            await app.alert(f"⚠️ Оплата заказа {inv_id} на сумму меньше счёта ({params.get('OutSum')} ₽). Проверь вручную.",
                            about_uid=order["user_id"])
            return web.Response(text=f"OK{inv_id}")
        if await app.db.mark_paid(inv_id):
            await app.on_paid(await app.db.order_by_id(inv_id))
        return web.Response(text=f"OK{inv_id}")

    async def health(_: web.Request) -> web.Response:
        return web.Response(text="ok")

    w = web.Application()
    w.router.add_get("/pay/{token}", pay)
    w.router.add_route("*", "/robokassa/result", result)
    w.router.add_get("/health", health)
    return w
