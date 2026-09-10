"""Presence engine: resolve a target to an address, then prove it is alive.

Design note. Every target shares one neighbour-table read per cycle (see
NeighbourCache) and spends exactly one ICMP exchange on itself. The expensive
operation - sweeping a subnet - happens only when a followed MAC disappears
from the cache, and is rate limited by Sweeper.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from icmplib import SocketPermissionError, async_multiping, async_ping, ping

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .arp import NeighbourReader, NeighbourTable, is_randomised_mac, normalise_mac
from .const import (
    DEFAULT_CONSIDER_HOME,
    DEFAULT_COUNT,
    DEFAULT_TIMEOUT,
    SWEEP_CONCURRENCY,
    SWEEP_MAX_BACKOFF,
    SWEEP_MIN_BACKOFF,
)
from .store import LearnedStore

_LOGGER = logging.getLogger(__name__)

# How long one neighbour-table read is reused across targets. Short enough that
# a DHCP change is picked up on the next cycle, long enough that ten targets
# polling together cause one read rather than ten.
TABLE_TTL = timedelta(seconds=10)

SOURCE_NEIGHBOUR = "neighbour_table"
SOURCE_STATIC = "static_host"
SOURCE_LAST_KNOWN = "last_known"
SOURCE_UNRESOLVED = "unresolved"


def _detect_privilege_blocking() -> bool | None:
    """Decide how icmplib may open sockets here.

    True  -> raw sockets available (CAP_NET_RAW)
    False -> unprivileged ICMP allowed (net.ipv4.ping_group_range)
    None  -> neither; ICMP is unavailable in this container

    Mirrors what Home Assistant core does for its own ping integration, so a
    deployment that can run `ping` can run this.
    """
    try:
        ping("127.0.0.1", count=0, timeout=0, privileged=True)
    except SocketPermissionError:
        try:
            ping("127.0.0.1", count=0, timeout=0, privileged=False)
        except SocketPermissionError:
            return None
        return False
    return True


@dataclass(slots=True)
class TargetData:
    """What one target looks like after a refresh cycle."""

    available: bool = False
    ip: str | None = None
    mac: str | None = None
    latency_ms: float | None = None
    ip_source: str = SOURCE_UNRESOLVED
    last_seen: datetime | None = None
    mac_is_randomised: bool = False


class NeighbourCache:
    """Serves one neighbour-table read to every target that asks."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._reader = NeighbourReader(hass)
        self._table = NeighbourTable()
        self._read_at: datetime | None = None
        self._lock = asyncio.Lock()

    @property
    def table(self) -> NeighbourTable:
        return self._table

    @property
    def backend(self) -> str:
        return self._reader.backend

    async def async_get(self, *, force: bool = False) -> NeighbourTable:
        async with self._lock:
            now = dt_util.utcnow()
            if force or self._read_at is None or now - self._read_at > TABLE_TTL:
                self._table = await self._reader.async_read()
                self._read_at = now
            return self._table


class Sweeper:
    """Rate-limited subnet sweep that repopulates the neighbour cache.

    Pinging a subnet makes the kernel ARP for each address, which is what
    actually refills the table. Doing that on a schedule is what makes
    nmap_tracker expensive, so here it is strictly a recovery path.
    """

    def __init__(self, hass: HomeAssistant, cache: NeighbourCache) -> None:
        self._hass = hass
        self._cache = cache
        self._lock = asyncio.Lock()
        self._next_allowed: datetime | None = None
        self._backoff = SWEEP_MIN_BACKOFF
        self.last_sweep: datetime | None = None
        self.sweep_count = 0

    def _note_outcome(self, *, recovered: bool) -> None:
        """Back off while sweeps keep failing; reset the moment one works."""
        if recovered:
            self._backoff = SWEEP_MIN_BACKOFF
        else:
            self._backoff = min(self._backoff * 2, SWEEP_MAX_BACKOFF)
        self._next_allowed = dt_util.utcnow() + timedelta(seconds=self._backoff)

    async def async_sweep(
        self, subnet: str, privileged: bool | None, *, forced: bool = False
    ) -> NeighbourTable:
        """Sweep `subnet` and return the refreshed table."""
        async with self._lock:
            now = dt_util.utcnow()
            if not forced and self._next_allowed and now < self._next_allowed:
                return self._cache.table

            try:
                network = ipaddress.ip_network(subnet, strict=False)
            except ValueError:
                _LOGGER.warning("Invalid subnet %s, skipping sweep", subnet)
                return self._cache.table

            hosts = [str(host) for host in network.hosts()]
            if not hosts:
                return self._cache.table

            _LOGGER.debug("Sweeping %s (%d addresses)", subnet, len(hosts))
            before = len(self._cache.table)
            try:
                await async_multiping(
                    hosts,
                    count=1,
                    timeout=1,
                    concurrent_tasks=SWEEP_CONCURRENCY,
                    privileged=bool(privileged),
                )
            except (SocketPermissionError, OSError) as err:
                _LOGGER.debug("Sweep of %s failed: %s", subnet, err)

            table = await self._cache.async_get(force=True)
            self.last_sweep = dt_util.utcnow()
            self.sweep_count += 1
            self._note_outcome(recovered=len(table) > before)
            return table


