"""Денежные значения Директа."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

MULTIPLIER = 1_000_000

# Сколько знаков после запятой умещается в множителе. Считается из него же,
# чтобы не завести вторую константу с тем же смыслом.
PRECISION = len(str(MULTIPLIER)) - 1

# Разделители русского формата. Неразрывный пробел здесь не годится: вывод
# уходит в терминал и в TSV-индексы, где он превращается в мусор.
THOUSANDS_SEPARATOR = " "
DECIMAL_SEPARATOR = ","


class MoneyError(ValueError):
    """Значение не является денежным.

    Отдельный класс, а не голый ValueError: вызывающий код должен уметь
    отличить «пользователь ввёл ерунду» от «сломался разбор ответа»."""


def _decimal(amount) -> Decimal:
    """Сумма в валюте кабинета как Decimal.

    `bool` отвергается до `int`: `True` — это не «одна единица валюты», а
    почти наверняка чужое значение, попавшее в денежное поле."""
    if isinstance(amount, bool):
        raise MoneyError(
            f"денежным значением не может быть логическое {amount!r}"
        )
    if isinstance(amount, Decimal):
        value = amount
    elif isinstance(amount, int):
        value = Decimal(amount)
    elif isinstance(amount, float):
        # Через str(), а не Decimal(float): Decimal(0.1) разворачивает двоичную
        # дробь в 0.1000000000000000055511151231257827, и точное умножение на
        # множитель после этого невозможно. str() отдаёт кратчайшую запись,
        # которая читается обратно в то же число.
        value = Decimal(str(amount))
    elif isinstance(amount, str):
        text = amount.strip()
        if not text:
            raise MoneyError("денежное значение пусто")
        try:
            value = Decimal(text)
        except InvalidOperation:
            # Запятая как десятичный разделитель не принимается намеренно:
            # «1,234» — это и одна целая двести тридцать четыре тысячных, и
            # тысяча двести тридцать четыре, и угадывать здесь нечего.
            raise MoneyError(
                f"{amount!r} не читается как число; десятичный разделитель — "
                f"точка"
            ) from None
    else:
        raise MoneyError(
            f"денежным значением не может быть {type(amount).__name__}"
        )
    if not value.is_finite():
        raise MoneyError(f"денежное значение не конечно: {amount!r}")
    return value


def _shift(value: Decimal, places: int) -> Decimal:
    """Сдвиг десятичной запятой без участия контекста Decimal.

    Умножение и деление округляются по текущей точности контекста — по
    умолчанию 28 значащих цифр. Для ставки этого хватает, для суммарного
    оборота крупного кабинета в микроединицах уже нет, а замалчивать разницу
    в деньгах нельзя. Сдвиг показателя степени точен всегда."""
    sign, digits, exponent = value.as_tuple()
    return Decimal((sign, digits, exponent + places))


def to_api(amount) -> int:
    """Сумма в валюте кабинета -> целое значение API.

    Дробный остаток не округляется, а отвергается: округление денег молча —
    это расхождение между тем, что человек подтвердил, и тем, что ушло в
    Директ. Точность API — шесть знаков после запятой, и всё, что в неё
    укладывается, проходит без потерь."""
    value = _shift(_decimal(amount), PRECISION)
    if value != value.to_integral_value():
        raise MoneyError(
            f"{amount!r} не умещается в {PRECISION} знаков после запятой: "
            f"после умножения на {MULTIPLIER} остаётся дробная часть"
        )
    return int(value)


def from_api(units) -> Decimal:
    """Целое значение API -> сумма в валюте кабинета.

    Строка принимается, потому что Директ её присылает: свойства справочника
    `Currencies` приходят в поле `Value` строками — `"MinimumBid": "300000"`.
    Проверено на живом кабинете 27.08.2026.

    `float` не принимается: денежных значений в плавающей точке API не
    отдаёт, и появление такого значения означает, что где-то выше по потоку
    уже потеряна точность. Молча принять его — значит эту потерю узаконить."""
    if isinstance(units, bool):
        raise MoneyError(
            f"значением API не может быть логическое {units!r}"
        )
    if isinstance(units, float):
        raise MoneyError(
            f"значение API пришло числом с плавающей точкой ({units!r}); "
            f"денежные поля Директа — целые"
        )
    if isinstance(units, str):
        text = units.strip()
        if not text.lstrip("-").isdigit():
            raise MoneyError(f"{units!r} не читается как целое значение API")
        units = int(text)
    if not isinstance(units, int):
        raise MoneyError(
            f"значением API не может быть {type(units).__name__}"
        )
    return _shift(Decimal(units), -PRECISION)


def format_api(units, currency: str = "") -> str:
    """Целое значение API как строка для человека.

    Незначащие нули убираются, но не все: два знака после запятой остаются
    всегда, иначе «300» читается как триста рублей ровно там, где речь про
    три рубля. Шесть знаков печатаются целиком, когда они есть, — обрезать
    их значило бы показать не ту сумму, которая уйдёт в Директ."""
    amount = from_api(units)
    sign = "-" if amount < 0 else ""
    whole, _, fraction = f"{abs(amount):.{PRECISION}f}".partition(".")
    fraction = fraction.rstrip("0")
    if len(fraction) < 2:
        fraction = fraction.ljust(2, "0")
    groups = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    shown = f"{sign}{THOUSANDS_SEPARATOR.join(groups)}{DECIMAL_SEPARATOR}{fraction}"
    return f"{shown} {currency}" if currency else shown
