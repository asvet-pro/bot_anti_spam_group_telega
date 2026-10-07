"""Управление стоп-листом: слова и regex-паттерны.

Команды в ЛС:
- /blocklist_words
- /blocklist_patterns
- /blocklist_add_word <слово> [заметка]
- /blocklist_add_pattern <regex> [заметка]
- /blocklist_del_word <id|слово>
- /blocklist_del_pattern <id|regex>
- /blocklist_test <текст>

Inline-через /help:
- «📝 Слова» / «🔧 Regex» — список с кнопками «❌ удалить»
- «➕ Добавить …» — переход в FSM (бот ждёт следующее сообщение)
- «🧪 Тест фильтра» — переход в FSM

Все работает только для админов в ЛС.
"""
from __future__ import annotations

import logging
import re
from typing import Iterable

import aiosqlite
from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import texts
from bot.config import Settings
from bot.db import Database
from bot.filters.blocklist import BlocklistFilter

logger = logging.getLogger(__name__)

router = Router(name="blocklist")

# Только в ЛС с ботом (как остальные админ-команды)
router.message.filter(F.chat.type == "private")


class BlocklistStates(StatesGroup):
    waiting_word = State()
    waiting_pattern = State()
    waiting_test = State()


def _is_admin(message: types.Message, settings: Settings) -> bool:
    return message.from_user is not None and message.from_user.id in settings.admin_ids


# --- helpers ---------------------------------------------------------------


def _parse_int(s: str) -> int | None:
    try:
        return int(s.strip())
    except (ValueError, AttributeError):
        return None


def _words_list_text(words) -> str:
    if not words:
        return texts.BLOCKLIST_WORDS_LIST_HEADER.format(count=0) + texts.BLOCKLIST_EMPTY
    lines = "\n".join(
        f"  {i}. <code>{w.word}</code>" + (f" — {w.note}" if w.note else "")
        for i, w in enumerate(words, 1)
    )
    return texts.BLOCKLIST_WORDS_LIST_HEADER.format(count=len(words)) + lines


def _patterns_list_text(patterns) -> str:
    if not patterns:
        return texts.BLOCKLIST_PATTERNS_LIST_HEADER.format(count=0) + texts.BLOCKLIST_EMPTY
    lines = "\n".join(
        f"  {i}. <code>{p.pattern}</code>" + (f" — {p.note}" if p.note else "")
        for i, p in enumerate(patterns, 1)
    )
    return texts.BLOCKLIST_PATTERNS_LIST_HEADER.format(count=len(patterns)) + lines


def _words_list_kb(words) -> InlineKeyboardBuilder:
    b = InlineKeyboardBuilder()
    for w in words:
        b.button(text=f"❌ {w.word}", callback_data=f"blocklist:del_word:{w.id}")
    b.button(text="➕ Добавить слово", callback_data="blocklist:add_word")
    b.button(text="🔧 Regex", callback_data="blocklist:patterns")
    b.button(text="🧪 Тест", callback_data="blocklist:test")
    b.button(text="← Назад", callback_data="help:blocklist")
    b.adjust(1)
    return b


def _patterns_list_kb(patterns) -> InlineKeyboardBuilder:
    b = InlineKeyboardBuilder()
    for p in patterns:
        b.button(
            text=f"❌ {p.pattern[:30]}{'…' if len(p.pattern) > 30 else ''}",
            callback_data=f"blocklist:del_pattern:{p.id}",
        )
    b.button(text="➕ Добавить regex", callback_data="blocklist:add_pattern")
    b.button(text="📝 Слова", callback_data="blocklist:words")
    b.button(text="🧪 Тест", callback_data="blocklist:test")
    b.button(text="← Назад", callback_data="help:blocklist")
    b.adjust(1)
    return b


# --- команды в ЛС ---------------------------------------------------------


@router.message(Command("blocklist_words"))
async def cmd_list_words(
    message: types.Message, settings: Settings, db: Database,
) -> None:
    if not _is_admin(message, settings):
        await message.reply(texts.BLOCKLIST_NOT_ADMIN)
        return
    words = await db.list_words()
    await message.reply(_words_list_text(words))


@router.message(Command("blocklist_patterns"))
async def cmd_list_patterns(
    message: types.Message, settings: Settings, db: Database,
) -> None:
    if not _is_admin(message, settings):
        await message.reply(texts.BLOCKLIST_NOT_ADMIN)
        return
    patterns = await db.list_patterns()
    await message.reply(_patterns_list_text(patterns))


