# NetWatch

Follow devices by name, resolve them through their MAC address, and prove they
are alive with ICMP.

Home Assistant can already scan a subnet (`nmap_tracker`) or ping a fixed
address (`ping`), but neither does the thing that actually matters on a home
network: **follow a device when DHCP moves it**. A ping target with a hardcoded
IP fails silently the day the lease changes, and a subnet scan learns the new
address only by probing all 254 hosts on a timer.

NetWatch does both jobs in one integration, and cheaply.

## How it works

The kernel already maintains an ARP/neighbour cache as a side effect of normal
LAN traffic. Reading it costs nothing, so every refresh is:

1. read the neighbour table — **0 packets**, shared by every target
2. resolve each target's MAC to its current address
3. send **one ICMP exchange** per target

Sweeping the subnet — the expensive operation — happens only when a followed
MAC disappears from the table, and is rate limited with a backoff that doubles
up to 30 minutes and resets as soon as it works.

For three targets, that is roughly 8,600 probes a day instead of the ~183,000 a
120-second `/24` scan spends, while detecting changes four times faster.

## Setup

Add a target from **Settings → Devices & Services → Add integration →
NetWatch**. Each target is its own entry, so each carries its own interval.

You can seed a target either way:

- **By MAC** — followed immediately, wherever it lives.
- **By IP or hostname**, with *Follow MAC* enabled — the MAC is learned on first
  contact, persisted, and used from then on. You never have to look one up.

Leave *Subnet* empty and the `/24` around the last known address is used.

Targets outside your LAN (`1.1.1.1`, a hostname) work too — turn *Follow MAC*
off, since they have no local hardware address.

## Entities

Each target produces:

| Entity | Meaning |
|---|---|
| `binary_sensor.<name>` | Connectivity — on while reachable within `consider_home` |
| `sensor.<name>_ip` | Address it currently resolves to |
| `sensor.<name>_latency` | Round-trip time of the last successful probe |

The binary sensor carries `ip`, `mac`, `latency_ms`, `last_seen` and
`ip_source` as attributes. `ip_source` is the useful one — it says whether the
address came from the neighbour table (followed), from configuration (static),
or is the last one that worked (the device has moved or gone).

That makes templates like this follow the device on their own:

```jinja
{{ state_attr('binary_sensor.ranger_cam', 'ip') }}
```

## Services

| Service | Purpose |
|---|---|
| `netwatch.ping` | Check one target, or all, immediately and return the result |
| `netwatch.refresh_table` | Re-read the MAC-to-IP table and return it |

Both return response data, so a script can branch on the answer without waiting
for the next poll.

## Tuning

`consider_home` is a grace period before a silent target is reported away. Raise
it for Wi-Fi devices that sleep — a power-saving plug or camera can miss a probe
without having gone anywhere. Raising `count` helps for the same reason.

## Requirements

NetWatch needs to see the host's neighbour table and to open ICMP sockets.
Home Assistant OS and Supervised installs run the core container with host
networking, which satisfies both. Download the diagnostics from a target to
check: `resolution.backend` should read `proc_net_arp` (or `ip_neigh`) with a
non-zero `neighbour_entries`. If it says `unavailable`, resolution is not
working and every target is falling back to its configured address.

Locally administered ("randomised") MAC addresses are detected and not adopted.
Phones rotate them per network, so following one is pointless — the entity says
so in a `warning` attribute rather than leaving you to debug it.

## Installation

Via HACS, as a custom repository: **HACS → Integrations → ⋮ → Custom
repositories**, add `https://github.com/valentino-90/netwatch` with category
*Integration*, install, then restart Home Assistant.

## License

MIT
