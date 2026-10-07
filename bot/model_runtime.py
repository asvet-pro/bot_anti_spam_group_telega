"""Загрузка ONNX-модели и инференс.

Использует onnxruntime (CPU) + tokenizers для предобработки текста.
В рантайме нет ни PyTorch, ни transformers — только эти две библиотеки и numpy.

Модель и токенизатор лежат в одном каталоге (получаются scripts/export_onnx.py):
    models/rubert-tiny-toxicity/
    ├── model.onnx
    ├── tokenizer.json
    ├── config.json
    └── special_tokens_map.json
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

logger = logging.getLogger(__name__)


# Максимальная длина в токенах. 128 хватает для чат-сообщений.
DEFAULT_MAX_LENGTH = 128


@dataclass(slots=True)
class MLSpamPrediction:
    score: float        # вероятность целевой метки (0.0..1.0)
    label: str          # какая метка (toxic / insult / etc.)
    all_scores: dict[str, float]  # все метки и их вероятности (для отладки/логов)


class MLSpamClassifier:
    """Обёртка над ONNX-моделью text-classification.

    Не async — onnxruntime.Session.run блокирующий и CPU-bound.
    Зовите через asyncio.to_thread(...) из хендлеров.
    """

    def __init__(
        self,
        model_dir: Path,
        *,
        max_length: int = DEFAULT_MAX_LENGTH,
        intra_op_num_threads: int | None = None,
    ) -> None:
        self.model_dir = model_dir
        self.max_length = max_length
        self.tokenizer: Tokenizer | None = None
        self.session: ort.InferenceSession | None = None
        self.labels: list[str] = []
        self.input_names: dict[str, str] = {}
        self.output_name: str = ""

        model_path = self._find_model_file(model_dir)
        tokenizer_path = self._find_tokenizer_file(model_dir)
        config_path = self._find_config_file(model_dir)

        logger.info("Loading ML model from %s", model_dir)

        sess_opts = ort.SessionOptions()
        # По умолчанию 1 поток — для одного-двух сообщений в секунду
        # это эффективнее, чем гонять по всем ядрам (overhead на синхронизацию).
        sess_opts.intra_op_num_threads = intra_op_num_threads or 1
        sess_opts.inter_op_num_threads = 1
        sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        self.session = ort.InferenceSession(
            str(model_path),
            sess_options=sess_opts,
            providers=["CPUExecutionProvider"],
        )
        self.tokenizer = Tokenizer.from_file(str(tokenizer_path))
        # Без pad-токенайзера паддинг вручную.
        pad_id = self.tokenizer.token_to_id("[PAD]")
        if pad_id is None:
            pad_id = 0
        self.pad_id = int(pad_id)
        # Отключаем встроенный паддинг токенайзера — будем паддить сами
        # массивами numpy, чтобы совпадало с тем, что ожидает модель.
        self.tokenizer.enable_padding(
            pad_id=self.pad_id, pad_token="[PAD]", length=max_length
        )
        self.tokenizer.enable_truncation(max_length=max_length)

        # Запоминаем имена входов
        self.input_names = {i.name: i for i in self.session.get_inputs()}
        self.output_name = self.session.get_outputs()[0].name

        # Метки
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
        self.labels = _extract_labels(cfg)
        logger.info(
            "ML model loaded: %d labels=%s, max_length=%d",
            len(self.labels),
            self.labels,
            self.max_length,
        )

    @staticmethod
    def _find_model_file(d: Path) -> Path:
        # optimum сохраняет model.onnx; на всякий случай — поиск.
        for name in ("model.onnx", "model_optimized.onnx"):
            p = d / name
            if p.exists():
                return p
        # Фолбэк — любой .onnx в каталоге
        candidates = sorted(d.glob("*.onnx"))
        if not candidates:
            raise FileNotFoundError(
                f"Не найден .onnx в {d}. Сначала запусти scripts/export_onnx.py"
            )
        return candidates[0]

    @staticmethod
    def _find_tokenizer_file(d: Path) -> Path:
        p = d / "tokenizer.json"
        if not p.exists():
            raise FileNotFoundError(
                f"Нет tokenizer.json в {d}. Перезапусти export_onnx.py"
            )
        return p

    @staticmethod
    def _find_config_file(d: Path) -> Path:
        p = d / "config.json"
        if not p.exists():
            raise FileNotFoundError(
                f"Нет config.json в {d}. Перезапусти export_onnx.py"
            )
        return p

    def predict(self, text: str) -> MLSpamPrediction:
        assert self.tokenizer is not None and self.session is not None

        # 1) Токенизация
        enc = self.tokenizer.encode(text)
        input_ids = np.array([enc.ids], dtype=np.int64)
        attention_mask = np.array([enc.attention_mask], dtype=np.int64)
        # token_type_ids нужны только BERT-моделям. Подаём нули,
        # если модель их не запрашивает — лишние входы игнорируются.
        token_type_ids = np.zeros_like(input_ids, dtype=np.int64)

        feeds: dict[str, Any] = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
        }
        if "token_type_ids" in self.input_names:
            feeds["token_type_ids"] = token_type_ids

        # 2) Инференс
        outputs = self.session.run([self.output_name], feeds)
        logits = outputs[0][0]  # первый элемент батча, [num_labels]

        # 3) Softmax → вероятности
        probs = _softmax(logits)

        # 4) Собираем {label: score}
        if len(self.labels) == len(probs):
            scores = dict(zip(self.labels, [float(x) for x in probs]))
        else:
            # Меток нет (или число не совпадает) — нумеруем
            scores = {f"label_{i}": float(p) for i, p in enumerate(probs)}

        # 5) Решающая метка — argmax
        best_label = max(scores, key=scores.get)  # type: ignore[arg-type]
        best_score = scores[best_label]
        return MLSpamPrediction(
            score=best_score,
            label=best_label,
            all_scores=scores,
        )


def _softmax(x: np.ndarray) -> np.ndarray:
    x = x - np.max(x)
    e = np.exp(x)
    return e / e.sum()


def _extract_labels(cfg: dict) -> list[str]:
    """Достаём имена меток из config.json.

    Поддерживаем форматы:
      - {"id2label": {"0": "neutral", "1": "toxic", ...}}
      - {"id2label": [{"id": "0", "label": "neutral"}, ...]}
      - {"label2id": {"neutral": 0, ...}}
    """
    id2label = cfg.get("id2label")
    if id2label:
        if isinstance(id2label, dict):
            # Ключи могут быть строками или интами
            items: list[tuple[int, str]] = []
            for k, v in id2label.items():
                try:
                    idx = int(k)
                except (TypeError, ValueError):
                    continue
                items.append((idx, v))
            items.sort()
            return [label for _, label in items]
        if isinstance(id2label, list):
            return [item["label"] for item in sorted(id2label, key=lambda x: x.get("id", 0))]
    label2id = cfg.get("label2id")
    if isinstance(label2id, dict):
        return [label for label, _ in sorted(label2id.items(), key=lambda kv: kv[1])]
    return []
