# ASVET Antispam Bot

Антиспам-бот для Telegram-чата про AI и автоматизацию в бизнесе.

## Что умеет

- **Капча на вход.** Новый участник должен за 2 минуты нажать «Я не робот» — иначе кик.
- **Фильтр свежих аккаунтов.** Если аккаунту меньше `MIN_ACCOUNT_AGE_DAYS` (по умолчанию 7 дней) — бан при вступлении. Возраст оценивается по `user_id` линейной интерполяцией (Telegram не отдаёт дату регистрации); на реальных парах id→дата оценка точная, а для слишком старых id бан не применяется.
- **Антифлуд.** Больше `FLOOD_MAX_MESSAGES` сообщений за `FLOOD_WINDOW_SECONDS` — сообщение удаляется, автоматического бана нет (чтобы не задеть живых).
- **Детектор дубликатов.** Одинаковые сообщения подряд (типичный спам «реклама ×10») удаляются. Сравнение по нормализованному тексту (регистр и пробелы игнорируются), короткие реплики («да», «ок») не считаются дублями.
- **ML-фильтр токсичности.** Готовая модель `cointegrated/rubert-tiny-toxicity` через onnxruntime (без PyTorch в рантайме). Ловит оскорбления, угрозы, нецензурную брань. Порог в `.env` (`ML_THRESHOLD`).
- **Мягкий режим + разметка спама.** `REVIEW_MODE=true` — подозрительное сообщение не удаляется сразу, а приходит админу в личку с кнопками **🚫 Спам / ✅ Ок / ⏭ В стоп-лист**. Ответы админа попадают в таблицу `spam_reports` — это датасет для последующего обучения модели.
- **Управляемый стоп-лист (слова + regex).** Добавляйте/удаляйте слова и regex-паттерны из `/help` → 🚫 Стоп-лист, без перезапуска бота. Изменения применяются сразу.
- **Спам-фильтр по regex из `.env` (`BANNED_PATTERNS`)** — базовый список для обратной совместимости, остаётся как есть.
- **Админы группы неприкосновенны.** Кроме `ADMIN_IDS`, бот сам подтягивает список администраторов чата (`getChatAdministrators`) — сообщения админов группы не удаляются, даже если их id нет в `ADMIN_IDS`.
- **Админ-команды в ЛС с ботом.** `/ban`, `/tban`, `/unban`, `/whitelist_add`, `/whitelist_del`, `/blocklist_*`, `/stats`, `/id`.
- **Белый список.** Участники в вайтлисте не проверяются фильтрами.
- **Статистика.** `/stats` показывает баны, удалённые сообщения, проваленные капчи и количество размеченных спам/ок. `/reports` — последние размеченные сообщения.
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
| `BANNED_PATTERNS` | Regex-паттерны спама, через `\|\|` | (набор для AI-чата) |
| `ML_ENABLED` | Включить ML-фильтр токсичности | `true` |
| `ML_MODEL_DIR` | Каталог с моделью (`./models/rubert-tiny-toxicity`) | — |
| `ML_THRESHOLD` | Порог «плохой» вероятности (0.0..1.0) | `0.6` |
| `ML_BAD_LABELS` | Какие метки считать плохими (через запятую) | авто (всё, кроме `non-toxic`) |
| `DUPLICATE_MAX_REPEATS` | Сколько одинаковых сообщений подряд считать спамом | `2` |
| `DUPLICATE_WINDOW_SECONDS` | Окно поиска повторов (сек) | `600` |
| `REVIEW_MODE` | Мягкий режим: подозрительное → админу в личку на разбор | `false` |
| `VLESS_URL` | VLESS-прокси (фолбэк; сначала бот пробует идти напрямую) | (пусто) |

### ML-фильтр

Используется предобученная модель `cointegrated/rubert-tiny-toxicity` (русский, multilabel, ≈45 МБ ONNX). В рантайме нет PyTorch — только `onnxruntime` + `tokenizers`. Модель **автоматически экспортируется при сборке Docker-образа**.

Локальный запуск без Docker (если нужно подёргать модель руками):

```bash
uv sync --extra export
HF_HOME=./.hf-cache uv run python scripts/export_onnx.py
# В проде достаточно базовых зависимостей, export нужен только один раз.
```

