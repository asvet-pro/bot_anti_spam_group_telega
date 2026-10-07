"""ML-фильтр токсичности/спама через ONNX.

Поведение:
- Загружает модель через bot.model_runtime.MLSpamClassifier.
- Решающее правило: max(scores[bad_labels]) >= threshold,
  где bad_labels — список меток, которые считаем «плохими»
  (toxic, insult, threat, dangerous и т.п.).
- enabled=False, если модель не загрузилась (например, нет .onnx
  на диске). Тогда хендлер messages.py молча пропускает этот фильтр.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from bot.model_runtime import MLSpamClassifier, MLSpamPrediction

logger = logging.getLogger(__name__)


# Метки, по которым решаем «плохое/не плохое». Модель
# rubert-tiny-toxicity выдаёт: non-toxic, insult, obscenity, threat, dangerous.
# Берём все, кроме non-toxic — то есть считаем, что «токсичность» это всё,
# что модель НЕ назвала non-toxic.
DEFAULT_BAD_LABELS = (
    "insult",
    "obscenity",
    "threat",
    "dangerous",
    "toxic",
    "hate",
)


@dataclass(slots=True)
class MLSpamCheck:
    """Тонкая обёртка над MLSpamClassifier, в стиле остальных фильтров."""

    classifier: MLSpamClassifier | None
    threshold: float
    bad_labels: tuple[str, ...] = DEFAULT_BAD_LABELS

    @property
    def enabled(self) -> bool:
        return self.classifier is not None

    def _bad_score(self, pred: MLSpamPrediction) -> float:
        """Возвращает максимальную вероятность среди bad_labels.

        Если каких-то меток нет в выдаче — пропускаем. Если ВСЕ нужные
        метки отсутствуют — берём 1 - non-toxic_score (всё, что модель
        не назвала «безопасным»).
        """
        scores = pred.all_scores
        bad_present = [scores[lbl] for lbl in self.bad_labels if lbl in scores]
        if bad_present:
            return max(bad_present)
        # Фолбэк для моделей с неожиданным набором меток
        non_toxic = scores.get("non-toxic", scores.get("neutral"))
        if non_toxic is not None:
            return 1.0 - non_toxic
        return 0.0

    def is_spam(self, text: str) -> tuple[bool, MLSpamPrediction | None]:
        """Возвращает (is_spam, prediction).

        Если фильтр выключен — (False, None). Если предсказание не
        превышает порог — (False, prediction). Иначе (True, prediction).
        """
        if not self.enabled or self.classifier is None:
            return False, None
        try:
            pred = self.classifier.predict(text)
        except Exception as e:  # noqa: BLE001 — не валим бот из-за одного сообщения
            logger.warning("ML predict failed: %s", e)
            return False, None
        score = self._bad_score(pred)
        return score >= self.threshold, pred
