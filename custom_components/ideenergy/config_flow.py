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


import os
from collections.abc import Mapping
from logging import getLogger
from typing import Any

import ideenergy
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .client import Client
from .const import CONF_CONTRACT, CONFIG_ENTRY_VERSION, DOMAIN
from .coordinator import AUTH_LOGIN_ERRORS, DATASET_FETCH_ERRORS

LOGGER = getLogger(__name__)

AUTH_SCHEMA = vol.Schema(
    {
        vol.Required(
            CONF_USERNAME, default=os.environ.get("I_DE_ENERYGY_USERNAME")
        ): str,
        vol.Required(
            CONF_PASSWORD, default=os.environ.get("I_DE_ENERYGY_PASSWORD")
        ): str,
    }
)

REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): str})


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):  # type: ignore[call-arg]
    VERSION = CONFIG_ENTRY_VERSION

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.info = {}
        self.api: ideenergy.Client | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a flow initialized by the user."""
        errors = {}

        if user_input is not None:
            # unique_id (CUPS) is set in async_step_contract
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]

            try:
                self.api = await create_api(self.hass, username, password)

            except AUTH_LOGIN_ERRORS:
                errors["base"] = "invalid_auth"

            except DATASET_FETCH_ERRORS:
                errors["base"] = "cannot_connect"

            except Exception:  # pylint: disable=broad-except
                LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

            else:
                self.info.update(
                    {
                        CONF_USERNAME: username,
                        CONF_PASSWORD: password,
                    }
                )
                return await self.async_step_contract()

        return self.async_show_form(
            step_id="user",
            data_schema=AUTH_SCHEMA,
            errors=errors,
        )

    async def async_step_contract(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        contracts = await self.api.get_contracts()
        contracts = {f"{x['cups']} ({x['direccion']})": x for x in contracts}

        schema = vol.Schema({vol.Required(CONF_CONTRACT): vol.In(contracts.keys())})

        if not user_input:
            return self.async_show_form(step_id="contract", data_schema=schema)

        contract = contracts[user_input["contract"]]

        # One config entry per supply point (CUPS)
        await self.async_set_unique_id(contract["cups"])
        self._abort_if_unique_id_configured()

        self.info.update(
            {
                CONF_CONTRACT: contract["codContrato"],
            }
        )

        title = "CUPS " + contract["cups"]
        return self.async_create_entry(title=title, data=self.info)

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauth when i-DE rejects the stored credentials."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new password and validate it against i-DE."""
        reauth_entry = self._get_reauth_entry()
        username = reauth_entry.data[CONF_USERNAME]
        errors = {}

        if user_input is not None:
            password = user_input[CONF_PASSWORD]
            try:
                await create_api(self.hass, username, password)

            except AUTH_LOGIN_ERRORS:
                errors["base"] = "invalid_auth"

            except DATASET_FETCH_ERRORS:
                errors["base"] = "cannot_connect"

            except Exception:  # pylint: disable=broad-except
                LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

            else:
                return self.async_update_reload_and_abort(
                    reauth_entry, data_updates={CONF_PASSWORD: password}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            errors=errors,
            description_placeholders={CONF_USERNAME: username},
        )


# class OptionsFlowHandler(config_entries.OptionsFlow):
#     def __init__(self, config_entry):
#         """Initialize options flow."""
#         self.config_entry = config_entry
#
#     async def async_step_init(self, user_input=None):
#         if user_input is not None:
#             return self.async_create_entry(title="", data=user_input)
#
#         OPTIONS_SCHEMA = vol.Schema({})
#
#         return self.async_show_form(step_id="init", data_schema=OPTIONS_SCHEMA)


async def create_api(hass, username, password):
    sess = async_create_clientsession(hass)
    client = Client(sess, username, password)

    await client.login()
    return client
