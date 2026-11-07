"""Точка входа: поднимает бота, регистрирует роутеры, запускает polling."""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.utils.backoff import BackoffConfig

from bot import proxy
from bot.config import load_settings
from bot.db import Database
from bot.filters.admin import AdminFilter
from bot.filters.blocklist import BlocklistFilter
from bot.filters.duplicate import DuplicateCheck
from bot.filters.flood import FloodCheck
from bot.filters.ml_spam import DEFAULT_BAD_LABELS, MLSpamCheck
from bot.filters.new_account import NewAccountCheck
from bot.filters.spam import SpamFilter
from bot.handlers import admin as admin_handlers
from bot.handlers import blocklist as blocklist_handlers
from bot.handlers import captcha as captcha_handlers
from bot.handlers import help as help_handlers
from bot.handlers import messages as messages_handlers
from bot.handlers import review as review_handlers
from bot.middlewares.deps import DependencyMiddleware
from bot.moderation import Moderation
from bot.model_runtime import MLSpamClassifier


# Auto-deploy via Coolify webhook (configured 2026-09-04).

# Короткий polling-цикл: getUpdates держим 20 секунд вместо дефолтных 30+,
# при обрыве ждём 2 секунды и забираем накопленное. Сообщения не теряются —
# Telegram хранит их в очереди до 24 часов, — но «окно слепоты» после
# обрыва сокращается с минут до секунд.
POLL_TIMEOUT = 20

# Мягкий backoff: раньше aiogram накапливал tryings до 16 и подвисал на
# минуты при обрыве. Теперь ограничиваем паузу 10 секундами.
POLL_BACKOFF = BackoffConfig(min_delay=1.0, max_delay=10.0, factor=1.5, jitter=0.2)


