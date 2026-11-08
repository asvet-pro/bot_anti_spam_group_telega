"""Проверка, является ли пользователь админом бота (по списку ADMIN_IDS).

Дополнительно учитываем администраторов самой группы: список из
ADMIN_IDS может быть неполным (так уже случилось — второй админ группы
не был в списке, и бот резал его сообщения). Админы группы определяются
через getChatAdministrators и кешируются на несколько минут.

Ключевая функция — is_protected(): её должен вызывать ЛЮБОЙ код,
который собирается забанить пользователя. Проверка защиты живёт здесь,
а не в вызывающем коде, чтобы её нельзя было забыть.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from aiogram import Bot, types
from aiogram.exceptions import TelegramAPIError

logger = logging.getLogger(__name__)

# Как долго кешировать список админов группы.
ADMINS_CACHE_TTL = 300.0


@dataclass(slots=True)
class AdminFilter:
    admin_ids: tuple[int, ...]
    # id админов группы (определяются лениво).
    group_admin_ids: set[int] = field(default_factory=set)
    _loaded_at: float = 0.0

    def is_admin(self, user: types.User | None) -> bool:
        if user is None:
            return False
        if user.id in self.admin_ids:
            return True
        # Админ группы тоже неприкосновенен.
        return user.id in self.group_admin_ids

    def is_protected(self, user_id: int) -> bool:
        """True, если пользователя нельзя банить/удалять его сообщения.

        Проверяет и ADMIN_IDS, и админов группы. Вызывается из функции
        бана — это единственная точка, где решается, можно ли тронуть
        участника.
        """
        return user_id in self.admin_ids or user_id in self.group_admin_ids

    async def refresh_group_admins(self, bot: Bot, chat_id: int) -> None:
        """Обновляет кеш админов группы. Ошибки не пробрасываем."""
        try:
            admins = await bot.get_chat_administrators(chat_id)
        except TelegramAPIError as e:
            logger.warning("Не удалось получить админов чата %s: %s", chat_id, e)
            return
        except Exception as e:  # noqa: BLE001
            logger.warning("Ошибка при получении админов чата %s: %s", chat_id, e)
            return

        ids = {a.user.id for a in admins if not a.user.is_bot}
        # Логируем только при изменении состава — иначе лог засоряется
        # одинаковой строкой каждые 5 минут.
        if ids != self.group_admin_ids:
            self.group_admin_ids = ids
            logger.info(
                "Админы чата %s обновлены: %s (из ADMIN_IDS: %s)",
                chat_id, sorted(ids), self.admin_ids,
            )
        else:
            logger.debug(
                "Админы чата %s без изменений (%s)", chat_id, sorted(ids)
            )
        self._loaded_at = time.monotonic()

    async def ensure_group_admins(self, bot: Bot, chat_id: int) -> None:
        """Обновляет список админов, если кеш устарел."""
        if time.monotonic() - self._loaded_at < ADMINS_CACHE_TTL:
            return
        await self.refresh_group_admins(bot, chat_id)
