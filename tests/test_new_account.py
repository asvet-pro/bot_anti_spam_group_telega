"""Тесты оценки возраста аккаунта по user_id.

Регрессионный тест: старый код ((user_id >> 22) / 1000.0) давал «20764 дня»
для ЛЮБОГО реального id → бан не срабатывал никогда. Эти тесты фиксируют
правильное поведение.
"""
from __future__ import annotations

import time

import pytest

from bot.filters.new_account import NewAccountCheck


class _U:
    """Минимальный аналог types.User для проверки is_too_young."""

    def __init__(self, uid: int) -> None:
        self.id = uid


# (user_id, реальная дата регистрации) из публичного датасета
# https://github.com/Aayco/Creation
KNOWN_PAIRS = [
    (5162494923, "2022-05-13"),
    (6279839148, "2023-07-03"),
    (6925870357, "2023-11-28"),
    (7682429075, "2024-09-05"),
    (8000499714, "2025-04-20"),
]


@pytest.mark.parametrize("user_id,real_date", KNOWN_PAIRS)
def test_known_ids_match_real_dates(user_id: int, real_date: str) -> None:
    """Оценка даты регистрации совпадает с реальной (допуск 3 дня)."""
    import datetime

    ts = NewAccountCheck.estimate_registration_ts(user_id)
    assert ts is not None, f"id={user_id} не должен давать None"
    est = datetime.datetime.fromtimestamp(ts)
    real = datetime.datetime.strptime(real_date, "%Y-%m-%d")
    delta = abs((est - real).days)
    assert delta <= 3, f"id={user_id}: оценка {est.date()} vs реальная {real_date}"


def test_old_accounts_are_not_banned() -> None:
    """Аккаунты 2022-2025 годов — не банятся (порог 7 дней)."""
    check = NewAccountCheck(min_age_days=7)
    for user_id, _ in KNOWN_PAIRS:
        assert not check.is_too_young(_U(user_id)), f"id={user_id} не должен баниться"


def test_fresh_account_is_banned() -> None:
    """Свежий аккаунт (создан сегодня) — бан."""
    import bot.filters.new_account as na

    last_id, last_ts = na._KNOWN_ID_DATES[-1]
    now = time.time()
    fresh_id = int(last_id + (now - last_ts) / na._EXTRAPOLATION_RATE)

    check = NewAccountCheck(min_age_days=7)
    assert check.is_too_young(_U(fresh_id)), "свежий аккаунт должен баниться"


def test_threshold_boundary() -> None:
    """Порог работает точно: 6 дней — бан, 8 дней — нет."""
    import bot.filters.new_account as na

    last_id, last_ts = na._KNOWN_ID_DATES[-1]
    now = time.time()
    fresh_id = int(last_id + (now - last_ts) / na._EXTRAPOLATION_RATE)

    def id_for_days_ago(days: float) -> int:
        return int(fresh_id - days * 86400 / na._EXTRAPOLATION_RATE)

    check = NewAccountCheck(min_age_days=7)
    assert check.is_too_young(_U(id_for_days_ago(6))), "6 дней → бан"
    assert not check.is_too_young(_U(id_for_days_ago(8))), "8 дней → не бан"


def test_small_ids_are_never_banned() -> None:
    """Маленькие/старые id: оценка ненадёжна → бана нет (False)."""
    check = NewAccountCheck(min_age_days=7)
    for user_id in (25, 1000000, 4_000_000_000):
        assert check.estimate_age_days(user_id) is None
        assert not check.is_too_young(_U(user_id)), f"id={user_id} не должен баниться"


def test_old_broken_formula_would_have_never_banned() -> None:
    """Старой формулой свежий аккаунт НЕ банился — это и был баг.

    Фиксируем, чтобы регрессия не вернулась.
    """
    import bot.filters.new_account as na

    last_id, last_ts = na._KNOWN_ID_DATES[-1]
    now = time.time()
    fresh_id = int(last_id + (now - last_ts) / na._EXTRAPOLATION_RATE)

    # Старая (сломанная) формула:
    broken_age = max(0, int((time.time() - (fresh_id >> 22) / 1000.0) // 86400))
    assert broken_age > 365, "старая формула считала возраст огромным"

    # Новая — считает правильно:
    check = NewAccountCheck(min_age_days=7)
    age = check.estimate_age_days(fresh_id)
    assert age is not None and age <= 1, "новая формула должна давать ~0 дней"
