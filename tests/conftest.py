"""Shared fixtures for ideenergy tests."""

from __future__ import annotations

from collections import Counter
from types import SimpleNamespace

import ideenergy
import pytest
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ideenergy.client import MockClient
from custom_components.ideenergy.const import (
    CONF_CONTRACT,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
    SESSION_TIMEOUT_OVERRIDE,
)
from custom_components.ideenergy.coordinator import IDeEnergyDataCoordinator
from custom_components.ideenergy.store import IDeEnergyConfigEntryState

TEST_USERNAME = "jaume@example.com"
TEST_PASSWORD = "test-password"
TEST_CONTRACT = "123456789"
TEST_CUPS = "ES0000000000000000AB"


def http_error(status: int) -> ideenergy.RequestFailedError:
    """Build the error the library raises on a non-200 response."""
    return ideenergy.RequestFailedError(
        SimpleNamespace(status=status, reason="test", url="https://i-de.test/x")
    )


class ScriptedClient(MockClient):
    """ideenergy.MockClient with call counters and scripted failures.

    `*_errors` lists are consumed one item per call; an empty list means the
    call succeeds with MockClient's canned data.
    """

    def __init__(self) -> None:
        super().__init__(None, TEST_USERNAME, TEST_PASSWORD, contract=TEST_CONTRACT)
        self._session_timeout = SESSION_TIMEOUT_OVERRIDE
        self.calls: Counter[str] = Counter()
        self.login_errors: list[Exception] = []
        self.renew_errors: list[Exception] = []
        self.measure_errors: list[Exception] = []
        self.consumption_errors: list[Exception] = []

    async def login(self) -> None:
        self.calls["login"] += 1
        if self.login_errors:
            raise self.login_errors.pop(0)
        await super().login()

    async def renew_session(self) -> dict:
        self.calls["renew"] += 1
        if self.renew_errors:
            raise self.renew_errors.pop(0)
        return await super().renew_session()

    async def get_measure(self):
        self.calls["measure"] += 1
        if self.measure_errors:
            raise self.measure_errors.pop(0)
        return await super().get_measure()

    async def get_historical_consumption(self, start=None, end=None):
        self.calls["consumption"] += 1
        if self.consumption_errors:
            raise self.consumption_errors.pop(0)
        return await super().get_historical_consumption(start=start, end=end)

    async def get_contracts(self) -> list[dict]:
        # MockClient returns the raw response; the real client returns the list
        data = await super().get_contracts()
        return [
            contract | {"cups": TEST_CUPS, "codContrato": TEST_CONTRACT}
            for contract in data["contratos"]
        ]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow loading custom_components/ideenergy in every test."""
    return


@pytest.fixture
def config_entry(hass) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=CONFIG_ENTRY_VERSION,
        title=f"CUPS {TEST_CUPS}",
        unique_id=TEST_CUPS,
        data={
            CONF_USERNAME: TEST_USERNAME,
            CONF_PASSWORD: TEST_PASSWORD,
            CONF_CONTRACT: TEST_CONTRACT,
        },
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def client() -> ScriptedClient:
    return ScriptedClient()


@pytest.fixture
async def entry_state(hass, config_entry) -> IDeEnergyConfigEntryState:
    state = IDeEnergyConfigEntryState(hass, config_entry)
    await state.async_load()
    return state


@pytest.fixture
def coordinator(hass, client, entry_state) -> IDeEnergyDataCoordinator:
    return IDeEnergyDataCoordinator(
        hass=hass, client=client, config_entry_state=entry_state
    )
