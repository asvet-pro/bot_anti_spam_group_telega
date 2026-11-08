"""Обработка входящих сообщений: ML + blocklist + legacy regex + дубликаты + антифлуд.

Порядок проверок:
1. Только защищаемый чат.
2. Админы/вайтлист — мимо (проверка в moderation.is_protected).
3. ML-фильтр (если включён).
4. Стоп-лист слов.
5. Стоп-лист regex.
6. Legacy BANNED_PATTERNS из .env.
7. Дубликаты (одинаковые подряд).
8. Антифлуд.

Действия:
- Явный спам (слово / regex / BANNED_PATTERNS / дубликаты сверх порога) →
  сообщение УДАЛЯЕТСЯ и автор получает бан 24 часа автоматически.
  Админу в личку уходит уведомление с текстом сообщения и кнопками,
  где можно поставить вечный бан или снять бан.
- ML (токсичность) и антифлуд → только удаление, без бана.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time

from aiogram import Bot, F, Router, types
from aiogram.exceptions import TelegramAPIError

from bot import texts
from bot.config import Settings
from bot.db import Database
from bot.filters.admin import AdminFilter
from bot.filters.blocklist import BlocklistFilter
from bot.filters.duplicate import DuplicateCheck
from bot.filters.flood import FloodCheck
from bot.filters.ml_spam import MLSpamCheck
from bot.filters.spam import SpamFilter
from bot.moderation import Moderation

logger = logging.getLogger(__name__)

router = Router(name="messages")

# Команды обрабатываются отдельным роутером, чтобы фильтры не сработали.
router.message.filter(~F.text.startswith("/"))


@router.message()
async def on_message(
    message: types.Message,
    bot: Bot,
    settings: Settings,
    db: Database,
    flood: FloodCheck,
    duplicate: DuplicateCheck,
    spam: SpamFilter,
    admin: AdminFilter,
    ml_spam: MLSpamCheck,
    blocklist: BlocklistFilter,
    moderation: Moderation,
) -> None:
    # Только в защищаемый чат
    if message.chat.id != settings.chat_id:
        return
    if not message.from_user:
        return

    user_id = message.from_user.id

    # Админы (в т.ч. админы самой группы) и вайтлист — мимо фильтров.
    if await moderation.is_protected(user_id):
        return

    # Системные сообщения (вступил/вышел) — мимо
    if not message.text and not message.caption:
        return

    payload = message.text or message.caption or ""

    # 1) Управляемый стоп-лист: слова.
    #    Дешёвое и точное правило — проверяем первым: так кнопка
    #    «В стоп-лист» появляется чаще, а ML не тратит время, если
    #    сообщение уже поймано стоп-словом.
    word_hit = await blocklist.match_word(payload)
    if word_hit:
        reason = f'стоп-слово «{word_hit}»'
        logger.info(
            "Word-blocklist hit user=%s word=%r text=%r",
            user_id, word_hit, payload[:200],
        )
        await _handle_spam(
            message, bot, db, settings, moderation, user_id, payload,
            reason, kind="word", action="temp_ban", hint=word_hit,
        )
        return

    # 2) Управляемый стоп-лист: regex.
    pattern_hit = await blocklist.match_pattern(payload)
    if pattern_hit:
        reason = f"стоп-regex `{pattern_hit.pattern}`"
        logger.info(
            "Pattern-blocklist hit user=%s pattern=%r text=%r",
            user_id, pattern_hit.pattern, payload[:200],
        )
        await _handle_spam(
            message, bot, db, settings, moderation, user_id, payload,
            reason, kind="pattern", action="temp_ban",
            hint=pattern_hit.pattern,
        )
        return

    # 3) Legacy BANNED_PATTERNS из .env (жёсткий список рекламы).
    if spam.enabled:
        hit = spam.match(payload)
        if hit:
            reason = f"паттерн `{hit.pattern}`"
            logger.info(
                "Legacy regex hit user=%s pattern=%r text=%r",
                user_id, hit.pattern, payload[:200],
            )
            await _handle_spam(
                message, bot, db, settings, moderation, user_id, payload,
                reason, kind="legacy", action="temp_ban", hint=hit.pattern,
            )
            return

    # 4) ML-фильтр спама. Идёт ПОСЛЕ стоп-листа: правила дешевле и
    #    точнее, а модель — тяжёлый inference на каждое сообщение.
    #    Токсичность/спам — удаляем без бана.
    if ml_spam.enabled:
        ml_hit, ml_pred = await asyncio.to_thread(ml_spam.is_spam, payload)
        if ml_hit and ml_pred is not None:
            top_label, top_score = max(
                ml_pred.all_scores.items(), key=lambda kv: kv[1]
            )
            reason = f"ML {top_label}={top_score:.2f}"
            logger.info(
                "ML hit user=%s label=%s score=%.3f text=%r",
                user_id, top_label, top_score, payload[:200],
            )
            # Подбираем ключевое слово, чтобы кнопка «В стоп-лист»
            # появлялась и для ML-спама.
            await _handle_spam(
                message, bot, db, settings, moderation, user_id, payload,
                reason, kind="ml", action="delete_only",
                hint=guess_spam_pattern(payload),
            )
            return

    # 5) Дубликаты: одно и то же сообщение подряд.
    #    dup_count — сколько таких сообщений от автора в окне (включая текущее).
    #    Порог считаем так: «5 одинаковых подряд» → бан.
    #    dup_count > 1 — сообщение само по себе повтор (3-е при max_repeats=2).
    #    dup_count >= duplicate_ban_threshold — бан 24ч.
    dup_hit, dup_count = await duplicate.is_duplicate(
        user_id, message.chat.id, payload
    )
    if dup_hit:
        is_ban = dup_count >= settings.duplicate_ban_threshold
        action = "temp_ban" if is_ban else "delete_only"
        reason = f"повтор сообщения ({dup_count}x)"
        logger.info(
            "Duplicate user=%s count=%s action=%s text=%r",
            user_id, dup_count, action, payload[:120],
        )
        await _handle_spam(
            message, bot, db, settings, moderation, user_id, payload,
            reason, kind="duplicate", action=action,
        )
        return

    # 6) Антифлуд: сообщение удаляем, но НЕ баним автоматически —
    #    единственный фильтр, который может задеть живых людей.
    count = await flood.check(user_id, message.chat.id)
    if flood.is_flood(count):
        logger.info("Flood: user=%s count=%s", user_id, count)
        await moderation.delete_message(message, user_id)
        try:
            await message.answer(texts.FLOOD_WARNING)
        except TelegramAPIError:
            pass


async def _handle_spam(
    message: types.Message,
    bot: Bot,
    db: Database,
    settings: Settings,
    moderation: Moderation,
    user_id: int,
    payload: str,
    reason: str,
    *,
    kind: str,
    action: str,
    hint: str | None = None,
) -> None:
    """Единая обработка срабатывания фильтра.

    1. Удаляет сообщение (если автор не защищён).
    2. Применяет бан согласно action (temp_ban → 24ч автоматически).
    3. Уведомляет админа в личку: текст сообщения + кнопки
       (🚫 Спам / ✅ Ок / ⏭ В стоп-лист / 🔨 БАН навсегда).
    """
    chat_id = message.chat.id
    msg_id = message.message_id

    # Запоминаем для разметки ДО удаления.
    await db.add_pending_review(
        msg_id=msg_id,
        user_id=user_id,
        chat_id=chat_id,
        text=payload,
        reason=reason,
        blocklist_hint=hint,
    )

    deleted = await moderation.delete_message(message, user_id)

    if action == "temp_ban":
        await moderation.ban_user(
            bot, user_id, chat_id,
            reason=f"{reason} (авто-бан 24ч)",
            action="temp_ban",
        )
        await db.inc("auto_bans")
    elif action == "permanent_ban":
        await moderation.ban_user(
            bot, user_id, chat_id,
            reason=f"{reason} (вечный бан)",
            action="permanent_ban",
        )
        await db.inc("auto_bans")

    # Короткое уведомление в чат — без деталей (чтобы спамер не понял,
    # какое правило сработало).
    if deleted:
        try:
            await message.answer(texts.SPAM_DELETED_SHORT)
        except TelegramAPIError:
            pass

    # Подробное уведомление админу в личку — с текстом и кнопками.
    await _notify_admin(
        bot, db, settings, moderation,
        msg_id=msg_id, user_id=user_id, chat_id=chat_id,
        payload=payload, reason=reason, kind=kind, action=action,
        deleted=deleted, hint=hint,
    )


async def _notify_admin(
    bot: Bot,
    db: Database,
    settings: Settings,
    moderation: Moderation,
    *,
    msg_id: int,
    user_id: int,
    chat_id: int,
    payload: str,
    reason: str,
    kind: str,
    action: str,
    deleted: bool,
    hint: str | None,
) -> None:
    """Отправляет админу в личку уведомление с текстом и кнопками."""
    from bot.handlers.review import review_keyboard

    # Дедупликация: если этот автор уже присылал такое же уведомление
    # недавно — не шлём второе, а ОБНОВЛЯЕМ предыдущее (счётчик серии
    # растёт). Так личка не засоряется, но видно масштаб.
    # Сами сообщения не теряются: лежат в pending_review и видны в /reports.
    series_count, notice_id = await db.get_notification(user_id, chat_id, payload)
    if series_count:
        await db.bump_notification_count(user_id, chat_id, payload)
        new_count = series_count + 1
        logger.info(
            "Review notification deduplicated (series=%s): user=%s",
            new_count, user_id,
        )
        # Обновляем заголовок прошлого уведомления, если знаем его id.
        if notice_id:
            for admin_id in settings.admin_ids:
                try:
                    await bot.edit_message_text(
                        chat_id=admin_id,
                        message_id=notice_id,
                        text=texts.REVIEW_HEADER.format(
                            user_id=user_id,
                            reason=reason,
                            action_text=texts.ACTION_LABELS.get(action, action),
                            text=_truncate(payload),
                            count_hint=f" ({new_count} одинаковых подряд)",
                        ),
                    )
                except TelegramAPIError:
                    pass
        return

    state = {
        "deleted": deleted,
        "action": action,
        "kind": kind,
    }
    text = texts.REVIEW_HEADER.format(
        user_id=user_id,
        reason=reason,
        action_text=texts.ACTION_LABELS.get(action, action),
        text=_truncate(payload),
        count_hint="",
    )
    for admin_id in settings.admin_ids:
        try:
            sent = await bot.send_message(
                admin_id,
                text,
                reply_markup=review_keyboard(msg_id, hint, state),
            )
            await db.mark_notified(
                user_id, chat_id, payload, notice_id=sent.message_id
            )
        except TelegramAPIError as e:
            logger.warning("Не удалось отправить уведомление админу %s: %s", admin_id, e)


def _truncate(text: str, limit: int = 800) -> str:
    """Обрезает текст для показа в личке и экранирует HTML."""
    if len(text) > limit:
        text = text[:limit] + "…"
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )


# Слова, которые описывают действие, а не суть предложения — они частые
# в любом рекламном тексте, но бесполезны как стоп-слово.
_NON_MARKER_WORDS = {
    "пиши", "пишите", "напиши", "напишите", "встречаемся",
    "присоединяйтесь", "вступай", "вступайте", "подписывайся",
    "подпишитесь", "пишем", "делаем", "создаём", "создаем", "продам",
    "продаю", "продаём", "продаем", "предлагаю", "предлагаем", "ищу",
    "ищем", "нужен", "нужна", "нужны", "беру", "берём", "берем",
    "работа", "работу", "работать", "доход", "дохода", "деньги",
    "денег", "прибыль", "свободный", "свободна", "свободны", "всегда",
    "только", "уже", "ещё", "еще", "очень", "быстро", "легко",
    "просто", "сейчас", "тут", "здесь", "мне", "тебе", "вам", "нам",
    "вас", "них", "него", "нее", "это", "как", "что", "все", "всё",
    "для", "или", "не", "нет", "да", "же", "бы", "быть", "есть",
    # слишком общие — как стоп-слово бесполезны
    "набор", "команда", "помогу", "поможем", "помогите", "у нас",
    "много", "мало", "хорошо", "плохо", "также", "оно", "всё", "вам",
    "курс", "обучение", "занятие", "занятия", "учимся", "учиться",
}

# Окончания, по которым отсекаем слишком общие слова в любом падеже.
# Слово считается бесполезным, если заканчивается на один из них И
# его основа короткая (менее 8 символов) — иначе потеряем «подписчиками».
_WEAK_SUFFIXES = ("ами", "ями", "ах", "ях", "ом", "ем", "ов", "ев", "ий", "ый")


def _is_weak_marker(word: str) -> bool:
    """Слишком ли общее слово, чтобы быть стоп-словом."""
    low = word.lower()
    if low in _NON_MARKER_WORDS:
        return True
    # Короткая основа + «общее» окончание → пропускаем.
    if len(low) <= 8 and low.endswith(_WEAK_SUFFIXES):
        return True
    return False


def guess_spam_keyword(text: str) -> str | None:
    """Подбирает ключевое слово из рекламного сообщения.

    Задача: дать админу конкретное правило для кнопки «В стоп-лист», чтобы
    он мог одним нажатием закрепить его.

    Логика:
    - слова короче 4 символов и «служебные» отбрасываем;
    - из оставшихся выбираем самое длинное слово, написанное с заглавной
      буквы (обычно это бренд или услуга: «Бинанс», «Promptium»);
    - если таких нет — берём самое длинное значимое слово.

    Возвращаем слово как есть (в нижнем регистре) — вызывающий код сам
    построит из него regex с \\w*, чтобы ловились все словоформы:
    «заработок» / «заработка» / «заработке» — это разные слова, а стоп-
    слово «заработать» не поймает «заработок».

    Возвращает None, если подобрать нечего.
    """
    if not text:
        return None

    # Слова из текста (латиница и кириллица), длиной от 4.
    raw = re.findall(r"[A-Za-zА-Яа-яЁё]{4,}", text)
    if not raw:
        return None

    # Отбрасываем слова, которые встречаются в рекламе всегда.
    words = [w for w in raw if not _is_weak_marker(w)]
    if not words:
        return None

    # Приоритет: слово с заглавной буквы (бренд/услуга).
    capitalized = [w for w in words if w[0].isupper()]
    if capitalized:
        # Самое длинное из «капитализированных» — вероятный бренд.
        chosen = max(capitalized, key=len)
    else:
        # Иначе — самое длинное слово (обычно самое содержательное).
        chosen = max(words, key=len)

    return chosen.lower()


# Русские окончания, которые отбрасываем, оставляя основу.
_RU_ENDINGS = (
    "ами", "ями", "ом", "ем", "ах", "ях", "ев", "ов", "ей", "ой",
    "ий", "ый", "ая", "яя", "ое", "ее", "ые", "ие", "ок", "ек",
    "ам", "ям", "ы", "и", "а", "я", "о", "е", "у", "ю", "ь",
)

# Минимальная длина «ядра» слова, по которому строим regex.
# По нему ловятся все словоформы: «заработ» + \w* → заработок/заработка/…
_STEM_CORE_LEN = 6


def _word_stem(word: str) -> str:
    """Возвращает ядро слова — первые N значащих символов.

    «заработком» → «зарабо», «подписками» → «подписк».
    По ядру строится regex «(?i)ядро\\w*», который ловит все словоформы.
    """
    low = word.lower()
    if len(low) <= _STEM_CORE_LEN:
        return low
    return low[:_STEM_CORE_LEN]


def guess_spam_pattern(text: str) -> str | None:
    """Подбирает regex-паттерн для кнопки «В стоп-лист».

    Возвращает готовый паттерн вида «(?i)зарабо\\w*» — он ловит все
    словоформы («заработок», «заработка», «заработке»). Обычное стоп-слово
    «заработать» не поймало бы «заработок», а это самый частый вариант.
    """
    stem = guess_spam_keyword(text)
    if not stem:
        return None
    core = _word_stem(stem)
    if len(core) < 4:
        return None
    return f"(?i){re.escape(core)}\\w*"
