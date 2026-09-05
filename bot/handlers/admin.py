"""Админ-команды.

Используются ТОЛЬКО в ЛС с ботом (чтобы не палить список админов в чате).
"""
from __future__ import annotations

import logging
import time

from aiogram import Bot, F, Router, types
from aiogram.filters import Command
from aiogram.exceptions import TelegramAPIError

from bot import texts
from bot.config import Settings
from bot.db import Database
from bot.filters.admin import AdminFilter

logger = logging.getLogger(__name__)

router = Router(name="admin")

# Команды только в личке с ботом
router.message.filter(F.chat.type == "private")


def _is_admin(message: types.Message, admin: AdminFilter) -> bool:
    return admin.is_admin(message.from_user)


@router.message(Command("ban"))
async def cmd_ban(
    message: types.Message, bot: Bot, settings: Settings,
    db: Database, admin: AdminFilter,
) -> None:
    if not _is_admin(message, admin):
        return
    target, reason = _parse_target_and_rest(message)
    if not target:
        await message.reply("Формат: /ban @user или reply на сообщение. Причина опционально.")
        return
    await db.ban(target, settings.chat_id, reason, by=message.from_user.id)
    try:
        await bot.ban_chat_member(chat_id=settings.chat_id, user_id=target)
    except TelegramAPIError as e:
        await message.reply(f"⚠️ Записал в базу, но не смог кикнуть в Telegram: {e}")
        return
    await message.reply(texts.ADMIN_USER_BANNED.format(name=f"id={target}", user_id=target))


@router.message(Command("tban"))
async def cmd_tban(
    message: types.Message, bot: Bot, settings: Settings,
    db: Database, admin: AdminFilter,
) -> None:
    if not _is_admin(message, admin):
        return
    # /tban @user 30 причина
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await message.reply("Формат: /tban @user <минуты> <причина>")
        return
    target = await _resolve_user(bot, parts[1])
    try:
        minutes = int(parts[2].split(maxsplit=1)[0])
    except ValueError:
        await message.reply("Минуты должны быть числом.")
        return
    reason = parts[2].split(maxsplit=1)[1] if " " in parts[2] else "tban"
    if not target:
        await message.reply("Не смог определить user_id.")
        return

    until = time.time() + minutes * 60
    await db.ban(target, settings.chat_id, reason, by=message.from_user.id, until=until)
    try:
        await bot.ban_chat_member(
            chat_id=settings.chat_id, user_id=target, until_date=int(until)
        )
    except TelegramAPIError as e:
        await message.reply(f"⚠️ Записал в базу, но не смог кикнуть: {e}")
        return
    await message.reply(f"⏱ {target} забанен на {minutes} мин.")


@router.message(Command("unban"))
async def cmd_unban(
    message: types.Message, bot: Bot, settings: Settings,
    db: Database, admin: AdminFilter,
) -> None:
    if not _is_admin(message, admin):
        return
    target, _ = _parse_target_and_rest(message)
    if not target:
        await message.reply("Формат: /unban @user или reply.")
        return
    await db.unban(target, settings.chat_id)
    try:
        await bot.unban_chat_member(chat_id=settings.chat_id, user_id=target, only_if_banned=True)
    except TelegramAPIError as e:
        logger.warning("unban tg: %s", e)
    await message.reply(texts.ADMIN_USER_UNBANNED.format(name=f"id={target}", user_id=target))


@router.message(Command("whitelist_add"))
async def cmd_wl_add(
    message: types.Message, bot: Bot, db: Database, admin: AdminFilter,
) -> None:
    if not _is_admin(message, admin):
        return
    target, _ = _parse_target_and_rest(message)
    if not target:
        await message.reply("Формат: /whitelist_add @user или reply.")
        return
    await db.add_whitelist(target)
    await message.reply(f"➕ id={target} добавлен в белый список.")


@router.message(Command("whitelist_del"))
async def cmd_wl_del(
    message: types.Message, bot: Bot, db: Database, admin: AdminFilter,
) -> None:
    if not _is_admin(message, admin):
        return
    target, _ = _parse_target_and_rest(message)
    if not target:
        await message.reply("Формат: /whitelist_del @user или reply.")
        return
    await db.remove_whitelist(target)
    await message.reply(f"➖ id={target} убран из белого списка.")


@router.message(Command("stats"))
async def cmd_stats(
    message: types.Message, db: Database, admin: AdminFilter,
) -> None:
    if not _is_admin(message, admin):
        return
    s = await db.get_stats()
    await message.reply(
        texts.ADMIN_STATS.format(
            total=s.get("total_events", 0),
            bans=s.get("bans", 0),
            deleted=s.get("deleted", 0),
            captcha_fails=s.get("captcha_fails", 0),
            flood_warns=s.get("flood_warns", 0),
        )
    )


@router.message(Command("id"))
async def cmd_id(
    message: types.Message, settings: Settings, admin: AdminFilter,
) -> None:
    if not _is_admin(message, admin):
        return
    await message.reply(
        f"chat_id = {message.chat.id}\n"
        f"user_id = {message.from_user.id}\n"
        f"CHAT_ID в .env = {settings.chat_id}"
    )


# ---------- helpers ----------


def _parse_target_and_rest(message: types.Message) -> tuple[int | None, str]:
    """Парсит /ban @username reason или /ban (reply) reason."""
    text = (message.text or "").split(maxsplit=1)
    if len(text) < 2:
        return None, ""
    arg = text[1]
    reason = ""
    if " " in arg:
        arg, reason = arg.split(maxsplit=1)
    # arg может быть @username, числом или ничего (если reply)
    target_id: int | None = None
    if message.reply_to_message and message.reply_to_message.from_user:
        target_id = message.reply_to_message.from_user.id
    elif arg.startswith("@"):
        # aiogram 3 умеет резолвить username через getChat, но без токена
        # это не сработает. Поэтому — отдельная команда с числом id.
        # Здесь оставим None, чтобы юзер использовал /id для уточнения.
        target_id = None
    else:
        try:
            target_id = int(arg)
        except ValueError:
            target_id = None
    return target_id, reason


async def _resolve_user(bot: Bot, arg: str) -> int | None:
    """Пробует получить user_id из @username или числа."""
    if arg.startswith("@"):
        try:
            chat = await bot.get_chat(arg)
            return chat.id
        except TelegramAPIError:
            return None
    try:
        return int(arg)
    except ValueError:
        return None
