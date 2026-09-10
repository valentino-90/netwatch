"""Constants for the NetWatch integration."""

from __future__ import annotations

DOMAIN = "netwatch"

CONF_TARGETS = "targets"
CONF_MAC = "mac"
CONF_HOST = "host"
CONF_FOLLOW_MAC = "follow_mac"
CONF_SUBNET = "subnet"
CONF_INTERVAL = "interval"
CONF_COUNT = "count"
CONF_TIMEOUT = "timeout"
CONF_CONSIDER_HOME = "consider_home"

DEFAULT_INTERVAL = 30
DEFAULT_COUNT = 2
DEFAULT_TIMEOUT = 2.0
DEFAULT_CONSIDER_HOME = 90
DEFAULT_FOLLOW_MAC = True

# A sweep is the only expensive thing this integration does, so it is rate
# limited: after a failed sweep the wait doubles up to SWEEP_MAX_BACKOFF, and
# resets as soon as every target resolves again.
SWEEP_MIN_BACKOFF = 120
SWEEP_MAX_BACKOFF = 1800
SWEEP_CONCURRENCY = 48

SERVICE_PING = "ping"
SERVICE_REFRESH_TABLE = "refresh_table"

ATTR_TARGET = "target"
ATTR_IP = "ip"
ATTR_MAC = "mac"
ATTR_LATENCY = "latency_ms"
ATTR_BACKEND = "backend"
ATTR_SOURCE = "ip_source"

STORAGE_KEY = "netwatch_learned_macs"
STORAGE_VERSION = 1