def resolve_allowed_updates(dp: Dispatcher) -> list[str]:
    """Список типов апдейтов, которые бот просит у Telegram.

    Гарантируем наличие chat_member — без него не работает капча на вход.
    """
    resolved = set(dp.resolve_used_update_types())
    resolved.add("message")
    resolved.add("chat_member")
    resolved.add("callback_query")
    return sorted(resolved)


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

    # Если задан VLESS_URL — поднимаем локальный SOCKS5 через sing-box.
    #
    # Важно: прокси НЕ обязателен. Если Telegram доступен напрямую с сервера —
    # работаем без прокси (прямое соединение надёжнее: нет лишнего hop и
    # длинных TCP-соединений, которые рвутся по таймауту). Прокси
    # включается только как фолбэк: сначала пробуем direct, при неудаче —
    # поднимаем VLESS.
    use_proxy = False
    vless_url = proxy.vless_url_from_env()
    singbox_proc: asyncio.subprocess.Process | None = None
    session: AiohttpSession | None = None

    direct_ok = await proxy.telegram_reachable_direct()
    if direct_ok:
        logger.info("Telegram доступен напрямую — прокси не используем.")
    elif vless_url:
        logger.info("Прямой доступ к Telegram недоступен, включаю VLESS-прокси.")
        singbox_proc = await proxy.start_singbox(vless_url)
        # aiogram 3.x принимает proxy как строку; aiohttp-socks добавит поддержку SOCKS5.
        session = AiohttpSession(
            proxy=proxy.socks_url(),
            timeout=proxy.SESSION_TIMEOUT,
        )
        use_proxy = True
        logger.info("Bot will use SOCKS5 proxy: %s", proxy.socks_url())
    else:
        logger.warning(
            "Прямой доступ к Telegram недоступен и VLESS_URL не задан — "
            "бот будет пытаться ходить напрямую."
        )

    if session is None:
        session = AiohttpSession(timeout=proxy.SESSION_TIMEOUT)

    db = Database(settings.db_path)
    await db.init()
    logger.info("DB initialized at %s", settings.db_path)

    new_account = NewAccountCheck(min_age_days=settings.min_account_age_days)
    flood = FloodCheck(
        db=db,
        max_messages=settings.flood_max_messages,
        window_seconds=settings.flood_window_seconds,
    )
    duplicate = DuplicateCheck(
        db=db,
        max_repeats=settings.duplicate_max_repeats,
        window_seconds=settings.duplicate_window_seconds,
    )
    spam = SpamFilter(patterns=settings.banned_patterns)
    admin = AdminFilter(admin_ids=settings.admin_ids)

    # ML-фильтр: загружаем модель, если включено. При любой ошибке —
    # переходим в режим «только regex», не валим процесс.
    ml_classifier: MLSpamClassifier | None = None
    if settings.ml_enabled:
        if settings.ml_model_dir.exists():
            try:
                ml_classifier = MLSpamClassifier(settings.ml_model_dir)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "Не удалось загрузить ML модель из %s: %s. "
                    "Работаем только по regex-паттернам.",
                    settings.ml_model_dir, e,
                )
        else:
            logger.warning(
                "ML включён, но каталог %s не найден. "
                "Запусти scripts/export_onnx.py или собери Docker-образ. "
                "Работаем только по regex-паттернам.",
                settings.ml_model_dir,
            )
    else:
        logger.info("ML выключен (ML_ENABLED=false). Только regex.")

    bad_labels = settings.ml_bad_labels or DEFAULT_BAD_LABELS
    ml_spam = MLSpamCheck(
        classifier=ml_classifier,
        threshold=settings.ml_threshold,
        bad_labels=bad_labels,
    )
    blocklist = BlocklistFilter(db=db)
    moderation = Moderation(db=db, admin=admin, settings=settings)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        session=session,
    )
    dp = Dispatcher()

    # Мидлварь: инжектим зависимости
    dp.message.middleware(
        DependencyMiddleware(
            settings, db, new_account, flood, duplicate, spam, admin, ml_spam, blocklist, moderation
        )
    )
    dp.callback_query.middleware(
        DependencyMiddleware(
            settings, db, new_account, flood, duplicate, spam, admin, ml_spam, blocklist, moderation
        )
    )
    dp.chat_member.middleware(
        DependencyMiddleware(
            settings, db, new_account, flood, duplicate, spam, admin, ml_spam, blocklist, moderation
        )
    )

    # Порядок важен: /help и /start (ЛС) → админ-команды + blocklist (ЛС) → капча → всё остальное.
    # Капча слушает F.new_chat_members, антиспам — обычные сообщения.
    dp.include_router(help_handlers.router)
    dp.include_router(admin_handlers.router)
    dp.include_router(blocklist_handlers.router)
    dp.include_router(review_handlers.router)
    dp.include_router(captcha_handlers.router)
    dp.include_router(messages_handlers.router)

    # Фоновая чистка капч
    sweeper_task = asyncio.create_task(
        captcha_handlers.captcha_sweeper(bot, db, settings),
        name="captcha_sweeper",
    )

    # Периодически подтягиваем список админов группы: админ из группы,
    # которого нет в ADMIN_IDS, тоже не должен попадать под фильтры.
    async def _admins_watcher() -> None:
        while True:
            try:
                await admin.ensure_group_admins(bot, settings.chat_id)
            except Exception as e:  # noqa: BLE001
                logger.warning("admins_watcher: %s", e)
            await asyncio.sleep(300)

    admins_task = asyncio.create_task(_admins_watcher(), name="admins_watcher")

    # Корректное завершение
    async def _shutdown() -> None:
        logger.info("Shutting down…")
        sweeper_task.cancel()
        admins_task.cancel()
        for t in (sweeper_task, admins_task):
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        await bot.session.close()
        if singbox_proc is not None:
            await proxy.stop_singbox(singbox_proc)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(_shutdown()))
        except NotImplementedError:
            # Windows: signal handlers нельзя ставить из loop
            pass

    logger.info("Starting polling…")
    # allowed_updates: важно, чтобы сюда входил chat_member — без него бот
    # НЕ получает событие «новый участник вступил», и капча не выдаётся
    # вообще (в проде allowed_updates был ["message","callback_query"]).
    allowed_updates = resolve_allowed_updates(dp)
    logger.info("allowed_updates = %s", allowed_updates)
    try:
        await dp.start_polling(
            bot,
            allowed_updates=allowed_updates,
            timeout=POLL_TIMEOUT,
            backoff_config=POLL_BACKOFF,
        )
    finally:
        await _shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        sys.exit(0)
