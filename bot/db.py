"""Асинхронная обёртка над SQLite (aiosqlite).

Хранит:
- капча-челленджи (кто должен нажать кнопку, дедлайн)
- баны (id, кто, кем, причина, до когда)
- флуд-окна (id, временные метки)
- счётчики событий для /stats
- белый список (кто освобождён от фильтров)
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import aiosqlite


SCHEMA = """
CREATE TABLE IF NOT EXISTS captchas (
    user_id    INTEGER PRIMARY KEY,
    chat_id    INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    deadline   REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS bans (
    user_id    INTEGER PRIMARY KEY,
    chat_id    INTEGER NOT NULL,
    reason     TEXT,
    by         INTEGER,
    until      REAL,    -- 0 = навсегда
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bans_until ON bans(until);

CREATE TABLE IF NOT EXISTS flood_log (
    user_id    INTEGER NOT NULL,
    chat_id    INTEGER NOT NULL,
    ts         REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_flood_user_time
    ON flood_log(user_id, chat_id, ts);

CREATE TABLE IF NOT EXISTS whitelist (
    user_id INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS stats (
    key   TEXT PRIMARY KEY,
    value INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS deleted_msgs (
    msg_id  INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    ts      REAL    NOT NULL
);
"""


@dataclass(slots=True)
class CaptchaChallenge:
    user_id: int
    chat_id: int
    message_id: int
    deadline: float


class Database:
    def __init__(self, path: Path):
        self.path = path

    async def init(self) -> None:
        # Безопасность: создаём родительскую папку, если её нет.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(SCHEMA)
            # Инициализируем счётчики по умолчанию
            for key in ("bans", "deleted", "captcha_fails", "flood_warns", "total_events"):
                await db.execute(
                    "INSERT OR IGNORE INTO stats(key, value) VALUES (?, 0)", (key,)
                )
            await db.commit()

    # ---------- captcha ----------

    async def save_captcha(self, c: CaptchaChallenge) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO captchas(user_id, chat_id, message_id, deadline) "
                "VALUES (?, ?, ?, ?)",
                (c.user_id, c.chat_id, c.message_id, c.deadline),
            )
            await db.commit()

    async def get_captcha(self, user_id: int) -> CaptchaChallenge | None:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT user_id, chat_id, message_id, deadline "
                "FROM captchas WHERE user_id = ?",
                (user_id,),
            ) as cur:
                row = await cur.fetchone()
                if not row:
                    return None
                return CaptchaChallenge(*row)

    async def delete_captcha(self, user_id: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("DELETE FROM captchas WHERE user_id = ?", (user_id,))
            await db.commit()

    async def get_expired_captchas(self, now: float | None = None) -> list[CaptchaChallenge]:
        now = now or time.time()
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT user_id, chat_id, message_id, deadline "
                "FROM captchas WHERE deadline < ?",
                (now,),
            ) as cur:
                rows = await cur.fetchall()
                return [CaptchaChallenge(*r) for r in rows]

    # ---------- bans ----------

    async def ban(
        self, user_id: int, chat_id: int, reason: str,
        by: int | None = None, until: float = 0.0,
    ) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO bans(user_id, chat_id, reason, by, until, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, chat_id, reason, by, until, time.time()),
            )
            await db.execute(
                "UPDATE stats SET value = value + 1 WHERE key IN ('bans', 'total_events')"
            )
            await db.commit()

    async def unban(self, user_id: int, chat_id: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "DELETE FROM bans WHERE user_id = ? AND chat_id = ?",
                (user_id, chat_id),
            )
            await db.commit()

    async def is_banned(self, user_id: int, chat_id: int) -> bool:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT until FROM bans WHERE user_id = ? AND chat_id = ?",
                (user_id, chat_id),
            ) as cur:
                row = await cur.fetchone()
                if not row:
                    return False
                until = row[0]
                if until == 0:
                    return True
                if until > time.time():
                    return True
                # Временный бан истёк — убираем
                await db.execute(
                    "DELETE FROM bans WHERE user_id = ? AND chat_id = ?",
                    (user_id, chat_id),
                )
                await db.commit()
                return False

    # ---------- flood ----------

    async def record_message(self, user_id: int, chat_id: int) -> int:
        """Записывает сообщение и возвращает сколько у этого юзера
        в окне последних N секунд."""
        now = time.time()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO flood_log(user_id, chat_id, ts) VALUES (?, ?, ?)",
                (user_id, chat_id, now),
            )
            await db.commit()
        return await self.count_recent(user_id, chat_id, now)

    async def count_recent(
        self, user_id: int, chat_id: int, now: float | None = None,
        window: float = 10.0,
    ) -> int:
        now = now or time.time()
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM flood_log "
                "WHERE user_id = ? AND chat_id = ? AND ts >= ?",
                (user_id, chat_id, now - window),
            ) as cur:
                row = await cur.fetchone()
                return int(row[0]) if row else 0

    async def trim_flood(self, chat_id: int, keep_window: float = 3600.0) -> None:
        """Раз в час чистим старые записи, чтобы база не пухла."""
        cutoff = time.time() - keep_window
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "DELETE FROM flood_log WHERE chat_id = ? AND ts < ?",
                (chat_id, cutoff),
            )
            await db.commit()

    # ---------- whitelist ----------

    async def add_whitelist(self, user_id: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO whitelist(user_id) VALUES (?)", (user_id,)
            )
            await db.commit()

    async def remove_whitelist(self, user_id: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("DELETE FROM whitelist WHERE user_id = ?", (user_id,))
            await db.commit()

    async def is_whitelisted(self, user_id: int) -> bool:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT 1 FROM whitelist WHERE user_id = ?", (user_id,)
            ) as cur:
                return (await cur.fetchone()) is not None

    # ---------- stats ----------

    async def inc(self, key: str, by: int = 1) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE stats SET value = value + ? WHERE key = ?", (by, key)
            )
            await db.execute(
                "UPDATE stats SET value = value + 1 WHERE key = 'total_events'"
            )
            await db.commit()

    async def get_stats(self) -> dict[str, int]:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute("SELECT key, value FROM stats") as cur:
                return {k: v for k, v in await cur.fetchall()}

    # ---------- deleted messages ----------

    async def log_deleted(self, msg_id: int, user_id: int, chat_id: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO deleted_msgs(msg_id, user_id, chat_id, ts) "
                "VALUES (?, ?, ?, ?)",
                (msg_id, user_id, chat_id, time.time()),
            )
            await db.commit()
