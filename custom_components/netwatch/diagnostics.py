"""Diagnostics: the full picture of what NetWatch is following and how."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant

from .const import DOMAIN

if TYPE_CHECKING:
    from . import NetWatchConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: NetWatchConfigEntry
) -> dict[str, Any]:
    """Dump this target plus the neighbour table it resolves against.

    The table is the thing worth seeing: if `backend` is unavailable or the
    entry count is zero, resolution is not working and every target is falling
    back to its configured address - which is the failure this integration
    exists to remove.
    """
    coordinator = entry.runtime_data
    runtime = hass.data[DOMAIN]
    table = await runtime.cache.async_get()
    data = coordinator.data

    return {
        "target": {
            "name": coordinator.target_name,
            "followed_mac": coordinator.mac,
            "configured_host": entry.data.get("host"),
            "follow_mac": entry.data.get("follow_mac"),
            "options": dict(entry.options),
        },
        "state": {
            "available": data.available,
            "ip": data.ip,
            "ip_source": data.ip_source,
            "latency_ms": data.latency_ms,
            "last_seen": data.last_seen.isoformat() if data.last_seen else None,
            "mac_is_randomised": data.mac_is_randomised,
        },
        "resolution": {
            "backend": table.backend,
            "icmp_privileged": coordinator.privileged,
            "neighbour_entries": len(table),
            "neighbour_table": dict(table.by_mac),
        },
        "sweeper": {
            "sweeps_run": runtime.sweeper.sweep_count,
            "last_sweep": (
                runtime.sweeper.last_sweep.isoformat()
                if runtime.sweeper.last_sweep
                else None
            ),
        },
    }
