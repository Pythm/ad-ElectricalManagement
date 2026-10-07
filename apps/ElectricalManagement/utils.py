""" Shared helpers for ElectricalManagement.

    This module must not import pydantic_models at runtime: pydantic_models imports the
    WattSlot/Decision dataclasses from here, so a runtime import in the other direction
    would be circular. TempConsumption is only needed for type annotations.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, Optional, Sequence

if TYPE_CHECKING:  # pragma: no cover
    from pydantic_models import TempConsumption


@dataclass(order=True)
class WattSlot:
    """ One price slot with the Wh still available for charging in it. """
    start: datetime
    end: datetime
    available_Wh: float

    @property
    def duration_hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600.0


@dataclass(frozen=True)
class Decision:
    """ One row of the per-minute decision table: first predicate that is true wins. """
    name: str
    predicate: Callable[[], bool]
    action:   Callable[[], None]


def cancel_timer_handler(ADapi, handler, name) -> bool:
    if handler is not None:
        if ADapi.timer_running(handler):
            try:
                ADapi.cancel_timer(handler)
            except Exception as e:
                ADapi.log(
                    f"Not able to stop timer handler for {name}. Exception: {e}",
                    level = 'DEBUG'
                )
                return False
    return True

def cancel_listen_handler(ADapi, handler, name) -> bool:
    if handler is not None:
        try:
            ADapi.cancel_listen_state(handler, silent=True)
        except Exception as e:
            ADapi.log(
                f"Not able to stop listen handler for {name}. Exception: {e}",
                level = 'DEBUG'
            )
            return False
    return True

def get_next_runtime_aware(startTime, offset_seconds, delta_in_seconds):
    """ Next multiple of delta_in_seconds after startTime (counted from the top of the hour),
        shifted by offset_seconds. offset_seconds=5 with delta 60 gives hh:mm:05 every minute. """
    next_minute_mark = ((startTime.minute * 60 + startTime.second) // delta_in_seconds + 1) * delta_in_seconds
    next_runtime = startTime.replace(minute=0, second=offset_seconds % 60, microsecond=0)
    next_runtime += timedelta(seconds=next_minute_mark)

    return next_runtime

def to_float_or_none(value: Any) -> Optional[float]:
    """ float(value) or None when the value is missing / 'unavailable' / not numeric. """
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

def price_now_or_inf(electricalPriceApp) -> float:
    """ Current electricity price, or math.inf when the price app has no price for now
        (empty price list at startup, gap between days). inf makes every
        'price <= limit' comparison false, so consumers treat it as 'not cheap'. """
    price = electricalPriceApp.electricity_price_now()
    return math.inf if price is None else price

def slot_index_now(elpricestoday: Sequence, now: datetime) -> int:
    """ Index of the price slot that contains `now` (clamped to >= 0).
        On a normal 24-slot hourly day starting at 00:00 this equals now.hour. """
    starts = [item.start for item in elpricestoday]
    return max(bisect.bisect_right(starts, now) - 1, 0)

def get_consumption_for_outside_temp(
    data: Dict[str, "TempConsumption"],
    out_temp: float
) -> Optional["TempConsumption"]:
    """
    Return the TempConsumption record that best matches the current out_temp.
    ``data`` is a dict that maps a *temperature* (string) → TempConsumption.
    """
    if not data:
        return None

    even_key = floor_even(out_temp)
    if even_key in data:
        return data[even_key]

    keys = [int(k) for k in data.keys()]
    nearest = closest_value(data=keys, target=out_temp)
    if nearest is None:
        return None
    return data[nearest]

def closest_value(
    data: Iterable[Any],
    target: float,
    convert: Callable[[Any], float] | None = None,
) -> Optional[Any]:
    """ Return the element in *data* whose numeric value is closest to *target*."""
    try:
        iterator = iter(data)
        first = next(iterator)
    except StopIteration:
        return None

    conv = convert or (lambda x: float(x))

    best = first
    best_diff = abs(conv(first) - target)

    for item in iterator:
        try:
            diff = abs(conv(item) - target)
        except Exception:
            continue

        if diff < best_diff:
            best, best_diff = item, diff

    return best

def closest_temp_in_dict(temp: str, data: Dict[str, "TempConsumption"]) -> int | None:
    """Return the key that is numerically closest to `temp`."""
    if not data:
        return None
    try:
        target = int(temp)
    except ValueError:
        return None
    return min(data.keys(), key=lambda k: abs(int(k) - target))

def diff_ok(old_val: float | None, new_val: float, max_ratio: float) -> bool:
    """Return True if the new value is within `max_ratio` of the old one."""
    if old_val is None:
        return True
    if old_val == 0:
        return new_val == 0
    return abs(old_val - new_val) / abs(old_val) <= max_ratio

def floor_even(n: float) -> int:
    return int(math.floor(n / 2.0) * 2.0)

@dataclass(frozen=True)
class ModeTranslations:
    fire: str = "fire"
    false_alarm: str = "false-alarm"
