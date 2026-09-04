"""Обработка входящих сообщений: антифлуд + спам-фильтр."""
from __future__ import annotations

import logging
import time

from aiogram import Bot, F, Router, types
from aiogram.exceptions import TelegramAPIError

from bot import texts
from bot.config import Settings
from bot.db import Database
from bot.filters.admin import AdminFilter
from bot.filters.flood import FloodCheck
from bot.filters.spam import SpamFilter

logger = logging.getLogger(__name__)

router = Router(name="messages")

# Команды обрабатываются отдельным роутером, чтобы фильтры не сработали.
router.message.filter(~F.text.startswith("/"))


@router.message()
async def on_message(
    message: types.Message,
    bot: Bot,
    settings: Settings,
    db: Database,
    flood: FloodCheck,
    spam: SpamFilter,
    admin: AdminFilter,
) -> None:
    # Только в защищаемом чате
    if message.chat.id != settings.chat_id:
        return
    if not message.from_user:
        return

    user_id = message.from_user.id

    # Админы и вайтлист — мимо фильтров
    if admin.is_admin(message.from_user) or await db.is_whitelisted(user_id):
        return

    # Системные сообщения (вступил/вышел) — мимо
    if not message.text and not message.caption:
        return

    payload = message.text or message.caption or ""

    # 1) Спам-фильтр
    if spam.enabled:
        hit = spam.match(payload)
        if hit:
            try:
                await message.delete()
            except TelegramAPIError as e:
                logger.warning("Не удалось удалить спам-сообщение: %s", e)
            await db.log_deleted(message.message_id, user_id, message.chat.id)
            await db.inc("deleted")
            try:
                await message.answer(
                    texts.SPAM_DELETED.format(reason=f"паттерн `{hit.pattern}`")
                )
            except TelegramAPIError:
                pass
            return

    # 2) Антифлуд
    count = await flood.check(user_id, message.chat.id)
    if flood.is_flood(count):
        # Только бан на 5 минут, чтобы не злить живых людей
        until = time.time() + 5 * 60
        await db.ban(
            user_id=user_id,
            chat_id=message.chat.id,
            reason="флуд",
            by=None,
            until=until,
        )
        try:
            await message.delete()
        except TelegramAPIError:
            pass
        try:
            await message.answer(
                texts.FLOOD_BAN.format(minutes=5)
            )
        except TelegramAPIError:
            pass
        await db.inc("flood_warns")
        try:
            await bot.ban_chat_member(
                chat_id=message.chat.id,
                user_id=user_id,
                until_date=int(until),
            )
        except TelegramAPIError as e:
            logger.warning("Не удалось забанить за флуд: %s", e)
        return
