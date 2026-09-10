"""Neighbour-table backends: resolve MAC <-> IP without spending packets.

The kernel already maintains an ARP/neighbour cache as a side effect of normal
LAN traffic. Reading it costs nothing, which is the whole point of this
integration: nmap_tracker probes 254 hosts to learn what the kernel is happy to
tell us for free.

Two backends are tried in order, and whichever answers is reported through
diagnostics so a broken deployment is obvious rather than silently degraded.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field

_LOGGER = logging.getLogger(__name__)

PROC_NET_ARP = "/proc/net/arp"

BACKEND_PROC = "proc_net_arp"
BACKEND_IP_NEIGH = "ip_neigh"
BACKEND_NONE = "unavailable"

# An entry with flags 0x0 is incomplete: the kernel holds a slot but never
# resolved it. Trusting those invents devices that do not exist.
_ARP_INCOMPLETE_FLAG = "0x0"

_MAC_RE = re.compile(r"^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$")
_NULL_MAC = "00:00:00:00:00:00"
_BROADCAST_MAC = "ff:ff:ff:ff:ff:ff"

# `ip neigh show` emits lines like:
#   192.168.0.10 dev eth0 lladdr aa:bb:cc:dd:ee:ff REACHABLE
_IP_NEIGH_RE = re.compile(
    r"^(?P<ip>\d{1,3}(?:\.\d{1,3}){3})\s+dev\s+\S+\s+lladdr\s+(?P<mac>\S+)\s+(?P<state>\w+)"
)
# FAILED/INCOMPLETE are the neighbour-table equivalent of the 0x0 flag above.
_IP_NEIGH_DEAD_STATES = frozenset({"FAILED", "INCOMPLETE"})


def normalise_mac(mac: str) -> str:
    """Canonical form used as dictionary key: lower case, colon separated."""
    return mac.strip().lower().replace("-", ":")


def is_usable_mac(mac: str) -> bool:
    """Reject the null and broadcast addresses and anything malformed."""
    return bool(_MAC_RE.match(mac)) and mac not in (_NULL_MAC, _BROADCAST_MAC)


def is_randomised_mac(mac: str) -> bool:
    """True for locally administered addresses.

    Phones rotate these per network, so following one is pointless - the
    address is gone the next time the device associates. Worth surfacing so a
    user does not wonder why their tracker never comes back.
    """
    try:
        return bool(int(mac.split(":")[0], 16) & 0b10)
    except (ValueError, IndexError):
        return False


@dataclass(slots=True)
class NeighbourTable:
    """A point-in-time view of the kernel neighbour cache."""

    by_mac: dict[str, str] = field(default_factory=dict)
    by_ip: dict[str, str] = field(default_factory=dict)
    backend: str = BACKEND_NONE

    def ip_for_mac(self, mac: str | None) -> str | None:
        return self.by_mac.get(mac) if mac else None

    def mac_for_ip(self, ip: str | None) -> str | None:
        return self.by_ip.get(ip) if ip else None

    def __len__(self) -> int:
        return len(self.by_mac)


def _index(pairs: list[tuple[str, str]], backend: str) -> NeighbourTable:
    """Build both indexes from (ip, mac) pairs.

    A MAC can hold several addresses (dual stack, a stale lease alongside a
    fresh one). Last writer wins for by_mac, which favours the most recently
    parsed entry; both directions stay consistent because by_ip is total.
    """
    by_mac: dict[str, str] = {}
    by_ip: dict[str, str] = {}
    for ip, mac in pairs:
        by_mac[mac] = ip
        by_ip[ip] = mac
    return NeighbourTable(by_mac=by_mac, by_ip=by_ip, backend=backend)


def _parse_proc_net_arp(raw: str) -> list[tuple[str, str]]:
    """Parse /proc/net/arp, skipping the header row and incomplete entries."""
    pairs: list[tuple[str, str]] = []
    for line in raw.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 4:
            continue
        ip, _hw_type, flags, mac = fields[0], fields[1], fields[2], fields[3]
        if flags == _ARP_INCOMPLETE_FLAG:
            continue
        mac = normalise_mac(mac)
        if is_usable_mac(mac):
            pairs.append((ip, mac))
    return pairs


def _parse_ip_neigh(raw: str) -> list[tuple[str, str]]:
    """Parse `ip neigh show` output."""
    pairs: list[tuple[str, str]] = []
    for line in raw.splitlines():
        match = _IP_NEIGH_RE.match(line.strip())
        if not match:
            continue
        if match.group("state").upper() in _IP_NEIGH_DEAD_STATES:
            continue
        mac = normalise_mac(match.group("mac"))
        if is_usable_mac(mac):
            pairs.append((match.group("ip"), mac))
    return pairs


def _read_proc_net_arp_blocking() -> str | None:
    try:
        with open(PROC_NET_ARP, encoding="utf-8") as handle:
            return handle.read()
    except OSError as err:
        _LOGGER.debug("Cannot read %s: %s", PROC_NET_ARP, err)
        return None


class NeighbourReader:
    """Reads the neighbour cache, remembering which backend worked."""

    def __init__(self, hass) -> None:
        self._hass = hass
        self._backend: str | None = None

    @property
    def backend(self) -> str:
        return self._backend or BACKEND_NONE

    async def async_read(self) -> NeighbourTable:
        """Return the current table, trying each backend until one answers.

        Once a backend is chosen it is kept, so the common path is a single
        file read. An empty result is not a reason to switch: an idle LAN
        legitimately has an empty cache.
        """
        if self._backend in (None, BACKEND_PROC):
            raw = await self._hass.async_add_executor_job(_read_proc_net_arp_blocking)
            if raw is not None:
                self._backend = BACKEND_PROC
                return _index(_parse_proc_net_arp(raw), BACKEND_PROC)

        if self._backend in (None, BACKEND_IP_NEIGH):
            pairs = await self._async_read_ip_neigh()
            if pairs is not None:
                self._backend = BACKEND_IP_NEIGH
                return _index(pairs, BACKEND_IP_NEIGH)

        self._backend = BACKEND_NONE
        return NeighbourTable(backend=BACKEND_NONE)

    async def _async_read_ip_neigh(self) -> list[tuple[str, str]] | None:
        """Fallback for containers without a readable /proc/net/arp."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "ip",
                "neigh",
                "show",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
        except (OSError, FileNotFoundError) as err:
            _LOGGER.debug("`ip neigh` unavailable: %s", err)
            return None
        if proc.returncode != 0:
            return None
        return _parse_ip_neigh(stdout.decode("utf-8", errors="replace"))
