"""Coordinator tests: re-login/retry (4), backoff (5), conditional renew (6),
UpdateFailed (8) and reauth trigger (7)."""

from __future__ import annotations

from datetime import timedelta

import ideenergy
import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util

from custom_components.ideenergy.coordinator import (
    BACKOFF_DELAYS,
    DATASET_POLICIES,
    MEASURE_ACCUMULATED_KEY,
    IDeEnergyCoordinatorDataSet,
    IDeEnergyDataCoordinator,
    backoff_delay,
)
from custom_components.ideenergy.store import IDeEnergyConfigEntryState

from .conftest import http_error

DIRECT = IDeEnergyCoordinatorDataSet.DIRECT_READING
CONSUMPTION = IDeEnergyCoordinatorDataSet.HISTORICAL_CONSUMPTION
ONE_SECOND = timedelta(seconds=1)


def enable(coordinator, *datasets) -> None:
    # Set ref counts directly: activate_dataset() also schedules a refresh
    for ds in datasets:
        coordinator.dataset_counter[ds.name] = 1


async def update(coordinator):
    """Run one update cycle, tolerating UpdateFailed (all datasets failing)."""
    try:
        return await coordinator._async_update_data()
    except UpdateFailed:
        return coordinator.data


#
# Point 4: invalidate session, re-login and retry ONCE on 401/403
#


@pytest.mark.parametrize("status", [401, 403])
async def test_auth_error_relogins_and_retries_once(coordinator, client, status):
    enable(coordinator, DIRECT)
    client.measure_errors = [http_error(status)]

    data = await coordinator._async_update_data()

    assert client.calls["measure"] == 2
    assert client.calls["login"] == 2  # initial login + re-login
    assert data[DIRECT][MEASURE_ACCUMULATED_KEY] is not None
    assert coordinator.consecutive_failures(DIRECT) == 0


async def test_auth_error_is_retried_only_once(coordinator, client):
    enable(coordinator, DIRECT)
    client.measure_errors = [http_error(403), http_error(403)]

    data = await update(coordinator)

    assert client.calls["measure"] == 2
    assert data[DIRECT] is None
    assert coordinator.consecutive_failures(DIRECT) == 1


async def test_relogin_invalidates_session(coordinator, client):
    enable(coordinator, DIRECT)
    await client.login()
    login_ts_before = client._login_ts
    client.measure_errors = [http_error(403)]

    await coordinator._async_update_data()

    assert client.calls["login"] == 2
    assert client._login_ts is not None
    assert client._login_ts >= login_ts_before


async def test_non_auth_error_is_not_retried(coordinator, client):
    enable(coordinator, DIRECT)
    client.measure_errors = [http_error(500)]

    await update(coordinator)

    assert client.calls["measure"] == 1
    assert client.calls["login"] == 1
    assert coordinator.consecutive_failures(DIRECT) == 1


async def test_renew_session_auth_error_relogins(coordinator, client):
    enable(coordinator, DIRECT)
    client.renew_errors = [http_error(401)]

    await coordinator._async_update_data()

    assert client.calls["renew"] == 2
    assert client.calls["login"] == 2
    assert client.calls["measure"] == 1


async def test_rejected_credentials_raise_auth_failed(coordinator, client):
    enable(coordinator, DIRECT)
    client.login_errors = [ideenergy.CommandError({"success": "false"})]

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()

    assert client.calls["measure"] == 0


async def test_login_network_error_counts_as_dataset_failure(coordinator, client):
    enable(coordinator, DIRECT)
    client.login_errors = [TimeoutError()]

    await update(coordinator)

    assert client.calls["renew"] == 0
    assert client.calls["measure"] == 0
    assert coordinator.consecutive_failures(DIRECT) == 1


#
# Point 5: exponential backoff per dataset, persisted in the store
#


def test_backoff_delay_steps():
    assert backoff_delay(0) is None
    assert [backoff_delay(n) for n in range(1, 7)] == [
        timedelta(minutes=5),
        timedelta(minutes=30),
        timedelta(hours=2),
        timedelta(hours=6),
        timedelta(hours=6),
        timedelta(hours=6),
    ]


