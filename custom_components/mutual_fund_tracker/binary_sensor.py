from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .entity_identity import investor_entity_key


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = {}

    def sync_entities() -> None:
        current = {entity.unique_id: entity for entity in _entities_from_state(coordinator)}
        new_entities = []
        for unique_id, entity in current.items():
            if unique_id not in entities:
                entities[unique_id] = entity
                new_entities.append(entity)
        if new_entities:
            async_add_entities(new_entities)

    sync_entities()
    coordinator.async_add_listener(lambda: (sync_entities(), None)[1])


def _entities_from_state(coordinator):
    entities = []
    all_inv = coordinator.data.get("all_investors") or {}
    entities.append(MutualFundNSEHolidayBinarySensor(coordinator))
    entities.append(MutualFundNSETomorrowBinarySensor(coordinator))
    entities.append(MutualFundNSEMarketBinarySensor(coordinator))
    entities.append(MutualFundNSEYesterdayBinarySensor(coordinator))
    entities.append(MutualFundNAVRefreshErrorBinarySensor(coordinator))
    entities.append(MutualFundSIPExecutedTodayBinarySensor(coordinator, "all", "All Investors"))
    entities.append(MutualFundDelayedNAVUpdateBinarySensor(coordinator, "all", "All Investors"))
    for profile in coordinator.data.get("profiles", []):
        entities.append(MutualFundSIPExecutedTodayBinarySensor(
            coordinator, investor_entity_key(profile), profile.get("name") or "Investor"
        ))
        entities.append(MutualFundDelayedNAVUpdateBinarySensor(
            coordinator, investor_entity_key(profile), profile.get("name") or "Investor"
        ))
    return entities


class MutualFundNSEHolidayBinarySensor(CoordinatorEntity, BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_name = "NSE Today"

    def __init__(self, coordinator):
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_nse_today"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "controller")},
            name="Mutual Fund Tracker",
            manufacturer="Mutual Fund Tracker",
            model="NAV Controller",
        )

    @property
    def is_on(self):
        return bool((self.coordinator.data.get("nse_holiday") or {}).get("nse_open", False))

    @property
    def extra_state_attributes(self):
        return dict(self.coordinator.data.get("nse_holiday") or {})


class MutualFundNSETomorrowBinarySensor(CoordinatorEntity, BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_name = "NSE Tomorrow"

    def __init__(self, coordinator):
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_nse_tomorrow"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "controller")},
            name="Mutual Fund Tracker",
            manufacturer="Mutual Fund Tracker",
            model="NAV Controller",
        )

    @property
    def is_on(self):
        return bool((self.coordinator.data.get("nse_holiday") or {}).get("tomorrow_nse_open", False))

    @property
    def extra_state_attributes(self):
        holiday = self.coordinator.data.get("nse_holiday") or {}
        return {
            "tomorrow_date": holiday.get("tomorrow_date"),
            "tomorrow_nse_open": holiday.get("tomorrow_nse_open", False),
            "tomorrow_type": holiday.get("tomorrow_type"),
            "tomorrow_description": holiday.get("tomorrow_description"),
            "tomorrow_holiday_type": holiday.get("tomorrow_holiday_type"),
        }


class MutualFundNSEMarketBinarySensor(CoordinatorEntity, BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_name = "NSE Trading"

    def __init__(self, coordinator):
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_nse_trading"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "controller")},
            name="Mutual Fund Tracker",
            manufacturer="Mutual Fund Tracker",
            model="NAV Controller",
        )

    @property
    def is_on(self):
        return bool((self.coordinator.data.get("market_status") or {}).get("market_open", False))

    @property
    def extra_state_attributes(self):
        return dict(self.coordinator.data.get("market_status") or {})


class MutualFundNSEYesterdayBinarySensor(CoordinatorEntity, BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_name = "NSE Yesterday Status"

    def __init__(self, coordinator):
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_nse_yesterday_status"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "controller")},
            name="Mutual Fund Tracker",
            manufacturer="Mutual Fund Tracker",
            model="NAV Controller",
        )

    @property
    def is_on(self):
        return bool((self.coordinator.data.get("nse_holiday") or {}).get("yesterday_nse_open", False))

    @property
    def extra_state_attributes(self):
        holiday = self.coordinator.data.get("nse_holiday") or {}
        return {
            "yesterday_date": holiday.get("yesterday_date"),
            "yesterday_nse_open": holiday.get("yesterday_nse_open", False),
            "yesterday_type": holiday.get("yesterday_type"),
            "yesterday_description": holiday.get("yesterday_description"),
            "yesterday_holiday_type": holiday.get("yesterday_holiday_type"),
        }


class MutualFundNAVRefreshErrorBinarySensor(CoordinatorEntity, BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_name = "NAV Refresh Error"

    def __init__(self, coordinator):
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_nav_refresh_error"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "controller")},
            name="Mutual Fund Tracker",
            manufacturer="Mutual Fund Tracker",
            model="NAV Controller",
        )

    @property
    def is_on(self):
        return bool(self.coordinator.data.get("nav_refresh_error"))

    @property
    def extra_state_attributes(self):
        return dict(self.coordinator.data.get("nav_refresh_error") or {})


class MutualFundSIPExecutedTodayBinarySensor(CoordinatorEntity, BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_name = "SIP Executed Today"

    def __init__(self, coordinator, key, investor_name):
        super().__init__(coordinator)
        self._key = key
        self._investor_name = investor_name
        self._attr_unique_id = f"{DOMAIN}_{key}_sip_executed_today"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"investor_{key}")},
            name=f"Mutual Funds – {investor_name}",
            manufacturer="Mutual Fund Tracker",
            model="Investor Portfolio",
        )

    def _group(self):
        if self._key == "all":
            return self.coordinator.data.get("all_investors") or {}
        return next(
            (p for p in self.coordinator.data.get("profiles", []) if investor_entity_key(p) == self._key),
            None,
        )

    @property
    def available(self) -> bool:
        return self._group() is not None

    @property
    def is_on(self) -> bool:
        group = self._group() or {}
        return bool(group.get("today_executed_sip_count", 0))

    @property
    def extra_state_attributes(self):
        group = self._group() or {}
        return {
            "reporting_date": group.get("today_executed_sip_date"),
            "count": int(group.get("today_executed_sip_count", 0) or 0),
            "total_amount": float(group.get("today_executed_sip_total_amount", 0) or 0),
            "details": group.get("today_executed_sip_details") or [],
        }


class MutualFundDelayedNAVUpdateBinarySensor(CoordinatorEntity, BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Delayed NAV Update"

    def __init__(self, coordinator, key, investor_name):
        super().__init__(coordinator)
        self._key = key
        self._investor_name = investor_name
        self._attr_unique_id = f"{DOMAIN}_{key}_nav_update_delayed"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"investor_{key}")},
            name=f"Mutual Funds – {investor_name}",
            manufacturer="Mutual Fund Tracker",
            model="Investor Portfolio",
        )

    def _group(self):
        if self._key == "all":
            return self.coordinator.data.get("all_investors") or {}
        return next(
            (p for p in self.coordinator.data.get("profiles", []) if investor_entity_key(p) == self._key),
            None,
        )

    @property
    def available(self) -> bool:
        return self._group() is not None

    @property
    def is_on(self) -> bool:
        group = self._group() or {}
        return bool(group.get("delayed_nav_update_details") or [])

    @property
    def extra_state_attributes(self):
        group = self._group() or {}
        return {"details": group.get("delayed_nav_update_details") or []}
