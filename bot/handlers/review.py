"""Разметка подозрительных сообщений админом + управление банами.

Бот ловит спам автоматически (удаляет сообщение и банит автора на 24 часа),
и сообщает об этом админу в личку с кнопками:
  🚫 Спам           — подтвердить: удалить (если ещё не удалено) + записать как спам
  ✅ Ок             — оставить, записать как норма
  ⏭ В стоп-лист    — удалить и добавить текст в стоп-лист
  🔨 БАН навсегда   — вечный бан (обычно бот уже забанил на 24ч)
  ↩️ Снять бан      — снять автоматический бан 24ч

Записи попадают в таблицу spam_reports — из них потом обучается модель.
"""
from __future__ import annotations

import logging

from aiogram import F, Router, types
from aiogram.exceptions import TelegramAPIError

from bot import texts
from bot.config import Settings
from bot.db import Database
from bot.filters.blocklist import BlocklistFilter
from bot.moderation import Moderation

logger = logging.getLogger(__name__)

router = Router(name="review")

CB_PREFIX = "review:"


def review_keyboard(
    msg_id: int, suggestion: str | None, state: dict | None = None,
) -> types.InlineKeyboardMarkup:
    """Кнопки разметки.

    state — словарь с ключами deleted/action: если сообщение уже удалено
    и автор уже забанен, кнопку «Бан навсегда» не показываем.
    """
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    state = state or {}
    kb = InlineKeyboardBuilder()
    kb.button(text=texts.REVIEW_BTN_SPAM, callback_data=f"review:spam:{msg_id}")
    kb.button(text=texts.REVIEW_BTN_OK, callback_data=f"review:ok:{msg_id}")
    if suggestion:
        kb.button(
            text=texts.REVIEW_BTN_BLOCKLIST,
            callback_data=f"review:block:{msg_id}",
        )
    # «БАН навсегда» имеет смысл, если бот ещё не поставил вечный бан.
    if state.get("action") != "permanent_ban":
        kb.button(
            text=texts.REVIEW_BTN_PERMANENT,
            callback_data=f"review:permanent:{msg_id}",
        )
    kb.button(text=texts.REVIEW_BTN_UNBAN, callback_data=f"review:unban:{msg_id}")
    kb.adjust(1, 1, 1)
    return kb.as_markup()


def _footer(callback: types.CallbackQuery, extra: str) -> str:
    """Текст уведомления + строка результата."""
    return (callback.message.text or "") + "\n\n" + extra


@router.callback_query(F.data.startswith(CB_PREFIX))
async def on_review(
    callback: types.CallbackQuery,
    settings: Settings,
    db: Database,
    blocklist: BlocklistFilter,
    moderation: Moderation,
) -> None:
    """Админ нажал кнопку разметки."""
    if callback.from_user is None or callback.from_user.id not in settings.admin_ids:
        await callback.answer(texts.REVIEW_NOT_ADMIN, show_alert=True)
        return

    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Битая кнопка.", show_alert=True)
        return

    action = parts[1]
    try:
        msg_id = int(parts[2])
    except ValueError:
        await callback.answer("Битый id.", show_alert=True)
        return

    pending = await db.get_pending_review(msg_id)
    if pending is None:
        await callback.answer(texts.REVIEW_NOT_FOUND, show_alert=True)
        return

    user_id = pending["user_id"]
    chat_id = pending["chat_id"]

    # --- ✅ Ок: оставляем, помечаем как норма ---
    if action == "ok":
        await db.resolve_pending_review(msg_id, verdict="ok")
        await db.inc("reviews_ok")
        await callback.message.edit_text(
            _footer(callback, texts.REVIEW_DONE_OK)
        )
        await callback.answer("Отмечено: ок")
        return

    # --- 🚫 Спам: удаляем (если ещё не удалено) + помечаем как спам ---
    if action == "spam":
        await _try_delete(callback.bot, chat_id, msg_id)
        await db.log_deleted(msg_id, user_id, chat_id)
        await db.inc("deleted")
        await db.resolve_pending_review(msg_id, verdict="spam")
        await db.inc("reviews_spam")
        await callback.message.edit_text(
            _footer(callback, texts.REVIEW_DONE_SPAM)
        )
        await callback.answer("Удалено и помечено как спам")
        return

    # --- ⏭ В стоп-лист: удаляем + добавляем текст в стоп-лист ---
    if action == "block":
        hint = pending.get("blocklist_hint")
        await _try_delete(callback.bot, chat_id, msg_id)
        await db.log_deleted(msg_id, user_id, chat_id)
        await db.inc("deleted")

        added = False
        if hint:
            try:
                await db.add_word(hint, note="из разметки")
                blocklist.invalidate()
                added = True
            except Exception as e:  # noqa: BLE001 — IntegrityError и прочее
                logger.warning("Не удалось добавить в стоп-лист %r: %s", hint, e)

        await db.resolve_pending_review(msg_id, verdict="spam")
        await db.inc("reviews_spam")
        await callback.message.edit_text(
            _footer(
                callback,
                texts.REVIEW_DONE_BLOCKLIST if added else texts.REVIEW_DONE_SPAM,
            )
        )
        await callback.answer("Удалено" + (" и добавлено в стоп-лист" if added else ""))
        return

    # --- 🔨 БАН навсегда ---
    if action == "permanent":
        ok = await moderation.ban_user(
            callback.bot, user_id, chat_id,
            reason="вечный бан (решение админа)",
            action="permanent_ban",
            by=callback.from_user.id,
        )
        if not ok:
            await callback.answer(texts.REVIEW_BAN_BLOCKED, show_alert=True)
            return
        await db.inc("permanent_bans")
        await db.resolve_pending_review(msg_id, verdict="spam")
        await callback.message.edit_text(
            _footer(callback, texts.REVIEW_DONE_PERMANENT)
        )
        await callback.answer("Забанен навсегда")
        return

    # --- ↩️ Снять бан ---
    if action == "unban":
        # Проверяем, что бан вообще есть — иначе кнопка бессмысленна.
        if not await db.is_banned(user_id, chat_id):
            await callback.answer("Активного бана нет.", show_alert=True)
            return
        await moderation.unban_user(callback.bot, user_id, chat_id)
        await db.inc("unbans")
        await callback.message.edit_text(
            _footer(callback, texts.REVIEW_DONE_UNBAN)
        )
        await callback.answer("Бан снят")
        return

    await callback.answer("Неизвестное действие.", show_alert=True)


async def _try_delete(bot, chat_id: int, msg_id: int) -> bool:
    """Пытается удалить сообщение. False — не удалось (уже удалено/нет прав)."""
    try:
        await bot.delete_message(chat_id=chat_id, message_id=msg_id)
        return True
    except TelegramAPIError as e:
        logger.warning(
            "Не удалось удалить сообщение %s в чате %s: %s", msg_id, chat_id, e
        )
        return False
