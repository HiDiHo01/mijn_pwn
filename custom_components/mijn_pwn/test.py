# test.py

import asyncio
import logging

from api import PWNAPI

_LOGGER = logging.getLogger(__name__)


async def main():
    # Replace with your actual username and password
    username = "royarxhoek@gmail.com"
    password = "Supervis18!pwn"

    # Initialize PWNAPI instance
    # Pass None as Home Assistant instance for standalone usage
    pwn_api = PWNAPI(None)

    # Set up PWNAPI with authentication
    if await pwn_api.async_setup(username, password):
        _LOGGER.info("PWN API setup successful")
    else:
        _LOGGER.error("Failed to setup PWN API")
        return

    # Example fetching invoices
    invoices_data = await pwn_api.async_get_invoices()
    if invoices_data:
        _LOGGER.info("Invoices data:")
        print(invoices_data)  # Print invoices data
    else:
        _LOGGER.error("Failed to fetch invoices data")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
