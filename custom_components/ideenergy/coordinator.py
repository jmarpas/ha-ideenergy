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

from __future__ import annotations

import asyncio
import enum
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from logging import DEBUG, getLogger
from typing import Any

import aiohttp
import ideenergy
from homeassistant.core import HomeAssistant, dt_util
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant_historical_sensor import HistoricalState

from .const import LOCAL_TZ, UPDATE_INTERVAL
from .historical import drop_trailing_zeros, periods_as_historical_states
from .store import IDeEnergyConfigEntryState

LOGGER = getLogger(__name__)

# Stored state keys. Names are kept from previous versions so persisted
# timestamps keep being honored after upgrading.

# Direct reading (accumulated consumption, instant demand)
DIRECT_READING_LAST_SUCCESS_STORED_STATE_KEY = "direct_reading_last_success"
DIRECT_READING_LAST_ATTEMPT_STORED_STATE_KEY = "direct_reading_last_attempt"
DIRECT_READING_LAST_SUCCESS_MAX_AGE = timedelta(hours=6)

# Historical consumption
HISTORICAL_CONSUMPTION_LAST_SUCCESS_STORED_STATE_KEY = (
    "historical_consumption_last_success"
)
HISTORICAL_CONSUMPTION_LAST_ATTEMPT_STORED_STATE_KEY = (
    "historical_consumption_last_attempt"
)
HISTORICAL_CONSUMPTION_LAST_SUCCESS_MAX_AGE = timedelta(hours=12)

# Historical Generation
HISTORICAL_GENERATION_LAST_SUCCESS_STORED_STATE_KEY = (
    "historical_generation_last_success"
)
HISTORICAL_GENERATION_LAST_ATTEMPT_STORED_STATE_KEY = (
    "historical_generation_last_attempt"
)
HISTORICAL_GENERATION_LAST_SUCCESS_MAX_AGE = timedelta(hours=12)

# Power demand peaks
POWER_DEMAND_PEAKS_LAST_SUCCESS_STORED_STATE_KEY = "power_demand_peaks_last_success"
POWER_DEMAND_PEAKS_LAST_ATTEMPT_STORED_STATE_KEY = "power_demand_peaks_last_attempt"
POWER_DEMAND_PEAKS_LAST_SUCCESS_MAX_AGE = timedelta(hours=12)

MEASURE_ACCUMULATED_KEY = "measure_accumulated"
MEASURE_INSTANT_KEY = "measure_instant"

HISTORICAL_PERIOD_LENGHT = timedelta(days=7)

# Backfill: same window size as regular refreshes, sequential and paced
BACKFILL_CHUNK = HISTORICAL_PERIOD_LENGHT
BACKFILL_REQUEST_DELAY = timedelta(seconds=2)

# Wait time before retrying a dataset after N consecutive failures:
# 1 failure -> 5 min, 2 -> 30 min, 3 -> 2 h, 4 or more -> 6 h
BACKOFF_DELAYS: tuple[timedelta, ...] = (
    timedelta(minutes=5),
    timedelta(minutes=30),
    timedelta(hours=2),
    timedelta(hours=6),
)

# HTTP statuses that mean "session is no longer valid on i-DE side"
AUTH_ERROR_STATUSES = frozenset({401, 403})

# Errors raised by Client.login() when i-DE rejects the credentials
AUTH_LOGIN_ERRORS = (ideenergy.CommandError, ideenergy.UserExpiredError)

# Errors that make a single dataset fetch fail without aborting the cycle
DATASET_FETCH_ERRORS = (ideenergy.ClientError, aiohttp.ClientError, TimeoutError)


def is_auth_error(exc: BaseException) -> bool:
    """Check if exception is an HTTP 401/403 from the i-DE API."""
    if not isinstance(exc, ideenergy.RequestFailedError):
        return False

    return getattr(exc.response, "status", None) in AUTH_ERROR_STATUSES


def backoff_delay(consecutive_failures: int) -> timedelta | None:
    """Delay to wait before the next attempt, None if there is no failure."""
    if consecutive_failures <= 0:
        return None

    idx = min(consecutive_failures, len(BACKOFF_DELAYS)) - 1
    return BACKOFF_DELAYS[idx]


##
# IDeEnergyCoordinatorDataSet: types of data that can be registered in the
# coordinator to be fetched
class IDeEnergyCoordinatorDataSet(enum.Enum):
    DIRECT_READING = enum.auto()
    HISTORICAL_CONSUMPTION = enum.auto()
    HISTORICAL_GENERATION = enum.auto()
    POWER_DEMAND_PEAKS = enum.auto()