class NetWatchCoordinator(DataUpdateCoordinator[TargetData]):
    """Tracks one target: name -> MAC -> current IP -> ICMP."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        name: str,
        entry_id: str,
        cache: NeighbourCache,
        sweeper: Sweeper,
        store: LearnedStore,
        mac: str | None,
        host: str | None,
        follow_mac: bool,
        subnet: str | None,
        interval: int,
        count: int = DEFAULT_COUNT,
        timeout: float = DEFAULT_TIMEOUT,
        consider_home: int = DEFAULT_CONSIDER_HOME,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"NetWatch {name}",
            update_interval=timedelta(seconds=interval),
        )
        self.target_name = name
        self._entry_id = entry_id
        self._cache = cache
        self._sweeper = sweeper
        self._store = store
        self._follow_mac = follow_mac
        self._subnet = subnet
        self._count = count
        self._timeout = timeout
        self._consider_home = consider_home
        self._last_seen: datetime | None = None
        self.privileged: bool | None = None
        self._icmp_warned = False

        # An explicitly configured MAC always wins. Failing that, recover what
        # was learned before the last restart: without it, a target seeded by
        # IP whose device moved while Home Assistant was down would keep
        # pinging the stale address with no path back.
        learned = store.get(entry_id)
        self._mac = normalise_mac(mac) if mac else learned.get("mac")
        self._host = host
        self._last_ip: str | None = learned.get("ip") or host

    @property
    def mac(self) -> str | None:
        """The followed MAC, learned on first contact when seeded by IP."""
        return self._mac

    def _effective_subnet(self, ip: str | None) -> str | None:
        """Configured subnet, else the /24 around the last known address."""
        if self._subnet:
            return self._subnet
        candidate = ip or self._last_ip
        if not candidate:
            return None
        try:
            return str(ipaddress.ip_network(f"{candidate}/24", strict=False))
        except ValueError:
            return None

    def _resolve(self, table: NeighbourTable) -> tuple[str | None, str]:
        """Turn the target into an address to ping.

        Following the MAC takes priority: that is the whole point, an address
        from the neighbour table beats whatever was configured, because the
        configured one is what goes stale when DHCP moves the device.
        """
        if self._mac:
            if (ip := table.ip_for_mac(self._mac)) is not None:
                return ip, SOURCE_NEIGHBOUR
            if self._last_ip:
                return self._last_ip, SOURCE_LAST_KNOWN
            return None, SOURCE_UNRESOLVED

        if self._host:
            return self._host, SOURCE_STATIC
        return None, SOURCE_UNRESOLVED

    def _learn_mac(self, table: NeighbourTable, ip: str | None) -> None:
        """Seeded by IP: adopt the MAC found at that address, once.

        This is the bootstrap that spares the user from looking a MAC up by
        hand. From here on the target is followed by hardware address.
        """
        if self._mac or not self._follow_mac or not ip:
            return
        if (mac := table.mac_for_ip(ip)) and not is_randomised_mac(mac):
            self._mac = mac
            self._store.remember(self._entry_id, mac=mac, ip=ip)
            _LOGGER.info(
                "NetWatch '%s' now following %s (learned at %s)",
                self.target_name,
                mac,
                ip,
            )

    async def _async_ping(self, ip: str) -> float | None:
        """Round-trip time in ms, or None when the address does not answer."""
        try:
            host = await async_ping(
                ip,
                count=self._count,
                timeout=self._timeout,
                privileged=bool(self.privileged),
            )
        except (SocketPermissionError, OSError) as err:
            _LOGGER.debug("Ping to %s failed: %s", ip, err)
            return None
        return round(host.avg_rtt, 2) if host.is_alive else None

    async def _async_update_data(self) -> TargetData:
        if self.privileged is None:
            self.privileged = await self.hass.async_add_executor_job(
                _detect_privilege_blocking
            )
            if self.privileged is None and not self._icmp_warned:
                # Silence here would look exactly like "every device is off",
                # which is the most misleading failure this can have.
                self._icmp_warned = True
                _LOGGER.error(
                    "NetWatch cannot open ICMP sockets: no raw-socket capability "
                    "and unprivileged ping is disabled. Every target will report "
                    "unreachable until this is resolved"
                )

        table = await self._cache.async_get()
        ip, source = self._resolve(table)
        self._learn_mac(table, ip)

        latency = await self._async_ping(ip) if ip else None

        # A followed MAC that answers nothing may simply have moved. One
        # sweep repopulates the table; the backoff keeps this from becoming
        # the scheduled scan we set out to eliminate.
        if latency is None and self._mac and (subnet := self._effective_subnet(ip)):
            table = await self._sweeper.async_sweep(subnet, self.privileged)
            moved_ip, moved_source = self._resolve(table)
            if moved_ip and moved_ip != ip:
                _LOGGER.debug(
                    "NetWatch '%s' moved %s -> %s", self.target_name, ip, moved_ip
                )
                ip, source = moved_ip, moved_source
                latency = await self._async_ping(ip)

        now = dt_util.utcnow()
        if latency is not None:
            self._last_seen = now
            self._last_ip = ip
            # Survives a restart, so recovery starts from an address that
            # actually worked rather than from the original seed.
            self._store.remember(self._entry_id, ip=ip)
            available = True
        else:
            # consider_home rides out WiFi power-save, where a device sleeps
            # through a probe without having gone anywhere.
            available = bool(
                self._last_seen
                and (now - self._last_seen).total_seconds() < self._consider_home
            )

        return TargetData(
            available=available,
            ip=ip,
            mac=self._mac,
            latency_ms=latency,
            ip_source=source if ip else SOURCE_UNRESOLVED,
            last_seen=self._last_seen,
            mac_is_randomised=bool(self._mac and is_randomised_mac(self._mac)),
        )
