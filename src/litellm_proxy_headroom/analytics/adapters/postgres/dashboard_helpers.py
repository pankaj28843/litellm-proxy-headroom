from __future__ import annotations

from decimal import Decimal
from typing import Any


def int_value(value: Any) -> int:
    return int(value or 0)


def float_value(value: Any) -> float | None:
    return None if value is None else float(value)


def decimal_str(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def percent(part: int, whole: int) -> float | None:
    if whole <= 0:
        return None
    return round((part / whole) * 100, 4)
