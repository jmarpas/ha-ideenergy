"""Config flow tests: unique_id by CUPS (9), reauth (7) and unique_id backfill."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import ideenergy
import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ideenergy import async_backfill_entry_unique_id
from custom_components.ideenergy.const import CONF_CONTRACT, DOMAIN

from .conftest import (
    TEST_CONTRACT,
    TEST_CUPS,
    TEST_PASSWORD,
    TEST_USERNAME,
    ScriptedClient,
)

CREATE_API = "custom_components.ideenergy.config_flow.create_api"
SETUP_ENTRY = "custom_components.ideenergy.async_setup_entry"


@pytest.fixture
def mock_setup_entry():
    with patch(SETUP_ENTRY, return_value=True) as mock:
        yield mock


async def run_user_flow(hass):
    """Go through the user + contract steps with a MockClient-based API."""
    api = ScriptedClient()
    await api.login()

    with patch(CREATE_API, AsyncMock(return_value=api)):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: TEST_USERNAME, CONF_PASSWORD: TEST_PASSWORD},
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "contract"

        # The contract selector is vol.In(<labels>); pick the only one
        contract_selector = next(iter(result["data_schema"].schema.values()))
        contract_label = next(iter(contract_selector.container))
        return await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_CONTRACT: contract_label}
        )


#
# Point 9: unique_id = CUPS, duplicates aborted
#


async def test_user_flow_sets_cups_as_unique_id(hass, mock_setup_entry):
    result = await run_user_flow(hass)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == TEST_CUPS
    assert result["data"][CONF_CONTRACT] == TEST_CONTRACT


async def test_user_flow_aborts_if_cups_already_configured(
    hass, config_entry, mock_setup_entry
):
    result = await run_user_flow(hass)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ideenergy.CommandError({"success": "false"}), "invalid_auth"),
        (TimeoutError(), "cannot_connect"),
        (RuntimeError("boom"), "unknown"),
    ],
)
async def test_user_flow_errors(hass, error, expected):
    with patch(CREATE_API, AsyncMock(side_effect=error)):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: TEST_USERNAME, CONF_PASSWORD: TEST_PASSWORD},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}


#
# Backfill of unique_id on entries created by older versions
#


async def test_backfill_unique_id_on_legacy_entry(hass):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=None, data={})
    entry.add_to_hass(hass)

    async_backfill_entry_unique_id(hass, entry, TEST_CUPS)

    assert entry.unique_id == TEST_CUPS


async def test_backfill_skipped_when_cups_owned_by_other_entry(hass, config_entry):
    legacy = MockConfigEntry(domain=DOMAIN, unique_id=None, data={})
    legacy.add_to_hass(hass)

    async_backfill_entry_unique_id(hass, legacy, TEST_CUPS)

    assert legacy.unique_id is None


#
# Point 7: reauth flow
#


async def test_reauth_updates_password(hass, config_entry, mock_setup_entry):
    result = await config_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    with patch(CREATE_API, AsyncMock(return_value=ScriptedClient())) as create_api:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "new-password"}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_PASSWORD] == "new-password"
    assert config_entry.data[CONF_USERNAME] == TEST_USERNAME
    create_api.assert_awaited_once_with(hass, TEST_USERNAME, "new-password")


async def test_reauth_invalid_password(hass, config_entry):
    result = await config_entry.start_reauth_flow(hass)

    with patch(CREATE_API, AsyncMock(side_effect=ideenergy.CommandError({}))):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "wrong"}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert config_entry.data[CONF_PASSWORD] == TEST_PASSWORD
