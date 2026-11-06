# Sensor expansion: evidence and implementation decisions

This document records the verified and unverified findings from the Mijn PWN
sensor research (6 November 2026). It is not an API contract.

## Confirmed from the repository

- `PWNDataUpdateCoordinator` already requests `notifications`, `advance`,
  `payment_info`, and `contact_preferences`, without exposing them as
  Home Assistant entities. No shorter global polling interval is needed.
- `consumption_history.history` is already used by existing sensors, including
  `lastActualReading`, `endDate`, and `anomalyReason`.
- Stable devices currently exist for Invoices, User, and Consumption. New
  entities reuse those devices and do not change registry identifiers.

## Implemented in this PR

| Entity | Device | Availability and semantics |
| --- | --- | --- |
| `notification_count` | User | Only when the endpoint returns a JSON array. No notification text or identifying attributes. Unknown wrapped response shapes are unsupported, not counted as zero. |
| `water_consumption_status` | Consumption | Enum based solely on a present `anomalyReason` field on the latest dated actual reading; no historical leak detection claim. |
| `last_meter_reading_date` | Consumption | Date of the latest explicitly actual, dated reading. Diagnostic entity. |

The response shapes for account notifications are not publicly documented.
A raw array is intrinsically countable; dictionary wrappers are intentionally
not parsed until confirmed against an appropriately sanitized API response.
The new optional entities are registered only when their data is supported
on first refresh; reload the config entry after an account gains the feature.

The anomaly indicator is a **billing-history summary**, not realtime flow,
meter battery information, or proof that consumption is normal. Missing fields
mean unavailable, not a healthy or zero state.

## Sensor icons

Sensors now declare explicit static Material Design Icons matching their
meaning: financial totals and forecasts use cash/chart icons, invoice counts
use document icons, account fields use identity/location icons, and water
readings use meter/water icons. The invoice binary sensor uses a document alert.

The consumption anomaly **status** uses a neutral water outline, because an
icon implying normal consumption would be misleading when an anomaly is
reported. Icons are deliberately static and do not infer a state beyond the
actual reported sensor value. Entity keys, unique IDs, device associations,
API behavior, and translations remain unchanged.

## Deferred pending validated API fixtures

| Candidate | Existing coordinator dataset | Evidence needed |
| --- | --- | --- |
| Advance / installment amount | `advance` | Amount path, currency, period, account eligibility |
| Automatic debit / payment method | `payment_info` | Explicit method/mandate status; no IBAN in entities |
| Digital correspondence preference | `contact_preferences` | Boolean or documented enum semantics |
| Notification unread count | `notifications` | Explicit read/unread status and response container |
| Smart meter leak, flow, battery and hourly usage | Not verified | Actual authenticated endpoint and consent/capability semantics |

Do not add guessed response paths or fetch extra data to support these.
See [PWN's smart meter information](https://www.pwn.nl/drinkwater/slimme-watermeter):
hourly smart-meter telemetry is not proof that these values are exposed by the
Mijn PWN customer API.

## Security, compatibility and follow-up validation

- Keep notification messages, payment identifiers, account numbers and raw
  payloads out of new entity attributes, fixtures and logs.
- Preserve the current shared coordinator, twelve-hour polling and device
  registry identifiers. No config-entry or device migration is required.
- Keep optional failures independent of invoice, account and consumption data.
- Test success, empty arrays, unknown wrappers, invalid history, missing fields,
  latest-reading ordering, translated entity keys and sensor metadata.
- Run `ruff format --check .`, `ruff check --output-format=github .` and the
  Home Assistant tests on the **final PR head**. Also review CI and external
  quality gates before merging.

This change is AI-assisted and requires human review of runtime behavior,
especially real (sanitized) account payload shapes.

Relevant source:
- [coordinator.py](../custom_components/mijn_pwn/coordinator.py)
- [sensor.py](../custom_components/mijn_pwn/sensor.py)
- [PWN developer-independent public information](https://www.pwn.nl/drinkwater/slimme-watermeter)
- [Home Assistant entity descriptions](https://developers.home-assistant.io/blog/2023/12/11/entity-description-changes/)
