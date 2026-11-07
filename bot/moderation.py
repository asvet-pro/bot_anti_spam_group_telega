"""Центральная логика модерации: единая точка для бана и удаления.

Почему отдельный модуль:
- Раньше логика бана была размазана по хендлерам, и легко было забыть
  проверить «не админ ли это» в новом месте — тогда бот забанил бы
  администратора.
- Здесь бан применяется через одну функцию, которая ВСЕГДА проверяет
  защиту (админ / вайтлист). Забыть её невозможно.

Правила бана:
- Дубликаты (больше порога) → бан 24 часа (автоматически).
- Стоп-слово / стоп-regex / BANNED_PATTERNS → бан 24 часа (автоматически).
- ML → удаление без бана (фильтр токсичности может зацепить живого).
- Вечный бан ставит только админ кнопкой «🔨 БАН».
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Literal

from aiogram import Bot, types
from aiogram.exceptions import TelegramAPIError

from bot.config import Settings
from bot.db import Database
from bot.filters.admin import AdminFilter

logger = logging.getLogger(__name__)

# Длительность автоматического бана.
TEMP_BAN_HOURS = 24
TEMP_BAN_SECONDS = TEMP_BAN_HOURS * 3600

# Тип действия.
Action = Literal["delete_only", "temp_ban", "permanent_ban"]

# Уровни сигнала → действие по умолчанию.
# kind: ml | word | pattern | legacy | duplicate | flood
DEFAULT_ACTION: dict[str, Action] = {
    "ml": "delete_only",
    "word": "temp_ban",
    "pattern": "temp_ban",
    "legacy": "temp_ban",
    "duplicate": "temp_ban",
    "flood": "delete_only",
}


@dataclass(slots=True)
class Moderation:
    """Применяет решения модерации: удаление, бан, запись в БД."""

    db: Database
    admin: AdminFilter
    settings: Settings

    async def is_protected(self, user_id: int) -> bool:
        """Админ или вайтлист — их нельзя трогать ни при каких условиях."""
        if self.admin.is_protected(user_id):
            return True
        return await self.db.is_whitelisted(user_id)

    async def delete_message(self, message: types.Message, user_id: int) -> bool:
        """Удаляет сообщение, если автор не защищён.

        False — автор защищён (админ/вайтлист), ничего не сделано.
        """
        if await self.is_protected(user_id):
            logger.info(
                "Пропущено удаление: user=%s защищён (админ/вайтлист)", user_id
            )
            return False
        try:
            await message.delete()
        except TelegramAPIError as e:
            logger.warning("Не удалось удалить сообщение: %s", e)
            return False
        await self.db.log_deleted(message.message_id, user_id, message.chat.id)
        await self.db.inc("deleted")
        return True

    async def ban_user(
        self,
        bot: Bot,
        user_id: int,
        chat_id: int,
        reason: str,
        action: Action = "temp_ban",
        by: int | None = None,
    ) -> bool:
        """Банит пользователя согласно action.

        - temp_ban      — бан на 24 часа (автоматический).
        - permanent_ban — бан навсегда (только по решению админа).
        - delete_only   — ничего не банит.

        True — бан применён (или это был delete_only и он ничего не делал).
        False — автор защищён, бан не применялся.
        """
        if await self.is_protected(user_id):
            logger.info(
                "Бан отменён: user=%s защищён (админ/вайтлист)", user_id
            )
            return False

        if action == "delete_only":
            return True

        now = time.time()
        until = 0.0 if action == "permanent_ban" else now + TEMP_BAN_SECONDS

        await self.db.ban(
            user_id=user_id,
            chat_id=chat_id,
            reason=reason,
            by=by,
            until=until,
        )

        try:
            if action == "permanent_ban":
                # until_date=0 → бан навсегда.
                await bot.ban_chat_member(
                    chat_id=chat_id, user_id=user_id, until_date=0
                )
            else:
                # Временный бан: ban с таймером + unban, чтобы Telegram
                # не показал «забанен» в списке участников и не мешал
                # вернуться после истечения.
                await bot.ban_chat_member(
                    chat_id=chat_id, user_id=user_id, until_date=int(until)
                )
                await bot.unban_chat_member(
                    chat_id=chat_id, user_id=user_id, only_if_banned=True
                )
        except TelegramAPIError as e:
            logger.warning(
                "Не удалось применить бан в Telegram для %s: %s", user_id, e
            )
            return True  # запись в БД сделана, разбан вручную

        logger.info(
            "Бан применён: user=%s action=%s until=%s reason=%r",
            user_id, action, ("навсегда" if until == 0 else "24ч"), reason,
        )
        return True

    async def unban_user(
        self, bot: Bot, user_id: int, chat_id: int,
    ) -> bool:
        """Снимает бан (и из БД, и в Telegram)."""
        await self.db.unban(user_id, chat_id)
        try:
            await bot.unban_chat_member(
                chat_id=chat_id, user_id=user_id, only_if_banned=True
            )
        except TelegramAPIError as e:
            logger.warning("Не удалось разбанить в Telegram %s: %s", user_id, e)
            return False
        logger.info("Бан снят: user=%s", user_id)
        return True
