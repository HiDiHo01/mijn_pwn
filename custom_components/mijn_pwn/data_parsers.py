"""Validated, privacy-preserving summaries of optional Mijn PWN API payloads."""

from datetime import date


def get_notification_count(payload: object) -> int | None:
    """Count notifications only when the API returns an unambiguous JSON array.

    The response schema of the notifications endpoint is not documented. Do not
    infer the count from dictionary keys, message text, or other unknown shapes.
    """
    if not isinstance(payload, list):
        return None
    return len(payload)


def _last_actual_reading(
    payload: object,
) -> tuple[date, dict[str, object]] | None:
    """Find the latest dated, explicitly actual reading in validated history."""
    if not isinstance(payload, dict):
        return None

    history = payload.get("history")
    if not isinstance(history, list):
        return None

    readings: list[tuple[date, dict[str, object]]] = []
    for item in history:
        if not isinstance(item, dict) or item.get("lastActualReading") is not True:
            continue

        end_date = item.get("endDate")
        if not isinstance(end_date, str):
            continue

        try:
            parsed_date = date.fromisoformat(end_date[:10])
        except ValueError:
            continue

        readings.append((parsed_date, item))

    return max(readings, key=lambda reading: reading[0]) if readings else None


def get_last_meter_reading_date(payload: object) -> date | None:
    """Return the date of the most recent actual meter reading, if known."""
    reading = _last_actual_reading(payload)
    return reading[0] if reading is not None else None


def get_water_consumption_status(payload: object) -> str | None:
    """Summarize whether PWN reports an anomaly on the latest actual reading.

    This is a historical billing-data indicator, not a realtime leak detector.
    An omitted anomaly field is unknown; it does not prove normal consumption.
    """
    reading = _last_actual_reading(payload)
    if reading is None:
        return None

    record = reading[1]
    if "anomalyReason" not in record:
        return None

    reason = record["anomalyReason"]
    if reason is None or reason == "":
        return "no_reported_anomaly"
    if isinstance(reason, str):
        return "anomaly_reported"
    return None
