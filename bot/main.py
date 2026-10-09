"""Точка входа: python -m bot.main"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import BotCommand
from aiohttp import web

from .config import load_config
from .content import Content
from .core import App
from .db import DB
from .handlers import build_router
from .web import build_web


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    if not cfg.bot_token:
        sys.exit("BOT_TOKEN не задан в .env")
    if not (cfg.rk_login and cfg.rk_pass1 and cfg.rk_pass2):
        logging.warning("Данные Робокассы не заданы — оплата работать не будет")

    content = Content(cfg.content_path)
    for p in content.reload():
        logging.warning("content.yaml: %s", p)
    cfg.files_dir.mkdir(parents=True, exist_ok=True)

    db = DB(cfg.db_path, cfg.order_start)
    await db.open()
    bot = Bot(cfg.bot_token, default=DefaultBotProperties(parse_mode="HTML"))
    app = App(bot, db, content, cfg)

    dp = Dispatcher()
    dp.include_router(build_router(app))
    await bot.set_my_commands([BotCommand(command="start", description="Начать"),
                               BotCommand(command="menu", description="Главное меню")])

    runner = web.AppRunner(build_web(app))
    await runner.setup()
    await web.TCPSite(runner, cfg.web_host, cfg.web_port).start()
    logging.info("HTTP на %s:%s, публичный адрес %s", cfg.web_host, cfg.web_port, cfg.public_url)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    sched = asyncio.create_task(app.scheduler(stop))
    # бот работает через long polling: удаляем вебхук, оставшийся от Botmother
    await bot.delete_webhook(drop_pending_updates=False)
    polling = asyncio.create_task(dp.start_polling(bot, handle_signals=False))
    await stop.wait()
    await dp.stop_polling()
    await asyncio.gather(sched, polling, return_exceptions=True)
    await runner.cleanup()
    await db.close()
    await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
