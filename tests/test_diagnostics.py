"""Diagnostics never leak credentials and expose backoff/session state."""

from __future__ import annotations

import json
from types import SimpleNamespace

from custom_components.ideenergy.coordinator import IDeEnergyCoordinatorDataSet
from custom_components.ideenergy.data import IntegrationIDeEnergyRunTimeData
from custom_components.ideenergy.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import TEST_CONTRACT, TEST_CUPS, TEST_PASSWORD, TEST_USERNAME, http_error

DIRECT = IDeEnergyCoordinatorDataSet.DIRECT_READING


async def test_diagnostics_redacts_and_reports_state(
    hass, config_entry, coordinator, client
):
    coordinator.dataset_counter[DIRECT.name] = 1
    client.measure_errors = [http_error(500)]
    await coordinator.async_refresh()

    config_entry.runtime_data = IntegrationIDeEnergyRunTimeData(
        coordinator=coordinator,
        device_info={},
        integration=SimpleNamespace(version="3.0.0a3+jaime.2"),
    )

    diag = await async_get_config_entry_diagnostics(hass, config_entry)
    dumped = json.dumps(diag)

    for secret in (TEST_USERNAME, TEST_PASSWORD, TEST_CONTRACT, TEST_CUPS):
        assert secret not in dumped

    assert diag["client"]["username"] == "jau***@example.com"
    assert diag["client"]["session_timeout_s"] == 13 * 60
    assert diag["coordinator"]["last_update_success"] is False
    direct = diag["coordinator"]["datasets"]["DIRECT_READING"]
    assert direct["active"] is True
    assert direct["consecutive_failures"] == 1
    assert direct["backoff_delay_s"] == 300
    assert direct["last_attempt"] is not None
