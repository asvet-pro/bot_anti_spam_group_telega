"""Одноразовый экспорт HF-модели в ONNX.

Использование:

    uv sync --extra export
    uv run python scripts/export_onnx.py

По умолчанию экспортирует cointegrated/rubert-tiny-toxicity в ./models/rubert-tiny-toxicity/.
Модель и токенизатор кладутся в один каталог, который потом читает bot/model_runtime.py.

Запускается также при сборке Docker-образа (см. Dockerfile).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


DEFAULT_MODEL = "cointegrated/rubert-tiny-toxicity"


def _default_outdir(model_name: str) -> Path:
    """Каталог по умолчанию для модели.

    Для DEFAULT_MODEL кладём в models/rubert-tiny-toxicity/ — это
    совпадает с дефолтом ML_MODEL_DIR в bot/config.py. Для остальных
    моделей — models/<org>__<name>/, пользователь пропишет путь в .env.
    """
    if model_name == DEFAULT_MODEL:
        return Path("models") / "rubert-tiny-toxicity"
    safe = model_name.replace("/", "__")
    return Path("models") / safe


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

    print(f"[export_onnx] done. Artifacts in {out_dir}", file=sys.stderr)
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
