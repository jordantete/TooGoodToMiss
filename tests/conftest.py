import pytest
from unittest.mock import MagicMock
from app.core.scheduler import Scheduler
from app.services.tgtg_service.models import ItemDetails, Store, Item, PickupInterval, PickupLocation, PriceInfo, Picture, Address

@pytest.fixture
def mock_tgtg_client():
    return MagicMock()

@pytest.fixture
def mock_scheduler():
    return MagicMock(spec=Scheduler)

@pytest.fixture
def mock_item_details():
    return ItemDetails(
        item=Item(
            item_id="456",
            item_price=PriceInfo(code="EUR", minor_units=599, decimals=2),
            item_value=PriceInfo(code="EUR", minor_units=1599, decimals=2),
            cover_picture=Picture(
                picture_id="test_item_cover",
                current_url="http://test.com/item_cover",
                is_automatically_created=False
            ),
            logo_picture=Picture(
                picture_id="test_item_logo",
                current_url="http://test.com/item_logo",
                is_automatically_created=False
            ),
            name="Test Item",
            description="Test Description"
        ),
        store=Store(
            store_id="123",
            store_name="Test Store",
            website=None,
            store_location=Address(
                address={
                    "address_line": "123 Test Street",
                    "latitude": 48.8566,
                    "longitude": 2.3522
                }
            ),
            logo_picture=Picture(
                picture_id="test_logo",
                current_url="http://test.com/logo",
                is_automatically_created=False
            ),
            cover_picture=Picture(
                picture_id="test_cover",
                current_url="http://test.com/cover",
                is_automatically_created=False
            ),
            store_time_zone="Europe/Paris"
        ),
        display_name="Test Item",
        items_available=2,
        distance=1.5,
        favorite=True,
        item_type="MAGIC_BAG",
        pickup_location=PickupLocation(
            address={"address_line": "123 Test Street"},
            location={"latitude": 48.8566, "longitude": 2.3522}
        ),
        pickup_interval=PickupInterval(
            start="2024-03-20T14:00:00Z",
            end="2024-03-20T18:00:00Z"
        )
    )