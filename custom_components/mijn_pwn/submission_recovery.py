"""Explicit, account-scoped recovery for uncertain final meter submissions."""

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .coordinator import PWNDataUpdateCoordinator

SERVICE_RELEASE_UNCERTAIN = "release_uncertain_submission"


def _require_pwn_verification(value: object) -> bool:
    """Reject anything other than an explicit boolean True acknowledgement."""
    if value is not True:
        raise vol.Invalid("PWN must first confirm that the reading was not processed")
    return True


_RELEASE_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry_id"): vol.All(str, vol.Length(min=1)),
        vol.Required("meter_number"): vol.All(str, vol.Length(min=1)),
        vol.Required("reading_number"): vol.Match(r"^[0-9]{11}$"),
        vol.Required("pwn_confirmed_not_processed"): _require_pwn_verification,
    }
)


def async_register_submission_recovery(hass: HomeAssistant) -> None:
    """Register one manual recovery action for all loaded account entries."""
    if hass.services.has_service(DOMAIN, SERVICE_RELEASE_UNCERTAIN):
        return

    async def _release(call: ServiceCall) -> None:
        """Release only the exact request after explicit external verification."""
        coordinator = hass.data.get(DOMAIN, {}).get(call.data["config_entry_id"])
        if not isinstance(coordinator, PWNDataUpdateCoordinator):
            raise HomeAssistantError("The selected Mijn PWN integration is not loaded")
        try:
            await coordinator.async_release_uncertain_submission(
                call.data["meter_number"], call.data["reading_number"]
            )
        except ValueError:
            raise HomeAssistantError(
                "No matching unresolved submission can be released"
            ) from None
        except Exception:
            raise HomeAssistantError(
                "Could not persist the release; submission remains blocked"
            ) from None

    hass.services.async_register(
        DOMAIN, SERVICE_RELEASE_UNCERTAIN, _release, schema=_RELEASE_SCHEMA
    )
