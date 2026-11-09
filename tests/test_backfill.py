"""Backfill tests: sum rebuilding, recorder round trip, chunked download and
service validation."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    statistics_during_period,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant_historical_sensor import HistoricalState
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.ideenergy import coordinator as coordinator_module
from custom_components.ideenergy.backfill import (
    async_rebuild_statistics,
    hourly_values,
    rebuild_rows,
)
from custom_components.ideenergy.services import validate_backfill_range

HOUR = 3600
T0 = datetime(2026, 1, 1, tzinfo=UTC).timestamp()


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations():
    """Override conftest's: recorder_mock must be set up before hass."""
    return


def state_at(hour_idx: int, kwh: float) -> HistoricalState:
    """State for hour T0+idx (stamped at the end of the hour)."""
    return HistoricalState(state=kwh, timestamp=T0 + (hour_idx + 1) * HOUR)


#
# Pure helpers
#


def test_hourly_values_groups_by_hour_start():
    assert hourly_values([state_at(0, 1.0), state_at(1, 0.0)]) == {
        T0: 1.0,
        T0 + HOUR: 0.0,
    }


def test_rebuild_rows_recomputes_sums_after_backfilled_hours():
    existing = [
        {"start": T0 + 2 * HOUR, "state": 2.0, "sum": 12.0},
        {"start": T0 + 3 * HOUR, "state": 3.0, "sum": 15.0},
    ]
    new_hours = {T0: 1.0, T0 + HOUR: 0.5}

    rows = rebuild_rows(new_hours=new_hours, existing_rows=existing, base_sum=10.0)

    assert [r["state"] for r in rows] == [1.0, 0.5, 2.0, 3.0]
    assert [r["sum"] for r in rows] == [11.0, 11.5, 13.5, 16.5]


def test_rebuild_rows_new_values_override_existing():
    existing = [{"start": T0, "state": 9.0, "sum": 9.0}]

    rows = rebuild_rows(new_hours={T0: 1.0}, existing_rows=existing, base_sum=0.0)

    assert [(r["state"], r["sum"]) for r in rows] == [(1.0, 1.0)]


#
# Recorder round trip
#

STATISTIC_ID = "sensor:test_historical_consumption"
METADATA = StatisticMetaData(
    has_sum=True,
    mean_type=StatisticMeanType.NONE,
    name="Test",
    source="sensor",
    statistic_id=STATISTIC_ID,
    unit_class="energy",
    unit_of_measurement="kWh",
)


async def read_rows(hass):
    from homeassistant.components.recorder import get_instance

    res = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        datetime(2000, 1, 1, tzinfo=UTC),
        None,
        {STATISTIC_ID},
        "hour",
        None,
        {"state", "sum"},
    )
    return res.get(STATISTIC_ID, [])


async def test_backfill_before_existing_statistics(recorder_mock, hass):
    # Existing statistics for hours 10-11 (written by regular refreshes)
    async_add_external_statistics(
        hass,
        METADATA,
        [
            StatisticData(
                start=datetime.fromtimestamp(T0 + 10 * HOUR, UTC), state=2.0, sum=2.0
            ),
            StatisticData(
                start=datetime.fromtimestamp(T0 + 11 * HOUR, UTC), state=3.0, sum=5.0
            ),
        ],
    )
    await async_wait_recording_done(hass)

    written = await async_rebuild_statistics(
        hass, METADATA, [state_at(idx, 1.0) for idx in range(10)]
    )
    await async_wait_recording_done(hass)

    rows = await read_rows(hass)
    assert written == 10
    assert len(rows) == 12
    assert [r["sum"] for r in rows] == [float(n) for n in range(1, 11)] + [12.0, 15.0]


async def test_backfill_inside_existing_range_keeps_base_sum(recorder_mock, hass):
    async_add_external_statistics(
        hass,
        METADATA,
        [
            StatisticData(
                start=datetime.fromtimestamp(T0 + idx * HOUR, UTC),
                state=1.0,
                sum=100.0 + idx + 1,
            )
            for idx in range(5)
        ],
    )
    await async_wait_recording_done(hass)

    # Hour 2 was wrong (1.0), i-DE says 4.0
    await async_rebuild_statistics(hass, METADATA, [state_at(2, 4.0)])
    await async_wait_recording_done(hass)

    rows = await read_rows(hass)
    assert [r["sum"] for r in rows] == [101.0, 102.0, 106.0, 107.0, 108.0]


#
# Chunked download
#


async def test_range_fetch_is_chunked_and_deduped(
    coordinator, client, monkeypatch, freezer
):
    freezer.move_to("2026-02-01 12:00:00+01:00")
    monkeypatch.setattr(coordinator_module, "BACKFILL_REQUEST_DELAY", timedelta(0))

    states = await coordinator.async_fetch_historical_consumption_range(
        date(2026, 1, 1), date(2026, 1, 20)
    )

    # 20 days -> 3 requests of up to 7 days
    assert client.calls["consumption"] == 3
    timestamps = [hs.timestamp for hs in states]
    assert timestamps == sorted(set(timestamps))
    assert max(timestamps) <= datetime(2026, 1, 21, tzinfo=UTC).timestamp()
    assert min(timestamps) >= datetime(2025, 12, 31, 23, tzinfo=UTC).timestamp()


#
# Service validation
#

TODAY = date(2026, 11, 9)


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (date(2026, 11, 2), date(2026, 11, 1)),  # reversed
        (date(2026, 11, 1), date(2026, 11, 10)),  # future
        (date(2024, 1, 1), date(2026, 11, 1)),  # too long
    ],
)
def test_invalid_backfill_ranges(start, end):
    with pytest.raises(ServiceValidationError):
        validate_backfill_range(start, end, TODAY)


def test_valid_backfill_range():
    validate_backfill_range(date(2025, 11, 10), TODAY, TODAY)