Если модель не загрузилась (битый файл, нет интернета при билде, `ML_ENABLED=false`) — бот молча переключается на работу только по regex и пишет предупреждение в лог.

### Добавление своих спам-паттернов

В `.env` поле `BANNED_PATTERNS` — это **несколько regex-паттернов через `||`** (двойной пайп, чтобы не конфликтовать с `|` внутри самих regex). Каждый паттерн — самостоятельный regex, компилируется отдельно. Чтобы не учитывать регистр, добавь `(?i)` в начало.

```bash
# Ловить конкретные слова/фразы (без учёта регистра):
BANNED_PATTERNS=(?i)заработ[и]?к||(?i)казино||(?i)крипто||(?i)1xbet

# Запретить все ссылки, кроме t.me/your_chat:
BANNED_PATTERNS=http(s)?://(?!t\.me/your_chat)
```

### Управление стоп-листом из бота

Чтобы **не лазить в `.env` и не перезапускать бот**, слова и regex-паттерны можно добавлять/удалять прямо из `/help` в ЛС с ботом:

1. Откройте бота в ЛС, нажмите `/help` (или `/start`).
2. Нажмите **🚫 Стоп-лист**.
3. Дальше — кнопки:
   - **📝 Слова** — список с кнопкой «❌ удалить» у каждого.
   - **🔧 Regex** — то же для regex-паттернов.
   - **➕ Добавить слово** / **➕ Добавить regex** — бот попросит отправить слово/regex одним сообщением.
   - **🧪 Тест фильтра** — отправляете текст, бот показывает, что из стоп-листа сматчилось.

Изменения применяются **сразу**, без `systemctl restart` и без `git push`. Бот сам инвалидирует кеш после любой мутации.

#### Тот же функционал через команды (вместо inline-кнопок):

```
/blocklist_words
/blocklist_patterns
/blocklist_add_word казино
/blocklist_add_pattern (?i)крипт
/blocklist_del_word 3
/blocklist_del_pattern (?i)крипт
/blocklist_test Куплю крипту
```

Все команды — только в ЛС, только для админов из `ADMIN_IDS`.

### Подстройка ML под себя

- `ML_THRESHOLD=0.8` — реже ошибается, но больше пропускает.
- `ML_THRESHOLD=0.4` — агрессивнее, может задеть безобидные сообщения.
- `ML_BAD_LABELS=insult,threat,dangerous` — игнорировать `obscenity` (если в чате допустимо мат без оскорблений).

## Разметка спама и сбор датасета

Включается переменной `REVIEW_MODE=true`.

Как работает:

1. Фильтр (ML, стоп-слово, стоп-regex или `BANNED_PATTERNS`) срабатывает на
   сообщении в чате.
2. В мягком режиме бот **не удаляет** сообщение, а отправляет его тебе в личку
   с кнопками:
   - **🚫 Спам (удалить)** — удалить в чате, записать в `spam_reports` как спам;
   - **✅ Ок (оставить)** — оставить в чате, записать как норма;
   - **⏭ В стоп-лист** — удалить и добавить текст в стоп-лист (слово подбирается
     автоматически: чаще всего бренд или услуга из сообщения).
3. Из таблицы `spam_reports` потом собирается датасет для обучения модели.

**Что попадает в базу:** только сообщения, которые ты сам отметил. Обычная
переписка участников чата в базу не пишется.

**Команды:**
- `/stats` — статистика + счётчики разметки;
- `/reports` — последние 20 размеченных сообщений.

> Telegram не присылает боту событие «участник сам удалил своё сообщение»,
> поэтому автоматическая разметка возможна только для того, что бот удалил сам.
> В мягком режиме бот ничего не удаляет без твоего решения — так разметка
> получается полной и без ложных меток.

## План развития (после MVP)

- [x] Разметка спама админом (`REVIEW_MODE`) — датасет для обучения
- [ ] Логирование всех действий в файл (для разбора споров)
- [ ] Дашборд в ЛС (кнопки: «Топ спамеров за день», «Кого сегодня забанили»)
- [ ] Дообучение ML на примерах из чата (fine-tune rubert-tiny-toxicity)
- [ ] Режим тишины (бот-админ может поставить «без новых сообщений 1 час»)
- [ ] Реакция 🚫 на подозрительные сообщения вместо удаления (мягкий режим)

## Лицензия

MIT
