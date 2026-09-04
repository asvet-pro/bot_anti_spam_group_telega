"""Капча на вход в чат.

Логика:
- Когда в чат вступает новый участник — бот шлёт в ЛС (или в чат)
  сообщение с inline-кнопкой "Я не робот" и сохраняет челлендж.
- Если за CAPCHA_TIMEOUT_SECONDS пользователь не нажал — кикаем.
- Если нажал — помечаем как прошедшего.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from aiogram import Bot, F, Router, types
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import texts
from bot.config import Settings
from bot.db import CaptchaChallenge, Database
from bot.filters.admin import AdminFilter
from bot.filters.new_account import NewAccountCheck

logger = logging.getLogger(__name__)

router = Router(name="captcha")

# callback_data префикс
CB_CAPTCHA = "captcha:pass:"


def _make_kb(user_id: int) -> types.InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=texts.CAPTCHA_BUTTON, callback_data=f"{CB_CAPTCHA}{user_id}")
    return kb.as_markup()


@router.message(F.new_chat_members)
async def on_new_members(
    message: types.Message,
    bot: Bot,
    settings: Settings,
    db: Database,
    new_account: NewAccountCheck,
) -> None:
    """Кто-то вступил в чат — выдаём капчу или сразу бан, если аккаунт
    слишком молодой."""
    for user in message.new_chat_members:
        if user.is_bot:
            # Ботам вход запрещён. Кикаем и баним.
            try:
                await bot.ban_chat_member(
                    chat_id=settings.chat_id, user_id=user.id
                )
            except TelegramAPIError as e:
                logger.warning("Не удалось забанить бота %s: %s", user.id, e)
            await message.answer(
                texts.NEW_MEMBER_BANNED.format(
                    name=user.full_name, reason="это бот"
                )
            )
            continue

        if new_account.is_too_young(user):
            await db.ban(
                user_id=user.id,
                chat_id=settings.chat_id,
                reason=f"аккаунт < {settings.min_account_age_days} дней",
                by=None,
            )
            try:
                await bot.ban_chat_member(
                    chat_id=settings.chat_id, user_id=user.id
                )
            except TelegramAPIError as e:
                logger.warning("Не удалось забанить %s: %s", user.id, e)
            await message.answer(
                texts.NEW_MEMBER_BANNED.format(
                    name=user.full_name,
                    reason=f"аккаунт моложе {settings.min_account_age_days} дней",
                )
            )
            continue

        # Шлём капчу в чат (в ЛС она может не дойти).
        text = texts.WELCOME.format(
            name=user.first_name or user.full_name,
            timeout=settings.captcha_timeout_seconds,
        )
        try:
            sent = await message.answer(
                text,
                reply_markup=_make_kb(user.id),
            )
        except TelegramAPIError as e:
            logger.error("Не удалось отправить капчу: %s", e)
            continue

        await db.save_captcha(
            CaptchaChallenge(
                user_id=user.id,
                chat_id=message.chat.id,
                message_id=sent.message_id,
                deadline=time.time() + settings.captcha_timeout_seconds,
            )
        )


@router.callback_query(F.data.startswith(CB_CAPTCHA))
async def on_captcha_pass(
    callback: types.CallbackQuery,
    bot: Bot,
    settings: Settings,
    db: Database,
) -> None:
    """Пользователь нажал кнопку "Я не робот"."""
    try:
        target_id = int(callback.data[len(CB_CAPTCHA):])
    except ValueError:
        await callback.answer("Ошибка кнопки.", show_alert=True)
        return

    if callback.from_user.id != target_id:
        await callback.answer("Эта кнопка не для тебя.", show_alert=True)
        return

    challenge = await db.get_captcha(target_id)
    if not challenge:
        await callback.answer("Капча уже пройдена или истекла.", show_alert=True)
        return

    # Удаляем кнопку-сообщение
    try:
        await bot.delete_message(
            chat_id=challenge.chat_id, message_id=challenge.message_id
        )
    except TelegramAPIError:
        pass

    await db.delete_captcha(target_id)
    await callback.answer(texts.CAPTCHA_PASSED, show_alert=False)
    logger.info("Captcha passed: user=%s chat=%s", target_id, challenge.chat_id)


async def captcha_sweeper(bot: Bot, db: Database, settings: Settings) -> None:
    """Фоновая задача: раз в 10 сек проверяет истёкшие капчи и кикает."""
    while True:
        try:
            expired = await db.get_expired_captchas()
            for c in expired:
                try:
                    # Пытаемся кикнуть
                    await bot.ban_chat_member(
                        chat_id=c.chat_id, user_id=c.user_id
                    )
                    # и сразу разбанить (unban), чтобы только кикнуть
                    await bot.unban_chat_member(
                        chat_id=c.chat_id, user_id=c.user_id
                    )
                except TelegramForbiddenError:
                    logger.warning(
                        "Нет прав кикать user_id=%s — добавь бота админом",
                        c.user_id,
                    )
                except TelegramRetryAfter as e:
                    await asyncio.sleep(e.retry_after + 1)
                except TelegramAPIError as e:
                    logger.warning(
                        "Ошибка при кике user_id=%s: %s", c.user_id, e
                    )

                # Прячем кнопку
                try:
                    await bot.edit_message_reply_markup(
                        chat_id=c.chat_id,
                        message_id=c.message_id,
                        reply_markup=None,
                    )
                except TelegramAPIError:
                    pass

                await db.delete_captcha(c.user_id)
                await db.inc("captcha_fails")
                logger.info("Captcha expired/kicked: user=%s", c.user_id)
        except Exception as e:  # noqa: BLE001 — задача крутится в фоне
            logger.exception("captcha_sweeper: %s", e)
        await asyncio.sleep(10)
