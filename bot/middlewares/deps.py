"""Dependency-injection мидлварь: прокидывает settings/db/фильтры в хендлеры."""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from bot.config import Settings
from bot.db import Database
from bot.filters.admin import AdminFilter
from bot.filters.blocklist import BlocklistFilter
from bot.filters.flood import FloodCheck
from bot.filters.ml_spam import MLSpamCheck
from bot.filters.new_account import NewAccountCheck
from bot.filters.spam import SpamFilter


class DependencyMiddleware(BaseMiddleware):
    def __init__(
        self,
        settings: Settings,
        db: Database,
        new_account: NewAccountCheck,
        flood: FloodCheck,
        spam: SpamFilter,
        admin: AdminFilter,
        ml_spam: MLSpamCheck,
        blocklist: BlocklistFilter,
    ) -> None:
        self.settings = settings
        self.db = db
        self.new_account = new_account
        self.flood = flood
        self.spam = spam
        self.admin = admin
        self.ml_spam = ml_spam
        self.blocklist = blocklist

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        data["settings"] = self.settings
        data["db"] = self.db
        data["new_account"] = self.new_account
        data["flood"] = self.flood
        data["spam"] = self.spam
        data["admin"] = self.admin
        data["ml_spam"] = self.ml_spam
        data["blocklist"] = self.blocklist
        return await handler(event, data)
