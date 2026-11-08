"""ML-фильтр спама/токсичности через ONNX.

Поведение:
- Загружает модель через bot.model_runtime.MLSpamClassifier.
- Решающее правило: max(scores[bad_labels]) >= threshold,
  где bad_labels — список меток, которые считаем «плохими».
- enabled=False, если модель не загрузилась (например, нет .onnx
  на диске). Тогда хендлер messages.py молча пропускает этот фильтр.

Модели:
- RUSpam/spam_deberta_v4 (по умолчанию) — детектор СПАМА для русских
  сообщений, метки ['not-spam', 'spam']. Ловит рекламу услуг, «заработок»,
  набор в команду и т.п.
- cointegrated/rubert-tiny-toxicity — детектор ТОКСИЧНОСТИ, метки
  ['non-toxic', 'insult', 'obscenity', 'threat', 'dangerous'].
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from bot.model_runtime import MLSpamClassifier, MLSpamPrediction

logger = logging.getLogger(__name__)


# Метки, которые считаем «плохими» по умолчанию.
#
# Для детектора спама (RUSpam) плохая метка одна — 'spam'.
# Для детектора токсичности (rubert-tiny-toxicity) — все, кроме non-toxic.
DEFAULT_BAD_LABELS = (
    "spam",
    "insult",
    "obscenity",
    "threat",
    "dangerous",
    "toxic",
    "hate",
)

# Метки, которые НИКОГДА не считаем плохими (точно «не спам»).
SAFE_LABELS = (
    "non-toxic",
    "not-spam",
    "neutral",
    "safe",
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
        """Возвращает максимальную вероятность среди плохих меток.

        Если ни одной плохой метки нет в выдаче модели — считаем, что
        сообщение безопасное (0.0), а не «1 минус non-toxic». Иначе
        детектор спама, у которого в выдаче только spam/not-spam, считал бы
        любое нормальное сообщение подозрительным.
        """
        scores = pred.all_scores
        bad_present = [
            scores[lbl] for lbl in self.bad_labels
            if lbl in scores and lbl not in SAFE_LABELS
        ]
        if bad_present:
            return max(bad_present)
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
