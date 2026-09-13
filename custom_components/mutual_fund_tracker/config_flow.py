from __future__ import annotations

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
import voluptuous as vol

from .const import DOMAIN, STATE_FILES


class MutualFundTrackerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            return self.async_create_entry(
                title=user_input.get(CONF_NAME, "Mutual Fund Tracker"),
                data={},
            )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_NAME, default="Mutual Fund Tracker"): str}),
            description_placeholders={"state_file": STATE_FILES[0]},
        )
