# syntax=docker/dockerfile:1
# --- Этап 1: зависимости (кешируется отдельно) ---
FROM python:3.12-slim AS deps

WORKDIR /app

# uv ставим один раз
RUN pip install --no-cache-dir uv

# Копируем только манифест и код для установки зависимостей
COPY pyproject.toml README.md ./
COPY bot/ ./bot/
RUN uv sync --no-dev

# --- Этап 2: финальный образ ---
FROM python:3.12-slim

WORKDIR /app

# uv во втором этапе (он маленький, но нужен для `CMD`)
RUN pip install --no-cache-dir uv

# Копируем уже установленное окружение из первого этапа
COPY --from=deps /app /app

# Папка для SQLite. Coolify сюда монтирует volume.
RUN mkdir -p /app/data /app/logs
VOLUME ["/app/data"]

# Переменные окружения, которые ОБЯЗАТЕЛЬНО нужно задать в Coolify:
#   BOT_TOKEN
#   CHAT_ID
#   ADMIN_IDS
# (опционально: MIN_ACCOUNT_AGE_DAYS, FLOOD_*, CAPTCHA_*, BANNED_PATTERNS, DB_PATH)
# Coolify подхватит их из настроек ресурса.

# Логи — в stdout/stderr, чтобы Coolify их видел.
ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8

# По умолчанию БД лежит в /app/data/bot.db (volume).
# Менять через DB_PATH в env, если хочешь другой путь.

CMD ["uv", "run", "python", "-m", "bot"]
