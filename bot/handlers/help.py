"""/help — inline-меню с командами и настройками.

Работает только в ЛС с ботом. Не-админу — отказ.
"""
from __future__ import annotations

import logging

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.config import Settings
from bot.db import Database

logger = logging.getLogger(__name__)

router = Router(name="help")

# Только в ЛС с ботом
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")


MAIN_MENU_TEXT = (
    "🤖 <b>Бот Модератор — Помощь</b>\n\n"
    "Выберите раздел:"
)

DENIED_TEXT = (
    "🔒 Этот бот — админка для модерации чата.\n"
    "Доступ только для админ-пользователей."
)


def _is_admin(message: types.Message, settings: Settings) -> bool:
    return message.from_user is not None and message.from_user.id in settings.admin_ids


def _main_menu_kb() -> InlineKeyboardBuilder:
    b = InlineKeyboardBuilder()
    b.button(text="🛡 Админ-команды", callback_data="help:admin")
    b.button(text="📊 Статистика",     callback_data="help:stats")
    b.button(text="🔧 Настройки",       callback_data="help:settings")
    b.adjust(2, 1)
    return b


def _back_kb() -> InlineKeyboardBuilder:
    b = InlineKeyboardBuilder()
    b.button(text="← Назад", callback_data="help:back")
    return b


def _stats_kb() -> InlineKeyboardBuilder:
    b = InlineKeyboardBuilder()
    b.button(text="🔄 Обновить",  callback_data="help:stats")
    b.button(text="← Назад",     callback_data="help:back")
    b.adjust(2)
    return b


ADMIN_TEXT = (
    "🛡 <b>Админ-команды</b> (только в ЛС):\n\n"
    "<code>/ban @user [причина]</code>\n"
    "  Бан навсегда. Можно reply на сообщение.\n"
    "  Пример: <code>/ban @spammer реклама</code>\n\n"
    "<code>/tban @user &lt;мин&gt; [причина]</code>\n"
    "  Временный бан. Пример: <code>/tban @user 60 флуд</code>\n\n"
    "<code>/unban @user</code>\n"
    "  Разбанить (или reply).\n\n"
    "<code>/whitelist_add @user</code>\n"
    "  В белый список (мимо всех фильтров).\n\n"
    "<code>/whitelist_del @user</code>\n"
    "  Убрать из белого списка.\n\n"
    "<code>/stats</code>\n"
    "  Статистика по событиям.\n\n"
    "<code>/id</code>\n"
    "  Показать chat.id и user.id (работает в любом чате)."
)


@router.message(Command("help", "start"))
async def cmd_help(message: types.Message, settings: Settings) -> None:
    if not _is_admin(message, settings):
        await message.answer(DENIED_TEXT)
        return
    await message.answer(MAIN_MENU_TEXT, reply_markup=_main_menu_kb().as_markup())


@router.callback_query(F.data.startswith("help:"))
async def help_callback(
    callback: types.CallbackQuery,
    settings: Settings,
    db: Database,
) -> None:
    if callback.from_user is None or callback.from_user.id not in settings.admin_ids:
        await callback.answer("Нет доступа", show_alert=True)
        return

    section = callback.data.split(":", 1)[1]
    if section == "back":
        await callback.message.edit_text(MAIN_MENU_TEXT, reply_markup=_main_menu_kb().as_markup())
    elif section == "admin":
        await callback.message.edit_text(ADMIN_TEXT, reply_markup=_back_kb().as_markup())
    elif section == "stats":
        await _render_stats(callback, db)
    elif section == "settings":
        await _render_settings(callback, settings)
    await callback.answer()


async def _render_stats(callback: types.CallbackQuery, db: Database) -> None:
    s = await db.get_stats()
    text = (
        "📊 <b>Статистика</b>\n\n"
        f"Банов: <b>{s.get('bans', 0)}</b>\n"
        f"Удалённых сообщений: <b>{s.get('deleted', 0)}</b>\n"
        f"Проваленных капч: <b>{s.get('captcha_fails', 0)}</b>\n"
        f"Флуд-варнов: <b>{s.get('flood_warns', 0)}</b>\n"
        f"Всего событий: <b>{s.get('total_events', 0)}</b>"
    )
    await callback.message.edit_text(text, reply_markup=_stats_kb().as_markup())


async def _render_settings(callback: types.CallbackQuery, settings: Settings) -> None:
    patterns = "\n".join(f"  • <code>{p}</code>" for p in settings.banned_patterns) or "  (пусто)"
    text = (
        "🔧 <b>Настройки</b>\n\n"
        f"MIN_ACCOUNT_AGE_DAYS = <b>{settings.min_account_age_days}</b>\n"
        f"FLOOD_MAX_MESSAGES = <b>{settings.flood_max_messages}</b>\n"
        f"FLOOD_WINDOW_SECONDS = <b>{settings.flood_window_seconds}</b>\n"
        f"CAPTCHA_TIMEOUT_SECONDS = <b>{settings.captcha_timeout_seconds}</b>\n"
        f"DB_PATH = <code>{settings.db_path}</code>\n\n"
        f"<b>BANNED_PATTERNS</b> ({len(settings.banned_patterns)}):\n"
        f"{patterns}"
    )
    await callback.message.edit_text(text, reply_markup=_back_kb().as_markup())
