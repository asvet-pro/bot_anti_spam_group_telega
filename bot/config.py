"""Загрузка конфигурации из .env."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from dotenv import load_dotenv

import os


def _int_list(raw: str) -> list[int]:
    """Парсит '1,2,3' -> [1,2,3]. Пустая строка -> []."""
    if not raw:
        return []
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


@dataclass(frozen=True)
class Settings:
    bot_token: str
    chat_id: int
    admin_ids: tuple[int, ...]
    min_account_age_days: int
    flood_max_messages: int
    flood_window_seconds: int
    captcha_timeout_seconds: int
    banned_patterns: tuple[re.Pattern[str], ...]
    db_path: Path

    @property
    def db_dir(self) -> Path:
        return self.db_path.parent


def load_settings(env_file: str | Path = ".env") -> Settings:
    """Читает .env, валидирует, возвращает Settings.

    Бросает RuntimeError с понятным сообщением, если чего-то не хватает.
    """
    load_dotenv(env_file, override=False)

    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token or ":" not in bot_token:
        raise RuntimeError(
            "BOT_TOKEN не задан или некорректен. "
            "Создай бота в @BotFather и положи токен в .env"
        )

    raw_chat = os.getenv("CHAT_ID", "").strip()
    if not raw_chat:
        raise RuntimeError(
            "CHAT_ID не задан. Добавь бота в чат и укажи chat.id (обычно отрицательный) в .env"
        )
    try:
        chat_id = int(raw_chat)
    except ValueError as e:
        raise RuntimeError(f"CHAT_ID должен быть числом, получили: {raw_chat!r}") from e

    admin_ids = tuple(_int_list(os.getenv("ADMIN_IDS", "")))
    if not admin_ids:
        # Не критично — бот просто не сможет отвечать на /команды.
        # Но предупреждаем.
        import sys
        print("⚠️  ADMIN_IDS пуст — админ-команды работать не будут", file=sys.stderr)

    min_age = int(os.getenv("MIN_ACCOUNT_AGE_DAYS", "7"))
    flood_max = int(os.getenv("FLOOD_MAX_MESSAGES", "5"))
    flood_win = int(os.getenv("FLOOD_WINDOW_SECONDS", "10"))
    captcha_to = int(os.getenv("CAPTCHA_TIMEOUT_SECONDS", "120"))

    patterns_raw = os.getenv("BANNED_PATTERNS", "").strip()
    patterns: list[re.Pattern[str]] = []
    if patterns_raw:
        # Паттерны разделяются двойным пайпом "||" (чтобы не путать с regex "|").
        # Каждый паттерн — самостоятельный regex, компилируется отдельно.
        for part in patterns_raw.split("||"):
            part = part.strip()
            if not part:
                continue
            try:
                patterns.append(re.compile(part))
            except re.error as e:
                raise RuntimeError(f"Битый regex в BANNED_PATTERNS: {part!r} ({e})") from e

    db_path = Path(os.getenv("DB_PATH", "./data/bot.db")).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    return Settings(
        bot_token=bot_token,
        chat_id=chat_id,
        admin_ids=admin_ids,
        min_account_age_days=min_age,
        flood_max_messages=flood_max,
        flood_window_seconds=flood_win,
        captcha_timeout_seconds=captcha_to,
        banned_patterns=tuple(patterns),
        db_path=db_path,
    )
