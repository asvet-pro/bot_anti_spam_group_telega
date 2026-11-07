"""Асинхронная обёртка над SQLite (aiosqlite).

Хранит:
- капча-челленджи (кто должен нажать кнопку, дедлайн)
- баны (id, кто, кем, причина, до когда)
- флуд-окна (id, временные метки)
- счётчики событий для /stats
- белый список (кто освобождён от фильтров)
- стоп-лист (слова и regex-паттерны, управляются через /help в ЛС)
"""
from __future__ import annotations

import re
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

-- Разметка спама админом: «подозрительные» сообщения, которые бот НЕ
-- удалил, а отправил админу в личку на разбор. Админ жмёт «спам»/«ок».
-- Хранятся только помеченные админом сообщения — обычная переписка
-- участников чата в базу не попадает.
CREATE TABLE IF NOT EXISTS spam_reports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    msg_id      INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    chat_id     INTEGER NOT NULL,
    text        TEXT    NOT NULL,
    reason      TEXT,
    verdict     TEXT,               -- 'spam' | 'ok' | NULL (ещё не размечено)
    blocklist_hint TEXT,            -- что предложить добавить в стоп-лист
    created     REAL    NOT NULL,
    decided     REAL
);

CREATE INDEX IF NOT EXISTS idx_spam_reports_verdict ON spam_reports(verdict);
CREATE INDEX IF NOT EXISTS idx_spam_reports_msg ON spam_reports(msg_id);

-- Очередь сообщений, отправленных админу на разбор (чтобы не дублировать).
CREATE TABLE IF NOT EXISTS pending_review (
    msg_id      INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL,
    chat_id     INTEGER NOT NULL,
    text        TEXT    NOT NULL,
    reason      TEXT,
    blocklist_hint TEXT,
    created     REAL    NOT NULL
);

-- Управляемый стоп-лист: простые слова (подстрочный поиск, регистр игнорируется).
CREATE TABLE IF NOT EXISTS blocklist_words (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    word    TEXT    NOT NULL UNIQUE,
    note    TEXT,
    by      INTEGER,
    created REAL    NOT NULL
);

