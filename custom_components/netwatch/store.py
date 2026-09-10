"""Persistence for what NetWatch has learned about a target.

A MAC learned from an IP seed must outlive a restart. Otherwise the sequence
"Home Assistant restarts while the device is on a new lease" leaves the target
pinging a stale address forever, with no way back - the seed is wrong and the
MAC that would have corrected it was only ever in memory.

Kept in its own Store rather than in the config entry on purpose: writing to
the entry fires its update listener, which reloads it, which re-learns the MAC,
which writes to the entry again.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STORAGE_KEY, STORAGE_VERSION

_LOGGER = logging.getLogger(__name__)

# Learning is rare (a MAC once, an address on the odd lease change), so a lazy
# write keeps restarts from being a burst of disk I/O on an SD card.
SAVE_DELAY = 30


class LearnedStore:
    """Remembers the MAC and last working address of each target."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._data: dict[str, dict[str, Any]] = {}
        self._loaded = False

    async def async_load(self) -> None:
        if self._loaded:
            return
        self._data = await self._store.async_load() or {}
        self._loaded = True

    def get(self, entry_id: str) -> dict[str, Any]:
        return self._data.get(entry_id, {})

    def remember(
        self, entry_id: str, *, mac: str | None = None, ip: str | None = None
    ) -> None:
        """Record a learned fact, saving only when something actually changed."""
        current = self._data.setdefault(entry_id, {})
        changed = False
        if mac and current.get("mac") != mac:
            current["mac"] = mac
            changed = True
        if ip and current.get("ip") != ip:
            current["ip"] = ip
            changed = True
        if changed:
            self._store.async_delay_save(lambda: self._data, SAVE_DELAY)

    def forget(self, entry_id: str) -> None:
        """Drop a removed target so the store does not accumulate orphans."""
        if self._data.pop(entry_id, None) is not None:
            self._store.async_delay_save(lambda: self._data, SAVE_DELAY)