@router.message(Command("blocklist_add_word"))
async def cmd_add_word(
    message: types.Message,
    settings: Settings,
    db: Database,
    blocklist: BlocklistFilter,
) -> None:
    if not _is_admin(message, settings):
        await message.reply(texts.BLOCKLIST_NOT_ADMIN)
        return
    parts = (message.text or "").split(maxsplit=2)
    # /blocklist_add_word <word> [note]
    if len(parts) < 2 or not parts[1].strip():
        await message.reply(
            "Формат: <code>/blocklist_add_word слово [заметка]</code>\n"
            "Пример: <code>/blocklist_add_word казино азартные игры</code>"
        )
        return
    word = parts[1].strip()
    note = parts[2].strip() if len(parts) > 2 else None
    if not word:
        await message.reply(texts.BLOCKLIST_WORD_EMPTY)
        return
    try:
        entry = await db.add_word(word, note=note, by=message.from_user.id)
    except aiosqlite.IntegrityError:
        await message.reply(texts.BLOCKLIST_WORD_DUPLICATE)
        return
    blocklist.invalidate()
    await message.reply(texts.BLOCKLIST_WORD_ADDED.format(word=entry.word))


@router.message(Command("blocklist_add_pattern"))
async def cmd_add_pattern(
    message: types.Message,
    settings: Settings,
    db: Database,
    blocklist: BlocklistFilter,
) -> None:
    if not _is_admin(message, settings):
        await message.reply(texts.BLOCKLIST_NOT_ADMIN)
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 2 or not parts[1].strip():
        await message.reply(
            "Формат: <code>/blocklist_add_pattern regex [заметка]</code>\n"
            "Пример: <code>/blocklist_add_pattern (?i)крипт криптоспам</code>"
        )
        return
    pattern = parts[1].strip()
    note = parts[2].strip() if len(parts) > 2 else None
    try:
        entry = await db.add_pattern(pattern, note=note, by=message.from_user.id)
    except re.error as e:
        await message.reply(texts.BLOCKLIST_INVALID_REGEX.format(error=e))
        return
    except aiosqlite.IntegrityError:
        await message.reply(texts.BLOCKLIST_PATTERN_DUPLICATE)
        return
    blocklist.invalidate()
    await message.reply(texts.BLOCKLIST_PATTERN_ADDED.format(pattern=entry.pattern))


@router.message(Command("blocklist_del_word"))
async def cmd_del_word(
    message: types.Message,
    settings: Settings,
    db: Database,
    blocklist: BlocklistFilter,
) -> None:
    if not _is_admin(message, settings):
        await message.reply(texts.BLOCKLIST_NOT_ADMIN)
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.reply("Формат: <code>/blocklist_del_word id|слово</code>")
        return
    key: int | str = _parse_int(parts[1]) if parts[1].strip().isdigit() else parts[1].strip()
    entry = await db.delete_word(key)
    if entry is None:
        await message.reply(texts.BLOCKLIST_NOT_FOUND)
        return
    blocklist.invalidate()
    await message.reply(texts.BLOCKLIST_WORD_DELETED.format(word=entry.word, id=entry.id))


@router.message(Command("blocklist_del_pattern"))
async def cmd_del_pattern(
    message: types.Message,
    settings: Settings,
    db: Database,
    blocklist: BlocklistFilter,
) -> None:
    if not _is_admin(message, settings):
        await message.reply(texts.BLOCKLIST_NOT_ADMIN)
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.reply("Формат: <code>/blocklist_del_pattern id|regex</code>")
        return
    arg = parts[1].strip()
    key: int | str = _parse_int(arg) if arg.isdigit() else arg
    entry = await db.delete_pattern(key)
    if entry is None:
        await message.reply(texts.BLOCKLIST_NOT_FOUND)
        return
    blocklist.invalidate()
    await message.reply(texts.BLOCKLIST_PATTERN_DELETED.format(pattern=entry.pattern, id=entry.id))


@router.message(Command("blocklist_test"))
async def cmd_test(
    message: types.Message,
    settings: Settings,
    blocklist: BlocklistFilter,
    spam,
) -> None:
    if not _is_admin(message, settings):
        await message.reply(texts.BLOCKLIST_NOT_ADMIN)
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.reply("Формат: <code>/blocklist_test текст</code>")
        return
    text = parts[1]
    await _run_test(message, text, blocklist, spam)


async def _run_test(
    send_target: types.Message, text: str,
    blocklist: BlocklistFilter, spam,
) -> None:
    """Прогоняет text через все фильтры (без ML), выводит результат."""
    lines = [texts.BLOCKLIST_TEST_HEADER.format(text=_escape_html(text))]

    w = await blocklist.match_word(text)
    if w:
        lines.append(texts.BLOCKLIST_TEST_HIT_WORD.format(word=_escape_html(w)))

    p = await blocklist.match_pattern(text)
    if p:
        lines.append(texts.BLOCKLIST_TEST_HIT_PATTERN.format(pattern=_escape_html(p.pattern)))

    if spam.enabled:
        legacy = spam.match(text)
        if legacy:
            lines.append(texts.BLOCKLIST_TEST_HIT_LEGACY.format(pattern=_escape_html(legacy.pattern)))

    if len(lines) == 1:
        lines.append(texts.BLOCKLIST_TEST_NONE)

    await send_target.reply("\n".join(lines))


def _escape_html(s: str) -> str:
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
    )


# --- inline-кнопки из /help ------------------------------------------------


