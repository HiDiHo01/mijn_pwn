# Home Assistant Custom Integration Rules

Use these additional rules when working on a Home Assistant custom integration. Follow the current stable Home Assistant Core APIs and official developer documentation; verify API details when uncertain.

## Architecture and lifecycle
- Prefer config-entry-based setup and the current supported Home Assistant integration architecture.
- Use `DataUpdateCoordinator` for shared polling data when appropriate; avoid duplicate API requests across entities.
- Implement config-entry setup, unload, reload, and shutdown cleanup correctly.
- Forward platforms using supported config-entry APIs and clean up listeners, tasks, sessions, and other resources.
- Do not perform blocking I/O on the event loop. Use supported asynchronous APIs.
- Use Home Assistant's shared HTTP session via `async_get_clientsession` when appropriate.
- Apply bounded timeouts, sensible retry behavior, and clear handling of authentication, connection, and invalid-response errors.
- Avoid unnecessary polling and ensure coordinator refresh failures do not leave stale data incorrectly marked as available.

## Entities and registries
- Give entities stable, deterministic unique IDs.
- Use valid device information and stable identifiers; only set `via_device` when the referenced device is registered and valid.
- Keep entity names, device information, entity descriptions, units, device classes, state classes, and supported features consistent with Home Assistant conventions.
- Set availability accurately and guard against missing, invalid, or stale coordinator data.
- Create entities only when the corresponding feature, account capability, or active contract is present.
- Handle entity/device registry migrations conservatively. Do not modify entries belonging to other config entries or integrations.
- Prefer current supported entity properties and APIs over deprecated attributes.

## Python and asynchronous code
- Target the Python version supported by the integration and the current Home Assistant release; use Python 3.14+ when that is the project's declared target.
- Use accurate type hints, modern unions such as `str | None`, and built-in generic types.
- Add docstrings to functions and methods.
- Prefix class-internal helper methods with an underscore when they are not public API.
- Use timezone-aware datetimes and `datetime.now(timezone.utc)` for UTC timestamps.
- Use lazy `%s`-style formatting for logging calls (e.g. `_LOGGER.debug("Received %s", value)`); do not interpolate values with f-strings in logging calls.
- Never log credentials, tokens, personal data, or other secrets.
- Validate API responses before using them and provide useful, sanitized error messages.
- Avoid `Any` in internal helpers when a more precise type or `object` is suitable; use Home Assistant's required public types where necessary.

## Testing and quality
- Test config-entry setup and unload, coordinator success and failure, entity availability, and registry behavior where relevant.
- Cover missing optional features and account/contract changes.
- Test edge cases and regressions with current Home Assistant test utilities.
- Run the project's test suite, linting, type checking, and Home Assistant validation when available.
- Do not guess the current Home Assistant API. Check the installed version, pinned test dependencies, or official source when compatibility is in question.
- Do not update dependency pins arbitrarily; follow the project's dependency-management workflow.

## Pull-request review focus
Prioritize event-loop blocking, lifecycle leaks, invalid device identifiers, unstable unique IDs, incorrect entity metadata, unsupported/deprecated APIs, incorrect availability, duplicate requests, race conditions, and regressions in config-entry or registry migration logic.
