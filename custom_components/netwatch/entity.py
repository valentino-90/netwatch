"""Shared entity base for NetWatch."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import NetWatchCoordinator


class NetWatchEntity(CoordinatorEntity[NetWatchCoordinator]):
    """Every entity of a target hangs off one device, named after the target."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: NetWatchCoordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id

    @property
    def device_info(self) -> DeviceInfo:
        """Register the device by MAC when known.

        Declaring the MAC connection lets Home Assistant merge this device with
        the same hardware discovered elsewhere (dhcp, zeroconf), so a followed
        target shows up as one device rather than a duplicate.
        """
        info = DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=self.coordinator.target_name,
            manufacturer="NetWatch",
            model="Tracked target",
        )
        if mac := self.coordinator.mac:
            info["connections"] = {(CONNECTION_NETWORK_MAC, mac)}
        return info
