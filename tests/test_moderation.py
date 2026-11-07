"""Тесты модерации: баны, защита админов, дедупликация уведомлений."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from bot.config import load_settings
from bot.db import Database
from bot.filters.admin import AdminFilter
from bot.moderation import Moderation


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "moderation_test.db"


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    env = tmp_path / ".env.test"
    env.write_text(
        "BOT_TOKEN=123456:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n"
        "CHAT_ID=-1001234567890\n"
        "ADMIN_IDS=111,222\n"
        f"DB_PATH={tmp_path / 'bot.db'}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("BOT_TOKEN", "123456:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
    monkeypatch.setenv("CHAT_ID", "-1001234567890")
    monkeypatch.setenv("ADMIN_IDS", "111,222")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "bot.db"))
    return load_settings(env)


class FakeBot:
    """Минимальный бот: записывает вызовы ban/unban."""

    def __init__(self) -> None:
        self.banned: list[tuple[int, int]] = []
        self.unbanned: list[tuple[int, int]] = []

    async def ban_chat_member(self, chat_id: int, user_id: int, until_date: int = 0):
        self.banned.append((user_id, until_date))

    async def unban_chat_member(self, chat_id: int, user_id: int, only_if_banned: bool = True):
        self.unbanned.append((user_id, 0))


@pytest.mark.asyncio
async def test_admin_is_never_banned(db_path: Path, settings) -> None:
    """КЛЮЧЕВОЙ ТЕСТ: бан не применяется к админам."""
    db = Database(db_path)
    await db.init()
    admin = AdminFilter(admin_ids=settings.admin_ids)
    mod = Moderation(db=db, admin=admin, settings=settings)
    bot = FakeBot()

    for admin_id in (111, 222):
        ok = await mod.ban_user(
            bot, admin_id, settings.chat_id, "тест", action="permanent_ban"
        )
        assert ok is False, f"админ {admin_id} защищён от бана"
        assert bot.banned == [], "в Telegram бан не должен уходить"
        assert await db.is_banned(admin_id, settings.chat_id) is False


@pytest.mark.asyncio
async def test_group_admin_is_never_banned(db_path: Path, settings) -> None:
    """Админ группы (не в ADMIN_IDS) тоже защищён."""
    db = Database(db_path)
    await db.init()
    admin = AdminFilter(admin_ids=settings.admin_ids)
    # Имитируем, что кеш админов группы уже заполнен.
    admin.group_admin_ids = {999}
    mod = Moderation(db=db, admin=admin, settings=settings)
    bot = FakeBot()

    ok = await mod.ban_user(
        bot, 999, settings.chat_id, "тест", action="temp_ban"
    )
    assert ok is False
    assert bot.banned == []


@pytest.mark.asyncio
async def test_whitelisted_is_never_banned(db_path: Path, settings) -> None:
    """Участник из вайтлиста защищён от бана."""
    db = Database(db_path)
    await db.init()
    admin = AdminFilter(admin_ids=settings.admin_ids)
    mod = Moderation(db=db, admin=admin, settings=settings)
    bot = FakeBot()

    await db.add_whitelist(555)
    ok = await mod.ban_user(
        bot, 555, settings.chat_id, "тест", action="temp_ban"
    )
    assert ok is False
    assert bot.banned == []


@pytest.mark.asyncio
async def test_temp_ban_is_24_hours(db_path: Path, settings) -> None:
    """Автоматический бан — ровно 24 часа."""
    db = Database(db_path)
    await db.init()
    admin = AdminFilter(admin_ids=settings.admin_ids)
    mod = Moderation(db=db, admin=admin, settings=settings)
    bot = FakeBot()

    await mod.ban_user(bot, 777, settings.chat_id, "спам", action="temp_ban")

    assert len(bot.banned) == 1
    _, until_date = bot.banned[0]
    expected = time.time() + 24 * 3600
    assert abs(until_date - expected) < 120, "бан должен быть на 24 часа"
    # Временный бан в Telegram делается ban + unban
    assert len(bot.unbanned) == 1

    # И в БД записан срок
    assert await db.is_banned(777, settings.chat_id) is True


@pytest.mark.asyncio
async def test_permanent_ban_has_no_expiry(db_path: Path, settings) -> None:
    """Вечный бан: until_date=0, без unban."""
    db = Database(db_path)
    await db.init()
    admin = AdminFilter(admin_ids=settings.admin_ids)
    mod = Moderation(db=db, admin=admin, settings=settings)
    bot = FakeBot()

    await mod.ban_user(
        bot, 888, settings.chat_id, "спам", action="permanent_ban"
    )

    assert len(bot.banned) == 1
    _, until_date = bot.banned[0]
    assert until_date == 0, "вечный бан = until_date 0"
    assert bot.unbanned == [], "вечный бан не разбанивается сразу"


@pytest.mark.asyncio
async def test_delete_only_does_not_ban(db_path: Path, settings) -> None:
    """delete_only — удаляем, но не баним."""
    db = Database(db_path)
    await db.init()
    admin = AdminFilter(admin_ids=settings.admin_ids)
    mod = Moderation(db=db, admin=admin, settings=settings)
    bot = FakeBot()

    await mod.ban_user(bot, 333, settings.chat_id, "спам", action="delete_only")
    assert bot.banned == []
    assert await db.is_banned(333, settings.chat_id) is False


@pytest.mark.asyncio
async def test_unban_removes_from_db(db_path: Path, settings) -> None:
    """Снятие бана убирает запись из БД."""
    db = Database(db_path)
    await db.init()
    admin = AdminFilter(admin_ids=settings.admin_ids)
    mod = Moderation(db=db, admin=admin, settings=settings)
    bot = FakeBot()

    await mod.ban_user(bot, 444, settings.chat_id, "спам", action="temp_ban")
    assert await db.is_banned(444, settings.chat_id) is True

    await mod.unban_user(bot, 444, settings.chat_id)
    assert await db.is_banned(444, settings.chat_id) is False


@pytest.mark.asyncio
async def test_notification_dedup(db_path: Path) -> None:
    """Одинаковые сообщения от одного автора не спамят уведомлениями."""
    db = Database(db_path)
    await db.init()

    text = "Заработок на нейросетях, пиши в лс"
    assert await db.was_recently_notified(1, -100, text) is False
    await db.mark_notified(1, -100, text)
    assert await db.was_recently_notified(1, -100, text) is True

    # Другой автор — не дубликат
    assert await db.was_recently_notified(2, -100, text) is False
    # Другой текст — не дубликат
    assert await db.was_recently_notified(1, -100, "другой текст") is False


@pytest.mark.asyncio
async def test_notification_dedup_ignores_case_and_spaces(db_path: Path) -> None:
    """Дедуп по нормализованному тексту."""
    db = Database(db_path)
    await db.init()
    await db.mark_notified(1, -100, "Заработок на нейросетях")
    assert await db.was_recently_notified(1, -100, "заработок   на нейросетях") is True
