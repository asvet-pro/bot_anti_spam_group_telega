"""Обработка входящих сообщений: ML + blocklist (слова/regex) + legacy regex + антифлуд.

Порядок проверок:
1. Только защищаемый чат.
2. Админы/вайтлист — мимо.
3. ML-фильтр (если включён) → удалить.
4. Стоп-лист слов (управляется через /help) → удалить.
5. Стоп-лист regex (управляется через /help) → удалить.
6. Legacy BANNED_PATTERNS из .env → удалить (обратная совместимость).
7. Антифлуд (если дошёл — бан на 5 мин).
"""
from __future__ import annotations

import asyncio
import logging
import time

from aiogram import Bot, F, Router, types
from aiogram.exceptions import TelegramAPIError

from bot import texts
from bot.config import Settings
from bot.db import Database
from bot.filters.admin import AdminFilter
from bot.filters.blocklist import BlocklistFilter
from bot.filters.flood import FloodCheck
from bot.filters.ml_spam import MLSpamCheck
from bot.filters.spam import SpamFilter

logger = logging.getLogger(__name__)

router = Router(name="messages")

# Команды обрабатываются отдельным роутером, чтобы фильтры не сработали.
router.message.filter(~F.text.startswith("/"))


async def _delete_and_notify(
    message: types.Message,
    db: Database,
    user_id: int,
    reason: str,
) -> None:
    """Удаляет сообщение, логирует в БД, шлёт короткое объяснение в чат."""
    try:
        await message.delete()
    except TelegramAPIError as e:
        logger.warning("Не удалось удалить сообщение: %s", e)
    await db.log_deleted(message.message_id, user_id, message.chat.id)
    await db.inc("deleted")
    try:
        await message.answer(texts.SPAM_DELETED.format(reason=reason))
    except TelegramAPIError:
        pass


@router.message()
async def on_message(
    message: types.Message,
    bot: Bot,
    settings: Settings,
    db: Database,
    flood: FloodCheck,
    spam: SpamFilter,
    admin: AdminFilter,
    ml_spam: MLSpamCheck,
    blocklist: BlocklistFilter,
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

    # 1) ML-фильтр (самое «умное» — сначала).
    if ml_spam.enabled:
        ml_hit, ml_pred = await asyncio.to_thread(ml_spam.is_spam, payload)
        if ml_hit and ml_pred is not None:
            top_label, top_score = max(
                ml_pred.all_scores.items(), key=lambda kv: kv[1]
            )
            reason = f"ML {top_label}={top_score:.2f}"
            logger.info(
                "ML delete user=%s label=%s score=%.3f text=%r",
                user_id, top_label, top_score, payload[:200],
            )
            await _delete_and_notify(message, db, user_id, reason)
            return

    # 2) Управляемый стоп-лист: слова.
    word_hit = await blocklist.match_word(payload)
    if word_hit:
        reason = f'стоп-слово «{word_hit}»'
        logger.info(
            "Word-blocklist delete user=%s word=%r text=%r",
            user_id, word_hit, payload[:200],
        )
        await _delete_and_notify(message, db, user_id, reason)
        return

    # 3) Управляемый стоп-лист: regex.
    pattern_hit = await blocklist.match_pattern(payload)
    if pattern_hit:
        reason = f"стоп-regex `{pattern_hit.pattern}`"
        logger.info(
            "Pattern-blocklist delete user=%s pattern=%r text=%r",
            user_id, pattern_hit.pattern, payload[:200],
        )
        await _delete_and_notify(message, db, user_id, reason)
        return

    # 4) Legacy BANNED_PATTERNS из .env (обратная совместимость).
    if spam.enabled:
        hit = spam.match(payload)
        if hit:
            reason = f"паттерн `{hit.pattern}`"
            logger.info(
                "Legacy regex delete user=%s pattern=%r text=%r",
                user_id, hit.pattern, payload[:200],
            )
            await _delete_and_notify(message, db, user_id, reason)
            return

    # 5) Антифлуд
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