@router.callback_query(F.data.startswith("blocklist:"))
async def blocklist_callback(
    callback: types.CallbackQuery,
    settings: Settings,
    db: Database,
    blocklist: BlocklistFilter,
    state: FSMContext,
    spam,
) -> None:
    if callback.from_user is None or callback.from_user.id not in settings.admin_ids:
        await callback.answer("Нет доступа", show_alert=True)
        return

    parts = callback.data.split(":")
    # Форматы: blocklist:section, blocklist:add_word, blocklist:test,
    # blocklist:del_word:<id>, blocklist:del_pattern:<id>
    section = parts[1] if len(parts) > 1 else ""

    if section == "words":
        words = await db.list_words()
        await callback.message.edit_text(
            _words_list_text(words), reply_markup=_words_list_kb(words).as_markup()
        )
    elif section == "patterns":
        patterns = await db.list_patterns()
        await callback.message.edit_text(
            _patterns_list_text(patterns),
            reply_markup=_patterns_list_kb(patterns).as_markup(),
        )
    elif section == "add_word":
        await state.set_state(BlocklistStates.waiting_word)
        await callback.message.edit_text(texts.BLOCKLIST_ASK_WORD)
    elif section == "add_pattern":
        await state.set_state(BlocklistStates.waiting_pattern)
        await callback.message.edit_text(texts.BLOCKLIST_ASK_PATTERN)
    elif section == "test":
        await state.set_state(BlocklistStates.waiting_test)
        await callback.message.edit_text(texts.BLOCKLIST_ASK_TEST)
    elif section == "del_word" and len(parts) > 2:
        wid = _parse_int(parts[2])
        if wid is not None:
            entry = await db.delete_word(wid)
            if entry is not None:
                blocklist.invalidate()
            words = await db.list_words()
            await callback.message.edit_text(
                _words_list_text(words),
                reply_markup=_words_list_kb(words).as_markup(),
            )
        else:
            await callback.answer("Битый id", show_alert=True)
    elif section == "del_pattern" and len(parts) > 2:
        pid = _parse_int(parts[2])
        if pid is not None:
            entry = await db.delete_pattern(pid)
            if entry is not None:
                blocklist.invalidate()
            patterns = await db.list_patterns()
            await callback.message.edit_text(
                _patterns_list_text(patterns),
                reply_markup=_patterns_list_kb(patterns).as_markup(),
            )
        else:
            await callback.answer("Битый id", show_alert=True)
    else:
        await callback.answer("Неизвестная команда", show_alert=True)
        return

    await callback.answer()


# --- FSM: ловим следующее сообщение ---------------------------------------


@router.message(BlocklistStates.waiting_word)
async def state_word(
    message: types.Message,
    settings: Settings,
    db: Database,
    blocklist: BlocklistFilter,
    state: FSMContext,
) -> None:
    if not _is_admin(message, settings):
        await state.clear()
        return
    text = (message.text or "").strip()
    if text == "/cancel":
        await state.clear()
        await message.reply(texts.BLOCKLIST_CANCELLED)
        return
    if not text:
        await message.reply(texts.BLOCKLIST_WORD_EMPTY)
        return
    try:
        entry = await db.add_word(text, by=message.from_user.id)
    except aiosqlite.IntegrityError:
        await message.reply(texts.BLOCKLIST_WORD_DUPLICATE)
        await state.clear()
        return
    blocklist.invalidate()
    await state.clear()
    await message.reply(texts.BLOCKLIST_WORD_ADDED.format(word=entry.word))


@router.message(BlocklistStates.waiting_pattern)
async def state_pattern(
    message: types.Message,
    settings: Settings,
    db: Database,
    blocklist: BlocklistFilter,
    state: FSMContext,
) -> None:
    if not _is_admin(message, settings):
        await state.clear()
        return
    text = (message.text or "").strip()
    if text == "/cancel":
        await state.clear()
        await message.reply(texts.BLOCKLIST_CANCELLED)
        return
    try:
        entry = await db.add_pattern(text, by=message.from_user.id)
    except re.error as e:
        await message.reply(texts.BLOCKLIST_INVALID_REGEX.format(error=e))
        return
    except aiosqlite.IntegrityError:
        await message.reply(texts.BLOCKLIST_PATTERN_DUPLICATE)
        await state.clear()
        return
    blocklist.invalidate()
    await state.clear()
    await message.reply(texts.BLOCKLIST_PATTERN_ADDED.format(pattern=entry.pattern))


@router.message(BlocklistStates.waiting_test)
async def state_test(
    message: types.Message,
    settings: Settings,
    blocklist: BlocklistFilter,
    spam,
    state: FSMContext,
) -> None:
    if not _is_admin(message, settings):
        await state.clear()
        return
    text = (message.text or "").strip()
    if text == "/cancel":
        await state.clear()
        await message.reply(texts.BLOCKLIST_CANCELLED)
        return
    await _run_test(message, text, blocklist, spam)
    await state.clear()
