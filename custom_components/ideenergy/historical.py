# Copyright (C) 2021-2026 Luis López <luis@cuarentaydos.com>
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301,
# USA.

"""Conversion of i-DE hourly periods into HistoricalState objects."""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime, timedelta
from logging import getLogger

import ideenergy
from homeassistant.util import dt as dt_util
from homeassistant_historical_sensor import HistoricalState

from .const import LOCAL_TZ

LOGGER = getLogger(__name__)

WH_PER_KWH = 1000
ONE_HOUR = timedelta(hours=1)
TARIFF_PERIOD_RE = re.compile(r"P\d")
TARIFF_PERIODS_ATTR = "tariff_periods"


def _as_kwh(value) -> float | None:
    try:
        return float(value) / WH_PER_KWH
    except TypeError, ValueError:
        return None


def tariff_periods_kwh(pv: ideenergy.PeriodValue) -> dict[str, float]:
    """Per tariff period breakdown (P1, P2, ...) of an hour, in kWh.

    i-DE returns the breakdown in the order of its 'periodos' list (P1 first).
    Names are normalized to P<n> by position unless they already are.
    """
    desglosed = getattr(pv, "desglosed", None) or {}

    ret = {}
    for idx, (name, value) in enumerate(desglosed.items()):
        kwh = _as_kwh(value)
        if kwh is None:
            continue

        key = name if TARIFF_PERIOD_RE.fullmatch(str(name)) else f"P{idx + 1}"
        ret[key] = kwh

    return ret


def drop_trailing_zeros(hist_states: list[HistoricalState]) -> list[HistoricalState]:
    """Defer zero-valued hours at the end of the series.

    i-DE may report hours it has not consolidated yet as 0. Zeros between
    real values are genuine (0 kWh hours) and are kept; trailing zeros are
    dropped now and written in a later refresh, once a following hour with
    data exists (statistics are only appended after the last written hour).
    """
    last_nonzero = next(
        (idx for idx in range(len(hist_states) - 1, -1, -1) if hist_states[idx].state),
        None,
    )
    if last_nonzero is None:
        return []

    return hist_states[: last_nonzero + 1]


def hour_index(pv: ideenergy.PeriodValue, day0: datetime) -> int:
    """Position of the period in the i-DE series (0 = first hour of day0)."""
    return int((pv.start - day0) / ONE_HOUR)


def period_utc_bounds(
    pv: ideenergy.PeriodValue, day0: datetime
) -> tuple[datetime, datetime]:
    """Real (UTC) start/end of an i-DE hourly period.

    i-DE returns one value per real hour starting at day0 local midnight: 23
    values on the last Sunday of March and 25 on the last Sunday of October.
    ideenergy builds naive datetimes as `day0 + idx hours`, which is wrong
    after a DST change (the repeated 02:00-03:00 in October would collide and
    every later hour would be shifted). Rebuild the instant from the index
    using absolute (UTC) arithmetic instead.
    """
    day0_utc = dt_util.as_utc(day0.replace(tzinfo=LOCAL_TZ))
    start = day0_utc + hour_index(pv, day0) * ONE_HOUR
    return start, start + ONE_HOUR


def periods_as_historical_states(
    periods: Iterable[ideenergy.PeriodValue],
    *,
    day0: datetime | None = None,
    now: datetime | None = None,
) -> list[HistoricalState]:
    """Convert i-DE periods (Wh, naive local datetimes) into kWh states.

    `day0` is the naive local midnight the i-DE series starts at (the
    requested start date). The state timestamp is the end of the hour. Hours
    without a numeric value or ending in the future are skipped, 0 kWh hours
    are kept (see drop_trailing_zeros).
    """
    periods = list(periods)
    if not periods:
        return []

    if day0 is None:
        day0 = min(pv.start for pv in periods).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

    now_ts = dt_util.as_timestamp(now or dt_util.now())

    ret = []
    for pv in periods:
        kwh = _as_kwh(pv.value)
        if kwh is None:
            LOGGER.debug(f"skipping period without value: {pv!r}")
            continue

        start, end = period_utc_bounds(pv, day0)
        end_ts = dt_util.as_timestamp(end)
        if end_ts > now_ts:
            continue

        attributes = {"last_reset": start.astimezone(LOCAL_TZ)}
        if tariff_periods := tariff_periods_kwh(pv):
            attributes[TARIFF_PERIODS_ATTR] = tariff_periods

        ret.append(HistoricalState(state=kwh, timestamp=end_ts, attributes=attributes))

    ret.sort(key=lambda hs: hs.timestamp)
    return drop_trailing_zeros(ret)


def tariff_period_states(
    hist_states: Iterable[HistoricalState], period: str
) -> list[HistoricalState]:
    """Project total consumption states onto a single tariff period.

    Hours without breakdown are skipped; hours where the period is not
    active are 0 kWh (needed for a continuous cumulative sum).
    """
    ret = []
    for hs in hist_states:
        breakdown = (hs.attributes or {}).get(TARIFF_PERIODS_ATTR)
        if not breakdown:
            continue

        ret.append(
            HistoricalState(
                state=breakdown.get(period, 0.0),
                timestamp=hs.timestamp,
                attributes={"last_reset": hs.attributes["last_reset"]},
            )
        )

    return ret
