"""Presence of a NetWatch target."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ATTR_IP, ATTR_LATENCY, ATTR_MAC, ATTR_SOURCE
from .entity import NetWatchEntity

if TYPE_CHECKING:
    from . import NetWatchConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NetWatchConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([NetWatchPresence(entry.runtime_data, entry.entry_id)])


class NetWatchPresence(NetWatchEntity, BinarySensorEntity):
    """On when the target answered ICMP within consider_home."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_name = None

    def __init__(self, coordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_presence"

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.available

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Surface the resolution chain, so a stale entry is self-explanatory.

        ip_source is the useful one: it says whether the address came from the
        neighbour table (followed), from configuration (static), or is the last
        one that worked (the device has moved or gone).
        """
        data = self.coordinator.data
        attrs: dict[str, Any] = {
            ATTR_IP: data.ip,
            ATTR_MAC: data.mac,
            ATTR_LATENCY: data.latency_ms,
            ATTR_SOURCE: data.ip_source,
            "last_seen": data.last_seen.isoformat() if data.last_seen else None,
        }
        if data.mac_is_randomised:
            # Locally administered address: the device will rotate it and this
            # target will stop resolving. Better said out loud than debugged.
            attrs["warning"] = "randomised_mac"
        return attrs
