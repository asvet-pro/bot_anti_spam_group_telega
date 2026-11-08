"""Одноразовый экспорт HF-модели в ONNX.

Использование:

    uv sync --extra export
    uv run python scripts/export_onnx.py

По умолчанию экспортирует RUSpam/spam_deberta_v4 в ./models/rubert-spam-deberta/.
Модель и токенизатор кладутся в один каталог, который потом читает bot/model_runtime.py.

Запускается также при сборке Docker-образа (см. Dockerfile).

Про модели:
- RUSpam/spam_deberta_v4 — детектор СПАМА, обучен на русскоязычных
  сообщениях из Telegram-чатов. Класс 1 = спам, класс 0 = не спам.
  F1 = 0.99 по README модели.
- cointegrated/rubert-tiny-toxicity — детектор ТОКСИЧНОСТИ (мат, оскорбления).
  Рекламу не ловит вообще. Оставлен как запасной вариант.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


# Модель по умолчанию — детектор спама (под задачу «реклама услуг»).
DEFAULT_MODEL = "RUSpam/spam_deberta_v4"

# Имя меток для моделей, у которых в config.json нет id2label.
# Ключ — id модели, значение — список меток по индексам.
LABEL_OVERRIDES: dict[str, list[str]] = {
    # README модели: predicted_class == 1 → «Спам», 0 → «Не спам».
    "RUSpam/spam_deberta_v4": ["not-spam", "spam"],
    "cointegrated/rubert-tiny-toxicity": [
        "non-toxic", "insult", "obscenity", "threat", "dangerous",
    ],
}


def _default_outdir(model_name: str) -> Path:
    """Каталог для сохранения артефактов модели.

    Для DEFAULT_MODEL кладём в models/rubert-spam-deberta/ — это
    совпадает с дефолтом ML_MODEL_DIR в bot/config.py. Для остальных
    моделей — models/<org>__<name>/, пользователь пропишет путь в .env.
    """
    if model_name == DEFAULT_MODEL:
        return Path("models") / "rubert-spam-deberta"
    safe = model_name.replace("/", "__")
    return Path("models") / safe


def _apply_label_overrides(model_name: str, out_dir: Path) -> None:
    """Прописывает id2label в config.json, если модель их не содержит.

    Без этого bot/model_runtime.py не знает, какой индекс означает спам,
    и трактует все метки как «плохие» (DEFAULT_BAD_LABELS).
    """
    labels = LABEL_OVERRIDES.get(model_name)
    if not labels:
        return

    cfg_path = out_dir / "config.json"
    if not cfg_path.exists():
        return

    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    if cfg.get("id2label"):
        print(
            f"[export_onnx] id2label уже задан: {cfg['id2label']}",
            file=sys.stderr,
        )
        return

    cfg["id2label"] = {str(i): name for i, name in enumerate(labels)}
    cfg["label2id"] = {name: i for i, name in enumerate(labels)}
    cfg["num_labels"] = len(labels)
    cfg_path.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"[export_onnx] проставлены метки: {cfg['id2label']}", file=sys.stderr
    )


def export(model_name: str, out_dir: Path) -> Path:
    # Импорты внутри функции, чтобы этот модуль можно было импортировать
    # в проде (где optimum нет) без ошибок.
    from optimum.onnxruntime import ORTModelForSequenceClassification
    from transformers import AutoTokenizer

    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[export_onnx] model={model_name!r} -> {out_dir}", file=sys.stderr)

    print("[export_onnx] downloading tokenizer...", file=sys.stderr)
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    tokenizer.save_pretrained(out_dir)

    print("[export_onnx] exporting to ONNX (this can take a minute)...", file=sys.stderr)
    model = ORTModelForSequenceClassification.from_pretrained(
        model_name,
        export=True,
    )
    model.save_pretrained(out_dir)

    _apply_label_overrides(model_name, out_dir)

    print("[export_onnx] done. Artifacts in {out_dir}", file=sys.stderr)
    for p in sorted(out_dir.iterdir()):
        size_mb = p.stat().st_size / 1024 / 1024
        print(f"  {p.name:40s} {size_mb:6.2f} MB", file=sys.stderr)
    return out_dir


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"HuggingFace id модели (default: {DEFAULT_MODEL})",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Каталог для сохранения (default: models/<model>)",
    )
    args = p.parse_args(argv)

    out = args.out or _default_outdir(args.model)
    try:
        export(args.model, out)
    except Exception as e:  # noqa: BLE001
        print(f"[export_onnx] FAILED: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
