"""Проверка, является ли пользователь админом бота (по списку ADMIN_IDS)."""
from __future__ import annotations

from dataclasses import dataclass

from aiogram import types


@dataclass(slots=True)
class AdminFilter:
    admin_ids: tuple[int, ...]

    def is_admin(self, user: types.User | None) -> bool:
        if user is None:
            return False
        return user.id in self.admin_ids
