from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant, ServiceCall

from .const import DOMAIN, SERVICE_REBUILD_READY

_LOGGER = logging.getLogger(__name__)

async def async_register_services(hass: HomeAssistant) -> None:
    """Register custom services for Wine Cellar Manager."""

    async def handle_rebuild_ready(call: ServiceCall) -> None:
        store = hass.data.get(DOMAIN, {}).get("store")
        if store is None:
            _LOGGER.warning("Cannot execute service: no configuration entry found.")
            return

        _LOGGER.debug("Wine Cellar Manager: drinking-window data rebuild started.")
        # Re-fire the change event so sensors recompute "ready_to_drink" for today.
        await store.async_save(await store.async_load())

    hass.services.async_register(
        DOMAIN,
        SERVICE_REBUILD_READY,
        handle_rebuild_ready,
    )


async def async_unregister_services(hass: HomeAssistant) -> None:
    """Unregister custom services."""
    if hass.services.has_service(DOMAIN, SERVICE_REBUILD_READY):
        hass.services.async_remove(DOMAIN, SERVICE_REBUILD_READY)
