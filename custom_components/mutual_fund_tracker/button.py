from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path

from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, EXPORT_REQUEST_FILE, EXPORT_SHARE_DIR, HA_EXPORT_DIR, REFRESH_REQUEST_FILE
from .entity_identity import investor_entity_key


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = {}

    def sync_entities() -> None:
        current = {entity.unique_id: entity for entity in _entities_from_state(coordinator, hass)}
        new_entities = []
        for unique_id, entity in current.items():
            if unique_id not in entities:
                entities[unique_id] = entity
                new_entities.append(entity)
        if new_entities:
            async_add_entities(new_entities)

    sync_entities()
    coordinator.async_add_listener(lambda: (sync_entities(), None)[1])


def _entities_from_state(coordinator, hass):
    entities = [MutualFundRefreshButton(coordinator)]
    for profile in coordinator.data.get("profiles", []):
        pid = str(profile.get("id"))
        key = investor_entity_key(profile)
        if pid:
            entities.append(MutualFundExportButton(coordinator, pid, key, profile.get("name") or "Investor"))
    return entities


class MutualFundRefreshButton(CoordinatorEntity, ButtonEntity):
    _attr_has_entity_name = True
    _attr_name = "Refresh NAV"
    _attr_icon = "mdi:refresh"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_unique_id = f"{DOMAIN}_refresh_nav"

    def __init__(self, coordinator):
        super().__init__(coordinator)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "controller")},
            name="Mutual Fund Tracker",
            manufacturer="Mutual Fund Tracker",
            model="NAV Controller",
        )

    async def async_press(self) -> None:
        await self.hass.async_add_executor_job(self._request_refresh)

    @staticmethod
    def _request_refresh() -> None:
        path = Path(REFRESH_REQUEST_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("refresh\n", encoding="utf-8")


class MutualFundExportButton(CoordinatorEntity, ButtonEntity):
    _attr_has_entity_name = True
    _attr_name = "Export Portfolio PNG"
    _attr_icon = "mdi:file-image-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, profile_id: str, entity_key: str, investor_name: str):
        super().__init__(coordinator)
        self._profile_id = profile_id
        self._entity_key = entity_key
        self._investor_name = investor_name
        self._attr_unique_id = f"{DOMAIN}_{entity_key}_export_portfolio_png"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"investor_{entity_key}")},
            name=f"Mutual Funds – {investor_name}",
            manufacturer="Mutual Fund Tracker",
            model="Investor Portfolio",
        )

    async def async_press(self) -> None:
        await self.hass.async_add_executor_job(self._export_and_publish)

    def _export_and_publish(self) -> None:
        request_id = uuid.uuid4().hex
        safe = "".join(c.lower() if c.isalnum() else "-" for c in self._investor_name).strip("-") or f"investor-{self._profile_id}"
        output_name = f"{safe}-mutual-fund-analysis.png"
        share_dir = Path(EXPORT_SHARE_DIR)
        result_path = share_dir / f"export_result_{request_id}.json"
        request_path = Path(EXPORT_REQUEST_FILE)
        target_dir = Path(HA_EXPORT_DIR)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / output_name
        try:
            if target.exists():
                target.unlink()
            if result_path.exists():
                result_path.unlink()
            request_path.parent.mkdir(parents=True, exist_ok=True)
            request_path.write_text(json.dumps({
                "request_id": request_id,
                "profile_id": int(self._profile_id),
                "output_name": output_name,
            }), encoding="utf-8")
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if result_path.exists():
                    result = json.loads(result_path.read_text(encoding="utf-8"))
                    if result.get("status") == "completed":
                        source = Path(result["path"])
                        if not source.exists():
                            raise RuntimeError("Mutual Fund Tracker created the PNG but it could not be found")
                        shutil.copy2(source, target)
                        return
                    raise RuntimeError(result.get("error") or "PNG export failed")
                time.sleep(0.5)
            raise RuntimeError("Timed out waiting for Mutual Fund Tracker to create the PNG")
        finally:
            try:
                if result_path.exists():
                    result_path.unlink()
            except OSError:
                pass
