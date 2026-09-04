"""Точка входа: поднимает бота, регистрирует роутеры, запускает polling."""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bot.config import load_settings
from bot.db import Database
from bot.filters.admin import AdminFilter
from bot.filters.flood import FloodCheck
from bot.filters.new_account import NewAccountCheck
from bot.filters.spam import SpamFilter
from bot.handlers import admin as admin_handlers
from bot.handlers import captcha as captcha_handlers
from bot.handlers import messages as messages_handlers
from bot.middlewares.deps import DependencyMiddleware


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # aiogram сам шумит, приглушим
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)


async def main() -> None:
    _setup_logging()
    logger = logging.getLogger("bot")

    settings = load_settings()
    logger.info("Settings loaded: chat_id=%s admin_ids=%s", settings.chat_id, settings.admin_ids)

    db = Database(settings.db_path)
    await db.init()
    logger.info("DB initialized at %s", settings.db_path)

    new_account = NewAccountCheck(min_age_days=settings.min_account_age_days)
    flood = FloodCheck(
        db=db,
        max_messages=settings.flood_max_messages,
        window_seconds=settings.flood_window_seconds,
    )
    spam = SpamFilter(patterns=settings.banned_patterns)
    admin = AdminFilter(admin_ids=settings.admin_ids)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    # Мидлварь: инжектим зависимости
    dp.message.middleware(
        DependencyMiddleware(settings, db, new_account, flood, spam, admin)
    )
    dp.callback_query.middleware(
        DependencyMiddleware(settings, db, new_account, flood, spam, admin)
    )
    dp.chat_member.middleware(
        DependencyMiddleware(settings, db, new_account, flood, spam, admin)
    )

    # Порядок важен: админ-команды (в ЛС) → капча → всё остальное.
    # Капча слушает F.new_chat_members, антиспам — обычные сообщения.
    dp.include_router(admin_handlers.router)
    dp.include_router(captcha_handlers.router)
    dp.include_router(messages_handlers.router)

    # Фоновая чистка капч
    sweeper_task = asyncio.create_task(
        captcha_handlers.captcha_sweeper(bot, db, settings),
        name="captcha_sweeper",
    )

    # Корректное завершение
    async def _shutdown() -> None:
        logger.info("Shutting down…")
        sweeper_task.cancel()
        try:
            await sweeper_task
        except asyncio.CancelledError:
            pass
        await bot.session.close()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(_shutdown()))
        except NotImplementedError:
            # Windows: signal handlers нельзя ставить из loop
            pass

    logger.info("Starting polling…")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await _shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        sys.exit(0)
