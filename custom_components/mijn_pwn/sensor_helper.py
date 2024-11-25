"""
Setting up sensors for PWN
"""
# sensor_helper.py
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, DefaultDict, Optional, Union

from homeassistant.components.sensor import (SensorEntity,
                                             SensorEntityDescription)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNKNOWN
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (API_URL, ATTRIBUTION, COMPONENT_TITLE, DATA_INVOICES,
                    DATA_USER, DOMAIN, ICON, MANUFACTURER,
                    SERVICE_NAME_INVOICES, VERSION)
from .coordinator import PWNDataUpdateCoordinator
from .exceptions import PWNAPIAuthenticationError

_LOGGER = logging.getLogger(__name__)
INVOICE_DATE_FORMAT = '%Y-%m-%dT%H:%M:%S.%f0Z'
MONTHS_COVERED_BY_INVOICE = 3


@dataclass
class PWNEntityDescription(SensorEntityDescription):
    """Representation of a Sensor Entity Description."""

    authenticated: bool = False
    service_name: Optional[str] = SERVICE_NAME_INVOICES
    # value_fn: Optional[Callable[[dict], StateType]] = None
    value_fn: Optional[Callable[[dict[str, Any]], Any]] = None
    # attr_fn: Callable[[dict], dict[str, Union[StateType, list]]] = field(
    attr_fn: Callable[[dict[str, Any]], dict[str, Union[Any, list]]] = field(
        default_factory=lambda: {}  # type: ignore
    )

    def __init__(
        self,
        key: str,
        name: str,
        device_class: Optional[str] = None,
        state_class: Optional[str] = None,
        native_unit_of_measurement: Optional[str] = None,
        suggested_display_precision: Optional[int] = None,
        authenticated: bool = False,
        service_name: Optional[str] = None,
        value_fn: Callable[[dict[str, Any]],
                           Optional[Any]] = lambda data: None,
        attr_fn: Callable[[dict[str, Any]], dict[str, Any]] = lambda data: {},
        entity_registry_enabled_default: bool = True,
        entity_registry_visible_default: bool = True,
        entity_category: Optional[Union[str, EntityCategory]] = None,
        translation_key: Optional[str] = None,
        icon: Optional[str] = None,
    ):
        super().__init__(
            key=key,
            name=name,
            device_class=device_class,
            state_class=state_class,
            native_unit_of_measurement=native_unit_of_measurement,
            suggested_display_precision=suggested_display_precision,
            translation_key=translation_key,
            entity_category=entity_category
        )
        self.authenticated = authenticated
        # self.translation_key = translation_key
        self.entity_category = entity_category if entity_category is not None else None
        self.service_name = service_name or SERVICE_NAME_INVOICES
        self.value_fn = value_fn if callable(
            value_fn) else lambda data: STATE_UNKNOWN
        self.attr_fn = attr_fn if callable(attr_fn) else lambda data: {}
        self.entity_registry_enabled_default = entity_registry_enabled_default
        self.entity_registry_visible_default = entity_registry_visible_default
        self.icon = icon

    def get_state(self, data: dict[str, Any]) -> StateType:
        """Get the state value from the provided data.

        Args:
            data (dict[str, Any]): The data dictionary.

        Returns:
            Any: The state value.
        """
        return self.value_fn(data) if self.value_fn else STATE_UNKNOWN

    def get_attributes(self, data: dict[str, Any]) -> dict[str, Union[StateType, list]]:
        """Get the additional attributes from the provided data.

        Args:
            data (dict[str, Any]): The data dictionary.

        Returns:
            Dict[str, Union[Any, list]]: The attributes dictionary.
        """
        return self.attr_fn(data)

    @property
    def is_authenticated(self) -> bool:
        """Check if the entity is authenticated.

        Returns:
            bool: True if the entity is authenticated, False otherwise.
        """
        return self.authenticated


