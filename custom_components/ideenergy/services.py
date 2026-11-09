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

"""Integration services."""

from __future__ import annotations

from datetime import date
from logging import getLogger

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import DATASET_FETCH_ERRORS

LOGGER = getLogger(__name__)

SERVICE_BACKFILL_HISTORICAL_CONSUMPTION = "backfill_historical_consumption"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_START = "start"
ATTR_END = "end"

# i-DE keeps about two years of hourly data
MAX_BACKFILL_DAYS = 730

BACKFILL_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_START): cv.date,
        vol.Optional(ATTR_END): cv.date,
    }
)


def validate_backfill_range(start: date, end: date, today: date) -> None:
    if start > end:
        raise ServiceValidationError("'start' must be before or equal to 'end'")

    if end > today:
        raise ServiceValidationError("'end' can not be in the future")

    if (end - start).days + 1 > MAX_BACKFILL_DAYS:
        raise ServiceValidationError(
            f"Range too long, maximum is {MAX_BACKFILL_DAYS} days"
        )


async def _async_backfill_historical_consumption(call: ServiceCall) -> ServiceResponse:
    hass = call.hass
    entry_id = call.data[ATTR_CONFIG_ENTRY_ID]
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ServiceValidationError(f"Unknown {DOMAIN} config entry: {entry_id}")

    if entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(f"Config entry {entry.title} is not loaded")

    today = dt_util.now().date()
    start: date = call.data[ATTR_START]
    end: date = call.data.get(ATTR_END) or today
    validate_backfill_range(start, end, today)

    coordinator = entry.runtime_data.coordinator
    targets = coordinator.backfill_targets
    if not targets:
        raise ServiceValidationError(
            "No enabled historical consumption sensor to backfill"
        )

    try:
        states = await coordinator.async_fetch_historical_consumption_range(
            start, end
        )
    except DATASET_FETCH_ERRORS as e:
        raise HomeAssistantError(f"Unable to download data from i-DE: {e}") from e

    written = {}
    for entity in targets:
        written[entity.entity_id] = await entity.async_backfill_statistics(states)

    LOGGER.info(
        f"backfill {start} - {end}: {len(states)} hours downloaded, written={written}"
    )
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "hours_downloaded": len(states),
        "hours_written": written,
    }


def async_setup_services(hass: HomeAssistant) -> None:
    """Register integration services (once per HA instance)."""
    hass.services.async_register(
        DOMAIN,
        SERVICE_BACKFILL_HISTORICAL_CONSUMPTION,
        _async_backfill_historical_consumption,
        schema=BACKFILL_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
