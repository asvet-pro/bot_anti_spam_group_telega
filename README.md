# ASVET Antispam Bot

Антиспам-бот для Telegram-чата про AI и автоматизацию в бизнесе.

## Что умеет

- **Капча на вход.** Новый участник должен за 2 минуты нажать «Я не робот» — иначе кик.
- **Фильтр свежих аккаунтов.** Если аккаунту меньше `MIN_ACCOUNT_AGE_DAYS` (по умолчанию 7 дней) — бан при вступлении.
- **Антифлуд.** Больше `FLOOD_MAX_MESSAGES` сообщений за `FLOOD_WINDOW_SECONDS` — временный бан на 5 минут.
- **Спам-фильтр по regex.** Список триггер-паттернов в `.env` (`BANNED_PATTERNS`). Срабатывание — удаление сообщения + инкремент счётчика.
- **Админ-команды в ЛС с ботом.** `/ban`, `/tban`, `/unban`, `/whitelist_add`, `/whitelist_del`, `/stats`, `/id`.
- **Белый список.** Участники в вайтлисте не проверяются фильтрами.
- **Статистика.** `/stats` показывает, сколько банов, удалённых сообщений, проваленных капч и т.д.
- **Хранилище — SQLite** (`data/bot.db`). Никаких внешних БД.

## Стек

