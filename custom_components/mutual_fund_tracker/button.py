from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from datetime import timedelta
from pathlib import Path

from homeassistant.components.http.auth import async_sign_path
from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    EXPORT_EVENT,
    EXPORT_RETENTION_SECONDS,
    EXPORT_REQUEST_FILE,
    EXPORT_SHARE_DIR,
    EXPORT_SIGNED_URL_TTL_SECONDS,
    EXPORT_SUBDIR,
    EXPORT_URL_BASE,
    REFRESH_REQUEST_FILE,
)
from .entity_identity import investor_entity_key
from .export_utils import UnsafeExportError, prune_exports, read_validated_export, write_text_atomic


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
        target_dir = Path(self.hass.config.path(*EXPORT_SUBDIR))
        # One export at a time: the app has a single request file, so the whole
        # request -> result -> copy exchange must not interleave.
        lock = self.hass.data.setdefault(DOMAIN, {}).setdefault("_export_lock", asyncio.Lock())
        async with lock:
            target = await self.hass.async_add_executor_job(self._export_and_publish, target_dir)
        # Authenticated, time-limited link (works with Nabu Casa / tunnels).
        signed_path = async_sign_path(
            self.hass,
            f"{EXPORT_URL_BASE}/{target.name}",
            timedelta(seconds=EXPORT_SIGNED_URL_TTL_SECONDS),
        )
        # Prefer the externally reachable URL (Nabu Casa / configured external
        # URL) so Node-RED and other external services can fetch the image.
        scope = "external"
        try:
            base = get_url(self.hass, allow_internal=False, prefer_external=True)
        except NoURLAvailableError:
            try:
                base, scope = get_url(self.hass, allow_external=False), "internal"
            except NoURLAvailableError:
                base, scope = "", "relative"
        signed_url = f"{base}{signed_path}"
        self.hass.bus.async_fire(
            EXPORT_EVENT,
            {
                "investor": self._investor_name,
                "entity_key": self._entity_key,
                "file_name": target.name,
                "path": str(target),
                "signed_url": signed_url,
                "signed_url_scope": scope,
                "signed_url_expires_in": EXPORT_SIGNED_URL_TTL_SECONDS,
            },
        )

    @staticmethod
    def _withdraw_request(request_path: Path, request_id: str) -> None:
        """Remove our unprocessed request so it cannot run later unnoticed."""
        try:
            if json.loads(request_path.read_text(encoding="utf-8")).get("request_id") == request_id:
                request_path.unlink()
        except (OSError, ValueError):
            pass

    def _export_and_publish(self, target_dir: Path) -> Path:
        request_id = uuid.uuid4().hex
        # Opaque, per-export names: no investor name on disk, and an old link can
        # never show a newer export.
        output_name = f"export-{request_id}.png"
        share_dir = Path(EXPORT_SHARE_DIR)
        result_path = share_dir / f"export_result_{request_id}.json"
        request_path = Path(EXPORT_REQUEST_FILE)
        target_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(target_dir, 0o700)
        prune_exports(target_dir, share_dir, EXPORT_RETENTION_SECONDS)
        target = target_dir / output_name
        try:
            request_path.parent.mkdir(parents=True, exist_ok=True)
            write_text_atomic(request_path, json.dumps({
                "request_id": request_id,
                "profile_id": int(self._profile_id),
                "output_name": output_name,
            }))
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if result_path.exists():
                    try:
                        result = json.loads(result_path.read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        # Transient/partial write: try again rather than failing.
                        time.sleep(0.5)
                        continue
                    if result.get("status") == "completed":
                        try:
                            data = read_validated_export(str(result.get("path", "")), share_dir, output_name)
                        except UnsafeExportError as err:
                            raise RuntimeError(f"Rejected PNG export result: {err}") from err
                        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                        with os.fdopen(fd, "wb") as out:
                            out.write(data)
                        try:
                            (share_dir / output_name).unlink()
                        except OSError:
                            pass
                        return target
                    raise RuntimeError(result.get("error") or "PNG export failed")
                time.sleep(0.5)
            self._withdraw_request(request_path, request_id)
            raise RuntimeError("Timed out waiting for Mutual Fund Tracker to create the PNG")
        finally:
            try:
                if result_path.exists():
                    result_path.unlink()
            except OSError:
                pass
