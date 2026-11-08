"""Команда /learn — подбор стоп-слов по накопленной разметке.

По размеченным примерам («спам» / «ок») считаются слова, которые
характерны для спама. Админ может одним нажатием добавить любое из
них в стоп-лист — дальше такие сообщения удаляются автоматически.

Это практическая замена дообучению модели: работает сразу, без GPU
и без обучающего пайплайна.
"""
from __future__ import annotations

import logging

from aiogram import F, Router, types
from aiogram.exceptions import TelegramAPIError
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import texts
from bot.config import Settings
from bot.db import Database
from bot.filters.blocklist import BlocklistFilter
from bot.filters.learner import suggest_words

logger = logging.getLogger(__name__)

router = Router(name="learn")

CB_PREFIX = "learn:add:"


def _keyboard(words: list[str]) -> types.InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for w in words:
        kb.button(text=w, callback_data=f"{CB_PREFIX}{w}")
    kb.adjust(2)
    return kb.as_markup()


@router.message(F.text == "/learn")
async def cmd_learn(
    message: types.Message,
    settings: Settings,
    db: Database,
) -> None:
    """Показывает кандидатов в стоп-лист."""
    if message.from_user is None or message.from_user.id not in settings.admin_ids:
        return

    spam_reports = await db.list_reports(limit=500, verdict="spam")
    ok_reports = await db.list_reports(limit=500, verdict="ok")

    if not spam_reports:
        await message.reply(texts.LEARN_NO_DATA)
        return

    candidates = suggest_words(
        spam_texts=[r["text"] for r in spam_reports],
        ok_texts=[r["text"] for r in ok_reports],
    )
    if not candidates:
        await message.reply(texts.LEARN_NOT_ENOUGH)
        return

    lines = [
        texts.LEARN_HEADER.format(
            spam=len(spam_reports), ok=len(ok_reports)
        )
    ]
    for c in candidates:
        lines.append(f"• <code>{c.word}</code> — {c.spam_count} раз, {c.score:.1f}")

    await message.reply("\n".join(lines), reply_markup=_keyboard(
        [c.word for c in candidates]
    ))


@router.callback_query(F.data.startswith(CB_PREFIX))
async def on_learn_add(
    callback: types.CallbackQuery,
    settings: Settings,
    db: Database,
    blocklist: BlocklistFilter,
) -> None:
    """Админ нажал «добавить в стоп-лист»."""
    if callback.from_user is None or callback.from_user.id not in settings.admin_ids:
        await callback.answer(texts.REVIEW_NOT_ADMIN, show_alert=True)
        return

    word = callback.data[len(CB_PREFIX):].strip().lower()
    if not word:
        await callback.answer("Пустое слово.", show_alert=True)
        return

    try:
        await db.add_word(word, note="из /learn")
    except Exception as e:  # noqa: BLE001 — IntegrityError и прочее
        logger.info("Слово %r уже в стоп-листе: %s", word, e)
        await callback.answer("Уже в стоп-листе")
        return

    blocklist.invalidate()
    logger.info("Слово %r добавлено в стоп-лист через /learn", word)
    await callback.answer(f"«{word}» добавлено в стоп-лист")

    # Убираем кнопку из сообщения.
    try:
        kb = InlineKeyboardBuilder()
        await callback.message.edit_reply_markup(reply_markup=kb.as_markup())
    except TelegramAPIError:
        pass
