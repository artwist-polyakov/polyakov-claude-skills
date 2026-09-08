"""Описание изменений для полного показа «было → станет».
Прежнее значение подставляется из свежего ответа API."""

from __future__ import annotations

import json

from config import DirectFailure
from payload import compact_payload


class Change:
    """Правка одного поля объекта. field связывает строку плана с запросом,
    before заполняется свежим чтением, kind различает типы объектов."""

    __slots__ = ("object_id", "what", "before", "after", "title", "service",
                 "field", "kind")

    def __init__(self, *, object_id=None, what: str = "", before=None,
                 after=None, title: str = "", service: str = "",
                 field: str = "", kind: str = ""):
        self.object_id = object_id
        self.what = what
        self.before = before
        self.after = after
        self.title = title
        self.service = service
        self.field = field
        self.kind = kind

    def check(self) -> None:
        """Инварианты изменения. Прогоняются конвейером при каждой сборке."""
        if not self.what:
            raise DirectFailure(
                "Изменение без описания: человеку показывать нечего, а "
                "план должен описывать каждое отправляемое изменение."
            )

    def describe(self, title=None) -> str:
        """Строка «было → станет» для показа человеку.

        `title` подставляет имя объекта, прочитанное конвейером перед записью:
        вызывающий код его знать не обязан, а строка «кампания 713908311» без
        названия человеку ничего не говорит."""
        where = f"{self.service} " if self.service else ""
        who = f"{where}{self.object_id}" if self.object_id is not None else where.strip()
        shown = self.title or (title or "")
        name = f" «{shown}»" if shown else ""
        before = compact_payload({self.field: self.before})[self.field]
        after = compact_payload({self.field: self.after})[self.field]
        return (f"{who}{name} · {self.what}: "
                f"{_shown(before)} → {_shown(after)}").strip()

    def __repr__(self) -> str:
        return f"<Change {self.describe()}>"


def _shown(value) -> str:
    """Показать полное значение поля без обрезки текста и списков."""
    if value is None:
        return "—"
    if isinstance(value, str):
        return f"«{value}»"
    return json.dumps(value, ensure_ascii=False, default=str)


class Plan:
    """Перечень всех изменений задачи."""

    def __init__(self, changes, *, title: str = ""):
        self.changes = list(changes)
        self.title = title
        for change in self.changes:
            change.check()

    def touched(self) -> list:
        """Затронутые объекты: пара «вид, идентификатор», без повторов.

        Нужна отчёту и человеку: «правится семь объектов» читается иначе, чем
        «правится одно поле семь раз»."""
        seen = []
        for change in self.changes:
            key = (change.kind, change.object_id)
            if key not in seen:
                seen.append(key)
        return seen

    def __len__(self) -> int:
        return len(self.changes)

    def __iter__(self):
        return iter(self.changes)

    def __repr__(self) -> str:
        return f"<Plan {self.title}: изменений {len(self.changes)}>"
