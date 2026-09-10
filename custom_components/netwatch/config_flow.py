"""Config and options flow for NetWatch.

One config entry per tracked target, the same shape core's ping integration
uses. It keeps the Devices & Services card readable - every followed device is
its own row - and lets each target carry its own polling interval.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .arp import is_usable_mac, normalise_mac
from .const import (
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
)

CONF_NAME = "name"


def _tuning_schema(defaults: dict[str, Any]) -> dict[Any, Any]:
    """Fields shared by the initial flow and the options flow."""
    return {
        vol.Optional(
            CONF_INTERVAL, default=defaults.get(CONF_INTERVAL, DEFAULT_INTERVAL)
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=5, max=3600, step=1, unit_of_measurement="s", mode="box"
            )
        ),
        vol.Optional(
            CONF_CONSIDER_HOME,
            default=defaults.get(CONF_CONSIDER_HOME, DEFAULT_CONSIDER_HOME),
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0, max=3600, step=1, unit_of_measurement="s", mode="box"
            )
        ),
        vol.Optional(
            CONF_COUNT, default=defaults.get(CONF_COUNT, DEFAULT_COUNT)
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(min=1, max=10, step=1, mode="box")
        ),
        vol.Optional(
            CONF_TIMEOUT, default=defaults.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=1, max=30, step=0.5, unit_of_measurement="s", mode="box"
            )
        ),
        vol.Optional(
            CONF_SUBNET, description={"suggested_value": defaults.get(CONF_SUBNET)}
        ): selector.TextSelector(),
    }


class NetWatchConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add one target."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            host = (user_input.get(CONF_HOST) or "").strip()
            mac = (user_input.get(CONF_MAC) or "").strip()

            if not host and not mac:
                # Without one of the two there is nothing to resolve or ping.
                errors["base"] = "need_host_or_mac"
            elif mac and not is_usable_mac(normalise_mac(mac)):
                errors[CONF_MAC] = "invalid_mac"

            if not errors:
                mac = normalise_mac(mac) if mac else ""
                # Prefer the MAC as identity: it is what survives a DHCP move.
                await self.async_set_unique_id(mac or host.lower())
                self._abort_if_unique_id_configured()

                name = user_input[CONF_NAME].strip()
                return self.async_create_entry(
                    title=name,
                    data={
                        CONF_NAME: name,
                        CONF_HOST: host or None,
                        CONF_MAC: mac or None,
                        CONF_FOLLOW_MAC: user_input.get(
                            CONF_FOLLOW_MAC, DEFAULT_FOLLOW_MAC
                        ),
                    },
                    options={
                        CONF_INTERVAL: int(user_input[CONF_INTERVAL]),
                        CONF_CONSIDER_HOME: int(user_input[CONF_CONSIDER_HOME]),
                        CONF_COUNT: int(user_input[CONF_COUNT]),
                        CONF_TIMEOUT: float(user_input[CONF_TIMEOUT]),
                        CONF_SUBNET: (user_input.get(CONF_SUBNET) or "").strip() or None,
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): selector.TextSelector(),
                vol.Optional(CONF_HOST): selector.TextSelector(),
                vol.Optional(CONF_MAC): selector.TextSelector(),
                vol.Optional(
                    CONF_FOLLOW_MAC, default=DEFAULT_FOLLOW_MAC
                ): selector.BooleanSelector(),
                **_tuning_schema({}),
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> NetWatchOptionsFlow:
        return NetWatchOptionsFlow()


class NetWatchOptionsFlow(OptionsFlow):
    """Retune an existing target without recreating it."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_INTERVAL: int(user_input[CONF_INTERVAL]),
                    CONF_CONSIDER_HOME: int(user_input[CONF_CONSIDER_HOME]),
                    CONF_COUNT: int(user_input[CONF_COUNT]),
                    CONF_TIMEOUT: float(user_input[CONF_TIMEOUT]),
                    CONF_SUBNET: (user_input.get(CONF_SUBNET) or "").strip() or None,
                }
            )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(_tuning_schema(dict(self.config_entry.options))),
        )
