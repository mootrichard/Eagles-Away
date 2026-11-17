from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter

from cpfc_trip.config import Settings


async def connect(settings: Settings) -> Client:
    return await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
        tls=settings.temporal_tls,
        api_key=settings.temporal_api_key.get_secret_value() or None,
        data_converter=pydantic_data_converter,
    )
