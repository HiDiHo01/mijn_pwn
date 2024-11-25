"""Setting up sensors for PWN"""
# sensor.py
import logging
from datetime import datetime, timedelta

import homeassistant.util.dt as dt
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CURRENCY_EURO, STATE_UNKNOWN, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DATA_CONSUMPTION,
    DATA_INVOICES,
    DATA_USER,
    DOMAIN,
    SERVICE_NAME_CONSUMPTION,
    SERVICE_NAME_USER,
)
from .coordinator import PWNDataUpdateCoordinator
from .sensor_helper import PWNEntityDescription, PWNSensor

_LOGGER = logging.getLogger(__name__)
INVOICE_DATE_FORMAT = '%Y-%m-%dT%H:%M:%S.%f0Z'  # Format for invoice dates
MONTHS_COVERED_BY_INVOICE = 3
# DATA_INVOICES = DATA_KEYS['DATA_INVOICES']

SENSOR_DESCRIPTIONS: tuple[PWNEntityDescription, ...] = (
    PWNEntityDescription(
        key="total_open_amount",
        name="Total open amount",
        translation_key="total_open_amount",
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: data[DATA_INVOICES].get('invoicesSummary', {}).get(
            "totalOpenAmount")
        if data and DATA_INVOICES in data else None,
        attr_fn=lambda data: {
            "invoices": [
                invoice for account in data[DATA_INVOICES].get('contractAccounts', [])
                for invoice in account.get('summarizedInvoices', [])
                if invoice.get('openAmount', 0) > 0
            ]
        } if data and DATA_INVOICES in data and data[DATA_INVOICES].get('invoicesSummary', {}).get(
            "numberOfOpenItems") > 0 else {}
    ),
    PWNEntityDescription(
        key="open_invoices_document_numbers",
        name="Open invoices document numbers",
        translation_key="open_invoices_document_numbers",
        icon="mdi:invoice",
        suggested_display_precision=None,
        state_class=None,
        value_fn=lambda data: data[DATA_INVOICES].get('invoicesSummary', {}).get(
            "openInvoicesDocumentNumbers", "-")
        if data and DATA_INVOICES in data and not data[DATA_INVOICES].get('invoicesSummary', {}).get(
            "openInvoicesDocumentNumbers") == [] else None,
        attr_fn=lambda data: {
            "invoices": [invoice for account in data[DATA_INVOICES].get('contractAccounts', [])
                         for invoice in account.get('summarizedInvoices', [])
                         if invoice.get('openAmount', 0) > 0]
        }
        if data and DATA_INVOICES in data and data[DATA_INVOICES].get('invoicesSummary', {}).get(
            "numberOfOpenItems") > 0 else {}
    ),
    PWNEntityDescription(
        key="number_of_open_items",
        name="Number of open invoices",
        translation_key="number_of_open_items",
        suggested_display_precision=0,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:invoice",
        value_fn=lambda data: data[DATA_INVOICES].get('invoicesSummary', {}).get(
            "numberOfOpenItems")
        if data and DATA_INVOICES in data else None,
        attr_fn=lambda data: {
            "invoices": [invoice for account in data[DATA_INVOICES].get('contractAccounts', [])
                         for invoice in account.get('summarizedInvoices', [])
                         if invoice.get('openAmount', 0) > 0]
        }
        if data and DATA_INVOICES in data and data[DATA_INVOICES].get('invoicesSummary', {}).get(
            "numberOfOpenItems") > 0 else {}
    ),
    PWNEntityDescription(
        key="total_costs",
        name="Total costs",
        translation_key="total_costs",
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: PWNSensor.calculate_total_invoice_amount(data)
        if data and DATA_INVOICES in data else None,
        attr_fn=lambda data: PWNSensor.calculate_bills_per_year(data)
        if data and DATA_INVOICES in data else {}
    ),
    PWNEntityDescription(
        key="total_costs_current_year",
        name="Total costs current year",
        translation_key="total_costs_current_year",
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: PWNSensor.calculate_total_amount_current_year(
            data)
        if data and DATA_INVOICES in data else None,
        attr_fn=lambda data: PWNSensor.get_all_invoices_as_dict(data)
        if data and DATA_INVOICES in data else {}
    ),
    PWNEntityDescription(
        key="expected_costs_current_year",
        name="Expected costs current year",
        translation_key="expected_costs_current_year",
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: PWNSensor.calculate_expected_costs_current_year(
            data)
        if data and DATA_INVOICES in data else None,
        attr_fn=lambda data: PWNSensor.get_all_invoices_as_dict(data)
        if data and DATA_INVOICES in data else {}
    ),
    PWNEntityDescription(
        key="total_cost_previous_year",
        name="Total costs previous year",
        translation_key="total_cost_previous_year",
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: PWNSensor.calculate_total_amount_previous_year(
            data)
        if data and DATA_INVOICES in data else None,
        attr_fn=lambda data: PWNSensor.get_all_invoices_as_dict(data)
        if data and DATA_INVOICES in data else {}
    ),
    PWNEntityDescription(
        key="costs_per_month_current_year",
        name="Costs per month current year",
        translation_key="costs_per_month_current_year",
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=PWNSensor.get_costs_per_month_current_year,
        attr_fn=PWNSensor.get_current_year_as_dict
    ),
    PWNEntityDescription(
        key="invoice_quarter_current_year",
        name="Invoice Quarter Current Year",
        translation_key="invoice_quarter_current_year",
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=PWNSensor.calculate_invoice_quarter_current_year,
        attr_fn=lambda data: {
            "quarterly invoices": PWNSensor.calculate_invoice_quarters_current_year_asdict(data)}
    ),
    PWNEntityDescription(
        key="contract_account_number",
        name="Contract account number",
        translation_key="contract_account_number",
        suggested_display_precision=None,
        icon="mdi:invoice",
        device_class=None,
        value_fn=lambda data: next((account.get("contractAccountNumber")
                                   for account in data[DATA_INVOICES].get('contractAccounts', [])), None)
        if data and DATA_INVOICES in data else None
    ),
    PWNEntityDescription(
        key="relation_number",
        name="Relation number",
        translation_key="relation_number",
        suggested_display_precision=None,
        service_name=SERVICE_NAME_USER,
        icon="mdi:account",
        device_class=None,
        value_fn=lambda data: data[DATA_USER].get('relationNumber', None)
        if data and DATA_USER in data else None
    ),
    PWNEntityDescription(
        key="full_name",
        name="Full Name",
        translation_key="full_name",
        suggested_display_precision=None,
        service_name=SERVICE_NAME_USER,
        icon="mdi:account",
        device_class=None,
        value_fn=PWNSensor.get_full_name
    ),
    PWNEntityDescription(
        key="birth_place",
        name="Birth place",
        translation_key="birth_place",
        suggested_display_precision=None,
        service_name=SERVICE_NAME_USER,
        icon="mdi:account",
        device_class=None,
        value_fn=lambda data: data[DATA_USER].get(
            'privateClient', {}).get('birthplace', None)
        if data and DATA_USER in data else None,
    ),
    PWNEntityDescription(
        key="date_of_birth",
        name="Date of birth",
        translation_key="date_of_birth",
        suggested_display_precision=None,
        service_name=SERVICE_NAME_USER,
        icon="mdi:account",
        device_class=None,
        value_fn=lambda data: datetime.strptime(data[DATA_USER].get(
            'privateClient', {}).get('dateOfBirth', ''), INVOICE_DATE_FORMAT).strftime('%d-%m-%Y')
        if data and DATA_USER in data and data[DATA_USER].get('privateClient', {}).get('dateOfBirth') else None
    ),
    PWNEntityDescription(
        key="correspondence_address",
        name="Correspondence address",
        translation_key="correspondence_address",
        suggested_display_precision=None,
        service_name=SERVICE_NAME_USER,
        icon="mdi:account",
        device_class=None,
        value_fn=PWNSensor.get_correspondence_address
    ),
    PWNEntityDescription(
        key="latest_meter_reading",
        name="Latest Meter Reading",
        translation_key="latest_meter_reading",
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_display_precision=0,
        service_name=SERVICE_NAME_CONSUMPTION,
        icon="mdi:waves",
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: next(
            (entry['meterReading'] for entry in data[DATA_CONSUMPTION].get('history', [])
             if entry.get('lastActualReading') is True), None
        ) if data and DATA_CONSUMPTION in data else None,
        attr_fn=lambda data: {
            "periodDescription": next(
                (entry['periodDescription'] for entry in data[DATA_CONSUMPTION].get('history', [])
                 if entry.get('lastActualReading') is True), None),
            "startDate": next(
                (entry['startDate'] for entry in data[DATA_CONSUMPTION].get('history', [])
                 if entry.get('lastActualReading') is True), None),
            "endDate": next(
                (entry['endDate'] for entry in data[DATA_CONSUMPTION].get('history', [])
                 if entry.get('lastActualReading') is True), None),
            "consumption": next(
                (entry['consumption'] for entry in data[DATA_CONSUMPTION].get('history', [])
                 if entry.get('lastActualReading') is True), None),
            "measurementType": next(
                (entry['measurementType'] for entry in data[DATA_CONSUMPTION].get('history', [])
                 if entry.get('lastActualReading') is True), None)
        } if data and DATA_CONSUMPTION in data else {}
    ),
    PWNEntityDescription(
        key="total_consumption",
        name="Total Consumption",
        translation_key="total_consumption",
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_display_precision=0,
        service_name=SERVICE_NAME_CONSUMPTION,
        icon="mdi:cup-water",
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: sum(
            float(entry['consumption']) for entry in data[DATA_CONSUMPTION].get('history', [])
            if entry.get('consumption') is not None
        ) if data and DATA_CONSUMPTION in data else None,
        attr_fn=lambda data: {
            "readings": [
                {
                    "periodDescription": entry.get('periodDescription'),
                    "startDate": entry.get('startDate'),
                    "endDate": entry.get('endDate'),
                    "consumption": entry.get('consumption'),
                    "anomalyReason": entry.get('anomalyReason'),
                    "meterReading": entry.get('meterReading'),
                    "measurementType": entry.get('measurementType'),
                    "lastActualReading": entry.get('lastActualReading')
                }
                for entry in data[DATA_CONSUMPTION].get('history', [])
            ]
        } if data and DATA_CONSUMPTION in data else {}
    ),
    PWNEntityDescription(
        key="consumption_last_year",
        name="Consumption Last Year",
        translation_key="consumption_last_year",
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_display_precision=0,
        service_name=SERVICE_NAME_CONSUMPTION,
        icon="mdi:cup-water",
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: next(
            (entry['consumption'] for entry in data[DATA_CONSUMPTION].get('history', [])
             if entry.get('lastActualReading') is True), None
        ) if data and DATA_CONSUMPTION in data else None,
        attr_fn=lambda data: {
            "readings": [
                {
                    "periodDescription": entry.get('periodDescription'),
                    "startDate": entry.get('startDate'),
                    "endDate": entry.get('endDate'),
                    "consumption": entry.get('consumption'),
                    "anomalyReason": entry.get('anomalyReason'),
                    "meterReading": entry.get('meterReading'),
                    "measurementType": entry.get('measurementType'),
                    "lastActualReading": entry.get('lastActualReading')
                }
                for entry in data[DATA_CONSUMPTION].get('history', [])
                if datetime.strptime(entry['endDate'], INVOICE_DATE_FORMAT) >= datetime.now() - timedelta(days=365)
            ]
        } if data and DATA_CONSUMPTION in data else {}
    ),
    PWNEntityDescription(
        key="last_update",
        name="Last Update",
        translation_key="last_update",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=None,
        suggested_display_precision=None,
        service_name=SERVICE_NAME_USER,
        icon="mdi:update",
        device_class=None,
        value_fn=lambda data: (
            dt.as_local(datetime.fromisoformat(data['last_update'])).strftime(
                '%d-%m-%Y %H:%M:%S')
            if 'last_update' in data and data['last_update'] else STATE_UNKNOWN
        )
    ),
    PWNEntityDescription(
        key="last_update2",
        name="Last Update",
        translation_key="last_update2",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=None,  # No unit of measurement for timestamp
        suggested_display_precision=None,
        service_name=SERVICE_NAME_USER,
        icon="mdi:update",
        device_class="timestamp",
        value_fn=lambda data: (
            datetime.fromisoformat(data['last_update'])
            if 'last_update' in data and data['last_update'] else STATE_UNKNOWN
        )
    )
)


def safe_get_data(data, key, default=None):
    return data.get(key, default) if data else default


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback
) -> None:
    """Set up PWN sensor based on a config entry."""
    _LOGGER.debug("Setting up Mijn PWN sensors for entry: %s",
                  config_entry.entry_id)

    # Retrieve the coordinator for the current config entry
    coordinator: PWNDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    # Filter and create sensors based on the descriptions
    sensors = [
        PWNSensor(coordinator, description, config_entry)
        for description in SENSOR_DESCRIPTIONS
        if _should_add_sensor(description, coordinator)
    ]

    # Add entities if there are any valid sensors
    if sensors:
        async_add_entities(sensors, True)
        _LOGGER.info("Added %d sensors for entry: %s",
                     len(sensors), config_entry.entry_id)
    else:
        _LOGGER.warning("No sensors created for entry: %s",
                        config_entry.entry_id)


def _should_add_sensor(description: PWNEntityDescription, coordinator: PWNDataUpdateCoordinator) -> bool:
    """Determine if a sensor should be added based on the description and authentication status."""
    if not description.authenticated or coordinator.api.auth.is_authenticated():
        return True
    _LOGGER.debug(
        "Skipping sensor %s because authentication is required and not fulfilled.", description.key)
    return False
