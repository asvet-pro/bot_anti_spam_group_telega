# syntax=docker/dockerfile:1
# --- Этап 1: зависимости (кешируется отдельно) ---
FROM python:3.12-slim AS deps

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl unzip ca-certificates && rm -rf /var/lib/apt/lists/*

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

# sing-box (для VLESS-обхода блокировок Telegram).
# Без него бот не сможет достучаться до api.telegram.org на заблокированных хостингах.
ARG SING_VERSION=1.12.22
RUN apt-get update && apt-get install -y --no-install-recommends curl tar ca-certificates \
    && curl -fsSL -o /tmp/sing-box.tar.gz https://github.com/SagerNet/sing-box/releases/download/v${SING_VERSION}/sing-box-${SING_VERSION}-linux-amd64.tar.gz \
    && tar -xzf /tmp/sing-box.tar.gz -C /tmp \
    && mv /tmp/sing-box-${SING_VERSION}-linux-amd64/sing-box /usr/local/bin/sing-box \
    && chmod +x /usr/local/bin/sing-box \
    && apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/sing-box.tar.gz /tmp/sing-box-${SING_VERSION}-linux-amd64

# Копируем уже установленное окружение из первого этапа
COPY --from=deps /app /app

# Папка для SQLite. Coolify сюда монтирует volume.
RUN mkdir -p /app/data /app/logs
VOLUME ["/app/data"]

# Переменные окружения, которые ОБЯЗАТЕЛЬНО нужно задать в Coolify:
#   BOT_TOKEN
#   CHAT_ID
#   ADMIN_IDS
# (опционально: MIN_ACCOUNT_AGE_DAYS, FLOOD_*, CAPTCHA_*, BANNED_PATTERNS, DB_PATH,
#  VLESS_URL — если Telegram заблокирован на сервере)
# Coolify подхватит их из настроек ресурса.

# Логи — в stdout/stderr, чтобы Coolify их видел.
ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8

# По умолчанию БД лежит в /app/data/bot.db (volume).
# Менять через DB_PATH в env, если хочешь другой путь.

CMD ["uv", "run", "python", "-m", "bot"]
