#!/usr/bin/env python3
"""Обновляет BANNED_PATTERNS: добавляет покрытие склонений.

Было: (?i)заработ[и]?к  — ловит «заработка», но НЕ «заработок».
Стало: (?i)заработ\\w*   — ловит все склонения: заработок, заработка,
                          заработке, заработку...

Запуск:
    python scripts/fix_patterns.py            # показать новый список
    python scripts/fix_patterns.py --apply    # применить к Coolify
"""
from __future__ import annotations

import argparse
import json
import re
import urllib.request


COOLIFY_URL = "https://coolify.asvet-pro.ru"
APP_UUID = "lceys8tfjwlwu38u3bladm30"


# Паттерны, где нужно разрешить склонения: (старый, новый).
# \w* в конце основы слова покрывает падежные окончания.
REPLACEMENTS = [
    (r"(?i)заработ[и]?к", r"(?i)заработ\w*"),
    (r"(?i)пассивн[ыи]й доход", r"(?i)пассивн\w* доход"),
    (r"(?i)сигналы", r"(?i)сигнал\w*"),
    (r"(?i)закрыт[ыи]й клуб", r"(?i)закрыт\w* клуб"),
    (r"(?i)пирамида", r"(?i)пирамид\w*"),
    (r"(?i)набор в команду", r"(?i)набор\w* в команду"),
    (r"(?i)подпис[аи]ч[ик]ов.*доход", r"(?i)подпис\w*.*доход"),
    (r"(?i)инфокурс", r"(?i)инфокурс\w*"),
    (r"(?i)каппер", r"(?i)каппер\w*"),
    (r"(?i)ставк[аи]", r"(?i)ставк\w*"),
    (r"(?i)заработок на нейросет", r"(?i)заработ\w* на нейросет\w*"),
    (r"(?i)доход с нейросет", r"(?i)доход\w* с нейросет\w*"),
    (r"(?i)готовая нейросет", r"(?i)готов\w* нейросет\w*"),
    (r"(?i)нейросеть для заработка", r"(?i)нейросет\w* для заработ\w*"),
    (r"(?i)пишем нейросет", r"(?i)пишем\w* нейросет\w*"),
    (r"(?i)делаем нейросет", r"(?i)делаем\w* нейросет\w*"),
    (r"(?i)создаём нейросет", r"(?i)созда[её]м\w* нейросет\w*"),
    (r"(?i)создаем нейросет", r"(?i)создаем\w* нейросет\w*"),
    (r"(?i)продам нейросет", r"(?i)продам\w* нейросет\w*"),
    (r"(?i)обучение нейросет", r"(?i)обучени\w* нейросет\w*"),
    (r"(?i)курс по нейросет", r"(?i)курс\w* по нейросет\w*"),
    (r"(?i)интенсив по нейросет", r"(?i)интенсив\w* по нейросет\w*"),
    (r"(?i)ищу специалист[аов]? по нейросет", r"(?i)ищу специалист\w* по нейросет\w*"),
    (r"(?i)вакансия.*нейросет", r"(?i)ваканси\w*.*нейросет\w*"),
    (r"(?i)канал.*заработ", r"(?i)канал\w*.*заработ\w*"),
    (r"(?i)помогу.*заработ", r"(?i)помогу\w*.*заработ\w*"),
    (r"(?i)поможем.*заработ", r"(?i)поможем\w*.*заработ\w*"),
    (r"(?i)дневник дохода", r"(?i)дневник\w* доход\w*"),
    (r"(?i)доход в интернете", r"(?i)доход\w* в интернет\w*"),
    (r"(?i)заработок без вложений", r"(?i)заработ\w* без вложени\w*"),
    (r"(?i)работа в интернете", r"(?i)работ\w* в интернет\w*"),
    (r"(?i)удаленная работа", r"(?i)удаленн\w* работ\w*"),
    (r"(?i)удалённая работа", r"(?i)удалённ\w* работ\w*"),
    (r"(?i)продвижение сайта", r"(?i)продвижени\w* сайт\w*"),
    (r"(?i)накрутить", r"(?i)накрут\w*"),
    (r"(?i)накрутка", r"(?i)накрут\w*"),
    (r"(?i)бот для заработка", r"(?i)бот\w* для заработ\w*"),
    (r"(?i)нейросеть заменит", r"(?i)нейросет\w* заменит"),
]


def apply_replacements(patterns: list[str]) -> list[str]:
    """Заменяет старые паттерны на новые (без дублей)."""
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


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true", help="применить к Coolify")
    p.add_argument("--token", default="", help="Coolify API token")
    args = p.parse_args()

    if args.apply:
        if not args.token:
            print("Нужен --token", file=sys.stderr)
            return 2
        current = fetch_current(args.token)
        updated = apply_replacements(current)
        changed = [x for x in updated if x not in current]
        print(f"Было: {len(current)}, стало: {len(updated)}, изменено: {len(changed)}")
        for c in changed:
            print(f"  + {c}")
        ok = save_current(args.token, updated)
        print("Сохранено" if ok else "НЕ СОХРАНЕНО")
        return 0 if ok else 1

    # Без --apply показываем, что будет изменено.
    current = fetch_current(args.token) if args.token else []
    if not current:
        print("Передай --token или --apply", file=sys.stderr)
        return 2
    updated = apply_replacements(current)
    print(f"Было {len(current)} → станет {len(updated)}")
    for c in updated:
        mark = "+" if c not in current else " "
        print(f" {mark} {c}")
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main())
