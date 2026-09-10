"""NetWatch: follow devices by name, resolve through MAC, prove with ICMP."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.helpers import config_validation as cv

from .const import (
    ATTR_TARGET,
    CONF_CONSIDER_HOME,
    CONF_COUNT,
    CONF_FOLLOW_MAC,
    CONF_HOST,
    CONF_INTERVAL,
    CONF_MAC,
    CONF_SUBNET,
    CONF_TIMEOUT,
    DEFAULT_CONSIDER_HOME,
    DEFAULT_COUNT,
    DEFAULT_FOLLOW_MAC,
    DEFAULT_INTERVAL,
    DEFAULT_TIMEOUT,
    DOMAIN,
    SERVICE_PING,
    SERVICE_REFRESH_TABLE,
)
from .coordinator import NeighbourCache, NetWatchCoordinator, Sweeper
from .store import LearnedStore

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]

type NetWatchConfigEntry = ConfigEntry[NetWatchCoordinator]

ATTR_SWEEP = "sweep"
ATTR_SUBNET = "subnet"

SERVICE_PING_SCHEMA = vol.Schema({vol.Optional(ATTR_TARGET): cv.string})
SERVICE_REFRESH_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_SWEEP, default=False): cv.boolean,
        vol.Optional(ATTR_SUBNET): cv.string,
    }
)


@dataclass(slots=True)
class NetWatchRuntime:
    """State shared by every target, so one ARP read serves all of them."""

    cache: NeighbourCache
    sweeper: Sweeper
    store: LearnedStore


def _get_runtime(hass: HomeAssistant) -> NetWatchRuntime:
    if DOMAIN not in hass.data:
        cache = NeighbourCache(hass)
        hass.data[DOMAIN] = NetWatchRuntime(
            cache=cache,
            sweeper=Sweeper(hass, cache),
            store=LearnedStore(hass),
        )
    return hass.data[DOMAIN]


async def async_setup_entry(hass: HomeAssistant, entry: NetWatchConfigEntry) -> bool:
    """Set up one tracked target."""
    runtime = _get_runtime(hass)
    await runtime.store.async_load()
    options = entry.options

    coordinator = NetWatchCoordinator(
        hass,
        name=entry.title,
        entry_id=entry.entry_id,
        cache=runtime.cache,
        sweeper=runtime.sweeper,
        store=runtime.store,
        mac=entry.data.get(CONF_MAC),
        host=entry.data.get(CONF_HOST),
        follow_mac=entry.data.get(CONF_FOLLOW_MAC, DEFAULT_FOLLOW_MAC),
        subnet=options.get(CONF_SUBNET),
        interval=int(options.get(CONF_INTERVAL, DEFAULT_INTERVAL)),
        count=int(options.get(CONF_COUNT, DEFAULT_COUNT)),
        timeout=float(options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)),
        consider_home=int(options.get(CONF_CONSIDER_HOME, DEFAULT_CONSIDER_HOME)),
    )

    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    _async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: NetWatchConfigEntry) -> bool:
    """Unload a target, dropping shared state once the last one goes."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded and len(hass.config_entries.async_entries(DOMAIN)) <= 1:
        hass.data.pop(DOMAIN, None)
        hass.services.async_remove(DOMAIN, SERVICE_PING)
        hass.services.async_remove(DOMAIN, SERVICE_REFRESH_TABLE)
    return unloaded


async def _async_reload_entry(hass: HomeAssistant, entry: NetWatchConfigEntry) -> None:
    """Apply retuned options by rebuilding the coordinator."""
    await hass.config_entries.async_reload(entry.entry_id)


def _async_register_services(hass: HomeAssistant) -> None:
    """Register the on-demand services, once per Home Assistant run."""
    if hass.services.has_service(DOMAIN, SERVICE_PING):
        return

    async def async_ping_service(call: ServiceCall) -> ServiceResponse:
        """Check one target, or all of them, right now.

        This is the 'follow presence whenever I want' path: it bypasses the
        polling interval and answers with the result, so a script can branch
        on it without waiting for the next cycle.
        """
        wanted = call.data.get(ATTR_TARGET)
        results: dict[str, Any] = {}

        for entry in hass.config_entries.async_entries(DOMAIN):
            coordinator: NetWatchCoordinator | None = getattr(
                entry, "runtime_data", None
            )
            if coordinator is None:
                continue
            if wanted and wanted.lower() not in (
                entry.title.lower(),
                (coordinator.mac or "").lower(),
            ):
                continue

            await coordinator.async_refresh()
            data = coordinator.data
            results[entry.title] = {
                "available": data.available,
                "ip": data.ip,
                "mac": data.mac,
                "latency_ms": data.latency_ms,
                "ip_source": data.ip_source,
                "last_seen": data.last_seen.isoformat() if data.last_seen else None,
            }

        if wanted and not results:
            _LOGGER.warning("NetWatch: no target named '%s'", wanted)
        return {"targets": results}

    async def async_refresh_table_service(call: ServiceCall) -> ServiceResponse:
        """Re-read the neighbour table, optionally forcing a sweep first."""
        runtime = _get_runtime(hass)
        if call.data.get(ATTR_SWEEP):
            subnet = call.data.get(ATTR_SUBNET)
            if not subnet:
                _LOGGER.warning("NetWatch: sweep requested without a subnet")
            else:
                await runtime.sweeper.async_sweep(subnet, None, forced=True)

        table = await runtime.cache.async_get(force=True)
        return {
            "backend": table.backend,
            "entries": len(table),
            "by_mac": dict(table.by_mac),
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_PING,
        async_ping_service,
        schema=SERVICE_PING_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REFRESH_TABLE,
        async_refresh_table_service,
        schema=SERVICE_REFRESH_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Drop what was learned about a target the user deleted."""
    runtime = _get_runtime(hass)
    await runtime.store.async_load()
    runtime.store.forget(entry.entry_id)
