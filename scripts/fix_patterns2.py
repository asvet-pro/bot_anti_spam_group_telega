#!/usr/bin/env python3
"""Исправляет паттерны, которые дают ложные срабатывания.

Проблема: при покрытии склонений основа слова стала слишком широкой.
  (?i)работ\\w* в интернет\\w*   ловит «Подработка в интернете»
  (?i)заработ\\w*                ловит «заработная плата»

Решение: вместо «основа + \\w*» используем ЯВНЫЕ словоформы конкретного
рекламного выражения. Тогда «подработка» не ловится (нет слова
«подработка» в списке), а «заработок на нейросетях» — ловится.

Запуск:
    python scripts/fix_patterns2.py --token <token> --apply
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request


COOLIFY_URL = "https://coolify.asvet-pro.ru"
APP_UUID = "lceys8tfjwlwu38u3bladm30"


# (старый паттерн, новый паттерн)
REPLACEMENTS = [
    # «Работа в интернете» — ловим только явные рекламные формы,
    # а не «подработка»/«работа» в любом контексте.
    (
        r"(?i)работ\w* в интернет\w*",
        r"(?i)работ(а|у|е)\s+в\s+интернете\s+(от|на|без|с)",
    ),
    (
        r"(?i)доход\w* в интернет\w*",
        r"(?i)доход\w*\s+в\s+интернете",
    ),
    # «Заработок» — ловим только как самостоятельное рекламное слово.
    # «заработная плата» и «зарплата» — обычные слова, их не трогаем.
    (
        r"(?i)заработ\w*",
        r"(?i)заработ(ок|ка|ке|ку|ком|ки|ку)\b",
    ),
    (
        r"(?i)заработ\w* на нейросет\w*",
        r"(?i)заработ(ок|ка|ке|ку|ком|ки|ать)\s+на\s+нейросет\w*",
    ),
    (
        r"(?i)нейросет\w* для заработ\w*",
        r"(?i)нейросет\w*\s+для\s+заработ(ок|ка|ке|ку|ком|ки|ать)",
    ),
    (
        r"(?i)заработ\w* без вложени\w*",
        r"(?i)заработ(ок|ка|ке|ку|ком|ки)\s+без\s+вложени\w*",
    ),
    (
        r"(?i)бот\w* для заработ\w*",
        r"(?i)бот\w*\s+для\s+заработ(ок|ка|ке|ку|ком|ки|ать)",
    ),
    (
        r"(?i)помогу\w*.*заработ\w*",
        r"(?i)помогу\w*.*заработ(ок|ка|ке|ку|ком|ки|ать)",
    ),
    (
        r"(?i)поможем\w*.*заработ\w*",
        r"(?i)поможем\w*.*заработ(ок|ка|ке|ку|ком|ки|ать)",
    ),
    (
        r"(?i)канал\w*.*заработ\w*",
        r"(?i)канал\w*.*заработ(ок|ка|ке|ку|ком|ки|ать)",
    ),
    (
        r"(?i)схема заработка",
        r"(?i)схема\s+заработ(ка|ки|ке|ку)",
    ),
    # «Подписчики» — только рекламный контекст.
    (
        r"(?i)подпис\w*.*доход",
        r"(?i)подпис(чик|чиков|чиками|чика).*доход",
    ),
]


def apply_replacements(patterns: list[str]) -> list[str]:
    out: list[str] = []
    for p in patterns:
        new = p
        for old, repl in REPLACEMENTS:
            if p == old:
                new = repl
                break
        if new not in out:
            out.append(new)
    return out


def fetch_current(token: str) -> list[str]:
    req = urllib.request.Request(
        f"{COOLIFY_URL}/api/v1/applications/{APP_UUID}/envs",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    if isinstance(data, dict):
        data = data.get("envs", data.get("data", []))
    for e in data:
        if e.get("key") == "BANNED_PATTERNS":
            return [x.strip() for x in e.get("value", "").split("||") if x.strip()]
    return []


def save_current(token: str, patterns: list[str]) -> bool:
    body = json.dumps({
        "key": "BANNED_PATTERNS",
        "value": "||".join(patterns),
        "is_buildtime": False,
        "is_runtime": True,
    }).encode()
    req = urllib.request.Request(
        f"{COOLIFY_URL}/api/v1/applications/{APP_UUID}/envs",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="PATCH",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.load(resp)
    return bool(result.get("real_value"))


# Проверочные наборы.
SPAM = [
    "Заработок на нейросетях, пиши в личку",
    "заработка на нейросетях",
    "Заработок без вложений",
    "Бот для заработка",
    "Помогу заработать 100к в месяц",
    "Продам канал, заработок стабильный",
    "работа в интернете от 5 тысяч",
    "доход в интернете",
    "подписчики и доход",
    "схема заработка",
    "казино",
    "крипто",
]

NORMAL = [
    "Как увеличить заработную плату на переговорах?",
    "Подработка в интернете для студентов",
    "Работа в интернете — отличный вариант для удалёнки",
    "Зарплата в нашей компании зависит от результатов",
    "Доходы бюджета выросли",
    "Привет! Как у вас дела?",
    "Подскажите, как лучше настроить RAG-систему?",
    "Подписка на рассылку новостей",
    "Подписок много, но все активные",
    "Спасибо, очень полезный разбор!",
]


def check(patterns: list[str]) -> tuple[int, int]:
    missed = [t for t in SPAM if not any(re.search(p, t) for p in patterns)]
    false_pos = [t for t in NORMAL if any(re.search(p, t) for p in patterns)]
    return len(missed), len(false_pos)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true")
    p.add_argument("--token", default="")
    args = p.parse_args()

    if not args.token:
        print("Нужен --token", file=sys.stderr)
        return 2

    current = fetch_current(args.token)
    updated = apply_replacements(current)

    for pats, label in ((current, "БЫЛО"), (updated, "СТАЛО")):
        missed, fp = check(pats)
        print(f"{label}: паттернов={len(pats)} | не поймано спама={missed} | ложных={fp}")

    missed, fp = check(updated)
    if missed:
        print("\nНе поймано:")
        for t in SPAM:
            if not any(re.search(p, t) for p in updated):
                print(f"  ✗ {t}")
    if fp:
        print("\nЛожные срабатывания:")
        for t in NORMAL:
            hits = [p for p in updated if re.search(p, t)]
            if hits:
                print(f"  ⚠ {t}  → {hits}")

    if args.apply:
        ok = save_current(args.token, updated)
        print("\nСохранено" if ok else "\nНЕ СОХРАНЕНО")
        return 0 if ok else 1

    print("\n(для применения добавь --apply)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
