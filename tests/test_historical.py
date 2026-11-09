"""Tests for i-DE period -> HistoricalState conversion."""

from __future__ import annotations

from datetime import datetime, timedelta

import ideenergy
import pytest

from custom_components.ideenergy.const import LOCAL_TZ
from custom_components.ideenergy.historical import periods_as_historical_states

DAY0 = datetime(2026, 11, 2)  # naive local midnight, no DST change
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=LOCAL_TZ)


def periods(values, day0=DAY0):
    return [
        ideenergy.PeriodValue(
            start=day0 + timedelta(hours=idx),
            end=day0 + timedelta(hours=idx + 1),
            value=value,
        )
        for idx, value in enumerate(values)
    ]


def local_hours(hist_states):
    return [
        datetime.fromtimestamp(hs.timestamp, LOCAL_TZ).strftime("%H:%M")
        for hs in hist_states
    ]


#
# 0 kWh hours
#


def test_zero_hours_between_values_are_kept():
    states = periods_as_historical_states(periods([500, 0, 0, 250]), now=NOW)

    assert [hs.state for hs in states] == [0.5, 0.0, 0.0, 0.25]


def test_trailing_zero_hours_are_deferred():
    states = periods_as_historical_states(periods([500, 0, 250, 0, 0]), now=NOW)

    assert [hs.state for hs in states] == [0.5, 0.0, 0.25]


def test_all_zero_series_is_deferred():
    assert periods_as_historical_states(periods([0, 0, 0]), now=NOW) == []


def test_missing_values_are_skipped():
    states = periods_as_historical_states(periods([500, None, "n/a", 250]), now=NOW)

    assert [hs.state for hs in states] == [0.5, 0.25]


def test_future_hours_are_skipped():
    day0 = datetime(2026, 11, 9)
    states = periods_as_historical_states(periods([100] * 24, day0=day0), now=NOW)

    assert len(states) == 12
    assert local_hours(states)[-1] == "12:00"


def test_timestamp_is_end_of_hour():
    states = periods_as_historical_states(periods([100, 200]), now=NOW)

    assert local_hours(states) == ["01:00", "02:00"]
    assert states[0].attributes["last_reset"] == DAY0.replace(tzinfo=LOCAL_TZ)


@pytest.mark.parametrize("value", [0, 0.0, "0"])
def test_zero_like_values_are_zero(value):
    states = periods_as_historical_states(periods([100, value, 100]), now=NOW)

    assert states[1].state == 0.0