class PWNSensor(CoordinatorEntity, SensorEntity):
    """Representation of a PWN sensor."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION
    _attr_icon = ICON

    def __init__(
        self,
        coordinator: PWNDataUpdateCoordinator,
        description: PWNEntityDescription,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        self.entity_description: PWNEntityDescription = description
        self.config_entry = config_entry
        self._attr_name = description.name
        self._attr_unique_id = f"{config_entry.unique_id}_{description.key}"
        # self._attr_unique_id = f"{DOMAIN}_{
        #     description.service_name}_{description.key}"
        self._unique_id = f"{config_entry.unique_id}_{description.key}"
        # Automatically generated entity_id should not be manually set; it's managed by Home Assistant
        # self._attr_entity_id = f"sensor.{self._attr_name.lower().replace(' ', '_')}"
        # self._attr_unique_id = f"{DOMAIN}_{description.key.lower().replace(' ', '_')}_{config_entry.entry_id}"
        # self._attr_should_poll = True
        # Do not set extra identifier for default service, backwards compatibility
        self._entity_category = description.entity_category

        # Logging to verify initialization
        # _LOGGER.debug("Initializing PWNSensor: %s", self._attr_unique_id)

        device_info_identifiers: set[tuple[str, str, Optional[str]]] = (
            {(DOMAIN, f"{config_entry.entry_id}", None)}
            if description.service_name is SERVICE_NAME_INVOICES
            else {(DOMAIN, f"{config_entry.entry_id}", description.service_name)}
        )

        self._attr_device_info = DeviceInfo(
            identifiers=device_info_identifiers,
            name=f"{COMPONENT_TITLE} - {description.service_name}",
            translation_key=f"{COMPONENT_TITLE} - {description.service_name}",
            manufacturer=MANUFACTURER,
            entry_type=DeviceEntryType.SERVICE,
            suggested_area="Home",
            configuration_url=API_URL,
            model=description.service_name,
            sw_version=VERSION,
        )
        self._attr_icon = description.icon or self._attr_icon
        # self._update_state()

        # Initialize the sensor's state
        # self._attr_native_value = None
        super().__init__(coordinator)

    async def async_added_to_hass(self) -> None:
        """Handle when the sensor is added to Home Assistant."""
        if self.coordinator is None:
            _LOGGER.error(
                "Coordinator is not set up for sensor: %s", self._attr_name)
            return

        # Ensure all attributes are properly set before updating state
        if not self.entity_description or not self.entity_description.value_fn:
            _LOGGER.error(
                'Missing entity description or value function for sensor: %s',
                self._attr_name
            )
            return

        # Update state initially
        await self.async_update_state()

        # Add listener for coordinator updates if not already added
        if not hasattr(self, '_listener_added') or not self._listener_added:
            self._listener_added = True
            self.async_on_remove(
                self.coordinator.async_add_listener(self._update_state)
            )

    async def async_update_state(self) -> None:
        """Update the state of the sensor asynchronously."""
        try:
            if not self.coordinator.data:
                _LOGGER.error(
                    'Coordinator data is missing for sensor: %s',
                    self._attr_name
                )
                return

            # Check if value_fn is callable
            value_fn = self.entity_description.value_fn
            if value_fn is None or not callable(value_fn):
                _LOGGER.error(
                    'The value function is not callable or is None for sensor: %s',
                    self._attr_name
                )
                return

            # Use value_fn to get the sensor's state
            self._attr_native_value = value_fn(self.coordinator.data)

            # Special handling for 'last_update' if applicable
            if self.entity_description.key == 'last_update':
                last_update = self.coordinator.data.get('last_update')
                if last_update:
                    _LOGGER.debug(
                        "Last update timestamp: %s for sensor: %s", last_update, self._attr_name)
                    self._attr_native_value = last_update
                else:
                    _LOGGER.warning(
                        "Last update field is missing for sensor: %s", self._attr_name)

            self.async_write_ha_state()
            _LOGGER.debug(
                "Sensor %s state successfully updated to: %s",
                self._attr_name, self._attr_native_value
            )

        except Exception as e:
            _LOGGER.error("Error updating sensor state for %s: %s",
                          self._attr_name, e)

    def old_update_state(self):
        """Update the state of the sensor."""
        _LOGGER.debug('Updating state for %s', self._attr_name)
        try:
            # if not self.entity_description or not self.coordinator.data:
            # _LOGGER.error(
            #     'Missing entity description or coordinator data for %s',
            #     self._attr_name
            # )
            # return

            # Use value_fn for the sensor's main state
            if self.entity_description.value_fn(
                    self.coordinator.data):
                self._attr_native_value = self.entity_description.value_fn(
                    self.coordinator.data)
                self.async_write_ha_state()
            # Special handling for last_update if applicable
            # last_update = self.coordinator.data.get('last_update')
            # if last_update:
            #     _LOGGER.debug("Last update timestamp: %s", last_update)
            # Example: If you want the sensor to represent the last update time itself
            #     if self.entity_description.key == 'last_update':
            #         self._attr_native_value = last_update

            _LOGGER.debug(
                "Sensor %s state successfully updated to: %s",
                self._attr_name, self._attr_native_value
            )

        except Exception as e:
            _LOGGER.error("Error updating sensor state for %s: %s",
                          self._attr_name, e)

    def _update_state(self):
        """Update the state of the sensor."""
        # _LOGGER.debug('Updating state for %s', self._attr_name)
        try:
            # Ensure that coordinator data is not None
            if self.coordinator.data is None:
                _LOGGER.error('Coordinator data is None for %s',
                              self._attr_name)
                return

            # Use value_fn for the sensor's main state, ensuring coordinator data is valid
            main_value = self.entity_description.value_fn(
                self.coordinator.data)
            # Validate the fetched value
            if main_value is not None:
                self._attr_native_value = main_value
                self.async_write_ha_state()
            # else:
            # Value function returned None for Open invoices document numbers
            #     _LOGGER.warning(
            #         'Value function returned None for %s', self._attr_name)

            # _LOGGER.debug(
            #     "Sensor %s state successfully updated to: %s",
            #     self._attr_name, self._attr_native_value
            # )

        except TypeError as e:
            _LOGGER.error(
                "TypeError when updating sensor state for %s: %s. Data might be None or invalid.",
                self._attr_name, e
            )
        except Exception as e:
            _LOGGER.error("Error updating sensor state for %s: %s",
                          self._attr_name, e)

    async def async_refresh_data(self) -> None:
        """Method to refresh the coordinator data asynchronously."""
        try:
            await self.coordinator.async_request_refresh()
            _LOGGER.info("Coordinator data refreshed successfully.")

            # Update "Last Update" sensor with the current timestamp
            self.coordinator.data['last_update'] = datetime.now().isoformat()
            _LOGGER.debug("Updating states...")
            self._update_state()  # Ensure the state is updated with the new timestamp
        except PWNAPIAuthenticationError as e:
            _LOGGER.error(
                "Failed to refresh coordinator data due to authentication error: %s", e)
        except Exception as e:
            _LOGGER.error(
                "An unexpected error occurred while refreshing coordinator data: %s", e)

    @staticmethod
    def get_all_invoices(data: dict[str, Any]) -> dict:
        """Get all invoices from the data."""
        return {
            "invoices": [
                invoice for account in data[DATA_INVOICES].get('contractAccounts', [])
                for invoice in account.get('summarizedInvoices', [])
            ]
        } if data and DATA_INVOICES in data else {}

    @staticmethod
    def get_all_invoices_as_dict(data: dict[str, Any]) -> dict:
        """Get all invoices from the data as a dictionary."""
        all_invoices = [
            invoice for account in data[DATA_INVOICES].get('contractAccounts', [])
            for invoice in account.get('summarizedInvoices', [])
        ] if data and DATA_INVOICES in data else []

        return {"invoices": [PWNSensor.as_dict(invoice) for invoice in all_invoices]}

    @staticmethod
    def as_dict(invoice: dict[str, Any]) -> dict:
        """Convert an invoice to a dictionary format."""
        return {
            "invoiceNumber": invoice.get("invoiceNumber"),
            "documentNumber": invoice.get("documentNumber"),
            "paymentStatus": invoice.get("paymentStatus"),
            "totalAmount": invoice.get("totalAmount"),
            "openAmount": invoice.get("openAmount"),
            "invoiceDate": invoice.get("invoiceDate"),
            "expirationDate": invoice.get("expirationDate"),
            "dunningPhase": invoice.get("dunningPhase"),
            "invoiceType": invoice.get("invoiceType"),
            "documents": invoice.get("documents"),
        }

    @staticmethod
    def calculate_total_invoice_amount(data: dict[str, Any]) -> float:
        """Calculate and return the total invoice amount."""
        total_invoice_amount = 0.00
        if not data or DATA_INVOICES not in data:
            _LOGGER.warning(
                "No data available for calculating total invoice amount.")
            return total_invoice_amount

        try:
            invoices_data = data[DATA_INVOICES]
            for account in invoices_data.get('contractAccounts', []):
                for invoice in account.get('summarizedInvoices', []):
                    total_invoice_amount += PWNSensor._calculate_invoice_amount(
                        invoice)
        except Exception as e:
            _LOGGER.error("Error calculating total invoice amount: %s", e)
        return total_invoice_amount

    @staticmethod
    def get_total_number_of_invoices(data: dict[str, Any]) -> int:
        """Return the total number of invoices."""
        total_invoices = 0
        try:
            if data and DATA_INVOICES in data:
                invoices_data = data[DATA_INVOICES]
                if invoices_data and 'contractAccounts' in invoices_data:
                    for account in invoices_data['contractAccounts']:
                        if 'summarizedInvoices' in account:
                            total_invoices += len(
                                account['summarizedInvoices'])
        except Exception as e:
            _LOGGER.error("Error counting total number of invoices: %s", e)
        return total_invoices

    @staticmethod
    def calculate_average_invoice_amount(data: dict[str, Any]) -> float:
        """Calculate and return the average amount of all invoices."""
        total_invoice_amount = PWNSensor.calculate_total_invoice_amount(data)
        total_number_of_invoices = PWNSensor.get_total_number_of_invoices(data)
        try:
            average_invoice_amount = total_invoice_amount / \
                total_number_of_invoices if total_number_of_invoices > 0 else 0.00
        except Exception as e:
            _LOGGER.error("Error calculating average invoice amount: %s", e)
            average_invoice_amount = 0.00
        return average_invoice_amount

    @staticmethod
    def calculate_total_amount_per_year(data: dict[str, Any]) -> dict[int, float]:
        """Calculate and return the total amount for each year."""
        # total_amount_per_year = defaultdict(float)
        total_amount_per_year: DefaultDict[int, float] = defaultdict(float)
        try:
            if data and DATA_INVOICES in data:
                invoices_data = data[DATA_INVOICES]
                if invoices_data and 'contractAccounts' in invoices_data:
                    for account in invoices_data['contractAccounts']:
                        if 'summarizedInvoices' in account:
                            for invoice in account['summarizedInvoices']:
                                invoice_date = invoice.get("invoiceDate")
                                if invoice_date:
                                    year = int(invoice_date[:4])
                                    total_amount = invoice.get(
                                        "totalAmount", 0.00)
                                    payment_status = invoice.get(
                                        "paymentStatus")
                                    if payment_status == "Betaald":
                                        total_amount_per_year[year] += total_amount
                                    elif payment_status == "Uitbetaald":
                                        total_amount_per_year[year] -= total_amount
        except Exception as e:
            _LOGGER.error("Error calculating total amount per year: %s", e)
        return dict(total_amount_per_year)

    @staticmethod
    def calculate_total_amount_current_year(data: dict[str, Any]) -> float:
        """Calculate and return the total amount for the current year."""
        current_year = datetime.now().year
        total_amount_current_year = 0.0
        try:
            if data and DATA_INVOICES in data:
                invoices_data = data[DATA_INVOICES]
                if invoices_data and 'contractAccounts' in invoices_data:
                    for account in invoices_data['contractAccounts']:
                        if 'summarizedInvoices' in account:
                            for invoice in account['summarizedInvoices']:
                                invoice_date = invoice.get("invoiceDate")
                                if invoice_date and int(invoice_date[:4]) == current_year:
                                    total_amount = invoice.get(
                                        "totalAmount", 0.0)
                                    payment_status = invoice.get(
                                        "paymentStatus")
                                    if payment_status == "Betaald":
                                        total_amount_current_year += total_amount
                                    elif payment_status == "Uitbetaald":
                                        total_amount_current_year -= total_amount
        except Exception as e:
            _LOGGER.error(
                "Error calculating total amount for current year: %s", e)
        return total_amount_current_year

    @staticmethod
    def calculate_total_amount_previous_year(data: dict[str, Any]) -> float:
        """Calculate and return the total amount for the previous year."""
        previous_year = datetime.now().year - 1
        total_amount_previous_year = 0.0
        try:
            if data and DATA_INVOICES in data:
                invoices_data = data[DATA_INVOICES]
                if invoices_data and 'contractAccounts' in invoices_data:
                    for account in invoices_data['contractAccounts']:
                        if 'summarizedInvoices' in account:
                            for invoice in account['summarizedInvoices']:
                                invoice_date = invoice.get("invoiceDate")
                                if invoice_date and int(invoice_date[:4]) == previous_year:
                                    total_amount = invoice.get(
                                        "totalAmount", 0.00)
                                    payment_status = invoice.get(
                                        "paymentStatus")
                                    if payment_status == "Betaald":
                                        total_amount_previous_year += total_amount
                                    elif payment_status == "Uitbetaald":
                                        total_amount_previous_year -= total_amount
        except Exception as e:
            _LOGGER.error(
                "Error calculating total amount for previous year: %s", e)
        return total_amount_previous_year

    @staticmethod
    def calculate_expected_costs_current_year(data: dict[str, Any]) -> float:
        """Calculate and return the expected costs for the current year."""
        current_year = datetime.now().year
        total_amount_current_year = 0.00
        total_months_current_year = 0

        try:
            if data and DATA_INVOICES in data:
                invoices_data = data[DATA_INVOICES]
                for account in invoices_data.get('contractAccounts', []):
                    for invoice in account.get('summarizedInvoices', []):
                        invoice_date_str = invoice.get("invoiceDate")
                        if invoice_date_str:
                            invoice_date = datetime.strptime(
                                invoice_date_str, INVOICE_DATE_FORMAT)
                            if invoice_date.year == current_year:
                                total_amount_current_year += PWNSensor._calculate_invoice_amount(
                                    invoice)
                                total_months_current_year += 3

            if total_months_current_year > 0:
                # Calculate the average cost per month and scale it to 12 months
                average_cost_per_month = total_amount_current_year / total_months_current_year
                expected_costs_current_year = average_cost_per_month * 12
            else:
                expected_costs_current_year = 0.00

        except Exception as e:
            _LOGGER.error(
                "Error calculating expected costs for current year: %s", e)
            expected_costs_current_year = 0.00

        return expected_costs_current_year

    @staticmethod
    def calculate_amount_per_month_per_year(data: dict[str, Any], year: int) -> Optional[float]:
        """Calculate the total amount for a given year."""
        total_amount_for_year = 0.00
        number_of_months = 0

        try:
            if data and DATA_INVOICES in data:
                invoices_data = data[DATA_INVOICES]
                for account in invoices_data.get('contractAccounts', []):
                    for invoice in account.get('summarizedInvoices', []):
                        invoice_date_str = invoice.get("invoiceDate")
                        if invoice_date_str:
                            invoice_date = datetime.strptime(
                                invoice_date_str, INVOICE_DATE_FORMAT)
                            invoice_year = invoice_date.year
                            if invoice_year == year:
                                total_amount_for_year += invoice.get(
                                    "totalAmount", 0.0)
                                number_of_months += 3

        except Exception as e:
            _LOGGER.error("Error calculating amount per year: %s", e)

        # Divide by 12 to get the monthly amount
        if total_amount_for_year and number_of_months:
            return total_amount_for_year / number_of_months
        return None

    @staticmethod
    def calculate_amount_per_month_per_year_as_dict(data: dict[str, Any]) -> dict[str, Any]:
        """Calculate and return the amount per month for each year with string keys."""
        amount_per_month_per_year = defaultdict(lambda: defaultdict(float))

        try:
            if data and DATA_INVOICES in data:
                invoices_data = data[DATA_INVOICES]
                for account in invoices_data.get('contractAccounts', []):
                    for invoice in account.get('summarizedInvoices', []):
                        invoice_date_str = invoice.get("invoiceDate")
                        if invoice_date_str:
                            invoice_date = datetime.strptime(
                                invoice_date_str, INVOICE_DATE_FORMAT)
                            year = invoice_date.year
                            total_amount = invoice.get("totalAmount", 0.0)
                            amount_per_month = total_amount / 3  # Each invoice covers 3 months

                            # Distribute the amount over the three months the invoice covers
                            for month_offset in range(3):
                                month = (invoice_date.month +
                                         month_offset - 1) % 12 + 1
                                adjusted_year = year + \
                                    (invoice_date.month + month_offset - 1) // 12
                                amount_per_month_per_year[str(adjusted_year)][str(
                                    month)] += amount_per_month

        except Exception as e:
            _LOGGER.error("Error calculating amount per month per year: %s", e)

        # Convert to a format that matches the expected return type for attr_fn
        return {year: dict(months) for year, months in amount_per_month_per_year.items()}

    @staticmethod
    def get_costs_per_month_current_year(data: dict[str, Any]) -> float:
        current_year = datetime.now().year
        return PWNSensor.calculate_amount_per_month_per_year(data, current_year)

    @staticmethod
    def get_current_year_as_dict(data: dict[str, Any]) -> dict[str, Any]:
        return PWNSensor.calculate_amount_per_month_per_year_as_dict(data)

    @staticmethod
    def _calculate_invoice_amount(invoice: dict[str, Any]) -> float:
        """Helper function to calculate the amount for a single invoice."""
        payment_status = invoice.get("paymentStatus")
        total_amount = invoice.get("totalAmount", 0.00)
        if payment_status == "Betaald" or payment_status == "Geplande incasso":
            return total_amount
        elif payment_status == "Uitbetaald":
            return -total_amount
        return 0.00

    @staticmethod
    def calculate_total_costs_per_year_asdict(data: dict[str, Any]) -> dict[int, float]:
        """Calculate and return the total costs per year as a dictionary."""
        total_costs_per_year = defaultdict(float)
        try:
            if data and DATA_INVOICES in data:
                invoices_data = data[DATA_INVOICES]
                for account in invoices_data.get('contractAccounts', []):
                    for invoice in account.get('summarizedInvoices', []):
                        invoice_date_str = invoice.get("invoiceDate")
                        if invoice_date_str:
                            invoice_date = datetime.strptime(
                                invoice_date_str, INVOICE_DATE_FORMAT)
                            year = invoice_date.year
                            total_amount = invoice.get("totalAmount", 0.0)
                            payment_status = invoice.get("paymentStatus")
                            if payment_status == "Betaald":
                                total_costs_per_year[year] += total_amount
                            elif payment_status == "Uitbetaald":
                                total_costs_per_year[year] -= total_amount
        except Exception as e:
            _LOGGER.error("Error calculating total costs per year: %s", e)
        return dict(total_costs_per_year)

    @staticmethod
    def calculate_bills_per_year(data: dict[str, Any]) -> dict[str, Any]:
        """Calculate and return the bills per year in the specified format."""
        bills_per_year = []

        try:
            if data and DATA_INVOICES in data:
                invoices_data = data[DATA_INVOICES]
                yearly_data = defaultdict(lambda: {
                    'totalAmount': 0.00,
                    'number of invoices': 0,
                    'openAmount': 0.00,
                    'year end invoice': 0.00,
                    'invoice_quarter': 0.00,
                    'paymentStatus': 'Unknown',
                    'invoiceType': 'Jaarrekening'
                })

                for account in invoices_data.get('contractAccounts', []):
                    for invoice in account.get('summarizedInvoices', []):
                        invoice_date_str = invoice.get("invoiceDate")
                        if invoice_date_str:
                            invoice_date = datetime.strptime(
                                invoice_date_str, INVOICE_DATE_FORMAT)
                            year = invoice_date.year
                            total_amount = PWNSensor._calculate_invoice_amount(
                                invoice)
                            open_amount = invoice.get("openAmount", 0.0)
                            invoice_type = invoice.get(
                                "invoiceType", 'Unknown')

                            if invoice_type == "Termijnfactuur":
                                invoice_quarter = total_amount

                            yearly_data[year]['totalAmount'] += total_amount
                            yearly_data[year]['number of invoices'] += 1
                            yearly_data[year]['invoice_quarter'] = invoice_quarter
                            yearly_data[year]['openAmount'] += open_amount
                            if open_amount == 0:
                                yearly_data[year]['paymentStatus'] = "Betaald"
                            yearly_data[year]['invoiceType'] = invoice_type
                            if invoice_type == "Drinkwaterfactuur":
                                yearly_data[year]['year end invoice'] += total_amount

                for year, data in yearly_data.items():
                    data['average per month'] = round(
                        data['totalAmount'] / (data['number of invoices'] * MONTHS_COVERED_BY_INVOICE), 2)
                    bills_per_year.append({
                        'Year': year,
                        'Start date': f"01-01-{year}",
                        'Amount': data['totalAmount'],
                        'Number of invoices': data['number of invoices'],
                        'Open Amount': data['openAmount'],
                        'Average per month': data['average per month'],
                        'Invoice quarter': data['invoice_quarter'],
                        'Year end invoice': data['year end invoice'],
                        'Payment status': data['paymentStatus'],
                        'Invoice type': data['invoiceType']
                    })

        except Exception as e:
            _LOGGER.error("Error calculating bills per year: %s", e)

        return {'costs_per_year': bills_per_year}

    @staticmethod
    def calculate_invoice_quarter_current_year(data: dict[str, Any]) -> Optional[float]:
        """Calculate and return the total amount for each quarter of the current year."""
        current_year = int(datetime.now().year)
        quarters = defaultdict(float)
        quarter_amount = None

        try:
            if data and DATA_INVOICES in data:
                invoices_data = data[DATA_INVOICES]
                for account in invoices_data.get('contractAccounts', []):
                    for invoice in account.get('summarizedInvoices', []):
                        invoice_date_str = invoice.get("invoiceDate")
                        if invoice_date_str:
                            invoice_date = datetime.strptime(
                                invoice_date_str, INVOICE_DATE_FORMAT)
                            year = invoice_date.year
                            if year == current_year:
                                quarter = (invoice_date.month - 1) // 3 + 1
                                quarters[str(
                                    quarter)] += PWNSensor._calculate_invoice_amount(invoice)
                # Calculate the average amount per quarter
                if quarters:
                    quarter_amount = sum(quarters.values()) / len(quarters)
                else:
                    quarter_amount = 0.0

        except Exception as e:
            _LOGGER.error(
                "Error calculating invoice quarters current year: %s", e)

        return quarter_amount

    @staticmethod
    def calculate_invoice_quarters_current_year_asdict(data: dict[str, Any]) -> dict[str, float]:
        """Calculate and return the total amount for each quarter of the current year."""
        current_year = str(datetime.now().year)
        quarters = defaultdict(float)

        try:
            if data and DATA_INVOICES in data:
                invoices_data = data[DATA_INVOICES]
                for account in invoices_data.get('contractAccounts', []):
                    for invoice in account.get('summarizedInvoices', []):
                        invoice_date_str = invoice.get("invoiceDate")
                        if invoice_date_str:
                            invoice_date = datetime.strptime(
                                invoice_date_str, INVOICE_DATE_FORMAT)
                            year = invoice_date.year
                            if str(year) == current_year:
                                quarter = (invoice_date.month - 1) // 3 + 1
                                quarters[f"Q{str(
                                    quarter)}"] = PWNSensor._calculate_invoice_amount(invoice)
        except Exception as e:
            _LOGGER.error(
                "Error calculating invoice quarters current year: %s", e)

        return {str(k): v for k, v in quarters.items()}
        # return dict(quarters)

    @staticmethod
    def get_full_name(data: dict[str, Any]) -> Optional[str]:
        """Get the full name by combining initials and last name."""
        if data and DATA_USER in data:
            private_client = data[DATA_USER].get('privateClient', {})
            prefix = private_client.get('prefix', '').strip()
            initials = private_client.get('initials', '').strip()
            last_name = private_client.get('lastName', '').strip()
            full_name = f"{prefix} {initials} {last_name}"
            return full_name
        return None

    @staticmethod
    def get_correspondence_address(data: dict[str, Any]) -> Optional[str]:
        """Get the full name by combining initials and last name."""
        if data and DATA_USER in data:
            correspondence_address = data[DATA_USER].get(
                'correspondenceAddress', {})
            street = correspondence_address.get('street', '').strip()
            house_number = correspondence_address.get(
                'houseNumber', '').strip()
            postal_code = correspondence_address.get('postalCode', '').strip()
            city = correspondence_address.get('city', '').strip()
            country = correspondence_address.get('country', '').strip()
            correspondence_address = f"{street} {house_number} {
                postal_code} {city} {country}".strip()
            return correspondence_address
        return None

    def measurement_type(self, measurement_type: int) -> str:
        """The type of water measurement"""
        if measurement_type == 1:
            return "Doorgegeven via Mijn PWN"
        return "Onbekend"

    @property
    def entity_category(self) -> EntityCategory:
        """Return the category of the entity."""
        return self._entity_category

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return self.entity_description.name

    # @property
    # def state(self):
    #     """Return the state of the sensor."""
    #     return self.entity_description.value_fn(self.coordinator.data)

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return self._unique_id

    @property
    def extra_state_attributes(self) -> Optional[dict]:
        """Return the state attributes."""
        return self.entity_description.attr_fn(self.coordinator.data)

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return super().available and self.native_value is not None

    @property
    def native_value(self) -> Optional[Any]:
        """Return the state of the sensor."""
        if self.entity_description is None:
            _LOGGER.error("Entity description is None.")
            return None

        if self.coordinator is None:
            _LOGGER.error("Coordinator is None.")
            return None

        if self.coordinator.data is None:
            # _LOGGER.error("Coordinator data is None.")
            return None

        if not callable(self.entity_description.value_fn):
            _LOGGER.error("Value function is not callable.")
            return None

        try:
            value = self.entity_description.value_fn(self.coordinator.data)
#            if value is None:
#                _LOGGER.warning("Sensor %s state is None.",
#                                self.entity_description.key)
            return value
        except Exception as e:
            _LOGGER.error("Failed to retrieve sensor state: %s", str(e))
            return None

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_listener(self._update_state))
        self._update_state()

    def _update_invoice_sensor(self, invoices):
        """Update invoice sensor with new data."""
        if invoices:
            self._attr_state = invoices.get('some_value', 'default_value')
        else:
            self._attr_state = 'No data'
        _LOGGER.debug("Updated invoice sensor state to: %s", self._attr_state)


async def async_update(self):
    """Fetch the latest data from the coordinator and update the entity's state and attributes."""
    _LOGGER.debug("Starting async_update for %s",
                  self.entity_description.name)

    # Fetch data from the coordinator
    data = self.coordinator.data
    _LOGGER.debug("Data received from coordinator: %s", data)

    # If no data is available, log a warning and set the state to None
    if data is None:
        _LOGGER.warning("No data available from coordinator.")
        self._set_state(None, {})
        return

    try:
        # Validate data
        if not data:
            _LOGGER.warning("No data received from coordinator.")
            self._set_state(None, {})
            return

        # Validate entity description
        if not self.entity_description or not callable(self.entity_description.value_fn):
            _LOGGER.error("Invalid entity description or value function.")
            self._set_state(None, {})
            return

        # Check if entity ID is specified
        if not self.entity_description.key:
            _LOGGER.error("No entity ID specified for entity: %s",
                          self.entity_description.name)
            self._set_state(None, {})
            return

        # Retrieve the state value using the entity description's value function
        native_value = self.entity_description.value_fn(data)

        # Retrieve the additional attributes using the entity description's attribute function
        extra_attributes = self.entity_description.attr_fn(data)

        # Log the updated state for debugging purposes
        _LOGGER.debug("Updated sensor %s state to: %s",
                      self.entity_description.name, native_value)
        _LOGGER.debug("Updated sensor %s attributes to: %s",
                      self.entity_description.name, extra_attributes)

        # Set the entity's state and attributes using a helper method
        self._set_state(native_value, extra_attributes)

    except (TypeError, IndexError, ValueError) as ex:
        _LOGGER.warning("Data format error in PWNSensor: %s", ex)
        self._set_state(None, {})

    except ZeroDivisionError as ex:
        _LOGGER.error("Division by zero error in PWNSensor: %s", ex)
        self._set_state(None, {})

    except Exception as ex:
        _LOGGER.error("Unexpected error updating PWNSensor: %s", ex)
        self._set_state(None, {})


def _set_state(self, native_value: Any, extra_attributes: dict[str, Any]) -> None:
    """Helper method to set the sensor state and attributes.

    Args:
        native_value (Any): The new state value for the sensor.
        extra_attributes (dict[str, Any]): Additional attributes to set for the sensor.
    """
    self._attr_native_value = native_value
    self._attr_extra_state_attributes = extra_attributes

    # Write the updated state to Home Assistant
    self.async_write_ha_state()
