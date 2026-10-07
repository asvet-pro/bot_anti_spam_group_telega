"""Управляемый стоп-лист: слова и regex-паттерны.

Списки живут в БД и редактируются через /help в ЛС.
Фильтр кеширует содержимое на cache_ttl секунд (по умолчанию 5),
чтобы не дёргать БД на каждое сообщение. После любой мутации через
бота вызывается invalidate() — следующий же вызов match_*
перечитает БД.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field

from bot.db import Database

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class BlocklistFilter:
    db: Database
    cache_ttl: float = 5.0

    # Скомпилированные данные (не сохраняются на диск, инициализируются лениво).
    _words: tuple[str, ...] = field(default_factory=tuple)
    _patterns: tuple[re.Pattern[str], ...] = field(default_factory=tuple)
    _loaded_at: float = 0.0
    _lock: asyncio.Lock | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self._lock is None:
            self._lock = asyncio.Lock()

    def invalidate(self) -> None:
        """Сбрасывает кеш — следующий match_* перечитает БД."""
        self._loaded_at = 0.0
        self._words = ()
        self._patterns = ()

    async def _ensure_loaded(self) -> None:
        """Подтягивает данные из БД, если кеш истёк."""
        assert self._lock is not None
        now = time.monotonic()
        if (now - self._loaded_at) < self.cache_ttl and self._words is not None:
            return
        async with self._lock:
            # Повторная проверка под локом — вдруг уже подгрузили.
            now = time.monotonic()
            if (now - self._loaded_at) < self.cache_ttl and self._words:
                return
            try:
                words = await self.db.all_words()
                raw_patterns = await self.db.all_patterns()
            except Exception as e:  # noqa: BLE001
                logger.warning("Blocklist load failed: %s", e)
                # Не валим фильтр — оставляем пустые списки.
                self._words = ()
                self._patterns = ()
                self._loaded_at = now
                return
            compiled: list[re.Pattern[str]] = []
            for pat in raw_patterns:
                try:
                    compiled.append(re.compile(pat))
                except re.error as e:
                    logger.warning(
                        "Blocklist has invalid pattern %r: %s — skipping",
                        pat, e,
                    )
            self._words = words
            self._patterns = tuple(compiled)
            self._loaded_at = now

    async def match_word(self, text: str) -> str | None:
        """Возвращает первое сматчившееся слово (lowercase) или None."""
        if not text:
            return None
        await self._ensure_loaded()
        lowered = text.lower()
        for w in self._words:
            if w and w in lowered:
                return w
        return None

    async def match_pattern(self, text: str) -> re.Pattern[str] | None:
        """Возвращает первый сматчившийся regex или None."""
        if not text:
            return None
        await self._ensure_loaded()
        for p in self._patterns:
            if p.search(text):
                return p
        return None

    @property
    def enabled(self) -> bool:
        """True, если в кеше есть хоть что-то. Не дёргает БД."""
        return bool(self._words) or bool(self._patterns)
