"""Фильтр спам-сообщений по regex-паттернам."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(slots=True)
class SpamFilter:
    patterns: tuple[re.Pattern[str], ...]

    def match(self, text: str) -> re.Pattern[str] | None:
        """Возвращает первый совпавший паттерн или None."""
        for p in self.patterns:
            if p.search(text):
                return p
        return None

    @property
    def enabled(self) -> bool:
        return bool(self.patterns)