-- Управляемый стоп-лист: regex-паттерны (как в BANNED_PATTERNS).
CREATE TABLE IF NOT EXISTS blocklist_patterns (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern TEXT    NOT NULL UNIQUE,
    note    TEXT,
    by      INTEGER,
    created REAL    NOT NULL
);
"""


@dataclass(slots=True)
class CaptchaChallenge:
    user_id: int
    chat_id: int
    message_id: int
    deadline: float


@dataclass(slots=True)
class BlocklistWord:
    id: int
    word: str
    note: str | None
    by: int | None
    created: float


@dataclass(slots=True)
class BlocklistPattern:
    id: int
    pattern: str
    note: str | None
    by: int | None
    created: float


class Database:
    def __init__(self, path: Path):
        self.path = path

    async def init(self) -> None:
        # Безопасность: создаём родительскую папку, если её нет.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(SCHEMA)
            # Инициализируем счётчики по умолчанию
            for key in (
                "bans", "deleted", "captcha_fails", "flood_warns", "total_events",
                "reviews_spam", "reviews_ok",
            ):
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

    # ---------- blocklist (words) ----------

    async def add_word(
        self, word: str, note: str | None = None, by: int | None = None,
    ) -> BlocklistWord:
        """Добавляет слово. Возвращает запись. Бросает aiosqlite.IntegrityError
        на дубликат (UNIQUE constraint)."""
        word_clean = word.strip().lower()
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "INSERT INTO blocklist_words(word, note, by, created) "
                "VALUES (?, ?, ?, ?)",
                (word_clean, note, by, time.time()),
            )
            await db.commit()
            new_id = cur.lastrowid
        return BlocklistWord(
            id=int(new_id), word=word_clean, note=note, by=by, created=time.time()
        )

    async def delete_word(self, key: str | int) -> BlocklistWord | None:
        """Удаляет слово по id или по точному тексту. Возвращает удалённую запись."""
        async with aiosqlite.connect(self.path) as db:
            if isinstance(key, int):
                cur = await db.execute(
                    "SELECT id, word, note, by, created FROM blocklist_words WHERE id = ?",
                    (key,),
                )
            else:
                cur = await db.execute(
                    "SELECT id, word, note, by, created FROM blocklist_words "
                    "WHERE word = ?",
                    (key.strip().lower(),),
                )
            row = await cur.fetchone()
            if not row:
                return None
            entry = BlocklistWord(*row)
            await db.execute(
                "DELETE FROM blocklist_words WHERE id = ?", (entry.id,)
            )
            await db.commit()
            return entry

    async def list_words(self) -> list[BlocklistWord]:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT id, word, note, by, created FROM blocklist_words "
                "ORDER BY id"
            )
            rows = await cur.fetchall()
            return [BlocklistWord(*r) for r in rows]

    async def all_words(self) -> tuple[str, ...]:
        """Возвращает кортеж всех слов (lowercase). Для кеша фильтра."""
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("SELECT word FROM blocklist_words")
            rows = await cur.fetchall()
            return tuple(r[0] for r in rows)

    # ---------- blocklist (patterns) ----------

    async def add_pattern(
        self, pattern: str, note: str | None = None, by: int | None = None,
    ) -> BlocklistPattern:
        """Добавляет regex-паттерн. Бросает re.error если regex битый,
        aiosqlite.IntegrityError если дубликат."""
        # Валидация — компилируем сразу, чтобы кривой regex не сохранился.
        re.compile(pattern)
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "INSERT INTO blocklist_patterns(pattern, note, by, created) "
                "VALUES (?, ?, ?, ?)",
                (pattern, note, by, time.time()),
            )
            await db.commit()
            new_id = cur.lastrowid
        return BlocklistPattern(
            id=int(new_id), pattern=pattern, note=note, by=by, created=time.time()
        )

    async def delete_pattern(self, key: str | int) -> BlocklistPattern | None:
        async with aiosqlite.connect(self.path) as db:
            if isinstance(key, int):
                cur = await db.execute(
                    "SELECT id, pattern, note, by, created "
                    "FROM blocklist_patterns WHERE id = ?",
                    (key,),
                )
            else:
                cur = await db.execute(
                    "SELECT id, pattern, note, by, created "
                    "FROM blocklist_patterns WHERE pattern = ?",
                    (key,),
                )
            row = await cur.fetchone()
            if not row:
                return None
            entry = BlocklistPattern(*row)
            await db.execute(
                "DELETE FROM blocklist_patterns WHERE id = ?", (entry.id,)
            )
            await db.commit()
            return entry

    async def list_patterns(self) -> list[BlocklistPattern]:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT id, pattern, note, by, created "
                "FROM blocklist_patterns ORDER BY id"
            )
            rows = await cur.fetchall()
            return [BlocklistPattern(*r) for r in rows]

    async def all_patterns(self) -> tuple[str, ...]:
        """Возвращает кортеж строк-паттернов (как есть, без компиляции)."""
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("SELECT pattern FROM blocklist_patterns")
            rows = await cur.fetchall()
            return tuple(r[0] for r in rows)

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

    # ---------- spam reports (разметка админом) ----------

    async def add_pending_review(
        self,
        msg_id: int,
        user_id: int,
        chat_id: int,
        text: str,
        reason: str | None = None,
        blocklist_hint: str | None = None,
    ) -> bool:
        """Кладёт сообщение в очередь на разбор. False — уже было в очереди."""
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "INSERT OR IGNORE INTO pending_review"
                "(msg_id, user_id, chat_id, text, reason, blocklist_hint, created) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (msg_id, user_id, chat_id, text, reason, blocklist_hint, time.time()),
            )
            await db.commit()
            return cur.rowcount > 0

    async def get_pending_review(self, msg_id: int) -> dict | None:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT msg_id, user_id, chat_id, text, reason, blocklist_hint "
                "FROM pending_review WHERE msg_id = ?",
                (msg_id,),
            ) as cur:
                row = await cur.fetchone()
                if not row:
                    return None
                return {
                    "msg_id": row[0],
                    "user_id": row[1],
                    "chat_id": row[2],
                    "text": row[3],
                    "reason": row[4],
                    "blocklist_hint": row[5],
                }

    async def resolve_pending_review(
        self,
        msg_id: int,
        verdict: str,
        blocklist_hint: str | None = None,
    ) -> dict | None:
        """Помечает сообщение как спам/ок и переносит в spam_reports.

        verdict: 'spam' | 'ok'. Возвращает сохранённую запись или None.
        """
        now = time.time()
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT msg_id, user_id, chat_id, text, reason, blocklist_hint "
                "FROM pending_review WHERE msg_id = ?",
                (msg_id,),
            ) as cur:
                row = await cur.fetchone()
                if not row:
                    return None
                await db.execute(
                    "INSERT INTO spam_reports"
                    "(msg_id, user_id, chat_id, text, reason, verdict, "
                    " blocklist_hint, created, decided) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        row[0], row[1], row[2], row[3], row[4], verdict,
                        blocklist_hint or row[5], now, now,
                    ),
                )
                await db.execute(
                    "DELETE FROM pending_review WHERE msg_id = ?", (msg_id,)
                )
                await db.commit()
                return {
                    "msg_id": row[0],
                    "user_id": row[1],
                    "chat_id": row[2],
                    "text": row[3],
                    "reason": row[4],
                    "verdict": verdict,
                }

    async def count_reports(self, verdict: str | None = None) -> int:
        """Сколько записей в spam_reports (по вердикту, если задан)."""
        async with aiosqlite.connect(self.path) as db:
            if verdict is None:
                sql = "SELECT COUNT(*) FROM spam_reports"
                params: tuple = ()
            else:
                sql = "SELECT COUNT(*) FROM spam_reports WHERE verdict = ?"
                params = (verdict,)
            async with db.execute(sql, params) as cur:
                row = await cur.fetchone()
                return int(row[0]) if row else 0

    async def list_reports(self, limit: int = 50, verdict: str | None = None) -> list[dict]:
        """Последние размеченные сообщения — для просмотра в боте."""
        async with aiosqlite.connect(self.path) as db:
            if verdict is None:
                sql = (
                    "SELECT msg_id, user_id, text, reason, verdict, created "
                    "FROM spam_reports ORDER BY id DESC LIMIT ?"
                )
                params: tuple = (limit,)
            else:
                sql = (
                    "SELECT msg_id, user_id, text, reason, verdict, created "
                    "FROM spam_reports WHERE verdict = ? ORDER BY id DESC LIMIT ?"
                )
                params = (verdict, limit)
            async with db.execute(sql, params) as cur:
                rows = await cur.fetchall()
                return [
                    {
                        "msg_id": r[0],
                        "user_id": r[1],
                        "text": r[2],
                        "reason": r[3],
                        "verdict": r[4],
                        "created": r[5],
                    }
                    for r in rows
                ]
