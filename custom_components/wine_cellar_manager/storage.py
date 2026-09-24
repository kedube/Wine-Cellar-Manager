"""Storage layer for Wine Cellar Manager."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import uuid
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import (
    DOMAIN,
    EVENT_DATA_CHANGED,
    LANE_BACK,
    LANE_FRONT,
    LAYOUT_SINGLE,
    LAYOUT_STAGGERED,
    STORAGE_KEY,
    STORAGE_VERSION,
    WINE_TYPES,
)

_LOGGER = logging.getLogger(__name__)


def _utcnow() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def _remove_file_if_exists(path: str) -> None:
    """Delete a file, ignoring the case where it is already gone.

    Callers must pass a path already validated by ``_normalize_local_path``.
    """
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except OSError as err:
        _LOGGER.warning("Could not delete image file %s: %r", path, err)


ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

# Generous enough for a phone photo, small enough to bound memory and disk.
MAX_IMAGE_BYTES = 12 * 1024 * 1024


def _safe_external_url(value: Any) -> str | None:
    """Return the URL only when it is a plain http(s) link, else None.

    Values such as ``javascript:`` or ``data:`` URLs are rejected so they can
    never be rendered into an href by the frontend card.
    """
    text = str(value or "").strip()
    if not text:
        return None
    if not text.lower().startswith(("http://", "https://")):
        _LOGGER.warning("Rejected non-http(s) URL: %s", text[:80])
        return None
    return text


def _detect_image_mime(data: bytes) -> str | None:
    """Return the image MIME type implied by the file's magic bytes, or None."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _default_data() -> dict[str, Any]:
    """Return default storage structure."""
    return {
        "version": STORAGE_VERSION,
        "cellars": [],
        "bottles": [],
        "consumed_bottles": [],
    }


