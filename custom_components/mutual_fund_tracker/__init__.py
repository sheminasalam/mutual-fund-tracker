from __future__ import annotations

import json
import logging
from datetime import timedelta
from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import async_get as async_get_device_registry
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, STATE_FILES, UPDATE_INTERVAL_SECONDS, INTEGRATION_RELOAD_ACK_FILE

# Pre-import platform modules at module load time so Home Assistant does not
# need to import them synchronously while forwarding config-entry platforms.
from . import binary_sensor, button, sensor  # noqa: F401,E402
from .entity_identity import investor_entity_key

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Mutual Fund Tracker integration."""
    static_dir = Path(__file__).parent / "static"
    card_path = static_dir / "mutual-fund-tracker-card.js"
    logo_path = static_dir / "mutual-fund-tracker-logo.png"
    static_paths = []
    if card_path.exists():
        static_paths.append(StaticPathConfig(
            "/api/mutual_fund_tracker/mutual-fund-tracker-card.js",
            str(card_path),
            False,
        ))
    else:
        _LOGGER.warning("Mutual Fund Tracker custom card file is missing: %s", card_path)
    if logo_path.exists():
        static_paths.append(StaticPathConfig(
            "/api/mutual_fund_tracker/mutual-fund-tracker-logo.png",
            str(logo_path),
            True,
        ))
    else:
        _LOGGER.warning("Mutual Fund Tracker logo file is missing: %s", logo_path)
    if static_paths:
        await hass.http.async_register_static_paths(static_paths)
    else:
        _LOGGER.warning("Mutual Fund Tracker custom card file is missing: %s", card_path)
    return True


async def _migrate_profit_pct_entity_ids(hass: HomeAssistant) -> None:
    """Rename the percentage-profit entities away from the legacy collision name.

    Older installations could receive ``*_total_profit_2`` because Home
    Assistant stripped the ``%`` from ``Total Profit %`` and collided with
    ``Total Profit``.  Keep the stable unique_id and migrate only our own
    profit-percentage entities to the explicit ``*_total_profit_pct`` object id.
    """
    registry = er.async_get(hass)
    for entry in list(registry.entities.values()):
        if entry.platform != DOMAIN or not entry.unique_id.startswith(f"{DOMAIN}_"):
            continue
        if not entry.unique_id.endswith("_profit_pct"):
            continue
        object_id = entry.entity_id.split(".", 1)[-1]
        if not object_id.endswith("_total_profit_2"):
            continue
        target = object_id[:-len("_total_profit_2")] + "_total_profit_pct"
        target_entity_id = f"sensor.{target}"
        existing = registry.async_get(target_entity_id)
        if existing is not None:
            if existing.unique_id == entry.unique_id:
                continue
            _LOGGER.warning(
                "Cannot migrate Mutual Fund Tracker profit %% entity %s to %s; target already exists",
                entry.entity_id, target_entity_id,
            )
            continue
        try:
            registry.async_update_entity(entry.entity_id, new_entity_id=target_entity_id)
            _LOGGER.info("Migrated Mutual Fund Tracker profit %% entity %s -> %s", entry.entity_id, target_entity_id)
        except Exception:
            _LOGGER.exception("Unable to migrate Mutual Fund Tracker profit %% entity %s -> %s", entry.entity_id, target_entity_id)


async def _cleanup_legacy_investor_entities(hass: HomeAssistant, coordinator) -> None:
    """Remove old profile-id-based entity/device registry entries.

    Older releases used the reusable database profile ID as the Home Assistant
    unique_id/device identifier. Current releases derive the identity from PAN,
    so old registry entries are no longer logically associated with the current
    investor. Remove only Mutual Fund Tracker legacy entities/devices.
    """
    registry = er.async_get(hass)
    profiles = coordinator.data.get("profiles", []) if coordinator.data else []

    expected = {f"{DOMAIN}_refresh_nav", f"{DOMAIN}_nse_today", f"{DOMAIN}_nse_tomorrow", f"{DOMAIN}_nse_trading", f"{DOMAIN}_nse_yesterday_status", f"{DOMAIN}_nav_refresh_error"}
    expected.add(f"{DOMAIN}_all_value")
    # Build the exact set of current entity unique IDs.
    from .sensor import SENSOR_SPECS
    for field, *_ in SENSOR_SPECS:
        expected.add(f"{DOMAIN}_all_{field}")
    expected.add(f"{DOMAIN}_all_sip_executed_today")
    expected.add(f"{DOMAIN}_all_nav_update_delayed")

    active_keys = {"all"}
    for profile in profiles:
        key = investor_entity_key(profile)
        active_keys.add(key)
        for field, *_ in SENSOR_SPECS:
            expected.add(f"{DOMAIN}_{key}_{field}")
        expected.add(f"{DOMAIN}_{key}_sip_executed_today")
        expected.add(f"{DOMAIN}_{key}_nav_update_delayed")
        expected.add(f"{DOMAIN}_{key}_export_portfolio_png")

    # Remove obsolete profile-id-based entities (and any other stale MT entities)
    # that are not part of the current stable identity scheme. Do not touch other
    # integrations or unrelated entities.
    for entry in list(registry.entities.values()):
        if entry.platform != DOMAIN or not entry.unique_id.startswith(f"{DOMAIN}_"):
            continue
        if entry.unique_id not in expected:
            registry.async_remove(entry.entity_id)
            _LOGGER.debug("Removed legacy/stale Mutual Fund Tracker entity %s", entry.entity_id)

    device_registry = async_get_device_registry(hass)
    for device in list(device_registry.devices.values()):
        if hass.config_entries.async_entries(DOMAIN):
            if not any(eid in device.config_entries for eid in [e.entry_id for e in hass.config_entries.async_entries(DOMAIN)]):
                continue
        investor_keys = {
            identifier[1][len("investor_"):]
            for identifier in device.identifiers
            if identifier[0] == DOMAIN and identifier[1].startswith("investor_")
        }
        if investor_keys and investor_keys.isdisjoint(active_keys):
            device_registry.async_remove_device(device.id)
            _LOGGER.debug("Removed stale Mutual Fund Tracker investor device %s", device.id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    async def _async_update():
        last_not_found = None
        candidates = []
        errors = []
        for state_file in STATE_FILES:
            try:
                raw = await hass.async_add_executor_job(Path(state_file).read_text, "utf-8")
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError("Invalid Mutual Fund Tracker state file")
                if data.get("version", 0) < 1:
                    raise ValueError("Unsupported Mutual Fund Tracker state format")
                candidates.append(data)
            except FileNotFoundError as err:
                last_not_found = err
                continue
            except (OSError, json.JSONDecodeError, ValueError) as err:
                errors.append(f"{state_file}: {err}")
                continue
        if candidates:
            # Older installations may contain both /share and /config snapshots.
            # Select the newest valid snapshot rather than whichever path appears
            # first; otherwise a stale copy can keep HA entities and cards old.
            def freshness(data):
                try:
                    revision = int(data.get("state_revision", 0) or 0)
                except (TypeError, ValueError):
                    revision = 0
                return (revision, str(data.get("updated_at") or ""))
            return max(candidates, key=freshness)
        if errors:
            raise UpdateFailed("Unable to read Mutual Fund Tracker state: " + "; ".join(errors))
        raise UpdateFailed("Mutual Fund Tracker app has not exported its state yet") from last_not_found

    def _schedule_reload_if_requested(data):
        if not data.get('integration_reload_requested'):
            return
        token = str(data.get('integration_reload_token') or '')
        if not token:
            return
        pending = hass.data.setdefault(DOMAIN, {}).setdefault('_reload_requests_seen', set())
        if token in pending:
            return
        pending.add(token)

        async def _reload_entry():
            try:
                ok = await hass.config_entries.async_reload(entry.entry_id)
                if ok:
                    path = Path(INTEGRATION_RELOAD_ACK_FILE)
                    await hass.async_add_executor_job(lambda: path.parent.mkdir(parents=True, exist_ok=True))
                    await hass.async_add_executor_job(lambda: path.write_text(token, encoding='utf-8'))
                else:
                    pending.discard(token)
            except Exception as err:
                pending.discard(token)
                _LOGGER.error('Unable to reload Mutual Fund Tracker after import request: %s', err)

        hass.async_create_task(_reload_entry())

    coordinator = DataUpdateCoordinator(
        hass,
        logger=_LOGGER,
        name="Mutual Fund Tracker",
        update_method=_async_update,
        update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
    )
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    async def _coordinator_updated() -> None:
        await _cleanup_legacy_investor_entities(hass, coordinator)
        await _migrate_profit_pct_entity_ids(hass)
        _schedule_reload_if_requested(coordinator.data or {})

    def _schedule_registry_cleanup() -> None:
        hass.async_create_task(_coordinator_updated())

    coordinator.async_add_listener(_schedule_registry_cleanup)
    await _cleanup_legacy_investor_entities(hass, coordinator)
    await _migrate_profit_pct_entity_ids(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return ok
