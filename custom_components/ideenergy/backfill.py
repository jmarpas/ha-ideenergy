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

"""Rewrite hourly statistics with old data while keeping sums consistent.

homeassistant-historical-sensor only appends statistics after the last
written hour. Backfilling older hours requires merging them with existing
rows and recomputing every cumulative `sum` from the first backfilled hour.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from logging import getLogger

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from homeassistant_historical_sensor import HistoricalState

from .historical import hour_start_ts

LOGGER = getLogger(__name__)

# Lower bound used to look up the sum right before the backfilled range
STATISTICS_EPOCH = datetime(2000, 1, 1, tzinfo=UTC)


def hourly_values(hist_states: Iterable[HistoricalState]) -> dict[float, float]:
    """Group states (timestamp = end of hour) by hour start, summing values."""
    ret: dict[float, float] = {}
    for hs in hist_states:
        if hs.state is None:
            continue

        key = hour_start_ts(hs.timestamp)
        ret[key] = ret.get(key, 0.0) + float(hs.state)

    return ret


def rebuild_rows(
    *,
    new_hours: dict[float, float],
    existing_rows: Iterable[dict],
    base_sum: float,
) -> list[StatisticData]:
    """Merge new hourly values over existing rows and recompute sums."""
    merged = {
        float(row["start"]): float(row.get("state") or 0.0) for row in existing_rows
    }
    merged.update(new_hours)

    rows = []
    total = base_sum
    for start_ts in sorted(merged):
        total += merged[start_ts]
        rows.append(
            StatisticData(
                start=dt_util.utc_from_timestamp(start_ts),
                state=merged[start_ts],
                sum=total,
            )
        )

    return rows


async def async_rebuild_statistics(
    hass: HomeAssistant,
    metadata: StatisticMetaData,
    hist_states: Iterable[HistoricalState],
) -> int:
    """Backfill hist_states into the statistic, returning the hours written."""
    new_hours = hourly_values(hist_states)
    if not new_hours:
        return 0

    statistic_id = metadata["statistic_id"]
    first_dt = dt_util.utc_from_timestamp(min(new_hours))
    recorder = get_instance(hass)

    existing = await recorder.async_add_executor_job(
        statistics_during_period,
        hass,
        first_dt,
        None,
        {statistic_id},
        "hour",
        None,
        {"state", "sum"},
    )
    previous = await recorder.async_add_executor_job(
        statistics_during_period,
        hass,
        STATISTICS_EPOCH,
        first_dt,
        {statistic_id},
        "hour",
        None,
        {"sum"},
    )

    previous_rows = previous.get(statistic_id, [])
    base_sum = float(previous_rows[-1].get("sum") or 0.0) if previous_rows else 0.0

    rows = rebuild_rows(
        new_hours=new_hours,
        existing_rows=existing.get(statistic_id, []),
        base_sum=base_sum,
    )
    async_add_external_statistics(hass, metadata, rows)

    # Make sure following regular writes see the rewritten rows
    await recorder.async_block_till_done()

    LOGGER.info(
        f"{statistic_id}: backfilled {len(new_hours)} hours, "
        + f"rewrote {len(rows)} rows from {first_dt} (base sum {base_sum:.3f})"
    )
    return len(new_hours)
