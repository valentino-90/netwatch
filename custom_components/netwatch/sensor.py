"""Diagnostic sensors: where the target currently lives, and how far away."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import NetWatchEntity

if TYPE_CHECKING:
    from . import NetWatchConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NetWatchConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [
            NetWatchAddressSensor(coordinator, entry.entry_id),
            NetWatchLatencySensor(coordinator, entry.entry_id),
        ]
    )


class NetWatchAddressSensor(NetWatchEntity, SensorEntity):
    """The address the target resolved to on the last cycle."""

    _attr_translation_key = "ip_address"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:ip-network"

    def __init__(self, coordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_ip"

    @property
    def native_value(self) -> str | None:
        return self.coordinator.data.ip


class NetWatchLatencySensor(NetWatchEntity, SensorEntity):
    """Round-trip time of the last successful probe."""

    _attr_translation_key = "latency"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MILLISECONDS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_latency"

    @property
    def native_value(self) -> float | None:
        # None while unreachable, rather than a stale figure that reads as if
        # the device had answered.
        return self.coordinator.data.latency_ms