async def test_backoff_progression(coordinator, client, freezer):
    enable(coordinator, DIRECT)
    client.measure_errors = [http_error(500)] * 10

    await update(coordinator)
    assert client.calls["measure"] == 1

    for failures, delay in enumerate(BACKOFF_DELAYS + BACKOFF_DELAYS[-1:], start=1):
        assert coordinator.consecutive_failures(DIRECT) == failures

        # Still inside the backoff window: no request at all
        freezer.tick(delay - ONE_SECOND)
        await update(coordinator)
        assert client.calls["measure"] == failures

        # Window elapsed: exactly one new request
        freezer.tick(2 * ONE_SECOND)
        await update(coordinator)
        assert client.calls["measure"] == failures + 1


async def test_backoff_is_per_dataset(coordinator, client):
    enable(coordinator, DIRECT, CONSUMPTION)
    client.measure_errors = [http_error(500)] * 2

    await coordinator._async_update_data()

    assert coordinator.consecutive_failures(DIRECT) == 1
    assert coordinator.consecutive_failures(CONSUMPTION) == 0
    assert client.calls["consumption"] == 1


async def test_backoff_counter_is_persisted_and_reset(
    hass, coordinator, client, config_entry, freezer
):
    enable(coordinator, DIRECT)
    policy = DATASET_POLICIES[DIRECT]
    client.measure_errors = [http_error(500)] * 2

    await update(coordinator)
    freezer.tick(BACKOFF_DELAYS[0] + ONE_SECOND)
    await update(coordinator)

    reloaded = IDeEnergyConfigEntryState(hass, config_entry)
    await reloaded.async_load()
    assert reloaded.data[policy.consecutive_failures_key] == 2
    assert reloaded.data[policy.last_attempt_key] == pytest.approx(
        dt_util.as_timestamp(dt_util.now())
    )

    freezer.tick(BACKOFF_DELAYS[1] + ONE_SECOND)
    await coordinator._async_update_data()

    reloaded = IDeEnergyConfigEntryState(hass, config_entry)
    await reloaded.async_load()
    assert reloaded.data[policy.consecutive_failures_key] == 0
    assert policy.last_success_key in reloaded.data


async def test_backoff_survives_restart(hass, client, entry_state, freezer):
    """A new coordinator honors the failure counter stored by a previous one."""
    policy = DATASET_POLICIES[DIRECT]
    entry_state.data[policy.consecutive_failures_key] = 3
    entry_state.data[policy.last_attempt_key] = dt_util.as_timestamp(dt_util.now())

    coordinator = IDeEnergyDataCoordinator(
        hass=hass, client=client, config_entry_state=entry_state
    )
    enable(coordinator, DIRECT)

    freezer.tick(BACKOFF_DELAYS[2] - ONE_SECOND)
    await update(coordinator)
    assert client.calls["measure"] == 0


#
# Point 6: renew_session only when some dataset is going to be fetched
#


async def test_no_requests_without_active_datasets(coordinator, client):
    await coordinator._async_update_data()

    assert sum(client.calls.values()) == 0


async def test_no_renew_when_no_dataset_is_due(coordinator, client, freezer):
    enable(coordinator, DIRECT)
    await coordinator._async_update_data()
    assert client.calls["renew"] == 1

    # Last success is recent: nothing is due
    freezer.tick(timedelta(minutes=10))
    await coordinator._async_update_data()

    assert client.calls["renew"] == 1
    assert client.calls["measure"] == 1


async def test_no_renew_while_in_backoff(coordinator, client, freezer):
    enable(coordinator, DIRECT)
    client.measure_errors = [http_error(500)]

    await update(coordinator)
    freezer.tick(timedelta(minutes=1))
    await update(coordinator)

    assert client.calls["renew"] == 1


async def test_renew_failure_does_not_abort_cycle(coordinator, client):
    enable(coordinator, DIRECT)
    client.renew_errors = [http_error(500)]

    data = await coordinator._async_update_data()

    assert client.calls["measure"] == 1
    assert data[DIRECT] is not None


#
# Point 8: UpdateFailed when every active dataset is failing
#


async def test_all_datasets_failing_raises_update_failed(coordinator, client):
    enable(coordinator, DIRECT)
    client.measure_errors = [http_error(500)]

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_partial_failure_does_not_raise(coordinator, client):
    enable(coordinator, DIRECT, CONSUMPTION)
    client.measure_errors = [http_error(500)]

    data = await coordinator._async_update_data()

    assert data[CONSUMPTION] is not None
