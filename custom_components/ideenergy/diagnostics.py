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

"""Diagnostics: session, backoff and dataset state without credentials."""

from __future__ import annotations

from importlib import metadata
from typing import Any

from homeassistant.components.diagnostics import REDACTED, async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .client import mask_username
from .const import CONF_CONTRACT
from .coordinator import (
    DATASET_POLICIES,
    IDeEnergyCoordinatorDataSet,
    IDeEnergyDataCoordinator,
    backoff_delay,
)
from .data import IntegrationIDeEnergyConfigEntry
from .historical import TARIFF_PERIODS_ATTR

TO_REDACT = {CONF_USERNAME, CONF_PASSWORD, CONF_CONTRACT, "title", "unique_id"}


def _scrub(text: str, secrets: list[str]) -> str:
    """Remove sensitive values from free text (e.g. exception messages)."""
    for secret in sorted(filter(None, secrets), key=len, reverse=True):
        text = text.replace(secret, REDACTED)

    return text


def _iso(ts: Any) -> str | None:
    try:
        return dt_util.utc_from_timestamp(float(ts)).isoformat()
    except TypeError, ValueError:
        return None


def _data_summary(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None

    if isinstance(value, dict):
        return {"values": value}

    timestamps = [hs.timestamp for hs in value]
    tariff_periods = sorted(
        {
            period
            for hs in value
            for period in (hs.attributes or {}).get(TARIFF_PERIODS_ATTR, {})
        }
    )
    return {
        "hours": len(value),
        "first_hour_end": _iso(min(timestamps)) if timestamps else None,
        "last_hour_end": _iso(max(timestamps)) if timestamps else None,
        "zero_hours": sum(1 for hs in value if hs.state == 0),
        "tariff_periods": tariff_periods,
    }


def _dataset_diagnostics(
    coordinator: IDeEnergyDataCoordinator,
    dataset: IDeEnergyCoordinatorDataSet,
    stored: dict,
) -> dict[str, Any]:
    policy = DATASET_POLICIES[dataset]
    failures = coordinator.consecutive_failures(dataset)
    delay = backoff_delay(failures)

    return {
        "active": coordinator.dataset_counter[dataset.name] > 0,
        "consecutive_failures": failures,
        "backoff_delay_s": delay.total_seconds() if delay else None,
        "last_success": _iso(stored.get(policy.last_success_key)),
        "last_attempt": _iso(stored.get(policy.last_attempt_key)),
        "success_max_age_s": policy.success_max_age.total_seconds(),
        "data": _data_summary(coordinator.data.get(dataset)),
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: IntegrationIDeEnergyConfigEntry
) -> dict[str, Any]:
    runtime = entry.runtime_data
    coordinator = runtime.coordinator
    client = coordinator.client
    stored = dict(coordinator.config_entry_state.data or {})

    last_exception = coordinator.last_exception
    try:
        library_version = metadata.version("ideenergy")
    except metadata.PackageNotFoundError:
        library_version = None

    return {
        "entry": async_redact_data(
            {
                "title": entry.title,
                "unique_id": entry.unique_id,
                "version": entry.version,
                "data": dict(entry.data),
            },
            TO_REDACT,
        ),
        "integration_version": str(runtime.integration.version),
        "client": {
            "username": mask_username(client.username),
            "library_version": library_version,
            "session_timeout_s": client.session_timeout.total_seconds(),
            "is_logged": client.is_logged,
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "last_exception": (
                _scrub(
                    f"{type(last_exception).__name__}: {last_exception}",
                    [str(entry.data.get(key) or "") for key in TO_REDACT]
                    + [entry.unique_id or "", entry.title],
                )
                if last_exception
                else None
            ),
            "update_interval_s": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
            "backfill_targets": [e.entity_id for e in coordinator.backfill_targets],
            "datasets": {
                ds.name: _dataset_diagnostics(coordinator, ds, stored)
                for ds in IDeEnergyCoordinatorDataSet
            },
        },
    }
