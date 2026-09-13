from __future__ import annotations

from datetime import date

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import PERCENTAGE
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .entity_identity import investor_entity_key

SENSOR_SPECS = (
    ("value", "Total Value", "INR", "monetary"),
    ("invested", "Total Invested", "INR", "monetary"),
    ("profit", "Total Profit", "INR", "monetary"),
    ("profit_pct", "Total Profit %", PERCENTAGE, "percentage"),
    ("day_change", "Daily Change", "INR", "monetary"),
    ("month_change", "Monthly Change", "INR", "monetary"),
    ("year_change", "Yearly Change", "INR", "monetary"),
    ("xirr", "Portfolio XIRR", PERCENTAGE, "percentage"),
    ("fund_count", "Fund Count", None, "count"),
    ("next_expected_sip_date", "Next Expected SIP Date", None, "date"),
    ("sip_executed", "Executed SIP", None, "count"),
    ("sip_upcoming", "Upcoming SIP", None, "count"),
    ("nav_date", "Latest NAV Date", None, "date"),
    ("fund_details", "Fund Details", None, "fund_details"),
)


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
    entities.extend(_make_group(coordinator, "all", all_inv, "All Investors"))
    for profile in coordinator.data.get("profiles", []):
        entities.extend(
            _make_group(
                coordinator,
                investor_entity_key(profile),
                profile,
                profile.get("name") or "Investor",
            )
        )
    return entities


def _make_group(coordinator, key, group, name):
    total = group.get("total") or {}
    return [
        MutualFundInvestorSensor(
            coordinator,
            key,
            name,
            field,
            label,
            unit,
            kind,
            total,
            group.get("fund_count", 0),
            group,
        )
        for field, label, unit, kind in SENSOR_SPECS
    ]


class MutualFundInvestorSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, key, investor_name, field, label, unit, kind, initial_total, fund_count, group):
        super().__init__(coordinator)
        self._key = key
        self._investor_name = investor_name
        self._profile_id = group.get("id") if isinstance(group, dict) else None
        self._field = field
        self._kind = kind
        self._attr_name = label
        self._attr_native_unit_of_measurement = unit
        self._attr_unique_id = f"{DOMAIN}_{key}_{field}"
        # Use an explicit object-id for the percentage profit sensor so the
        # percent sign cannot be stripped into a collision with Total Profit.
        if field == "profit_pct":
            self._attr_suggested_object_id = "total_profit_pct"

        if kind in ("monetary", "percentage", "count"):
            self._attr_state_class = SensorStateClass.MEASUREMENT
        if kind == "monetary":
            self._attr_device_class = SensorDeviceClass.MONETARY
        elif kind == "date":
            self._attr_device_class = SensorDeviceClass.DATE

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

    def _attributes_for(self, group):
        if not group:
            return {}

        # Keep Home Assistant entity attributes intentionally minimal.
        # Each SIP sensor exposes only its authoritative detail list; the
        # parallel fund/amount/count/total fields are derived and belong in
        # the entity state/UI rather than being duplicated as attributes.
        if self._field == "fund_details":
            return {"funds": group.get("funds", [])}
        if self._field == "next_expected_sip_date":
            return {"details": group.get("next_expected_sip_details", [])}
        if self._field == "sip_executed":
            return {"details": group.get("executed_sip_details", [])}
        if self._field == "sip_upcoming":
            return {"details": group.get("upcoming_sip_details", [])}
        return {}

    @property
    def available(self) -> bool:
        return self._group() is not None

    @property
    def native_value(self):
        group = self._group()
        if not group:
            return None
        if self._field == "fund_count":
            return group.get("fund_count", 0)
        if self._field == "fund_details":
            return group.get("fund_count", 0)
        if self._field == "sip_executed":
            return group.get("executed_sip_count", 0)
        if self._field == "sip_upcoming":
            return group.get("upcoming_sip_count", 0)
        if self._field in ("next_expected_sip_date", "nav_date"):
            raw = group.get("next_expected_sip_date") if self._field == "next_expected_sip_date" else group.get("nav_date")
            try:
                return date.fromisoformat(raw) if raw else None
            except (TypeError, ValueError):
                return None
        return (group.get("total") or {}).get(self._field)

    @property
    def extra_state_attributes(self):
        return self._attributes_for(self._group())
