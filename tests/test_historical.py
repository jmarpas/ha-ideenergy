"""Tests for i-DE period -> HistoricalState conversion."""

from __future__ import annotations

from datetime import datetime, timedelta

import ideenergy
import pytest

from custom_components.ideenergy.const import LOCAL_TZ
from custom_components.ideenergy.historical import (
    periods_as_historical_states,
    tariff_period_states,
)

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


#
# DST changes (Europe/Madrid)
#

FALL_BACK_DAY = datetime(2026, 10, 25)  # 25 hours: 02:00-03:00 happens twice
SPRING_FORWARD_DAY = datetime(2026, 3, 29)  # 23 hours: 02:00-03:00 skipped


def assert_consecutive_hours(states):
    diffs = {b.timestamp - a.timestamp for a, b in zip(states, states[1:])}
    assert diffs == {3600}


def test_october_fold_day_has_25_distinct_hours():
    states = periods_as_historical_states(
        periods(range(1, 26), day0=FALL_BACK_DAY), day0=FALL_BACK_DAY, now=NOW
    )

    assert len(states) == 25
    assert_consecutive_hours(states)
    assert local_hours(states)[:4] == ["01:00", "02:00", "02:00", "03:00"]
    last = datetime.fromtimestamp(states[-1].timestamp, LOCAL_TZ)
    assert last == datetime(2026, 10, 26, 0, 0, tzinfo=LOCAL_TZ)
    # Both 02:00-03:00 hours keep their own value instead of being merged
    assert [hs.state for hs in states[2:4]] == [0.003, 0.004]


def test_october_fold_last_reset_marks_both_repeated_hours():
    states = periods_as_historical_states(
        periods(range(1, 26), day0=FALL_BACK_DAY), day0=FALL_BACK_DAY, now=NOW
    )

    first, second = states[2].attributes["last_reset"], states[3].attributes[
        "last_reset"
    ]
    assert first.strftime("%H:%M %Z") == "02:00 CEST"
    assert second.strftime("%H:%M %Z") == "02:00 CET"


def test_march_spring_forward_day_has_23_hours():
    states = periods_as_historical_states(
        periods([100] * 23, day0=SPRING_FORWARD_DAY),
        day0=SPRING_FORWARD_DAY,
        now=NOW,
    )

    assert len(states) == 23
    assert_consecutive_hours(states)
    assert local_hours(states)[:3] == ["01:00", "03:00", "04:00"]
    last = datetime.fromtimestamp(states[-1].timestamp, LOCAL_TZ)
    assert last == datetime(2026, 3, 30, 0, 0, tzinfo=LOCAL_TZ)


def test_week_spanning_fold_keeps_following_days_aligned():
    day0 = datetime(2026, 10, 24)
    states = periods_as_historical_states(
        periods([100] * (24 + 25 + 24), day0=day0), day0=day0, now=NOW
    )

    assert len(states) == 73
    assert_consecutive_hours(states)
    last = datetime.fromtimestamp(states[-1].timestamp, LOCAL_TZ)
    assert last == datetime(2026, 10, 27, 0, 0, tzinfo=LOCAL_TZ)


#
# 2.0TD tariff periods
#


def consumption_periods(rows, day0=DAY0):
    """rows: list of (total_wh, {period_name: wh})"""
    return [
        ideenergy.ConsumptionForPeriod(
            start=day0 + timedelta(hours=idx),
            end=day0 + timedelta(hours=idx + 1),
            value=total,
            desglosed=breakdown,
        )
        for idx, (total, breakdown) in enumerate(rows)
    ]


def test_tariff_breakdown_is_attached_in_kwh():
    rows = [(300, {"P1": 0, "P2": 0, "P3": 300}), (500, {"P1": 500, "P2": 0, "P3": 0})]
    states = periods_as_historical_states(consumption_periods(rows), now=NOW)

    assert states[0].attributes["tariff_periods"] == {"P1": 0.0, "P2": 0.0, "P3": 0.3}
    assert states[1].attributes["tariff_periods"]["P1"] == 0.5


def test_tariff_period_names_are_normalized_by_position():
    rows = [(300, {"Punta": 100, "Llano": 200, "Valle": 0})]
    states = periods_as_historical_states(consumption_periods(rows), now=NOW)

    assert states[0].attributes["tariff_periods"] == {"P1": 0.1, "P2": 0.2, "P3": 0.0}


def test_tariff_period_projection_keeps_zero_hours():
    rows = [
        (300, {"P1": 0, "P2": 0, "P3": 300}),
        (500, {"P1": 500, "P2": 0, "P3": 0}),
        (200, {"P1": 0, "P2": 200, "P3": 0}),
    ]
    states = periods_as_historical_states(consumption_periods(rows), now=NOW)

    assert [hs.state for hs in tariff_period_states(states, "P1")] == [0.0, 0.5, 0.0]
    assert [hs.state for hs in tariff_period_states(states, "P3")] == [0.3, 0.0, 0.0]
    assert [hs.timestamp for hs in tariff_period_states(states, "P2")] == [
        hs.timestamp for hs in states
    ]


def test_tariff_period_projection_skips_hours_without_breakdown():
    rows = [(300, {}), (500, {"P1": 500, "P2": 0, "P3": 0})]
    states = periods_as_historical_states(consumption_periods(rows), now=NOW)

    assert len(tariff_period_states(states, "P1")) == 1


def test_generation_periods_have_no_breakdown():
    states = periods_as_historical_states(periods([100]), now=NOW)

    assert "tariff_periods" not in states[0].attributes
    assert tariff_period_states(states, "P1") == []
