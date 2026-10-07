"""Тексты, которые бот отправляет пользователям."""
from __future__ import annotations


# Приветствие нового участника (в ЛС после вступления в чат).
WELCOME = (
    "👋 Привет, {name}!\n\n"
    "Это чат про AI и автоматизацию в бизнесе. "
    "Чтобы остаться — нажми кнопку ниже в течение {timeout} сек.\n\n"
    "Если кнопка не появилась — открой чат и нажми на сообщение бота."
)

# Кнопка капчи
CAPTCHA_BUTTON = "✅ Я не робот"

# Когда новичок не нажал кнопку
CAPTCHA_FAILED = "⏱ Время вышло. Вы исключены из чата."

# Когда новичок нажал кнопку
CAPTCHA_PASSED = "✅ Готово, добро пожаловать в чат!"

# Когда аккаунт слишком молодой
ACCOUNT_TOO_YOUNG = (
    "⛔ Аккаунт слишком молодой (менее {min_days} дней). "
    "Подожди немного и попробуй снова."
)

# Когда сработал антифлуд
FLOOD_WARNING = "⚠️ Слишком много сообщений. Подожди немного."
FLOOD_BAN = "🚫 Флуд — бан на {minutes} мин."

# Когда сработал фильтр спама
SPAM_DELETED = (
    "🗑 Сообщение удалено как спам.\n"
    "Причина: {reason}"
)

# Когда нового участника забанили при входе
NEW_MEMBER_BANNED = "🚫 {name} забанен: {reason}"

# Общая ошибка
ERROR_GENERIC = "⚠️ Произошла ошибка. Попробуй позже."

# Приветствие бота в ЛС
BOT_DM_HELLO = (
    "👋 Я антиспам-бот чата про AI в бизнесе.\n\n"
    "Команды в самом чате: /help"
)

# Сообщение при бане (админ)
ADMIN_USER_BANNED = "✅ {name} (id={user_id}) забанен."

# Сообщение при разбане
ADMIN_USER_UNBANNED = "♻️ {name} (id={user_id}) разбанен."

# Статистика
ADMIN_STATS = (
    "📊 Статистика\n"
    "─────────────\n"
    "Всего событий: {total}\n"
    "Банов: {bans}\n"
    "Удалённых сообщений: {deleted}\n"
    "Проваленных капч: {captcha_fails}\n"
    "Флуд-варнов: {flood_warns}"
)

# --- Стоп-лист (blocklist) ---

# Заголовок inline-меню
BLOCKLIST_MENU_TEXT = (
    "🚫 <b>Стоп-лист</b>\n\n"
    "Слова и regex, которые автоматически удаляются в чате.\n"
    "Управляются без перезапуска — применяются сразу.\n\n"
    "📝 Слов: <b>{words_count}</b>\n"
    "🔧 Regex: <b>{patterns_count}</b>"
)

BLOCKLIST_WORDS_LIST_HEADER = "📝 <b>Стоп-слова</b> ({count}):\n"
BLOCKLIST_PATTERNS_LIST_HEADER = "🔧 <b>Стоп-regex</b> ({count}):\n"
BLOCKLIST_EMPTY = "  <i>(пусто)</i>"

BLOCKLIST_WORD_ADDED = '✅ Слово «{word}» добавлено в стоп-лист.'
BLOCKLIST_WORD_DELETED = '♻️ Слово «{word}» (id={id}) удалено.'
BLOCKLIST_PATTERN_ADDED = '✅ Regex «{pattern}» добавлен в стоп-лист.'
BLOCKLIST_PATTERN_DELETED = '♻️ Regex «{pattern}» (id={id}) удалён.'
BLOCKLIST_WORD_DUPLICATE = "⚠️ Это слово уже в стоп-листе."
BLOCKLIST_PATTERN_DUPLICATE = "⚠️ Этот regex уже в стоп-листе."
BLOCKLIST_NOT_FOUND = "⚠️ Не нашёл такой записи."
BLOCKLIST_INVALID_REGEX = "⚠️ Битый regex: {error}"
BLOCKLIST_WORD_EMPTY = "⚠️ Слово не может быть пустым."

BLOCKLIST_TEST_HEADER = "🧪 <b>Тест фильтра</b> для: <code>{text}</code>\n"
BLOCKLIST_TEST_HIT_WORD = "  • слово «{word}»"
BLOCKLIST_TEST_HIT_PATTERN = "  • regex <code>{pattern}</code>"
BLOCKLIST_TEST_HIT_LEGACY = "  • legacy BANNED_PATTERNS: <code>{pattern}</code>"
BLOCKLIST_TEST_NONE = "  <i>ничего не сматчилось</i>"

BLOCKLIST_ASK_WORD = (
    "📝 <b>Добавление слова в стоп-лист</b>\n\n"
    "Отправьте слово или фразу одним сообщением.\n"
    "Регистр игнорируется, поиск подстрокой.\n\n"
    "Примеры: <code>казино</code>, <code>binance</code>, <code>сигналы</code>\n\n"
    "Для отмены отправьте /cancel"
)
BLOCKLIST_ASK_PATTERN = (
    "🔧 <b>Добавление regex-паттерна в стоп-лист</b>\n\n"
    "Отправьте regex одним сообщением.\n"
    "Флаги типа <code>(?i)</code> учитываются.\n\n"
    "Примеры:\n"
    "  <code>(?i)крипт</code> — слова на «крипт» в любом регистре\n"
    "  <code>http(s)?://(?!t\\.me/)</code> — все ссылки, кроме t.me\n\n"
    "Для отмены отправьте /cancel"
)
BLOCKLIST_ASK_TEST = (
    "🧪 <b>Тест фильтра</b>\n\n"
    "Отправьте текст, который хотите проверить.\n"
    "Бот покажет, что сматчится из стоп-листа.\n\n"
    "Для отмены отправьте /cancel"
)
BLOCKLIST_CANCELLED = "Окей, отменил."
BLOCKLIST_NOT_ADMIN = "🔒 Эта команда только для админов."
