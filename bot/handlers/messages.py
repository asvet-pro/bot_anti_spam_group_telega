"""Обработка входящих сообщений: ML + blocklist (слова/regex) + legacy regex + антифлуд.

Порядок проверок:
1. Только защищаемый чат.
2. Админы/вайтлист — мимо.
3. ML-фильтр (если включён).
4. Стоп-лист слов (управляется через /help).
5. Стоп-лист regex (управляется через /help).
6. Legacy BANNED_PATTERNS из .env (обратная совместимость).
7. Антифлуд.

Режим REVIEW (мягкий): вместо немедленного удаления подозрительное сообщение
уходит админу в личку с кнопками «Спам / Ок / В стоп-лист». Так собирается
датасет для будущего обучения модели. Если REVIEW выключен — удаляем сразу.

Тип срабатывания передаётся в review_handler, чтобы админ увидел причину.
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
    duplicate: DuplicateCheck,
    spam: SpamFilter,
    admin: AdminFilter,
    ml_spam: MLSpamCheck,
    blocklist: BlocklistFilter,
) -> None:
    # Только в защищаемый чат
    if message.chat.id != settings.chat_id:
        return
    if not message.from_user:
        return

    user_id = message.from_user.id

    # Админы (в т.ч. админы самой группы) и вайтлист — мимо фильтров.
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
                "ML hit user=%s label=%s score=%.3f text=%r",
                user_id, top_label, top_score, payload[:200],
            )
            if await _maybe_review(
                message, bot, db, settings, user_id, payload, reason, kind="ml"
            ):
                return
            await _delete_and_notify(message, db, user_id, reason)
            return

    # 2) Управляемый стоп-лист: слова.
    word_hit = await blocklist.match_word(payload)
    if word_hit:
        reason = f'стоп-слово «{word_hit}»'
        logger.info(
            "Word-blocklist hit user=%s word=%r text=%r",
            user_id, word_hit, payload[:200],
        )
        if await _maybe_review(
            message, bot, db, settings, user_id, payload, reason, kind="word",
            hint=word_hit,
        ):
            return
        await _delete_and_notify(message, db, user_id, reason)
        return

    # 3) Управляемый стоп-лист: regex.
    pattern_hit = await blocklist.match_pattern(payload)
    if pattern_hit:
        reason = f"стоп-regex `{pattern_hit.pattern}`"
        logger.info(
            "Pattern-blocklist hit user=%s pattern=%r text=%r",
            user_id, pattern_hit.pattern, payload[:200],
        )
        if await _maybe_review(
            message, bot, db, settings, user_id, payload, reason, kind="pattern",
            hint=pattern_hit.pattern,
        ):
            return
        await _delete_and_notify(message, db, user_id, reason)
        return

    # 4) Legacy BANNED_PATTERNS из .env (обратная совместимость).
    if spam.enabled:
        hit = spam.match(payload)
        if hit:
            reason = f"паттерн `{hit.pattern}`"
            logger.info(
                "Legacy regex hit user=%s pattern=%r text=%r",
                user_id, hit.pattern, payload[:200],
            )
            if await _maybe_review(
                message, bot, db, settings, user_id, payload, reason, kind="legacy",
                hint=hit.pattern,
            ):
                return
            await _delete_and_notify(message, db, user_id, reason)
            return

    # 5) Дубликаты: одно и то же сообщение подряд.
    #    Самый частый вид спама — «реклама», продублированная 10 раз.
    #    Антифлуд это не ловит (5 одинаковых сообщений = ровно лимит).
    dup_hit, dup_count = await duplicate.is_duplicate(
        user_id, message.chat.id, payload
    )
    if dup_hit:
        reason = f"повтор сообщения ({dup_count}x)"
        logger.info(
            "Duplicate delete user=%s count=%s text=%r",
            user_id, dup_count, payload[:120],
        )
        if await _maybe_review(
            message, bot, db, settings, user_id, payload, reason, kind="duplicate",
        ):
            return
        await _delete_and_notify(message, db, user_id, reason)
        return

    # 6) Антифлуд: сообщение удаляем, но НЕ баним автоматически —
    #    единственный работающий фильтр не должен резать живых людей.
    count = await flood.check(user_id, message.chat.id)
    if flood.is_flood(count):
        logger.info("Flood: user=%s count=%s", user_id, count)
        try:
            await message.delete()
        except TelegramAPIError:
            pass
        await db.inc("flood_warns")
        try:
            await message.answer(texts.FLOOD_WARNING)
        except TelegramAPIError:
            pass
        return


async def _maybe_review(
    message: types.Message,
    bot: Bot,
    db: Database,
    settings: Settings,
    user_id: int,
    payload: str,
    reason: str,
    *,
    kind: str,
    hint: str | None = None,
) -> bool:
    """Мягкий режим: отправляет подозрительное сообщение админу на разбор.

    True — сообщение ушло на разбор (дальше обработка не идёт).
    False — режим выключен или не удалось отправить (удаляем как раньше).
    """
    if not settings.review_mode:
        return False

    added = await db.add_pending_review(
        msg_id=message.message_id,
        user_id=user_id,
        chat_id=message.chat.id,
        text=payload,
        reason=reason,
        blocklist_hint=hint,
    )
    if not added:
        # Уже в очереди на разбор — не дублируем.
        return True

    # Извлекаем «слово-подсказку» для кнопки «В стоп-лист».
    suggestion = hint if kind in ("word", "pattern") else _guess_hint(payload)

    sent = False
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(
                admin_id,
                texts.REVIEW_HEADER.format(
                    user_id=user_id,
                    reason=reason,
                    text=_truncate(payload),
                ),
                reply_markup=review_keyboard(message.message_id, suggestion),
            )
            sent = True
        except TelegramAPIError as e:
            logger.warning("Не удалось отправить на разбор админу %s: %s", admin_id, e)

    if not sent:
        logger.warning(
            "Не удалось отправить сообщение на разбор — удаляю сразу. "
            "Проверь, что бот может писать админам в личку."
        )
        return False

    logger.info("Review requested: msg_id=%s user=%s reason=%s", message.message_id, user_id, reason)
    return True


def review_keyboard(msg_id: int, suggestion: str | None) -> types.InlineKeyboardMarkup:
    """Кнопки разметки: Спам / Ок / В стоп-лист."""
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    kb = InlineKeyboardBuilder()
    kb.button(text=texts.REVIEW_BTN_SPAM, callback_data=f"review:spam:{msg_id}")
    kb.button(text=texts.REVIEW_BTN_OK, callback_data=f"review:ok:{msg_id}")
    if suggestion:
        kb.button(
            text=texts.REVIEW_BTN_BLOCKLIST,
            callback_data=f"review:block:{msg_id}",
        )
    kb.adjust(1, 1)
    return kb.as_markup()


def _guess_hint(text: str) -> str | None:
    """Пытается предположить стоп-слово для кнопки «В стоп-лист».

    Берём самое «подозрительное» слово: длинное слово с заглавной буквы или
    со знаком препинания. Если ничего не нашли — None.
    """
    import re

    words = re.findall(r"[A-Za-zА-Яа-яЁё]{4,}", text)
    if not words:
        return None
    # Предпочитаем слова с заглавной буквы (часто — бренды/услуги).
    caps = [w for w in words if w[0].isupper()]
    if caps:
        # Самое длинное из «капитализированных» — вероятный бренд.
        return max(caps, key=len).lower()
    return max(words, key=len).lower()


def _truncate(text: str, limit: int = 800) -> str:
    """Обрезает текст для показа в личке и экранирует HTML."""
    if len(text) > limit:
        text = text[:limit] + "…"
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )
