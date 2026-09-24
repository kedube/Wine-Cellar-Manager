"""Wine Cellar Manager integration."""
from __future__ import annotations

from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS
from .services import async_register_services, async_unregister_services
from .storage import async_get_store
from .websocket_api import async_register_websockets


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Wine Cellar Manager integration."""
    hass.data.setdefault(DOMAIN, {})

    if not hass.data[DOMAIN].get("websockets_registered"):
        async_register_websockets(hass)
        hass.data[DOMAIN]["websockets_registered"] = True

    if not hass.data[DOMAIN].get("services_registered"):
        await async_register_services(hass)
        hass.data[DOMAIN]["services_registered"] = True

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Wine Cellar Manager from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    if not hass.data[DOMAIN].get("websockets_registered"):
        async_register_websockets(hass)
        hass.data[DOMAIN]["websockets_registered"] = True

    store = async_get_store(hass)
    await store.async_load()

    hass.data[DOMAIN][entry.entry_id] = {"store": store, "entry": entry}

    if not hass.data[DOMAIN].get("frontend_registered"):
        frontend_dir = hass.config.path("custom_components", DOMAIN, "frontend")
        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(
                    "/wine-cellar-manager-frontend", frontend_dir, False
                )
            ]
        )
        hass.data[DOMAIN]["frontend_registered"] = True

    if "lovelace" in hass.data:
        lovelace = hass.data["lovelace"]
        if hasattr(lovelace, "async_register_custom_card"):
            await lovelace.async_register_custom_card(
                "wine-cellar-card",
                "/wine-cellar-manager-frontend/wine-cellar-card.js"
            )

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok and DOMAIN in hass.data:
        domain_data = hass.data[DOMAIN]
        domain_data.pop(entry.entry_id, None)

        # When the last entry goes away, drop the services and the shared store
        # so a later setup starts from a clean state instead of a stale cache.
        remaining = [
            key
            for key in domain_data
            if key
            not in {
                "store",
                "services_registered",
                "websockets_registered",
                "frontend_registered",
            }
        ]
        if not remaining:
            if domain_data.pop("services_registered", False):
                await async_unregister_services(hass)
            domain_data.pop("store", None)

    return unload_ok