def async_get_store(hass: HomeAssistant) -> WineCellarStore:
    """Return the domain storage helper."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    store = domain_data.get("store")
    if store is None:
        store = WineCellarStore(hass)
        domain_data["store"] = store
    return store


class WineCellarStore:
    """Persistent storage helper for wine cellar data."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize store."""
        self.hass = hass
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        # Authoritative in-memory copy. The on-disk JSON is only re-read once,
        # on first access; every later read is served from here.
        self._data: dict[str, Any] | None = None
        # Serializes read-modify-write sequences so two concurrent mutations
        # cannot each load, edit and save on top of one another.
        self._lock = asyncio.Lock()

    async def _async_ensure_loaded(self) -> dict[str, Any]:
        """Populate the in-memory cache from disk on first use."""
        if self._data is not None:
            return self._data

        try:
            data = await self._store.async_load()
        except Exception as err:
            _LOGGER.exception("Store load failed: %r", err)
            data = None

        if not isinstance(data, dict):
            self._data = _default_data()
            return self._data

        self._data = self._normalize_data(self._migrate_if_needed(data))
        return self._data

    async def async_load(self) -> dict[str, Any]:
        """Return a private copy of the stored data, safe for the caller to mutate."""
        data = await self._async_ensure_loaded()
        return deepcopy(data)

    async def async_save(self, data: dict[str, Any]) -> None:
        """Persist data and refresh the in-memory cache."""
        payload = self._normalize_data(data)
        self._data = payload
        await self._store.async_save(payload)
        # Hand listeners the already-computed payload so sensors need not reload.
        self.hass.bus.async_fire(EVENT_DATA_CHANGED, {"data": payload})

    async def async_get_cached(self) -> dict[str, Any]:
        """Return the shared cached data. Callers must not mutate the result."""
        return await self._async_ensure_loaded()

    async def async_export_cached(self) -> dict[str, Any]:
        """Return an independent, enriched copy of the cached data."""
        return self.async_export(await self._async_ensure_loaded())

    def async_export(self, data: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return normalized data for sensors and websocket consumers.

        The input is already normalized by ``async_load``/``async_save``, so this
        only deep-copies it once and layers the derived fields on top.
        """
        payload = deepcopy(data) if data is not None else _default_data()
        payload.setdefault("bottles", [])
        payload.setdefault("consumed_bottles", [])
        payload.setdefault("cellars", [])

        current_year = datetime.now().year

        for bottle in payload["bottles"]:
            start = bottle.get("aging_start_year")
            end = bottle.get("aging_end_year")
            bottle["ready_to_drink"] = bool(start is not None and end is not None and start <= current_year <= end)
            bottle["past_peak"] = bool(end is not None and current_year > end)
            bottle["display_origin"] = self._compute_display_origin(bottle)

        for bottle in payload["consumed_bottles"]:
            start = bottle.get("aging_start_year")
            end = bottle.get("aging_end_year")
            bottle["ready_to_drink"] = bool(start is not None and end is not None and start <= current_year <= end)
            bottle["past_peak"] = bool(end is not None and current_year > end)
            bottle["display_origin"] = self._compute_display_origin(bottle)

        return payload

    def _normalize_data(self, data: dict[str, Any]) -> dict[str, Any]:
        result = _default_data()

        raw_cellars = data.get("cellars", [])
        raw_bottles = data.get("bottles", [])
        raw_consumed = data.get("consumed_bottles", [])

        if isinstance(raw_cellars, list):
            result["cellars"] = [self._normalize_cellar(item, index) for index, item in enumerate(raw_cellars) if isinstance(item, dict)]
        if isinstance(raw_bottles, list):
            result["bottles"] = [self._normalize_bottle(item) for item in raw_bottles if isinstance(item, dict)]
        if isinstance(raw_consumed, list):
            result["consumed_bottles"] = [self._normalize_bottle(item) for item in raw_consumed if isinstance(item, dict)]

        return result

    def _migrate_if_needed(self, data: dict[str, Any]) -> dict[str, Any]:
        version = int(data.get("version", 1) or 1)
        if version >= 2:
            return data

        result = {
            "version": 2,
            "cellars": [],
            "bottles": [],
            "consumed_bottles": [],
        }

        raw_cellars = data.get("cellars", [])
        if isinstance(raw_cellars, list):
            for idx, item in enumerate(raw_cellars):
                if not isinstance(item, dict):
                    continue
                cellar_id = str(item.get("id") or uuid.uuid4().hex)
                rows = int(item.get("rows", 0) or 0)
                bottles_per_row = int(item.get("bottles_per_row", 0) or 0)
                shelves = []
                for row_index in range(max(rows, 0)):
                    shelves.append(
                        {
                            "id": f"{cellar_id}_shelf_{row_index + 1}",
                            "name": f"Shelf {row_index + 1}",
                            "display_order": row_index,
                            "capacity_front": max(bottles_per_row, 1),
                            "capacity_back": 0,
                            "layout_mode": LAYOUT_SINGLE,
                        }
                    )

                result["cellars"].append(
                    {
                        "id": cellar_id,
                        "name": str(item.get("name") or ""),
                        "display_order": int(item.get("display_order", idx) or idx),
                        "shelves": shelves,
                    }
                )

        def migrate_bottle(item: dict[str, Any]) -> dict[str, Any]:
            migrated = dict(item)
            if not migrated.get("shelf_id"):
                cellar_id = str(migrated.get("cellar_id") or "")
                row = max(1, int(migrated.get("row", 1) or 1))
                migrated["shelf_id"] = f"{cellar_id}_shelf_{row}"
            migrated["lane"] = LANE_FRONT
            return self._normalize_bottle(migrated)

        raw_bottles = data.get("bottles", [])
        if isinstance(raw_bottles, list):
            result["bottles"] = [migrate_bottle(item) for item in raw_bottles if isinstance(item, dict)]

        raw_consumed = data.get("consumed_bottles", [])
        if isinstance(raw_consumed, list):
            result["consumed_bottles"] = [migrate_bottle(item) for item in raw_consumed if isinstance(item, dict)]

        return result

    def _normalize_cellar(self, item: dict[str, Any], index: int) -> dict[str, Any]:
        cellar_id = str(item.get("id") or uuid.uuid4().hex)
        raw_shelves = item.get("shelves", [])
        shelves = []
        if isinstance(raw_shelves, list):
            for shelf_index, shelf in enumerate(raw_shelves):
                if not isinstance(shelf, dict):
                    continue
                shelves.append(self._normalize_shelf(shelf, shelf_index))

        return {
            "id": cellar_id,
            "name": str(item.get("name") or ""),
            "display_order": int(item.get("display_order", index) or index),
            "shelves": shelves,
            "bg_color": str(item.get("bg_color") or ""),
        }

    def _normalize_shelf(self, item: dict[str, Any], index: int) -> dict[str, Any]:
        capacity_front = int(item.get("capacity_front", 1) or 1)
        capacity_back = int(item.get("capacity_back", 0) or 0)
        if capacity_front < 1:
            capacity_front = 1
        if capacity_back < 0:
            capacity_back = 0

        layout_mode = str(item.get("layout_mode") or (LAYOUT_STAGGERED if capacity_back > 0 else LAYOUT_SINGLE))
        if layout_mode not in {LAYOUT_SINGLE, LAYOUT_STAGGERED}:
            layout_mode = LAYOUT_STAGGERED if capacity_back > 0 else LAYOUT_SINGLE

        if capacity_back == 0:
            layout_mode = LAYOUT_SINGLE
        elif layout_mode == LAYOUT_SINGLE:
            layout_mode = LAYOUT_STAGGERED

        return {
            "id": str(item.get("id") or uuid.uuid4().hex),
            "name": str(item.get("name") or f"Shelf {index + 1}"),
            "display_order": int(item.get("display_order", index) or index),
            "capacity_front": capacity_front,
            "capacity_back": capacity_back,
            "layout_mode": layout_mode,
        }

    def _normalize_bottle(self, item: dict[str, Any]) -> dict[str, Any]:
        wine_type = str(item.get("wine_type") or "other")
        if wine_type not in WINE_TYPES:
            wine_type = "other"

        lane = str(item.get("lane") or LANE_FRONT)
        if lane not in {LANE_FRONT, LANE_BACK}:
            lane = LANE_FRONT

        rating = self._safe_int(item.get("rating"))
        if rating is not None:
            rating = max(0, min(5, rating))

        return {
            "id": str(item.get("id") or uuid.uuid4().hex),
            "cellar_id": str(item.get("cellar_id") or ""),
            "shelf_id": str(item.get("shelf_id") or ""),
            "lane": lane,
            "position": max(1, int(item.get("position", 1) or 1)),
            "wine_name": str(item.get("wine_name") or ""),
            "producer": str(item.get("producer") or ""),
            "region": str(item.get("region") or ""),
            "country": str(item.get("country") or ""),
            "varietal": str(item.get("varietal") or ""),
            "vintage": self._safe_int(item.get("vintage")),
            "wine_year": self._safe_int(item.get("wine_year")),
            "wine_type": wine_type,
            "price": self._safe_float(item.get("price")),
            "image_path": str(item.get("image_path") or ""),
            "barcode": str(item.get("barcode") or ""),
            "saq_url": _safe_external_url(item.get("saq_url")),
            "aging_start_year": self._safe_int(item.get("aging_start_year")),
            "aging_end_year": self._safe_int(item.get("aging_end_year")),
            "rating": rating,
            "notes": str(item.get("notes") or ""),
            "created_at": str(item.get("created_at") or _utcnow()),
            "updated_at": str(item.get("updated_at") or _utcnow()),
            "consumed_at": str(item.get("consumed_at") or ""),
            "serving_temp": self._safe_float(item.get("serving_temp")),
            "alcohol_pct": self._safe_float(item.get("alcohol_pct")),
            "original_bottle_id": str(item.get("original_bottle_id") or ""),
        }

    def _safe_int(self, value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _safe_float(self, value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _compute_display_origin(self, bottle: dict[str, Any]) -> str:
        country = str(bottle.get("country") or "").strip().lower()
        region = str(bottle.get("region") or "").strip()
        varietal = str(bottle.get("varietal") or "").strip()

        if country == "france" and region:
            return region
        if varietal:
            return varietal
        if region:
            return region
        return ""

    def _find_cellar(self, data: dict[str, Any], cellar_id: str) -> dict[str, Any] | None:
        for cellar in data["cellars"]:
            if cellar.get("id") == cellar_id:
                return cellar
        return None

    def _find_shelf(self, data: dict[str, Any], cellar_id: str, shelf_id: str) -> dict[str, Any] | None:
        cellar = self._find_cellar(data, cellar_id)
        if not cellar:
            return None
        for shelf in cellar.get("shelves", []):
            if shelf.get("id") == shelf_id:
                return shelf
        return None

    def _validate_slot_available(
        self,
        data: dict[str, Any],
        *,
        bottle_id: str | None,
        cellar_id: str,
        shelf_id: str,
        lane: str,
        position: int,
    ) -> None:
        shelf = self._find_shelf(data, cellar_id, shelf_id)
        if not shelf:
            raise ValueError("Shelf not found")

        if lane == LANE_FRONT:
            if position < 1 or position > int(shelf.get("capacity_front", 0) or 0):
                raise ValueError("Front position is out of range")
        elif lane == LANE_BACK:
            if int(shelf.get("capacity_back", 0) or 0) < 1:
                raise ValueError("This shelf has no back lane")
            if position < 1 or position > int(shelf.get("capacity_back", 0) or 0):
                raise ValueError("Back position is out of range")
        else:
            raise ValueError("Invalid lane")

        for bottle in data["bottles"]:
            if bottle_id and bottle.get("id") == bottle_id:
                continue
            if (
                bottle.get("cellar_id") == cellar_id
                and bottle.get("shelf_id") == shelf_id
                and bottle.get("lane") == lane
                and int(bottle.get("position", 0) or 0) == int(position)
            ):
                raise ValueError("Target slot is already occupied")

    def _labels_base_dir(self) -> Path:
        """Return the only directory this integration may read or delete images in."""
        return Path(self.hass.config.path("www", "wine_labels")).resolve()

    def _normalize_local_path(self, image_path: str) -> str:
        """Resolve an HA-style local path to a filesystem path inside the labels dir.

        Returns "" for anything that escapes the labels directory, so that callers
        can never be tricked into touching arbitrary files via "..".
        """
        value = str(image_path or "").strip()
        if not value:
            return ""

        www_root = Path(self.hass.config.path("www"))

        if value.startswith("/local/"):
            candidate = www_root / value[len("/local/"):]
        elif value.startswith("local/"):
            candidate = www_root / value[len("local/"):]
        elif value.startswith("/www/"):
            candidate = Path(self.hass.config.path()) / value[len("/"):]
        elif value.startswith("www/"):
            candidate = Path(self.hass.config.path()) / value
        elif value.startswith("/config/www/"):
            candidate = Path(value)
        else:
            return ""

        try:
            resolved = candidate.resolve()
            base_dir = self._labels_base_dir()
        except (OSError, ValueError):
            return ""

        # Reject traversal: the path must live inside <config>/www/wine_labels.
        if resolved != base_dir and base_dir not in resolved.parents:
            _LOGGER.warning(
                "Rejected image path outside the wine_labels directory: %s", value
            )
            return ""

        return str(resolved)

    def _sha256(self, path: Path) -> str:
        """Hash a file with SHA-256."""
        try:
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(65536), b""):
                    digest.update(chunk)
            return digest.hexdigest()
        except Exception as err:
            _LOGGER.warning("Could not hash %s: %r", path, err)
            return ""

    def _iter_all_bottles(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        return list(data.get("bottles", [])) + list(data.get("consumed_bottles", []))

    def _is_image_referenced_elsewhere(
        self,
        data: dict[str, Any],
        *,
        image_path: str,
        exclude_ids: set[str] | None = None,
    ) -> bool:
        normalized_target = str(image_path or "").strip()
        if not normalized_target:
            return False

        exclude_ids = exclude_ids or set()

        for bottle in self._iter_all_bottles(data):
            bottle_id = str(bottle.get("id") or "")
            if bottle_id in exclude_ids:
                continue
            candidate = str(bottle.get("image_path") or "").strip()
            if candidate and candidate == normalized_target:
                return True

        return False

    async def _async_delete_image_if_unreferenced(
        self,
        data: dict[str, Any],
        *,
        image_path: str,
        exclude_ids: set[str] | None = None,
    ) -> None:
        """Delete an image file when no remaining bottle references it."""
        normalized_target = str(image_path or "").strip()
        if not normalized_target:
            return

        if self._is_image_referenced_elsewhere(
            data,
            image_path=normalized_target,
            exclude_ids=exclude_ids,
        ):
            return

        # _normalize_local_path already refuses anything outside the labels dir.
        local_path = self._normalize_local_path(normalized_target)
        if not local_path:
            return

        await self.hass.async_add_executor_job(_remove_file_if_exists, local_path)

    async def async_delete_image(self, image_path: str) -> None:
        """Delete an image stored under /local/wine_labels."""
        normalized_target = str(image_path or "").strip()
        if not normalized_target:
            return

        # _normalize_local_path refuses anything outside the labels directory.
        local_path = self._normalize_local_path(normalized_target)
        if not local_path:
            return

        await self.hass.async_add_executor_job(_remove_file_if_exists, local_path)

    # ------------------------------------------------------------------
    # Public mutating API.
    #
    # Each of these takes the store lock and delegates to the matching
    # ``*_unlocked`` implementation, so that a load/modify/save sequence is
    # never interleaved with another one.
    # ------------------------------------------------------------------

    async def async_save_cellar(
        self,
        cellar_id: str | None,
        name: str,
        shelves: list,
        display_order: int = 0,
        bg_color: str = "",
    ) -> str:
        """Create or update a cellar."""
        async with self._lock:
            return await self._async_save_cellar_unlocked(
                cellar_id,
                name,
                shelves,
                display_order=display_order,
                bg_color=bg_color,
            )

    async def async_delete_cellar(self, cellar_id: str) -> None:
        """Delete a cellar and everything stored in it."""
        async with self._lock:
            await self._async_delete_cellar_unlocked(cellar_id)

    async def async_save_bottle(self, **kwargs: Any) -> str:
        """Create or update a bottle."""
        async with self._lock:
            return await self._async_save_bottle_unlocked(**kwargs)

    async def async_delete_bottle(self, bottle_id: str) -> None:
        """Delete a bottle permanently."""
        async with self._lock:
            await self._async_delete_bottle_unlocked(bottle_id)

    async def async_consume_bottle(self, bottle_id: str) -> str:
        """Move an active bottle to the consumed history."""
        async with self._lock:
            return await self._async_consume_bottle_unlocked(bottle_id)

    async def async_copy_bottle(self, **kwargs: Any) -> str:
        """Copy a bottle into an active slot."""
        async with self._lock:
            return await self._async_copy_bottle_unlocked(**kwargs)

    async def async_move_bottle(self, **kwargs: Any) -> None:
        """Move a bottle to another slot."""
        async with self._lock:
            await self._async_move_bottle_unlocked(**kwargs)

    async def async_swap_bottles(self, **kwargs: Any) -> None:
        """Swap the physical locations of two bottles."""
        async with self._lock:
            await self._async_swap_bottles_unlocked(**kwargs)

    async def _async_save_cellar_unlocked(
        self,
        cellar_id: str | None,
        name: str,
        shelves: list,
        display_order: int = 0,
        bg_color: str = "",
    ) -> str:
        """Create or update a cellar."""
        name = str(name or "").strip()
        if not name:
            raise ValueError("Cellar name is required")

        data = await self.async_load()
        cellars = data["cellars"]

        normalized_shelves = []
        for index, shelf in enumerate(shelves):
            normalized_shelves.append(self._normalize_shelf(shelf, index))

        if cellar_id:
            for cellar in cellars:
                if cellar.get("id") == cellar_id:
                    existing_ids = {str(item.get("id")) for item in cellar.get("shelves", [])}
                    new_ids = {str(item.get("id")) for item in normalized_shelves}
                    removed_ids = existing_ids - new_ids

                    for bottle in data["bottles"]:
                        if bottle.get("cellar_id") == cellar_id and bottle.get("shelf_id") in removed_ids:
                            raise ValueError("Cannot remove a shelf that still contains bottles")

                    for bottle in data["bottles"]:
                        if bottle.get("cellar_id") != cellar_id:
                            continue
                        shelf = next((s for s in normalized_shelves if s.get("id") == bottle.get("shelf_id")), None)
                        if not shelf:
                            continue
                        if bottle.get("lane") == LANE_FRONT and int(bottle.get("position", 0) or 0) > int(shelf.get("capacity_front", 0) or 0):
                            raise ValueError("Cannot shrink front capacity below existing bottle positions")
                        if bottle.get("lane") == LANE_BACK:
                            if int(shelf.get("capacity_back", 0) or 0) < 1:
                                raise ValueError("Cannot remove back lane while bottles are stored there")
                            if int(bottle.get("position", 0) or 0) > int(shelf.get("capacity_back", 0) or 0):
                                raise ValueError("Cannot shrink back capacity below existing bottle positions")

                    cellar["name"] = name
                    cellar["shelves"] = normalized_shelves
                    cellar["display_order"] = int(display_order)
                    cellar["bg_color"] = bg_color
                    await self.async_save(data)
                    return cellar_id

        new_id = cellar_id or uuid.uuid4().hex
        cellars.append(
            {
                "id": new_id,
                "name": name,
                "shelves": normalized_shelves,
                "display_order": int(display_order),
                "bg_color": bg_color,
            }
        )
        await self.async_save(data)
        return new_id

    async def _async_delete_cellar_unlocked(self, cellar_id: str) -> None:
        """Delete a cellar and its active/consumed bottles, cleaning unreferenced images."""
        data = await self.async_load()

        cellar_bottles = [
            b for b in self._iter_all_bottles(data)
            if b.get("cellar_id") == cellar_id
        ]
        removed_ids = {str(b.get("id") or "") for b in cellar_bottles}
        image_paths = {
            str(b.get("image_path") or "").strip()
            for b in cellar_bottles
            if str(b.get("image_path") or "").strip()
        }

        data["cellars"] = [c for c in data["cellars"] if c.get("id") != cellar_id]
        data["bottles"] = [b for b in data["bottles"] if b.get("cellar_id") != cellar_id]
        data["consumed_bottles"] = [b for b in data["consumed_bottles"] if b.get("cellar_id") != cellar_id]

        for image_path in image_paths:
            await self._async_delete_image_if_unreferenced(
                data,
                image_path=image_path,
                exclude_ids=removed_ids,
            )

        await self.async_save(data)

    async def _async_save_bottle_unlocked(
        self,
        *,
        bottle_id: str | None,
        cellar_id: str,
        shelf_id: str,
        lane: str,
        position: int,
        wine_name: str,
        producer: str = "",
        vintage: int | None = None,
        region: str = "",
        country: str = "",
        varietal: str = "",
        wine_year: int | None = None,
        wine_type: str = "other",
        price: float | int | None = None,
        serving_temp: float | int | None = None,
        alcohol_pct: float | int | None = None,
        image_path: str = "",
        barcode: str = "",
        saq_url: str | None = None,
        aging_start_year: int | None = None,
        aging_end_year: int | None = None,
        rating: int | None = None,
        notes: str = "",
    ) -> str:
        """Create or update a bottle."""
        data = await self.async_load()
        self._validate_slot_available(
            data,
            bottle_id=bottle_id,
            cellar_id=cellar_id,
            shelf_id=shelf_id,
            lane=lane,
            position=int(position),
        )

        # Régularisation et renommage sécurisé du fichier image avec le nom du vin
        current_image_path = str(image_path or "").strip()
        previous_image_path = ""
        existing_bottle_id = bottle_id or ""

        if bottle_id:
            for bottle in data["bottles"]:
                if bottle.get("id") == bottle_id:
                    previous_image_path = str(bottle.get("image_path") or "").strip()
                    break

        # ALGORITHME DE MUTUALISATION DE L'IMAGE POUR BOUTEILLES SEMBLABLES
        if wine_name and current_image_path:
            import os

            # Normalisation stricte pour trouver une bouteille identique en stock
            target_name_norm = str(wine_name).strip().lower()
            target_prod_norm = str(producer).strip().lower()
            existing_shared_path = ""

            for b in self._iter_all_bottles(data):
                if str(b.get("id")) == existing_bottle_id:
                    continue
                # Alignement sur la logique de similarité de l'interface
                b_name_norm = str(b.get("wine_name") or "").strip().lower()
                b_prod_norm = str(b.get("producer") or "").strip().lower()
                b_img = str(b.get("image_path") or "").strip()

                if b_name_norm == target_name_norm and b_prod_norm == target_prod_norm and b_img.startswith("/local/wine_labels/"):
                    existing_shared_path = b_img
                    break

            # Cas A : Une bouteille identique existe déjà avec son image -> on réutilise son fichier
            if existing_shared_path:
                # Si l'image actuelle était un fichier temporaire issu d'un nouvel upload, on le nettoie pour ne pas saturer le disque
                if current_image_path != existing_shared_path and current_image_path.startswith("/local/wine_labels/"):
                    current_local_path = self._normalize_local_path(current_image_path)
                    try:
                        # On ne supprime le fichier physique que s'il n'est pas utilisé ailleurs
                        if not self._is_image_referenced_elsewhere(data, image_path=current_image_path, exclude_ids={existing_bottle_id}):
                            await self.hass.async_add_executor_job(
                                _remove_file_if_exists, current_local_path
                            )
                    except Exception as img_err:
                        _LOGGER.debug("Skipped duplicate image cleanup: %r", img_err)

                _LOGGER.info("Wine Cellar Manager: Reusing shared image for wine '%s'", wine_name)
                image_path = existing_shared_path

            # Cas B : Aucun vin similaire trouvé -> On procède au renommage standard sécurisé de l'image
            elif current_image_path.startswith("/local/wine_labels/"):
                import time
                clean_wine_name = "".join(c for c in wine_name if c.isalnum() or c in (" ", "_", "-")).strip()
                clean_wine_name = clean_wine_name.replace(" ", "_") or "vin"

                current_local_path = self._normalize_local_path(current_image_path)
                _, ext = os.path.splitext(current_local_path)
                ext = ext.lower() if ext else ".jpg"

                new_filename = f"{clean_wine_name}_{int(time.time())}_{uuid.uuid4().hex[:8]}{ext}"
                new_local_path = os.path.join(self.hass.config.path("www", "wine_labels"), new_filename)
                new_image_path_url = f"/local/wine_labels/{new_filename}"

                source_exists = await self.hass.async_add_executor_job(
                    os.path.exists, current_local_path
                )
                if source_exists and current_local_path != new_local_path:
                    try:
                        if self._is_image_referenced_elsewhere(data, image_path=current_image_path, exclude_ids={existing_bottle_id}):
                            import shutil
                            def _sync_copy(): shutil.copy2(current_local_path, new_local_path)
                            await self.hass.async_add_executor_job(_sync_copy)
                        else:
                            def _sync_rename(): os.rename(current_local_path, new_local_path)
                            await self.hass.async_add_executor_job(_sync_rename)

                        image_path = new_image_path_url
                    except Exception as err:
                        _LOGGER.error("Impossible de renommer le fichier image : %r", err)

        payload = {
            "id": bottle_id or uuid.uuid4().hex,
            "cellar_id": cellar_id,
            "shelf_id": shelf_id,
            "lane": lane,
            "position": int(position),
            "wine_name": wine_name,
            "producer": producer,
            "vintage": vintage,
            "region": region,
            "country": country,
            "varietal": varietal,
            "wine_year": wine_year,
            "wine_type": wine_type,
            "price": float(price) if price is not None else None,
            "serving_temp": float(serving_temp) if serving_temp is not None else None,
            "alcohol_pct": float(alcohol_pct) if alcohol_pct is not None else None,
            "image_path": image_path,
            "barcode": barcode,
            "saq_url": _safe_external_url(saq_url),
            "aging_start_year": aging_start_year,
            "aging_end_year": aging_end_year,
            "rating": rating,
            "notes": notes,
            "created_at": _utcnow(),
            "updated_at": _utcnow(),
        }

        if bottle_id:
            for index, bottle in enumerate(data["bottles"]):
                if bottle.get("id") == bottle_id:
                    payload["created_at"] = bottle.get("created_at") or _utcnow()
                    payload["updated_at"] = _utcnow()
                    data["bottles"][index] = self._normalize_bottle(payload)

                    new_image_path = str(payload.get("image_path") or "").strip()
                    if previous_image_path and previous_image_path != new_image_path:
                        await self._async_delete_image_if_unreferenced(
                            data,
                            image_path=previous_image_path,
                            exclude_ids={existing_bottle_id},
                        )

                    await self.async_save(data)
                    return payload["id"]

        data["bottles"].append(self._normalize_bottle(payload))
        await self.async_save(data)
        return payload["id"]

    async def _async_delete_bottle_unlocked(self, bottle_id: str) -> None:
        """Delete a bottle permanently from active and consumed records, cleaning unreferenced images."""
        data = await self.async_load()

        target = None
        for bottle in self._iter_all_bottles(data):
            if bottle.get("id") == bottle_id:
                target = bottle
                break

        image_path = str(target.get("image_path") or "").strip() if target else ""

        data["bottles"] = [b for b in data["bottles"] if b.get("id") != bottle_id]
        data["consumed_bottles"] = [b for b in data["consumed_bottles"] if b.get("id") != bottle_id]

        if image_path:
            await self._async_delete_image_if_unreferenced(
                data,
                image_path=image_path,
                exclude_ids={bottle_id},
            )

        await self.async_save(data)

    async def _async_consume_bottle_unlocked(self, bottle_id: str) -> str:
        """Move active bottle to consumed history."""
        data = await self.async_load()
        bottles = data["bottles"]
        consumed = data["consumed_bottles"]

        source = None
        source_index = None
        for index, bottle in enumerate(bottles):
            if bottle.get("id") == bottle_id:
                source = dict(bottle)
                source_index = index
                break

        if source is None or source_index is None:
            raise ValueError("Bottle not found")

        source["original_bottle_id"] = source.get("id")
        source["id"] = uuid.uuid4().hex
        source["consumed_at"] = _utcnow()

        consumed.append(self._normalize_bottle(source))
        del bottles[source_index]

        await self.async_save(data)
        return source["id"]

    async def _async_copy_bottle_unlocked(
        self,
        *,
        source_bottle_id: str,
        cellar_id: str,
        shelf_id: str,
        lane: str,
        position: int,
    ) -> str:
        """Copy active or consumed bottle into an active slot."""
        data = await self.async_load()
        active = data["bottles"]
        consumed = data["consumed_bottles"]

        source = None
        for bottle in active:
            if bottle.get("id") == source_bottle_id:
                source = dict(bottle)
                break

        if source is None:
            for bottle in consumed:
                if bottle.get("id") == source_bottle_id:
                    source = dict(bottle)
                    break

        if source is None:
            raise ValueError("Source bottle not found")

        self._validate_slot_available(
            data,
            bottle_id=None,
            cellar_id=cellar_id,
            shelf_id=shelf_id,
            lane=lane,
            position=int(position),
        )

        source["id"] = uuid.uuid4().hex
        source["cellar_id"] = cellar_id
        source["shelf_id"] = shelf_id
        source["lane"] = lane
        source["position"] = int(position)
        source["consumed_at"] = ""
        source["original_bottle_id"] = ""
        source["created_at"] = _utcnow()
        source["updated_at"] = _utcnow()

        active.append(self._normalize_bottle(source))
        await self.async_save(data)
        return source["id"]

    async def _async_move_bottle_unlocked(
        self,
        *,
        bottle_id: str,
        cellar_id: str,
        shelf_id: str,
        lane: str,
        position: int,
    ) -> None:
        """Move a bottle to another slot."""
        data = await self.async_load()

        for index, bottle in enumerate(data["bottles"]):
            if bottle.get("id") != bottle_id:
                continue

            self._validate_slot_available(
                data,
                bottle_id=bottle_id,
                cellar_id=cellar_id,
                shelf_id=shelf_id,
                lane=lane,
                position=int(position),
            )

            updated = dict(bottle)
            updated["cellar_id"] = cellar_id
            updated["shelf_id"] = shelf_id
            updated["lane"] = lane
            updated["position"] = int(position)
            updated["updated_at"] = _utcnow()

            data["bottles"][index] = self._normalize_bottle(updated)
            await self.async_save(data)
            return

        raise ValueError("Bottle not found")

    async def _async_swap_bottles_unlocked(
        self,
        *,
        source_id: str,
        dest_id: str,
    ) -> None:
        """Swap the physical locations of two bottles atomically."""
        if source_id == dest_id:
            raise ValueError("Cannot swap a bottle with itself")

        data = await self.async_load()

        source_bottle = None
        dest_bottle = None

        for bottle in data["bottles"]:
            if bottle.get("id") == source_id:
                source_bottle = bottle
            elif bottle.get("id") == dest_id:
                dest_bottle = bottle

        if not source_bottle or not dest_bottle:
            raise ValueError("One or both bottles to swap were not found")

        # Interversion des coordonnées physiques en mémoire
        s_cellar, s_shelf, s_lane, s_pos = (
            source_bottle["cellar_id"],
            source_bottle["shelf_id"],
            source_bottle["lane"],
            source_bottle["position"],
        )

        source_bottle["cellar_id"] = dest_bottle["cellar_id"]
        source_bottle["shelf_id"] = dest_bottle["shelf_id"]
        source_bottle["lane"] = dest_bottle["lane"]
        source_bottle["position"] = dest_bottle["position"]
        source_bottle["updated_at"] = _utcnow()

        dest_bottle["cellar_id"] = s_cellar
        dest_bottle["shelf_id"] = s_shelf
        dest_bottle["lane"] = s_lane
        dest_bottle["position"] = s_pos
        dest_bottle["updated_at"] = _utcnow()

        # Normalisation sécurisée et sauvegarde unique
        for index, b in enumerate(data["bottles"]):
            if b.get("id") == source_id:
                data["bottles"][index] = self._normalize_bottle(source_bottle)
            elif b.get("id") == dest_id:
                data["bottles"][index] = self._normalize_bottle(dest_bottle)

        await self.async_save(data)


    async def async_search_bottles(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Search active and consumed bottles."""
        q = (query or "").strip().lower()
        if not q:
            return []

        data = await self.async_load()
        cellar_map = {c.get("id", ""): c.get("name", "") for c in data["cellars"]}

        results: list[dict[str, Any]] = []

        def score_text(value: Any) -> int:
            text = str(value or "").lower()
            if not text:
                return 0
            if text == q:
                return 100
            if text.startswith(q):
                return 75
            if q in text:
                return 50
            return 0

        for source_name, items in (("active", data["bottles"]), ("consumed", data["consumed_bottles"])):
            for bottle in items:
                best = max(
                    score_text(bottle.get("wine_name")),
                    score_text(bottle.get("producer")),
                    score_text(bottle.get("region")),
                    score_text(bottle.get("country")),
                    score_text(bottle.get("varietal")),
                    score_text(bottle.get("vintage")),
                    score_text(bottle.get("notes")),
                )
                if best <= 0:
                    continue

                cellar_id = bottle.get("cellar_id", "")
                results.append(
                    {
                        "bottle_id": bottle.get("id", ""),
                        "wine_name": bottle.get("wine_name", ""),
                        "producer": bottle.get("producer", ""),
                        "vintage": bottle.get("vintage"),
                        "region": bottle.get("region", ""),
                        "country": bottle.get("country", ""),
                        "varietal": bottle.get("varietal", ""),
                        "display_origin": self._compute_display_origin(bottle),
                        "cellar_name": cellar_map.get(cellar_id, "") or "Consumed / archived",
                        "source": source_name,
                        "score": best,
                        "image_path": bottle.get("image_path", ""),
                        "wine_type": bottle.get("wine_type", "other"),
                        "price": bottle.get("price"),
                        "serving_temp": bottle.get("serving_temp"),
                        "alcohol_pct": bottle.get("alcohol_pct"),
                        "aging_start_year": bottle.get("aging_start_year"),
                        "aging_end_year": bottle.get("aging_end_year"),
                        "rating": bottle.get("rating"),
                        "notes": bottle.get("notes", ""),
                        "saq_url": bottle.get("saq_url"),
                    }
                )

        results.sort(key=lambda item: (-int(item.get("score", 0)), str(item.get("wine_name", "")).lower()))
        return results[:limit]

    async def async_find_duplicate_labels(self, image_path: str) -> list[dict[str, Any]]:
        """Find duplicate label files by hash across active and consumed bottles."""
        local_path = self._normalize_local_path(image_path)
        if not local_path:
            return []

        target = Path(local_path)
        if not target.exists() or not target.is_file():
            return []

        target_hash = self._sha256(target)
        if not target_hash:
            return []

        data = await self.async_load()
        cellar_map = {c.get("id", ""): c.get("name", "") for c in data["cellars"]}
        results: list[dict[str, Any]] = []

        for source_name, items in (("active", data["bottles"]), ("consumed", data["consumed_bottles"])):
            for bottle in items:
                candidate_path = self._normalize_local_path(str(bottle.get("image_path", "")))
                if not candidate_path:
                    continue

                candidate = Path(candidate_path)
                if not candidate.exists() or not candidate.is_file():
                    continue

                try:
                    if candidate.resolve() == target.resolve():
                        continue
                except Exception:
                    pass

                candidate_hash = self._sha256(candidate)
                if not candidate_hash or candidate_hash != target_hash:
                    continue

                results.append(
                    {
                        "bottle_id": bottle.get("id", ""),
                        "wine_name": bottle.get("wine_name", ""),
                        "producer": bottle.get("producer", ""),
                        "vintage": bottle.get("vintage"),
                        "region": bottle.get("region", ""),
                        "country": bottle.get("country", ""),
                        "varietal": bottle.get("varietal", ""),
                        "display_origin": self._compute_display_origin(bottle),
                        "cellar_name": cellar_map.get(bottle.get("cellar_id", ""), "") or "Consumed / archived",
                        "distance": 0,
                        "source": source_name,
                        "image_path": bottle.get("image_path", ""),
                        "wine_type": bottle.get("wine_type", "other"),
                        "price": bottle.get("price"),
                        "serving_temp": bottle.get("serving_temp"),
                        "alcohol_pct": bottle.get("alcohol_pct"),
                        "aging_start_year": bottle.get("aging_start_year"),
                        "aging_end_year": bottle.get("aging_end_year"),
                        "rating": bottle.get("rating"),
                        "notes": bottle.get("notes", ""),
                        "saq_url": bottle.get("saq_url"),
                    }
                )

        return results[:10]

    async def async_upload_label_image(self, filename: str, data_base64: str) -> str:
        """Decode base64 image data and save it securely to the local filesystem."""
        import base64
        import binascii
        import time

        upload_dir = self.hass.config.path("www", "wine_labels")

        clean_filename = "".join(
            c for c in filename if c.isalnum() or c in (".", "_", "-")
        ).strip()
        if not clean_filename:
            clean_filename = "uploaded_label.jpg"

        name_part, ext_part = os.path.splitext(clean_filename)
        # Only ever write known image extensions into the web-served www/ tree,
        # so an upload can never become a served .html/.js/.svg payload.
        ext_part = ext_part.lower()
        if ext_part not in ALLOWED_IMAGE_EXTENSIONS:
            ext_part = ".jpg"
        if not name_part:
            name_part = "uploaded_label"

        try:
            image_bytes = base64.b64decode(data_base64, validate=True)
        except (binascii.Error, ValueError) as err:
            raise ValueError("Uploaded data is not valid base64") from err

        if not image_bytes:
            raise ValueError("Uploaded image is empty")

        if len(image_bytes) > MAX_IMAGE_BYTES:
            raise ValueError(
                f"Image is too large ({len(image_bytes) // 1024} KB); "
                f"the limit is {MAX_IMAGE_BYTES // 1024} KB"
            )

        detected = _detect_image_mime(image_bytes)
        if detected is None:
            raise ValueError("Uploaded file is not a recognized image")

        unique_filename = f"{name_part}_{int(time.time())}_{uuid.uuid4().hex[:8]}{ext_part}"
        target_path = os.path.join(upload_dir, unique_filename)

        def _sync_write_image():
            os.makedirs(upload_dir, exist_ok=True)
            with open(target_path, "wb") as handle:
                handle.write(image_bytes)

        try:
            await self.hass.async_add_executor_job(_sync_write_image)
            _LOGGER.debug("Saved label image to %s (%s)", target_path, detected)
            return f"/local/wine_labels/{unique_filename}"

        except OSError as err:
            _LOGGER.error("Failed to write image to disk: %r", err)
            raise ValueError(f"Could not save the image: {err}") from err
