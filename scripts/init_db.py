"""Утилита для ручной инициализации БД (на случай деплоя отдельно от бота)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Добавляем корень проекта в sys.path, чтобы запускать uv run python scripts/init_db.py
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.config import load_settings  # noqa: E402
from bot.db import Database  # noqa: E402


async def main() -> None:
    s = load_settings()
    db = Database(s.db_path)
    await db.init()
    print(f"✅ DB initialized at {s.db_path}")


if __name__ == "__main__":
    asyncio.run(main())