@dataclass(frozen=True)
class DatasetPolicy:
    """Refresh policy for a dataset."""

    last_success_key: str
    last_attempt_key: str
    success_max_age: timedelta

    @property
    def consecutive_failures_key(self) -> str:
        return self.last_attempt_key.removesuffix("_last_attempt") + (
            "_consecutive_failures"
        )


DATASET_POLICIES: dict[IDeEnergyCoordinatorDataSet, DatasetPolicy] = {
    IDeEnergyCoordinatorDataSet.DIRECT_READING: DatasetPolicy(
        last_success_key=DIRECT_READING_LAST_SUCCESS_STORED_STATE_KEY,
        last_attempt_key=DIRECT_READING_LAST_ATTEMPT_STORED_STATE_KEY,
        success_max_age=DIRECT_READING_LAST_SUCCESS_MAX_AGE,
    ),
    IDeEnergyCoordinatorDataSet.HISTORICAL_CONSUMPTION: DatasetPolicy(
        last_success_key=HISTORICAL_CONSUMPTION_LAST_SUCCESS_STORED_STATE_KEY,
        last_attempt_key=HISTORICAL_CONSUMPTION_LAST_ATTEMPT_STORED_STATE_KEY,
        success_max_age=HISTORICAL_CONSUMPTION_LAST_SUCCESS_MAX_AGE,
    ),
    IDeEnergyCoordinatorDataSet.HISTORICAL_GENERATION: DatasetPolicy(
        last_success_key=HISTORICAL_GENERATION_LAST_SUCCESS_STORED_STATE_KEY,
        last_attempt_key=HISTORICAL_GENERATION_LAST_ATTEMPT_STORED_STATE_KEY,
        success_max_age=HISTORICAL_GENERATION_LAST_SUCCESS_MAX_AGE,
    ),
    IDeEnergyCoordinatorDataSet.POWER_DEMAND_PEAKS: DatasetPolicy(
        last_success_key=POWER_DEMAND_PEAKS_LAST_SUCCESS_STORED_STATE_KEY,
        last_attempt_key=POWER_DEMAND_PEAKS_LAST_ATTEMPT_STORED_STATE_KEY,
        success_max_age=POWER_DEMAND_PEAKS_LAST_SUCCESS_MAX_AGE,
    ),
}

# Fetch order inside a refresh cycle
DATASET_FETCH_ORDER = (
    IDeEnergyCoordinatorDataSet.HISTORICAL_CONSUMPTION,
    IDeEnergyCoordinatorDataSet.HISTORICAL_GENERATION,
    IDeEnergyCoordinatorDataSet.POWER_DEMAND_PEAKS,
    IDeEnergyCoordinatorDataSet.DIRECT_READING,
)


##
# IDeEnergyDataCoordinatorData: data stored inside the coordinator
type IDeEnergyDataCoordinatorData = dict[
    IDeEnergyCoordinatorDataSet, list[HistoricalState] | None
]


