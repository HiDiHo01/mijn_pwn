"""Offline regressions for account-scoped Mijn PWN reconfiguration."""

from secrets import token_urlsafe
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import ClientConnectionError, ClientResponseError

from homeassistant import config_entries
from homeassistant.data_entry_flow import AbortFlow, FlowResultType

from .config_flow import PWNConfigFlow
from .const import DOMAIN


class TestPWNReconfigureFlow(unittest.IsolatedAsyncioTestCase):
    """Ensure reconfiguration never replaces or mutates another PWN account."""

    @staticmethod
    def _make_entry(entry_id: str, username: str) -> MagicMock:
        """Build a config entry with fictional credentials and extra data."""
        entry = MagicMock()
        entry.entry_id = entry_id
        entry.unique_id = username
        entry.data = {
            "username": username,
            "password": token_urlsafe(16),
            "saved_setting": f"preserved-{entry_id}",
        }
        entry.update_listeners = []
        return entry

    def setUp(self) -> None:
        """Create two independent entries and a real HA config-flow object."""
        self._first = self._make_entry("entry-first", "first@example.invalid")
        self._second = self._make_entry("entry-second", "second@example.invalid")
        self._entries: dict[str, MagicMock] = {
            self._first.entry_id: self._first,
            self._second.entry_id: self._second,
        }
        self._first_original = dict(self._first.data)
        self._second_original = dict(self._second.data)

        self._hass = MagicMock()
        config_entries_manager = self._hass.config_entries
        config_entries_manager.async_get_known_entry.side_effect = self._get_entry_by_id
        config_entries_manager.async_entry_for_domain_unique_id.side_effect = (
            self._get_entry_by_unique_id
        )
        config_entries_manager.async_update_entry.side_effect = self._update_entry

        self._flow = PWNConfigFlow()
        self._flow.hass = self._hass
        self._flow.handler = DOMAIN
        self._flow.flow_id = "synthetic-reconfigure-flow"
        self._flow.context = {
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": self._second.entry_id,
        }
        self._flow._async_in_progress = MagicMock(return_value=[])
        self._flow._validate_user_input = AsyncMock(return_value=True)

    def _get_entry_by_id(self, entry_id: str) -> MagicMock:
        """Return only the explicitly selected mock config entry."""
        return self._entries[entry_id]

    def _get_entry_by_unique_id(self, domain: str, unique_id: str) -> MagicMock | None:
        """Resolve configured identities without inventing new accounts."""
        if domain != DOMAIN:
            return None
        return next(
            (entry for entry in self._entries.values() if entry.unique_id == unique_id),
            None,
        )

    def _update_entry(
        self, *, entry: MagicMock, data: dict[str, object], **_kwargs: object
    ) -> bool:
        """Simulate Home Assistant's data update for exactly one entry."""
        entry.data = dict(data)
        return True

    async def test_only_selected_second_account_is_updated_and_reloaded(self) -> None:
        """Use the context entry, merge data, and abort without creating a new entry."""
        replacement_password = token_urlsafe(16)
        input_data = {
            "username": self._second.unique_id,
            "password": replacement_password,
        }

        result = await self._flow.async_step_reconfigure(input_data)

        self.assertEqual(result["type"], FlowResultType.ABORT)
        self.assertEqual(result["reason"], "reconfigure_successful")
        self.assertEqual(self._first.data, self._first_original)
        self.assertEqual(
            self._second.data,
            {**self._second_original, "password": replacement_password},
        )
        self.assertEqual(self._second.unique_id, self._second_original["username"])
        self.assertEqual(len(self._entries), 2)
        self._hass.config_entries.async_schedule_reload.assert_called_once_with(
            self._second.entry_id
        )
        self._hass.config_entries.async_update_entry.assert_called_once()
        self._flow._validate_user_input.assert_awaited_once_with(input_data)

    async def test_first_account_can_also_be_selected(self) -> None:
        """Reconfiguration must not be hard-coded to any entry list position."""
        self._flow.context["entry_id"] = self._first.entry_id
        replacement_password = token_urlsafe(16)

        result = await self._flow.async_step_reconfigure(
            {"username": self._first.unique_id, "password": replacement_password}
        )

        self.assertEqual(result["type"], FlowResultType.ABORT)
        self.assertEqual(self._second.data, self._second_original)
        self.assertEqual(self._first.data["password"], replacement_password)
        self._hass.config_entries.async_schedule_reload.assert_called_once_with(
            self._first.entry_id
        )

    async def test_invalid_credentials_do_not_update_or_reload(self) -> None:
        """Keep both entries intact when the authentication check fails."""
        self._flow._validate_user_input.return_value = False

        result = await self._flow.async_step_reconfigure(
            {"username": self._second.unique_id, "password": token_urlsafe(16)}
        )

        self.assertEqual(result["type"], FlowResultType.FORM)
        self.assertEqual(result["errors"], {"base": "invalid_auth"})
        self.assertEqual(self._first.data, self._first_original)
        self.assertEqual(self._second.data, self._second_original)
        self._hass.config_entries.async_update_entry.assert_not_called()
        self._hass.config_entries.async_schedule_reload.assert_not_called()

    async def test_other_configured_account_identity_is_rejected(self) -> None:
        """Reject a duplicate-account target before validating or writing data."""
        input_data = {
            "username": self._first.unique_id,
            "password": token_urlsafe(16),
        }

        with self.assertRaises(AbortFlow) as result:
            await self._flow.async_step_reconfigure(input_data)

        self.assertEqual(result.exception.reason, "unique_id_mismatch")
        self._flow._validate_user_input.assert_not_awaited()
        self._hass.config_entries.async_update_entry.assert_not_called()
        self._hass.config_entries.async_schedule_reload.assert_not_called()
        self.assertEqual(self._first.data, self._first_original)
        self.assertEqual(self._second.data, self._second_original)

    async def test_unknown_account_identity_is_rejected(self) -> None:
        """An existing entry cannot silently become a different new account."""
        input_data = {
            "username": "third@example.invalid",
            "password": token_urlsafe(16),
        }
        with self.assertRaises(AbortFlow) as result:
            await self._flow.async_step_reconfigure(input_data)

        self.assertEqual(result.exception.reason, "unique_id_mismatch")
        self._flow._validate_user_input.assert_not_awaited()
        self._hass.config_entries.async_update_entry.assert_not_called()

    async def test_empty_input_does_not_authenticate_or_mutate(self) -> None:
        """Invalid direct inputs only redisplay the form."""
        for user_input in (
            None,
            {},
            {"username": "", "password": token_urlsafe(16)},
            {"username": self._second.unique_id, "password": ""},
        ):
            with self.subTest(user_input=user_input):
                result = await self._flow.async_step_reconfigure(user_input)
                self.assertEqual(result["type"], FlowResultType.FORM)

        self._flow._validate_user_input.assert_not_awaited()
        self._hass.config_entries.async_update_entry.assert_not_called()
        self._hass.config_entries.async_schedule_reload.assert_not_called()

    async def test_reauth_password_only_form_targets_selected_entry(self) -> None:
        """Present only a password control for the account in the flow context."""
        self._flow.context["source"] = config_entries.SOURCE_REAUTH
        result = await self._flow.async_step_reauth(dict(self._second.data))
        self.assertEqual(result["type"], FlowResultType.FORM)
        self.assertEqual(result["step_id"], "reauth_confirm")
        self.assertEqual(
            {marker.schema for marker in result["data_schema"].schema},
            {"password"},
        )
        self._flow._validate_user_input.assert_not_awaited()

    async def test_reauth_updates_only_selected_password_and_reloads(self) -> None:
        """Preserve the other account and unrelated data during password renewal."""
        self._flow.context["source"] = config_entries.SOURCE_REAUTH
        password = token_urlsafe(16)
        result = await self._flow.async_step_reauth_confirm({"password": password})
        self.assertEqual(result["type"], FlowResultType.ABORT)
        self.assertEqual(result["reason"], "reauth_successful")
        self._flow._validate_user_input.assert_awaited_once_with(
            {"username": self._second.unique_id, "password": password}
        )
        self.assertEqual(self._first.data, self._first_original)
        self.assertEqual(
            self._second.data, {**self._second_original, "password": password}
        )
        self.assertEqual(self._second.unique_id, self._second_original["username"])
        self.assertEqual(len(self._entries), 2)
        self._hass.config_entries.async_update_entry.assert_called_once()
        self._hass.config_entries.async_schedule_reload.assert_called_once_with(
            self._second.entry_id
        )

    async def test_reauth_respects_first_entry_selection(self) -> None:
        """Reauthenticate the selected entry regardless of entry list order."""
        self._flow.context.update(
            {
                "source": config_entries.SOURCE_REAUTH,
                "entry_id": self._first.entry_id,
            }
        )
        replacement_password = token_urlsafe(16)
        result = await self._flow.async_step_reauth_confirm(
            {"password": replacement_password}
        )
        self.assertEqual(result["type"], FlowResultType.ABORT)
        self.assertEqual(self._first.data["password"], replacement_password)
        self.assertEqual(self._second.data, self._second_original)
        self._hass.config_entries.async_schedule_reload.assert_called_once_with(
            self._first.entry_id
        )

    async def test_reauth_failed_authentication_preserves_entries(self) -> None:
        """Do not save credentials after a rejected authentication attempt."""
        self._flow.context["source"] = config_entries.SOURCE_REAUTH
        self._flow._validate_user_input.return_value = False
        result = await self._flow.async_step_reauth_confirm(
            {"password": token_urlsafe(16)}
        )
        self.assertEqual(result["type"], FlowResultType.FORM)
        self.assertEqual(result["errors"], {"base": "invalid_auth"})
        self.assertEqual(self._first.data, self._first_original)
        self.assertEqual(self._second.data, self._second_original)
        self._hass.config_entries.async_update_entry.assert_not_called()
        self._hass.config_entries.async_schedule_reload.assert_not_called()

    async def test_reauth_real_validator_distinguishes_http_failures(self) -> None:
        """401 and 403 reject credentials; 503 and 429 report temporary failures."""
        self._flow.context["source"] = config_entries.SOURCE_REAUTH
        self._flow._validate_user_input = PWNConfigFlow._validate_user_input.__get__(
            self._flow
        )

        for status, expected_error in (
            (401, "invalid_auth"),
            (403, "invalid_auth"),
            (503, "cannot_connect"),
            (429, "cannot_connect"),
        ):
            with self.subTest(status=status):
                response = MagicMock()
                response.status = status
                context = MagicMock()
                context.__aenter__ = AsyncMock(return_value=response)
                context.__aexit__ = AsyncMock(return_value=None)
                session = MagicMock()
                session.post.return_value = context

                with patch(
                    "custom_components.mijn_pwn.config_flow.async_get_clientsession",
                    return_value=session,
                ):
                    result = await self._flow.async_step_reauth_confirm(
                        {"password": token_urlsafe(16)}
                    )

                self.assertEqual(result["type"], FlowResultType.FORM)
                self.assertEqual(result["errors"], {"base": expected_error})
                self.assertEqual(self._first.data, self._first_original)
                self.assertEqual(self._second.data, self._second_original)
                self._hass.config_entries.async_update_entry.assert_not_called()
                self._hass.config_entries.async_schedule_reload.assert_not_called()

    async def test_reauth_real_validator_handles_connection_and_http_errors(
        self,
    ) -> None:
        """Transport failures must keep existing entries unchanged."""
        self._flow.context["source"] = config_entries.SOURCE_REAUTH
        self._flow._validate_user_input = PWNConfigFlow._validate_user_input.__get__(
            self._flow
        )
        for exception in (
            ClientConnectionError("fictional-offline"),
            ClientResponseError(None, (), status=503),
            TimeoutError(),
        ):
            with self.subTest(error_type=type(exception).__name__):
                context = MagicMock()
                context.__aenter__ = AsyncMock(side_effect=exception)
                context.__aexit__ = AsyncMock(return_value=None)
                session = MagicMock()
                session.post.return_value = context

                with patch(
                    "custom_components.mijn_pwn.config_flow.async_get_clientsession",
                    return_value=session,
                ):
                    result = await self._flow.async_step_reauth_confirm(
                        {"password": token_urlsafe(16)}
                    )

                self.assertEqual(result["type"], FlowResultType.FORM)
                self.assertEqual(result["errors"], {"base": "cannot_connect"})
                self.assertEqual(self._first.data, self._first_original)
                self.assertEqual(self._second.data, self._second_original)
                self._hass.config_entries.async_update_entry.assert_not_called()
                self._hass.config_entries.async_schedule_reload.assert_not_called()

    async def test_reconfigure_real_validator_reports_temporary_outage(self) -> None:
        """Reconfiguration must not mistake an outage for changed credentials."""
        self._flow._validate_user_input = PWNConfigFlow._validate_user_input.__get__(
            self._flow
        )
        session = MagicMock()
        context = MagicMock()
        context.__aenter__ = AsyncMock(
            side_effect=ClientConnectionError("fictional-offline")
        )
        context.__aexit__ = AsyncMock(return_value=None)
        session.post.return_value = context

        with patch(
            "custom_components.mijn_pwn.config_flow.async_get_clientsession",
            return_value=session,
        ):
            result = await self._flow.async_step_reconfigure(
                {"username": self._second.unique_id, "password": token_urlsafe(16)}
            )

        self.assertEqual(result["type"], FlowResultType.FORM)
        self.assertEqual(result["errors"], {"base": "cannot_connect"})
        self.assertEqual(self._first.data, self._first_original)
        self.assertEqual(self._second.data, self._second_original)
        self._hass.config_entries.async_update_entry.assert_not_called()

    async def test_new_account_real_validator_reports_temporary_outage(self) -> None:
        """A failed sign-in must not create a config entry."""
        self._flow.context["source"] = config_entries.SOURCE_USER
        self._flow._validate_user_input = PWNConfigFlow._validate_user_input.__get__(
            self._flow
        )
        session = MagicMock()
        response = MagicMock()
        response.status = 503
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=response)
        context.__aexit__ = AsyncMock(return_value=None)
        session.post.return_value = context

        with patch(
            "custom_components.mijn_pwn.config_flow.async_get_clientsession",
            return_value=session,
        ):
            result = await self._flow.async_step_user(
                {"username": "third@example.invalid", "password": token_urlsafe(16)}
            )

        self.assertEqual(result["type"], FlowResultType.FORM)
        self.assertEqual(result["errors"], {"base": "cannot_connect"})
        self.assertEqual(self._first.data, self._first_original)
        self.assertEqual(self._second.data, self._second_original)
        self._hass.config_entries.async_update_entry.assert_not_called()

    async def test_reauth_rejects_inconsistent_entry_identity(self) -> None:
        """Reject corrupted identity state before contacting the API."""
        self._flow.context["source"] = config_entries.SOURCE_REAUTH
        self._second.unique_id = "other@example.invalid"
        result = await self._flow.async_step_reauth_confirm(
            {"password": token_urlsafe(16)}
        )
        self.assertEqual(result["type"], FlowResultType.ABORT)
        self.assertEqual(result["reason"], "reauth_account_mismatch")
        self._flow._validate_user_input.assert_not_awaited()
        self._hass.config_entries.async_update_entry.assert_not_called()

    async def test_reauth_empty_password_does_not_update_entry(self) -> None:
        """Blank input must not invoke the API or modify a config entry."""
        self._flow.context["source"] = config_entries.SOURCE_REAUTH
        for user_input in (None, {}, {"password": ""}):
            with self.subTest(user_input=user_input):
                result = await self._flow.async_step_reauth_confirm(user_input)
                self.assertEqual(result["type"], FlowResultType.FORM)
        self._flow._validate_user_input.assert_not_awaited()
        self._hass.config_entries.async_update_entry.assert_not_called()
        self._hass.config_entries.async_schedule_reload.assert_not_called()
