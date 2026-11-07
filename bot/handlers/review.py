"""Разметка подозрительных сообщений админом.

Бот в мягком режиме (REVIEW_MODE=true) не удаляет подозрительное сообщение,
а отправляет его админу в личку с кнопками:
  🚫 Спам (удалить)  — удалить в чате, записать как спам
  ✅ Ок (оставить)   — оставить в чате, записать как норма
  ⏭ В стоп-лист     — удалить и добавить текст в стоп-лист

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

logger = logging.getLogger(__name__)

router = Router(name="review")

CB_PREFIX = "review:"


@router.callback_query(F.data.startswith(CB_PREFIX))
async def on_review(
    callback: types.CallbackQuery,
    settings: Settings,
    db: Database,
    blocklist: BlocklistFilter,
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

    if action == "ok":
        await db.resolve_pending_review(msg_id, verdict="ok")
        await db.inc("reviews_ok")
        await callback.message.edit_text(
            texts.REVIEW_HEADER.format(
                user_id=pending["user_id"],
                reason=pending["reason"] or "-",
                text=pending["text"][:800],
            ) + "\n\n" + texts.REVIEW_DONE_OK
        )
        await callback.answer("Отмечено: ок")
        return

    if action in ("spam", "block"):
        # Удаляем сообщение в чате.
        try:
            await callback.bot.delete_message(
                chat_id=pending["chat_id"], message_id=msg_id
            )
        except TelegramAPIError as e:
            logger.warning(
                "Не удалось удалить сообщение %s в чате %s: %s",
                msg_id, pending["chat_id"], e,
            )
        await db.log_deleted(msg_id, pending["user_id"], pending["chat_id"])
        await db.inc("deleted")

        if action == "spam":
            await db.resolve_pending_review(msg_id, verdict="spam")
            await db.inc("reviews_spam")
            await callback.message.edit_text(
                texts.REVIEW_HEADER.format(
                    user_id=pending["user_id"],
                    reason=pending["reason"] or "-",
                    text=pending["text"][:800],
                ) + "\n\n" + texts.REVIEW_DONE_SPAM
            )
            await callback.answer("Удалено и помечено как спам")
            return

        # action == "block": удалить + добавить в стоп-лист.
        hint = pending.get("blocklist_hint")
        if not hint:
            await db.resolve_pending_review(msg_id, verdict="spam")
            await db.inc("reviews_spam")
            await callback.message.edit_text(
                texts.REVIEW_HEADER.format(
                    user_id=pending["user_id"],
                    reason=pending["reason"] or "-",
                    text=pending["text"][:800],
                ) + "\n\n" + texts.REVIEW_DONE_SPAM
            )
            await callback.answer("Удалено (в стоп-лист добавлять нечего)")
            return

        try:
            await db.add_word(hint, note="из разметки")
            blocklist.invalidate()
            added = True
        except Exception as e:  # noqa: BLE001 — IntegrityError и прочее
            logger.warning("Не удалось добавить в стоп-лист %r: %s", hint, e)
            added = False

        await db.resolve_pending_review(msg_id, verdict="spam")
        await db.inc("reviews_spam")
        await callback.message.edit_text(
            texts.REVIEW_HEADER.format(
                user_id=pending["user_id"],
                reason=pending["reason"] or "-",
                text=pending["text"][:800],
            ) + "\n\n" + (texts.REVIEW_DONE_BLOCKLIST if added else texts.REVIEW_DONE_SPAM)
        )
        await callback.answer("Удалено и добавлено в стоп-лист")
        return

    await callback.answer("Неизвестное действие.", show_alert=True)