- Python 3.11+
- [aiogram 3](https://docs.aiogram.dev/) (async)
- [aiosqlite](https://github.com/omnilib/aiosqlite) для хранения
- [uv](https://docs.astral.sh/uv/) для управления окружением
- Systemd для запуска 24/7 на VPS

## Структура

```
.
├── bot/
│   ├── __main__.py          # точка входа
│   ├── config.py            # загрузка настроек из .env
│   ├── db.py                # SQLite + репозиторий
│   ├── texts.py             # все тексты сообщений
│   ├── filters/             # проверки (new_account, flood, spam, admin)
│   ├── handlers/            # captcha, messages, admin
│   └── middlewares/deps.py  # инъекция зависимостей в хендлеры
├── scripts/init_db.py       # ручная инициализация БД
├── deploy/
│   ├── asvet-antispam.service
│   └── install.sh
├── pyproject.toml
├── .env.example
└── README.md
```

## Установка локально (для разработки)

```bash
# Установить uv, если ещё нет
# Windows (PowerShell):
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
# Linux/macOS:
curl -LsSf https://astral.sh/uv/install.sh | sh

# Скопировать и заполнить .env
cp .env.example .env       # Linux/macOS
copy .env.example .env     # Windows PowerShell
# затем отредактировать .env

# Установить зависимости
uv sync

# Запустить
uv run python -m bot
```

## Подготовка бота и чата (один раз)

1. **Создай бота** в [@BotFather](https://t.me/BotFather), скопируй токен в `.env` → `BOT_TOKEN`.
2. **Создай группу/чат**, в который добавишь бота.
3. **Добавь бота в чат** и сделай его **админом** с правами:
   - Удалять сообщения
   - Банить пользователей
   - Приглашать пользователей (для капчи не обязательно, но удобно)
4. **Узнай `chat_id`** группы. Самый простой путь:
   - В группе отправь любое сообщение
   - Открой `https://api.telegram.org/bot<BOT_TOKEN>/getUpdates`
   - В JSON найди `chat.id` группы (будет отрицательным, вида `-100…`)
   - Положи это число в `.env` → `CHAT_ID`
5. **Узнай свой `user_id`** через [@userinfobot](https://t.me/userinfobot) или [@RawDataBot](https://t.me/RawDataBot), положи в `.env` → `ADMIN_IDS`.

## Деплой на VPS (Ubuntu/Debian)

```bash
# На локальной машине: упакуй проект (без .env и data/)
cd D:\Workspace\БотАнтиспам
# через git или scp — как удобно. Пример с scp:
scp -r . user@your-vps:/tmp/asvet-antispam

# На VPS:
ssh user@your-vps
sudo mv /tmp/asvet-antispam /opt/
cd /opt/asvet-antispam
cp .env.example .env
nano .env                  # заполни BOT_TOKEN, CHAT_ID, ADMIN_IDS
chmod 600 .env

sudo bash deploy/install.sh
```

После установки:

```bash
sudo systemctl status asvet-antispam      # статус
sudo journalctl -u asvet-antispam -f      # логи в реальном времени
sudo systemctl restart asvet-antispam     # перезапуск
```

## Деплой через Coolify (рекомендуется)

Coolify — self-hosted PaaS. Деплой сводится к «подключил Git-репо → задал env → volume → Deploy».

### Шаг 1. Залей код в Git

```bash
cd D:\Workspace\БотАнтиспам
git init
git add .
git commit -m "init: asvet antispam bot"
# создай пустой репозиторий на GitHub/GitLab/Gitea, затем:
git remote add origin <url>
git push -u origin main
```

> В репо **не попадёт** `.env`, `data/`, `.venv/`, `logs/` — они в `.gitignore` и `.dockerignore`.

### Шаг 2. Создай ресурс в Coolify

1. В Coolify: **+ New Resource** → **Application**
2. **Source**: Public/Private Repository → вставь URL репо
3. **Build Pack**: `Dockerfile` (Coolify определит автоматически)
4. **Branch**: `main`
5. **Port**: оставь пустым — бот не слушает порты
6. **Persistent Storage** (вкладка Storage):
   - `Destination` = `/app/data`
   - `Source` = named volume, например `asvet-bot-data`
7. **Environment Variables** (вкладка Environment) — задай:

   | Key | Value |
   |---|---|
   | `BOT_TOKEN` | токен из @BotFather |
   | `CHAT_ID` | `-100...` (отрицательный) |
   | `ADMIN_IDS` | твой Telegram ID |
   | `BANNED_PATTERNS` | `(?i)казино\|\|(?i)крипто\|\|...` (из `.env`) |
   | `MIN_ACCOUNT_AGE_DAYS` | `7` |
   | `FLOOD_MAX_MESSAGES` | `5` |
   | `FLOOD_WINDOW_SECONDS` | `10` |
   | `CAPTCHA_TIMEOUT_SECONDS` | `120` |
   | `DB_PATH` | `/app/data/bot.db` |

8. Нажми **Deploy**

### Шаг 3. Проверь

Coolify → ресурс → вкладка **Logs**. Должно появиться:

```
Settings loaded: chat_id=...
DB initialized at /app/data/bot.db
Starting polling…
Run polling for bot @asvet_probot
```

В Telegram: попроси кого-то зайти в чат — должна прилететь капча. Если в логах ошибки — см. таблицу граблей ниже.

### Шаг 4. Автообновление

Каждый `git push` → Coolify сам пересоберёт и перезапустит контейнер. Никаких ручных `docker pull` / `systemctl restart`.

## Обновление (VPS-вариант)

```bash
# На локальной машине
cd D:\Workspace\БотАнтиспам
scp -r . user@your-vps:/tmp/asvet-antispam-new

# На VPS
ssh user@your-vps
sudo systemctl stop asvet-antispam
sudo rm -rf /opt/asvet-antispam
sudo mv /tmp/asvet-antispam-new /opt/asvet-antispam
cd /opt/asvet-antispam
sudo chown -R asvet:asvet .
sudo bash deploy/install.sh
```

## Команды бота (только в ЛС, только для админов)

| Команда | Что делает |
|---|---|
| `/help` (или `/start`) | Inline-меню с командами и настройками (только в ЛС) |
| `/ban @user [причина]` | Бан навсегда (можно reply) |
| `/tban @user <мин> [причина]` | Временный бан на N минут |
| `/unban @user` | Разбанить |
| `/whitelist_add @user` | В белый список (мимо всех фильтров) |
| `/whitelist_del @user` | Из белого списка |
| `/stats` | Статистика по событиям |
| `/id` | Показать `chat.id` и `user.id` (в любом чате) |

## Настройка антиспама

Все пороги — в `.env`:

| Параметр | Что делает | По умолчанию |
|---|---|---|
| `MIN_ACCOUNT_AGE_DAYS` | Минимальный возраст аккаунта | `7` |
| `FLOOD_MAX_MESSAGES` | Сколько сообщений в окне | `5` |
| `FLOOD_WINDOW_SECONDS` | Размер окна (сек) | `10` |
| `CAPTCHA_TIMEOUT_SECONDS` | Сколько ждать капчу | `120` |
| `BANNED_PATTERNS` | Regex-паттерны спама, через `\|` | (набор для AI-чата) |

### Добавление своих спам-паттернов

В `.env` поле `BANNED_PATTERNS` — это **несколько regex-паттернов через `||`** (двойной пайп, чтобы не конфликтовать с `|` внутри самих regex). Каждый паттерн — самостоятельный regex, компилируется отдельно. Чтобы не учитывать регистр, добавь `(?i)` в начало.

```bash
# Ловить конкретные слова/фразы (без учёта регистра):
BANNED_PATTERNS=(?i)заработ[и]?к||(?i)казино||(?i)крипто||(?i)1xbet

# Запретить все ссылки, кроме t.me/your_chat:
BANNED_PATTERNS=http(s)?://(?!t\.me/your_chat)
```

## План развития (после MVP)

- [ ] Логирование всех действий в файл (для разбора споров)
- [ ] Дашборд в ЛС (кнопки: «Топ спамеров за день», «Кого сегодня забанили»)
- [ ] ML-классификатор спама (легковесный, через transformers/onnx)
- [ ] Режим тишины (бот-админ может поставить «без новых сообщений 1 час»)
- [ ] Реакция 🚫 на подозрительные сообщения вместо удаления (мягкий режим)

## Лицензия

MIT