class IDeEnergyDataCoordinator(DataUpdateCoordinator[IDeEnergyDataCoordinatorData]):
    def __init__(
        self,
        *,
        hass: HomeAssistant,
        client: ideenergy.Client,
        config_entry_state: IDeEnergyConfigEntryState,
        update_interval: timedelta = UPDATE_INTERVAL,
    ):
        name = f"{client} coordinator" if client else "i-de coordinator"
        super().__init__(hass, LOGGER, name=name, update_interval=update_interval)

        # Use dataset names as keys so all counter accesses are consistent.
        self.dataset_counter = {ds.name: 0 for ds in IDeEnergyCoordinatorDataSet}
        self.data = {k: None for k in IDeEnergyCoordinatorDataSet}

        self._client = client
        self._config_entry_state = config_entry_state
        self._backfill_targets: list[Any] = []
        self._fetchers: dict[IDeEnergyCoordinatorDataSet, Callable[[], Awaitable]] = {
            IDeEnergyCoordinatorDataSet.HISTORICAL_CONSUMPTION: self._async_fetch_historical_consumption,
            IDeEnergyCoordinatorDataSet.HISTORICAL_GENERATION: self._async_fetch_historical_generation,
            IDeEnergyCoordinatorDataSet.POWER_DEMAND_PEAKS: self._async_fetch_power_demand_peaks,
            IDeEnergyCoordinatorDataSet.DIRECT_READING: self._async_fetch_direct_reading,
        }

    def activate_dataset(self, dataset: IDeEnergyCoordinatorDataSet) -> None:
        self.dataset_counter[dataset.name] += 1
        if self.dataset_counter[dataset.name] == 1:
            LOGGER.info(f"[{self._client}] dataset {dataset.name} enabled")
            # Fix a better place for this call, it's sub-optimal
            self.hass.async_create_task(self.async_request_refresh())

        LOGGER.debug(
            f"[{self._client}] dataset {dataset.name} ref_count incremented"
            + f" (count={self.dataset_counter[dataset.name]})"
        )

    def deactivate_dataset(self, dataset: IDeEnergyCoordinatorDataSet) -> None:
        if self.dataset_counter[dataset.name] > 0:
            self.dataset_counter[dataset.name] -= 1

        LOGGER.debug(
            f"[{self._client}] dataset {dataset.name} ref_count decremented"
            + f" (count={self.dataset_counter[dataset.name]})"
        )
        if self.dataset_counter[dataset.name] == 0:
            LOGGER.info(f"[{self._client}] dataset {dataset.name} disabled")

    def active_datasets(self) -> list[IDeEnergyCoordinatorDataSet]:
        return [ds for ds in DATASET_FETCH_ORDER if self.dataset_counter[ds.name] > 0]

    async def _async_setup(self) -> None:
        """Set up the coordinator

        This is the place to set up your coordinator,
        or to load data, that only needs to be loaded once.

        This method will be called automatically during
        coordinator.async_config_entry_first_refresh.
        """
        pass

    async def _async_update_data(self) -> IDeEnergyDataCoordinatorData:
        """Fetch data from API endpoint.

        See: https://developers.home-assistant.io/docs/integration_fetching_data/
        """
        active = self.active_datasets()
        dsstr = ", ".join(ds.name for ds in active)
        LOGGER.debug(f"[{self._client}] datasets enabled: {dsstr}")

        data = await self._async_refresh_datasets(active)

        # Entities become unavailable while every active dataset is failing
        # (including cycles where they are skipped because of the backoff).
        if active and all(self.consecutive_failures(ds) > 0 for ds in active):
            raise UpdateFailed(
                f"[{self._client}] all active datasets are failing: {dsstr}"
            )

        return data

    async def _async_refresh_datasets(
        self, active: list[IDeEnergyCoordinatorDataSet]
    ) -> IDeEnergyDataCoordinatorData:
        due = [ds for ds in active if self.is_dataset_due(ds)]
        if not due:
            LOGGER.debug(f"[{self._client}] no dataset due, skipping i-DE requests")
            return self.data

        # Rejected credentials raise ConfigEntryAuthFailed (-> reauth flow).
        # Any other login problem counts as a failure for every due dataset.
        try:
            await self._async_ensure_logged()
        except DATASET_FETCH_ERRORS as e:
            LOGGER.warning(f"[{self._client}] unable to log in: {e}")
            for ds in due:
                await self._async_record_failure(ds)
            return self.data

        # Only touch i-DE when some request is going to be performed.
        # A failed keep-alive is not fatal: datasets will record their own
        # failures (and back off) if the session is really broken. Aborting
        # here would bypass the backoff and hammer i-DE every cycle.
        try:
            await self._async_call_with_reauth(self._client.renew_session)
        except DATASET_FETCH_ERRORS as e:
            LOGGER.warning(f"[{self._client}] unable to renew session: {e}")
        else:
            LOGGER.info(f"[{self._client}] session renewed")

        updated_data = {}
        for ds in due:
            try:
                updated_data[ds] = await self._async_fetch_dataset(ds)
            except DATASET_FETCH_ERRORS as e:
                LOGGER.warning(
                    f"[{self._client}] error updating dataset '{ds.name}': {e}",
                    exc_info=LOGGER.isEnabledFor(DEBUG),  # traceback only on DEBUG
                )
                continue

            LOGGER.info(f"[{self._client}] {ds.name}: dataset updated")

        return self.data | updated_data

    #
    # Refresh policy (gating + backoff)
    #

    def consecutive_failures(self, dataset: IDeEnergyCoordinatorDataSet) -> int:
        key = DATASET_POLICIES[dataset].consecutive_failures_key
        try:
            return max(0, int(self._config_entry_state.data.get(key, 0)))
        except TypeError, ValueError:
            return 0

    def is_dataset_due(self, dataset: IDeEnergyCoordinatorDataSet) -> bool:
        """Check if dataset should be requested to i-DE in this cycle."""
        policy = DATASET_POLICIES[dataset]

        if self._state_is_too_recent_with_debug(
            key=policy.last_success_key,
            max_age=policy.success_max_age,
            label=f"{dataset.name} success check",
        ):
            return False

        failures = self.consecutive_failures(dataset)
        delay = backoff_delay(failures)
        if delay is not None and self._state_is_too_recent_with_debug(
            key=policy.last_attempt_key,
            max_age=delay,
            label=f"{dataset.name} backoff check (failures={failures})",
        ):
            return False

        return True

    async def _async_fetch_dataset(self, dataset: IDeEnergyCoordinatorDataSet):
        """Fetch dataset, recording success or failure in the store."""
        try:
            result = await self._fetchers[dataset]()
        except Exception:
            await self._async_record_failure(dataset)
            raise

        await self._async_record_success(dataset)
        return result

    async def _async_record_success(self, dataset: IDeEnergyCoordinatorDataSet) -> None:
        policy = DATASET_POLICIES[dataset]
        self._config_entry_state.data[policy.last_success_key] = self._now_ts()
        self._config_entry_state.data[policy.consecutive_failures_key] = 0
        await self._config_entry_state.async_save()

    async def _async_record_failure(self, dataset: IDeEnergyCoordinatorDataSet) -> None:
        policy = DATASET_POLICIES[dataset]
        failures = self.consecutive_failures(dataset) + 1
        self._config_entry_state.data[policy.last_attempt_key] = self._now_ts()
        self._config_entry_state.data[policy.consecutive_failures_key] = failures
        await self._config_entry_state.async_save()

        LOGGER.warning(
            f"[{self._client}] {dataset.name}: {failures} consecutive failure(s), "
            + f"next attempt in {backoff_delay(failures)}"
        )

    @staticmethod
    def _now_ts() -> float:
        return dt_util.as_timestamp(dt_util.now())

    #
    # Fetchers
    #

    async def _async_fetch_direct_reading(self) -> dict[str, int | float]:
        data = await self._async_call_with_reauth(self._client.get_measure)
        return {
            MEASURE_ACCUMULATED_KEY: data.accumulate,
            MEASURE_INSTANT_KEY: data.instant,
        }

    async def _async_fetch_historical_consumption(self) -> list[HistoricalState]:
        return await self._async_fetch_historical(
            self._client.get_historical_consumption
        )

    async def _async_fetch_historical_generation(self) -> list[HistoricalState]:
        return await self._async_fetch_historical(
            self._client.get_historical_generation
        )

    async def _async_fetch_power_demand_peaks(self) -> list[HistoricalState]:
        def historical_power_demand_as_historical_state(
            dai: ideenergy.DemandAtInstant,
        ) -> HistoricalState | None:
            dt = dai.dt.replace(tzinfo=LOCAL_TZ)

            try:
                return HistoricalState(
                    state=dai.value / 1000,
                    timestamp=dt_util.as_timestamp(dt),
                )
            except Exception:
                LOGGER.exception(f"[{self._client}] invalid DemandAtInstant '{dai!r}'")
                return None

        data = await self._async_call_with_reauth(
            self._client.get_historical_power_demand
        )
        hist_states = [
            historical_power_demand_as_historical_state(dai) for dai in data.demands
        ]
        return [hs for hs in hist_states if hs is not None]

    async def _async_fetch_historical(self, afn: Callable) -> list[HistoricalState]:
        end = datetime.now()
        # Start at local midnight: i-DE series are indexed from day start,
        # which is needed to place hours correctly around DST changes.
        start = (end - HISTORICAL_PERIOD_LENGHT).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        data = await self._async_call_with_reauth(afn, start=start, end=end)
        return periods_as_historical_states(data.periods, day0=start)

    #
    # Backfill
    #

    def register_backfill_target(self, entity: Any) -> Callable[[], None]:
        """Register a sensor able to backfill historical consumption."""
        self._backfill_targets.append(entity)
        return lambda: self._backfill_targets.remove(entity)

    @property
    def backfill_targets(self) -> list[Any]:
        return list(self._backfill_targets)

    async def async_fetch_historical_consumption_range(
        self, start: date, end: date
    ) -> list[HistoricalState]:
        """Download hourly consumption for [start, end] in small chunks.

        Requests are sequential with a pause between them to be gentle with
        i-DE. Overlapping hours (chunks get a 1 h safety margin) are deduped.
        """
        await self._async_ensure_logged()

        by_ts: dict[float, HistoricalState] = {}
        chunk_start = datetime.combine(start, time.min)
        range_end = datetime.combine(end + timedelta(days=1), time.min)
        range_end_ts = dt_util.as_timestamp(range_end.replace(tzinfo=LOCAL_TZ))

        while chunk_start < range_end:
            chunk_end = min(chunk_start + BACKFILL_CHUNK, range_end)
            LOGGER.info(
                f"[{self._client}] backfill: requesting {chunk_start:%Y-%m-%d} "
                + f"- {chunk_end:%Y-%m-%d}"
            )
            # +1 h margin: ideenergy filters by naive 'end', which drops the
            # last hour of a 25 h (October) day otherwise.
            data = await self._async_call_with_reauth(
                self._client.get_historical_consumption,
                start=chunk_start,
                end=chunk_end + timedelta(hours=1),
            )
            for hs in periods_as_historical_states(
                data.periods, day0=chunk_start, defer_trailing_zeros=False
            ):
                if hs.timestamp <= range_end_ts:
                    by_ts[hs.timestamp] = hs

            chunk_start = chunk_end
            if chunk_start < range_end:
                await asyncio.sleep(BACKFILL_REQUEST_DELAY.total_seconds())

        return drop_trailing_zeros([by_ts[ts] for ts in sorted(by_ts)])

    #
    # Session handling
    #

    async def _async_call_with_reauth[R](
        self, afn: Callable[..., Awaitable[R]], *args: Any, **kwargs: Any
    ) -> R:
        """Call an API method; on HTTP 401/403 re-login and retry exactly once.

        i-DE silently expires sessions server-side. When that happens every
        request answers 401/403 until a new login is performed.
        """
        try:
            return await afn(*args, **kwargs)
        except ideenergy.RequestFailedError as e:
            if not is_auth_error(e):
                raise

            LOGGER.warning(
                f"[{self._client}] got HTTP {e.response.status}, "
                + "invalidating session and retrying once after re-login"
            )

        self._client._login_ts = None
        await self._async_login()
        return await afn(*args, **kwargs)

    async def _async_ensure_logged(self) -> None:
        if not self._client.is_logged:
            await self._async_login()

    async def _async_login(self) -> None:
        """Log in, translating rejected credentials into ConfigEntryAuthFailed."""
        try:
            await self._client.login()
        except AUTH_LOGIN_ERRORS as e:
            raise ConfigEntryAuthFailed(f"i-DE rejected the credentials: {e}") from e

    #
    # Helpers
    #

    def _state_is_too_recent_with_debug(
        self, *, key: str, max_age: timedelta, label: str
    ) -> bool:
        """Check if max_age timedelta has passed since key was last updated.

        Args:
            key: The state key to check
            max_age: The maximum age timedelta
            label: Debug label for logging

        Returns:
            True if not enough time has passed (too recent), False if enough time has passed
        """
        now_ts = dt_util.as_timestamp(dt_util.now())
        now_dt = dt_util.as_local(dt_util.utc_from_timestamp(now_ts))
        max_age_seconds = max_age.total_seconds()

        try:
            prev_ts = float(self._config_entry_state.data[key])
        except TypeError, ValueError, KeyError:
            LOGGER.debug(f"[{self._client}] {label}: no previous timestamp found")
            return False

        elapsed_seconds = now_ts - prev_ts
        if elapsed_seconds < 0:
            prev_dt = dt_util.as_local(dt_util.utc_from_timestamp(prev_ts))
            skew_seconds = abs(elapsed_seconds)
            LOGGER.warning(
                f"[{self._client}] {label}: stored timestamp is in the future "
                + f"(key={key!r}, now={now_dt}, prev={prev_dt}); "
                + f"clock skew: {skew_seconds:.0f}s; allowing refresh"
            )
            return False

        prev_dt = dt_util.as_local(dt_util.utc_from_timestamp(prev_ts))
        is_too_recent = elapsed_seconds <= max_age_seconds

        if is_too_recent:
            fulfillment_dt = prev_dt + max_age
            remaining_seconds = max_age_seconds - elapsed_seconds

            LOGGER.debug(
                f"[{self._client}] {label}: too recent - "
                f"last check: {prev_dt}, "
                f"required interval: {max_age_seconds:.0f}s, "
                f"remaining time: {remaining_seconds:.0f}s "
                f"(will be ready at {fulfillment_dt})"
            )

        return is_too_recent
