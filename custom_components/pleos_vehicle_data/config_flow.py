"""Guided setup and replacement of manually renewed personal API keys."""

from urllib.parse import urlparse

import probatio as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector, NumberSelector, NumberSelectorConfig, NumberSelectorMode,
    SelectSelector, SelectSelectorConfig, SelectSelectorMode,
    TextSelector, TextSelectorConfig, TextSelectorType,
)

from .api import ApiError, AuthError, PleosClient
from .const import BRANDS, DEFAULT_OPTIONS, DOMAIN
from .export import FORMATS as EXPORT_FORMATS

KEY_FIELDS = {
    vol.Required("client_id"): TextSelector(),
    vol.Required("client_secret"): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
}
PLEOS_URL = "https://pleos.ai/"
KEY_GUIDE_URL = "https://document.pleos.ai/en/api-reference/vehicle-data-api/getting-started/for-private-users/request-my-vehicle-api-key"


class PleosConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def validate(self, brand, user_input):
        credentials = {key: user_input[key].strip() for key in ("client_id", "client_secret")}
        if not all(credentials.values()):
            return "invalid_auth"
        try:
            await PleosClient(async_get_clientsession(self.hass), brand, **credentials).vehicles()
        except AuthError:
            return "invalid_auth"
        except ApiError as error:
            return "rate_limited" if error.status == 429 else "cannot_connect"
        return None

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input:
            brand = user_input["brand"]
            await self.async_set_unique_id(brand)
            self._abort_if_unique_id_configured()
            if error := await self.validate(brand, user_input):
                errors["base"] = error
            else:
                data = {key: value.strip() for key, value in user_input.items()}
                return self.async_create_entry(title=BRANDS[brand], data=data)
        schema = vol.Schema({
            vol.Required("brand"): SelectSelector(SelectSelectorConfig(
                options=[{"value": key, "label": name} for key, name in BRANDS.items()],
                mode=SelectSelectorMode.DROPDOWN,
            )), **KEY_FIELDS,
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors,
                                    description_placeholders={"pleos_url": PLEOS_URL, "key_guide_url": KEY_GUIDE_URL})

    async def async_step_reauth(self, entry_data):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        return await self.replace_key("reauth_confirm", self._get_reauth_entry(), user_input)

    async def async_step_reconfigure(self, user_input=None):
        return await self.replace_key("reconfigure", self._get_reconfigure_entry(), user_input)

    async def replace_key(self, step, entry, user_input):
        errors = {}
        if user_input:
            if error := await self.validate(entry.data["brand"], user_input):
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={key: user_input[key].strip() for key in ("client_id", "client_secret")},
                )
        return self.async_show_form(step_id=step, data_schema=vol.Schema(KEY_FIELDS), errors=errors,
                                    description_placeholders={"brand": BRANDS[entry.data["brand"]], "pleos_url": PLEOS_URL})

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return PleosOptionsFlow()


# Options shown together in the form; entry options stay flat.
OPTION_SECTIONS = {"polling": ("poll_minutes", "stale_minutes", "location"),
                   "trips": ("trip_poll_minutes", "trip_events"),
                   "storage": ("archive",),
                   "export": ("export_format", "export_url", "export_database", "export_org", "export_token")}
# Empty text options are allowed; the rest must always be set.
OPTIONAL_FIELDS = ("export_url", "export_database", "export_org", "export_token")


def minutes(maximum):
    return NumberSelector(NumberSelectorConfig(min=5, max=maximum, step=1, mode=NumberSelectorMode.BOX,
                                               unit_of_measurement="min"))


def export_error(options):
    """Why the export settings cannot work, or None."""
    if options["export_format"] == "off":
        return None
    url = urlparse(options["export_url"].strip())
    if url.scheme not in ("http", "https") or not url.netloc:
        return "invalid_export_url"
    if url.username and options["export_token"].strip():
        return "credentials_and_token"
    if options["export_format"] == "influx_v2" and not (options["export_database"].strip() and options["export_org"].strip()):
        return "missing_bucket"
    return None


class PleosOptionsFlow(config_entries.OptionsFlowWithReload):
    async def async_step_init(self, user_input=None):
        options = DEFAULT_OPTIONS | dict(self.config_entry.options)
        errors = {}
        if user_input is not None:
            submitted = {key: value for fields in user_input.values() for key, value in fields.items()}
            submitted = {key: value.strip() if isinstance(value, str) else value for key, value in submitted.items()}
            if error := export_error(DEFAULT_OPTIONS | submitted):
                errors["base"] = error
                options |= submitted
            else:
                return self.async_create_entry(title="", data=submitted)
        selectors = {"poll_minutes": minutes(1440), "stale_minutes": minutes(10080), "location": BooleanSelector(),
                     "trip_poll_minutes": minutes(1440), "trip_events": BooleanSelector(),
                     "archive": BooleanSelector(),
                     "export_format": SelectSelector(SelectSelectorConfig(
                         options=list(EXPORT_FORMATS), translation_key="export_format",
                         mode=SelectSelectorMode.DROPDOWN)),
                     "export_url": TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
                     "export_database": TextSelector(), "export_org": TextSelector(),
                     "export_token": TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))}
        return self.async_show_form(step_id="init", errors=errors, data_schema=vol.Schema({
            vol.Required(name): section(vol.Schema({
                (vol.Optional if key in OPTIONAL_FIELDS else vol.Required)(key, default=options[key]): selectors[key]
                for key in keys
            }), {"collapsed": name == "export" and options["export_format"] == "off" and not errors})
            for name, keys in OPTION_SECTIONS.items()
        }))
